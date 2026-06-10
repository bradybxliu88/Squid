"""Snap controller: capture one frame per channel and save a multichannel OME-TIFF.

Driven by CaptureMultichannelCommand on the EventBus. For each requested channel it
applies the acquisition mode, opens the matching illumination shutter, triggers the
camera, reads one frame, then saves all frames as a single (C, Y, X) OME-TIFF.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Tuple

import squid.core.logging
from squid.backend.controllers.base import BaseController
from squid.backend.io.writers.single_capture_ome_writer import (
    CapturePlane,
    next_capture_path,
    write_multichannel_ome_tiff,
)
from squid.core.events import (
    CaptureCompleted,
    CaptureFailed,
    CaptureMultichannelCommand,
    event_bus as _global_event_bus,
    handles,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus
    from squid.backend.services import CameraService
    from squid.backend.services.illumination_service import IlluminationService
    from squid.backend.controllers.live_controller import LiveController
    from squid.backend.controllers.microscope_mode_controller import (
        MicroscopeModeController,
    )

_log = squid.core.logging.get_logger(__name__)


class SnapController(BaseController):
    """Capture one frame per channel and save a multichannel OME-TIFF.

    Subscribes to: CaptureMultichannelCommand
    Publishes: CaptureCompleted, CaptureFailed
    """

    def __init__(
        self,
        camera_service: "CameraService",
        illumination_service: "IlluminationService",
        mode_controller: "MicroscopeModeController",
        live_controller: "LiveController",
        channel_config_service,
        event_bus: "EventBus" = _global_event_bus,
    ) -> None:
        super().__init__(event_bus)
        self._camera = camera_service
        self._illumination = illumination_service
        self._mode_controller = mode_controller
        self._live_controller = live_controller
        self._channel_configs = channel_config_service

    def snap_multichannel(
        self, channel_names: List[str], objective: str
    ) -> List[Tuple[str, object]]:
        """Capture one frame per channel. Returns [(name, frame), ...] for non-None frames."""
        was_live = bool(getattr(self._live_controller, "is_live", False))
        self._live_controller.stop_live()

        captured: List[Tuple[str, object]] = []
        try:
            for name in channel_names:
                self._mode_controller.apply_mode_for_acquisition(name)

                config = self._channel_configs.get_channel_configuration_by_name(objective, name)
                src = getattr(config, "illumination_source", None) if config is not None else None

                if src is not None:
                    self._illumination.turn_on_channel(src)
                try:
                    self._camera.send_trigger()
                    frame = self._camera.read_camera_frame()
                finally:
                    if src is not None:
                        self._illumination.turn_off_channel(src)

                if frame is not None:
                    captured.append((name, frame))
                else:
                    self._log.warning("Channel '%s' produced no frame; skipping", name)
        finally:
            if was_live:
                self._live_controller.start_live()

        return captured

    @handles(CaptureMultichannelCommand)
    def _on_capture(self, cmd: CaptureMultichannelCommand) -> None:
        """Handle CaptureMultichannelCommand: snap all channels and save one OME-TIFF."""
        try:
            captured = self.snap_multichannel(cmd.channel_names, cmd.objective)
            if not captured:
                self._event_bus.publish(CaptureFailed(reason="no frames captured"))
                return

            display_by_name = {
                info.name: info
                for info in self._channel_configs.get_channel_display_info(cmd.objective)
            }

            planes: List[CapturePlane] = []
            for name, frame in captured:
                info = display_by_name.get(name)
                config = self._channel_configs.get_channel_configuration_by_name(cmd.objective, name)
                planes.append(
                    CapturePlane(
                        name=name,
                        data=frame.frame,
                        color=getattr(info, "color", "#FFFFFF") if info is not None else "#FFFFFF",
                        exposure_ms=getattr(config, "exposure_time", 0.0) if config is not None else 0.0,
                        wavelength=getattr(info, "ex_wavelength", None) if info is not None else None,
                    )
                )

            path = next_capture_path(cmd.save_directory, cmd.file_prefix)
            write_multichannel_ome_tiff(path, planes)

            self._event_bus.publish(
                CaptureCompleted(save_path=path, channel_names=[name for name, _ in captured])
            )
        except Exception as e:  # noqa: BLE001 - handler must never raise
            self._log.exception("Multichannel capture failed")
            self._event_bus.publish(CaptureFailed(reason=str(e)))

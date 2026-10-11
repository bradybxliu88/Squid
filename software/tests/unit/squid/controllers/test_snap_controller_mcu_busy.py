"""SnapController must not trigger into a running MCU command.

A snap fires the same trigger as live view; clicked mid-move it would reproduce the
MCU wedge that LiveController.trigger_acquisition guards against. It waits for the
MCU and gives up rather than triggering.
"""

from unittest.mock import MagicMock

import pytest

from squid.backend.controllers.snap_controller import SnapController
from squid.core.events import CaptureFailed, CaptureMultichannelCommand, EventBus


def _snap_controller(peripheral_service=None, was_live: bool = True):
    bus = EventBus()
    live_controller = MagicMock()
    live_controller.is_live = was_live
    camera_service = MagicMock()
    illumination_service = MagicMock()
    channel_configs = MagicMock()
    channel_configs.get_channel_configuration_by_name.return_value = MagicMock(illumination_source=11)
    snap = SnapController(
        camera_service=camera_service,
        illumination_service=illumination_service,
        mode_controller=MagicMock(),
        live_controller=live_controller,
        channel_config_service=channel_configs,
        event_bus=bus,
        peripheral_service=peripheral_service,
    )
    return snap, bus, camera_service, illumination_service, live_controller


def test_snap_refuses_when_microcontroller_stays_busy():
    peripheral_service = MagicMock()
    peripheral_service.wait_for_idle.side_effect = TimeoutError("busy")
    snap, _, camera_service, illumination_service, live_controller = _snap_controller(peripheral_service)

    with pytest.raises(TimeoutError, match="Microcontroller still busy"):
        snap.snap_multichannel(["BF"], "10x")

    peripheral_service.wait_for_idle.assert_called_once_with(timeout_s=SnapController._MCU_IDLE_WAIT_S)
    camera_service.send_trigger.assert_not_called()
    illumination_service.turn_on_channel.assert_not_called()
    live_controller.start_live.assert_called_once()  # live is still restored


def test_capture_reports_busy_microcontroller_as_failure():
    peripheral_service = MagicMock()
    peripheral_service.wait_for_idle.side_effect = TimeoutError("busy")
    snap, bus, camera_service, _, _ = _snap_controller(peripheral_service, was_live=False)
    failures = []
    bus.subscribe(CaptureFailed, failures.append)

    snap._on_capture(
        CaptureMultichannelCommand(
            channel_names=["BF"], objective="10x", save_directory="/nonexistent", file_prefix="x"
        )
    )
    bus.drain()

    camera_service.send_trigger.assert_not_called()
    assert len(failures) == 1
    assert "Microcontroller still busy" in failures[0].reason


def test_snap_waits_for_idle_microcontroller_before_triggering():
    order = []
    peripheral_service = MagicMock()
    peripheral_service.wait_for_idle.side_effect = lambda **_kwargs: order.append("wait_for_idle")
    snap, _, camera_service, _, _ = _snap_controller(peripheral_service, was_live=False)
    camera_service.send_trigger.side_effect = lambda: order.append("send_trigger")

    frames = snap.snap_multichannel(["BF", "GFP"], "10x")

    assert len(frames) == 2
    assert order == ["wait_for_idle", "send_trigger", "send_trigger"]


def test_snap_without_peripheral_service_still_triggers():
    snap, _, camera_service, _, _ = _snap_controller(peripheral_service=None, was_live=False)

    frames = snap.snap_multichannel(["BF"], "10x")

    assert len(frames) == 1
    camera_service.send_trigger.assert_called_once()

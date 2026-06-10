"""Image click to stage movement controller.

Converts image pixel coordinates to stage movement commands.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

from _def import SOFTWARE_POS_LIMIT
from squid.backend.controllers.base import BaseController
from squid.core.events import (
    ImageCoordinateClickedCommand,
    ImageScrollZCommand,
    ClickToMoveEnabledChanged,
    handles,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus
    from squid.backend.managers import ObjectiveStore
    from squid.backend.services import CameraService
    from squid.backend.services import StageService


class ImageClickController(BaseController):
    """Handles image click-to-move functionality.

    Subscribes to ImageCoordinateClickedCommand and converts pixel coordinates
    to stage movement commands based on objective magnification and camera binning.

    Subscribes to: ImageCoordinateClickedCommand
    Moves stage via StageService (x and y)
    """

    def __init__(
        self,
        objective_store: "ObjectiveStore",
        camera_service: "CameraService",
        stage_service: Optional["StageService"],
        event_bus: "EventBus",
        inverted_objective: bool = False,
    ) -> None:
        """Initialize the ImageClickController.

        Args:
            objective_store: Store for objective information and pixel size factor.
            camera_service: Camera service for getting binned pixel size.
            stage_service: Stage service used to perform the move synchronously.
            event_bus: Event bus for publishing/subscribing.
            inverted_objective: Whether the objective is inverted (affects Y sign).
        """
        super().__init__(event_bus)
        self._objective_store = objective_store
        self._camera_service = camera_service
        self._stage_service = stage_service
        self._inverted_objective = inverted_objective
        self._lock = threading.RLock()

        self._click_to_move_enabled = True  # Default enabled

    def set_click_to_move_enabled(self, enabled: bool) -> None:
        """Set whether click-to-move is enabled.

        Args:
            enabled: Whether to enable click-to-move functionality.
        """
        with self._lock:
            self._click_to_move_enabled = enabled

    @handles(ClickToMoveEnabledChanged)
    def _on_click_to_move_changed(self, event: ClickToMoveEnabledChanged) -> None:
        """Handle ClickToMoveEnabledChanged event."""
        self.set_click_to_move_enabled(event.enabled)

    def _get_pixel_size_um(self) -> Optional[float]:
        """µm per displayed-image pixel for the current objective and binning.

        Returns None when either factor is unavailable; some camera drivers raise
        NotImplementedError for unknown sensor models, which we fold into None so
        callers don't need their own try/except.
        """
        try:
            factor = self._objective_store.get_pixel_size_factor()
            binned_um = self._camera_service.get_pixel_size_binned_um()
        except (NotImplementedError, AttributeError):
            return None
        if factor is None or binned_um is None:
            return None
        return factor * binned_um

    @handles(ImageCoordinateClickedCommand)
    def _on_image_clicked(self, cmd: ImageCoordinateClickedCommand) -> None:
        """Handle ImageCoordinateClickedCommand.

        Converts pixel coordinates to stage movement and moves via StageService.

        Important: we perform the move before returning so that any subsequent queued
        commands (e.g. StartAcquisitionCommand(acquire_current_fov=True)) observe the
        updated stage position.
        """
        with self._lock:
            if not self._click_to_move_enabled:
                self._log.debug(
                    f"Click to move disabled, ignoring click at x={cmd.x_pixel}, y={cmd.y_pixel}"
                )
                return
            if self._stage_service is None:
                self._log.warning("No StageService available; ignoring image click move")
                return
            blocked_fn = getattr(self._stage_service, "_blocked_for_ui_hardware_commands", None)
            if callable(blocked_fn) and blocked_fn():
                self._log.info("Ignoring image click move due to global mode gate")
                return

            # Calculate pixel size in um (None when objective/camera unavailable)
            pixel_size_um = self._get_pixel_size_um()
            if pixel_size_um is None:
                self._log.warning("Click to move: pixel size unavailable, ignoring click")
                return

            # Sign corrections
            pixel_sign_x = 1
            pixel_sign_y = 1 if self._inverted_objective else -1

            # Convert pixels to mm
            delta_x_mm = pixel_sign_x * pixel_size_um * cmd.x_pixel / 1000.0
            delta_y_mm = pixel_sign_y * pixel_size_um * cmd.y_pixel / 1000.0

            self._log.debug(
                f"Click to move: click at x={cmd.x_pixel}, y={cmd.y_pixel} -> "
                f"delta_x={delta_x_mm:.4f}mm, delta_y={delta_y_mm:.4f}mm "
                f"(pixel_size={pixel_size_um:.3f}um)"
            )

        # Perform movement outside our lock (StageService has its own lock).
        # StageService moves are blocking by default; this avoids the "one click behind"
        # behavior when an acquisition starts immediately after click-to-move.
        try:
            self._stage_service.move_x(delta_x_mm)
            self._stage_service.move_y(delta_y_mm)
        except Exception:
            self._log.exception("Failed to move stage from image click")

    @handles(ImageScrollZCommand)
    def _on_image_scroll_z(self, cmd: ImageScrollZCommand) -> None:
        """Handle ImageScrollZCommand: Ctrl+Scroll Z navigation over the live view.

        Gated on click-to-move (so it can't fire during acquisition, which forces
        click-to-move off) and the global mode gate; clamped to the software Z
        limits. The move is non-blocking so rapid scrolling stays responsive.
        """
        with self._lock:
            if not self._click_to_move_enabled:
                return
            if self._stage_service is None:
                self._log.warning("No StageService available; ignoring z scroll")
                return
            blocked_fn = getattr(self._stage_service, "_blocked_for_ui_hardware_commands", None)
            if callable(blocked_fn) and blocked_fn():
                self._log.info("Ignoring z scroll due to global mode gate")
                return

            delta_mm = cmd.delta_um / 1000.0
            current_z_mm = self._stage_service.get_position().z_mm
            target_z_mm = current_z_mm + delta_mm
            if target_z_mm < SOFTWARE_POS_LIMIT.Z_NEGATIVE or target_z_mm > SOFTWARE_POS_LIMIT.Z_POSITIVE:
                self._log.debug(f"z scroll clamped: target={target_z_mm:.4f} mm out of bounds")
                return
            self._log.debug(f"z scroll: delta={cmd.delta_um:.2f} um, {current_z_mm=:.4f} -> {target_z_mm=:.4f}")

        try:
            self._stage_service.move_z(delta_mm, blocking=False)
        except Exception:
            self._log.exception("Failed to move stage Z from scroll")


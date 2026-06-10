"""Tests for ImageClickController — pixel-size safety + Ctrl+Scroll Z (ports #532)."""

from unittest.mock import MagicMock

import pytest

from squid.core.events import EventBus, ImageCoordinateClickedCommand, ImageScrollZCommand
from squid.backend.controllers.image_click_controller import ImageClickController

# From _def.SOFTWARE_POS_LIMIT: Z in [0.05, 7] mm.


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def objective_store():
    store = MagicMock()
    store.get_pixel_size_factor.return_value = 1.0
    return store


@pytest.fixture
def camera_service():
    svc = MagicMock()
    svc.get_pixel_size_binned_um.return_value = 0.5
    return svc


@pytest.fixture
def stage_service():
    svc = MagicMock()
    svc.get_position.return_value = MagicMock(z_mm=3.0)
    # Not mode-gated by default.
    svc._blocked_for_ui_hardware_commands = MagicMock(return_value=False)
    return svc


@pytest.fixture
def controller(objective_store, camera_service, stage_service, event_bus):
    return ImageClickController(objective_store, camera_service, stage_service, event_bus)


# --- Z scroll ---------------------------------------------------------------


def test_z_scroll_moves_within_bounds(controller, stage_service):
    controller._on_image_scroll_z(ImageScrollZCommand(delta_um=5.0))
    stage_service.move_z.assert_called_once_with(0.005, blocking=False)


def test_z_scroll_clamped_when_out_of_bounds(controller, stage_service):
    stage_service.get_position.return_value = MagicMock(z_mm=6.99)
    controller._on_image_scroll_z(ImageScrollZCommand(delta_um=40.0))  # -> 7.03 > 7
    stage_service.move_z.assert_not_called()


def test_z_scroll_noop_when_click_to_move_disabled(controller, stage_service):
    controller.set_click_to_move_enabled(False)
    controller._on_image_scroll_z(ImageScrollZCommand(delta_um=5.0))
    stage_service.move_z.assert_not_called()


def test_z_scroll_noop_when_mode_gated(controller, stage_service):
    stage_service._blocked_for_ui_hardware_commands.return_value = True
    controller._on_image_scroll_z(ImageScrollZCommand(delta_um=5.0))
    stage_service.move_z.assert_not_called()


# --- Click-to-move pixel-size None-safety -----------------------------------


def _click(controller):
    controller._on_image_clicked(
        ImageCoordinateClickedCommand(x_pixel=100, y_pixel=100, image_width=512, image_height=512)
    )


def test_click_ignored_when_factor_none(controller, objective_store, stage_service):
    objective_store.get_pixel_size_factor.return_value = None
    _click(controller)
    stage_service.move_x.assert_not_called()


def test_click_ignored_when_camera_raises_not_implemented(controller, camera_service, stage_service):
    camera_service.get_pixel_size_binned_um.side_effect = NotImplementedError
    _click(controller)
    stage_service.move_x.assert_not_called()


def test_click_moves_when_pixel_size_available(controller, stage_service):
    _click(controller)
    stage_service.move_x.assert_called_once()
    stage_service.move_y.assert_called_once()

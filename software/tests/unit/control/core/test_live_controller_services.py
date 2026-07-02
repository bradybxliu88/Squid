"""Tests for LiveController using services and EventBus."""

import pytest
from unittest.mock import MagicMock

from squid.core.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    TriggerModeChanged,
    TriggerFPSChanged,
    LiveStateChanged,
)
from squid.backend.controllers.live_controller import (
    LiveController,
    TriggerMode,
    LiveStateData,
    LiveControllerState,
)
from squid.core.abc import CameraAcquisitionMode


def make_controller(bus: EventBus):
    cam_service = MagicMock()
    illum_service = MagicMock()
    periph_service = MagicMock()
    controller = LiveController(
        camera_service=cam_service,
        event_bus=bus,
        illumination_service=illum_service,
        peripheral_service=periph_service,
    )
    return controller, cam_service, illum_service, periph_service


def test_start_stop_live_uses_camera_service():
    bus = EventBus()
    controller, cam_svc, _, periph_svc = make_controller(bus)

    events = []
    bus.subscribe(LiveStateChanged, events.append)

    bus.publish(StartLiveCommand(configuration="BF"))
    bus.drain()
    cam_svc.start_streaming.assert_called_once()
    assert controller.is_live is True
    assert controller.observable_state.is_live is True
    assert controller.observable_state.current_channel == "BF"
    assert events[-1].is_live is True

    events.clear()
    bus.publish(StopLiveCommand())
    bus.drain()
    cam_svc.stop_streaming.assert_called_once()
    assert controller.is_live is False
    assert any(evt.is_live is False for evt in events)


def test_trigger_mode_change_uses_service():
    bus = EventBus()
    controller, cam_svc, _, _ = make_controller(bus)

    events = []
    bus.subscribe(TriggerModeChanged, events.append)

    bus.publish(SetTriggerModeCommand(mode="Hardware"))
    bus.drain()
    cam_svc.set_acquisition_mode.assert_called_with(
        CameraAcquisitionMode.HARDWARE_TRIGGER
    )
    assert controller.trigger_mode == TriggerMode.HARDWARE
    assert events[-1].mode == "Hardware"


def test_trigger_mode_full_value_from_ui_dropdown():
    """Regression: the live-view dropdowns publish the FULL TriggerMode value
    (e.g. "Hardware Trigger"/"Continuous Acquisition"), not the short name.

    These must reach the camera as the correct acquisition mode. Before the fix
    to _str_to_trigger_mode, the full values silently fell through to SOFTWARE,
    so selecting Hardware/Continuous in the UI did nothing.
    """
    bus = EventBus()
    controller, cam_svc, _, _ = make_controller(bus)

    bus.publish(SetTriggerModeCommand(mode=TriggerMode.HARDWARE))  # "Hardware Trigger"
    bus.drain()
    cam_svc.set_acquisition_mode.assert_called_with(
        CameraAcquisitionMode.HARDWARE_TRIGGER
    )
    assert controller.trigger_mode == TriggerMode.HARDWARE

    bus.publish(SetTriggerModeCommand(mode=TriggerMode.CONTINUOUS))  # "Continuous Acquisition"
    bus.drain()
    cam_svc.set_acquisition_mode.assert_called_with(CameraAcquisitionMode.CONTINUOUS)
    assert controller.trigger_mode == TriggerMode.CONTINUOUS

    bus.publish(SetTriggerModeCommand(mode=TriggerMode.SOFTWARE))  # "Software Trigger"
    bus.drain()
    cam_svc.set_acquisition_mode.assert_called_with(
        CameraAcquisitionMode.SOFTWARE_TRIGGER
    )
    assert controller.trigger_mode == TriggerMode.SOFTWARE


@pytest.mark.parametrize(
    "mode_str, expected",
    [
        # full canonical values — what the real live-view dropdowns publish
        ("Software Trigger", TriggerMode.SOFTWARE),
        ("Hardware Trigger", TriggerMode.HARDWARE),
        ("Continuous Acquisition", TriggerMode.CONTINUOUS),
        # short title-case names — backend initial state / older callers / tests
        ("Software", TriggerMode.SOFTWARE),
        ("Hardware", TriggerMode.HARDWARE),
        ("Continuous", TriggerMode.CONTINUOUS),
        # lowercase
        ("software", TriggerMode.SOFTWARE),
        ("hardware", TriggerMode.HARDWARE),
        ("continuous", TriggerMode.CONTINUOUS),
        # unrecognized -> defensive SOFTWARE fallback (must not raise)
        ("bogus", TriggerMode.SOFTWARE),
        ("", TriggerMode.SOFTWARE),
    ],
)
def test_str_to_trigger_mode_accepts_all_vocabularies(mode_str, expected):
    assert LiveController._str_to_trigger_mode(mode_str) == expected


def test_trigger_fps_update():
    bus = EventBus()
    controller, _, _, _ = make_controller(bus)
    events = []
    bus.subscribe(TriggerFPSChanged, events.append)

    bus.publish(SetTriggerFPSCommand(fps=5.0))
    bus.drain()
    assert controller.observable_state.trigger_fps == 5.0
    assert events[-1].fps == 5.0


def test_live_controller_requires_illumination_service_when_enabled():
    bus = EventBus()
    cam_service = MagicMock()
    try:
        LiveController(camera_service=cam_service, event_bus=bus)
    except ValueError as exc:
        assert "IlluminationService" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError when illumination service missing")

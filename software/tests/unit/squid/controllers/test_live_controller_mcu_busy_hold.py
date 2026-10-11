"""LiveController must not fire triggers while the microcontroller is busy.

A trigger sent mid-command (usually a stage move) corrupts the firmware's single
command-status slot and wedges the MCU (see LiveController.trigger_acquisition).
"""

from unittest.mock import MagicMock, patch

import pytest

from squid.backend.controllers.live_controller import LiveController, LiveControllerState
from squid.core.events import EventBus


def _controller(peripheral_busy: bool = False, with_peripheral: bool = True):
    camera_service = MagicMock()
    camera_service.get_ready_for_trigger.return_value = True
    peripheral_service = None
    if with_peripheral:
        peripheral_service = MagicMock()
        peripheral_service.is_busy.return_value = peripheral_busy
    controller = LiveController(
        camera_service=camera_service,
        event_bus=EventBus(),
        illumination_service=MagicMock(),
        peripheral_service=peripheral_service,
    )
    return controller, camera_service, peripheral_service


@pytest.mark.parametrize("busy,expected_triggers", [(True, 0), (False, 1)])
def test_trigger_is_held_while_the_microcontroller_is_busy(busy, expected_triggers):
    controller, camera_service, _ = _controller(peripheral_busy=busy)

    with patch.object(controller, "turn_on_illumination") as turn_on_illumination:
        assert controller.trigger_acquisition() is (not busy)

    assert camera_service.send_trigger.call_count == expected_triggers
    # A held trigger must not send any other MCU command (illumination) either.
    assert turn_on_illumination.call_count == expected_triggers


def test_trigger_without_peripheral_service_is_not_held():
    controller, camera_service, _ = _controller(with_peripheral=False)

    assert controller.trigger_acquisition() is True
    camera_service.send_trigger.assert_called_once()


def test_busy_hold_rechecks_at_frame_cadence_not_every_10ms():
    # A held trigger re-checks at the normal frame interval: each re-check spins up a fresh
    # Timer thread, and a stage move lasts hundreds of ticks at 10 ms.
    controller, camera_service, _ = _controller(peripheral_busy=True)
    controller.is_live = True

    with patch.object(controller, "_start_new_timer") as start_new_timer:
        controller._trigger_acquisition_timer_fn()

    camera_service.send_trigger.assert_not_called()
    start_new_timer.assert_called_once_with()  # default interval == frame cadence


def test_camera_not_ready_still_retries_after_10ms():
    controller, camera_service, _ = _controller(peripheral_busy=False)
    camera_service.get_ready_for_trigger.return_value = False
    controller.is_live = True

    with patch.object(controller, "_start_new_timer") as start_new_timer:
        controller._trigger_acquisition_timer_fn()

    camera_service.send_trigger.assert_not_called()
    start_new_timer.assert_called_once_with(maybe_custom_interval_ms=10)


def test_live_resumes_by_itself_once_the_move_finishes():
    controller, camera_service, peripheral_service = _controller(peripheral_busy=True)
    controller.is_live = True

    with patch.object(controller, "_start_new_timer"), patch.object(controller, "turn_on_illumination"):
        controller._trigger_acquisition_timer_fn()
        assert camera_service.send_trigger.call_count == 0

        peripheral_service.is_busy.return_value = False
        controller._trigger_acquisition_timer_fn()
        assert camera_service.send_trigger.call_count == 1


def test_held_trigger_does_not_rearm_after_live_stopped():
    controller, _, _ = _controller(peripheral_busy=True)
    controller.is_live = False

    with patch.object(controller, "_start_new_timer") as start_new_timer:
        controller._trigger_acquisition_timer_fn()

    start_new_timer.assert_not_called()


def test_stop_live_survives_a_wedged_mcu_illumination_off_timeout():
    # Starting an acquisition stops live first; with a wedged MCU the illumination-off wait
    # times out. stop_live() must still stop live (trigger timer cancelled, stream stopped)
    # without raising, so MultiPointController.run_acquisition can go on to wait for the MCU.
    controller, camera_service, _ = _controller(peripheral_busy=False)
    with patch.object(controller, "_start_new_timer"):
        controller.start_live()
    assert controller.is_live is True

    with patch.object(controller, "turn_off_illumination", side_effect=TimeoutError("illumination-off wait")):
        controller.stop_live()  # must not raise

    assert controller.is_live is False
    assert controller.state is LiveControllerState.STOPPED
    assert controller.timer_trigger is None
    camera_service.stop_streaming.assert_called_once()

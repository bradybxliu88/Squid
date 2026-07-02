"""Tests for LiveController cleanup when _start_live() fails partway through.

Covers the M1 fix: a failed start_live() must best-effort undo the partial
startup (reset is_live, cancel the trigger timer, stop streaming, disable
callbacks, turn off illumination/AF laser, reset observable_state) so live
state is never left stuck True after a failed start.

These tests use a stateful fake camera whose get_ready_for_trigger() always
returns False and whose timers are daemon Timers; every test also calls
stop_live() to cancel any armed timer, so the file self-terminates. Run under
an OS-level timeout (pytest-timeout is not installed), e.g. from software/:
    timeout 120 python -m pytest \
        tests/unit/control/core/test_live_controller_start_failure.py \
        -p no:cacheprovider -q --no-header
"""

from unittest.mock import MagicMock

from squid.core.events import EventBus
from squid.backend.controllers.live_controller import (
    LiveController,
    LiveControllerState,
    TriggerMode,
)


class FakeCameraService:
    """Stateful fake camera service that can be made to fail start_streaming().

    Tracks streaming / callback state so cleanup can be observed, and can be
    toggled to raise on start_streaming() via ``fail_start``.
    """

    def __init__(self) -> None:
        self.streaming = False
        self.callbacks_enabled = False
        self.fail_start = False
        self.start_calls = 0
        self.stop_calls = 0

    def start_streaming(self) -> None:
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("boom")
        self.streaming = True

    def stop_streaming(self) -> None:
        self.stop_calls += 1
        self.streaming = False

    def enable_callbacks(self, enabled: bool) -> None:
        self.callbacks_enabled = enabled

    def get_is_streaming(self) -> bool:
        return self.streaming

    def get_ready_for_trigger(self) -> bool:
        # Never ready -> trigger_acquisition() returns False and the timer
        # callback does no hardware work, so armed timers are harmless.
        return False

    def get_total_frame_time(self) -> float:
        return 0.0

    def send_trigger(self) -> None:  # pragma: no cover - not reached (never ready)
        pass

    def set_acquisition_mode(self, mode) -> None:  # no-op
        pass

    def set_exposure_time(self, exposure) -> None:  # no-op
        pass

    def set_analog_gain(self, gain) -> None:  # no-op
        pass


def _make_controller(cam: FakeCameraService) -> LiveController:
    bus = EventBus()
    return LiveController(
        camera_service=cam,
        event_bus=bus,
        illumination_service=MagicMock(),
        peripheral_service=MagicMock(),
    )


def test_start_live_cleans_up_when_start_streaming_raises() -> None:
    """A failure in start_streaming() must fully undo the partial startup, and
    a subsequent start_live() must still succeed."""
    cam = FakeCameraService()
    c = _make_controller(cam)

    # First start fails inside start_streaming().
    cam.fail_start = True
    c.start_live("BF")

    assert c.is_live is False
    assert c.state is LiveControllerState.STOPPED
    assert cam.streaming is False
    assert cam.callbacks_enabled is False
    assert cam.stop_calls >= 1
    assert c.observable_state.is_live is False
    assert c.timer_trigger is None

    # A subsequent start (no failure) must succeed cleanly.
    cam.fail_start = False
    c.start_live("BF")

    assert c.is_live is True
    assert c.state is LiveControllerState.LIVE
    assert cam.streaming is True
    assert cam.callbacks_enabled is True

    # Always stop to cancel the daemon trigger timer.
    c.stop_live()
    assert c.is_live is False


def test_start_live_cleanup_cancels_timer_on_late_failure() -> None:
    """When a step AFTER the trigger timer is armed raises, cleanup must cancel
    the timer (timer_trigger is None), stop streaming, reset is_live, STOPPED."""
    cam = FakeCameraService()
    c = _make_controller(cam)
    # SOFTWARE trigger arms the timer during _start_live (this is the default,
    # set explicitly to make the intent clear).
    c.trigger_mode = TriggerMode.SOFTWARE

    real = c._start_triggerred_acquisition

    def arm_then_fail() -> None:
        real()  # arm the daemon timer as the real code would
        raise RuntimeError("post-timer failure")

    c._start_triggerred_acquisition = arm_then_fail

    c.start_live("BF")

    assert c.is_live is False
    assert c.state is LiveControllerState.STOPPED
    assert cam.streaming is False
    assert c.timer_trigger is None


def test_repeated_failed_starts_do_not_leak_state() -> None:
    """Alternating failed/successful starts over many iterations must never
    leave is_live stuck True, leak a timer, or corrupt the state machine."""
    cam = FakeCameraService()
    c = _make_controller(cam)

    for _ in range(30):  # fixed, self-terminating bound (no sleeps)
        # Failed start.
        cam.fail_start = True
        c.start_live("BF")
        assert c.is_live is False
        assert c.state is LiveControllerState.STOPPED

        # Successful start.
        cam.fail_start = False
        c.start_live("BF")
        assert c.is_live is True
        assert c.state is LiveControllerState.LIVE

        # Always stop to cancel the armed daemon timer (safety limit).
        c.stop_live()
        assert c.is_live is False

    assert c.timer_trigger is None

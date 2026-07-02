"""Regression tests for DefaultCamera trigger back-pressure (bug L6).

Previously ``send_trigger`` never set ``_in_trigger = True`` for the triggered
modes, so ``get_ready_for_trigger`` (``return not self._in_trigger``) always
returned True and provided no back-pressure. The fix sets ``_in_trigger = True``
after dispatch in both SOFTWARE_TRIGGER and HARDWARE_TRIGGER branches;
``_frame_callback`` clears it on real frame arrival for both modes.

DefaultCamera is built via ``object.__new__`` + mocks so the test needs no
Galaxy SDK / hardware. SimulatedCamera is a separate class and cannot exercise
this code path.
"""

import time
from unittest.mock import MagicMock

from squid.backend.drivers.cameras.base import DefaultCamera
from squid.core.abc import CameraAcquisitionMode


def _make(mode: CameraAcquisitionMode) -> DefaultCamera:
    cam = object.__new__(DefaultCamera)
    cam._log = MagicMock()
    cam._camera = MagicMock()
    cam._in_trigger = False
    cam._last_trigger_timestamp = 0.0
    cam._exposure_time_ms = 10
    cam._hw_trigger_fn = MagicMock()
    cam.get_is_streaming = lambda: True
    cam.get_acquisition_mode = lambda: mode
    return cam


def test_send_trigger_gates_in_software_trigger_mode():
    cam = _make(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    assert cam.get_ready_for_trigger() is True

    cam.send_trigger()

    assert cam._camera.TriggerSoftware.send_command.called
    assert cam._in_trigger is True
    assert cam.get_ready_for_trigger() is False

    # Simulate _frame_callback delivering a frame -> flag cleared.
    cam._in_trigger = False
    assert cam.get_ready_for_trigger() is True


def test_send_trigger_gates_in_hardware_trigger_mode():
    cam = _make(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert cam.get_ready_for_trigger() is True

    cam.send_trigger()

    assert cam._hw_trigger_fn.called
    assert cam._in_trigger is True
    assert cam.get_ready_for_trigger() is False


def test_send_trigger_continuous_mode_never_gates():
    cam = _make(CameraAcquisitionMode.CONTINUOUS)

    cam.send_trigger()

    assert cam._in_trigger is False
    assert cam.get_ready_for_trigger() is True
    assert cam._log.warning.called


def test_trigger_backpressure_cycles():
    """L6 stress: many send/frame-clear cycles alternate correctly, no stuck state.

    Purely synchronous (no sleeps) so it cannot hang; bounded loop.
    """
    cam = _make(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    for _ in range(200):
        assert cam.get_ready_for_trigger() is True
        cam.send_trigger()
        assert cam.get_ready_for_trigger() is False
        cam._in_trigger = False  # simulate _frame_callback frame arrival


def test_trigger_timeout_escape_clears_stuck_flag():
    """The timeout safety escape flips get_ready_for_trigger back to True."""
    cam = _make(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    cam.send_trigger()
    assert cam.get_ready_for_trigger() is False

    # No frame ever arrives; pretend the last trigger was long ago.
    cam._last_trigger_timestamp = time.time() - 10
    assert cam.get_ready_for_trigger() is True
    assert cam._log.warning.called

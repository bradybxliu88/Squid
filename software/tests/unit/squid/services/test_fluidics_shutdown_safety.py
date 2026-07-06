"""Fluidics shutdown safety: on app close the driver empties the syringe to
waste, but the pump's wait-for-stop is unbounded. FluidicsService.shutdown() must
still ATTEMPT the empty on every close, yet never let a stuck pump hang the whole
application shutdown.
"""

import threading
import time
from unittest.mock import MagicMock

from squid.backend.services.fluidics_service import FluidicsService


class _RecordingDriver:
    """Minimal fake AbstractFluidicsController for shutdown tests. Deliberately
    lacks set_phase_callback/set_progress_callback so __init__ skips wiring them."""

    def __init__(self, close_seconds: float = 0.0):
        self._close_seconds = close_seconds
        self.close_started = threading.Event()
        self.close_returned = threading.Event()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        if self._close_seconds:
            time.sleep(self._close_seconds)
        self.close_returned.set()


def test_shutdown_attempts_empty_to_waste_on_normal_close():
    driver = _RecordingDriver(close_seconds=0.0)
    svc = FluidicsService(driver=driver, event_bus=MagicMock())

    svc.shutdown()

    # The empty-to-waste (driver.close) ran to completion.
    assert driver.close_started.is_set()
    assert driver.close_returned.wait(timeout=2.0)


def test_shutdown_does_not_hang_on_a_stuck_pump():
    # A close that never returns in time must NOT wedge app shutdown.
    driver = _RecordingDriver(close_seconds=10.0)
    svc = FluidicsService(driver=driver, event_bus=MagicMock())
    svc.SHUTDOWN_CLOSE_TIMEOUT_S = 0.3  # bound the wait for the test

    start = time.monotonic()
    svc.shutdown()
    elapsed = time.monotonic() - start

    # It attempted the empty, but returned well before the 10s close finished.
    assert driver.close_started.wait(timeout=1.0)
    assert elapsed < 3.0
    assert not driver.close_returned.is_set()  # abandoned as a daemon, not awaited


def test_shutdown_is_idempotent_only_one_close():
    # A second shutdown() must NOT spawn a second close thread racing the pump.
    driver = _RecordingDriver(close_seconds=0.0)
    svc = FluidicsService(driver=driver, event_bus=MagicMock())

    svc.shutdown()
    svc.shutdown()  # no-op

    assert driver.close_returned.wait(timeout=2.0)
    assert driver.close_calls == 1


def test_shutdown_close_runs_under_service_lock():
    # The empty-to-waste (driver.close) must serialize with normal ops: if the
    # service lock is held, the close must not proceed until it's released.
    driver = _RecordingDriver(close_seconds=0.0)
    svc = FluidicsService(driver=driver, event_bus=MagicMock())
    svc.SHUTDOWN_CLOSE_TIMEOUT_S = 0.5

    with svc._lock:
        svc.shutdown()  # close thread blocks on the held lock
        assert not driver.close_started.wait(timeout=0.3)  # did not run while locked
    # Lock released — the abandoned daemon can now finish the close.
    assert driver.close_started.wait(timeout=2.0)


def test_shutdown_with_no_driver_is_noop():
    svc = FluidicsService(driver=None, event_bus=MagicMock())
    svc.shutdown()  # must not raise

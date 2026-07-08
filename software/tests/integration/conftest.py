import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_event_bus_subscribers():
    """Prevent zombie controllers from one test polluting the next.

    The EventBus is a process-wide singleton. Tests that build a controller on it
    without a context manager (e.g. test_MultiPointController.py via
    get_test_multi_point_controller) leave that controller *subscribed* after the
    test ends. A later test's StartNewExperiment/StartAcquisition commands are
    then also handled by the leftover controller, which runs a phantom
    acquisition of its stale scan coordinates and emits FOV/coordinate events
    stamped with the new test's experiment_id - inflating that test's image/FOV
    counts. BackendContext already clears the bus on __exit__; this fixture does
    the same for tests that don't use it, so every test starts with no leftover
    subscribers. Snapshot/restore rather than a blanket clear so subscribers
    registered before the test (e.g. at import time) survive.
    """
    from squid.core.events import event_bus

    with event_bus._lock:  # type: ignore[attr-defined]
        baseline = {et: list(hs) for et, hs in event_bus._subscribers.items()}
    yield
    with event_bus._lock:  # type: ignore[attr-defined]
        event_bus._subscribers.clear()
        event_bus._subscribers.update({et: list(hs) for et, hs in baseline.items()})


@pytest.fixture(scope="session", autouse=True)
def enable_fast_test_timing() -> None:
    """Enable faster timing defaults for integration tests."""
    prior_fast = os.environ.get("SQUID_TEST_FAST")
    prior_speedup = os.environ.get("SQUID_TEST_SPEEDUP")

    if "SQUID_TEST_FAST" not in os.environ and "SQUID_TEST_SPEEDUP" not in os.environ:
        os.environ["SQUID_TEST_FAST"] = "1"

    yield

    if prior_fast is None:
        os.environ.pop("SQUID_TEST_FAST", None)
    else:
        os.environ["SQUID_TEST_FAST"] = prior_fast

    if prior_speedup is None:
        os.environ.pop("SQUID_TEST_SPEEDUP", None)
    else:
        os.environ["SQUID_TEST_SPEEDUP"] = prior_speedup

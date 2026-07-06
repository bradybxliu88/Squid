"""Tests for fluidics pause/resume mid-step re-run, latched emergency stop, and
cancel-safety of run_protocol_blocking (Features 2 + 3).

Core correctness guarantee under test: pausing DURING a fluidics step must
re-run that step in full on resume, never skip it. Silently dropping a
partially-executed flow/incubate would under-deliver reagent / under-incubate
and corrupt a MERFISH round.
"""

from __future__ import annotations

import threading
import time

import pytest

from squid.core.events import EventBus
from squid.core.protocol.fluidics_protocol import (
    FluidicsProtocol,
    FluidicsProtocolStep,
    FluidicsCommand,
)
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.backend.controllers.fluidics_controller import (
    FluidicsController,
    FluidicsControllerState,
)


class ControllableService:
    """Fake FluidicsService whose FLOW of a named 'blocking' solution blocks
    (polling the abort flag) on its FIRST call, so a test can pause exactly while
    that step is in-flight. Subsequent calls return immediately, so a re-run on
    resume completes fast.
    """

    def __init__(self, blocking_solution: str = "probe"):
        self.is_available = True
        self._blocking_solution = blocking_solution
        self.flow_calls: list[str] = []
        self.incubate_calls: list[dict] = []
        self._abort = threading.Event()
        self.entered = threading.Event()  # set when we begin the blocking flow
        self.reset_abort_calls = 0

    def get_available_solutions(self):
        return {"probe": 1, "final": 2, "wash": 3}

    def reset_abort(self):
        self.reset_abort_calls += 1
        self._abort.clear()

    def abort(self):
        self._abort.set()

    def flow_solution_by_name(self, solution_name, volume_ul, flow_rate_ul_per_min):
        self.flow_calls.append(solution_name)
        first_blocking = (
            solution_name == self._blocking_solution
            and self.flow_calls.count(self._blocking_solution) == 1
        )
        if first_blocking:
            self.entered.set()
            # Block until aborted (mimics a driver honoring abort()).
            for _ in range(1000):
                if self._abort.is_set():
                    return False
                time.sleep(0.005)
            return True
        return True

    def incubate(self, duration_seconds, solution=None, progress_interval=1.0, cancel_token=None):
        self.incubate_calls.append({"cancel_token": cancel_token, "duration": duration_seconds})
        return True

    def wash(self, **kw):
        return True

    def empty_syringe(self):
        return True


def _flow_step(solution: str):
    return FluidicsProtocolStep(
        operation=FluidicsCommand.FLOW,
        solution=solution,
        volume_ul=100,
        flow_rate_ul_per_min=100,
    )


@pytest.fixture
def event_bus():
    return EventBus()


def _make_controller(event_bus, service):
    ctrl = FluidicsController(event_bus=event_bus, fluidics_service=service)
    return ctrl


def _wait_state(ctrl, state, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ctrl.state == state:
            return True
        time.sleep(0.01)
    return False


class TestPauseMidStepReRun:
    def test_pause_midstep_reruns_step_and_does_not_skip(self, event_bus):
        """The regression test for the mid-step-drop bug: pause during step 0,
        resume, and assert step 0 RE-RAN (probe appears twice) and step 1 ('final')
        ran AFTER — proving no step was skipped."""
        service = ControllableService(blocking_solution="probe")
        ctrl = _make_controller(event_bus, service)
        ctrl.add_protocol("p", FluidicsProtocol(steps=[_flow_step("probe"), _flow_step("final")]))

        assert ctrl.run_protocol("p") is True

        # Wait until the worker is inside the (blocking) probe flow, then pause.
        assert service.entered.wait(3.0), "worker never entered the probe flow"
        assert ctrl.pause() is True

        # It must land in PAUSED, with the interrupted step NOT advanced past.
        assert _wait_state(ctrl, FluidicsControllerState.PAUSED), "did not reach PAUSED"
        assert service.flow_calls == ["probe"], "final ran before resume (step skipped!)"

        # Resume: the interrupted probe step must re-run, THEN final.
        assert ctrl.resume() is True
        assert _wait_state(ctrl, FluidicsControllerState.IDLE), "did not complete"

        assert service.flow_calls == ["probe", "probe", "final"], (
            f"expected probe to re-run then final; got {service.flow_calls}"
        )
        assert ctrl.last_terminal_state == FluidicsControllerState.COMPLETED

    def test_pause_at_boundary_still_runs_pending_step(self, event_bus):
        """Pausing cleanly between steps must still run the pending step on
        resume (no off-by-one that skips it)."""
        # No blocking solution -> steps complete instantly; we pause after start.
        service = ControllableService(blocking_solution="__none__")
        ctrl = _make_controller(event_bus, service)
        ctrl.add_protocol("p", FluidicsProtocol(steps=[_flow_step("wash"), _flow_step("final")]))
        assert ctrl.run_protocol("p") is True
        assert _wait_state(ctrl, FluidicsControllerState.IDLE)
        # Both steps ran exactly once, in order.
        assert service.flow_calls == ["wash", "final"]


class TestEmergencyStop:
    def test_emergency_stop_latches_and_blocks_new_run(self, event_bus):
        service = ControllableService()
        ctrl = _make_controller(event_bus, service)
        ctrl.add_protocol("p", FluidicsProtocol(steps=[_flow_step("final")]))

        assert ctrl.emergency_stop() is True
        assert ctrl.is_emergency_stopped is True
        # A new protocol is refused while latched.
        assert ctrl.run_protocol("p") is False

        # Re-arm, then it runs.
        assert ctrl.clear_emergency_stop() is True
        assert ctrl.is_emergency_stopped is False
        assert ctrl.run_protocol("p") is True
        _wait_state(ctrl, FluidicsControllerState.IDLE)

    def test_clear_when_not_latched_returns_false(self, event_bus):
        service = ControllableService()
        ctrl = _make_controller(event_bus, service)
        assert ctrl.clear_emergency_stop() is False

    def test_emergency_stop_during_run_halts_and_stays_latched(self, event_bus):
        service = ControllableService(blocking_solution="probe")
        ctrl = _make_controller(event_bus, service)
        ctrl.add_protocol("p", FluidicsProtocol(steps=[_flow_step("probe"), _flow_step("final")]))
        assert ctrl.run_protocol("p") is True
        assert service.entered.wait(3.0)

        assert ctrl.emergency_stop() is True
        # Worker halts (probe aborted -> STOPPED -> IDLE) and 'final' never runs.
        assert _wait_state(ctrl, FluidicsControllerState.IDLE)
        assert "final" not in service.flow_calls
        assert ctrl.last_terminal_state == FluidicsControllerState.STOPPED
        # Still latched: a fresh run is refused until re-armed.
        assert ctrl.is_emergency_stopped is True
        assert ctrl.run_protocol("p") is False


class TestCancelSafety:
    def test_blocking_cancel_stops_executor_not_orphaned(self, event_bus):
        """A cancel raised out of run_protocol_blocking's poll loop must stop the
        executor worker (which drives hardware), not leave it running."""
        service = ControllableService(blocking_solution="probe")
        ctrl = _make_controller(event_bus, service)
        ctrl.add_protocol("p", FluidicsProtocol(steps=[_flow_step("probe"), _flow_step("final")]))

        token = CancelToken()
        holder: dict = {}

        def run():
            try:
                holder["ret"] = ctrl.run_protocol_blocking("p", cancel_token=token)
            except CancellationError as exc:
                holder["exc"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert service.entered.wait(3.0), "worker never entered probe flow"

        token.cancel("test cancel")
        t.join(6.0)

        assert not t.is_alive(), "run_protocol_blocking hung after cancel"
        assert "exc" in holder, "CancellationError was not propagated"
        # Executor stopped and returned to IDLE (not orphaned mid-flow).
        assert ctrl.state == FluidicsControllerState.IDLE
        assert "final" not in service.flow_calls

    def test_incubate_receives_active_cancel_token(self, event_bus):
        """The orchestrator cancel token must be threaded into incubation (was
        hard-coded None before)."""
        service = ControllableService()
        ctrl = _make_controller(event_bus, service)
        ctrl.add_protocol(
            "p",
            FluidicsProtocol(steps=[FluidicsProtocolStep(operation=FluidicsCommand.INCUBATE, duration_s=1)]),
        )
        token = CancelToken()
        ctrl.run_protocol_blocking("p", cancel_token=token)

        assert len(service.incubate_calls) == 1
        assert service.incubate_calls[0]["cancel_token"] is token

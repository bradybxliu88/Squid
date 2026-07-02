"""Unit tests for ExperimentRunner."""

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

import pytest

from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    ImagingStep,
    ImagingProtocol,
    FluidicsStep,
    StepFailurePolicy,
)
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.backend.controllers.orchestrator.experiment_runner import ExperimentRunner
from squid.backend.controllers.orchestrator.state import (
    ExperimentProgress,
    RoundProgress,
    StepOutcome,
    Checkpoint,
)

_MONO = "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"


def _make_runner(
    *,
    protocol: ExperimentProtocol | None = None,
    imaging_executor=None,
    fluidics_controller=None,
    scan_coordinates=None,
    start_from_fov: int = 0,
    on_checkpoint=None,
):
    protocol = protocol or ExperimentProtocol(
        name="test_protocol",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="round_1", steps=[ImagingStep(protocol="standard")])],
    )
    progress = ExperimentProgress()
    progress.current_round_index = 0
    progress.current_step_index = 0
    return ExperimentRunner(
        protocol=protocol,
        experiment_path="/tmp",
        experiment_id="exp1",
        cancel_token=CancelToken(),
        event_bus=MagicMock(),
        progress=progress,
        progress_lock=threading.RLock(),
        imaging_executor=imaging_executor,
        fluidics_controller=fluidics_controller,
        scan_coordinates=scan_coordinates,
        experiment_manager=object(),
        experiment_context=object(),
        protocol_path=None,
        on_operation_change=lambda _op: None,
        on_progress=lambda: None,
        on_checkpoint=on_checkpoint or (lambda: None),
        on_round_started=lambda *_: None,
        on_round_completed=lambda *_: None,
        on_transition=lambda *_: None,
        on_pause=lambda: True,
        on_add_warning=lambda **_: False,
        intervention_acknowledged=threading.Event(),
        start_from_fov=start_from_fov,
        step_time_estimates={(0, 0): 10.0},
        total_estimated_seconds=10.0,
    )


def test_compute_eta_ignores_pause_time():
    runner = _make_runner()
    runner._step_start_time = 100.0

    with patch(
        "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"
    ) as mono:
        mono.return_value = 105.0
        eta_before = runner.compute_eta()
        assert eta_before == pytest.approx(5.0)

        mono.return_value = 106.0
        eta_at_pause = runner.compute_eta()
        runner.notify_pause()

        mono.return_value = 110.0
        eta_during_pause = runner.compute_eta()
        assert eta_during_pause == pytest.approx(eta_at_pause)

        mono.return_value = 112.0
        runner.notify_resume()

        mono.return_value = 113.0
        eta_after = runner.compute_eta()
        assert eta_after == pytest.approx(3.0)


def test_request_skip_to_round_rejects_invalid_targets():
    protocol = ExperimentProtocol(
        name="skip_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[
            Round(name="r0", steps=[ImagingStep(protocol="standard")]),
            Round(name="r1", steps=[ImagingStep(protocol="standard")]),
        ],
    )
    runner = _make_runner(protocol=protocol)
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    assert runner.request_skip_to_round(-1) is False
    assert runner.request_skip_to_round(0) is False
    assert runner.request_skip_to_round(2) is False
    assert runner.request_skip_to_round(1) is True


def test_execute_fluidics_step_without_controller_fails():
    protocol = ExperimentProtocol(
        name="fluidics_test",
        rounds=[Round(name="r0", steps=[FluidicsStep(protocol="wash")])],
    )
    runner = _make_runner(protocol=protocol, fluidics_controller=None)
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_fluidics_step(0, FluidicsStep(protocol="wash"))
    assert result.outcome == StepOutcome.FAILED
    assert result.error_message == "No fluidics controller configured"


def test_execute_imaging_step_without_executor_fails():
    protocol = ExperimentProtocol(
        name="imaging_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    # Provide a run-level FOV plan so execution gets PAST the missing-FOV-plan
    # guard and actually reaches the "no executor configured" check this test
    # is named for.
    runner = _make_runner(
        protocol=protocol,
        imaging_executor=None,
        scan_coordinates=_three_fov_scan_coordinates(),
    )
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_imaging_step(0, ImagingStep(protocol="standard"))
    assert result.outcome == StepOutcome.FAILED
    assert result.error_message == "No imaging executor configured"


def test_execute_imaging_step_rejects_resume_fov_out_of_bounds():
    protocol = ExperimentProtocol(
        name="imaging_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    imaging_executor = MagicMock()
    scan_coordinates = MagicMock()
    scan_coordinates.region_fov_coordinates = {"region_1": [(0.0, 0.0, 0.0)]}
    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        scan_coordinates=scan_coordinates,
    )
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_imaging_step(0, ImagingStep(protocol="standard"), resume_fov=3)
    assert result.outcome == StepOutcome.FAILED
    assert "start_from_fov out of bounds" in (result.error_message or "")
    imaging_executor.execute_with_config.assert_not_called()


def test_run_saves_checkpoint_after_successful_step():
    protocol = ExperimentProtocol(
        name="checkpoint_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    imaging_executor = MagicMock()
    imaging_executor.execute_with_config.return_value = True
    on_checkpoint = MagicMock()
    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        on_checkpoint=on_checkpoint,
        # A loaded FOV plan is required so the imaging step runs to success
        # instead of failing the missing-FOV-plan guard (which, under the
        # default pause policy, would block on a never-arriving intervention).
        scan_coordinates=_three_fov_scan_coordinates(),
    )

    result = runner.run()
    assert result.outcome == StepOutcome.SUCCESS
    # At least one step-start checkpoint + one post-success checkpoint.
    assert on_checkpoint.call_count >= 2


def test_run_passes_start_from_fov_to_imaging_executor():
    protocol = ExperimentProtocol(
        name="resume_fov_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    imaging_executor = MagicMock()
    imaging_executor.execute_with_config.return_value = True
    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        start_from_fov=2,
        # 3-FOV plan so start_from_fov=2 is in bounds and the imaging step runs
        # (rather than failing the missing-FOV-plan guard and pausing forever).
        scan_coordinates=_three_fov_scan_coordinates(3),
    )

    result = runner.run()
    assert result.outcome == StepOutcome.SUCCESS
    kwargs = imaging_executor.execute_with_config.call_args.kwargs
    assert kwargs["resume_fov_index"] == 2


def test_execute_imaging_step_surfaces_executor_error_message():
    protocol = ExperimentProtocol(
        name="imaging_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    imaging_executor = MagicMock()
    imaging_executor.execute_with_config.return_value = False
    imaging_executor.last_error = "Focus lock verification failed before FOV capture"
    # FOV plan present so execution reaches the executor and surfaces its
    # last_error, instead of short-circuiting on the missing-FOV-plan guard.
    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        scan_coordinates=_three_fov_scan_coordinates(),
    )
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_imaging_step(0, ImagingStep(protocol="standard"))

    assert result.outcome == StepOutcome.FAILED
    assert result.error_message == "Focus lock verification failed before FOV capture"


# ---------------------------------------------------------------------------
# H2: step-advance / start-of-step checkpoint pairs the next step with FOV 0
# ---------------------------------------------------------------------------


def _three_fov_scan_coordinates(n: int = 3):
    scan_coordinates = MagicMock()
    scan_coordinates.region_fov_coordinates = {
        "region_1": [(float(i), 0.0, 0.0) for i in range(n)]
    }
    return scan_coordinates


def test_step_advance_checkpoint_pairs_next_step_with_fov_zero():
    """H2: the first checkpoint written with step_index==1 must carry
    imaging_fov_index==0, not the last FOV of step 0.

    A two-imaging-step round shares one FOV plan. Before the fix, the
    step-advance checkpoint (and the step-1 start checkpoint) inherited the
    final FOV of step 0 (==2). A resume from such a checkpoint would skip
    FOVs 0 and 1 of step 1 (data loss).
    """
    protocol = ExperimentProtocol(
        name="h2",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[
            Round(
                name="r0",
                steps=[
                    ImagingStep(protocol="standard"),
                    ImagingStep(protocol="standard"),
                ],
            )
        ],
    )
    scan_coordinates = _three_fov_scan_coordinates(3)

    def _execute(**kwargs):
        cb = kwargs["progress_callback"]
        resume = kwargs["resume_fov_index"]
        for fov in range(resume, 3):
            cb(fov, 3, None)
        return True

    imaging_executor = MagicMock()
    imaging_executor.execute_with_config.side_effect = _execute

    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        scan_coordinates=scan_coordinates,
    )

    snaps = []

    def rec():
        with runner._progress_lock:
            rp = runner._progress.current_round
            snaps.append(
                (
                    runner._progress.current_step_index,
                    rp.imaging_fov_index if rp else None,
                )
            )

    runner._on_checkpoint = rec

    result = runner.run()

    assert result.outcome == StepOutcome.SUCCESS
    # The first checkpoint captured with step_index==1 must show FOV 0.
    first_step1_fov = next(fov for (si, fov) in snaps if si == 1)
    assert first_step1_fov == 0


def test_resume_after_between_steps_checkpoint_starts_next_step_at_fov_zero():
    """H2 end-to-end: a between-steps checkpoint captured just before a crash
    on step 1, when fed to a fresh runner, resumes step 1 from FOV 0 (not from
    a stale FOV carried over from step 0)."""
    protocol = ExperimentProtocol(
        name="h2_resume",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[
            Round(
                name="r0",
                steps=[
                    ImagingStep(protocol="standard"),
                    ImagingStep(
                        protocol="standard",
                        failure_policy=StepFailurePolicy(
                            max_attempts=1, on_fail="abort"
                        ),
                    ),
                ],
            )
        ],
    )
    scan_coordinates = _three_fov_scan_coordinates(3)

    call_state = {"n": 0}

    def _execute1(**kwargs):
        call_state["n"] += 1
        cb = kwargs["progress_callback"]
        if call_state["n"] == 1:
            # Step 0: complete all three FOVs, then succeed.
            for fov in range(0, 3):
                cb(fov, 3, None)
            return True
        # Step 1: crash before imaging any FOV.
        return False

    imaging_executor1 = MagicMock()
    imaging_executor1.execute_with_config.side_effect = _execute1

    runner1 = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor1,
        scan_coordinates=scan_coordinates,
    )

    # on_checkpoint mirrors the real _save_checkpoint: snapshot the current
    # progress into a Checkpoint at every checkpoint boundary.
    latest = []

    def rec():
        with runner1._progress_lock:
            rp = runner1._progress.current_round
            latest.append(
                Checkpoint(
                    protocol_name=protocol.name,
                    protocol_version="1",
                    experiment_id="exp1",
                    experiment_path="/tmp",
                    round_index=runner1._progress.current_round_index,
                    step_index=runner1._progress.current_step_index,
                    imaging_fov_index=max(rp.imaging_fov_index, 0) if rp else 0,
                    current_attempt=1,
                    elapsed_seconds=0.0,
                    paused_seconds=0.0,
                    effective_run_seconds=0.0,
                )
            )

    runner1._on_checkpoint = rec

    with pytest.raises(CancellationError):
        runner1.run()

    ckpt = latest[-1]
    assert ckpt.step_index == 1
    assert ckpt.imaging_fov_index == 0

    # Resume on a fresh runner and confirm step 1 starts at FOV 0.
    imaging_executor2 = MagicMock()
    imaging_executor2.execute_with_config.return_value = True
    runner2 = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor2,
        scan_coordinates=_three_fov_scan_coordinates(3),
    )

    runner2.run(resume_checkpoint=ckpt)

    calls = imaging_executor2.execute_with_config.call_args_list
    # Step 0 was skipped; exactly one imaging call, resuming step 1 from FOV 0.
    assert len(calls) == 1
    assert calls[0].kwargs["resume_fov_index"] == 0


# ---------------------------------------------------------------------------
# L3: per-step timing reset preserves an active pause across a step boundary
# ---------------------------------------------------------------------------


def test_begin_step_timing_preserves_active_pause():
    """L3: a pause active at a step boundary is preserved by
    _begin_step_timing, so notify_resume still credits its full duration to
    _total_paused_seconds."""
    runner = _make_runner()
    with patch(_MONO) as mono:
        mono.return_value = 100.0
        runner._run_start_time = 0.0
        runner.notify_pause()
        assert runner._paused_at == 100.0

        mono.return_value = 105.0
        runner._begin_step_timing()
        # Active pause preserved across the step boundary.
        assert runner._paused_at is not None
        # Per-step tally reset, step clock restarted at "now".
        assert runner._step_paused_total == 0.0
        assert runner._step_start_time == 105.0

        mono.return_value = 110.0
        runner.notify_resume()
        # Full 10s pause (100 -> 110) credited to run-level total.
        assert runner._total_paused_seconds == pytest.approx(10.0)
        assert runner._step_paused_total == pytest.approx(10.0)


def test_begin_step_timing_resets_step_tally_when_not_paused():
    """L3: the non-paused path is unchanged: step tally reset to 0, _paused_at
    stays None, _step_start_time set to now."""
    runner = _make_runner()
    runner._step_paused_total = 5.0
    runner._paused_at = None
    with patch(_MONO) as mono:
        mono.return_value = 200.0
        runner._begin_step_timing()

    assert runner._step_paused_total == 0.0
    assert runner._paused_at is None
    assert runner._step_start_time == 200.0


# ---------------------------------------------------------------------------
# L4: imaging retry resumes from the FOV reached on the failed attempt
# ---------------------------------------------------------------------------


def test_imaging_retry_resumes_from_failed_fov():
    """L4: on a multi-attempt ImagingStep, attempt 2 resumes from the FOV
    reached on attempt 1 rather than re-imaging from FOV 0."""
    step = ImagingStep(
        protocol="standard",
        failure_policy=StepFailurePolicy(
            max_attempts=2, retry_delay_s=0.0, on_fail="abort"
        ),
    )
    protocol = ExperimentProtocol(
        name="l4",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[step])],
    )
    scan_coordinates = _three_fov_scan_coordinates(5)

    calls = []

    def _execute(**kwargs):
        cb = kwargs["progress_callback"]
        resume = kwargs["resume_fov_index"]
        calls.append(resume)
        if len(calls) == 1:
            # Attempt 1: reach FOV 2, then fail.
            for fov in range(resume, 3):
                cb(fov, 5, None)
            return False
        # Attempt 2: succeed.
        return True

    imaging_executor = MagicMock()
    imaging_executor.execute_with_config.side_effect = _execute

    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        scan_coordinates=scan_coordinates,
    )
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_step_with_retries(
        round_idx=0, step_idx=0, step=step, resume_fov=0
    )

    assert result.outcome == StepOutcome.SUCCESS
    # Attempt 1 started at FOV 0; attempt 2 resumed at FOV 2 (reached before failure).
    assert calls == [0, 2]


# ---------------------------------------------------------------------------
# L3 stress: pause/resume hammering across step boundaries never loses a pause
# ---------------------------------------------------------------------------


def test_l3_pause_step_boundary_stress_no_lost_pause():
    """L3 under concurrency: hammering notify_pause/notify_resume from one
    thread while calling _begin_step_timing from another must never yield a
    negative paused total, must not crash, and must stay finite.

    Self-terminating (fixed iteration count) so it cannot hang even without an
    external timeout wrapper (pytest-timeout is not installed).
    """
    runner = _make_runner()
    runner._run_start_time = time.monotonic() - 10.0
    runner._step_start_time = time.monotonic() - 5.0
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    errors = []
    N = 2000

    def pauser():
        for _ in range(N):
            runner.notify_pause()
            runner.notify_resume()
            snap = runner.get_timing_snapshot()
            paused = snap["paused_seconds"]
            if paused is None or paused < 0:
                errors.append(f"bad paused_seconds: {paused}")

    def boundary():
        for _ in range(N):
            runner._begin_step_timing()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pauser), pool.submit(boundary)]
        for f in as_completed(futures):
            f.result()

    assert not errors, errors[:5]
    final = runner.get_timing_snapshot()["paused_seconds"]
    assert final >= 0
    assert math.isfinite(runner._total_paused_seconds)
    assert runner._total_paused_seconds >= 0

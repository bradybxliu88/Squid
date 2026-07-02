"""Tests for MultiPointWorker.run() finally-block teardown resilience (bug H3).

The run() finally block wraps every teardown step in a _guard() so that an
exception in any teardown call (focus-lock teardown, stop_streaming,
remove_frame_callback, _finish_jobs) can no longer propagate out of the finally
and skip ProgressTracker.finish(). finish() drives the
AcquisitionWorkerFinished -> AcquisitionFinished publish path that the
orchestrator's imaging wait loop blocks on; if it is skipped the orchestrator
hangs forever with no operator present.

_guard() also distinguishes data-relevant steps from benign cleanup via
fail_acquisition:

* Data-relevant steps (wait_for_outstanding_callback_images, _finish_jobs) flip
  the run to failed (success=False) when they raise -- dropped/uncommitted
  frames or unflushed jobs mean the data is suspect.
* Benign post-capture cleanup (focus-lock teardown, timing report,
  stop_streaming, remove_frame_callback) is logged but must NOT flip an
  otherwise-successful acquisition to FAILED. All images are already saved by
  then; a spurious FAILED here would trigger an orchestrator retry/pause on an
  unattended overnight run.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker


def _build_bare_worker(*, stop_streaming_raises: bool = False, finish_jobs_raises: bool = False) -> MultiPointWorker:
    """Build a MultiPointWorker with only the attributes run() touches.

    Nt=0 so the acquisition while-loop body is skipped and the try-body
    completes cleanly without doing any real hardware work; the finally block
    then exercises the teardown guards.
    """
    worker = MultiPointWorker.__new__(MultiPointWorker)

    worker._log = MagicMock()
    worker._prepare_focus_lock_for_acquisition = MagicMock()
    worker._teardown_focus_lock_for_acquisition = MagicMock()
    worker._progress_tracker = MagicMock()
    worker._wait_for_outstanding_callback_images = MagicMock()
    worker._finish_jobs = MagicMock()
    if finish_jobs_raises:
        worker._finish_jobs.side_effect = RuntimeError("finish boom")
    worker._timing = MagicMock()
    worker._image_callback = MagicMock()
    worker._fatal_error = None
    worker._aborted = False
    worker.dt = 1.0
    worker.time_point = 0
    worker.Nt = 0

    camera_service = MagicMock()
    camera_service.add_frame_callback = MagicMock(return_value="cb-1")
    if stop_streaming_raises:
        camera_service.stop_streaming.side_effect = RuntimeError("stream boom")
    worker._camera_service = camera_service

    return worker


def test_run_finally_always_calls_finish_when_benign_teardown_raises():
    """A raising BENIGN teardown step must not skip finish() and must not fail the run."""
    worker = _build_bare_worker(stop_streaming_raises=True)

    # Must NOT raise, even though stop_streaming blows up mid-teardown.
    worker.run()

    # finish() still ran exactly once. stop_streaming is benign post-capture
    # cleanup, so the acquisition is still reported successful -- no spurious
    # orchestrator retry/pause on an overnight run.
    worker._progress_tracker.finish.assert_called_once()
    kwargs = worker._progress_tracker.finish.call_args.kwargs
    assert kwargs["success"] is True
    assert kwargs["error"] is None

    # Teardown continued past the raising stop_streaming step.
    worker._camera_service.remove_frame_callback.assert_called_once_with("cb-1")
    worker._finish_jobs.assert_called_once()


def test_run_finally_fails_run_when_data_relevant_teardown_raises():
    """A raising DATA-relevant teardown step (finish_jobs) fails the run, but finish() still fires."""
    worker = _build_bare_worker(finish_jobs_raises=True)

    # Must NOT raise, even though _finish_jobs blows up mid-teardown.
    worker.run()

    # finish() still ran exactly once, reporting failure with the teardown error.
    worker._progress_tracker.finish.assert_called_once()
    kwargs = worker._progress_tracker.finish.call_args.kwargs
    assert kwargs["success"] is False
    assert isinstance(kwargs["error"], RuntimeError)
    assert str(kwargs["error"]) == "finish boom"

    # The data-relevant step was still attempted.
    worker._finish_jobs.assert_called_once()


def test_run_finally_reports_success_when_teardown_clean():
    """Regression: a clean teardown must still report success=True, error=None."""
    worker = _build_bare_worker()

    worker.run()

    worker._progress_tracker.finish.assert_called_once()
    kwargs = worker._progress_tracker.finish.call_args.kwargs
    assert kwargs["success"] is True
    assert kwargs["error"] is None

    worker._camera_service.stop_streaming.assert_called_once()
    worker._camera_service.remove_frame_callback.assert_called_once_with("cb-1")
    worker._finish_jobs.assert_called_once()

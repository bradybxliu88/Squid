"""B1.3 tests: RunLogger resume continuity.

A fresh RunLogger started on an experiment folder that already holds pre-crash
step/fov metrics + a warning summary must PRELOAD them so the truncating "w"
CSV writes and warning_summary.json extend history instead of erasing it.
"""

import csv
import json
import time

from squid.core.events import EventBus
from squid.backend.controllers.multipoint import FovStatus, FovTaskCompleted, FovTaskStarted
from squid.backend.controllers.orchestrator.run_logger import RunLogger
from squid.backend.controllers.orchestrator.state import (
    OrchestratorStepCompleted,
    OrchestratorStepStarted,
    WarningRaised,
)


def _emit_step(bus, *, round_index, step_index, duration):
    bus.publish(
        OrchestratorStepStarted(
            experiment_id="exp1",
            round_index=round_index,
            step_index=step_index,
            step_type="imaging",
            estimated_seconds=10.0,
        )
    )
    bus.publish(
        OrchestratorStepCompleted(
            experiment_id="exp1",
            round_index=round_index,
            step_index=step_index,
            step_type="imaging",
            success=True,
            duration_seconds=duration,
        )
    )


def _emit_fov(bus, *, fov_id, fov_index, round_index):
    bus.publish(
        FovTaskStarted(
            fov_id=fov_id,
            fov_index=fov_index,
            region_id="A1",
            round_index=round_index,
            time_point=0,
            x_mm=1.0,
            y_mm=2.0,
            attempt=1,
            pending_count=1,
            completed_count=0,
        )
    )
    bus.publish(
        FovTaskCompleted(
            fov_id=fov_id,
            fov_index=fov_index,
            round_index=round_index,
            time_point=0,
            status=FovStatus.COMPLETED,
            attempt=1,
        )
    )


def _emit_warning(bus, *, category, severity, round_name):
    bus.publish(
        WarningRaised(
            experiment_id="exp1",
            category=category,
            severity=severity,
            message="w",
            round_index=0,
            round_name=round_name,
            time_point=0,
            fov_id="A1_0000",
            fov_index=0,
            total_warnings=1,
            warnings_in_category=1,
        )
    )


def _run_session(tmp_path, *, step, fov, warning):
    bus = EventBus()
    bus.start()
    logger = RunLogger(bus, str(tmp_path))
    logger.start()
    _emit_step(bus, **step)
    _emit_fov(bus, **fov)
    _emit_warning(bus, **warning)
    time.sleep(0.2)
    logger.stop()
    bus.stop()


def test_resume_preserves_pre_crash_history(tmp_path):
    # Pre-crash session.
    _run_session(
        tmp_path,
        step=dict(round_index=0, step_index=0, duration=5.0),
        fov=dict(fov_id="A1_0000", fov_index=0, round_index=0),
        warning=dict(category="FOCUS", severity="HIGH", round_name="Round 1"),
    )

    # Resume session on the SAME folder.
    _run_session(
        tmp_path,
        step=dict(round_index=1, step_index=0, duration=7.0),
        fov=dict(fov_id="A1_0001", fov_index=1, round_index=1),
        warning=dict(category="FOCUS", severity="HIGH", round_name="Round 2"),
    )

    with open(tmp_path / "step_metrics.csv", newline="") as f:
        step_rows = list(csv.DictReader(f))
    with open(tmp_path / "fov_metrics.csv", newline="") as f:
        fov_rows = list(csv.DictReader(f))

    # Both pre-crash and post-resume rows survive (no truncation).
    assert len(step_rows) == 2
    assert {r["round_index"] for r in step_rows} == {"0", "1"}
    assert len(fov_rows) == 2
    assert {r["fov_id"] for r in fov_rows} == {"A1_0000", "A1_0001"}

    # Warning counts accumulate across the resume (1 + 1 = 2).
    summary = json.loads((tmp_path / "warning_summary.json").read_text())
    assert summary["warning_counts"]["FOCUS:HIGH"] == 2
    assert set(summary["affected_rounds"]["FOCUS"]) == {"Round 1", "Round 2"}


def test_fresh_run_not_affected_by_preload(tmp_path):
    # A fresh folder has no artifacts, so preload is a no-op and output matches
    # a single non-resumed session exactly.
    _run_session(
        tmp_path,
        step=dict(round_index=0, step_index=0, duration=5.0),
        fov=dict(fov_id="A1_0000", fov_index=0, round_index=0),
        warning=dict(category="FOCUS", severity="HIGH", round_name="Round 1"),
    )

    with open(tmp_path / "step_metrics.csv", newline="") as f:
        step_rows = list(csv.DictReader(f))
    assert len(step_rows) == 1
    summary = json.loads((tmp_path / "warning_summary.json").read_text())
    assert summary["warning_counts"]["FOCUS:HIGH"] == 1


def test_defensive_preload_tolerates_garbage(tmp_path):
    # A corrupt pre-crash warning summary + garbage CSV must not break start().
    (tmp_path / "warning_summary.json").write_text("{ not json")
    (tmp_path / "step_metrics.csv").write_text("\x00\x01 not,a,valid\ncsv")

    bus = EventBus()
    bus.start()
    logger = RunLogger(bus, str(tmp_path))
    logger.start()  # must not raise despite the garbage
    _emit_warning(bus, category="FOCUS", severity="LOW", round_name="Round 1")
    time.sleep(0.15)
    logger.stop()
    bus.stop()

    summary = json.loads((tmp_path / "warning_summary.json").read_text())
    assert summary["warning_counts"]["FOCUS:LOW"] == 1

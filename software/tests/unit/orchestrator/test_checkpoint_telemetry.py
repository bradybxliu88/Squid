"""B1.3 tests: run telemetry carried across a resume via the checkpoint.

Covers the 5 new Checkpoint fields (retry/intervention overhead, subsystem
seconds, completed estimated/actual totals): save/load round-trip, backward
compatibility with pre-B1.3 checkpoints, and mutable-default safety.
"""

from datetime import datetime

from squid.backend.controllers.orchestrator import checkpoint as ckpt


def _kwargs(tmp_path):
    return dict(
        protocol_name="P",
        protocol_version="3.0",
        experiment_id="e",
        experiment_path=str(tmp_path),
        round_index=1,
    )


def test_telemetry_round_trip(tmp_path):
    cp = ckpt.create_checkpoint(
        **_kwargs(tmp_path),
        retry_overhead_seconds=3.0,
        intervention_overhead_seconds=5.0,
        subsystem_seconds={"imaging": 10.0, "fluidics": 2.0},
        completed_estimated_total=100.0,
        completed_actual_total=150.0,
    )
    ckpt.save_checkpoint(cp, str(tmp_path))
    loaded = ckpt.load_checkpoint(str(tmp_path))

    assert loaded.retry_overhead_seconds == 3.0
    assert loaded.intervention_overhead_seconds == 5.0
    assert loaded.subsystem_seconds == {"imaging": 10.0, "fluidics": 2.0}
    assert loaded.completed_estimated_total == 100.0
    assert loaded.completed_actual_total == 150.0


def test_backward_compat_missing_keys_default_to_zero():
    # A pre-B1.3 checkpoint.json dict lacking the 5 new keys must load cleanly.
    data = {
        "protocol_name": "P",
        "protocol_version": "3.0",
        "experiment_id": "e",
        "experiment_path": "/tmp",
        "round_index": 0,
        "created_at": datetime(2026, 7, 1).isoformat(),
    }
    cp = ckpt._checkpoint_from_dict(data)

    assert cp.retry_overhead_seconds == 0.0
    assert cp.intervention_overhead_seconds == 0.0
    assert cp.subsystem_seconds == {}
    assert cp.completed_estimated_total == 0.0
    assert cp.completed_actual_total == 0.0


def test_create_checkpoint_subsystem_default_not_shared(tmp_path):
    a = ckpt.create_checkpoint(**_kwargs(tmp_path), subsystem_seconds=None)
    b = ckpt.create_checkpoint(**_kwargs(tmp_path), subsystem_seconds=None)

    a.subsystem_seconds["x"] = 1.0
    assert b.subsystem_seconds == {}  # each call gets a distinct empty dict


def test_defaults_when_omitted(tmp_path):
    # Old callers that omit the new kwargs get inert 0.0/{} values.
    cp = ckpt.create_checkpoint(**_kwargs(tmp_path))
    assert cp.retry_overhead_seconds == 0.0
    assert cp.subsystem_seconds == {}
    assert cp.completed_actual_total == 0.0

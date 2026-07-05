"""B1.2 tests: one-click resume-from-checkpoint discovery + gating.

Covers OrchestratorControlPanel._discover_checkpoint (newest-by-created_at,
protocol-name hard filter, version-mismatch flag), the INDIVIDUAL_IMAGES gate on
the click handler (C1/C2), and the _update_button_states lifecycle guard.
"""

import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from squid.core.events import EventBus
from squid.backend.controllers.orchestrator import OrchestratorState
from squid.ui.widgets.orchestrator.orchestrator_widget import OrchestratorControlPanel


@pytest.fixture
def event_bus():
    bus = EventBus()
    bus.start()
    yield bus
    bus.stop()


@pytest.fixture
def panel(event_bus, qtbot, gui_dialog_patches):
    orchestrator = MagicMock()
    widget = OrchestratorControlPanel(event_bus=event_bus, orchestrator=orchestrator)
    qtbot.addWidget(widget)
    # Default: pretend the runtime writer is INDIVIDUAL_IMAGES so the gate is open.
    # Individual tests override this to exercise the closed gate.
    widget._individual_images_selected = lambda: True
    return widget


def _write_checkpoint(folder, *, name, version, created_at, round_index=0, fov_index=0):
    os.makedirs(folder, exist_ok=True)
    data = {
        "protocol_name": name,
        "protocol_version": version,
        "experiment_id": "exp",
        "experiment_path": str(folder),
        "round_index": round_index,
        "step_index": 0,
        "imaging_fov_index": fov_index,
        "imaging_z_index": 0,
        "imaging_channel_index": 0,
        "created_at": created_at.isoformat(),
        "paused_at": None,
        "metadata": {},
        "current_attempt": 1,
        "elapsed_seconds": 0.0,
        "paused_seconds": 0.0,
        "effective_run_seconds": 0.0,
    }
    with open(os.path.join(folder, "checkpoint.json"), "w") as f:
        json.dump(data, f)
    return str(folder)


def test_discover_picks_newest_same_protocol(panel, tmp_path):
    base = tmp_path / "out"
    t0 = datetime(2026, 7, 1, 8, 0, 0)
    old = _write_checkpoint(base / "run_old", name="MyProto", version="3.0", created_at=t0, round_index=0)
    new = _write_checkpoint(
        base / "run_new", name="MyProto", version="3.0", created_at=t0 + timedelta(hours=2), round_index=3, fov_index=7
    )
    panel._base_path = str(base)

    panel._discover_checkpoint("MyProto", "3.0")

    assert panel._resume_run_folder == new
    assert panel._resume_checkpoint.round_index == 3
    assert panel._resume_version_mismatch is False
    # Label reflects Round (1-based) / FOV (raw), and button is enabled (gate open).
    assert "Round 4" in panel._resume_ckpt_btn.text()
    assert "FOV 7" in panel._resume_ckpt_btn.text()
    assert panel._resume_ckpt_btn.isEnabled()


def test_discover_flags_version_mismatch(panel, tmp_path):
    base = tmp_path / "out"
    _write_checkpoint(base / "run", name="MyProto", version="2.9", created_at=datetime(2026, 7, 1))
    panel._base_path = str(base)

    panel._discover_checkpoint("MyProto", "3.0")

    assert panel._resume_run_folder is not None
    assert panel._resume_version_mismatch is True
    assert "version differs" in panel._resume_ckpt_btn.text()


def test_discover_excludes_other_protocol(panel, tmp_path):
    base = tmp_path / "out"
    _write_checkpoint(base / "run_other", name="OtherProto", version="3.0", created_at=datetime(2026, 7, 1))
    panel._base_path = str(base)

    panel._discover_checkpoint("MyProto", "3.0")

    assert panel._resume_run_folder is None
    assert panel._resume_checkpoint is None
    assert not panel._resume_ckpt_btn.isEnabled()


def test_discover_none_when_no_checkpoints(panel, tmp_path):
    base = tmp_path / "out"
    (base / "empty_run").mkdir(parents=True)
    panel._base_path = str(base)

    panel._discover_checkpoint("MyProto", "3.0")

    assert panel._resume_run_folder is None
    assert not panel._resume_ckpt_btn.isEnabled()


def test_discover_disabled_when_not_individual_images(panel, tmp_path):
    # A checkpoint exists, but the runtime writer is NOT INDIVIDUAL_IMAGES (C1/C2).
    panel._individual_images_selected = lambda: False
    base = tmp_path / "out"
    _write_checkpoint(base / "run", name="MyProto", version="3.0", created_at=datetime(2026, 7, 1))
    panel._base_path = str(base)

    panel._discover_checkpoint("MyProto", "3.0")

    assert panel._resume_run_folder is not None  # discovered...
    assert not panel._resume_ckpt_btn.isEnabled()  # ...but gated off


def test_resume_click_individual_images_calls_start(panel):
    panel._orchestrator.start_experiment.return_value = True
    panel._protocol_path = "/tmp/protocol.yaml"
    panel._resume_run_folder = "/tmp/out/run_new"
    panel._resume_checkpoint = MagicMock()
    panel._resume_version_mismatch = False

    panel._on_resume_from_checkpoint_clicked()

    panel._orchestrator.start_experiment.assert_called_once_with(
        protocol_path="/tmp/protocol.yaml",
        base_path="/tmp/out/run_new",
        experiment_id=None,
        resume_from_checkpoint=True,
    )


def test_resume_click_non_individual_does_not_start(panel):
    panel._individual_images_selected = lambda: False
    panel._protocol_path = "/tmp/protocol.yaml"
    panel._resume_run_folder = "/tmp/out/run_new"
    panel._resume_checkpoint = MagicMock()

    panel._on_resume_from_checkpoint_clicked()

    panel._orchestrator.start_experiment.assert_not_called()


def test_resume_click_no_checkpoint_does_not_start(panel):
    panel._protocol_path = "/tmp/protocol.yaml"
    panel._resume_run_folder = None
    panel._resume_checkpoint = None

    panel._on_resume_from_checkpoint_clicked()

    panel._orchestrator.start_experiment.assert_not_called()


def test_update_button_states_gates_resume(panel):
    panel._resume_run_folder = "/tmp/out/run"

    # Idle + folder + individual -> enabled
    panel._individual_images_selected = lambda: True
    panel._update_button_states(OrchestratorState.IDLE)
    assert panel._resume_ckpt_btn.isEnabled()

    # Running -> disabled even with a folder
    panel._update_button_states(OrchestratorState.RUNNING)
    assert not panel._resume_ckpt_btn.isEnabled()

    # Terminal + individual -> enabled again
    panel._update_button_states(OrchestratorState.COMPLETED)
    assert panel._resume_ckpt_btn.isEnabled()

    # Idle but NOT individual -> disabled
    panel._individual_images_selected = lambda: False
    panel._update_button_states(OrchestratorState.IDLE)
    assert not panel._resume_ckpt_btn.isEnabled()

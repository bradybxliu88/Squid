"""Regression test for orchestrator Round-label doubling (bug L8).

The progress section used to reassign ``self._round_label`` (created for the
status strip), so the strip's Round line never updated and both update handlers
fought over one QLabel. The fix gives the progress section its own
``_progress_round_label`` and drops the redundant 'Round: ' prefix (a static
QLabel('Round:') already sits next to it).
"""

from unittest.mock import MagicMock

from squid.ui.widgets.orchestrator.orchestrator_widget import OrchestratorControlPanel


def test_orchestrator_round_labels_are_distinct(qapp):
    panel = OrchestratorControlPanel.__new__(OrchestratorControlPanel)
    # Retain the returned containers so their child QLabels are not GC'd.
    panel._status_frame = panel._create_status_section()
    panel._progress_group = panel._create_progress_section()

    # Distinct objects with the right initial text.
    assert panel._round_label is not panel._progress_round_label
    assert panel._round_label.text() == "Round: -"  # strip label intact
    assert panel._progress_round_label.text() == "- / -"  # progress value only


def test_progress_update_has_no_doubled_round_prefix(qapp):
    panel = OrchestratorControlPanel.__new__(OrchestratorControlPanel)
    # Retain the returned containers so their child QLabels are not GC'd.
    panel._status_frame = panel._create_status_section()
    panel._progress_group = panel._create_progress_section()
    panel._history = {"progress": []}

    event = MagicMock()
    event.current_round = 1
    event.total_rounds = 3
    event.current_round_name = "foo"
    event.progress_percent = 10.0
    event.paused_seconds = 0
    event.retry_overhead_seconds = 0
    event.eta_seconds = 0

    panel._on_progress_updated_ui(event)

    assert "Round:" not in panel._progress_round_label.text()
    assert panel._progress_round_label.text() == "1 / 3  (foo)"
    # Strip label untouched by the progress handler.
    assert panel._round_label.text() == "Round: -"

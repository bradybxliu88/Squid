"""Regression test for orchestrator parameter-panel wiring (bug L9).

``OrchestratorWorkflowTree.set_param_panel`` was never called, so ``_param_panel``
stayed None and the live ``show_imaging_protocol(...)`` push in
``_handle_step_started_ui`` was silently skipped. main_window now calls
set_param_panel; these tests verify the wiring mechanism and the None guard.
"""

from unittest.mock import MagicMock

from squid.ui.widgets.orchestrator.orchestrator_widget import OrchestratorWorkflowTree


def _bare_tree() -> OrchestratorWorkflowTree:
    tree = OrchestratorWorkflowTree.__new__(OrchestratorWorkflowTree)
    tree._param_panel = None
    tree.update_item_status = MagicMock()
    tree._update_current_step_indicator = MagicMock()
    tree.set_time_estimate = MagicMock()
    tree._tree_items = {}
    return tree


def test_set_param_panel_assigns_panel(qapp):
    tree = _bare_tree()
    panel = MagicMock()
    tree.set_param_panel(panel)
    assert tree._param_panel is panel


def test_step_started_pushes_protocol_when_panel_set(qapp):
    tree = _bare_tree()
    panel = MagicMock()
    tree.set_param_panel(panel)

    proto = object()
    tree._handle_step_started_ui(0, 0, "imaging", 0.0, proto)

    assert panel.show_imaging_protocol.called


def test_step_started_no_panel_does_not_raise(qapp):
    tree = _bare_tree()  # _param_panel stays None
    proto = object()
    # Must not raise despite the panel being unset.
    tree._handle_step_started_ui(0, 0, "imaging", 0.0, proto)

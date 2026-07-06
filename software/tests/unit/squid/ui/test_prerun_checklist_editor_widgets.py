"""Headless smoke tests for the pre-run checklist + protocol editor widgets.

Handlers/slots are invoked directly (on the test's main thread) rather than
dispatched through the background EventBus thread, so widget mutation is safe
and deterministic on this headless Windows box. We only use the bus to (a) prove
@handles subscriptions were registered and (b) capture a published command.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

import pytest

from squid.core.events import (
    AcquisitionStateChanged,
    FluidicsProtocolsLoaded,
    FluidicsProtocolsLoadFailed,
    FocusLockStatusChanged,
    GlobalModeChanged,
    LaserAFReferenceSet,
    LoadFluidicsProtocolsCommand,
    ObjectiveChanged,
    ScanCoordinatesUpdated,
    EventBus,
)
from squid.backend.controllers.orchestrator.state import (
    OrchestratorStateChanged,
    ProtocolValidationComplete,
)
from squid.ui.widgets.orchestrator import protocol_editor_model as em


@pytest.fixture
def bus():
    b = EventBus()
    yield b
    try:
        b.stop()
    except Exception:
        pass


def _validation(valid, errors=()):
    return ProtocolValidationComplete(
        protocol_name="p",
        valid=valid,
        total_rounds=1,
        estimated_seconds=0.0,
        estimated_disk_bytes=0,
        operation_estimates=(),
        errors=tuple(errors),
        warnings=(),
    )


# ============================================================ checklist widget
def test_checklist_subscriptions_registered(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    for evt in (ProtocolValidationComplete, FluidicsProtocolsLoaded, ScanCoordinatesUpdated,
                ObjectiveChanged, LaserAFReferenceSet, FocusLockStatusChanged):
        assert evt in bus._subscribers and bus._subscribers[evt], f"{evt.__name__} not subscribed"


def test_checklist_auto_checks(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)

    w._on_validation_complete(_validation(True))
    assert w._checkboxes["protocol_validated"].isChecked()
    assert w._checkboxes["protocol_loaded"].isChecked()

    # An invalid re-validation clears the validated item
    w._on_validation_complete(_validation(False, errors=("boom",)))
    assert not w._checkboxes["protocol_validated"].isChecked()

    w._on_fluidics_loaded(FluidicsProtocolsLoaded(path="x", protocols={"a": {}, "b": {}}))
    assert w._checkboxes["fluidics_loaded"].isChecked()
    w._on_fluidics_load_failed(FluidicsProtocolsLoadFailed(path="x", error_message="bad"))
    assert not w._checkboxes["fluidics_loaded"].isChecked()

    w._on_scan_coords(ScanCoordinatesUpdated(total_regions=1, total_fovs=5, region_ids=("5",)))
    assert w._checkboxes["fov_present"].isChecked()
    w._on_scan_coords(ScanCoordinatesUpdated(total_regions=0, total_fovs=0, region_ids=()))
    assert not w._checkboxes["fov_present"].isChecked()

    w._on_objective(ObjectiveChanged(position=1, objective_name="60x"))
    assert w._checkboxes["objective_selected"].isChecked()

    w._on_laser_af_reference(LaserAFReferenceSet(success=True))
    assert w._checkboxes["laser_af_reference"].isChecked()

    w._on_focus_lock(FocusLockStatusChanged(is_locked=True, status="locked", lock_buffer_fill=5, lock_buffer_length=5))
    assert w._checkboxes["focus_locked"].isChecked()
    # Latches: a subsequent 'searching' does not un-check it
    w._on_focus_lock(FocusLockStatusChanged(is_locked=False, status="searching", lock_buffer_fill=0, lock_buffer_length=5))
    assert w._checkboxes["focus_locked"].isChecked()


def test_checklist_protocol_loaded_slot(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    # Pretend it was validated + focus locked, then a fresh load clears them
    w._on_validation_complete(_validation(True))
    w._on_focus_lock(FocusLockStatusChanged(is_locked=True, status="locked", lock_buffer_fill=5, lock_buffer_length=5))
    assert w._checkboxes["focus_locked"].isChecked()

    w.on_protocol_loaded({"output_directory": str(tmp_path)})  # tmp_path is writable
    assert w._checkboxes["protocol_loaded"].isChecked()
    assert w._checkboxes["output_path"].isChecked()  # writable dir -> green
    assert not w._checkboxes["protocol_validated"].isChecked()  # re-validate needed
    assert not w._checkboxes["focus_locked"].isChecked()  # cleared for the new run

    w.on_fov_positions_changed({"5": [1, 2, 3]})
    assert w._checkboxes["fov_present"].isChecked()


def test_checklist_output_path_not_writable(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    w.on_protocol_loaded({"output_directory": "Z:/definitely/not/here_12345"})
    assert not w._checkboxes["output_path"].isChecked()
    # And a missing output_directory does not raise and stays unchecked
    w.on_protocol_loaded({})
    assert not w._checkboxes["output_path"].isChecked()


def test_checklist_validation_invalidated_slot(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    w._on_validation_complete(_validation(True))
    assert w._checkboxes["protocol_validated"].isChecked()
    w.on_validation_invalidated()  # edited a file in-app
    assert not w._checkboxes["protocol_validated"].isChecked()


def test_checklist_seed_objective(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    w.seed_objective("60x")
    assert w._checkboxes["objective_selected"].isChecked()
    w.seed_objective(None)  # no-op, no crash


# ----------------------------------------- Run-environment notes + jump/detail
def test_checklist_shows_image_format_note(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    # The authoritative format is surfaced at construction, independent of a load.
    assert w._format_label.text()  # non-empty
    assert "Image format" in w._format_label.text()


def test_checklist_fluidics_divergence_note_on_load(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    with patch("_def.RUN_FLUIDICS", True), patch(
        "_def.FLUIDICS_CONFIG_PATH", "./merfish_config/MERFISH_config.json"
    ):
        w.on_protocol_loaded(
            {
                "output_directory": str(tmp_path),
                "resources": {"fluidics_config_file": str(tmp_path / "bundle_MERFISH.json")},
            }
        )
    # Different files -> warning surfaced and visible.
    assert not w._fluidics_config_label.isHidden()
    assert "reagent" in w._fluidics_config_label.text().lower()


def test_checklist_fov_freshness_note_on_load(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    fov = tmp_path / "fov_positions.csv"
    fov.write_text("region,x_mm,y_mm\n5,1,2\n")
    w.on_protocol_loaded(
        {"output_directory": str(tmp_path), "resources": {"fov_file": str(fov)}}
    )
    assert not w._fov_file_label.isHidden()
    assert "fov_positions.csv" in w._fov_file_label.text()


def test_checklist_laser_af_stamps_time_detail(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    w._on_laser_af_reference(LaserAFReferenceSet(success=True))
    assert w._checkboxes["laser_af_reference"].isChecked()
    # A visible freshness stamp ("set HH:MM") accompanies the checkmark.
    assert w._detail_labels["laser_af_reference"].text().startswith("set ")


def test_checklist_detail_labels_track_events(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    w._on_scan_coords(ScanCoordinatesUpdated(total_regions=1, total_fovs=25, region_ids=("5",)))
    assert "25" in w._detail_labels["fov_present"].text()
    w._on_objective(ObjectiveChanged(position=1, objective_name="60x"))
    assert "60x" in w._detail_labels["objective_selected"].text()


def test_checklist_jump_button_emits_navigate(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    seen = []
    w.navigate_requested.connect(seen.append)
    w._on_jump_clicked("Laser AF")
    assert seen == ["Laser AF"]


def test_checklist_manual_toggle(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)
    w._checkboxes["oil"].setChecked(True)  # user click -> toggled signal
    assert w._model.is_checked("oil")


def test_checklist_bad_event_does_not_crash(qtbot, bus):
    from squid.ui.widgets.orchestrator.prerun_checklist_panel import PreRunChecklistPanel

    w = PreRunChecklistPanel(event_bus=bus)
    qtbot.addWidget(w)

    class Weird:
        pass

    # Handlers use getattr with defaults, so a malformed event must not raise.
    w._on_objective(Weird())
    w._on_fluidics_loaded(Weird())
    w._on_focus_lock(Weird())


# =============================================================== editor widget
class _FakePanel:
    def __init__(self, path, data):
        self._bundle = {"protocol_path": path, "protocol_data": data}
        self.invalidated = []

    def get_loaded_protocol_bundle(self):
        return self._bundle

    def invalidate_validation(self, reason=""):
        self.invalidated.append(reason)


def _bundle(tmp_path):
    proto = tmp_path / "protocol.yaml"
    fluidics = tmp_path / "fluidics.yaml"
    fov = tmp_path / "fov.csv"
    proto.write_text("name: T\nversion: '3.0'\nrounds:\n  - name: R1\n    steps:\n      - step_type: fluidics\n        protocol: wash\n")
    fluidics.write_text("protocols:\n  wash:\n    steps:\n      - operation: wash\n        solution: Wash Buffer\n        volume_ul: 1000\n        flow_rate_ul_per_min: 300\n")
    fov.write_text("region,x_mm,y_mm,z_mm\n5,1,2,0\n")
    data = {
        "resources": {"fluidics_protocols_file": str(fluidics), "fov_file": str(fov)},
        "rounds": [{"steps": [{"step_type": "fluidics", "protocol": "wash"}]}],
    }
    return str(proto), data, str(fluidics), str(fov)


def test_editor_lists_bundle_files(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.protocol_editor_panel import ProtocolEditorPanel

    proto, data, _, _ = _bundle(tmp_path)
    panel = _FakePanel(proto, data)
    w = ProtocolEditorPanel(event_bus=bus, control_panel=panel)
    qtbot.addWidget(w)
    assert w._file_combo.count() >= 3  # protocol + fluidics + fov


def test_editor_saves_valid_file_and_invalidates(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.protocol_editor_panel import ProtocolEditorPanel

    proto, data, _, fov = _bundle(tmp_path)
    panel = _FakePanel(proto, data)
    w = ProtocolEditorPanel(event_bus=bus, control_panel=panel)
    qtbot.addWidget(w)

    w._current = em.EditableFile(path=fov, role=em.ROLE_FOV, label="fov", exists=True)
    w._editor.setPlainText("region,x_mm,y_mm,z_mm\n5,9.9,8.8,0\n")
    with patch("squid.ui.widgets.orchestrator.protocol_editor_panel.QMessageBox"):
        w._on_save_clicked()

    assert "9.9" in open(fov).read()
    assert panel.invalidated, "editing a file must invalidate the orchestrator's validation"
    assert not (tmp_path / "fov.csv.tmp").exists()
    assert (tmp_path / "fov.csv.bak").exists()


def test_editor_refuses_save_during_run(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.protocol_editor_panel import ProtocolEditorPanel

    proto, data, _, fov = _bundle(tmp_path)
    panel = _FakePanel(proto, data)
    w = ProtocolEditorPanel(event_bus=bus, control_panel=panel)
    qtbot.addWidget(w)

    original = open(fov).read()
    w._current = em.EditableFile(path=fov, role=em.ROLE_FOV, label="fov", exists=True)
    w._editor.setPlainText("region,x_mm,y_mm,z_mm\n5,0,0,0\n")

    # Simulate a run starting
    w._on_orch_state(OrchestratorStateChanged(old_state="IDLE", new_state="RUNNING", experiment_id="e"))
    assert w._run_active() is True
    assert not w._save_btn.isEnabled()

    with patch("squid.ui.widgets.orchestrator.protocol_editor_panel.QMessageBox") as mb:
        w._on_save_clicked()
        assert mb.warning.called
    assert open(fov).read() == original  # not written

    # Run ends -> save re-enabled
    w._on_orch_state(OrchestratorStateChanged(old_state="RUNNING", new_state="COMPLETED", experiment_id="e"))
    assert w._run_active() is False
    assert w._save_btn.isEnabled()


def test_editor_blocks_unparseable_save(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.protocol_editor_panel import ProtocolEditorPanel

    proto, data, fluidics, _ = _bundle(tmp_path)
    panel = _FakePanel(proto, data)
    w = ProtocolEditorPanel(event_bus=bus, control_panel=panel)
    qtbot.addWidget(w)

    original = open(fluidics).read()
    w._current = em.EditableFile(path=fluidics, role=em.ROLE_FLUIDICS_PROTOCOLS, label="fl", exists=True)
    w._editor.setPlainText(":\n  - broken: ][")  # not valid YAML
    with patch("squid.ui.widgets.orchestrator.protocol_editor_panel.QMessageBox") as mb:
        w._on_save_clicked()
        assert mb.warning.called
    assert open(fluidics).read() == original  # unparseable -> never written


def test_editor_fluidics_save_publishes_reload(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.protocol_editor_panel import ProtocolEditorPanel

    proto, data, fluidics, _ = _bundle(tmp_path)
    panel = _FakePanel(proto, data)
    w = ProtocolEditorPanel(event_bus=bus, control_panel=panel)
    qtbot.addWidget(w)

    collected = []
    bus.subscribe(LoadFluidicsProtocolsCommand, lambda e: collected.append(e))
    bus.start()

    w._current = em.EditableFile(path=fluidics, role=em.ROLE_FLUIDICS_PROTOCOLS, label="fl", exists=True)
    w._editor.setPlainText(open(fluidics).read())  # valid content
    with patch("squid.ui.widgets.orchestrator.protocol_editor_panel.QMessageBox"):
        w._on_save_clicked()
    bus.drain()
    assert any(getattr(c, "path", None) == fluidics for c in collected)


def test_editor_fluidics_config_save_warns_restart(qtbot, bus, tmp_path):
    from squid.ui.widgets.orchestrator.protocol_editor_panel import ProtocolEditorPanel

    proto, data, _, _ = _bundle(tmp_path)
    cfg = tmp_path / "MERFISH_config.json"
    cfg.write_text('{"microcontroller":{},"syringe_pump":{},"selector_valves":{},"solution_port_mapping":{}}')
    panel = _FakePanel(proto, data)
    w = ProtocolEditorPanel(event_bus=bus, control_panel=panel)
    qtbot.addWidget(w)

    collected = []
    bus.subscribe(LoadFluidicsProtocolsCommand, lambda e: collected.append(e))
    bus.start()

    w._current = em.EditableFile(path=str(cfg), role=em.ROLE_FLUIDICS_CONFIG, label="cfg", exists=True)
    w._editor.setPlainText('{"microcontroller":{"serial_number":"X"},"syringe_pump":{},"selector_valves":{},"solution_port_mapping":{"Wash Buffer":26}}')
    with patch("squid.ui.widgets.orchestrator.protocol_editor_panel.QMessageBox") as mb:
        w._on_save_clicked()
        # A hardware-config edit must warn that a restart is required.
        assert mb.warning.called
    bus.drain()
    # It must NOT masquerade as a live fluidics-protocol reload.
    assert not collected
    # File was still written (the edit is saved to disk).
    assert "serial_number" in cfg.read_text()

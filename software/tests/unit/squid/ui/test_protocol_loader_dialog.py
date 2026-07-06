"""Tests for the protocol loader dialog."""

from unittest.mock import MagicMock, patch

from squid.core.events import ClearScanCoordinatesCommand, LoadScanCoordinatesCommand
from squid.ui.widgets.orchestrator.protocol_loader_dialog import (
    ProtocolLoaderDialog,
    ProtocolLoaderPanel,
    _norm_dir,
)


def _imaging_protocol_text(output_directory: str) -> str:
    return "\n".join(
        [
            "name: Output Dir Test",
            'version: "3.0"',
            f"output_directory: {output_directory}",
            "resources:",
            "  fov_file: fovs.csv",
            "imaging_protocols:",
            "  standard:",
            "    channels: [BF]",
            "rounds:",
            "  - name: Round 1",
            "    steps:",
            "      - step_type: imaging",
            "        protocol: standard",
        ]
    )


def test_load_protocol_prefills_output_directory_and_fovs(qtbot, tmp_path):
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    fov_path = tmp_path / "fovs.csv"
    fov_path.write_text("region,x (mm),y (mm)\nregion_1,1.0,2.0\n")
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(
        "\n".join(
            [
                "name: Dialog Load Test",
                'version: "3.0"',
                "output_directory: outputs",
                "resources:",
                "  fov_file: fovs.csv",
                "imaging_protocols:",
                "  standard:",
                "    channels: [BF]",
                "rounds:",
                "  - name: Round 1",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: standard",
            ]
        )
    )

    dialog = ProtocolLoaderPanel()
    qtbot.addWidget(dialog)

    dialog._load_protocol(str(protocol_path))

    assert dialog.get_output_path() == str(outputs_dir)
    assert dialog.get_fov_path() == str(fov_path)
    assert dialog.get_fov_positions()["region_1"] == [(1.0, 2.0, 0.0)]
    assert dialog._start_btn.isEnabled() is True


def test_load_protocol_publishes_preview_fovs(qtbot, tmp_path):
    event_bus = MagicMock()
    fov_path = tmp_path / "fovs.csv"
    fov_path.write_text("region,x (mm),y (mm),z (mm)\nregion_1,20.0,30.0,1.0\n")
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(
        "\n".join(
            [
                "name: Dialog Preview Publish Test",
                'version: "3.0"',
                "resources:",
                "  fov_file: fovs.csv",
                "imaging_protocols:",
                "  standard:",
                "    channels: [BF]",
                "rounds:",
                "  - name: Round 1",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: standard",
            ]
        )
    )

    dialog = ProtocolLoaderPanel(event_bus=event_bus)
    qtbot.addWidget(dialog)

    dialog._load_protocol(str(protocol_path))

    published_events = [call.args[0] for call in event_bus.publish.call_args_list]
    assert any(isinstance(event, ClearScanCoordinatesCommand) for event in published_events)
    load_events = [
        event for event in published_events if isinstance(event, LoadScanCoordinatesCommand)
    ]
    assert len(load_events) == 1
    assert load_events[0].region_fov_coordinates == {"region_1": ((20.0, 30.0, 1.0),)}


def test_norm_dir_idempotent_and_normalizes_dots():
    assert _norm_dir("/a/b/") == _norm_dir("/a/b")
    assert _norm_dir("/a/./b") == _norm_dir("/a/b")


def test_output_override_is_flagged_and_revertible(qtbot, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    (tmp_path / "fovs.csv").write_text("region,x (mm),y (mm)\nr1,1.0,2.0\n")
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(_imaging_protocol_text("outputs"))

    dlg = ProtocolLoaderPanel()
    qtbot.addWidget(dlg)
    dlg._load_protocol(str(protocol))

    # The protocol's output_directory is mirrored in by default and matches.
    assert _norm_dir(dlg.get_output_path()) == _norm_dir(str(outputs))
    assert "Matches" in dlg._output_divergence_label.text()
    assert dlg._use_protocol_folder_btn.isHidden() is True

    # An explicit override is flagged and offers a reset button.
    dlg._output_edit.setText(str(other))
    assert "different folder" in dlg._output_divergence_label.text()
    assert dlg._use_protocol_folder_btn.isHidden() is False

    # Reverting restores the protocol's authoritative folder.
    dlg._on_use_protocol_folder()
    assert _norm_dir(dlg.get_output_path()) == _norm_dir(str(outputs))
    assert dlg._use_protocol_folder_btn.isHidden() is True


def test_output_dir_missing_from_protocol_prompts_but_does_not_crash(qtbot, tmp_path):
    (tmp_path / "fovs.csv").write_text("region,x (mm),y (mm)\nr1,1.0,2.0\n")
    protocol = tmp_path / "protocol.yaml"
    # No output_directory line.
    protocol.write_text(
        "\n".join(
            [
                "name: No Output Dir",
                'version: "3.0"',
                "resources:",
                "  fov_file: fovs.csv",
                "imaging_protocols:",
                "  standard:",
                "    channels: [BF]",
                "rounds:",
                "  - name: Round 1",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: standard",
            ]
        )
    )
    dlg = ProtocolLoaderPanel(default_path=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._load_protocol(str(protocol))
    # No protocol output_directory -> no reset button, no crash.
    assert dlg._protocol_output_dir is None
    assert dlg._use_protocol_folder_btn.isHidden() is True


def test_failed_reload_clears_stale_output_match(qtbot, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (tmp_path / "fovs.csv").write_text("region,x (mm),y (mm)\nr1,1.0,2.0\n")
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(_imaging_protocol_text("outputs"))

    dlg = ProtocolLoaderPanel()
    qtbot.addWidget(dlg)
    dlg._load_protocol(str(protocol))
    assert "Matches" in dlg._output_divergence_label.text()
    assert dlg._protocol_output_dir is not None

    # A subsequent FAILED load must not leave a stale '✓ Matches' indicator.
    dlg._load_protocol(str(tmp_path / "does_not_exist.yaml"))
    assert dlg._current_protocol is None
    assert dlg._protocol_output_dir is None
    assert "Matches" not in dlg._output_divergence_label.text()


def test_empty_output_field_message_is_honest(qtbot, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (tmp_path / "fovs.csv").write_text("region,x (mm),y (mm)\nr1,1.0,2.0\n")
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(_imaging_protocol_text("outputs"))

    dlg = ProtocolLoaderPanel()
    qtbot.addWidget(dlg)
    dlg._load_protocol(str(protocol))
    dlg._output_edit.setText("")  # operator clears the auto-filled field

    txt = dlg._output_divergence_label.text().lower()
    # Must not claim a fallback that doesn't exist; must point at the real remedy.
    assert "will default" not in txt
    assert "no output folder" in txt
    assert dlg._use_protocol_folder_btn.isHidden() is False


def test_panel_emits_load_requested_and_creates_output_dir(qtbot, tmp_path):
    outputs = tmp_path / "outputs"  # intentionally NOT pre-created
    (tmp_path / "fovs.csv").write_text("region,x (mm),y (mm)\nr1,1.0,2.0\n")
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(_imaging_protocol_text("outputs"))

    panel = ProtocolLoaderPanel()
    qtbot.addWidget(panel)
    panel._load_protocol(str(protocol))
    assert panel._start_btn.isEnabled() is True

    seen = []
    panel.load_requested.connect(lambda: seen.append(True))
    panel._on_start_clicked()

    # Commit signals the host and creates the output folder.
    assert seen == [True]
    assert outputs.exists()


def test_dialog_wrapper_delegates_and_accepts_on_commit(qtbot, tmp_path):
    from PyQt5.QtWidgets import QDialog

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (tmp_path / "fovs.csv").write_text("region,x (mm),y (mm)\nr1,1.0,2.0\n")
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(_imaging_protocol_text("outputs"))

    dialog = ProtocolLoaderDialog()
    qtbot.addWidget(dialog)
    dialog._panel._load_protocol(str(protocol))

    # Delegated getters reflect the embedded panel's state.
    assert _norm_dir(dialog.get_output_path()) == _norm_dir(str(outputs))
    assert dialog.get_fov_path() == str(tmp_path / "fovs.csv")
    assert dialog.base_path == dialog.get_output_path()

    # The panel's Load button commit accepts the wrapper dialog.
    dialog._panel._on_start_clicked()
    assert dialog.result() == QDialog.Accepted


def test_panel_refresh_recents_picks_up_new_entries(qtbot):
    import squid.ui.widgets.orchestrator.protocol_loader_dialog as mod

    panel = ProtocolLoaderPanel()
    qtbot.addWidget(panel)
    # The long-lived docked panel must re-read recents from disk on demand.
    with patch.object(mod, "_load_recents", return_value=["/x/new_protocol.yaml"]):
        panel.refresh_recents()
    datas = [panel._recent_combo.itemData(i) for i in range(panel._recent_combo.count())]
    assert "/x/new_protocol.yaml" in datas

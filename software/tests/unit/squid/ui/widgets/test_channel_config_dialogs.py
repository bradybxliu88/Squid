"""Tests for the channel configuration dialogs (Settings menu configurators).

Covers the acquisition-channel configurator's save path, which must propagate
added/removed channels to the per-objective configs (ports upstream 24276973),
and the illumination dialogs, which must edit private copies and publish to the
shared ConfigRepository only on Save (ports upstream edc13253).
"""

import pytest

import squid.ui.widgets.channel_config_dialogs as dialogs
from squid.core.config.models.acquisition_config import (
    AcquisitionChannel,
    CameraSettings,
    GeneralChannelConfig,
    IlluminationSettings,
    ObjectiveChannelConfig,
)
from squid.core.config.repository import ConfigRepository


def _acq_channel(name: str, exposure: float = 20.0, intensity: float = 30.0) -> AcquisitionChannel:
    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=1.0),
        illumination_settings=IlluminationSettings(illumination_channel=None, intensity=intensity),
    )


@pytest.fixture
def profile_repo(tmp_path):
    """Repository with a 'default' profile holding general.yaml plus two objective files."""
    (tmp_path / "machine_configs").mkdir()
    repo = ConfigRepository(base_path=tmp_path)
    repo.create_profile("default")
    repo.set_profile("default")
    repo.save_general_config("default", GeneralChannelConfig(channels=[_acq_channel("BF")]))
    for objective in ("10x", "20x"):
        repo.save_objective_config(
            "default", objective, ObjectiveChannelConfig(channels=[_acq_channel("BF", exposure=5.0)])
        )
    return repo


@pytest.fixture
def message_boxes(monkeypatch):
    """Record QMessageBox calls instead of opening modal dialogs."""
    calls = []

    def _record(kind):
        def _fn(*args, **kwargs):
            calls.append((kind, args))
            return dialogs.QMessageBox.Yes

        return _fn

    for kind in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(dialogs.QMessageBox, kind, _record(kind))
    return calls


class TestAcquisitionChannelConfiguratorSave:
    def test_added_channel_is_seeded_into_objective_configs(self, qtbot, profile_repo, message_boxes):
        dialog = dialogs.AcquisitionChannelConfiguratorDialog(profile_repo)
        qtbot.addWidget(dialog)
        # Same mutation _add_channel performs after the Add dialog is accepted
        dialog.general_config.channels.append(_acq_channel("New 561", exposure=42.0))
        dialog._load_channels()

        with qtbot.waitSignal(dialog.signal_channels_updated, timeout=1000):
            dialog._save_changes()

        assert not [c for c in message_boxes if c[0] in ("warning", "critical")]
        profile_repo.clear_profile_cache()
        for objective in ("10x", "20x"):
            ch = profile_repo.get_objective_config(objective).get_channel_by_name("New 561")
            assert ch is not None
            assert ch.camera_settings.exposure_time_ms == 42.0

        # A later live edit of the new channel now persists (no longer dropped)
        assert profile_repo.update_channel_setting("10x", "New 561", "ExposureTime", 123.0) is True
        merged = {ch.name: ch for ch in profile_repo.get_merged_channels("10x")}
        assert merged["New 561"].camera_settings.exposure_time_ms == 123.0

    def test_partial_objective_failure_warns_and_still_saves(self, qtbot, profile_repo, message_boxes):
        orig_save_yaml = profile_repo._save_yaml

        def failing_save_yaml(path, model):
            if path.name == "20x.yaml":
                raise PermissionError(f"denied: {path}")
            return orig_save_yaml(path, model)

        profile_repo._save_yaml = failing_save_yaml

        dialog = dialogs.AcquisitionChannelConfiguratorDialog(profile_repo)
        qtbot.addWidget(dialog)
        dialog.general_config.channels.append(_acq_channel("New 561"))
        dialog._load_channels()

        with qtbot.waitSignal(dialog.signal_channels_updated, timeout=1000):
            dialog._save_changes()

        warnings = [args for kind, args in message_boxes if kind == "warning"]
        assert len(warnings) == 1
        assert warnings[0][1] == "Partial Save"
        assert "20x" in warnings[0][2]
        assert not [c for c in message_boxes if c[0] == "critical"]
        assert dialog.result() == dialogs.QDialog.Accepted

        profile_repo._save_yaml = orig_save_yaml
        profile_repo.clear_profile_cache()
        assert profile_repo.get_general_config().get_channel_by_name("New 561") is not None
        assert profile_repo.get_objective_config("10x").get_channel_by_name("New 561") is not None
        assert profile_repo.get_objective_config("20x").get_channel_by_name("New 561") is None

    def test_general_save_failure_is_fatal(self, qtbot, profile_repo, message_boxes):
        def failing_save_yaml(path, model):
            raise PermissionError(f"denied: {path}")

        dialog = dialogs.AcquisitionChannelConfiguratorDialog(profile_repo)
        qtbot.addWidget(dialog)
        profile_repo._save_yaml = failing_save_yaml
        emitted = []
        dialog.signal_channels_updated.connect(lambda: emitted.append(True))

        dialog._save_changes()

        assert [args[1] for kind, args in message_boxes if kind == "critical"] == ["Save Failed"]
        assert emitted == []


ILLUMINATION_YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D2: 12
  USB1: 0
channels:
  - name: BF LED matrix full
    type: transillumination
    controller_port: USB1
    wavelength_nm: null
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
"""


@pytest.fixture
def illum_repo(tmp_path):
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(ILLUMINATION_YAML)
    return ConfigRepository(base_path=tmp_path)


def _set_port_mapping_in_dialog(dialog, port, source_code):
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 0).text() == port:
            widget = dialog.table.cellWidget(row, 1)
            assert isinstance(widget, dialogs.SourceCodeWidget)
            widget.set_source_code(source_code)
            return
    raise AssertionError(f"Port {port} not found in dialog table")


class TestIlluminationDialogsEditCopies:
    def test_port_mapping_dialog_save_updates_shared_repo_cache(self, qtbot, illum_repo, message_boxes):
        """Saving the port mapping dialog must update the repo other readers share."""
        dialog = dialogs.ControllerPortMappingDialog(illum_repo)
        qtbot.addWidget(dialog)
        _set_port_mapping_in_dialog(dialog, "D1", 16)

        dialog._save_changes()

        assert illum_repo.get_illumination_config().controller_port_mapping["D1"] == 16
        fresh = ConfigRepository(base_path=illum_repo.base_path).get_illumination_config()
        assert fresh.controller_port_mapping["D1"] == 16

    def test_port_mapping_save_keeps_channels_saved_since_dialog_opened(self, qtbot, illum_repo, message_boxes):
        """The mapping save is partial: it must not clobber channel edits saved after the dialog loaded."""
        dialog = dialogs.ControllerPortMappingDialog(illum_repo)
        qtbot.addWidget(dialog)
        _set_port_mapping_in_dialog(dialog, "D1", 16)

        # Another editor saves a channel change while the port dialog is open
        concurrent = illum_repo.get_illumination_config(for_edit=True)
        del concurrent.channels[0]
        illum_repo.save_illumination_config(concurrent)

        dialog._save_changes()

        saved = illum_repo.get_illumination_config()
        assert saved.controller_port_mapping["D1"] == 16
        assert len(saved.channels) == 1, "Port-mapping save must not restore the removed channel"

    def test_port_mapping_cancel_leaves_shared_config_unchanged(self, qtbot, illum_repo, message_boxes):
        dialog = dialogs.ControllerPortMappingDialog(illum_repo)
        qtbot.addWidget(dialog)
        _set_port_mapping_in_dialog(dialog, "D1", 16)
        dialog.reject()

        assert illum_repo.get_illumination_config().controller_port_mapping["D1"] == 11

    def test_configurator_cancel_leaves_shared_config_unchanged(self, qtbot, illum_repo, message_boxes):
        """Remove/move then Cancel must not mutate the shared cached config."""
        shared_before = illum_repo.get_illumination_config()
        assert [ch.name for ch in shared_before.channels] == ["BF LED matrix full", "Fluorescence 405 nm Ex"]

        dialog = dialogs.IlluminationChannelConfiguratorDialog(illum_repo)
        qtbot.addWidget(dialog)
        dialog.table.selectRow(1)
        dialog._move_up()
        dialog.table.selectRow(0)
        dialog._remove_channel()
        dialog.reject()

        shared_after = illum_repo.get_illumination_config()
        assert shared_after is shared_before
        assert [ch.name for ch in shared_after.channels] == ["BF LED matrix full", "Fluorescence 405 nm Ex"]

    def test_configurator_save_publishes_edits_to_shared_repo(self, qtbot, illum_repo, message_boxes):
        """Removing a channel and saving must update the shared cache and the YAML."""
        dialog = dialogs.IlluminationChannelConfiguratorDialog(illum_repo)
        qtbot.addWidget(dialog)
        dialog.table.selectRow(0)
        dialog._remove_channel()

        with qtbot.waitSignal(dialog.signal_channels_updated, timeout=1000):
            dialog._save_changes()

        shared = illum_repo.get_illumination_config()
        assert [ch.name for ch in shared.channels] == ["Fluorescence 405 nm Ex"]
        fresh = ConfigRepository(base_path=illum_repo.base_path).get_illumination_config()
        assert [ch.name for ch in fresh.channels] == ["Fluorescence 405 nm Ex"]

    def test_configurator_port_mapping_update_keeps_unsaved_channel_edits(self, qtbot, illum_repo, message_boxes):
        """A port-mapping save from inside the configurator refreshes the available ports
        without discarding the configurator's unsaved add/remove/move edits."""
        dialog = dialogs.IlluminationChannelConfiguratorDialog(illum_repo)
        qtbot.addWidget(dialog)
        dialog.table.selectRow(0)
        dialog._remove_channel()

        illum_repo.update_port_mapping({"D1": 11, "D2": 12, "D3": 13, "USB1": 0})
        dialog._on_port_mappings_updated()

        assert [ch.name for ch in dialog.illumination_config.channels] == ["Fluorescence 405 nm Ex"]
        assert dialog.illumination_config.controller_port_mapping["D3"] == 13
        port_combo = dialog.table.cellWidget(0, dialog.COL_PORT)
        assert "D3" in [port_combo.itemText(i) for i in range(port_combo.count())]
        # Shared config has the new mapping but still both channels (nothing saved yet)
        assert len(illum_repo.get_illumination_config().channels) == 2

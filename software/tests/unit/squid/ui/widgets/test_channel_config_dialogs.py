"""Tests for the channel configuration dialogs (Settings menu configurators).

Covers the acquisition-channel configurator's save path, which must propagate
added/removed channels to the per-objective configs (ports upstream 24276973).
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

"""Tests for ConfigRepository."""

import pytest
from pathlib import Path

from squid.core.config.repository import ConfigRepository
from squid.core.config.models.acquisition_config import (
    AcquisitionChannel,
    CameraSettings,
    GeneralChannelConfig,
    IlluminationSettings,
    ObjectiveChannelConfig,
)
from squid.core.config.models.illumination_config import (
    IlluminationType,
    IlluminationChannel,
    IlluminationChannelConfig,
)
from squid.core.config.models.filter_wheel_config import (
    FilterWheelType,
    FilterWheelDefinition,
    FilterWheelRegistryConfig,
)


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory structure."""
    machine_configs = tmp_path / "machine_configs"
    machine_configs.mkdir()
    user_profiles = tmp_path / "user_profiles"
    user_profiles.mkdir()
    return tmp_path


@pytest.fixture
def repo(tmp_config_dir):
    """Create a ConfigRepository with temporary directory."""
    return ConfigRepository(base_path=tmp_config_dir)


@pytest.fixture
def illumination_config():
    """Create a sample illumination config."""
    return IlluminationChannelConfig(
        controller_port_mapping={"D1": 11, "USB1": 0},
        channels=[
            IlluminationChannel(
                name="405nm Laser",
                type=IlluminationType.EPI_ILLUMINATION,
                controller_port="D1",
                wavelength_nm=405,
            ),
            IlluminationChannel(
                name="BF Full",
                type=IlluminationType.TRANSILLUMINATION,
                controller_port="USB1",
            ),
        ],
    )


def _make_channel(name="Test", exposure=20.0, gain=10.0, intensity=50.0, ill_channel=None):
    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=gain),
        illumination_settings=IlluminationSettings(
            illumination_channel=ill_channel, intensity=intensity
        ),
    )


class TestConfigRepositoryBasics:
    def test_init(self, repo, tmp_config_dir):
        assert repo.base_path == tmp_config_dir
        assert repo.current_profile is None

    def test_machine_configs_path(self, repo, tmp_config_dir):
        assert repo.machine_configs_path == tmp_config_dir / "machine_configs"

    def test_save_and_load_illumination(self, repo, illumination_config):
        repo.save_illumination_config(illumination_config)
        loaded = repo.get_illumination_config()
        assert loaded is not None
        assert len(loaded.channels) == 2
        assert loaded.channels[0].name == "405nm Laser"

    def test_illumination_for_edit_returns_isolated_copy(self, repo, illumination_config):
        """Editors get a deep copy; mutating it must not leak into the shared cache."""
        repo.save_illumination_config(illumination_config)
        shared = repo.get_illumination_config()

        working = repo.get_illumination_config(for_edit=True)
        assert working is not shared
        working.channels.pop()
        working.channels[0].controller_port = "D2"
        working.controller_port_mapping["D1"] = 99

        assert repo.get_illumination_config() is shared
        assert len(shared.channels) == 2
        assert shared.channels[0].controller_port == "D1"
        assert shared.controller_port_mapping["D1"] == 11

    def test_illumination_for_edit_none_when_missing(self, repo):
        assert repo.get_illumination_config(for_edit=True) is None

    def test_update_port_mapping_replaces_only_mapping(self, repo, illumination_config):
        repo.save_illumination_config(illumination_config)

        assert repo.update_port_mapping({"D1": 16, "USB1": 0}) is True

        shared = repo.get_illumination_config()
        assert shared.controller_port_mapping == {"D1": 16, "USB1": 0}
        assert [ch.name for ch in shared.channels] == ["405nm Laser", "BF Full"]
        reloaded = ConfigRepository(base_path=repo.base_path).get_illumination_config()
        assert reloaded.controller_port_mapping == {"D1": 16, "USB1": 0}

    def test_update_port_mapping_keeps_channel_edits_saved_since_snapshot(self, repo, illumination_config):
        """A mapping save is read-modify-write on the current cache, not on a stale snapshot."""
        repo.save_illumination_config(illumination_config)
        stale_snapshot = repo.get_illumination_config(for_edit=True)

        concurrent = repo.get_illumination_config(for_edit=True)
        del concurrent.channels[0]
        repo.save_illumination_config(concurrent)

        new_mapping = dict(stale_snapshot.controller_port_mapping, D1=16)
        assert repo.update_port_mapping(new_mapping) is True

        saved = repo.get_illumination_config()
        assert saved.controller_port_mapping["D1"] == 16
        assert [ch.name for ch in saved.channels] == ["BF Full"]

    def test_update_port_mapping_without_config(self, repo):
        assert repo.update_port_mapping({"D1": 11}) is False
        assert repo.get_illumination_config() is None

    def test_illumination_not_found(self, repo):
        assert repo.get_illumination_config() is None


class TestProfileManagement:
    def test_create_profile(self, repo):
        repo.create_profile("test_profile")
        assert repo.profile_exists("test_profile")

    def test_create_duplicate_raises(self, repo):
        repo.create_profile("test_profile")
        with pytest.raises(ValueError, match="already exists"):
            repo.create_profile("test_profile")

    def test_set_profile(self, repo):
        repo.create_profile("test_profile")
        repo.set_profile("test_profile")
        assert repo.current_profile == "test_profile"

    def test_set_nonexistent_profile_raises(self, repo):
        with pytest.raises(ValueError, match="does not exist"):
            repo.set_profile("nonexistent")

    def test_get_available_profiles(self, repo):
        repo.create_profile("alpha")
        repo.create_profile("beta")
        profiles = repo.get_available_profiles()
        assert profiles == ["alpha", "beta"]

    def test_copy_profile(self, repo):
        repo.create_profile("source")
        repo.set_profile("source")

        # Save a general config
        config = GeneralChannelConfig(channels=[_make_channel("DAPI")])
        repo.save_general_config("source", config)

        # Copy
        repo.copy_profile("source", "dest")
        assert repo.profile_exists("dest")

        # Verify config was copied
        repo.set_profile("dest")
        loaded = repo.get_general_config()
        assert loaded is not None
        assert len(loaded.channels) == 1


class TestChannelConfigs:
    def test_save_and_load_general(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        config = GeneralChannelConfig(
            channels=[_make_channel("DAPI", ill_channel="405nm")]
        )
        repo.save_general_config("test", config)

        loaded = repo.get_general_config()
        assert loaded is not None
        assert len(loaded.channels) == 1
        assert loaded.channels[0].name == "DAPI"

    def test_save_and_load_objective(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        config = ObjectiveChannelConfig(
            channels=[_make_channel("DAPI", exposure=100.0)]
        )
        repo.save_objective_config("test", "10x", config)

        loaded = repo.get_objective_config("10x")
        assert loaded is not None
        assert loaded.channels[0].camera_settings.exposure_time_ms == 100.0

    def test_get_merged_channels(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        general = GeneralChannelConfig(
            channels=[
                _make_channel("DAPI", exposure=20.0, ill_channel="405nm"),
                _make_channel("GFP", exposure=20.0, ill_channel="488nm"),
            ]
        )
        repo.save_general_config("test", general)

        objective = ObjectiveChannelConfig(
            channels=[_make_channel("DAPI", exposure=100.0)]
        )
        repo.save_objective_config("test", "10x", objective)

        merged = repo.get_merged_channels("10x")
        assert len(merged) == 2
        dapi = next(ch for ch in merged if ch.name == "DAPI")
        gfp = next(ch for ch in merged if ch.name == "GFP")
        # DAPI should have objective override
        assert dapi.exposure_time == 100.0
        # GFP should have general defaults
        assert gfp.exposure_time == 20.0
        # illumination_channel preserved from general
        assert dapi.illumination_settings.illumination_channel == "405nm"

    def test_update_channel_setting(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        general = GeneralChannelConfig(
            channels=[_make_channel("DAPI", ill_channel="405nm")]
        )
        repo.save_general_config("test", general)

        # Update should create objective config
        success = repo.update_channel_setting("10x", "DAPI", "ExposureTime", 200.0)
        assert success

        obj_config = repo.get_objective_config("10x")
        assert obj_config is not None
        ch = obj_config.get_channel_by_name("DAPI")
        assert ch is not None
        assert ch.camera_settings.exposure_time_ms == 200.0

    def test_update_channel_setting_zoffset(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        general = GeneralChannelConfig(
            channels=[_make_channel("DAPI", ill_channel="405nm")]
        )
        repo.save_general_config("test", general)

        success = repo.update_channel_setting("10x", "DAPI", "ZOffset", 5.0)
        assert success

        obj_config = repo.get_objective_config("10x")
        assert obj_config is not None
        ch = obj_config.get_channel_by_name("DAPI")
        assert ch is not None
        assert ch.z_offset_um == 5.0

    def test_get_available_objectives(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        general = GeneralChannelConfig(channels=[_make_channel("DAPI")])
        repo.save_general_config("test", general)

        for obj in ["10x", "20x", "40x"]:
            repo.save_objective_config(
                "test", obj, ObjectiveChannelConfig(channels=[_make_channel("DAPI")])
            )

        objectives = repo.get_available_objectives()
        assert set(objectives) == {"10x", "20x", "40x"}


class TestFilterWheelRegistry:
    def test_save_and_load(self, repo):
        config = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(
                    name="Emission",
                    id=1,
                    type=FilterWheelType.EMISSION,
                    positions={1: "Empty", 2: "DAPI"},
                )
            ]
        )
        repo.save_filter_wheel_registry(config)
        loaded = repo.get_filter_wheel_registry()
        assert loaded is not None
        assert len(loaded.filter_wheels) == 1
        assert loaded.filter_wheels[0].name == "Emission"


class TestAcquisitionOutput:
    def test_save_acquisition_output(self, repo, tmp_config_dir):
        output_dir = tmp_config_dir / "experiment_001"
        output_dir.mkdir()

        channels = [_make_channel("DAPI")]
        repo.save_acquisition_output(output_dir, "10x", channels)

        output_file = output_dir / "acquisition_channels.yaml"
        assert output_file.exists()


class TestCacheManagement:
    def test_clear_profile_cache(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        config = GeneralChannelConfig(channels=[_make_channel("DAPI")])
        repo.save_general_config("test", config)

        # Load to cache
        repo.get_general_config()
        assert "general" in repo._profile_cache

        repo.clear_profile_cache()
        assert "general" not in repo._profile_cache

    def test_clear_all_cache(self, repo, illumination_config):
        repo.save_illumination_config(illumination_config)
        repo.get_illumination_config()  # Cache
        assert "illumination" in repo._machine_cache

        repo.clear_all_cache()
        assert len(repo._machine_cache) == 0


class TestChannelMissingFromObjectiveConfig:
    """Channels added to general.yaml after objective files exist (e.g. via the channel
    configurator) must still be editable and persist per-objective.

    Ported from upstream 24276973 (tests/control/core/config/test_repository.py).
    """

    @pytest.fixture
    def repo_with_new_channel(self, tmp_path):
        """Profile where '561nm - 1' exists in general.yaml but not in the objective files."""
        machine = tmp_path / "machine_configs"
        machine.mkdir()
        (machine / "illumination_channel_config.yaml").write_text(
            "version: 1\n"
            "channels:\n"
            '  - name: "488nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D2\n"
            "    wavelength_nm: 488\n"
            '  - name: "561nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D3\n"
            "    wavelength_nm: 561\n"
        )
        profile = tmp_path / "user_profiles" / "default"
        (profile / "channel_configs").mkdir(parents=True)
        (profile / "laser_af_configs").mkdir()
        (profile / "channel_configs" / "general.yaml").write_text(
            "version: 1.0\n"
            "channels:\n"
            '  - name: "488nm"\n'
            '    display_color: "#1FFF00"\n'
            "    camera_settings: {exposure_time_ms: 20.0, gain_mode: 10.0}\n"
            '    illumination_settings: {illumination_channel: "488nm", intensity: 20.0}\n'
            "    z_offset_um: 0.0\n"
            '  - name: "561nm - 1"\n'
            '    display_color: "#FF8000"\n'
            "    camera_settings: {exposure_time_ms: 30.0, gain_mode: 5.0}\n"
            '    illumination_settings: {illumination_channel: "561nm", intensity: 44.0}\n'
            "    filter_position: 2\n"
            "    z_offset_um: 1.5\n"
        )
        objective_yaml = (
            "version: 1.0\n"
            "channels:\n"
            '  - name: "488nm"\n'
            '    display_color: "#1FFF00"\n'
            "    camera_settings: {exposure_time_ms: 50.0, gain_mode: 1.0}\n"
            "    illumination_settings: {intensity: 75.0}\n"
        )
        (profile / "channel_configs" / "20x.yaml").write_text(objective_yaml)
        (profile / "channel_configs" / "4x.yaml").write_text(objective_yaml)
        repo = ConfigRepository(base_path=tmp_path)
        repo.set_profile("default")
        return repo

    def test_update_seeds_missing_channel_and_persists(self, repo_with_new_channel):
        """Editing a channel absent from the objective file seeds it there and saves the edit."""
        result = repo_with_new_channel.update_channel_setting("20x", "561nm - 1", "ExposureTime", 123.0)
        assert result is True

        # Persisted to YAML — clear cache and reload
        repo_with_new_channel.clear_profile_cache()
        obj = repo_with_new_channel.get_objective_config("20x")
        ch = obj.get_channel_by_name("561nm - 1")
        assert ch is not None
        assert ch.camera_settings.exposure_time_ms == 123.0
        # Other per-objective settings seeded from general.yaml
        assert ch.camera_settings.gain_mode == 5.0
        assert ch.illumination_settings.intensity == 44.0
        assert ch.z_offset_um == 1.5
        # Channel identity stays in general.yaml, not in the objective file
        assert ch.filter_position is None
        assert ch.illumination_settings.illumination_channel is None

        # The merged view reflects the edit (and keeps identity from general)
        merged = repo_with_new_channel.get_merged_channels("20x")
        merged_ch = next(c for c in merged if c.name == "561nm - 1")
        assert merged_ch.camera_settings.exposure_time_ms == 123.0
        assert merged_ch.filter_position == 2
        assert merged_ch.illumination_settings.illumination_channel == "561nm"

    def test_update_unknown_channel_still_fails(self, repo_with_new_channel):
        """A channel in neither general nor objective config is still rejected."""
        assert repo_with_new_channel.update_channel_setting("20x", "no such channel", "ExposureTime", 10.0) is False

    def test_update_failed_save_does_not_poison_cache(self, repo_with_new_channel):
        """A failed objective write must leave the cached objective config untouched."""
        repo = repo_with_new_channel
        cached_before = repo.get_objective_config("20x")

        def failing_save_yaml(path, model):
            raise PermissionError(f"denied: {path}")

        repo._save_yaml = failing_save_yaml
        with pytest.raises(PermissionError):
            repo.update_channel_setting("20x", "488nm", "ExposureTime", 999.0)
        with pytest.raises(PermissionError):
            repo.update_channel_setting("20x", "561nm - 1", "ExposureTime", 999.0)

        cached = repo.get_objective_config("20x")
        assert cached is cached_before
        assert cached.get_channel_by_name("488nm").camera_settings.exposure_time_ms == 50.0
        assert cached.get_channel_by_name("561nm - 1") is None

    @staticmethod
    def _make_channel(name, exposure=15.0, intensity=33.0):
        return AcquisitionChannel(
            name=name,
            display_color="#FFFFFF",
            camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=2.0),
            illumination_settings=IlluminationSettings(illumination_channel="561nm", intensity=intensity),
        )

    def test_save_with_sync_seeds_only_added_channels(self, repo_with_new_channel):
        """Channels added in this save are seeded into every objective file; channels that
        were already in general.yaml but absent from an objective file are NOT re-added."""
        general = repo_with_new_channel.get_general_config()
        general.channels.append(self._make_channel("561nm - 2"))

        failed = repo_with_new_channel.save_general_config_with_sync("default", general)
        assert failed == []

        repo_with_new_channel.clear_profile_cache()
        for objective in ("20x", "4x"):
            obj = repo_with_new_channel.get_objective_config(objective)
            ch = obj.get_channel_by_name("561nm - 2")
            assert ch is not None, f"'561nm - 2' missing from {objective}"
            assert ch.camera_settings.exposure_time_ms == 15.0
            assert ch.illumination_settings.intensity == 33.0
            # '561nm - 1' was already in general before this save: its absence from the
            # objective file is preserved (a user's fallback-to-general is respected).
            assert obj.get_channel_by_name("561nm - 1") is None
            # Existing entry untouched (keeps objective-specific values)
            ch488 = obj.get_channel_by_name("488nm")
            assert ch488.camera_settings.exposure_time_ms == 50.0
            assert ch488.illumination_settings.intensity == 75.0

    def test_save_with_sync_diffs_against_disk_when_cache_mutated_in_place(self, repo_with_new_channel):
        """The configurator dialog appends to the cached GeneralChannelConfig in place; the
        sync must still detect the new channel by diffing against general.yaml on disk."""
        general = repo_with_new_channel.get_general_config()
        general.channels.append(self._make_channel("561nm - 2"))
        # The cache already contains the new channel before the save
        assert repo_with_new_channel.get_general_config().get_channel_by_name("561nm - 2") is not None

        assert repo_with_new_channel.save_general_config_with_sync("default", general) == []

        assert repo_with_new_channel.get_objective_config("20x").get_channel_by_name("561nm - 2") is not None

    def test_save_with_sync_prunes_removed_channels_and_prevents_resurrection(self, repo_with_new_channel):
        """Removing a channel prunes its objective entries; re-adding the same name later
        seeds fresh settings instead of resurrecting the stale ones."""
        # Materialize '561nm - 1' in 20x.yaml with a distinctive value via the lazy seed path
        assert repo_with_new_channel.update_channel_setting("20x", "561nm - 1", "ExposureTime", 123.0) is True

        # Remove the channel in the configurator and save
        general = repo_with_new_channel.get_general_config()
        general.channels = [ch for ch in general.channels if ch.name != "561nm - 1"]
        assert repo_with_new_channel.save_general_config_with_sync("default", general) == []

        repo_with_new_channel.clear_profile_cache()
        assert repo_with_new_channel.get_objective_config("20x").get_channel_by_name("561nm - 1") is None

        # Re-add a channel with the same name but different settings
        general = repo_with_new_channel.get_general_config()
        general.channels.append(self._make_channel("561nm - 1", exposure=10.0, intensity=5.0))
        assert repo_with_new_channel.save_general_config_with_sync("default", general) == []

        repo_with_new_channel.clear_profile_cache()
        ch = repo_with_new_channel.get_objective_config("20x").get_channel_by_name("561nm - 1")
        assert ch is not None
        assert ch.camera_settings.exposure_time_ms == 10.0  # not the stale 123.0
        assert ch.illumination_settings.intensity == 5.0

    def test_save_with_sync_objective_failure_is_isolated_and_does_not_poison_cache(self, repo_with_new_channel):
        """A failing objective write is reported, does not block other objectives, and
        leaves neither the cache nor the disk with the unsaved change."""
        orig_save_yaml = repo_with_new_channel._save_yaml

        def failing_save_yaml(path, model):
            if path.name == "20x.yaml":
                raise PermissionError(f"denied: {path}")
            return orig_save_yaml(path, model)

        repo_with_new_channel._save_yaml = failing_save_yaml

        general = repo_with_new_channel.get_general_config()
        general.channels.append(self._make_channel("561nm - 2"))
        failed = repo_with_new_channel.save_general_config_with_sync("default", general)
        assert failed == ["20x"]

        # 4x still synced despite the 20x failure
        assert repo_with_new_channel.get_objective_config("4x").get_channel_by_name("561nm - 2") is not None
        # Cached 20x config does not contain the never-persisted channel...
        assert repo_with_new_channel.get_objective_config("20x").get_channel_by_name("561nm - 2") is None
        # ...and neither does the file on disk
        repo_with_new_channel._save_yaml = orig_save_yaml
        repo_with_new_channel.clear_profile_cache()
        assert repo_with_new_channel.get_objective_config("20x").get_channel_by_name("561nm - 2") is None

    def test_save_with_sync_general_failure_raises(self, repo_with_new_channel):
        """A failure writing general.yaml is fatal and leaves the objective files alone."""
        orig_save_yaml = repo_with_new_channel._save_yaml

        def failing_save_yaml(path, model):
            if path.name == "general.yaml":
                raise PermissionError(f"denied: {path}")
            return orig_save_yaml(path, model)

        repo_with_new_channel._save_yaml = failing_save_yaml

        general = repo_with_new_channel.get_general_config().model_copy(deep=True)
        general.channels.append(self._make_channel("561nm - 2"))
        with pytest.raises(PermissionError):
            repo_with_new_channel.save_general_config_with_sync("default", general)

        repo_with_new_channel._save_yaml = orig_save_yaml
        repo_with_new_channel.clear_profile_cache()
        assert repo_with_new_channel.get_general_config().get_channel_by_name("561nm - 2") is None
        for objective in ("20x", "4x"):
            assert repo_with_new_channel.get_objective_config(objective).get_channel_by_name("561nm - 2") is None

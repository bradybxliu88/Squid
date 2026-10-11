"""Tests for the camera settings cache (squid.backend.drivers.cameras.settings_cache).

Ported from upstream tests/squid/test_settings_cache.py (98c7fbd6, 56db6965).
"""

from unittest.mock import Mock

import pytest
import yaml

from squid.backend.drivers.cameras.settings_cache import (
    DEFAULT_BINNING,
    CachedCameraSettings,
    load_camera_settings,
    restore_sensor_mode,
    save_camera_settings,
)
from squid.core.abc import CameraError
from squid.core.config import CameraPixelFormat


def _mock_camera(binning=(1, 1), pixel_format=None, sensor_mode=None):
    camera = Mock()
    camera.get_binning.return_value = binning
    camera.get_pixel_format.return_value = pixel_format
    camera.get_sensor_mode.return_value = sensor_mode
    return camera


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "camera_settings.yaml"


class TestCachedCameraSettings:
    def test_valid_settings(self):
        settings = CachedCameraSettings(binning=(2, 2), pixel_format="MONO8")
        assert settings.binning == (2, 2)
        assert settings.pixel_format == "MONO8"
        assert settings.sensor_mode is None

    def test_valid_settings_with_sensor_mode(self):
        settings = CachedCameraSettings(binning=(1, 1), pixel_format=None, sensor_mode="Ultra Quiet")
        assert settings.sensor_mode == "Ultra Quiet"

    @pytest.mark.parametrize("binning", [(1,), (1, 2, 3)])
    def test_invalid_binning_length(self, binning):
        with pytest.raises(ValueError, match="must be a 2-tuple"):
            CachedCameraSettings(binning=binning, pixel_format=None)

    @pytest.mark.parametrize("binning", [(0, 1), (1, -1)])
    def test_invalid_binning_values(self, binning):
        with pytest.raises(ValueError, match="must be positive"):
            CachedCameraSettings(binning=binning, pixel_format=None)

    def test_frozen_dataclass(self):
        settings = CachedCameraSettings(binning=(2, 2), pixel_format="MONO8")
        with pytest.raises(Exception):  # FrozenInstanceError
            settings.binning = (1, 1)


class TestSaveCameraSettings:
    def test_save_settings(self, cache_path):
        camera = _mock_camera(binning=(2, 2), pixel_format=CameraPixelFormat.MONO8, sensor_mode="Ultra Quiet")
        save_camera_settings(camera, cache_path)

        data = yaml.safe_load(cache_path.read_text())
        assert data["binning"] == [2, 2]
        assert data["pixel_format"] == "MONO8"
        assert data["sensor_mode"] == "Ultra Quiet"

    def test_save_settings_no_pixel_format(self, cache_path):
        """Pixel format and sensor mode are None when the camera does not support them."""
        save_camera_settings(_mock_camera(binning=(1, 1)), cache_path)

        data = yaml.safe_load(cache_path.read_text())
        assert data["binning"] == [1, 1]
        assert data["pixel_format"] is None
        assert data["sensor_mode"] is None

    def test_save_creates_parent_directories(self, tmp_path):
        cache_path = tmp_path / "nested" / "dir" / "camera_settings.yaml"
        save_camera_settings(_mock_camera(), cache_path)
        assert cache_path.exists()

    def test_save_handles_camera_error(self, cache_path):
        camera = _mock_camera()
        camera.get_binning.side_effect = RuntimeError("Camera disconnected")

        save_camera_settings(camera, cache_path)  # logs, does not raise

        assert not cache_path.exists()


class TestLoadCameraSettings:
    def test_load_settings(self, cache_path):
        cache_path.write_text(yaml.safe_dump({"binning": [2, 2], "pixel_format": "MONO8", "sensor_mode": "fast"}))

        settings = load_camera_settings(cache_path)

        assert settings is not None
        assert settings.binning == (2, 2)
        assert settings.pixel_format == "MONO8"
        assert settings.sensor_mode == "fast"

    def test_load_old_cache_without_sensor_mode_key(self, cache_path):
        """Cache files written before sensor mode existed load with sensor_mode=None."""
        cache_path.write_text(yaml.safe_dump({"binning": [2, 2], "pixel_format": "MONO8"}))

        settings = load_camera_settings(cache_path)

        assert settings is not None
        assert settings.binning == (2, 2)
        assert settings.sensor_mode is None

    def test_load_missing_file(self, tmp_path):
        assert load_camera_settings(tmp_path / "nonexistent.yaml") is None

    def test_load_empty_file_returns_none(self, cache_path):
        """Empty cache file: yaml.safe_load returns None; must fail safe, not raise."""
        cache_path.write_text("")

        assert load_camera_settings(cache_path) is None

    @pytest.mark.parametrize("content", ["just a string\n", "- 2\n- 2\n", "42\n"])
    def test_load_non_mapping_yaml_returns_none(self, cache_path, content):
        """Cache file holding a scalar/list instead of a mapping must fail safe."""
        cache_path.write_text(content)

        assert load_camera_settings(cache_path) is None

    def test_load_corrupted_yaml(self, cache_path):
        cache_path.write_text("not: valid: yaml: {{{\n  - broken")

        assert load_camera_settings(cache_path) is None

    def test_load_missing_binning_uses_default(self, cache_path):
        cache_path.write_text(yaml.safe_dump({"pixel_format": "MONO8"}))

        settings = load_camera_settings(cache_path)

        assert settings is not None
        assert settings.binning == DEFAULT_BINNING
        assert settings.pixel_format == "MONO8"

    @pytest.mark.parametrize("binning", ["invalid", [1, 2, 3]])
    def test_load_invalid_binning_uses_default(self, cache_path, binning):
        cache_path.write_text(yaml.safe_dump({"binning": binning, "pixel_format": None}))

        settings = load_camera_settings(cache_path)

        assert settings is not None
        assert settings.binning == DEFAULT_BINNING

    def test_load_binning_negative_returns_none(self, cache_path):
        cache_path.write_text(yaml.safe_dump({"binning": [-1, 2], "pixel_format": None}))

        assert load_camera_settings(cache_path) is None


class TestRoundTrip:
    def test_round_trip(self, cache_path):
        camera = _mock_camera(binning=(4, 4), pixel_format=CameraPixelFormat.MONO12, sensor_mode="standard")
        save_camera_settings(camera, cache_path)

        settings = load_camera_settings(cache_path)

        assert settings is not None
        assert settings.binning == (4, 4)
        assert settings.pixel_format == "MONO12"
        assert settings.sensor_mode == "standard"

    def test_round_trip_no_pixel_format(self, cache_path):
        save_camera_settings(_mock_camera(binning=(2, 2)), cache_path)

        settings = load_camera_settings(cache_path)

        assert settings is not None
        assert settings.binning == (2, 2)
        assert settings.pixel_format is None
        assert settings.sensor_mode is None

    def test_round_trip_with_simulated_camera(self, cache_path, simulated_camera):
        simulated_camera.set_sensor_mode("fast")
        save_camera_settings(simulated_camera, cache_path)

        settings = load_camera_settings(cache_path)

        assert settings is not None
        assert settings.sensor_mode == "fast"


class TestRestoreSensorMode:
    """Startup restore of a cached sensor mode (upstream's _restore_sensor_mode)."""

    @pytest.fixture
    def camera_service(self, simulated_camera):
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        return CameraService(simulated_camera, EventBus())

    def test_applies_mode_through_camera_service(self, camera_service, simulated_camera):
        from squid.core.events import SensorModeChanged

        received = []
        camera_service._event_bus.subscribe(SensorModeChanged, received.append)

        assert restore_sensor_mode(camera_service, "fast") is True
        camera_service._event_bus.drain()

        assert simulated_camera.get_sensor_mode() == "fast"
        assert [e.sensor_mode for e in received] == ["fast"]

    @pytest.mark.parametrize("sensor_mode", [None, ""])
    def test_nothing_cached_is_skipped(self, sensor_mode):
        camera = Mock()

        assert restore_sensor_mode(camera, sensor_mode) is False
        camera.set_sensor_mode.assert_not_called()

    def test_unknown_mode_is_rejected(self, camera_service, simulated_camera):
        """E.g. the cache was written by an ORCA ("Ultra Quiet") and the camera was swapped."""
        assert restore_sensor_mode(camera_service, "Ultra Quiet") is False
        assert simulated_camera.get_sensor_mode() == "standard"

    def test_camera_without_sensor_modes(self):
        camera = Mock()
        camera.get_available_sensor_modes.return_value = []

        assert restore_sensor_mode(camera, "fast") is False
        camera.set_sensor_mode.assert_not_called()

    def test_already_active_mode_sends_no_camera_command(self):
        """Re-applying the active mode would needlessly pause streaming on real hardware."""
        camera = Mock()
        camera.get_available_sensor_modes.return_value = ["standard", "fast"]
        camera.get_sensor_mode.return_value = "standard"

        assert restore_sensor_mode(camera, "standard") is True
        camera.set_sensor_mode.assert_not_called()

    @pytest.mark.parametrize("failing_call", ["get_sensor_mode", "set_sensor_mode"])
    def test_camera_error_fails_safe(self, failing_call):
        """A device error must fail the restore, not crash startup."""
        camera = Mock()
        camera.get_available_sensor_modes.return_value = ["standard", "fast"]
        camera.get_sensor_mode.return_value = "standard"
        getattr(camera, failing_call).side_effect = CameraError("device error")

        assert restore_sensor_mode(camera, "fast") is False

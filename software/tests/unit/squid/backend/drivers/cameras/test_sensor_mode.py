"""Sensor mode capability: AbstractCamera defaults, CameraConfig plumbing, SimulatedCamera.

Ports the tests from upstream 875eda71 (tests/squid/test_camera.py).
"""

import logging

import pytest

import _def
import squid.core.config
from squid.backend.drivers.cameras.camera_utils import get_camera
from squid.core.abc import AbstractCamera


@pytest.fixture
def sim_cam():
    camera = get_camera(squid.core.config.get_camera_config(), simulated=True)
    yield camera
    camera.close()


def test_abstract_camera_sensor_mode_defaults(sim_cam):
    # Call the base-class implementations explicitly: SimulatedCamera overrides
    # these, but the ABC defaults must mean "not supported".
    assert AbstractCamera.get_available_sensor_modes(sim_cam) == []
    assert AbstractCamera.get_sensor_mode(sim_cam) is None
    with pytest.raises(NotImplementedError):
        AbstractCamera.set_sensor_mode(sim_cam, "fast")


def test_camera_config_default_sensor_mode():
    config = squid.core.config.get_camera_config()
    assert config.default_sensor_mode is None

    updated = config.model_copy(update={"default_sensor_mode": "fast"})
    assert updated.default_sensor_mode == "fast"


@pytest.mark.parametrize(
    "ini_value, expected",
    [
        (None, None),
        ("", None),
        ("Ultra Quiet", "Ultra Quiet"),
        # The INI reader turns bare numbers into ints; mode names are strings.
        (2, "2"),
    ],
)
def test_sensor_mode_default_from_def_normalizes_ini_value(monkeypatch, ini_value, expected):
    monkeypatch.setattr(_def.CAMERA_CONFIG, "SENSOR_MODE_DEFAULT", ini_value)
    assert squid.core.config._sensor_mode_default_from_def() == expected


def test_simulated_camera_sensor_modes(sim_cam):
    assert sim_cam.get_available_sensor_modes() == ["standard", "fast"]
    assert sim_cam.get_sensor_mode() == "standard"

    sim_cam.set_sensor_mode("fast")
    assert sim_cam.get_sensor_mode() == "fast"

    with pytest.raises(ValueError):
        sim_cam.set_sensor_mode("warp_speed")
    assert sim_cam.get_sensor_mode() == "fast"


def test_simulated_camera_default_sensor_mode_from_config():
    config = squid.core.config.get_camera_config().model_copy(update={"default_sensor_mode": "fast"})
    camera = get_camera(config, simulated=True)
    try:
        assert camera.get_sensor_mode() == "fast"
    finally:
        camera.close()


def test_simulated_camera_ignores_hardware_default_sensor_mode(caplog):
    """A machine INI written for real hardware (e.g. sensor_mode_default = Standard for an ORCA)
    must still start in --simulation: the unknown mode is ignored with a warning."""
    config = squid.core.config.get_camera_config().model_copy(update={"default_sensor_mode": "Standard"})
    with caplog.at_level(logging.WARNING):
        camera = get_camera(config, simulated=True)
    try:
        assert camera.get_sensor_mode() == "standard"
        assert "Ignoring default sensor mode 'Standard'" in caplog.text
    finally:
        camera.close()

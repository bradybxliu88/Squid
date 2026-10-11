"""Tests for LiveController._check_laser_engine_warn_only (ports #536 part 3c)."""

from unittest.mock import MagicMock

from squid.backend.controllers.live_controller import LiveController
from squid.core.events import LaserEngineWarning


def _controller(service, *, wavelength=488, ready=False):
    c = LiveController.__new__(LiveController)
    c._laser_engine_service = service
    c._event_bus = MagicMock()
    c._log = MagicMock()
    # Wavelength resolution stubs.
    c.currentConfiguration = MagicMock()
    c.currentConfiguration.get_illumination_wavelength.return_value = wavelength
    ccm = MagicMock()
    ccm.get_illumination_config.return_value = MagicMock() if wavelength is not None else None
    c._channel_config_service = ccm
    if service is not None:
        service.channel_keys_for_wavelengths.return_value = ["470"] if wavelength else []
        status = MagicMock()
        status.is_ready_for.return_value = ready
        service.get_latest_status.return_value = status
    return c


def _warnings(controller):
    return [
        c.args[0] for c in controller._event_bus.publish.call_args_list if isinstance(c.args[0], LaserEngineWarning)
    ]


def test_noop_when_no_service():
    c = _controller(None)
    c._laser_engine_service = None
    c._check_laser_engine_warn_only()  # must not raise


def test_noop_when_wavelength_none():
    svc = MagicMock()
    c = _controller(svc, wavelength=None)
    c._check_laser_engine_warn_only()
    svc.wake_up.assert_not_called()
    assert _warnings(c) == []


def test_noop_when_already_ready():
    svc = MagicMock()
    c = _controller(svc, ready=True)
    c._check_laser_engine_warn_only()
    svc.wake_up.assert_not_called()
    assert _warnings(c) == []


def test_wakes_and_warns_when_not_ready():
    svc = MagicMock()
    c = _controller(svc, ready=False)
    c._check_laser_engine_warn_only()
    svc.wake_up.assert_called_once_with("470")
    warnings = _warnings(c)
    assert len(warnings) == 1
    assert "470" in warnings[0].message

"""Tests for the MultiPointWorker laser-engine acquisition gate (ports #536 part 3).

Constructs the worker via __new__ + minimal attributes so the gate methods can be
exercised without the full acquisition dependency graph.
"""

from unittest.mock import MagicMock

import pytest

from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker


def _worker(service, channels=("470",), abort=False):
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._laser_engine_service = service
    w._laser_channels_needed = list(channels)
    w._event_bus = MagicMock()
    w._abort_requested = MagicMock()
    w._abort_requested.is_set.return_value = abort
    w._log = MagicMock()
    return w


def _published(worker):
    return [type(c.args[0]).__name__ for c in worker._event_bus.publish.call_args_list]


def test_gate_short_circuits_when_already_ready():
    svc = MagicMock()
    status = MagicMock()
    status.is_ready_for.return_value = True
    svc.get_latest_status.return_value = status
    w = _worker(svc)

    w._wait_for_laser_engine()

    svc.wait_until_ready.assert_not_called()
    w._event_bus.publish.assert_not_called()  # no modal when already ready


def test_gate_waits_then_ready_publishes_waiting_and_ready():
    svc = MagicMock()
    svc.get_latest_status.return_value = None  # not ready yet
    svc.wait_until_ready.return_value = True
    w = _worker(svc)

    w._wait_for_laser_engine()

    svc.wait_until_ready.assert_called_once()
    assert _published(w) == ["LaserEngineWaitingForReady", "LaserEngineReady"]


def test_gate_timeout_raises_and_still_publishes_ready():
    svc = MagicMock()
    svc.get_latest_status.return_value = None
    svc.wait_until_ready.return_value = False
    svc.is_connection_lost.return_value = False
    w = _worker(svc, abort=False)

    with pytest.raises(RuntimeError, match="did not reach ready"):
        w._wait_for_laser_engine()
    # The Ready event is published in the finally even on failure (clears the modal).
    assert "LaserEngineReady" in _published(w)


def test_gate_disconnect_raises_connection_lost():
    svc = MagicMock()
    svc.get_latest_status.return_value = None
    svc.wait_until_ready.return_value = False
    svc.is_connection_lost.return_value = True
    w = _worker(svc)

    with pytest.raises(RuntimeError, match="connection lost"):
        w._wait_for_laser_engine()


def test_gate_abort_returns_silently():
    svc = MagicMock()
    svc.get_latest_status.return_value = None
    svc.wait_until_ready.return_value = False
    w = _worker(svc, abort=True)

    # Abort requested → caller handles it; the gate must not raise.
    w._wait_for_laser_engine()


def test_compute_channels_maps_wavelengths():
    svc = MagicMock()
    svc.channel_keys_for_wavelengths.return_value = ["470", "55x"]
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._laser_engine_service = svc
    ccm = MagicMock()
    ccm.get_illumination_config.return_value = MagicMock()  # present
    w.channelConfigurationManager = ccm
    cfg1, cfg2 = MagicMock(), MagicMock()
    cfg1.get_illumination_wavelength.return_value = 488
    cfg2.get_illumination_wavelength.return_value = 561
    w.selected_configurations = [cfg1, cfg2]

    assert w._compute_laser_channels_needed() == ["470", "55x"]
    svc.channel_keys_for_wavelengths.assert_called_once_with([488, 561])


def test_compute_channels_empty_without_service():
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._laser_engine_service = None
    assert w._compute_laser_channels_needed() == []

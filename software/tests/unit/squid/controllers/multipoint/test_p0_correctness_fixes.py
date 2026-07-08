"""Tests for the P0 correctness fixes from the Squid_widefield vs Squid_BL
comparison:

1. Channel-mode / illumination setup must NOT swallow image-destroying hardware
   errors (they should propagate so the FOV is marked failed), while tolerating
   non-destructive ones (analog gain = camera capability gap; strobe delay).
3. FOVs that fail DURING acquisition are folded into the run's SUCCESS/FAILED
   verdict once they exceed the configured fraction (and are always logged).
4. A stalled software-trigger live view auto-recovers: after a sustained run of
   "camera not ready" ticks the camera is re-armed (enable_callbacks), while a
   single skip does nothing and a successful trigger clears the stall.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import _def
from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker
from squid.backend.controllers.live_controller import LiveController


# ---------------------------------------------------------------------------
# Fix 1: channel-mode / illumination error handling
# ---------------------------------------------------------------------------


def _make_worker_for_channel_mode(
    *,
    illumination_service=None,
    filter_wheel_service=None,
    camera_service=None,
) -> MultiPointWorker:
    worker = MultiPointWorker.__new__(MultiPointWorker)
    worker._log = MagicMock()
    worker._camera_service = camera_service or MagicMock()
    worker._illumination_service = illumination_service
    worker._filter_wheel_service = filter_wheel_service
    worker._enable_channel_auto_filter_switching = False
    worker._trigger_mode = _def.TriggerMode.SOFTWARE
    return worker


def _channel(**overrides) -> SimpleNamespace:
    base = dict(
        name="638",
        exposure_time=50.0,
        analog_gain=0.0,
        illumination_source=11,
        illumination_intensity=40.0,
        emission_filter_position=1,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_apply_channel_mode_raises_on_illumination_power_failure() -> None:
    """A failed set_channel_power mis-exposes the frame and must propagate."""
    illum = MagicMock()
    illum.set_channel_power.side_effect = RuntimeError("power comms lost")
    worker = _make_worker_for_channel_mode(illumination_service=illum)

    with pytest.raises(RuntimeError, match="power comms lost"):
        worker._apply_channel_mode(_channel())


def test_apply_channel_mode_tolerates_analog_gain_failure() -> None:
    """Analog gain is a camera-capability gap: warn and continue, do not raise."""
    camera = MagicMock()
    camera.set_analog_gain.side_effect = RuntimeError("gain not supported")
    worker = _make_worker_for_channel_mode(
        illumination_service=MagicMock(), camera_service=camera
    )

    # Should NOT raise; illumination power is still applied afterwards.
    worker._apply_channel_mode(_channel(analog_gain=5.0))
    worker._illumination_service.set_channel_power.assert_called_once()
    worker._log.warning.assert_called()


def test_apply_channel_mode_raises_on_filter_position_failure() -> None:
    """A wrong emission filter means the wrong channel is saved: must propagate."""
    fw = MagicMock()
    fw.is_available.return_value = True
    fw.set_filter_wheel_position.side_effect = RuntimeError("filter jam")
    worker = _make_worker_for_channel_mode(
        illumination_service=MagicMock(), filter_wheel_service=fw
    )
    worker._enable_channel_auto_filter_switching = True

    with pytest.raises(RuntimeError, match="filter jam"):
        worker._apply_channel_mode(_channel())


def test_turn_on_illumination_raises_on_failure() -> None:
    """No illumination => black frame; the failure must propagate, not be swallowed."""
    illum = MagicMock()
    illum.turn_on_channel.side_effect = RuntimeError("laser offline")
    worker = _make_worker_for_channel_mode(illumination_service=illum)

    with pytest.raises(RuntimeError, match="laser offline"):
        worker._turn_on_illumination(_channel())


def test_turn_off_illumination_raises_on_failure() -> None:
    """A light stuck on contaminates the next channel; surface it rather than swallow."""
    illum = MagicMock()
    illum.turn_off_channel.side_effect = RuntimeError("relay stuck")
    worker = _make_worker_for_channel_mode(illumination_service=illum)

    with pytest.raises(RuntimeError, match="relay stuck"):
        worker._turn_off_illumination(_channel())


def test_illumination_no_op_when_service_absent() -> None:
    """With no illumination service the setup is a safe no-op (simulation/no hardware)."""
    worker = _make_worker_for_channel_mode(illumination_service=None)
    # None of these should raise.
    worker._turn_on_illumination(_channel())
    worker._turn_off_illumination(_channel())
    worker._apply_channel_mode(_channel())


# ---------------------------------------------------------------------------
# Fix 3: failed-FOV count folded into the run verdict
# ---------------------------------------------------------------------------


def _worker_with_fov_tallies(failed: int, completed: int) -> MultiPointWorker:
    worker = MultiPointWorker.__new__(MultiPointWorker)
    worker._log = MagicMock()
    worker._failed_fov_total = failed
    worker._completed_fov_total = completed
    return worker


def test_no_failed_fovs_gives_no_verdict_error() -> None:
    worker = _worker_with_fov_tallies(failed=0, completed=144)
    assert worker._evaluate_failed_fov_verdict() is None
    worker._log.error.assert_not_called()


def test_isolated_failed_fovs_logged_but_not_fatal(monkeypatch) -> None:
    """A handful of failures on a large grid is logged but stays within tolerance."""
    monkeypatch.setattr(_def, "ACQUISITION_MAX_FAILED_FOV_FRACTION", 0.1, raising=False)
    worker = _worker_with_fov_tallies(failed=5, completed=139)  # 5 <= int(0.1*144)=14
    assert worker._evaluate_failed_fov_verdict() is None
    worker._log.error.assert_called_once()  # never silent


def test_systematic_failed_fovs_fail_the_run(monkeypatch) -> None:
    """Failures beyond the tolerated fraction flip the verdict to FAILED."""
    monkeypatch.setattr(_def, "ACQUISITION_MAX_FAILED_FOV_FRACTION", 0.1, raising=False)
    worker = _worker_with_fov_tallies(failed=30, completed=114)  # 30 > int(0.1*144)=14
    err = worker._evaluate_failed_fov_verdict()
    assert isinstance(err, Exception)
    assert "30 of 144" in str(err)


def test_zero_fraction_fails_run_on_any_failure(monkeypatch) -> None:
    """Fraction 0.0 makes any failed FOV fail the run (strictest policy)."""
    monkeypatch.setattr(_def, "ACQUISITION_MAX_FAILED_FOV_FRACTION", 0.0, raising=False)
    worker = _worker_with_fov_tallies(failed=1, completed=143)
    assert worker._evaluate_failed_fov_verdict() is not None


# ---------------------------------------------------------------------------
# Fix 4: frozen-live-view stall auto-recovery
# ---------------------------------------------------------------------------


def _make_live_controller(*, frame_time_ms: float = 100.0) -> LiveController:
    lc = LiveController.__new__(LiveController)
    lc._log = MagicMock()
    lc.is_live = True
    lc._live_stall_since = None
    lc._live_stall_recovery_min_s = 2.0
    lc._live_stall_recovery_max_s = 10.0
    cam = MagicMock()
    cam.get_total_frame_time.return_value = frame_time_ms
    cam.get_exposure_time.return_value = frame_time_ms
    lc._camera_service = cam
    return lc


def test_single_not_ready_tick_does_not_rearm() -> None:
    """One skipped trigger is normal over-triggering: no recovery on the first tick."""
    lc = _make_live_controller()
    lc._handle_trigger_not_ready()
    assert lc._live_stall_since is not None
    lc._camera_service.enable_callbacks.assert_not_called()


def test_sustained_stall_rearms_camera() -> None:
    """A sustained 'not ready' run past the threshold re-arms the camera."""
    lc = _make_live_controller(frame_time_ms=100.0)  # threshold = max(2.0, 0.3) = 2.0s
    lc._handle_trigger_not_ready()  # first tick: start the stall clock
    # Simulate that the stall started well beyond the recovery threshold ago.
    lc._live_stall_since -= 5.0
    lc._handle_trigger_not_ready()

    lc._camera_service.enable_callbacks.assert_called_once_with(True)
    lc._log.warning.assert_called()


def test_stall_recovery_no_op_when_not_live() -> None:
    """No recovery attempts when the live view is not running."""
    lc = _make_live_controller()
    lc.is_live = False
    lc._handle_trigger_not_ready()
    assert lc._live_stall_since is None
    lc._camera_service.enable_callbacks.assert_not_called()


def test_recovery_threshold_scales_with_frame_time_and_clamps() -> None:
    """Threshold scales with frame time but is clamped to [min, max]."""
    lc = _make_live_controller(frame_time_ms=100.0)
    # 3 * 0.1s = 0.3s -> clamped up to the 2.0s floor.
    assert lc._live_stall_recovery_threshold() == pytest.approx(2.0)
    lc._camera_service.get_total_frame_time.return_value = 10000.0  # 10s frame
    # 3 * 10s = 30s -> clamped down to the 10.0s ceiling.
    assert lc._live_stall_recovery_threshold() == pytest.approx(10.0)


def test_recovery_probes_optional_reset_trigger_readiness() -> None:
    """If the camera service exposes reset_trigger_readiness, recovery calls it."""
    lc = _make_live_controller()
    lc._camera_service.reset_trigger_readiness = MagicMock()
    lc._recover_stalled_live()
    lc._camera_service.enable_callbacks.assert_called_once_with(True)
    lc._camera_service.reset_trigger_readiness.assert_called_once_with()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

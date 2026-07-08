"""Integration tests for MultiPointWorker._apply_focus_guard wiring.

Builds a partial worker via __new__ (matching test_multi_point_worker_focus_lock)
and drives the guard with fake stage/piezo services + a fake laser AF.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker
from squid.backend.controllers.multipoint.focus_guard import FocusGuard
from squid.core.events import AutofocusMode


class _FakePiezo:
    def __init__(self, pos_um: float = 140.0):
        self._pos = float(pos_um)

    def get_position(self) -> float:
        return self._pos

    def move_to(self, pos_um: float) -> None:
        self._pos = float(pos_um)

    def move_relative(self, delta_um: float) -> None:
        self._pos += float(delta_um)

    def get_range(self):
        return (0.0, 300.0)


def _make_worker(*, piezo, af_side_effect, max_retries=1, tol=10.0):
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._log = MagicMock()
    w.autofocus_mode = AutofocusMode.LASER_REFLECTION
    w._focus_guard = FocusGuard(tolerance_um=tol, min_spatial_points=3, k_neighbors=6)
    w._af_guard_max_retries = max_retries
    w._focus_guard_records = []
    w.use_piezo = True
    w._piezo_service = piezo
    w._stage_service = SimpleNamespace(
        get_position=lambda: SimpleNamespace(z_mm=0.0),
        move_z=lambda d: None,
        wait_for_idle=lambda: None,
    )
    w._event_bus = None
    w._current_round_index = 0
    w.time_point = 0
    # A row of accepted good FOVs at z=140 so spatial prediction is ~140.
    w.scan_region_fov_coords_mm = {"A": [(i * 0.2, 0.0, 0.0) for i in range(12)]}
    for i in range(8):
        w._focus_guard.observe("A", i, i * 0.2, 0.0, 140.0)
    # perform_autofocus is where a retry would re-run laser AF.
    w.perform_autofocus = MagicMock(side_effect=af_side_effect)
    return w


def test_guard_accepts_inlier_and_observes():
    piezo = _FakePiezo(pos_um=142.0)  # 2 um from predicted 140 -> inlier
    w = _make_worker(piezo=piezo, af_side_effect=lambda *a, **k: True)
    w._apply_focus_guard("A", 8, fov_id=None)
    # Not moved, accepted, recorded as non-outlier, and fed back to the predictor.
    assert piezo.get_position() == 142.0
    assert w.perform_autofocus.call_count == 0
    rec = w._focus_guard_records[-1]
    assert rec["outlier"] is False
    assert w._focus_guard.predict("A", 8, 1.6, 0.0)[0] is not None


def test_guard_corrects_persistent_outlier_to_predicted_plane():
    # AF aliased 25 um high and keeps re-aliasing on retry (side effect resets piezo).
    piezo = _FakePiezo(pos_um=165.0)

    def realias(*_a, **_k):
        piezo.move_to(165.0)  # retry re-runs AF which locks wrong again
        return True

    w = _make_worker(piezo=piezo, af_side_effect=realias, max_retries=1)
    w._apply_focus_guard("A", 8, fov_id=None)
    # Retry attempted once, then fell back to predicted (~140), NOT left at 165.
    assert w.perform_autofocus.call_count == 1
    assert abs(piezo.get_position() - 140.0) < 3.0
    rec = w._focus_guard_records[-1]
    assert rec["outlier"] is True
    # Rejected value must NOT poison the predictor.
    assert abs(w._focus_guard.predict("A", 9, 1.8, 0.0)[0] - 140.0) < 3.0


def test_guard_retry_recovers_and_accepts():
    # First read is an outlier; the retry AF fixes focus to ~140 (spot re-found).
    piezo = _FakePiezo(pos_um=168.0)

    def fix(*_a, **_k):
        piezo.move_to(140.5)
        return True

    w = _make_worker(piezo=piezo, af_side_effect=fix, max_retries=1)
    w._apply_focus_guard("A", 8, fov_id=None)
    assert w.perform_autofocus.call_count == 1
    assert abs(piezo.get_position() - 140.5) < 1.0
    rec = w._focus_guard_records[-1]
    assert rec["outlier"] is False  # recovered within tolerance


def test_guard_noop_for_non_laser_reflection_mode():
    piezo = _FakePiezo(pos_um=999.0)
    w = _make_worker(piezo=piezo, af_side_effect=lambda *a, **k: True)
    w.autofocus_mode = AutofocusMode.CONTRAST
    w._apply_focus_guard("A", 8, fov_id=None)
    assert piezo.get_position() == 999.0  # untouched
    assert w._focus_guard_records == []

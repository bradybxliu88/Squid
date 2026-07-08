"""Unit tests for FocusGuard — the laser-AF outlier guard."""

from __future__ import annotations

import pytest

from squid.backend.controllers.multipoint.focus_guard import FocusGuard, GuardDecision


def _grid_points(n=12, y=0.0, z=140.0, dx=0.2):
    """A row of accepted FOVs along x at constant z."""
    return [(i * dx, y, z) for i in range(n)]


# ---------------------------------------------------------------------------
# No basis yet -> accept
# ---------------------------------------------------------------------------


def test_no_history_accepts_and_does_not_predict():
    g = FocusGuard(tolerance_um=10.0)
    d = g.evaluate("r", 0, 0.0, 0.0, 999.0)
    assert isinstance(d, GuardDecision)
    assert d.predicted_um is None
    assert d.residual_um is None
    assert d.rejected is False
    assert d.source == "none"


def test_fewer_than_min_spatial_points_accepts():
    g = FocusGuard(tolerance_um=5.0, min_spatial_points=3)
    g.observe("r", 0, 0.0, 0.0, 140.0)
    g.observe("r", 1, 0.2, 0.0, 140.0)
    # Only 2 accepted points (< min 3) and a different FOV -> no prediction yet.
    d = g.evaluate("r", 2, 0.4, 0.0, 200.0)
    assert d.predicted_um is None
    assert d.rejected is False


# ---------------------------------------------------------------------------
# Spatial prediction (first round, no temporal history)
# ---------------------------------------------------------------------------


def test_spatial_prediction_flags_outlier():
    g = FocusGuard(tolerance_um=10.0, min_spatial_points=3, k_neighbors=6)
    for i, (x, y, z) in enumerate(_grid_points(n=8, z=140.0)):
        g.observe("r", i, x, y, z)
    # A new FOV adjacent to the row, measured 25 um high -> outlier.
    d = g.evaluate("r", 8, 1.6, 0.0, 165.0)
    assert d.source == "spatial"
    assert d.predicted_um == pytest.approx(140.0, abs=1.0)
    assert d.rejected is True
    assert d.residual_um == pytest.approx(25.0, abs=1.0)


def test_spatial_prediction_accepts_inlier():
    g = FocusGuard(tolerance_um=10.0, min_spatial_points=3)
    for i, (x, y, z) in enumerate(_grid_points(n=8, z=140.0)):
        g.observe("r", i, x, y, z)
    d = g.evaluate("r", 8, 1.6, 0.0, 142.0)  # 2 um off -> within tolerance
    assert d.rejected is False
    assert d.residual_um == pytest.approx(2.0, abs=1.0)


def test_spatial_prediction_follows_tilt():
    """kNN inverse-distance weighting should track a tilted surface locally."""
    g = FocusGuard(tolerance_um=5.0, min_spatial_points=3, k_neighbors=4)
    # z rises 10 um per 0.2 mm step (steep tilt).
    for i in range(10):
        g.observe("r", i, i * 0.2, 0.0, 100.0 + 10.0 * i)
    # Query near x=1.0 (between points at 90..180). Local neighbors ~ 150.
    d = g.evaluate("r", 10, 1.0, 0.0, 150.0)
    assert d.predicted_um == pytest.approx(150.0, abs=6.0)
    assert d.rejected is False


# ---------------------------------------------------------------------------
# Temporal prediction (same FOV across rounds) takes precedence
# ---------------------------------------------------------------------------


def test_temporal_prediction_takes_precedence():
    g = FocusGuard(tolerance_um=10.0, temporal_tolerance_um=4.0)
    # Establish a spatial field far from the true same-FOV value.
    for i in range(6):
        g.observe("r", i, i * 0.2, 0.0, 100.0)
    # FOV 3 was actually accepted at 130 last round (temporal memory).
    g.observe("r", 3, 0.6, 0.0, 130.0)
    d = g.evaluate("r", 3, 0.6, 0.0, 131.0)
    assert d.source == "temporal"
    assert d.predicted_um == pytest.approx(130.0)
    assert d.tolerance_um == pytest.approx(4.0)
    assert d.rejected is False
    # A 25 um jump from the same FOV's history is rejected even though spatial
    # neighbors are elsewhere.
    d2 = g.evaluate("r", 3, 0.6, 0.0, 155.0)
    assert d2.source == "temporal"
    assert d2.rejected is True


# ---------------------------------------------------------------------------
# Rejected points must not poison the predictor
# ---------------------------------------------------------------------------


def test_rejected_focus_not_observed_keeps_predictor_clean():
    g = FocusGuard(tolerance_um=10.0, min_spatial_points=3, k_neighbors=6)
    for i, (x, y, z) in enumerate(_grid_points(n=8, z=140.0)):
        g.observe("r", i, x, y, z)
    # Simulate the caller's contract: evaluate, and only observe when accepted.
    bad = g.evaluate("r", 8, 1.6, 0.0, 170.0)
    assert bad.rejected is True
    # Caller does NOT observe the rejected value. Predictor stays at ~140.
    again = g.evaluate("r", 9, 1.8, 0.0, 170.0)
    assert again.predicted_um == pytest.approx(140.0, abs=2.0)
    assert again.rejected is True


def test_region_isolation():
    g = FocusGuard(tolerance_um=10.0, min_spatial_points=3)
    for i, (x, y, z) in enumerate(_grid_points(n=6, z=140.0)):
        g.observe("rA", i, x, y, z)
    # A different region has no history -> no prediction, accept.
    d = g.evaluate("rB", 0, 0.0, 0.0, 300.0)
    assert d.predicted_um is None
    assert d.rejected is False


def test_nonfinite_measurement_is_not_rejected():
    g = FocusGuard(tolerance_um=10.0, min_spatial_points=3)
    for i, (x, y, z) in enumerate(_grid_points(n=6, z=140.0)):
        g.observe("r", i, x, y, z)
    d = g.evaluate("r", 6, 1.2, 0.0, float("nan"))
    assert d.rejected is False


def test_invalid_tolerance_raises():
    with pytest.raises(ValueError):
        FocusGuard(tolerance_um=0.0)

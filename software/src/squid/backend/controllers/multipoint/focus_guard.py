"""Spatial/temporal focus outlier guard for laser-reflection autofocus.

Reflection AF (``LaserAutofocusController.move_to_target(0)``) has no spatial
prior: if it locks onto the wrong reflection it silently returns a focal plane
many microns off. In a real 144-FOV x 8-round run this produced ~100 catastrophic
>15 um focus jumps confined to a contiguous band of FOVs (the reflection aliased
to a different surface there), while the rest of the field was fine (median
round-to-round change 1.3 um). The failures were bimodal: a tight <5 um cluster
(normal AF) and a separate 20-35 um cluster (aliased lock), with a clear gap in
between — so a single tolerance placed in that gap cleanly separates them.

This guard predicts the expected focal Z for a FOV from previously ACCEPTED
focus results and flags a measured focus that deviates beyond a tolerance:

  * temporal prior  - the SAME (region, fov)'s last accepted Z (same physical
    point; only thermal drift ~1 um/round separates rounds). Tightest.
  * spatial  prior  - inverse-distance-weighted mean of the k nearest accepted
    FOVs in the region (captures sample tilt/curvature). Used on the first round
    before any temporal history exists.

Rejected results are never stored, so a bad lock cannot poison the predictor.
The caller decides what to do with a rejection (retry the AF seeded to the
prediction, or fall back to the predicted plane).

Pure logic, no hardware imports — unit-testable with plain numbers, mirroring
:class:`PerFOVFocusLockStrategy`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


@dataclass(frozen=True)
class GuardDecision:
    """Outcome of evaluating one measured focus against the prediction."""

    measured_um: float
    predicted_um: Optional[float]  # None when there is no basis to predict yet
    residual_um: Optional[float]  # measured - predicted (None if no prediction)
    rejected: bool  # True => |residual| exceeded tolerance (an outlier)
    source: str  # "temporal" | "spatial" | "none"
    tolerance_um: float
    n_reference: int  # accepted points that informed the prediction


class FocusGuard:
    """Predicts per-FOV focus from accepted history and flags outliers.

    Args:
        tolerance_um: Max |measured - predicted| before a focus is rejected.
            Set it inside the run's bimodal gap between normal AF scatter and
            aliased-lock jumps (empirically ~5-18 um; default 10).
        temporal_tolerance_um: Tolerance for the temporal (same-FOV) prior;
            defaults to ``tolerance_um``. Same-point cross-round drift is small,
            so this may be tightened independently.
        min_spatial_points: Minimum accepted points in a region before a spatial
            prediction is attempted (fewer => accept, no basis to judge).
        k_neighbors: Number of nearest accepted FOVs averaged for the spatial
            prediction. >1 dilutes a single bad early point.
        max_points_per_region: Cap on retained points per region (ring buffer).
    """

    def __init__(
        self,
        *,
        tolerance_um: float = 10.0,
        temporal_tolerance_um: Optional[float] = None,
        min_spatial_points: int = 3,
        k_neighbors: int = 6,
        max_points_per_region: int = 4000,
    ) -> None:
        if tolerance_um <= 0:
            raise ValueError(f"tolerance_um must be > 0, got {tolerance_um}")
        self._tol = float(tolerance_um)
        self._temporal_tol = (
            float(temporal_tolerance_um) if temporal_tolerance_um is not None else float(tolerance_um)
        )
        self._min_spatial = max(1, int(min_spatial_points))
        self._k = max(1, int(k_neighbors))
        self._max_points = max(self._k, int(max_points_per_region))
        # region_id -> list of (x_mm, y_mm, z_um) accepted focus points
        self._points: Dict[str, List[Tuple[float, float, float]]] = {}
        # (region_id, fov) -> last accepted z_um (temporal memory)
        self._fov_z: Dict[Tuple[str, int], float] = {}

    def observe(self, region_id: str, fov: int, x_mm: float, y_mm: float, z_um: float) -> None:
        """Record an ACCEPTED focus point. Never call this for a rejected/fallback Z."""
        z = float(z_um)
        if not math.isfinite(z):
            return
        pts = self._points.setdefault(region_id, [])
        pts.append((float(x_mm), float(y_mm), z))
        if len(pts) > self._max_points:  # keep the most recent points
            del pts[: len(pts) - self._max_points]
        self._fov_z[(region_id, int(fov))] = z

    def _predict_spatial(self, region_id: str, x_mm: float, y_mm: float) -> Tuple[Optional[float], int]:
        pts = self._points.get(region_id) or []
        if len(pts) < self._min_spatial:
            return None, 0
        # k nearest by planar distance, inverse-distance-squared weighted.
        by_dist = sorted(
            ((math.hypot(px - x_mm, py - y_mm), pz) for (px, py, pz) in pts),
            key=lambda t: t[0],
        )
        k = min(self._k, len(by_dist))
        num = den = 0.0
        for dist, pz in by_dist[:k]:
            w = 1.0 / (dist * dist + 1e-9)
            num += w * pz
            den += w
        if den <= 0:
            return None, 0
        return num / den, k

    def predict(
        self, region_id: str, fov: int, x_mm: float, y_mm: float
    ) -> Tuple[Optional[float], str, float, int]:
        """Return (predicted_z_um, source, tolerance_um, n_reference).

        Prefers the temporal (same-FOV) prior when available, else spatial.
        """
        tz = self._fov_z.get((region_id, int(fov)))
        if tz is not None:
            return tz, "temporal", self._temporal_tol, 1
        sp, k = self._predict_spatial(region_id, x_mm, y_mm)
        if sp is not None:
            return sp, "spatial", self._tol, k
        return None, "none", self._tol, 0

    def evaluate(
        self, region_id: str, fov: int, x_mm: float, y_mm: float, measured_um: float
    ) -> GuardDecision:
        """Evaluate a measured focus Z against the prediction for this FOV."""
        m = float(measured_um)
        pred, source, tol, n = self.predict(region_id, fov, x_mm, y_mm)
        if pred is None or not math.isfinite(m):
            return GuardDecision(m, None, None, False, "none", tol, 0)
        residual = m - pred
        rejected = abs(residual) > tol
        return GuardDecision(m, pred, residual, rejected, source, tol, n)

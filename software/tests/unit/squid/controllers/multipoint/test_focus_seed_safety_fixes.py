"""Regression tests for the FOV-save / piezo focus-safety fixes.

These lock in the behavior that fixes the reported symptom "after saving a FOV
the piezo moves off the sample":

- H2: ad-hoc single-FOV ("current") runs must never seed the piezo from a prior
  run's cached/default Z, and must never record into memory (a stale seed from a
  different physical location drives the piezo off the next sample).
- H3: degraded seed/no_seed fallbacks are flagged ``degraded=True`` (so the caller
  won't cache the unverified Z); verified locked/single_shot are ``degraded=False``.
- H4: single-shot correction rejects implausibly large displacements, targets that
  would pin the piezo at a travel edge, and readings below the SNR floor.
- M1 / set_home: ZStackExecutor tracks the actual (clamped) hardware position and
  re-homes to the locked plane.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory
from squid.backend.controllers.multipoint.per_fov_focus_lock import (
    PerFOVFocusLockStrategy,
)
from squid.backend.controllers.multipoint.position_zstack import (
    ZStackConfig,
    ZStackExecutor,
)


class SimpleResult:
    def __init__(self, displacement_um: float, spot_snr: float):
        self.displacement_um = displacement_um
        self.spot_snr = spot_snr


class FakeLaserAF:
    def __init__(self, results: Optional[List[object]] = None):
        self._results = list(results) if results else []
        self.measure_calls = 0

    def measure_displacement_continuous(self):
        self.measure_calls += 1
        if self._results:
            return self._results.pop(0)
        return SimpleResult(float("nan"), 0.0)


class FakePiezo:
    """Fake piezo. Optionally clamps to ``rng`` and exposes get_range like PiezoService."""

    def __init__(self, initial_um: float = 150.0, rng: Optional[Tuple[float, float]] = None):
        self._pos = float(initial_um)
        self.moves: List[float] = []
        self._range = rng

    def get_position(self) -> float:
        return self._pos

    def move_to(self, position_um: float) -> float:
        self.moves.append(float(position_um))
        if self._range is not None:
            lo, hi = self._range
            position_um = max(lo, min(hi, position_um))
        self._pos = float(position_um)
        return self._pos

    def get_range(self) -> Tuple[float, float]:
        if self._range is None:
            raise AttributeError("no range")
        return self._range


class NeverLocks:
    def __init__(self):
        self._is_running = True
        self._laser_af = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self):
        self._is_running = True

    def stop(self):
        self._is_running = False

    def acquire_lock_reference(self, timeout_s: float = 5.0, confirmation_readings=None):
        return False


class AlwaysLocks(NeverLocks):
    def acquire_lock_reference(self, timeout_s: float = 5.0, confirmation_readings=None):
        return True


def _strat(controller, piezo, mem, **kw) -> PerFOVFocusLockStrategy:
    return PerFOVFocusLockStrategy(
        focus_lock_controller=controller,
        piezo_service=piezo,
        memory=mem,
        settle_s=0.0,
        sleep_fn=lambda _s: None,
        **kw,
    )


# ---------------------------------------------------------------------------
# H2 — ephemeral (ad-hoc "Run Current FOV") runs never seed/record from memory.
# Ephemerality is an explicit flag, NOT the region name — so a real region that
# happens to be named "current" (live-scan uses that name) still seeds normally.
# ---------------------------------------------------------------------------
class TestEphemeralRun:
    def test_ephemeral_ignores_stale_cached_and_default_seed(self):
        mem = FOVFocusMemory(default_z_um=250.0)
        mem.remember("current", 0, 250.0)  # stale cache from a prior run
        piezo = FakePiezo(initial_um=150.0)  # user is currently focused here
        strat = _strat(NeverLocks(), piezo, mem, ephemeral=True)

        result = strat.prepare("current", 0)

        # The piezo must NOT jump to the stale 250 — it stays at the live 150.
        assert piezo.get_position() == 150.0
        assert result.seed_used_um is None
        assert result.method in ("seed", "no_seed")

    def test_ephemeral_success_is_not_recorded(self):
        mem = FOVFocusMemory()
        strat = _strat(AlwaysLocks(), FakePiezo(150.0), mem, ephemeral=True)

        result = strat.prepare("current", 0)
        assert result.method == "locked"
        strat.record_success("current", 0, 150.0)

        assert ("current", 0) not in mem
        assert len(mem) == 0

    def test_non_ephemeral_region_still_seeds_from_memory(self):
        mem = FOVFocusMemory(default_z_um=250.0)
        piezo = FakePiezo(initial_um=150.0)
        strat = _strat(NeverLocks(), piezo, mem)  # ephemeral defaults to False

        result = strat.prepare("A1", 0)

        assert piezo.get_position() == 250.0  # real regions still seed
        assert result.seed_used_um == 250.0

    def test_region_named_current_still_seeds_when_not_ephemeral(self):
        # Regression guard for the sentinel-collision fix: live-scan creates a
        # real region literally named "current"; it must still seed/record.
        mem = FOVFocusMemory(default_z_um=250.0)
        piezo = FakePiezo(initial_um=150.0)
        strat = _strat(NeverLocks(), piezo, mem)  # not ephemeral

        result = strat.prepare("current", 0)

        assert piezo.get_position() == 250.0  # seeded despite the name "current"
        assert result.seed_used_um == 250.0
        # ...and record_success caches it (not skipped).
        strat.record_success("current", 5, 175.0)
        assert mem.get("current", 5) == 175.0


# ---------------------------------------------------------------------------
# H3 — degraded flag distinguishes verified focus from seed fallback
# ---------------------------------------------------------------------------
class TestDegradedFlag:
    def test_seed_fallback_is_degraded(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        strat = _strat(
            NeverLocks(),
            FakePiezo(100.0),
            mem,
            laser_af_controller=FakeLaserAF([SimpleResult(float("nan"), 0.0)]),
        )
        result = strat.prepare("A1", 0)
        assert result.method == "seed"
        assert result.degraded is True

    def test_locked_is_not_degraded(self):
        strat = _strat(AlwaysLocks(), FakePiezo(150.0), FOVFocusMemory())
        result = strat.prepare("A1", 0)
        assert result.method == "locked"
        assert result.degraded is False

    def test_single_shot_is_not_degraded(self):
        strat = _strat(
            NeverLocks(),
            FakePiezo(150.0),
            FOVFocusMemory(default_z_um=150.0),
            laser_af_controller=FakeLaserAF([SimpleResult(2.0, 5.0)]),
        )
        result = strat.prepare("A1", 0)
        assert result.method == "single_shot"
        assert result.degraded is False


# ---------------------------------------------------------------------------
# H4 — single-shot correction is bounded
# ---------------------------------------------------------------------------
class TestSingleShotBounds:
    def test_rejects_implausibly_large_displacement(self):
        piezo = FakePiezo(150.0)
        strat = _strat(
            NeverLocks(),
            piezo,
            FOVFocusMemory(default_z_um=150.0),
            laser_af_controller=FakeLaserAF([SimpleResult(50.0, 8.0)]),  # 50um >> max 10um
        )
        result = strat.prepare("A1", 0)
        assert result.method in ("seed", "no_seed")
        assert piezo.get_position() == 150.0  # not moved by the bogus 50um

    def test_rejects_target_at_piezo_edge(self):
        # seed 5um near the low limit; a +8um displacement would target -3um.
        piezo = FakePiezo(5.0, rng=(0.0, 300.0))
        strat = _strat(
            NeverLocks(),
            piezo,
            FOVFocusMemory(default_z_um=5.0),
            laser_af_controller=FakeLaserAF([SimpleResult(8.0, 8.0)]),
        )
        result = strat.prepare("A1", 0)
        assert result.method in ("seed", "no_seed")

    def test_honors_raised_snr_floor(self):
        strat = _strat(
            NeverLocks(),
            FakePiezo(150.0),
            FOVFocusMemory(default_z_um=150.0),
            laser_af_controller=FakeLaserAF([SimpleResult(1.0, 1.5)]),  # 1.5 < floor 2.0
        )
        result = strat.prepare("A1", 0)
        assert result.method in ("seed", "no_seed")

    def test_valid_correction_still_applies(self):
        piezo = FakePiezo(150.0, rng=(0.0, 300.0))
        strat = _strat(
            NeverLocks(),
            piezo,
            FOVFocusMemory(default_z_um=150.0),
            laser_af_controller=FakeLaserAF([SimpleResult(2.5, 5.0)]),
        )
        result = strat.prepare("A1", 0)
        assert result.method == "single_shot"
        assert piezo.get_position() == 147.5


# ---------------------------------------------------------------------------
# M1 / set_home — ZStackExecutor tracks clamped position and re-homes
# ---------------------------------------------------------------------------
def _executor(piezo, num_z_levels=5, delta_z_um=5.0):
    cfg = ZStackConfig(
        num_z_levels=num_z_levels,
        delta_z_um=delta_z_um,
        z_range=(0.0, 0.0),
        use_piezo=True,
    )
    return ZStackExecutor(piezo_service=piezo, config=cfg, piezo_delay_ms=0.0)


class TestZStackClampAndHome:
    def test_step_tracks_clamped_position(self):
        piezo = FakePiezo(295.0, rng=(0.0, 300.0))
        ex = _executor(piezo, delta_z_um=5.0)
        ex.initialize()  # home = 295
        ex.step()  # target 300 -> clamped 300
        assert ex.z_piezo_um == 300.0
        ex.step()  # target 305 -> clamped 300 (stuck at limit, not fictitious 305)
        assert ex.z_piezo_um == 300.0

    def test_set_home_reroots_return_to_start(self):
        piezo = FakePiezo(150.0)
        ex = _executor(piezo, delta_z_um=1.0)
        ex.initialize()  # home = 150 (pre-lock)
        ex.set_home(160.0)  # per-FOV lock moved the piezo to 160
        assert ex.z_piezo_um == 160.0
        ex.step()  # 161
        ex.step()  # 162
        ex.return_to_start()
        assert piezo.get_position() == 160.0  # returns to the NEW (locked) home

"""Tests for the per-FOV focus-lock hardening (Feature 1):

  * Tier 2.5 bounded piezo SWEEP re-acquire — when the strict lock and
    single-shot both fail because the chamber shifted in Z beyond the AF window
    at the seed, sweep the piezo to re-find the spot and re-lock at the
    corrected Z (which is then cached so the next round seeds from it).
  * Seed BOUNDS CLAMP — a stale/out-of-range cached seed must not drive the
    piezo to a hard travel limit.

These directly exercise the lab's failure mode: "the chamber gets moved around
during fluidics rounds so the auto focus fails and never grabs onto anything
after that." With the sweep, a shifted FOV is re-grabbed and its seed advances,
instead of degrading forever at the round-1 seed.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory
from squid.backend.controllers.multipoint.per_fov_focus_lock import (
    PerFOVFocusLockStrategy,
)


class SimpleResult:
    def __init__(self, displacement_um: float, spot_snr: float):
        self.displacement_um = displacement_um
        self.spot_snr = spot_snr


class RangedPiezo:
    """Fake piezo with a declared travel range (so bounds logic engages)."""

    def __init__(self, initial_um: float = 150.0, lo: float = 100.0, hi: float = 200.0):
        self._pos = float(initial_um)
        self._lo = float(lo)
        self._hi = float(hi)
        self.moves: List[float] = []

    def get_position(self) -> float:
        return self._pos

    def get_range(self) -> Tuple[float, float]:
        return (self._lo, self._hi)

    def move_to(self, position_um: float) -> None:
        self.moves.append(float(position_um))
        self._pos = float(position_um)


class NeverLocksController:
    """acquire_lock_reference always fails (forces the fallback tiers)."""

    def __init__(self, laser_af=None):
        self._is_running = True
        self.acquire_calls = 0
        self._laser_af = laser_af

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        self._is_running = True

    def stop(self) -> None:
        self._is_running = False

    def acquire_lock_reference(self, timeout_s: float = 5.0, confirmation_readings=None):
        self.acquire_calls += 1
        return False


class SpotAtZLaserAF:
    """Laser AF whose reflection spot is only visible when the piezo is within
    ``window_um`` of ``true_focus_um``. Models a chamber whose focal plane sits
    at true_focus_um: away from it, no spot (NaN); near it, displacement is the
    signed distance to focus.
    """

    def __init__(self, piezo: RangedPiezo, true_focus_um: float, window_um: float = 6.0, snr: float = 8.0):
        self._piezo = piezo
        self._true = float(true_focus_um)
        self._window = float(window_um)
        self._snr = float(snr)
        self.measure_calls = 0

    def measure_displacement_continuous(self):
        self.measure_calls += 1
        pos = self._piezo.get_position()
        err = pos - self._true  # >0 means piezo above focus
        if abs(err) > self._window:
            return SimpleResult(displacement_um=float("nan"), spot_snr=0.0)
        # In-window: report the displacement to nutllify (piezo should move by -err).
        return SimpleResult(displacement_um=err, spot_snr=self._snr)


def _strategy(controller, piezo, laser_af, memory, **kw):
    return PerFOVFocusLockStrategy(
        focus_lock_controller=controller,
        piezo_service=piezo,
        memory=memory,
        laser_af_controller=laser_af,
        settle_s=0.0,
        sleep_fn=lambda _s: None,
        **kw,
    )


class TestSweepReacquire:
    def test_sweep_regrabs_shifted_chamber(self):
        """Seed is 150 but the chamber's focus moved to 170 (14 um beyond the
        6 um AF window). Strict lock + single-shot at the seed both fail; the
        sweep must find the spot near 170 and null the residual."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = RangedPiezo(initial_um=150.0)
        laser_af = SpotAtZLaserAF(piezo, true_focus_um=170.0, window_um=6.0)
        controller = NeverLocksController(laser_af)
        strat = _strategy(
            controller, piezo, laser_af, mem,
            sweep_range_um=30.0, sweep_step_um=2.0,
        )

        result = strat.prepare("region_0", 3)

        assert result.ok
        assert result.method == "sweep"
        assert result.degraded is False  # positively re-found → safe to cache
        # Piezo ended near the true focus (170), not pinned at the stale seed.
        assert abs(piezo.get_position() - 170.0) < 1.0

    def test_sweep_result_is_cached_and_tracks_drift(self):
        """A successful sweep is non-degraded, so record_success caches the
        corrected Z — the next round then seeds from ~170 instead of 150."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = RangedPiezo(initial_um=150.0)
        laser_af = SpotAtZLaserAF(piezo, true_focus_um=170.0, window_um=6.0)
        controller = NeverLocksController(laser_af)
        strat = _strategy(controller, piezo, laser_af, mem, sweep_range_um=30.0, sweep_step_um=2.0)

        r = strat.prepare("region_0", 3)
        assert r.ok and not r.degraded and r.locked_piezo_um is not None
        strat.record_success("region_0", 3, r.locked_piezo_um)

        # Next round: the memory seeds this FOV from the corrected Z, not default.
        seed = mem.get_seed("region_0", 3)
        assert seed is not None
        assert abs(seed - 170.0) < 1.0

    def test_sweep_finds_nothing_restores_piezo_to_seed(self):
        """If the shift is beyond the sweep range, the spot is never found; the
        piezo must be restored to the seed and the FOV degrades (seed tier)."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = RangedPiezo(initial_um=150.0)
        # Focus is at 195 but sweep only spans +/-10 around the seed 150.
        laser_af = SpotAtZLaserAF(piezo, true_focus_um=195.0, window_um=4.0)
        controller = NeverLocksController(laser_af)
        strat = _strategy(controller, piezo, laser_af, mem, sweep_range_um=10.0, sweep_step_um=2.0)

        result = strat.prepare("region_0", 0)

        assert result.ok
        assert result.method == "seed"
        assert result.degraded is True  # not found → must NOT be cached
        # Piezo restored to the seed, not left at the far edge of the sweep.
        assert piezo.get_position() == 150.0

    def test_sweep_disabled_falls_to_seed(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = RangedPiezo(initial_um=150.0)
        laser_af = SpotAtZLaserAF(piezo, true_focus_um=170.0, window_um=6.0)
        controller = NeverLocksController(laser_af)
        strat = _strategy(
            controller, piezo, laser_af, mem,
            enable_single_shot_fallback=False,
            enable_sweep_fallback=False,
        )
        result = strat.prepare("region_0", 0)
        assert result.method in ("seed", "no_seed")
        assert result.degraded is True

    def test_single_shot_still_wins_when_spot_at_seed(self):
        """When the spot IS visible at the seed, single-shot handles it and the
        (more expensive) sweep never runs."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = RangedPiezo(initial_um=150.0)
        # Focus 2 um away — within both the AF window and the single-shot cap.
        laser_af = SpotAtZLaserAF(piezo, true_focus_um=152.0, window_um=6.0)
        controller = NeverLocksController(laser_af)
        strat = _strategy(controller, piezo, laser_af, mem)
        result = strat.prepare("region_0", 0)
        assert result.method == "single_shot"
        assert abs(piezo.get_position() - 152.0) < 0.01


class TestSeedBoundsClamp:
    def test_stale_seed_beyond_range_is_clamped(self):
        """A cached seed of 500 with a piezo range [100, 200] must clamp into the
        usable travel instead of commanding the piezo to 500."""
        mem = FOVFocusMemory()
        mem.remember("region_0", 0, 500.0)  # absurd stale value
        piezo = RangedPiezo(initial_um=150.0, lo=100.0, hi=200.0)
        laser_af = SpotAtZLaserAF(piezo, true_focus_um=150.0, window_um=6.0)
        controller = NeverLocksController(laser_af)
        strat = _strategy(controller, piezo, laser_af, mem, enable_sweep_fallback=False)

        strat.prepare("region_0", 0)

        # The seed move must never exceed hi - edge_margin (200 - 1 = 199).
        assert max(piezo.moves) <= 199.0 + 1e-9

    def test_in_range_seed_not_clamped(self):
        mem = FOVFocusMemory(default_z_um=160.0)
        piezo = RangedPiezo(initial_um=150.0, lo=100.0, hi=200.0)
        laser_af = SpotAtZLaserAF(piezo, true_focus_um=160.0, window_um=6.0)
        controller = NeverLocksController(laser_af)
        strat = _strategy(controller, piezo, laser_af, mem, enable_sweep_fallback=False)
        result = strat.prepare("region_0", 0)
        # Seed 160 is well within [100,200]; used verbatim.
        assert result.seed_used_um == 160.0

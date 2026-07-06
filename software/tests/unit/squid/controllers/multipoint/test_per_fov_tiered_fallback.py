"""Tests for the 3-tier fallback in PerFOVFocusLockStrategy.

Tier 1: strict acquire_lock_reference — method="locked"
Tier 2: single-shot measure_displacement_continuous + piezo correction — method="single_shot"
Tier 3: use seeded piezo position, capture anyway — method="seed" (or "no_seed")
"""

from __future__ import annotations

import math
from typing import List, Optional

from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory
from squid.backend.controllers.multipoint.per_fov_focus_lock import (
    PerFOVFocusLockStrategy,
)


class FakeLaserAF:
    """Fake LaserAutofocusController exposing measure_displacement_continuous."""

    def __init__(self, results: Optional[List[object]] = None):
        self._results = list(results) if results else []
        self.measure_calls = 0

    def measure_displacement_continuous(self):
        self.measure_calls += 1
        if self._results:
            return self._results.pop(0)

        class Result:
            displacement_um = float("nan")
            spot_snr = 0.0

        return Result()


class SimpleResult:
    def __init__(self, displacement_um: float, spot_snr: float):
        self.displacement_um = displacement_um
        self.spot_snr = spot_snr


class FakePiezo:
    def __init__(self, initial_um: float = 150.0):
        self._pos = float(initial_um)
        self.moves: List[float] = []

    def get_position(self) -> float:
        return self._pos

    def move_to(self, position_um: float) -> None:
        self.moves.append(float(position_um))
        self._pos = float(position_um)


class FakeControllerNeverLocks:
    """Controller whose acquire_lock_reference always returns False."""

    def __init__(self):
        self._is_running = True
        self.acquire_calls = 0
        self._laser_af = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self):
        self._is_running = True

    def stop(self):
        self._is_running = False

    def acquire_lock_reference(self, timeout_s: float = 5.0, confirmation_readings=None):
        self.acquire_calls += 1
        return False


class FakeControllerAlwaysLocks:
    def __init__(self, piezo: FakePiezo, shift_on_lock: float = 0.0):
        self._is_running = True
        self.acquire_calls = 0
        self._laser_af = None
        self._piezo = piezo
        self._shift = shift_on_lock

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self):
        self._is_running = True

    def stop(self):
        self._is_running = False

    def acquire_lock_reference(self, timeout_s: float = 5.0, confirmation_readings=None):
        self.acquire_calls += 1
        if self._shift:
            self._piezo.move_to(self._piezo.get_position() + self._shift)
        return True


# ---------------------------------------------------------------------------
# Tier 1: strict lock
# ---------------------------------------------------------------------------


class TestTier1Lock:
    def test_lock_succeeds_method_is_locked(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeControllerAlwaysLocks(piezo, shift_on_lock=0.3)
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )
        result = strat.prepare("r", 0)
        assert result.ok
        assert result.method == "locked"
        assert result.locked_piezo_um is not None


# ---------------------------------------------------------------------------
# Tier 2: single-shot fallback
# ---------------------------------------------------------------------------


class TestTier2SingleShot:
    def test_single_shot_applies_displacement_correction(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo(initial_um=150.0)
        controller = FakeControllerNeverLocks()
        # Single valid measurement: displacement=2.5 um (piezo should correct by -2.5)
        laser_af = FakeLaserAF(results=[SimpleResult(displacement_um=2.5, spot_snr=5.0)])
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            laser_af_controller=laser_af,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )
        result = strat.prepare("r", 0)
        assert result.ok
        assert result.method == "single_shot"
        # Piezo started at 150, seeded to 150, then corrected by -2.5 → 147.5
        assert piezo.get_position() == 147.5
        # The locked_piezo_um should be the corrected position
        assert result.locked_piezo_um == 147.5
        # Laser AF got one measurement call
        assert laser_af.measure_calls == 1

    def test_single_shot_rejects_nan_displacement(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeControllerNeverLocks()
        laser_af = FakeLaserAF(results=[SimpleResult(displacement_um=float("nan"), spot_snr=5.0)])
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            laser_af_controller=laser_af,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )
        result = strat.prepare("r", 0)
        assert result.ok  # falls through to seed tier
        assert result.method in ("seed", "no_seed")  # NaN rejected, used seed

    def test_single_shot_rejects_low_snr(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeControllerNeverLocks()
        laser_af = FakeLaserAF(results=[SimpleResult(displacement_um=0.5, spot_snr=0.5)])
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            laser_af_controller=laser_af,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )
        result = strat.prepare("r", 0)
        assert result.ok
        # SNR < 1.0 rejected, falls through to seed
        assert result.method in ("seed", "no_seed")

    def test_single_shot_disabled(self):
        """When single_shot_fallback is disabled, skip tier 2.

        Also disable the Tier 2.5 sweep here so this isolates the single-shot
        tier: with both re-acquire tiers off, a valid reading must NOT be
        consumed and we fall straight through to the seed tier.
        """
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeControllerNeverLocks()
        laser_af = FakeLaserAF(results=[SimpleResult(displacement_um=2.0, spot_snr=5.0)])
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            laser_af_controller=laser_af,
            enable_single_shot_fallback=False,
            enable_sweep_fallback=False,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )
        result = strat.prepare("r", 0)
        assert result.ok
        assert result.method in ("seed", "no_seed")
        assert laser_af.measure_calls == 0  # Tier 2 + 2.5 never called


# ---------------------------------------------------------------------------
# Tier 3: seed fallback
# ---------------------------------------------------------------------------


class TestTier3Seed:
    def test_seed_fallback_with_default(self):
        """Nothing works — fall back to seeded position (default), capture anyway."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo(initial_um=100.0)
        controller = FakeControllerNeverLocks()
        # Laser AF returns invalid readings
        laser_af = FakeLaserAF(results=[SimpleResult(displacement_um=float("nan"), spot_snr=0.0)])
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            laser_af_controller=laser_af,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )
        result = strat.prepare("r", 0)
        assert result.ok
        assert result.method == "seed"
        # Piezo was seeded to default 150
        assert piezo.get_position() == 150.0
        assert result.seed_used_um == 150.0

    def test_no_seed_fallback(self):
        """No cached value AND no default — method='no_seed', piezo unchanged."""
        mem = FOVFocusMemory()  # no default
        piezo = FakePiezo(initial_um=123.0)
        controller = FakeControllerNeverLocks()
        laser_af = FakeLaserAF(results=[SimpleResult(displacement_um=float("nan"), spot_snr=0.0)])
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            laser_af_controller=laser_af,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )
        result = strat.prepare("r", 0)
        assert result.ok
        assert result.method == "no_seed"
        assert piezo.get_position() == 123.0  # untouched (no seed to apply)
        assert result.seed_used_um is None

    def test_seed_fallback_disabled_returns_not_ok(self):
        """When all fallbacks disabled, ok=False."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeControllerNeverLocks()
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            enable_single_shot_fallback=False,
            enable_seed_fallback=False,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )
        result = strat.prepare("r", 0)
        assert result.ok is False
        assert result.method is None


# ---------------------------------------------------------------------------
# End-to-end: simulate 25 FOVs with mixed lock outcomes
# ---------------------------------------------------------------------------


class TestRealisticMixedScenario:
    def test_25_fovs_with_flaky_laser_af_still_completes_all(self):
        """Simulated realistic water-run scenario: lock fails often, but single-shot
        usually works, and seed fallback catches the rest. ALL FOVs must complete."""
        import random

        rng = random.Random(42)
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo(initial_um=150.0)

        # Controller fails 80% of the time
        class FlakyController:
            is_running = True
            acquire_calls = 0
            _laser_af = None

            def start(self):
                pass

            def stop(self):
                pass

            def acquire_lock_reference(self, timeout_s=5.0, confirmation_readings=None):
                self.acquire_calls += 1
                return rng.random() < 0.2  # 20% success rate

        controller = FlakyController()

        # Laser AF returns valid measurement 60% of the time
        class FlakyLaserAF:
            measure_calls = 0

            def measure_displacement_continuous(self):
                self.measure_calls += 1
                if rng.random() < 0.6:
                    return SimpleResult(
                        displacement_um=rng.uniform(-2.0, 2.0),
                        spot_snr=rng.uniform(2.0, 10.0),
                    )
                else:
                    return SimpleResult(displacement_um=float("nan"), spot_snr=0.0)

        laser_af = FlakyLaserAF()

        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            laser_af_controller=laser_af,
            max_attempts=1,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )

        methods: List[str] = []
        for fov in range(25):
            r = strat.prepare("region_5", fov)
            assert r.ok, f"FOV {fov} failed — tiered fallback should guarantee ok=True"
            methods.append(r.method)
            if r.locked_piezo_um is not None:
                strat.record_success("region_5", fov, r.locked_piezo_um)

        # All 25 FOVs completed
        assert len(methods) == 25
        # Expect a mix of methods
        method_counts = {m: methods.count(m) for m in set(methods)}
        print(f"Method distribution: {method_counts}")
        # Should have some locked + some single_shot + some seed
        assert "seed" in methods or "single_shot" in methods

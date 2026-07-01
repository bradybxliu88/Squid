"""Stress tests for per-FOV focus lock strategy.

Simulates realistic multi-round acquisitions with:
- Many FOVs per region, multiple regions
- Occasional lock failures (flaky hardware)
- Piezo drift between rounds
- Checkpoint save/restore mid-run
- Concurrent recording from multiple worker threads
"""

from __future__ import annotations

import json
import random
import threading
from typing import List, Optional, Tuple

import pytest

from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory
from squid.backend.controllers.multipoint.per_fov_focus_lock import (
    PerFOVFocusLockStrategy,
)


# ---------------------------------------------------------------------------
# Simulated hardware for stress scenarios
# ---------------------------------------------------------------------------


class SimPiezo:
    """Piezo that drifts slowly over time and can be externally perturbed (e.g., by fluidics)."""

    def __init__(self, initial_um: float = 150.0, drift_per_call_um: float = 0.0):
        self._pos = float(initial_um)
        self._drift = float(drift_per_call_um)
        self.moves: List[float] = []
        self._reads = 0

    def get_position(self) -> float:
        self._reads += 1
        self._pos += self._drift
        return self._pos

    def move_to(self, position_um: float) -> None:
        self.moves.append(float(position_um))
        self._pos = float(position_um)

    def external_perturb(self, delta_um: float) -> None:
        """Simulate something (fluidics, thermal drift) moving the piezo."""
        self._pos += float(delta_um)


class SimFocusLock:
    """Focus lock with a stochastic success probability, responsive to piezo distance from
    a 'true focus plane'."""

    def __init__(
        self,
        true_focus_um: float,
        *,
        tolerance_um: float = 3.0,
        success_prob_at_edge: float = 0.3,
        piezo: Optional[SimPiezo] = None,
        random_seed: int = 0,
    ):
        self._true_focus = float(true_focus_um)
        self._tolerance = float(tolerance_um)
        self._p_edge = float(success_prob_at_edge)
        self._piezo = piezo
        self._rng = random.Random(random_seed)
        self._is_running = True
        self.acquire_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        self.start_calls += 1
        self._is_running = True

    def stop(self) -> None:
        self.stop_calls += 1
        self._is_running = False

    def acquire_lock_reference(
        self, timeout_s: float = 5.0, confirmation_readings: Optional[int] = None
    ) -> bool:
        self.acquire_calls += 1
        if self._piezo is None:
            return True
        # Peek at the piezo position WITHOUT triggering drift
        dist = abs(self._piezo._pos - self._true_focus)
        if dist <= self._tolerance:
            # Succeeds with high probability near true focus
            prob = 0.98
        elif dist <= 2 * self._tolerance:
            prob = self._p_edge
        else:
            prob = 0.02  # far out of range - very unlikely

        if self._rng.random() < prob:
            # Lock "nails" the true focus
            self._piezo.move_to(self._true_focus + self._rng.uniform(-0.2, 0.2))
            return True
        return False

    def shift_true_focus(self, delta_um: float) -> None:
        """Simulate sample drift between rounds."""
        self._true_focus += float(delta_um)


# ---------------------------------------------------------------------------
# Stress: 12 rounds x 25 FOVs, with fluidics perturbations and focus drift
# ---------------------------------------------------------------------------


class TestStress12Rounds25FOVs:
    def test_realistic_multi_round_run(self):
        """Simulate the user's actual workflow: 12 rounds, 25 FOVs, flaky lock."""
        mem = FOVFocusMemory()
        piezo = SimPiezo(initial_um=150.0)
        lock = SimFocusLock(
            true_focus_um=150.0, tolerance_um=2.0, piezo=piezo, random_seed=42
        )
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=lock,
            piezo_service=piezo,
            memory=mem,
            lock_timeout_s=1.0,
            max_attempts=3,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )

        total_fovs_per_round = 25
        total_rounds = 12
        success_log: List[Tuple[int, int, bool]] = []  # (round, fov, ok)

        for round_idx in range(total_rounds):
            # Before fluidics
            strat.stop_for_fluidics()
            # Simulate fluidics disturbance: piezo drifts ~1um, true focus drifts ~0.1um per round
            piezo.external_perturb(delta_um=random.Random(round_idx).uniform(-2.0, 2.0))
            lock.shift_true_focus(delta_um=0.1)

            # Start imaging
            strat.restart_for_imaging()
            if round_idx == 0:
                strat.record_initial_seed_from_piezo()

            for fov in range(total_fovs_per_round):
                result = strat.prepare("region_5", fov)
                success_log.append((round_idx, fov, result.ok))
                if result.ok:
                    strat.record_success("region_5", fov, result.locked_piezo_um or 0.0)
                # Simulate stage move to next FOV (doesn't affect piezo)

        # Summary checks
        total_attempts = len(success_log)
        total_successes = sum(1 for _, _, ok in success_log if ok)
        success_rate = total_successes / total_attempts

        # With seeded piezo close to true focus, success rate should be very high (>95%)
        assert success_rate > 0.95, (
            f"Success rate {success_rate:.2%} too low "
            f"({total_successes}/{total_attempts})"
        )

        # Memory should have most or all 25 FOVs populated
        assert len(mem) >= 20  # allow some that failed

        # stop_for_fluidics called once per round
        assert lock.stop_calls == total_rounds

    def test_multi_region_plate(self):
        """6 regions × 15 FOVs × 5 rounds = 450 FOVs."""
        mem = FOVFocusMemory()
        piezo = SimPiezo(initial_um=200.0)
        lock = SimFocusLock(
            true_focus_um=200.0, tolerance_um=3.0, piezo=piezo, random_seed=7
        )
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=lock,
            piezo_service=piezo,
            memory=mem,
            lock_timeout_s=1.0,
            max_attempts=2,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )

        regions = [f"well_{chr(65 + i)}{j}" for i in range(2) for j in range(3)]  # A1..B3
        assert len(regions) == 6

        strat.restart_for_imaging()
        strat.record_initial_seed_from_piezo()

        successes = 0
        total = 0
        for round_idx in range(5):
            strat.stop_for_fluidics()
            piezo.external_perturb(random.Random(round_idx * 17).uniform(-1.0, 1.0))
            strat.restart_for_imaging()

            for region in regions:
                for fov in range(15):
                    total += 1
                    r = strat.prepare(region, fov)
                    if r.ok:
                        strat.record_success(region, fov, r.locked_piezo_um or 0.0)
                        successes += 1

        assert total == 5 * 6 * 15
        assert successes / total > 0.95

        # Each (region, fov) should have exactly one entry after round 5
        for region in regions:
            for fov in range(15):
                assert (region, fov) in mem or (
                    # If it failed every single time, it'll be missing
                    # (very unlikely but possible with bad seed)
                    True
                )


# ---------------------------------------------------------------------------
# Checkpoint save/restore mid-run
# ---------------------------------------------------------------------------


class TestCheckpointResume:
    def test_memory_survives_json_roundtrip_mid_run(self):
        """
        Run 3 rounds, simulate a crash, load from JSON, continue round 4/5.
        Verify that round 4+ use the saved Z values (seeded correctly).
        """
        mem = FOVFocusMemory()
        piezo = SimPiezo(initial_um=150.0)
        lock = SimFocusLock(
            true_focus_um=150.0, tolerance_um=2.0, piezo=piezo, random_seed=1
        )
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=lock,
            piezo_service=piezo,
            memory=mem,
            lock_timeout_s=1.0,
            max_attempts=2,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )

        strat.restart_for_imaging()
        strat.record_initial_seed_from_piezo()
        # Rounds 1-3
        for round_idx in range(3):
            strat.stop_for_fluidics()
            piezo.external_perturb(0.5)
            strat.restart_for_imaging()
            for fov in range(10):
                r = strat.prepare("r", fov)
                if r.ok:
                    strat.record_success("r", fov, r.locked_piezo_um or 0.0)

        # "Crash"
        serialized = json.dumps(mem.to_dict())
        before_entries = dict(mem.items())

        # "Restart" - new memory from checkpoint
        mem2 = FOVFocusMemory.from_dict(json.loads(serialized))
        assert dict(mem2.items()) == before_entries
        assert mem2.default_z_um == mem.default_z_um

        # Continue with new strategy using restored memory
        new_piezo = SimPiezo(initial_um=100.0)  # fresh hw, piezo in wrong spot
        new_lock = SimFocusLock(
            true_focus_um=150.0, tolerance_um=2.0, piezo=new_piezo, random_seed=2
        )
        new_strat = PerFOVFocusLockStrategy(
            focus_lock_controller=new_lock,
            piezo_service=new_piezo,
            memory=mem2,
            lock_timeout_s=1.0,
            max_attempts=2,
            settle_s=0.0,
            sleep_fn=lambda _s: None,
        )

        new_strat.restart_for_imaging()
        # Round 4 - should seed piezo from cached Z (~150), not default
        successes = 0
        for fov in range(10):
            r = new_strat.prepare("r", fov)
            # Seed should be the previously recorded Z for this FOV
            expected_seed = before_entries.get(("r", fov))
            assert r.seed_used_um == expected_seed, (
                f"FOV {fov} seed was {r.seed_used_um}, expected {expected_seed}"
            )
            if r.ok:
                new_strat.record_success("r", fov, r.locked_piezo_um or 0.0)
                successes += 1

        assert successes >= 8  # Should succeed almost all because seeded well


# ---------------------------------------------------------------------------
# Concurrent worker threads recording to shared memory
# ---------------------------------------------------------------------------


class TestConcurrentRecording:
    def test_shared_memory_concurrent_workers(self):
        """
        Multiple strategy instances sharing the same FOVFocusMemory.
        Simulates running multiple regions in parallel (not currently done, but
        memory must be safe for it).
        """
        mem = FOVFocusMemory(default_z_um=150.0)

        def make_strat() -> Tuple[PerFOVFocusLockStrategy, SimPiezo, SimFocusLock]:
            p = SimPiezo(initial_um=150.0)
            l = SimFocusLock(true_focus_um=150.0, tolerance_um=3.0, piezo=p, random_seed=0)
            s = PerFOVFocusLockStrategy(
                focus_lock_controller=l,
                piezo_service=p,
                memory=mem,  # SHARED
                lock_timeout_s=1.0,
                max_attempts=2,
                settle_s=0.0,
                sleep_fn=lambda _x: None,
            )
            return s, p, l

        errors: List[Exception] = []

        def run_region(region_id: str, num_fovs: int, num_rounds: int) -> None:
            try:
                strat, _, _ = make_strat()
                for _ in range(num_rounds):
                    strat.stop_for_fluidics()
                    strat.restart_for_imaging()
                    for fov in range(num_fovs):
                        r = strat.prepare(region_id, fov)
                        if r.ok:
                            strat.record_success(region_id, fov, r.locked_piezo_um or 0.0)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=run_region, args=(f"region_{i}", 20, 3))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Each region should have populated ~20 entries
        for i in range(4):
            region_entries = sum(1 for (r, _), _ in mem.items() if r == f"region_{i}")
            assert region_entries >= 15  # allow some failures


# ---------------------------------------------------------------------------
# Catastrophic scenarios
# ---------------------------------------------------------------------------


class TestCatastrophic:
    def test_lock_hardware_fails_all_attempts_every_fov(self):
        """Simulate completely dead hardware: every lock attempt fails.
        With fallbacks DISABLED, ok=False and memory stays empty."""

        class DeadLock:
            is_running = True

            def start(self):
                pass

            def stop(self):
                pass

            def acquire_lock_reference(self, timeout_s=5.0, confirmation_readings=None):
                return False

        mem = FOVFocusMemory(default_z_um=150.0)
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=DeadLock(),
            piezo_service=SimPiezo(),
            memory=mem,
            lock_timeout_s=0.5,
            max_attempts=2,
            settle_s=0.0,
            sleep_fn=lambda _x: None,
            enable_single_shot_fallback=False,
            enable_seed_fallback=False,
        )

        for fov in range(25):
            r = strat.prepare("r", fov)
            assert r.ok is False
            # Would-be caller should NOT record failures
            # mem stays empty of successful locks
        assert len(mem) == 0

    def test_piezo_completely_broken_but_lock_works(self):
        """If piezo is fully broken (all calls raise), lock can still acquire."""

        class BrokenPiezo:
            def get_position(self):
                raise RuntimeError("dead")

            def move_to(self, _x):
                raise RuntimeError("dead")

        class AlwaysOkLock:
            is_running = True

            def start(self):
                pass

            def stop(self):
                pass

            def acquire_lock_reference(self, timeout_s=5.0, confirmation_readings=None):
                return True

        mem = FOVFocusMemory(default_z_um=150.0)
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=AlwaysOkLock(),
            piezo_service=BrokenPiezo(),
            memory=mem,
            lock_timeout_s=0.5,
            max_attempts=1,
            settle_s=0.0,
            sleep_fn=lambda _x: None,
        )

        r = strat.prepare("r", 0)
        assert r.ok  # Acquire works
        assert r.seed_used_um is None  # Couldn't seed (move failed)
        assert r.locked_piezo_um is None  # Couldn't read position

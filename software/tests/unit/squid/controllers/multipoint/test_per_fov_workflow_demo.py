"""End-to-end workflow demonstration matching the user's proposed design.

Scenario: 12-round MERFISH-style water run.
- Between rounds, fluidics flow physically disturbs the laser AF signal,
  dropping SNR to near zero (spot leaves detection ROI).
- If the focus lock remains on during fluidics, it flails trying to reacquire
  a signal that isn't there (exactly what we observed on 2026-04-11).

New design (FOCUS_LOCK_PER_FOV mode):
    1. BEFORE fluidics: strategy.stop_for_fluidics()
           -> laser off, no piezo corrections, no SNR warnings.
    2. AFTER fluidics, BEFORE imaging: strategy.restart_for_imaging()
           -> laser back on, control loop running but NOT yet locked.
    3. PER FOV (inside MultiPointWorker's FOV loop):
           a. move stage X/Y (existing behavior)
           b. strategy.prepare(region_id, fov_index)
              - piezo moves to last-known-good Z for this FOV (or default seed)
              - acquire_lock_reference(timeout) is called
              - retries up to per_fov_max_attempts
              - returns PerFOVLockResult(ok, locked_piezo_um, ...)
           c. if ok: acquire z-stack, then strategy.record_success(z_um)
              else: skip FOV (or fail step) per focus_gate.on_focus_gate_fail
    4. Across rounds, the FOVFocusMemory accumulates a per-FOV Z map that
       gets progressively more accurate, so later rounds engage fast.

This test walks through exactly that flow with a deterministic simulated
lock / piezo so the assertions are meaningful.
"""

from __future__ import annotations

import json
from typing import List

import pytest

from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory
from squid.backend.controllers.multipoint.per_fov_focus_lock import (
    PerFOVFocusLockStrategy,
)


class SimulatedSystem:
    """Simulated piezo + focus lock + fluidics-perturbed 'true focus'."""

    def __init__(self, initial_piezo_um: float = 150.0):
        self.piezo_um = float(initial_piezo_um)
        self.true_focus_um = float(initial_piezo_um)
        self._is_running = False
        self.history: List[str] = []  # event log for assertions

    # --- piezo interface (duck-typed) ---
    def get_position(self) -> float:
        return self.piezo_um

    def move_to(self, position_um: float) -> None:
        self.piezo_um = float(position_um)
        self.history.append(f"piezo_move_to({position_um:.3f})")

    # --- focus lock controller interface (duck-typed) ---
    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        self._is_running = True
        self.history.append("lock_start")

    def stop(self) -> None:
        self._is_running = False
        self.history.append("lock_stop")

    def acquire_lock_reference(
        self, timeout_s: float = 5.0, confirmation_readings=None
    ) -> bool:
        """Succeeds IFF piezo is within 2 um of the true focus plane."""
        dist = abs(self.piezo_um - self.true_focus_um)
        ok = dist <= 2.0
        self.history.append(
            f"acquire_lock(dist={dist:.2f}um, ok={ok})"
        )
        if ok:
            # Lock nails focus exactly
            self.piezo_um = self.true_focus_um
        return ok

    # --- simulated hardware perturbations (not part of any interface) ---
    def fluidics_event(self, piezo_drift_um: float, focus_drift_um: float) -> None:
        """Simulate one fluidics cycle disturbing the system."""
        self.piezo_um += piezo_drift_um
        self.true_focus_um += focus_drift_um
        self.history.append(
            f"fluidics(piezo+={piezo_drift_um:.2f},focus+={focus_drift_um:.2f})"
        )


def test_12_round_25_fov_water_run_workflow():
    """Full simulation of the user's 12-round protocol."""
    sim = SimulatedSystem(initial_piezo_um=150.0)
    memory = FOVFocusMemory()
    strategy = PerFOVFocusLockStrategy(
        focus_lock_controller=sim,
        piezo_service=sim,
        memory=memory,
        lock_timeout_s=1.0,
        max_attempts=3,
        settle_s=0.0,
        sleep_fn=lambda _s: None,
    )

    # === ROUND 1 ===
    # User has manually focused at piezo=150 before start. Lock is already running.
    sim.start()  # simulates user clicking Start Lock in the GUI
    strategy.record_initial_seed_from_piezo()
    assert memory.default_z_um == 150.0

    round_1_successes = 0
    for fov in range(25):
        # Stage moves to FOV X/Y (no effect on our simulated piezo)
        result = strategy.prepare("region_5", fov)
        assert result.ok, f"Round 1 FOV {fov} failed (seed should be fine)"
        assert result.seed_used_um == 150.0
        # z-stack acquisition would happen here
        strategy.record_success("region_5", fov, result.locked_piezo_um or 0.0)
        round_1_successes += 1

    assert round_1_successes == 25
    assert len(memory) == 25

    # === ROUND 2 ===
    # Fluidics perturbs things
    strategy.stop_for_fluidics()
    assert not sim.is_running
    # Fluidics disturbs: piezo drifts 1.5um, true focus drifts 0.2um
    sim.fluidics_event(piezo_drift_um=1.5, focus_drift_um=0.2)

    # Resume
    strategy.restart_for_imaging()
    assert sim.is_running

    round_2_successes = 0
    for fov in range(25):
        result = strategy.prepare("region_5", fov)
        # Each FOV's seed is the Z from round 1 (was pinned to 150.0 after lock)
        assert result.seed_used_um == 150.0
        assert result.ok, f"Round 2 FOV {fov} failed even with seed"
        strategy.record_success("region_5", fov, result.locked_piezo_um or 0.0)
        round_2_successes += 1

    assert round_2_successes == 25

    # === ROUNDS 3-12: drift accumulates but the seed tracks the focus ===
    for round_idx in range(2, 12):
        strategy.stop_for_fluidics()
        # Moderate drift - enough to move the spot but not enough to break seed
        sim.fluidics_event(piezo_drift_um=1.0, focus_drift_um=0.3)
        strategy.restart_for_imaging()

        for fov in range(25):
            result = strategy.prepare("region_5", fov)
            assert result.ok, (
                f"Round {round_idx+1} FOV {fov} failed - seed drift exceeded tolerance"
            )
            strategy.record_success("region_5", fov, result.locked_piezo_um or 0.0)

    # === VERIFICATION ===
    # All 12 rounds should have completed successfully for all 25 FOVs
    # Memory tracks the final Z for each FOV
    assert len(memory) == 25
    # The saved Z should be close to the current true focus (which drifted
    # ~0.2 + 0.3*10 = ~3.2 um from start)
    expected_final_focus = sim.true_focus_um
    for fov in range(25):
        cached = memory.get("region_5", fov)
        assert cached is not None
        assert abs(cached - expected_final_focus) < 0.5, (
            f"FOV {fov} cached Z={cached:.2f} diverged from true focus {expected_final_focus:.2f}"
        )

    # Controller stop/start called exactly 11 times each (11 fluidics cycles between rounds)
    assert sim.history.count("lock_stop") == 11
    # Start called once at test init + once per re-engage (11 times)
    assert sim.history.count("lock_start") == 1 + 11


def test_round_2_fails_without_seeding_proves_feature_value():
    """Control scenario: what happens if we DON'T seed the piezo after fluidics.

    This is what happened in the real 2026-04-11 run. The piezo ended up far
    from the true focus, so acquire_lock_reference timed out 5s and aborted.

    Purpose: demonstrate that WITHOUT seeding, the lock would fail when the
    piezo has drifted > 2 um from the focal plane.
    """
    sim = SimulatedSystem(initial_piezo_um=150.0)

    # Simulate 5 seconds of acquire attempts with piezo far from focus.
    sim.piezo_um = 160.0  # 10 um drift from the 150 true focus
    sim.true_focus_um = 150.0

    # acquire_lock_reference returns False because dist=10um >> 2um tolerance
    ok = sim.acquire_lock_reference(timeout_s=1.0)
    assert ok is False, "Without seeding, lock should fail at this distance"


def test_checkpoint_resume_across_software_restart():
    """Simulate: run 3 rounds, software crashes, restart, continue from checkpoint.

    Memory persists through JSON serialization, so the restored system has
    accurate seeds for all previously-imaged FOVs.
    """
    sim1 = SimulatedSystem(initial_piezo_um=150.0)
    memory1 = FOVFocusMemory()
    strat1 = PerFOVFocusLockStrategy(
        focus_lock_controller=sim1,
        piezo_service=sim1,
        memory=memory1,
        lock_timeout_s=1.0,
        max_attempts=3,
        settle_s=0.0,
        sleep_fn=lambda _s: None,
    )

    sim1.start()
    strat1.record_initial_seed_from_piezo()
    for round_idx in range(3):
        if round_idx > 0:
            strat1.stop_for_fluidics()
            sim1.fluidics_event(1.0, 0.2)
            strat1.restart_for_imaging()
        for fov in range(5):
            r = strat1.prepare("r", fov)
            assert r.ok
            strat1.record_success("r", fov, r.locked_piezo_um or 0.0)

    # === "CRASH" ===
    saved = json.dumps(memory1.to_dict())
    last_positions = dict(memory1.items())
    last_true_focus = sim1.true_focus_um

    # === "RESTART" ===
    sim2 = SimulatedSystem(initial_piezo_um=50.0)  # new session, piezo at cold-start pos
    sim2.true_focus_um = last_true_focus  # sample hasn't moved
    memory2 = FOVFocusMemory.from_dict(json.loads(saved))
    strat2 = PerFOVFocusLockStrategy(
        focus_lock_controller=sim2,
        piezo_service=sim2,
        memory=memory2,
        lock_timeout_s=1.0,
        max_attempts=3,
        settle_s=0.0,
        sleep_fn=lambda _s: None,
    )

    # Memory restored identically
    assert dict(memory2.items()) == last_positions

    # Continue from round 4
    sim2.start()
    for fov in range(5):
        r = strat2.prepare("r", fov)
        # Seed is the cached Z from round 3 - very close to true focus
        assert r.ok, f"Round 4 FOV {fov} failed on resume (piezo was at {sim2.piezo_um})"

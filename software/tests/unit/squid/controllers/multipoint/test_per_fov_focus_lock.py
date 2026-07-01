"""Unit tests for PerFOVFocusLockStrategy."""

from __future__ import annotations

from typing import List, Optional

import pytest

from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory
from squid.backend.controllers.multipoint.per_fov_focus_lock import (
    PerFOVFocusLockStrategy,
    PerFOVLockResult,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePiezo:
    def __init__(self, initial_um: float = 150.0, move_raises: bool = False, get_raises: bool = False):
        self._pos = float(initial_um)
        self.moves: List[float] = []
        self._move_raises = move_raises
        self._get_raises = get_raises

    def get_position(self) -> float:
        if self._get_raises:
            raise RuntimeError("simulated piezo read fail")
        return self._pos

    def move_to(self, position_um: float) -> None:
        if self._move_raises:
            raise RuntimeError("simulated piezo move fail")
        self.moves.append(float(position_um))
        self._pos = float(position_um)


class FakeFocusLock:
    """Minimal focus-lock controller fake.

    - ``acquire_results`` is a list of booleans consumed in order across
      attempts. When exhausted, defaults to False.
    - ``shift_on_acquire_um`` optionally moves the simulated piezo by that
      amount when acquire succeeds (so the recorded Z differs from the seed).
    """

    def __init__(
        self,
        acquire_results: Optional[List[bool]] = None,
        *,
        running: bool = True,
        piezo: Optional[FakePiezo] = None,
        shift_on_acquire_um: float = 0.0,
        start_raises: bool = False,
        acquire_raises_first_n: int = 0,
    ) -> None:
        self._acquire_results = list(acquire_results) if acquire_results is not None else []
        self._is_running = running
        self.start_calls = 0
        self.stop_calls = 0
        self.acquire_calls: List[dict] = []
        self._piezo = piezo
        self._shift = float(shift_on_acquire_um)
        self._start_raises = start_raises
        self._acquire_raises_first_n = acquire_raises_first_n

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        self.start_calls += 1
        if self._start_raises:
            raise RuntimeError("simulated start failure")
        self._is_running = True

    def stop(self) -> None:
        self.stop_calls += 1
        self._is_running = False

    def acquire_lock_reference(
        self,
        timeout_s: float = 5.0,
        confirmation_readings: Optional[int] = None,
    ) -> bool:
        self.acquire_calls.append(
            {"timeout_s": timeout_s, "confirmation_readings": confirmation_readings}
        )
        if len(self.acquire_calls) <= self._acquire_raises_first_n:
            raise RuntimeError("simulated acquire exception")
        if self._acquire_results:
            result = self._acquire_results.pop(0)
        else:
            result = False
        if result and self._piezo is not None and self._shift:
            # Simulate the lock actually shifting the piezo to nail focus
            self._piezo.move_to(self._piezo.get_position() + self._shift)
        return result


def _mk_sleep_recorder():
    calls: List[float] = []

    def fake_sleep(s: float) -> None:
        calls.append(float(s))

    return calls, fake_sleep


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_requires_controller(self):
        with pytest.raises(ValueError, match="focus_lock_controller"):
            PerFOVFocusLockStrategy(
                focus_lock_controller=None,  # type: ignore[arg-type]
                piezo_service=FakePiezo(),
                memory=FOVFocusMemory(),
            )

    def test_requires_memory(self):
        with pytest.raises(ValueError, match="memory"):
            PerFOVFocusLockStrategy(
                focus_lock_controller=FakeFocusLock(),
                piezo_service=FakePiezo(),
                memory=None,  # type: ignore[arg-type]
            )

    def test_rejects_bad_max_attempts(self):
        with pytest.raises(ValueError, match="max_attempts"):
            PerFOVFocusLockStrategy(
                focus_lock_controller=FakeFocusLock(),
                piezo_service=FakePiezo(),
                memory=FOVFocusMemory(),
                max_attempts=0,
            )

    def test_rejects_bad_timeout(self):
        with pytest.raises(ValueError, match="lock_timeout_s"):
            PerFOVFocusLockStrategy(
                focus_lock_controller=FakeFocusLock(),
                piezo_service=FakePiezo(),
                memory=FOVFocusMemory(),
                lock_timeout_s=0,
            )

    def test_rejects_negative_settle(self):
        with pytest.raises(ValueError, match="settle_s"):
            PerFOVFocusLockStrategy(
                focus_lock_controller=FakeFocusLock(),
                piezo_service=FakePiezo(),
                memory=FOVFocusMemory(),
                settle_s=-0.1,
            )

    def test_allows_no_piezo(self):
        """Piezo is optional - strategy should still construct without it."""
        strat = PerFOVFocusLockStrategy(
            focus_lock_controller=FakeFocusLock(),
            piezo_service=None,
            memory=FOVFocusMemory(),
        )
        assert strat is not None


# ---------------------------------------------------------------------------
# stop_for_fluidics / restart_for_imaging
# ---------------------------------------------------------------------------


class TestFluidicsHooks:
    def _strat(self, **kwargs):
        return PerFOVFocusLockStrategy(
            focus_lock_controller=kwargs.pop("controller"),
            piezo_service=kwargs.pop("piezo", FakePiezo()),
            memory=kwargs.pop("memory", FOVFocusMemory()),
            **kwargs,
        )

    def test_stop_for_fluidics_when_running(self):
        c = FakeFocusLock(running=True)
        s = self._strat(controller=c)
        assert s.stop_for_fluidics() is True
        assert c.stop_calls == 1
        assert not c.is_running

    def test_stop_for_fluidics_when_already_stopped(self):
        c = FakeFocusLock(running=False)
        s = self._strat(controller=c)
        assert s.stop_for_fluidics() is False
        assert c.stop_calls == 0

    def test_stop_swallows_exceptions(self):
        class Flaky:
            is_running = True

            def stop(self):
                raise RuntimeError("bang")

        s = self._strat(controller=Flaky())
        # Should not raise
        result = s.stop_for_fluidics()
        # Was running - return True even though stop raised
        assert result is True

    def test_restart_for_imaging_starts_if_stopped(self):
        c = FakeFocusLock(running=False)
        s = self._strat(controller=c)
        s.restart_for_imaging()
        assert c.start_calls == 1
        assert c.is_running

    def test_restart_for_imaging_noop_if_running(self):
        c = FakeFocusLock(running=True)
        s = self._strat(controller=c)
        s.restart_for_imaging()
        assert c.start_calls == 0

    def test_restart_swallows_start_exception(self):
        c = FakeFocusLock(running=False, start_raises=True)
        s = self._strat(controller=c)
        # Should not raise
        s.restart_for_imaging()


# ---------------------------------------------------------------------------
# record_initial_seed_from_piezo
# ---------------------------------------------------------------------------


class TestInitialSeed:
    def test_records_piezo_position_when_default_unset(self):
        mem = FOVFocusMemory()
        piezo = FakePiezo(initial_um=142.7)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=FakeFocusLock(),
            piezo_service=piezo,
            memory=mem,
        )
        seed = s.record_initial_seed_from_piezo()
        assert seed == 142.7
        assert mem.default_z_um == 142.7

    def test_does_not_override_existing_default(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo(initial_um=100.0)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=FakeFocusLock(),
            piezo_service=piezo,
            memory=mem,
        )
        seed = s.record_initial_seed_from_piezo()
        assert seed == 100.0  # returns what was read
        assert mem.default_z_um == 150.0  # but doesn't overwrite

    def test_no_piezo_returns_none_and_leaves_default(self):
        mem = FOVFocusMemory()
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=FakeFocusLock(),
            piezo_service=None,
            memory=mem,
        )
        assert s.record_initial_seed_from_piezo() is None
        assert mem.default_z_um is None

    def test_read_failure_returns_none_safely(self):
        mem = FOVFocusMemory()
        piezo = FakePiezo(get_raises=True)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=FakeFocusLock(),
            piezo_service=piezo,
            memory=mem,
        )
        # Should not raise
        assert s.record_initial_seed_from_piezo() is None
        assert mem.default_z_um is None


# ---------------------------------------------------------------------------
# prepare() happy path
# ---------------------------------------------------------------------------


class TestPrepareHappyPath:
    def test_seeds_piezo_and_acquires_lock_first_try(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo(initial_um=160.0)  # stale position from prior FOV
        controller = FakeFocusLock(acquire_results=[True], piezo=piezo, shift_on_acquire_um=0.2)
        _, sleep = _mk_sleep_recorder()
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            lock_timeout_s=5.0,
            max_attempts=2,
            settle_s=0.1,
            sleep_fn=sleep,
        )

        result = s.prepare("well_A1", 0)

        assert result.ok
        assert result.seed_used_um == 150.0
        assert result.locked_piezo_um == pytest.approx(150.2)
        assert result.attempts == 1
        # First move is the seed; second is the simulated lock-shift inside the fake
        assert piezo.moves[0] == 150.0
        assert len(controller.acquire_calls) == 1

    def test_prefers_cached_over_default(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        mem.remember("well_A1", 0, 143.5)
        piezo = FakePiezo()
        controller = FakeFocusLock(acquire_results=[True], piezo=piezo)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller, piezo_service=piezo, memory=mem
        )
        result = s.prepare("well_A1", 0)
        assert result.ok
        assert result.seed_used_um == 143.5
        assert piezo.moves == [143.5]

    def test_succeeds_on_second_attempt(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeFocusLock(acquire_results=[False, True], piezo=piezo)
        _, sleep = _mk_sleep_recorder()
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            max_attempts=3,
            settle_s=0.2,
            sleep_fn=sleep,
        )

        result = s.prepare("r", 0)
        assert result.ok
        assert result.attempts == 2
        assert len(controller.acquire_calls) == 2

    def test_records_success_updates_memory(self):
        mem = FOVFocusMemory()
        piezo = FakePiezo()
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=FakeFocusLock(),
            piezo_service=piezo,
            memory=mem,
        )
        s.record_success("r", 5, 143.2)
        assert mem.get("r", 5) == 143.2

    def test_record_success_with_invalid_value_swallowed(self):
        mem = FOVFocusMemory()
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=FakeFocusLock(),
            piezo_service=FakePiezo(),
            memory=mem,
        )
        # Should not raise
        s.record_success("r", 0, float("nan"))
        # Memory should be unchanged
        assert mem.get("r", 0) is None


# ---------------------------------------------------------------------------
# prepare() failure / fallback paths
# ---------------------------------------------------------------------------


class TestPrepareFailures:
    def test_all_attempts_fail_returns_not_ok(self):
        """With both fallbacks disabled, lock failure gives ok=False."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeFocusLock(acquire_results=[False, False, False], piezo=piezo)
        _, sleep = _mk_sleep_recorder()
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            max_attempts=3,
            settle_s=0.05,
            sleep_fn=sleep,
            enable_single_shot_fallback=False,
            enable_seed_fallback=False,
        )

        result = s.prepare("r", 0)
        assert result.ok is False
        assert result.attempts == 3
        assert "acquire_timeout" in (result.reason or "")
        assert result.locked_piezo_um is None

    def test_no_seed_no_piezo_move(self):
        """If no cached value and no default, do NOT move piezo."""
        mem = FOVFocusMemory()  # no default
        piezo = FakePiezo(initial_um=99.9)
        controller = FakeFocusLock(acquire_results=[True], piezo=piezo)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller, piezo_service=piezo, memory=mem
        )

        result = s.prepare("r", 0)
        assert result.ok
        assert result.seed_used_um is None
        assert piezo.moves == []  # no seed -> no move

    def test_no_piezo_but_has_seed_still_acquires(self):
        """Seed value exists but piezo driver absent - skip seed, still try lock."""
        mem = FOVFocusMemory(default_z_um=150.0)
        controller = FakeFocusLock(acquire_results=[True], piezo=None)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller, piezo_service=None, memory=mem
        )
        result = s.prepare("r", 0)
        assert result.ok
        assert result.seed_used_um is None  # can't seed without piezo
        assert result.locked_piezo_um is None  # can't read without piezo

    def test_piezo_move_failure_continues_with_acquire(self):
        """If piezo.move_to raises, we still try to acquire."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo(move_raises=True)
        controller = FakeFocusLock(acquire_results=[True], piezo=piezo)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller, piezo_service=piezo, memory=mem
        )

        result = s.prepare("r", 0)
        assert result.ok  # acquire succeeded anyway
        # seed_used_um should be None because the move failed
        assert result.seed_used_um is None

    def test_controller_start_failure_returns_failure(self):
        """Controller not running AND start raises -> return ok=False."""
        controller = FakeFocusLock(running=False, start_raises=True)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=FakePiezo(),
            memory=FOVFocusMemory(default_z_um=150.0),
        )
        result = s.prepare("r", 0)
        assert result.ok is False
        assert result.reason == "controller_start_failed"
        assert result.attempts == 0

    def test_acquire_exception_treated_as_failed_attempt(self):
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        # First call raises, second returns True
        controller = FakeFocusLock(
            acquire_results=[True], piezo=piezo, acquire_raises_first_n=1
        )
        _, sleep = _mk_sleep_recorder()
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            max_attempts=2,
            settle_s=0.0,
            sleep_fn=sleep,
        )
        result = s.prepare("r", 0)
        assert result.ok
        assert result.attempts == 2

    def test_controller_stopped_mid_experiment_restarts_it(self):
        """If controller somehow stopped, prepare() restarts it."""
        controller = FakeFocusLock(running=False, acquire_results=[True])
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=FakePiezo(),
            memory=FOVFocusMemory(default_z_um=150.0),
        )
        result = s.prepare("r", 0)
        assert result.ok
        assert controller.start_calls == 1


# ---------------------------------------------------------------------------
# settle_s behavior
# ---------------------------------------------------------------------------


class TestSettleTime:
    def test_settle_zero_skips_sleep(self):
        calls, sleep = _mk_sleep_recorder()
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeFocusLock(acquire_results=[True], piezo=piezo)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            settle_s=0.0,
            sleep_fn=sleep,
        )
        s.prepare("r", 0)
        assert calls == []

    def test_settle_called_before_acquire(self):
        calls, sleep = _mk_sleep_recorder()
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeFocusLock(acquire_results=[True], piezo=piezo)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            settle_s=0.3,
            sleep_fn=sleep,
        )
        s.prepare("r", 0)
        # One sleep for settle before first acquire
        assert calls == [0.3]

    def test_settle_between_retries(self):
        calls, sleep = _mk_sleep_recorder()
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        controller = FakeFocusLock(acquire_results=[False, False, True], piezo=piezo)
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            max_attempts=3,
            settle_s=0.1,
            sleep_fn=sleep,
        )
        s.prepare("r", 0)
        # Initial settle + 2 inter-attempt settles
        assert calls == [0.1, 0.1, 0.1]


# ---------------------------------------------------------------------------
# Timeout / confirmation parameters passed through
# ---------------------------------------------------------------------------


class TestParameterPassthrough:
    def test_lock_timeout_and_confirmation_forwarded(self):
        controller = FakeFocusLock(acquire_results=[True])
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=FakePiezo(),
            memory=FOVFocusMemory(default_z_um=150.0),
            lock_timeout_s=7.5,
            confirmation_readings=4,
            settle_s=0,
        )
        s.prepare("r", 0)
        call = controller.acquire_calls[0]
        assert call["timeout_s"] == 7.5
        assert call["confirmation_readings"] == 4

    def test_older_controller_without_confirmation_readings_kwarg(self):
        """Controllers that don't accept confirmation_readings should still work."""

        class LegacyController:
            is_running = True

            def start(self):
                pass

            def stop(self):
                pass

            def acquire_lock_reference(self, timeout_s: float = 5.0) -> bool:  # no kwarg
                return True

        s = PerFOVFocusLockStrategy(
            focus_lock_controller=LegacyController(),
            piezo_service=FakePiezo(),
            memory=FOVFocusMemory(default_z_um=150.0),
            confirmation_readings=5,
        )
        result = s.prepare("r", 0)
        assert result.ok


# ---------------------------------------------------------------------------
# End-to-end sequencing: simulate a 3-round run
# ---------------------------------------------------------------------------


class TestEndToEndSequence:
    def test_three_rounds_four_fovs_happy_path(self):
        """
        Round 1: no cached positions -> use default seed (from piezo). Lock succeeds,
                 recorded Z varies per FOV.
        Fluidics: stop -> restart.
        Round 2: each FOV seeded from round-1 cached Z. Lock succeeds, new Z recorded.
        Round 3: same, seeded from round-2 Z.
        """
        mem = FOVFocusMemory()
        piezo = FakePiezo(initial_um=150.0)
        # Each FOV lock shifts piezo by a small amount, so we see recorded Z diverge
        controller = FakeFocusLock(
            acquire_results=[True] * 12, piezo=piezo, shift_on_acquire_um=0.3
        )
        _, sleep = _mk_sleep_recorder()
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            settle_s=0,
            sleep_fn=sleep,
        )

        # Round 1: capture default from piezo
        s.restart_for_imaging()
        s.record_initial_seed_from_piezo()
        assert mem.default_z_um == 150.0

        # 4 FOVs
        for fov in range(4):
            r = s.prepare("region_0", fov)
            assert r.ok, f"Round 1 FOV {fov} failed"
            assert r.seed_used_um == 150.0  # all seeded to default in round 1
            s.record_success("region_0", fov, r.locked_piezo_um)

        # Round 1 should have 4 entries, default unchanged
        assert len(mem) == 4
        round1_positions = {fov: mem.get("region_0", fov) for fov in range(4)}

        # Fluidics break
        s.stop_for_fluidics()
        assert controller.stop_calls == 1
        piezo.move_to(99.9)  # simulate piezo drift during fluidics

        # Round 2
        s.restart_for_imaging()
        assert controller.start_calls == 1
        for fov in range(4):
            r = s.prepare("region_0", fov)
            assert r.ok
            # Seeded from round 1's cached value
            assert r.seed_used_um == round1_positions[fov]
            s.record_success("region_0", fov, r.locked_piezo_um)

        # Round 2 positions drift further (piezo shifted 0.3 per lock)
        round2_positions = {fov: mem.get("region_0", fov) for fov in range(4)}
        for fov in range(4):
            assert round2_positions[fov] > round1_positions[fov]

        # Fluidics again
        s.stop_for_fluidics()

        # Round 3
        s.restart_for_imaging()
        for fov in range(4):
            r = s.prepare("region_0", fov)
            assert r.ok
            assert r.seed_used_um == round2_positions[fov]
            s.record_success("region_0", fov, r.locked_piezo_um)

        assert controller.stop_calls == 2
        assert controller.start_calls == 2  # Round 2 + Round 3
        assert len(mem) == 4  # still 4 FOVs, just updated each round

    def test_fov_failure_does_not_corrupt_other_fovs(self):
        """If FOV 1 lock fails (and fallbacks disabled), FOVs 0/2/3 should still succeed and be recorded."""
        mem = FOVFocusMemory(default_z_um=150.0)
        piezo = FakePiezo()
        # FOV 0: True, FOV 1: False,False, FOV 2: True, FOV 3: True
        controller = FakeFocusLock(
            acquire_results=[True, False, False, True, True], piezo=piezo
        )
        s = PerFOVFocusLockStrategy(
            focus_lock_controller=controller,
            piezo_service=piezo,
            memory=mem,
            max_attempts=2,
            settle_s=0,
            sleep_fn=lambda s: None,
            enable_single_shot_fallback=False,
            enable_seed_fallback=False,
        )

        results = []
        for fov in range(4):
            r = s.prepare("r", fov)
            results.append(r)
            if r.ok:
                s.record_success("r", fov, r.locked_piezo_um or 150.0)

        assert results[0].ok
        assert not results[1].ok
        assert results[2].ok
        assert results[3].ok
        assert mem.get("r", 0) is not None
        assert mem.get("r", 1) is None  # FOV 1 failed -> not recorded
        assert mem.get("r", 2) is not None
        assert mem.get("r", 3) is not None

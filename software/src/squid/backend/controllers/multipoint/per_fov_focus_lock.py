"""Per-FOV focus lock strategy: seed piezo Z, engage lock, record outcome.

This helper encapsulates the behavior triggered when
``AutofocusMode.FOCUS_LOCK_PER_FOV`` is selected. It is called by
MultiPointWorker before each FOV's z-stack acquisition:

    strategy = PerFOVFocusLockStrategy(...)
    result = strategy.prepare(region_id, fov_index)
    if result.ok:
        # acquire z-stack normally
        strategy.record_success(region_id, fov_index)
    else:
        # skip FOV / report failure per focus_gate policy

It handles:
- Restoring the piezo to the FOV's last-known-good Z (or a global seed on
  round 1, or leaving it alone if neither is set).
- Engaging the focus lock via ``acquire_lock_reference``.
- Bounded retries with settle-time between attempts.
- Recording the post-lock piezo Z into the FOVFocusMemory on success.

Nothing in here touches the stage X/Y — the caller is responsible for having
already moved to the FOV position before calling :meth:`prepare`.

This module has **no direct hardware imports**: it takes protocol-style
``piezo_service`` and ``focus_lock_controller`` objects, so it is easy to
unit-test with fakes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import squid.core.logging

from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory

_log = squid.core.logging.get_logger(__name__)


class _PiezoServiceLike(Protocol):
    def get_position(self) -> float: ...
    def move_to(self, position_um: float) -> None: ...


class _FocusLockControllerLike(Protocol):
    @property
    def is_running(self) -> bool: ...  # type: ignore[misc]

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def acquire_lock_reference(
        self, timeout_s: float = 5.0, confirmation_readings: Optional[int] = None
    ) -> bool: ...


@dataclass(frozen=True)
class PerFOVLockResult:
    """Outcome of a per-FOV prepare call.

    Method semantics (when ok=True):
      - "locked": acquire_lock_reference succeeded — tightest focus correction
      - "single_shot": one measure_displacement_continuous returned valid; we
                       corrected piezo using that single displacement reading
      - "seed": neither lock nor single-shot worked; piezo is at the seeded
                Z (either cached or default). Z-stack will still capture an
                image but the focal plane may be off by whatever drift exists.
      - "no_seed": no cached or default seed was available; piezo wasn't moved.
    """

    ok: bool
    # Seed value that was used (or None if no seed available / piezo wasn't moved).
    seed_used_um: Optional[float]
    # Piezo position after lock engaged (only meaningful if ok is True).
    locked_piezo_um: Optional[float]
    # Human-readable reason when ok is False (or degradation when method != "locked").
    reason: Optional[str]
    # How many acquire attempts were made (>=1).
    attempts: int
    # Which tier succeeded: "locked" | "single_shot" | "seed" | "no_seed" | None (if ok=False)
    method: Optional[str] = None
    # True when focus was NOT positively verified this FOV (seed/no_seed fallbacks).
    # The caller images anyway, but the piezo Z is UNTRUSTWORTHY: it must not be
    # recorded into FOVFocusMemory (would poison future seeds) and should raise a
    # focus-quality warning.
    degraded: bool = False


class PerFOVFocusLockStrategy:
    """Per-FOV focus lock engagement with seeded piezo Z.

    Args:
        focus_lock_controller: The continuous focus lock controller. Must
            implement ``is_running``, ``start()``, ``stop()``, and
            ``acquire_lock_reference(timeout_s, confirmation_readings)``.
        piezo_service: Piezo driver (``get_position`` / ``move_to``). May be
            None, in which case seeding is disabled (lock is attempted
            wherever the hardware happens to be).
        memory: Shared :class:`FOVFocusMemory` - persists through the experiment
            and is checkpointed.
        lock_timeout_s: Per-attempt timeout for acquire_lock_reference.
        max_attempts: Total acquire attempts before giving up (>=1).
        settle_s: Sleep between piezo seed move and acquire attempt, and
            between retries. Allows hardware to quiesce.
        confirmation_readings: Forwarded to ``acquire_lock_reference``.
    """

    def __init__(
        self,
        focus_lock_controller: _FocusLockControllerLike,
        piezo_service: Optional[_PiezoServiceLike],
        memory: FOVFocusMemory,
        *,
        lock_timeout_s: float = 5.0,
        max_attempts: int = 2,
        settle_s: float = 0.1,
        confirmation_readings: Optional[int] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
        # New: tiered fallback settings
        enable_single_shot_fallback: bool = True,
        enable_seed_fallback: bool = True,
        laser_af_controller: Optional[object] = None,
        # Single-shot (Tier 2) safety bounds. A single displacement reading must
        # not command a large or edge-pinning piezo move (a spurious reflection
        # otherwise slams the piezo to a travel limit, then gets cached as the
        # seed). Corrections violating these bounds are rejected -> fall to seed.
        single_shot_min_snr: float = 2.0,
        single_shot_max_disp_um: float = 10.0,
        piezo_edge_margin_um: float = 1.0,
        # Ephemeral run: an ad-hoc "Run Current FOV" acquisition. Its FOV lives at
        # wherever the user just focused, so we must NOT seed the piezo from (or
        # record into) the persistent per-FOV memory — a stale cross-run Z would
        # drive the piezo off the sample. Set explicitly by the caller rather than
        # inferred from the region name (live-scan also uses a region named
        # "current", but that IS a real, seedable multi-FOV region).
        ephemeral: bool = False,
    ) -> None:
        if focus_lock_controller is None:
            raise ValueError("focus_lock_controller is required")
        if memory is None:
            raise ValueError("memory is required")
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        if lock_timeout_s <= 0:
            raise ValueError(f"lock_timeout_s must be > 0, got {lock_timeout_s}")
        if settle_s < 0:
            raise ValueError(f"settle_s must be >= 0, got {settle_s}")

        self._controller = focus_lock_controller
        self._piezo = piezo_service
        self._memory = memory
        self._lock_timeout_s = float(lock_timeout_s)
        self._max_attempts = int(max_attempts)
        self._settle_s = float(settle_s)
        self._confirmation_readings = confirmation_readings
        self._sleep = sleep_fn or time.sleep
        self._enable_single_shot = bool(enable_single_shot_fallback)
        self._enable_seed_fallback = bool(enable_seed_fallback)
        self._single_shot_min_snr = float(single_shot_min_snr)
        self._single_shot_max_disp_um = abs(float(single_shot_max_disp_um))
        self._piezo_edge_margin_um = abs(float(piezo_edge_margin_um))
        self._ephemeral = bool(ephemeral)
        # Optional: direct access to the laser AF for single-shot fallback.
        # If not provided, we try to extract it from the controller.
        self._laser_af = laser_af_controller
        if self._laser_af is None:
            self._laser_af = getattr(focus_lock_controller, "_laser_af", None)

    # ---------------------------------------------------------------------
    # Hooks for orchestrator/experiment_runner
    # ---------------------------------------------------------------------
    def stop_for_fluidics(self) -> bool:
        """Stop the focus lock before fluidics runs. Safe to call when already stopped.

        Returns True if the controller was running before this call.
        """
        was_running = bool(getattr(self._controller, "is_running", False))
        if was_running:
            try:
                self._controller.stop()
            except Exception:
                _log.exception("PerFOV focus lock: failed to stop controller for fluidics")
        return was_running

    def restart_for_imaging(self) -> None:
        """Ensure the focus lock controller is running before per-FOV engagement.

        The lock will NOT be acquired here - each FOV calls :meth:`prepare`
        which handles seed + acquire. We just need the laser on + control loop
        running so ``acquire_lock_reference`` has fresh measurements to work with.
        """
        if not bool(getattr(self._controller, "is_running", False)):
            try:
                self._controller.start()
            except Exception:
                _log.exception("PerFOV focus lock: failed to start controller")

    def record_initial_seed_from_piezo(self) -> Optional[float]:
        """Capture the piezo's current position as the round-1 default seed.

        Call this once per imaging step, immediately after
        :meth:`restart_for_imaging`, if no per-FOV entries exist yet. This lets
        the first round's FOVs start from a sensible Z even before any lock
        has been established for them.

        Returns the seed captured, or None if no piezo is available.
        """
        if self._piezo is None:
            return None
        try:
            z = float(self._piezo.get_position())
        except Exception:
            _log.exception("PerFOV focus lock: failed to read piezo for initial seed")
            return None
        # Only populate default if it is currently unset (do not override a user value)
        if self._memory.default_z_um is None:
            self._memory.set_default(z)
        return z

    # ---------------------------------------------------------------------
    # Per-FOV entry
    # ---------------------------------------------------------------------
    def prepare(self, region_id: str, fov_index: int) -> PerFOVLockResult:
        """Seed the piezo (if we have a Z) and engage focus lock.

        Flow:
            1. Look up a seed Z (cached > default > None).
            2. If we have a seed and the piezo driver is available, move there.
            3. Sleep ``settle_s`` so the motion settles.
            4. Call ``acquire_lock_reference(timeout)`` up to ``max_attempts``.
            5. Return a PerFOVLockResult describing what happened.

        Does NOT record the successful Z - use :meth:`record_success` for that,
        called by MultiPointWorker after it has verified the FOV actually imaged.
        """
        if self._ephemeral:
            # Ad-hoc single-FOV run: never seed from a prior run's Z (it belongs
            # to a different physical location). Lock/measure at wherever the user
            # just focused instead of jumping the piezo off the sample.
            seed = None
        else:
            seed = self._memory.get_seed(region_id, fov_index)

        # Ensure the controller is running; re-attempt if stopped silently.
        if not bool(getattr(self._controller, "is_running", False)):
            try:
                self._controller.start()
            except Exception:
                _log.exception("PerFOV focus lock: controller start failed during prepare")
                return PerFOVLockResult(
                    ok=False,
                    seed_used_um=seed,
                    locked_piezo_um=None,
                    reason="controller_start_failed",
                    attempts=0,
                )

        # Seed the piezo
        seed_used: Optional[float] = None
        if seed is not None and self._piezo is not None:
            try:
                self._piezo.move_to(float(seed))
                seed_used = float(seed)
            except Exception:
                _log.exception(
                    "PerFOV focus lock: piezo.move_to(%.3f) failed for region=%s fov=%d",
                    seed,
                    region_id,
                    fov_index,
                )
                # Continue anyway - maybe the lock can still acquire without seeding

        # Let the piezo settle
        if self._settle_s > 0:
            self._sleep(self._settle_s)

        # ================================================================
        # TIER 1: Try strict lock via acquire_lock_reference
        # ================================================================
        last_reason: Optional[str] = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                ok = bool(
                    self._controller.acquire_lock_reference(
                        timeout_s=self._lock_timeout_s,
                        confirmation_readings=self._confirmation_readings,
                    )
                )
            except TypeError:
                # Older controllers without confirmation_readings kwarg
                try:
                    ok = bool(self._controller.acquire_lock_reference(timeout_s=self._lock_timeout_s))
                except Exception:
                    _log.exception("PerFOV focus lock: acquire_lock_reference raised")
                    last_reason = "acquire_exception"
                    ok = False
            except Exception:
                _log.exception("PerFOV focus lock: acquire_lock_reference raised")
                last_reason = "acquire_exception"
                ok = False

            if ok:
                locked_z = None
                if self._piezo is not None:
                    try:
                        locked_z = float(self._piezo.get_position())
                    except Exception:
                        _log.exception("PerFOV focus lock: failed to read piezo after lock")
                return PerFOVLockResult(
                    ok=True,
                    seed_used_um=seed_used,
                    locked_piezo_um=locked_z,
                    reason=None,
                    attempts=attempt,
                    method="locked",
                )

            last_reason = last_reason or f"acquire_timeout_{self._lock_timeout_s:.1f}s"
            if attempt < self._max_attempts:
                _log.info(
                    "PerFOV focus lock: attempt %d/%d failed for region=%s fov=%d; retrying after %.2fs",
                    attempt,
                    self._max_attempts,
                    region_id,
                    fov_index,
                    self._settle_s,
                )
                if self._settle_s > 0:
                    self._sleep(self._settle_s)

        # ================================================================
        # TIER 2: Single-shot displacement correction
        # If the strict lock failed but the laser AF can still see the spot
        # at all, use one measurement to correct piezo Z.
        # ================================================================
        if self._enable_single_shot and self._laser_af is not None:
            single_shot_result = self._try_single_shot_correction(
                region_id, fov_index, seed_used
            )
            if single_shot_result is not None:
                return single_shot_result

        # ================================================================
        # TIER 3: Seed-only fallback
        # Neither lock nor single-shot worked. The piezo is at the seeded
        # position (or wherever it happened to be if no seed). Proceed with
        # capture anyway — the z-stack may still cover the focal plane.
        # ================================================================
        if self._enable_seed_fallback:
            piezo_z = None
            if self._piezo is not None:
                try:
                    piezo_z = float(self._piezo.get_position())
                except Exception:
                    _log.exception("PerFOV focus lock: failed to read piezo for seed fallback")
            method = "seed" if seed_used is not None else "no_seed"
            _log.warning(
                "PerFOV focus lock: lock + single-shot failed for region=%s fov=%d; "
                "falling back to %s at piezo=%s",
                region_id, fov_index, method,
                f"{piezo_z:.2f}um" if piezo_z is not None else "unknown",
            )
            return PerFOVLockResult(
                ok=True,  # Proceed with capture, but flag degraded mode
                seed_used_um=seed_used,
                locked_piezo_um=piezo_z,
                reason=f"{last_reason}; fell_back_to_{method}",
                attempts=self._max_attempts,
                method=method,
                degraded=True,  # focus NOT verified — caller must not cache this Z
            )

        return PerFOVLockResult(
            ok=False,
            seed_used_um=seed_used,
            locked_piezo_um=None,
            reason=last_reason,
            attempts=self._max_attempts,
            method=None,
        )

    def _try_single_shot_correction(
        self, region_id: str, fov_index: int, seed_used: Optional[float]
    ) -> Optional[PerFOVLockResult]:
        """One-shot displacement measurement + piezo correction.

        Calls laser_af.measure_displacement_continuous() once. If it returns
        a valid displacement (non-NaN, reasonable SNR), applies the
        correction to the piezo and returns an ok=True result. If the
        measurement is invalid, returns None so the caller can fall through
        to the seed fallback.
        """
        import math

        measure = getattr(self._laser_af, "measure_displacement_continuous", None)
        if not callable(measure):
            return None
        try:
            result = measure()
        except Exception:
            _log.exception(
                "PerFOV focus lock: single-shot measure_displacement_continuous raised"
            )
            return None

        disp_um = getattr(result, "displacement_um", float("nan"))
        spot_snr = getattr(result, "spot_snr", 0.0)

        # Relaxed validity check: spot must be detected (non-NaN) with minimal SNR.
        # Lower bar than strict lock because we're only using one sample.
        if disp_um is None or math.isnan(disp_um) or math.isinf(disp_um):
            return None
        if spot_snr is None or math.isnan(spot_snr) or spot_snr < self._single_shot_min_snr:
            return None
        # Reject an implausibly large single-reading correction. A genuine focal
        # error is small; a big displacement is almost always a spurious/aliased
        # reflection. Applying it would slam the piezo far off the sample.
        if abs(float(disp_um)) > self._single_shot_max_disp_um:
            _log.warning(
                "PerFOV focus lock: single-shot displacement %.2fum exceeds max %.2fum; "
                "rejecting (region=%s fov=%d)",
                disp_um, self._single_shot_max_disp_um, region_id, fov_index,
            )
            return None

        # Apply correction: move piezo by -displacement_um (to nullify measured error).
        # If displacement is 3 µm (too far from focus), move piezo by -3 µm.
        corrected_z = None
        if self._piezo is not None:
            try:
                current = float(self._piezo.get_position())
                target = current - float(disp_um)
                # Reject corrections that would pin the piezo at (or past) a travel
                # limit — the resulting edge Z is meaningless and would be cached.
                if not self._piezo_target_in_bounds(target):
                    _log.warning(
                        "PerFOV focus lock: single-shot target %.2fum is at/beyond the "
                        "piezo travel limit (margin=%.2fum); rejecting (region=%s fov=%d)",
                        target, self._piezo_edge_margin_um, region_id, fov_index,
                    )
                    return None
                self._piezo.move_to(target)
                if self._settle_s > 0:
                    self._sleep(self._settle_s)
                corrected_z = float(self._piezo.get_position())
            except Exception:
                _log.exception(
                    "PerFOV focus lock: failed to apply single-shot correction"
                )
                return None

        _log.info(
            "PerFOV focus lock: single-shot correction applied for region=%s fov=%d "
            "(displacement=%.3fum, snr=%.2f, piezo=%.2fum)",
            region_id, fov_index, disp_um, spot_snr,
            corrected_z if corrected_z is not None else float("nan"),
        )
        return PerFOVLockResult(
            ok=True,
            seed_used_um=seed_used,
            locked_piezo_um=corrected_z,
            reason=f"single_shot_snr={spot_snr:.2f}_disp={disp_um:.2f}um",
            attempts=self._max_attempts,
            method="single_shot",
        )

    def _piezo_target_in_bounds(self, target_um: float) -> bool:
        """True if ``target_um`` is within the piezo's usable travel.

        Returns True when the piezo does not expose ``get_range`` (e.g. fakes /
        piezos without a declared range) so behavior is unchanged there.
        """
        get_range = getattr(self._piezo, "get_range", None)
        if not callable(get_range):
            return True
        try:
            lo, hi = get_range()
            lo = float(lo)
            hi = float(hi)
        except Exception:
            return True
        margin = self._piezo_edge_margin_um
        return (lo + margin) <= float(target_um) <= (hi - margin)

    # ---------------------------------------------------------------------
    # Record successful lock for later rounds
    # ---------------------------------------------------------------------
    def record_success(self, region_id: str, fov_index: int, z_um: float) -> None:
        """Cache the successful lock's Z so subsequent rounds seed from it.

        Typically called by MultiPointWorker after acquire_at_position completes.
        """
        if self._ephemeral:
            # Never cache ephemeral single-FOV positions — they'd become a stale
            # cross-run seed and drive the piezo off the next sample.
            return
        try:
            self._memory.remember(region_id, fov_index, float(z_um))
        except ValueError:
            _log.warning(
                "PerFOV focus lock: refused to record invalid z_um=%r for region=%s fov=%d",
                z_um,
                region_id,
                fov_index,
            )

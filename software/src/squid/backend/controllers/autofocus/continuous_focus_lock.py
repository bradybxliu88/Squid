from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

import numpy as np

import squid.core.logging
from squid.backend.controllers.autofocus.laser_auto_focus_controller import (
    LaserAFResult,
    LaserAutofocusController,
)
from squid.backend.services.piezo_service import PiezoService
from squid.core.config.focus_lock import FocusLockConfig, FocusLockMode
from squid.backend.controllers.base import BaseController
from squid.core.events import (
    AdjustFocusLockTargetCommand,
    EventBus,
    FocusLockFrameUpdated,
    FocusLockMetricsUpdated,
    FocusLockModeChanged,
    FocusLockPiezoLimitCritical,
    FocusLockSearchProgress,
    FocusLockStatusChanged,
    FocusLockWarning,
    PauseFocusLockCommand,
    ReleaseFocusLockReferenceCommand,
    ResumeFocusLockCommand,
    SetFocusLockAutoSearchCommand,
    SetFocusLockModeCommand,
    SetFocusLockParamsCommand,
    SetFocusLockReferenceCommand,
    StartFocusLockCommand,
    StopFocusLockCommand,
    handles,
)


@dataclass(frozen=True)
class FocusLockCandidate:
    """Scored candidate for lock acquisition or re-lock search."""

    piezo_position_um: float
    displacement_um: float
    target_error_um: float
    spot_snr: float
    correlation: float
    score: float
    is_valid: bool


class ContinuousFocusLockController(BaseController):
    """Continuous closed-loop focus lock using laser autofocus."""

    def __init__(
        self,
        laser_af: LaserAutofocusController,
        piezo_service: PiezoService,
        event_bus: EventBus,
        config: Optional[FocusLockConfig] = None,
    ) -> None:
        super().__init__(event_bus)
        self._laser_af = laser_af
        self._piezo_service = piezo_service
        self._config = config or FocusLockConfig()

        self._mode: FocusLockMode = self._config.default_mode
        self._status: str = "disabled"
        self._laser_on = False
        self._paused = False
        self._running = False
        self._should_run = False
        self._lock_reference_active = False
        self._target_um = 0.0

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        # Set by the control loop when it observes _paused=True and is guaranteed
        # to issue no further correction this cycle; awaited by pause() so that
        # call is synchronous (no stray piezo move fires after pause() returns).
        self._pause_ack_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._lock_buffer_fill = 0
        self._error_history: Deque[float] = deque(maxlen=max(10, self._config.buffer_length * 3))
        # Short-window error buffer used for the quality metric so that a
        # one-off large excursion does not linger in the RMS for seconds.
        self._recent_error_window: Deque[float] = deque(maxlen=8)
        self._drift_history: Deque[tuple[float, float]] = deque(maxlen=30)
        self._smoothed_quality = 1.0  # Exponential moving average of lock quality (0-1)

        self._warning_debounce_s = 5.0
        self._warning_last_time: dict[str, float] = {}

        # Frame preview configuration
        self._preview_publish_period_s = 0.2  # Limit preview updates to 5 Hz.
        self._last_preview_publish_time = 0.0

        # Recovery state
        self._recovery_attempts_remaining = 0
        self._recovery_start_time: Optional[float] = None
        self._recovery_good_count = 0
        # Consecutive-bad counter for recovery exhaustion. Any good frame
        # resets it; any bad frame increments it. Used instead of wall-clock
        # so flicker near the threshold doesn't silently burn the budget.
        self._consecutive_bad_count = 0
        # Persistent-large-error counter for the "unrecoverable" guard.
        # Incremented whenever |error| > lock_break_error_um; reset whenever
        # |error| dips below. Only drives locked->recovering when it exceeds
        # lock_break_persist_cycles.
        self._persistent_large_error_count = 0
        # Persistent-untrustworthy counter (sustained NaN / low SNR after
        # the short NaN-holdover has expired). Mirrors the above for the
        # "spot disappeared" failure mode.
        self._persistent_untrustworthy_count = 0

        # Auto-search state (runtime toggle, can differ from config default)
        self._auto_search_enabled = self._config.auto_search_enabled
        self._search_phase: str = ""  # "last_position" or "sweep"
        self._search_position: float = 0.0
        self._search_positions: list[float] = []
        self._search_position_index: int = 0
        self._search_candidate_confirmations: int = 0

        # Search timeout tracking
        self._search_start_time: Optional[float] = None

        # Reference piezo position when lock was set (for search recovery)
        self._locked_piezo_um: float = 0.0
        self._locked_spot_x_px: float = float("nan")
        self._latest_spot_x_px: float = float("nan")

        # PI controller state
        self._integral_accumulator: float = 0.0

        # NaN holdover state
        self._last_good_error_um: float = 0.0
        self._consecutive_nan_count: int = 0
        self._latest_valid_displacement_um: float = float("nan")

        # Deterministic lock acquisition / stale-measurement guards.
        self._acquire_confirmation_readings: int = max(2, min(3, self._config.buffer_length))
        self._search_confirmation_readings: int = 2
        self._stale_measurement_limit: int = 5
        self._last_measurement_signature: Optional[tuple[float, float, float, float]] = None
        self._stale_measurement_count: int = 0
        self._last_published_status: Optional[str] = None

        # Per-FOV focus lock memory (for FOCUS_LOCK_PER_FOV mode). Attached here
        # because the controller outlives MultiPointWorker and is the natural
        # shared-state owner for cross-round data. Lazily initialized.
        from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory  # local import to avoid cycle
        self.fov_focus_memory: FOVFocusMemory = FOVFocusMemory()

    @property
    def mode(self) -> FocusLockMode:
        with self._lock:
            return self._mode

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def is_active(self) -> bool:
        """True if focus lock was started and should be used (even if paused)."""
        with self._lock:
            return self._should_run

    def set_mode(self, mode: FocusLockMode) -> None:
        if mode not in ("off", "on"):
            raise ValueError(f"Invalid focus lock mode: {mode}")
        with self._lock:
            if mode == self._mode:
                return
            self._mode = mode
        self._event_bus.publish(FocusLockModeChanged(mode=mode))

        if mode == "off":
            self.stop()

    def start(self, target_um: float = 0.0) -> None:
        # Soft-init: apply cached ROI/exposure from per-objective settings without
        # running the stage-moving auto-calibration. Lets the user click Start
        # directly without first running the legacy "Initialize" workflow.
        if not self._laser_af.is_initialized:
            try:
                self._laser_af.initialize_manual(self._laser_af.laser_af_properties)
            except Exception:
                self._log.exception("Cannot start: soft-init via initialize_manual failed")
                return
        # has_reference is no longer required up front. The lock target is held
        # by this controller; users click Lock to capture the current spot
        # position as the reference once spot detection looks healthy.
        with self._lock:
            if self._running:
                return
            self._target_um = float(target_um)
            self._mode = "on"
            self._paused = False
            self._should_run = True
            self._stop_event.clear()
            self._reset_lock_state()
            self._running = True
            self._thread = threading.Thread(
                target=self._control_loop,
                name="ContinuousFocusLock",
                daemon=True,
            )
            self._turn_on_laser()
            # Ensure focus camera is streaming for measure_displacement_continuous()
            if not self._laser_af._camera_service.get_is_streaming():
                self._laser_af._camera_service.start_streaming()
            self._thread.start()
        self._event_bus.publish(FocusLockModeChanged(mode="on"))
        self._set_status("ready")

    def stop(self) -> None:
        thread = None
        with self._lock:
            if not self._running and self._status == "disabled":
                return
            self._mode = "off"
            self._paused = False
            self._running = False
            self._should_run = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._cleanup()
        self._set_status("disabled")
        self._event_bus.publish(FocusLockModeChanged(mode="off"))

    def pause(self) -> None:
        """Pause focus corrections without stopping the control loop.

        The control loop continues running to monitor focus state, but no
        piezo corrections are applied. The laser stays on for sensing.
        Use this during image capture to prevent piezo jitter.

        Synchronous: blocks (bounded) until the control loop has observed the
        pause and is guaranteed to issue no further piezo correction this cycle.
        Without this handshake an in-flight loop iteration that already passed
        the is_paused check could still fire one move_to_fast() up to ~one loop
        period after pause() returned, perturbing a piezo position the caller
        reads immediately afterwards (e.g. MultiPointWorker's pre-z-stack seed).
        """
        with self._lock:
            if not self._should_run:
                # Not started, nothing to pause
                return
            if self._paused:
                # Already paused
                return
            self._paused = True
            # Clear the ack under the same lock the loop uses to read _paused so
            # the loop can only re-set it AFTER observing paused=True (no stale
            # or lost wakeup).
            self._pause_ack_event.clear()
            loop_thread = self._thread
            loop_running = self._running
        self._set_status("paused")

        # Fast path / deadlock guard: nothing to wait for if the loop is not
        # running, or if we are (unexpectedly) called from the loop thread.
        if (
            not loop_running
            or loop_thread is None
            or not loop_thread.is_alive()
            or loop_thread is threading.current_thread()
        ):
            return

        # Bounded wait: a few loop periods so an unusually slow or stalled loop
        # can never hang the caller. On timeout we degrade to best-effort.
        ack_timeout_s = max(0.15, 5.0 / self._config.loop_rate_hz)
        if not self._pause_ack_event.wait(ack_timeout_s):
            self._log.warning(
                "pause(): focus-lock control loop did not acknowledge pause "
                "within %.3fs; a stray piezo correction may still be in flight",
                ack_timeout_s,
            )

    def resume(self) -> None:
        """Resume focus corrections after a pause.

        Resumes piezo corrections without resetting lock state.
        The lock continues from where it was before pause.
        """
        with self._lock:
            if not self._should_run:
                return
            if not self._paused:
                # Not paused, nothing to resume
                return
            self._paused = False
            # Restore status based on lock state
            if self._lock_buffer_fill >= self._config.buffer_length:
                new_status = "locked"
            elif self._lock_buffer_fill > 0:
                new_status = "ready"
            else:
                new_status = "ready"
        self._set_status(new_status)

    def shutdown(self) -> None:
        self.stop()
        super().shutdown()

    def apply_settings(self, settings: "object") -> None:
        """Apply acquisition-scoped focus-lock settings synchronously."""
        updates = {}
        for field_name in (
            "buffer_length",
            "recovery_attempts",
            "min_spot_snr",
            "acquire_threshold_um",
            "maintain_threshold_um",
        ):
            value = getattr(settings, field_name, None)
            if value is not None:
                updates[field_name] = value

        if updates:
            with self._lock:
                self._config = self._config.model_copy(update=updates)
                self._lock_buffer_fill = min(self._lock_buffer_fill, self._config.buffer_length)
                self._error_history = deque(
                    self._error_history,
                    maxlen=max(10, self._config.buffer_length * 3),
                )
                self._acquire_confirmation_readings = max(2, min(3, self._config.buffer_length))
        auto_search_enabled = getattr(settings, "auto_search_enabled", None)
        if auto_search_enabled is not None:
            with self._lock:
                self._auto_search_enabled = bool(auto_search_enabled)
        self._last_published_status = None  # Force publish to refresh UI
        self._set_status(self._status)

    def wait_for_lock(self, timeout_s: float = 5.0) -> bool:
        if not self.is_running:
            return False
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            if self.status == "locked":
                return True
            if self._stop_event.wait(0.05):
                return False
        self._log.warning(f"wait_for_lock timed out after {timeout_s}s")
        return False

    def adjust_target(self, delta_um: float) -> None:
        with self._lock:
            self._target_um += float(delta_um)

    def set_lock_reference(self) -> None:
        """Engage lock at the current displacement reference."""
        self._set_lock_reference()

    def release_lock_reference(self) -> None:
        """Release active lock reference and return to ready state."""
        self._release_lock_reference()

    @handles(SetFocusLockModeCommand)
    def _on_set_mode_command(self, cmd: SetFocusLockModeCommand) -> None:
        self.set_mode(cmd.mode)

    @handles(StartFocusLockCommand)
    def _on_start_command(self, cmd: StartFocusLockCommand) -> None:
        self.start(target_um=cmd.target_um)

    @handles(StopFocusLockCommand)
    def _on_stop_command(self, cmd: StopFocusLockCommand) -> None:
        self.stop()

    @handles(PauseFocusLockCommand)
    def _on_pause_command(self, cmd: PauseFocusLockCommand) -> None:
        self.pause()

    @handles(ResumeFocusLockCommand)
    def _on_resume_command(self, cmd: ResumeFocusLockCommand) -> None:
        self.resume()

    @handles(AdjustFocusLockTargetCommand)
    def _on_adjust_target_command(self, cmd: AdjustFocusLockTargetCommand) -> None:
        self.adjust_target(cmd.delta_um)

    @handles(SetFocusLockReferenceCommand)
    def _on_set_reference_command(self, cmd: SetFocusLockReferenceCommand) -> None:
        self.set_lock_reference()

    @handles(ReleaseFocusLockReferenceCommand)
    def _on_release_reference_command(self, cmd: ReleaseFocusLockReferenceCommand) -> None:
        self.release_lock_reference()

    @handles(SetFocusLockAutoSearchCommand)
    def _on_auto_search_command(self, cmd: SetFocusLockAutoSearchCommand) -> None:
        with self._lock:
            self._auto_search_enabled = cmd.enabled

    @handles(SetFocusLockParamsCommand)
    def _on_set_params(self, cmd: SetFocusLockParamsCommand) -> None:
        updates = {}
        if cmd.buffer_length is not None:
            updates["buffer_length"] = cmd.buffer_length
        if cmd.recovery_attempts is not None:
            updates["recovery_attempts"] = cmd.recovery_attempts
        if cmd.min_spot_snr is not None:
            updates["min_spot_snr"] = cmd.min_spot_snr
        if cmd.acquire_threshold_um is not None:
            updates["acquire_threshold_um"] = cmd.acquire_threshold_um
        if cmd.maintain_threshold_um is not None:
            updates["maintain_threshold_um"] = cmd.maintain_threshold_um
        if updates:
            with self._lock:
                self._config = self._config.model_copy(update=updates)
                # Keep runtime state consistent with updated thresholds/buffer.
                self._lock_buffer_fill = min(self._lock_buffer_fill, self._config.buffer_length)
                self._error_history = deque(
                    self._error_history,
                    maxlen=max(10, self._config.buffer_length * 3),
                )
            # Refresh UI-visible lock bar length even if status value is unchanged.
            self._last_published_status = None  # Force publish to refresh UI
            self._set_status(self._status)

    def acquire_lock_reference(
        self,
        timeout_s: float = 5.0,
        confirmation_readings: Optional[int] = None,
    ) -> bool:
        """Acquire a stable lock reference using bounded retries."""
        if not self.is_running:
            return False

        required = confirmation_readings or self._acquire_confirmation_readings
        deadline = time.monotonic() + max(0.0, timeout_s)
        accepted: list[LaserAFResult] = []

        while time.monotonic() <= deadline:
            result = self._laser_af.measure_displacement_continuous()
            if self._detect_stale_measurement(result):
                self._publish_warning("measurement_stale", "Focus lock measurements appear stale")
                accepted.clear()
                continue

            if not self._is_valid_lock_sample(result):
                accepted.clear()
                continue

            accepted.append(result)
            if len(accepted) > required:
                accepted = accepted[-required:]

            if len(accepted) < required:
                continue

            displacements = [sample.displacement_um for sample in accepted]
            spread = max(displacements) - min(displacements)
            if spread > self._config.acquire_threshold_um:
                accepted = accepted[-1:]
                continue

            target_um = float(sum(displacements) / len(displacements))
            spot_x_px = accepted[-1].spot_x_px
            self._commit_lock_reference(target_um, spot_x_px)
            return True

        return False

    def _set_lock_reference(self) -> None:
        """Set the lock reference at current position.

        Captures one measurement to anchor x_reference on the laser AF config so
        subsequent frames compute real displacements (otherwise the no-reference
        fallback returns 0 for every frame and the feedback loop never moves
        the piezo). Then commits the lock target at displacement = 0.
        """
        result = self._laser_af.measure_displacement_continuous()
        if (
            result.spot_x_px is not None
            and result.image is not None
            and not math.isnan(result.spot_snr)
            and result.spot_snr >= self._config.min_spot_snr
        ):
            committed = self._laser_af.commit_reference_from_result(result)
            if committed:
                # Re-measure now that has_reference is True so target_um is the
                # true (near-zero) displacement against the freshly anchored
                # reference rather than the displacement we read pre-anchor.
                result = self._laser_af.measure_displacement_continuous()

        target_um = result.displacement_um
        if math.isnan(target_um):
            target_um = self._latest_valid_displacement_um
            if not math.isnan(target_um):
                self._log.info(
                    "Using last valid displacement (%.3f um) as lock reference", target_um
                )
        if math.isnan(target_um):
            self._log.warning("Cannot set focus lock reference: no valid displacement reading")
            return
        with self._lock:
            if not self._running:
                return
        self._commit_lock_reference(target_um, result.spot_x_px)

    def _release_lock_reference(self) -> None:
        """Release the lock and return to ready state."""
        with self._lock:
            if not self._running:
                return
            self._lock_reference_active = False
            self._lock_buffer_fill = 0
            self._locked_spot_x_px = float("nan")
        # Clear the laser AF reference too so the next Lock starts from a clean
        # has_reference=False state and re-anchors against the live spot rather
        # than the previously committed (now stale) reference.
        try:
            self._laser_af.clear_reference()
        except Exception:
            self._log.exception("Failed to clear laser AF reference on release")
        self._set_status("ready")

    def _commit_lock_reference(self, target_um: float, spot_x_px: Optional[float]) -> None:
        with self._lock:
            if not self._running:
                return
            self._lock_reference_active = True
            self._locked_piezo_um = self._piezo_service.get_position()
            self._lock_buffer_fill = self._config.buffer_length
            self._target_um = target_um
            self._latest_valid_displacement_um = target_um
            self._integral_accumulator = 0.0
            self._search_candidate_confirmations = 0
            self._locked_spot_x_px = (
                float(spot_x_px) if spot_x_px is not None else float("nan")
            )
        self._set_status("locked")

    def _is_valid_lock_sample(self, result: LaserAFResult) -> bool:
        if math.isnan(result.displacement_um):
            return False
        if math.isnan(result.spot_snr) or result.spot_snr < self._config.min_spot_snr:
            return False
        if result.correlation is not None:
            if math.isnan(result.correlation):
                return False
            threshold = self._laser_af.laser_af_properties.correlation_threshold
            if result.correlation < threshold:
                return False
        return True

    def _p_gain(self, error_um: float) -> float:
        """Proportional gain that is highest at small errors and decays at large errors.

        Returns gain_max at error=0, decaying towards gain at large errors.
        This provides precise tracking near the setpoint and avoids
        oscillation at the extremes of the working range.
        """
        dx = (error_um ** 2) / self._config.gain_sigma
        scale = self._config.gain_max - self._config.gain
        return self._config.gain + scale * math.exp(-dx)

    def _control_fn(self, error_um: float, dt: float, *, update_integral: bool = True) -> float:
        """PI controller with anti-windup.

        Args:
            error_um: Current tracking error in microns (positive = above target).
            dt: Time step in seconds since last control cycle.

        Returns:
            Correction in microns to apply to the piezo.
        """
        # Proportional term
        p_correction = -self._p_gain(error_um) * error_um

        # Integral term (only when ki > 0)
        i_correction = 0.0
        if self._config.ki > 0:
            # Conditional anti-windup: don't accumulate near piezo limits
            piezo_pos = self._piezo_service.get_position()
            min_um, max_um = self._piezo_service.get_range()
            near_limit = (
                piezo_pos <= min_um + 5.0 or piezo_pos >= max_um - 5.0
            )
            if not near_limit and update_integral:
                self._integral_accumulator += error_um * dt
                # Clamp (anti-windup)
                limit = self._config.integral_limit_um
                self._integral_accumulator = max(-limit, min(limit, self._integral_accumulator))

            i_correction = -self._config.ki * self._integral_accumulator

        return p_correction + i_correction

    def _control_loop(self) -> None:
        period = 1.0 / self._config.loop_rate_hz
        metrics_period = 1.0 / self._config.metrics_rate_hz
        last_metrics_time = 0.0

        # One-shot cycle-time probe over the first N iterations. Helps decide
        # whether raising FOCUS_LOCK_LOOP_RATE_HZ is safe without dedicating
        # a runtime flag. Median is robust to startup transients.
        probe_samples: list[float] = []
        probe_target = 100
        probe_logged = False

        try:
            while not self._stop_event.is_set():
                start = time.monotonic()

                # Check if paused - skip corrections but keep monitoring.
                # Acknowledge the pause under the same lock pause() uses so a
                # synchronous pause() caller can only return once we've observed
                # paused=True here and are guaranteed to issue no correction this
                # cycle. Setting the ack under the lock keeps it strictly ordered
                # against pause()'s clear (no lost or stale wakeup).
                with self._lock:
                    is_paused = self._paused
                    if is_paused:
                        self._pause_ack_event.set()

                if is_paused:
                    # When paused: still measure and publish metrics for monitoring,
                    # but don't apply corrections or update lock state
                    result = self._laser_af.measure_displacement_continuous()
                    stale_measurement = self._detect_stale_measurement(result)
                    if not math.isnan(result.displacement_um):
                        self._latest_valid_displacement_um = result.displacement_um
                    if result.spot_x_px is not None:
                        self._latest_spot_x_px = float(result.spot_x_px)
                    error_um = self._compute_error(result)
                    is_good = self._is_good_reading(result, error_um) and not stale_measurement

                    now = time.monotonic()
                    if now - last_metrics_time >= metrics_period:
                        self._publish_metrics(result, error_um, is_good)
                        last_metrics_time = now
                    self._check_warnings(result, error_um)
                elif self._status == "searching":
                    # Handle search mode separately
                    self._search_step()
                else:
                    # Normal operation
                    result = self._laser_af.measure_displacement_continuous()
                    stale_measurement = self._detect_stale_measurement(result)
                    if not math.isnan(result.displacement_um):
                        self._latest_valid_displacement_um = result.displacement_um
                    if result.spot_x_px is not None:
                        self._latest_spot_x_px = float(result.spot_x_px)

                    error_um = self._compute_error(result)
                    is_good = self._is_good_reading(result, error_um) and not stale_measurement

                    self._update_lock_state(is_good, error_um)

                    # Apply correction when locked or recovering
                    applied_correction_um = 0.0
                    if self._status in ("locked", "recovering"):
                        if not math.isnan(error_um):
                            # Good reading — update holdover state and apply correction
                            self._last_good_error_um = error_um
                            self._consecutive_nan_count = 0
                            correction = self._control_fn(error_um, period)
                            current_pos = self._piezo_service.get_position()
                            new_pos = self._clamp_to_range(current_pos + correction)
                            self._piezo_service.move_to_fast(new_pos)
                            applied_correction_um = new_pos - current_pos
                        elif self._consecutive_nan_count < self._config.max_nan_holdover_cycles:
                            # NaN holdover: use last-known-good with decaying gain
                            self._consecutive_nan_count += 1
                            decay = self._config.nan_holdover_decay ** self._consecutive_nan_count
                            # Don't update integral during holdover — save and restore
                            correction = self._control_fn(
                                self._last_good_error_um, period, update_integral=False
                            ) * decay
                            current_pos = self._piezo_service.get_position()
                            new_pos = self._clamp_to_range(current_pos + correction)
                            self._piezo_service.move_to_fast(new_pos)
                            applied_correction_um = new_pos - current_pos
                        else:
                            # Holdover expired — stop correcting, let recovery handle it
                            pass

                    # Piezo-settle guard: move_to_fast is non-blocking, so on
                    # a large correction the next measurement can sample the
                    # piezo mid-settle and drive ringing. Brief sleep after
                    # large moves only; small tracking corrections skip this
                    # so the loop stays fast when near the setpoint.
                    if (
                        abs(applied_correction_um)
                        > self._config.settle_correction_threshold_um
                        and self._config.settle_delay_s > 0.0
                    ):
                        time.sleep(self._config.settle_delay_s)

                    now = time.monotonic()
                    if now - last_metrics_time >= metrics_period:
                        self._publish_metrics(result, error_um, is_good)
                        last_metrics_time = now

                    self._check_warnings(result, error_um)

                elapsed = time.monotonic() - start

                # Record cycle time for the one-shot probe (excluding the
                # pacing sleep so the measurement reflects real work per cycle).
                if not probe_logged:
                    probe_samples.append(elapsed)
                    if len(probe_samples) >= probe_target:
                        samples_sorted = sorted(probe_samples)
                        median_s = samples_sorted[len(samples_sorted) // 2]
                        p95_s = samples_sorted[int(0.95 * len(samples_sorted))]
                        self._log.info(
                            "Focus lock cycle-time probe over %d iterations: "
                            "median=%.1f ms p95=%.1f ms (target period=%.1f ms @ %.0f Hz)",
                            probe_target,
                            median_s * 1000.0,
                            p95_s * 1000.0,
                            period * 1000.0,
                            self._config.loop_rate_hz,
                        )
                        probe_logged = True

                time.sleep(max(0.0, period - elapsed))
        except Exception:
            self._log.exception("Control loop crashed — failing safe to disabled")
        finally:
            with self._lock:
                self._running = False
                self._should_run = False
            # Release any pause() waiter: the loop has exited and will issue no
            # further corrections, so an unacknowledged pause is now satisfied.
            self._pause_ack_event.set()
            self._cleanup()
            self._set_status("disabled")

    def _compute_error(self, result: LaserAFResult) -> float:
        if math.isnan(result.displacement_um):
            return float("nan")
        return result.displacement_um - self._target_um

    def _is_good_reading(self, result: LaserAFResult, error_um: float) -> bool:
        if math.isnan(result.displacement_um):
            return False
        # During locked/recovering, "good" means the measurement is trustworthy
        # (displacement is a real number). Error magnitude itself is NOT a
        # gate — a large error is exactly what the PI loop is for, and gating
        # lock status on |error| breaks the lock on every legitimate manual
        # Z move before the piezo has a chance to correct. SNR/correlation
        # are intentionally NOT enforced in the per-frame check either: a
        # momentarily noisy spot is still more informative than none, and
        # sustained low-SNR runs are handled by the persistent-untrustworthy
        # guard in _update_lock_state (plus the warning system). The separate
        # persistent-large-error guard demotes only when |error| exceeds
        # lock_break_error_um for lock_break_persist_cycles.
        if self._status in ("locked", "recovering"):
            return not math.isnan(error_um)

        # Acquisition path (ready/lost/search setup): require stronger quality.
        if math.isnan(result.spot_snr) or result.spot_snr < self._config.min_spot_snr:
            return False
        threshold_um = self._config.acquire_threshold_um
        if math.isnan(error_um) or abs(error_um) > threshold_um:
            return False
        if result.correlation is not None:
            if math.isnan(result.correlation):
                return False
            threshold = self._laser_af.laser_af_properties.correlation_threshold
            if result.correlation < threshold:
                return False
        return True

    def _update_lock_state(self, is_good: bool, error_um: float) -> None:
        """Update lock state machine with recovery and search support."""
        if not self._lock_reference_active:
            self._lock_buffer_fill = 0
            self._consecutive_bad_count = 0
            self._persistent_large_error_count = 0
            self._persistent_untrustworthy_count = 0
            if self._status in ("locked", "recovering", "lost"):
                self._set_status("ready")
            return

        new_status: Optional[str] = None

        # Track persistent unrecoverable conditions independently of is_good,
        # so that the PI loop keeps correcting normal disturbances while only
        # genuinely-unrecoverable situations break the lock.
        trustworthy = not math.isnan(error_um)
        if trustworthy:
            self._persistent_untrustworthy_count = 0
            if abs(error_um) > self._config.lock_break_error_um:
                self._persistent_large_error_count += 1
            else:
                self._persistent_large_error_count = 0
        else:
            self._persistent_large_error_count = 0
            self._persistent_untrustworthy_count += 1

        untrustworthy_grace = (
            self._config.max_nan_holdover_cycles + self._config.lock_break_persist_cycles
        )

        if self._status == "locked":
            if is_good:
                # Trustworthy reading — the PI loop handles the correction
                # regardless of error magnitude. Stay locked.
                self._consecutive_bad_count = 0
                self._lock_buffer_fill = min(
                    self._lock_buffer_fill + 1, self._config.buffer_length
                )
                # Only demote if we've observed a persistent unrecoverable
                # error (piezo saturated / spot at extreme) for a full guard
                # window. A brief 10 µm manual move will not trigger this.
                if (
                    self._persistent_large_error_count
                    >= self._config.lock_break_persist_cycles
                ):
                    new_status = "recovering"
                    self._recovery_attempts_remaining = self._config.recovery_attempts
                    self._recovery_start_time = time.monotonic()
                    self._recovery_good_count = 0
                    self._consecutive_bad_count = 0
                    # NOTE: integral is deliberately NOT reset here. It has an
                    # anti-windup clamp and carries useful low-frequency drift
                    # information across the flicker into recovering.
            else:
                # Untrustworthy reading. Stay locked through short NaN/SNR
                # glitches (handled by the NaN-holdover correction path) and
                # only demote once the untrustworthy count crosses the guard.
                self._consecutive_bad_count += 1
                if self._persistent_untrustworthy_count >= untrustworthy_grace:
                    new_status = "recovering"
                    self._recovery_attempts_remaining = self._config.recovery_attempts
                    self._recovery_start_time = time.monotonic()
                    self._recovery_good_count = 0
                    self._consecutive_bad_count = 0

        elif self._status == "recovering":
            if is_good:
                # Good reading during recovery - count towards recovery
                self._recovery_good_count += 1
                self._consecutive_bad_count = 0
                if self._recovery_good_count >= self._config.recovery_window_readings:
                    # Successfully recovered!
                    new_status = "locked"
                    self._lock_buffer_fill = self._config.recovery_window_readings
            else:
                # Bad reading during recovery.
                self._recovery_good_count = 0
                self._consecutive_bad_count += 1
                # Exhaust on consecutive bad frames, not wall-clock. A single
                # good frame fully extends the recovery budget (reset above).
                exhaustion_budget = max(
                    1,
                    self._config.recovery_attempts
                    * max(1, self._config.recovery_window_readings),
                )
                if self._consecutive_bad_count >= exhaustion_budget:
                    # Truly can't recover — transition to lost/searching.
                    self._recovery_attempts_remaining = 0
                    self._integral_accumulator = 0.0  # only reset on actual loss
                    if self._auto_search_enabled:
                        self._start_search()
                        new_status = "searching"
                    else:
                        new_status = "lost"
                        self._lock_buffer_fill = 0

        elif self._status == "ready":
            # Building up to lock
            if is_good:
                self._lock_buffer_fill = min(
                    self._lock_buffer_fill + 1, self._config.buffer_length
                )
                if self._lock_buffer_fill >= self._config.buffer_length:
                    new_status = "locked"
                    self._locked_piezo_um = self._piezo_service.get_position()
            else:
                self._lock_buffer_fill = max(0, self._lock_buffer_fill - 1)

        elif self._status == "lost":
            # Try to recover from lost state
            if is_good:
                self._lock_buffer_fill += 1
                if self._lock_buffer_fill >= self._config.buffer_length:
                    new_status = "locked"
                    self._locked_piezo_um = self._piezo_service.get_position()
            else:
                self._lock_buffer_fill = 0

        if new_status is not None:
            self._set_status(new_status)

    def _start_search(self) -> None:
        """Start the piezo sweep search to re-find focus."""
        self._search_positions = self._build_search_positions()
        self._search_position_index = 0
        self._search_position = self._search_positions[0] if self._search_positions else self._locked_piezo_um
        self._search_phase = "last_position"
        self._search_candidate_confirmations = 0
        self._integral_accumulator = 0.0
        self._search_start_time = time.monotonic()

    def _get_search_bounds(self) -> tuple[float, float]:
        """Get the search bounds: ±search_range_um around last position, clamped to safe range."""
        min_um, max_um = self._piezo_service.get_range()
        # Safety clamp based on percentage of piezo range
        safe_min = min_um + (max_um - min_um) * self._config.search_min_percent / 100.0
        safe_max = min_um + (max_um - min_um) * self._config.search_max_percent / 100.0
        # Local search around last position
        local_min = self._locked_piezo_um - self._config.search_range_um
        local_max = self._locked_piezo_um + self._config.search_range_um
        # Clamp to safe range
        search_min = max(safe_min, local_min)
        search_max = min(safe_max, local_max)
        return search_min, search_max

    def _build_search_positions(self) -> list[float]:
        search_min, search_max = self._get_search_bounds()
        positions = [self._locked_piezo_um]
        step_index = 1
        while True:
            delta = step_index * self._config.search_step_um
            added = False
            low = self._locked_piezo_um - delta
            high = self._locked_piezo_um + delta
            if low >= search_min:
                positions.append(low)
                added = True
            if high <= search_max:
                positions.append(high)
                added = True
            if not added:
                break
            step_index += 1
        return positions

    def _search_target_tolerance_um(self) -> float:
        return max(
            self._config.maintain_threshold_um * 2.0,
            self._config.acquire_threshold_um * 3.0,
        )

    def _score_search_candidate(
        self,
        result: LaserAFResult,
        piezo_position_um: float,
    ) -> FocusLockCandidate:
        correlation = float("nan")
        if result.correlation is not None:
            correlation = float(result.correlation)
        target_error_um = (
            float("inf")
            if math.isnan(result.displacement_um)
            else abs(result.displacement_um - self._target_um)
        )
        is_valid = self._is_valid_lock_sample(result) and (
            target_error_um <= self._search_target_tolerance_um()
        )
        if math.isnan(correlation):
            correlation_score = 0.0
        else:
            correlation_score = correlation
        score = (
            result.spot_snr * 0.1
            + correlation_score
            - target_error_um * 10.0
        )
        return FocusLockCandidate(
            piezo_position_um=piezo_position_um,
            displacement_um=result.displacement_um,
            target_error_um=target_error_um,
            spot_snr=result.spot_snr,
            correlation=correlation,
            score=score,
            is_valid=is_valid,
        )

    def _search_step(self) -> None:
        """Perform one step of the piezo sweep search."""
        if self._status != "searching":
            return

        if self._search_start_time is not None:
            elapsed = time.monotonic() - self._search_start_time
            if elapsed >= self._config.search_timeout_s:
                self._log.warning("Focus search timed out after %.1fs", elapsed)
                self._piezo_service.move_to(self._locked_piezo_um)
                self._lock_buffer_fill = 0
                self._set_status("lost")
                return

        # Move piezo to current search position
        self._piezo_service.move_to(self._search_position)
        time.sleep(self._config.search_settle_ms / 1000.0)

        # Get a measurement and check if we found focus
        result = self._laser_af.measure_displacement_continuous()
        candidate = self._score_search_candidate(result, self._search_position)
        if candidate.is_valid:
            self._search_candidate_confirmations += 1
            if self._search_candidate_confirmations >= self._search_confirmation_readings:
                self._locked_piezo_um = self._search_position
                self._commit_lock_reference(self._target_um, result.spot_x_px)
            return
        else:
            self._search_candidate_confirmations = 0

        # Get search bounds
        search_min, search_max = self._get_search_bounds()

        # Publish search progress
        self._event_bus.publish(
            FocusLockSearchProgress(
                phase=self._search_phase,
                current_position_um=self._search_position,
                search_min_um=search_min,
                search_max_um=search_max,
            )
        )

        if self._search_phase == "last_position":
            # Last position didn't work, start local sweep
            self._search_phase = "sweep"
            self._search_position_index = 1
            if self._search_position_index < len(self._search_positions):
                self._search_position = self._search_positions[self._search_position_index]
        else:
            # Continue sweep
            self._search_position_index += 1
            if self._search_position_index < len(self._search_positions):
                self._search_position = self._search_positions[self._search_position_index]
            else:
                # Search failed — restore piezo to last known good position
                self._piezo_service.move_to(self._locked_piezo_um)
                self._lock_buffer_fill = 0
                self._set_status("lost")

    def _is_good_search_reading(self, result: LaserAFResult) -> bool:
        """Check if a laser AF result is good enough to establish lock during search.

        During search, we just need a valid spot with good SNR - we don't check against
        the old target or reference correlation since we're trying to find a new lock
        position around the last known good piezo position.
        """
        if math.isnan(result.displacement_um):
            return False
        if math.isnan(result.spot_snr) or result.spot_snr < self._config.min_spot_snr:
            return False
        return abs(result.displacement_um - self._target_um) <= self._search_target_tolerance_um()

    def _detect_stale_measurement(self, result: LaserAFResult) -> bool:
        spot_x = float("nan") if result.spot_x_px is None else float(result.spot_x_px)
        correlation = float("nan") if result.correlation is None else float(result.correlation)
        signature = (
            float(result.timestamp),
            float(result.displacement_um),
            spot_x,
            correlation,
        )
        previous = self._last_measurement_signature
        self._last_measurement_signature = signature
        if previous is None:
            self._stale_measurement_count = 1
            return False

        same_timestamp = signature[0] <= previous[0]
        same_measurement = (
            math.isclose(signature[1], previous[1], abs_tol=1e-9)
            and (
                (math.isnan(signature[2]) and math.isnan(previous[2]))
                or math.isclose(signature[2], previous[2], abs_tol=1e-9)
            )
            and (
                (math.isnan(signature[3]) and math.isnan(previous[3]))
                or math.isclose(signature[3], previous[3], abs_tol=1e-9)
            )
        )
        if same_timestamp or same_measurement:
            self._stale_measurement_count += 1
        else:
            self._stale_measurement_count = 1
        return self._stale_measurement_count >= self._stale_measurement_limit

    def _publish_metrics(self, result: LaserAFResult, error_um: float, is_good: bool) -> None:
        if not math.isnan(error_um):
            self._error_history.append(error_um)
            self._recent_error_window.append(error_um)
            self._drift_history.append((time.monotonic(), error_um))

        # Full-history RMS is retained as an event payload for logging. The
        # quality score below uses a *recent* window so it responds within a
        # fraction of a second after a disturbance clears.
        z_error_rms = float("nan")
        if self._error_history:
            z_error_rms = math.sqrt(
                sum(err * err for err in self._error_history) / len(self._error_history)
            )

        recent_rms = float("nan")
        if self._recent_error_window:
            recent_rms = math.sqrt(
                sum(err * err for err in self._recent_error_window)
                / len(self._recent_error_window)
            )

        drift_rate = 0.0
        if len(self._drift_history) >= 2:
            t0, e0 = self._drift_history[0]
            t1, e1 = self._drift_history[-1]
            if t1 > t0:
                drift_rate = (e1 - e0) / (t1 - t0)

        # Recent-window RMS-based quality (0-1, higher is better).
        quality_threshold = self._config.offset_threshold_um
        if not math.isnan(recent_rms):
            current_quality = max(0.0, 1.0 - recent_rms / quality_threshold)
        else:
            current_quality = 1.0

        # Only force quality to zero when lock is truly lost. During
        # "recovering" we trust the measurement-derived quality — the status
        # field already tells the UI we are recovering, so the numeric score
        # should reflect actual recent performance (which is what the user
        # wants to see climbing back within a few frames).
        status = self.status
        if status in ("lost", "searching"):
            current_quality = 0.0

        # Update smoothed quality with exponential moving average, but force
        # immediate drop when lock is explicitly lost/searching.
        if status in ("lost", "searching"):
            self._smoothed_quality = 0.0
        else:
            alpha = 0.35  # Responsive smoothing — ~3-frame time constant.
            self._smoothed_quality = alpha * current_quality + (1 - alpha) * self._smoothed_quality

        z_position = self._piezo_service.get_position()
        spot_offset_px = float("nan")
        if not math.isnan(self._locked_spot_x_px) and result.spot_x_px is not None:
            spot_offset_px = float(result.spot_x_px) - self._locked_spot_x_px
        piezo_delta_um = float("nan")
        if self._lock_reference_active:
            piezo_delta_um = z_position - self._locked_piezo_um

        self._event_bus.publish(
            FocusLockMetricsUpdated(
                z_error_um=error_um,
                z_position_um=z_position,
                spot_snr=result.spot_snr,
                spot_intensity=result.spot_intensity,
                z_error_rms_um=z_error_rms,
                drift_rate_um_per_s=drift_rate,
                is_good_reading=is_good,
                correlation=result.correlation if result.correlation is not None else float("nan"),
                spot_offset_px=spot_offset_px,
                piezo_delta_um=piezo_delta_um,
                lock_buffer_fill=self._lock_buffer_fill,
                lock_buffer_length=self._config.buffer_length,
                lock_quality=self._smoothed_quality,
            )
        )

        # Publish frame preview
        self._publish_frame(result)

    def _publish_frame(self, result: LaserAFResult) -> None:
        """Publish the AF camera frame and spot position for preview."""
        now = time.monotonic()
        if now - self._last_preview_publish_time < self._preview_publish_period_s:
            return

        frame = result.image
        if frame is None:
            return

        raw_spot_x = result.spot_x_px
        raw_spot_y = result.spot_y_px
        spot_valid = raw_spot_x is not None and raw_spot_y is not None
        if spot_valid and raw_spot_x is not None and raw_spot_y is not None:
            spot_x = float(raw_spot_x)
            spot_y = float(raw_spot_y)
        else:
            # Use center if no spot detected (still show frame, but no marker)
            h, w = frame.shape[:2]
            spot_x = w / 2.0
            spot_y = h / 2.0
            spot_valid = False

        self._last_preview_publish_time = now
        frame_h, frame_w = frame.shape[:2]
        self._event_bus.publish(
            FocusLockFrameUpdated(
                frame=frame,
                spot_x_px=spot_x,
                spot_y_px=spot_y,
                frame_width=frame_w,
                frame_height=frame_h,
                spot_valid=spot_valid,
            )
        )

    def _check_warnings(self, result: LaserAFResult, error_um: float) -> None:
        min_um, max_um = self._piezo_service.get_range()
        position = self._piezo_service.get_position()
        critical_margin = self._config.piezo_critical_margin_um
        warning_margin = self._config.piezo_warning_margin_um

        # Two-tier piezo limit warnings: critical (inner) fires before warning (outer)
        if position <= min_um + critical_margin:
            self._publish_critical_warning("low", position, min_um, critical_margin)
            self._publish_warning("piezo_low", "Piezo approaching lower limit")
        elif position >= max_um - critical_margin:
            self._publish_critical_warning("high", position, max_um, critical_margin)
            self._publish_warning("piezo_high", "Piezo approaching upper limit")
        elif position <= min_um + warning_margin:
            self._publish_warning("piezo_low", "Piezo approaching lower limit")
        elif position >= max_um - warning_margin:
            self._publish_warning("piezo_high", "Piezo approaching upper limit")

        if self._status == "lost":
            self._publish_warning("signal_lost", "Focus lock signal lost")
        if math.isnan(result.spot_snr) or result.spot_snr < self._config.min_spot_snr:
            self._publish_warning("snr_low", "Spot SNR below threshold")
        if self._stale_measurement_count >= self._stale_measurement_limit:
            self._publish_warning("measurement_stale", "Focus lock measurements appear stale")

    def _publish_warning(self, warning_type: str, message: str) -> None:
        now = time.monotonic()
        last_time = self._warning_last_time.get(warning_type)
        if last_time is not None and now - last_time < self._warning_debounce_s:
            return
        self._warning_last_time[warning_type] = now
        self._event_bus.publish(FocusLockWarning(warning_type=warning_type, message=message))

    def _publish_critical_warning(
        self, direction: str, position_um: float, limit_um: float, margin_um: float
    ) -> None:
        key = f"piezo_critical_{direction}"
        now = time.monotonic()
        last_time = self._warning_last_time.get(key)
        if last_time is not None and now - last_time < self._warning_debounce_s:
            return
        self._warning_last_time[key] = now
        self._event_bus.publish(
            FocusLockPiezoLimitCritical(
                direction=direction,
                position_um=position_um,
                limit_um=limit_um,
                margin_um=margin_um,
            )
        )

    def _clamp_to_range(self, position_um: float) -> float:
        min_um, max_um = self._piezo_service.get_range()
        return max(min_um, min(max_um, position_um))

    def _reset_lock_state(self) -> None:
        self._lock_reference_active = False
        self._lock_buffer_fill = 0
        self._error_history.clear()
        self._recent_error_window.clear()
        self._drift_history.clear()
        self._smoothed_quality = 1.0
        self._warning_last_time.clear()
        # Reset recovery state
        self._recovery_attempts_remaining = 0
        self._recovery_start_time = None
        self._recovery_good_count = 0
        self._consecutive_bad_count = 0
        self._persistent_large_error_count = 0
        self._persistent_untrustworthy_count = 0
        # Reset search state
        self._search_phase = ""
        self._search_position = 0.0
        self._search_positions = []
        self._search_position_index = 0
        self._search_candidate_confirmations = 0
        self._search_start_time = None
        # Reset PI controller state
        self._integral_accumulator = 0.0
        # Reset NaN holdover state
        self._last_good_error_um = 0.0
        self._consecutive_nan_count = 0
        self._latest_valid_displacement_um = float("nan")
        self._locked_spot_x_px = float("nan")
        self._latest_spot_x_px = float("nan")
        self._last_measurement_signature = None
        self._stale_measurement_count = 0
        self._last_published_status = None

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status
            if status == self._last_published_status:
                return
            self._last_published_status = status
            buffer_fill = self._lock_buffer_fill
            buffer_length = self._config.buffer_length
        self._event_bus.publish(
            FocusLockStatusChanged(
                is_locked=status == "locked",
                status=status,
                lock_buffer_fill=buffer_fill,
                lock_buffer_length=buffer_length,
            )
        )

    def _turn_on_laser(self) -> None:
        if not self._laser_on:
            self._laser_af.turn_on_laser(bypass_mode_gate=True)
            self._laser_on = True

    def _turn_off_laser(self) -> None:
        if self._laser_on:
            self._laser_af.turn_off_laser(bypass_mode_gate=True)
            self._laser_on = False

    def _cleanup(self) -> None:
        try:
            if self._laser_af._camera_service.get_is_streaming():
                self._laser_af._camera_service.stop_streaming()
        except Exception:
            self._log.exception("Failed to stop focus camera streaming during cleanup")
        self._turn_off_laser()

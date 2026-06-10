# Squid Laser Engine (603c0ad3)

**Status:** COMPLETED (all parts done)
**Started:** 2026-06-09
**Completed:** 2026-06-10
**Commits:** 6df218f8 (driver), 5c8bb3ae (service+widget+addons), 4da055b6 (gate),
c0d4eab3 (acquisition modal), 41f410bc (live-mode warn)

Port upstream #536 — Cephla Squid laser engine (USB-serial Teensy, 5 laser
channels / 6 TCM modules). Re-architected to arch_v2's EventBus model (upstream
is Qt-signal coupled).

## Upstream Commit
- [ ] `603c0ad3` - feat: Squid laser engine — status polling, GUI tab, acquisition gating (#536)

## Part 1 — Driver foundation (DONE, 6df218f8)
- [x] `backend/drivers/lighting/squid_laser_engine.py` — full driver decoupled
      from Qt: base class delivers status/connection-loss via callbacks
      (`set_on_status`/`set_on_connection_lost`) instead of Qt signals. Parsing,
      packet building, threads, `wait_until_ready`, simulation twin unchanged.
- [x] `_def`: `USE_SQUID_LASER_ENGINE = False`, `SQUID_LASER_ENGINE_SN = None`.
- [x] Tests: 41 (full upstream suite, 3 Qt-signal tests → callback assertions).

## Part 2 — Service + UI + addons (DONE, 5c8bb3ae)
- [x] `core/events.py`: `LaserEngineStatusUpdated`, `LaserEngineConnectionLost`
      (state), `WakeAllLasersCommand`/`SleepAllLasersCommand` (UI cmds),
      `LaserEngineWaitingForReady`/`LaserEngineReady` (gate events for part 3).
- [x] `backend/services/laser_engine_service.py`: wraps the driver, callbacks →
      EventBus, wake/sleep command handlers, thread-safe readiness API.
- [x] `ui/widgets/hardware/laser_engine.py`: "Laser Engine" tab (EventBus
      subscriptions; per-channel state/temp/ΔT/TTL; summary dot; disconnect
      banner; Wake-All/Sleep-All publish commands). Tab between Piezo and NL5.
- [x] Microscope addons: factory builds driver+service when `USE_SQUID_LASER_ENGINE`
      (uses global event_bus); `start()` + `wake_up_all()` in prepare_for_use;
      `close()` in Microscope.close.
- [x] Widget wiring in widget_factory/main_window.
- [x] Tests: service (6) + widget (4); full suite 51 green; app integration green.

## Part 3a — Acquisition gate (DONE, 4da055b6)
- [x] **DI plumbing:** `laser_engine` added to `AcquisitionServices`
      (+ `has_laser_engine`) and `AcquisitionDependencies.create`; threaded
      through `MultiPointController` → `MultiPointWorker` (both dependencies and
      direct-kwarg paths); `application.py` registers the engine addon as the
      `"laser_engine"` service and passes it to the controller.
- [x] MultiPointWorker gate: `_compute_laser_channels_needed()` (from
      `selected_configurations` × `_illumination_config`) +
      `_wait_for_laser_engine()` per timepoint after the abort check
      (`cancel_fn = self._abort_requested.is_set` → Abort button cancels);
      publishes `LaserEngineWaitingForReady`/`LaserEngineReady`; raises on
      timeout/disconnect (caught → clean abort). No-op when the flag is off.
- [x] Tests: `test_multipoint_laser_gate.py` (7) — ready short-circuit,
      wait-then-ready event order, timeout/disconnect raise, abort exits,
      channel computation. Multipoint + app integration green.

## Part 3b — Acquisition modal (DONE, c0d4eab3)
- [x] Main window subscribes (auto_subscribe/@handles, GUI-thread marshaled) to
      `LaserEngineWaitingForReady`/`LaserEngineReady` → modeless non-cancelable
      `QProgressDialog` shown/hidden; Abort button cancels the wait.

## Part 3c — LiveController warn (DONE, 41f410bc)
- [x] `LaserEngineWarning` event. LiveController takes `laser_engine_service` +
      `channel_config_service`; `_get_illumination_wavelength()` resolves via
      `channel_config_service._illumination_config`; `_check_laser_engine_warn_only()`
      at `start_live` wakes the channel + publishes the warning (never blocks).
      Wired in application.py (main LiveController). Widget shows a transient amber
      notice. 4 warn tests + 1 widget test.

## Summary
COMPLETE — full port of #536, EventBus-architected. Built (flag-gated), visible
(status tab), controllable (wake/sleep), woken at startup, acquisition gates on
readiness (abortable + modal), and live-mode warns. 63 laser-engine tests
(driver 41, service 6, widget 5, gate 7, live-warn 4). Hardware/serial path
untested (no engine).

## Notes
- Driver callbacks fire on the engine's background thread; the service must
  publish to the EventBus in a thread-safe way (UIEventBus marshals to GUI).
- Hardware (serial) path untestable here; simulation + service + gate logic are
  testable. Acquisition gating is the sensitive piece — needs dedicated tests
  (ready short-circuit, wait-then-ready, timeout/disconnect raises, abort).

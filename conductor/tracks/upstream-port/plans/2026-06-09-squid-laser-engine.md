# Squid Laser Engine (603c0ad3)

**Status:** IN-PROGRESS (Part 1 of 2 done in 6df218f8)
**Started:** 2026-06-09

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

## Part 2 — Service + UI + integration (TODO)
- [ ] `core/events.py`: `LaserEngineStatusUpdated`, `LaserEngineConnectionLost`
      (state events), `WakeAllLasersCommand` / `SleepAllLasersCommand` (UI cmds),
      plus laser-engine-waiting/ready gate events for the acquisition modal.
- [ ] `backend/services/laser_engine_service.py`: wrap the driver, set its
      callbacks to publish the EventBus status/connection events; expose
      wake/sleep/wait_until_ready/get_latest_status thread-safely; handle the
      wake/sleep commands.
- [ ] `ui/widgets/hardware/laser_engine.py`: "Laser Engine" tab subscribing to
      the status/connection events; per-channel state/temp/ΔT/TTL; Wake-All /
      Sleep-All buttons; last-update indicator + disconnect banner.
- [ ] Microscope addons: build engine + service when `USE_SQUID_LASER_ENGINE`;
      `start()` + non-blocking `wake_up_all()` in prepare_for_use; idempotent close.
- [ ] LiveController: warn-only readiness check at `start_live` (publish a warning
      event; arch_v2 has no Qt `signal_warning`).
- [ ] MultiPointWorker: per-timepoint gate — block on `wait_until_ready` for the
      channels the acquisition will fire; surface a cancelable modal via events;
      re-check each timepoint. **Acquisition-critical — port carefully + test.**
- [ ] Widget wiring in widget_factory/main_window.

## Notes
- Driver callbacks fire on the engine's background thread; the service must
  publish to the EventBus in a thread-safe way (UIEventBus marshals to GUI).
- Hardware (serial) path untestable here; simulation + service + gate logic are
  testable. Acquisition gating is the sensitive piece — needs dedicated tests
  (ready short-circuit, wait-then-ready, timeout/disconnect raises, abort).

# Squid Laser Engine (603c0ad3)

**Status:** IN-PROGRESS (Parts 1 & 2 done; Part 3 = acquisition gate + live warn)
**Started:** 2026-06-09
**Commits:** 6df218f8 (driver), 5c8bb3ae (service + widget + addons)

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

## Part 3 — Acquisition gate + live warn (TODO)
- [ ] **DI plumbing:** the arch_v2 MultiPointWorker takes services via
      `AcquisitionDependencies`, not a microscope ref. Add `laser_engine` to the
      services dependency set and thread it through dependency construction so the
      worker can reach `LaserEngineService`.
- [ ] MultiPointWorker: cache the service + compute needed channels from
      `selected_configurations` × `channelConfigurationManager._illumination_config`
      (`cfg.get_illumination_wavelength(...)` → `channel_keys_for_wavelengths`).
      Per-timepoint (after the abort check in `run()`): if needed channels,
      `wait_until_ready(cancel_fn=self._abort_requested.is_set)`; publish
      `LaserEngineWaitingForReady`/`LaserEngineReady` around it (UI shows a
      cancelable modal); raise on timeout/disconnect. **Acquisition-critical —
      dedicated tests: ready short-circuit, wait-then-ready, timeout/disconnect
      raises, abort exits.**
- [ ] LiveController: warn-only readiness check at `start_live` — needs the
      LaserEngineService threaded into LiveController; publish a warning event
      (arch_v2 has no Qt `signal_warning`). Wake the needed channel; don't block.
- [ ] UI: subscribe to the gate events → show/hide a cancelable "laser warming
      up" modal during acquisition.

Recommend a focused session for Part 3 given the acquisition-critical gate +
the DI plumbing.

## Notes
- Driver callbacks fire on the engine's background thread; the service must
  publish to the EventBus in a thread-safe way (UIEventBus marshals to GUI).
- Hardware (serial) path untestable here; simulation + service + gate logic are
  testable. Acquisition gating is the sensitive piece — needs dedicated tests
  (ready short-circuit, wait-then-ready, timeout/disconnect raises, abort).

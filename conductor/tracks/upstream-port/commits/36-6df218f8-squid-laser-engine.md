# Squid Laser Engine (full port, EventBus-architected)

**Our Commits:** 6df218f8, 5c8bb3ae, 4da055b6, c0d4eab3, 41f410bc
**Date:** 2026-06-09 / 2026-06-10
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 603c0ad3 | feat: Squid laser engine — status polling, GUI tab, acquisition gating (#536) |

## Summary

Full port of the Cephla Squid laser engine (USB-serial Teensy controller; 5
wake/sleep laser channels over 6 TCM modules), **re-architected onto arch_v2's
EventBus model** — upstream's driver is a QObject emitting Qt signals; arch_v2
keeps backend Qt-free, so status flows driver → callback → service → EventBus →
UI. Landed in five commits.

## Commits

- **6df218f8 (part 1):** `backend/drivers/lighting/squid_laser_engine.py` — driver
  + `SquidLaserEngine_Simulation`, decoupled from Qt (base class delivers status /
  connection-loss via `set_on_status` / `set_on_connection_lost` callbacks).
  Protocol unchanged: CRC32-framed status parse, command packets, query+receive
  threads, `wait_until_ready`. `_def` flags `USE_SQUID_LASER_ENGINE`,
  `SQUID_LASER_ENGINE_SN`. 41 tests (full upstream suite; 3 Qt-signal tests →
  callbacks).
- **5c8bb3ae (part 2):** events (`LaserEngineStatusUpdated`,
  `LaserEngineConnectionLost`, `WakeAllLasersCommand`, `SleepAllLasersCommand`,
  `LaserEngineWaitingForReady`, `LaserEngineReady`); `LaserEngineService` (driver
  callbacks → events, wake/sleep command handlers, thread-safe readiness API);
  `ui/widgets/hardware/laser_engine.py` "Laser Engine" tab (between Piezo and NL5);
  microscope-addons build/start/close via the factory (global event_bus). 10 tests.
- **4da055b6 (part 3a):** acquisition gate — `laser_engine` threaded through
  `AcquisitionServices`/`AcquisitionDependencies` → `MultiPointController` →
  `MultiPointWorker`; `application.py` registers the engine addon as the
  `"laser_engine"` service. Per-timepoint `_wait_for_laser_engine()` gate
  (abort-cancelable; raises on timeout/disconnect). 7 tests.
- **c0d4eab3 (part 3b):** main window subscribes to the gate events → modeless
  `QProgressDialog` (Abort cancels the wait).
- **41f410bc (part 3c):** `LaserEngineWarning` event; LiveController warn-only
  check at `start_live` (resolves the channel wavelength via
  `channel_config_service._illumination_config`, wakes the laser, publishes the
  warning — never blocks); widget shows a transient amber notice. 5 tests.

## Architecture Adaptations

- **Qt decoupling:** the upstream QObject-driver's signals become callbacks; the
  `LaserEngineService` bridges them to EventBus state events (UIEventBus marshals
  to the GUI thread).
- **DI:** upstream accesses the engine via `microscope.addons`; arch_v2's
  MultiPointWorker takes services via `AcquisitionDependencies`, so the engine
  (as a `LaserEngineService`) is threaded through the dependency set. The addon
  IS the service (built in the factory where the event_bus is available).
- **Live warn:** upstream uses `microscope.config_repo` + a Qt `signal_warning`;
  arch_v2 uses `channel_config_service._illumination_config` + a
  `LaserEngineWarning` EventBus event (mirrors `FocusLockWarning`).

## Tests

63 total: driver 41, service 6, widget 5, gate 7, live-warn 4. Multipoint +
application integration green. Hardware (serial) path untestable here; the
simulation, service, gate, and warn logic are all covered.

## Audit

- [x] Logic matches upstream (all of #536)
- [x] arch_v2 EventBus/service/DI patterns followed (Qt-free backend)
- [x] Tests added (63) + integration green
- [x] black-clean (added lines / new files)
- [ ] Hardware validation (needs a Squid laser engine; serial path untested here)

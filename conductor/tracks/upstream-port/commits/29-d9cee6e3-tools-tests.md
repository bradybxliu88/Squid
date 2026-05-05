# MCU Stress Test Tool + Single-Instance Lock

**Our Commit:** d9cee6e3
**Date:** 2026-05-05
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| e4fcfe68 | feat: add MCU triggering stress test and timeout counter tools (#525) |
| d556a42c | feat: enforce single Squid GUI instance via QLockFile (#534) |

## Upstream Commits Skipped

| Hash | Title | Reason |
|------|-------|--------|
| 3b24b637 | fix: align zarr writer test helper with current acquisition schema (#531) | already-fixed |

## Summary

`microcontroller_triggering_stress_test.py` is a standalone tool that
replays the MCU command pattern observed in `main_hcs.log` during multi-channel
HCS acquisition: cycling `set_illumination()` through D1–D5 (405/488/561/638/730 nm)
followed by `send_hardware_trigger(control_illumination=True, illumination_on_time_us=100_000)`,
with `wait_till_operation_is_completed()` after each. It tracks per-command
latency (min/avg/p50/p95/p99/max), resends and aborts (sniffed from logs),
wait timeouts, and prints a structured report. Exit code is non-zero if any
failures were observed.

Single-instance enforcement uses `QLockFile`. `acquire_single_instance_lock()`
returns a `LockAcquireResult(lock, path, busy)`. If `lock` is None and `busy`
is True, another instance is running. The lock embeds the holding process's
PID so it auto-recovers from crashes. The GUI shows a `QMessageBox.critical`
dialog with the lock path and exits with status 1 if blocked. The check is
skipped when `--skip-init` is passed, since that path is the legitimate
restart-after-settings-change flow which intentionally launches a new process.

## Files Created/Modified

### Created
- `software/tools/microcontroller_triggering_stress_test.py` (358 lines) — MCU stress test tool
- `software/src/squid/core/utils/single_instance.py` (61 lines) — `acquire_single_instance_lock()` helper
- `software/tests/unit/squid/core/utils/test_single_instance.py` (92 lines) — 4 tests
- `conductor/tracks/upstream-port/plans/2026-05-05-tools-tests.md`

### Modified
- `software/src/main_hcs.py` — moved QApplication construction earlier, added single-instance check before logging

## Architecture Adaptations

- **Stress test imports**: `control.microcontroller` → `squid.backend.microcontroller`, `squid.logging` → `squid.core.logging`, `from control._def import ILLUMINATION_CODE` → `from _def import ILLUMINATION_CODE`. Logger name `squid.Microcontroller` matches both.
- **Single-instance helper location**: Upstream put it under `control/`. arch_v2 puts it in `squid.core.utils.single_instance` since it's a low-level utility with no dependencies on backend or UI.
- **--skip-init bypass**: arch_v2 has a `--skip-init` flag for restart-after-settings-change (config.py:1703). Lock check is skipped on that path so the new process can start while the parent is still finishing shutdown.

## Tests

`tests/unit/squid/core/utils/test_single_instance.py` — 4 tests, all pass:
- `test_acquire_returns_lock_and_path`
- `test_second_acquire_in_same_process_is_busy`
- `test_release_allows_subsequent_acquire`
- `test_stale_lock_from_dead_process_is_reclaimed`

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] 4 unit tests added (all pass)
- [x] --skip-init bypass for restart flow
- [x] Skip 3b24b637 documented (already-fixed in arch_v2)

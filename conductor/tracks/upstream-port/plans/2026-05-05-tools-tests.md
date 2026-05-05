# Tools and Tests

**Status:** IN-PROGRESS
**Started:** 2026-05-05

## Upstream Commits

- [ ] `e4fcfe68` - feat: add MCU triggering stress test and timeout counter tools (#525)
- [ ] `3b24b637` - fix: align zarr writer test helper with current acquisition schema (#531) [SKIP — already-fixed]
- [ ] `d556a42c` - feat: enforce single Squid GUI instance via QLockFile (#534)

## Implementation Checklist

### Phase 1: 3b24b637 — Skip
arch_v2's `tests/unit/test_zarr_writer.py:make_test_capture_info` already uses
the current schema (`illumination_channel="LED"`, `intensity=50.0` as float,
`camera_settings=CameraSettings(...)` as a single object). Mark as already-fixed.

- [ ] Mark 3b24b637 skipped — already-fixed

### Phase 2: e4fcfe68 — MCU triggering stress test
- [ ] Create `software/tools/microcontroller_triggering_stress_test.py`
  - Adapt imports: `control.microcontroller` → `squid.backend.microcontroller`, `squid.logging` → `squid.core.logging`, `control._def` → `_def`
  - Logger name: `squid.Microcontroller` is already the arch_v2 logger name (matching upstream)

### Phase 3: d556a42c — Single instance lock
- [ ] Create `software/src/squid/core/utils/single_instance.py` with `acquire_single_instance_lock()` returning `LockAcquireResult(lock, path, busy)`
- [ ] Modify `software/src/main_hcs.py`:
  - Move `QApplication` construction before file logging setup
  - Call `acquire_single_instance_lock()` early; if busy, show QMessageBox.critical and `sys.exit(1)`
  - Connect `app.aboutToQuit.connect(instance_lock.unlock)` for clean release
- [ ] Create `software/tests/unit/squid/core/utils/test_single_instance.py` with 4 tests

### Tests
- [ ] Run single_instance unit tests
- [ ] Verify tools can be imported
- [ ] Black formatting

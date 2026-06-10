# RecordingWidget Fix + ImageSaver Metadata + Toupcam FPS

**Our Commit:** 4658668d
**Date:** 2026-06-09
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| a894eda5 | fix: legacy RecordingWidget + Toupcam continuous-mode FPS + Live illumination (#535) |

## Summary

Port the three independent concerns of upstream #535 to arch_v2. The headline
fix is that **recording was broken in arch_v2** — the Record button always
short-circuited with "Please choose base saving directory first".

## Files Created/Modified

### Created
- `software/tests/unit/squid/backend/io/{__init__.py,test_utils_acquisition.py}`
- `software/tests/unit/squid/ui/test_image_saver.py`

### Modified
- `software/src/_def.py` — `Acquisition.IMAGE_FORMAT` `"bmp"` → `"tiff"` (source
  default; INI `[ACQUISITION] image_format` overrides at module load).
- `software/src/squid/backend/io/utils_acquisition.py` — `get_image_filepath`
  channel-name sanitization (`_UNSAFE_CHANNEL_CHARS` regex; was bare
  `replace(" ", "_")`).
- `software/src/squid/ui/image_saver.py` — ImageSaver rebuild: `get_image_filepath`
  naming, `cv2.imwrite` for all dtypes, `frames.csv` sidecar, `channel_provider`,
  drop-on-full + OSError auto-stop logging, per-recording diagnostics,
  `stop_experiment()` lifecycle, `_DefaultRecordingChannel`. Drops the
  `image_format` ctor arg.
- `software/src/squid/ui/widgets/camera/recording.py` — RecordingWidget: remove
  the `base_path_is_set` gate (the bug), guard `set_saving_dir` against cancel,
  add `channel_provider`, call `stop_experiment()` + clear provider on stop.
- `software/src/squid/ui/gui/widget_factory.py` — wire
  `channel_provider=lambda: gui.liveController.currentConfiguration`.
- `software/src/squid/backend/controllers/live_controller.py` — turn on
  illumination in `start_live` CONTINUOUS mode (symmetric with `_stop_live`'s
  unconditional off).
- `software/src/squid/backend/drivers/cameras/toupcam.py` — `put_AutoExpoEnable(False)`
  in `_configure_camera` (unblocks DDR buffering → fixes ~2 fps continuous mode);
  MAX_PRECISE_FRAMERATE read/put made non-fatal with sensor-floor fallback.

## Architecture Adaptations

- Upstream's monolithic `control/core/core.py ImageSaver` maps to arch_v2's
  already-extracted `ui/image_saver.py` (which was the pre-fix version + cv2);
  the rebuild applied directly. `get_image_filepath` is imported from
  `squid.backend.io.utils_acquisition` (a pure helper).
- Upstream's `control/core/live_controller.py` continuous-illumination branch
  maps to arch_v2's state-machine `start_live`; `_stop_live` already turns off
  illumination unconditionally when `control_illumination`, so the add is balanced.
- **Deliberate omission:** upstream's per-frame timing diagnostics +
  `_log_startup_option_dump` (Toupcam) were NOT ported — pure debug
  instrumentation that adds untestable hot-path overhead, and the option dump
  references SKU-specific `TOUPCAM_OPTION_*` constants that risk AttributeError
  at construction on arch_v2's binding. The FPS fix itself is complete.

## Tests

- `tests/unit/squid/backend/io/test_utils_acquisition.py` (7) — channel-name
  sanitization (spaces + path-unsafe chars), uint16→tiff, uint8→runtime
  IMAGE_FORMAT, and the `"tiff"` source default.
- `tests/unit/squid/ui/test_image_saver.py` (4) — frames.csv contents +
  channel-named files, default-`live` channel fallback, idempotent
  `stop_experiment`, counter reset across experiments.

Verification: 11 new tests pass; `tests/integration/squid/test_application.py`
(6) green — exercises the full startup wiring (ImageSaver, live_controller,
RecordingWidget-with-channel_provider). Toupcam FPS path untestable without the
SDK (no `put_AutoExpoEnable` hardware); changes compile + are black-clean.

## Audit

- [x] Logic matches upstream (3 concerns)
- [x] arch_v2 patterns followed
- [x] Tests added (11) + integration green
- [x] black-clean (added lines)
- [x] Deliberate omission documented (Toupcam debug instrumentation)
- [ ] Toupcam FPS hardware validation (needs a Toupcam camera)

# May 2026 Misc Fixes

**Our Commit:** ec83f777
**Date:** 2026-05-05
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 36dc1444 | fix: LaserAFConfig defaults from _def.py & status bar height stability (#517) |
| 30d95ea1 | fix: suppress Pydantic serializer int/float warning in LaserAFConfig (#523) |
| cc677e2c | fix: handle illumination sources without a hardware source code (#518) |
| 6e3f731a | fix: fix logging format error and noisy backpressure warning (#512) |

## Summary

Four small upstream fixes ported as a single bundle.

`LaserAFConfig` defaults now reference `_def.py` constants via `default_factory`,
fixing divergence — `correlation_threshold` now defaults to 0.7 (was 0.9),
`spot_detection_mode` to `DUAL_LEFT` (was `DUAL_RIGHT`), and so on. The
WarningErrorWidget message label gets a fixed height and collapses newlines,
preventing the status bar from growing when error messages contain newlines.
The `MULTI_SECOND_RIGHT` spot mode is now hidden from the combo box.

The Pydantic int/float serializer warnings during laser autofocus initialization
are silenced by passing `warnings=False` to the relevant `model_dump()` calls.

`AcquisitionChannel.get_illumination_source_code()` now returns `-1` (was 0)
when a channel exists with no hardware source code, aligning the API contract
with upstream. The companion `_is_led_matrix()` change in upstream's
LiveController doesn't apply — arch_v2 dispatches LED matrix via
`ChannelType.LED_MATRIX` enum, not by checking `source < 10`.

The microcontroller's `log.warning()` for `last_command_aborted_error` now has
a `%s` placeholder, fixing the `TypeError: not all arguments converted during
string formatting` on every aborted command. The companion
`BackpressureMonitorWidget` change in upstream doesn't apply — arch_v2's widget
is push-based via `update_stats()` and doesn't have the `get_lock()` race.

## Files Created/Modified

### Created
- `conductor/tracks/upstream-port/plans/2026-05-05-misc-fixes.md`

### Modified
- `software/src/squid/core/config/models/laser_af_config.py` — all defaults now use `default_factory=lambda: _def.<CONST>`
- `software/src/squid/core/config/models/acquisition_config.py` — `get_illumination_source_code` returns -1 when channel has no source code
- `software/src/squid/core/config/repository.py` — `_save_yaml` passes `warnings=False` to `model_dump()`
- `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py` — two `model_dump()` calls pass `warnings=False`
- `software/src/squid/backend/microcontroller.py` — `log.warning` for aborted command now has `%s` placeholder
- `software/src/squid/ui/widgets/hardware/laser_autofocus.py` — hides `MULTI_SECOND_RIGHT` from spot mode combo
- `software/src/squid/ui/widgets/warning_error_widget.py` — fixed height on message label, collapse newlines

## Architecture Adaptations

- **LED matrix dispatch**: Upstream's `_is_led_matrix()` check by source code doesn't exist in arch_v2 (uses `ChannelType.LED_MATRIX` enum). Only the model API contract change applied.
- **Backpressure widget**: Upstream's polling-based widget with `get_lock()` calls and Qt timer doesn't exist in arch_v2 (push-based via `update_stats()`). No race to fix.

## Tests

Ran:
- `tests/unit/squid/core/config/` — 65 passed
- `tests/unit/test_warning_error_widget.py` — 22 passed
- `tests/unit/squid/backend/controllers/`, `tests/unit/squid/backend/managers/`, `tests/unit/squid/backend/processing/` — 400 passed (1 pre-existing geometry test failure deselected, unrelated)

Verified `LaserAFConfig()` default values now resolve from `_def.py` (e.g.
`correlation_threshold=0.7`, `spot_detection_mode=DUAL_LEFT`).

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Tests added (existing tests verify config + widget behavior)
- [x] Skip portions documented

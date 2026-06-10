# Config Token + Small Fixes + Logging

**Our Commit:** a8039815
**Date:** 2026-06-09
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| fae3aa0a | config: migrate filter_controller_enable to USE_EMISSION_FILTER_WHEEL (#543) |
| 345f1249 | feat: allow negative FOV overlap to introduce spacing between tiles (#555) |
| 6713d854 | log: enable per-acquisition log by default (#547) |
| 24bb38b1 | fix: clear line-profiler preview when toggled off mid-drawing (#544) |
| 0771c850 | log: add filter wheel position diagnostics (#545) |

## Summary

Batch port of five low-risk upstream commits — a config-token rename, two
small UI/behavior fixes, a default-flag flip, and filter-wheel diagnostic
logging. Each was reimplemented against arch_v2 patterns rather than patched.
Two related commits were skipped (see Skips) and the upstream "untrack machine
configs" hygiene bundle was deferred.

## Files Created/Modified

### Created
- `software/tests/unit/squid/config/test_emission_filter_wheel_config_token.py`
  (66 lines) — guards the token rename: scans shipped configs for the live
  `USE_EMISSION_FILTER_WHEEL` token and the absence of the dead one.

### Modified
- `software/configurations/configuration_{HCS_v2,Squid+,Squid+_Cicero_LDI,Squid+_H117_ORCA_Andor,Squid+_H117_ORCA_LDI_Fluidics,Squid+_Kinetix_LDI_XLight_Xeryon,Squid+_Tucsen}.ini`
  — replaced `filter_controller_enable` / `_filter_controller_enable_options`
  with `USE_EMISSION_FILTER_WHEEL = False` + `EMISSION_FILTER_WHEEL_TYPE = "SQUID"`.
  (`configuration_Squid+_Tucsen_Libra.ini` carries the same token but is
  gitignored/untracked under arch_v2's `software/.gitignore: *.ini`; its local
  copy was updated for consistency but is not committed.)
- `software/src/_def.py` — `ENABLE_PER_ACQUISITION_LOG = True`.
- `software/src/squid/core/config/feature_flags.py` — registration default
  for `ENABLE_PER_ACQUISITION_LOG` → True.
- `software/src/squid/ui/widgets/acquisition/flexible_multipoint.py`,
  `wellplate_multipoint.py`, `acquisition_setup.py` — overlap spinboxes
  `setRange(-1000, 99)` + tooltip (6 spinboxes total).
- `software/src/squid/ui/widgets/display/image_display.py` — line-profiler
  toggle-off cleanup + `_active_view()` / `_remove_line_preview_items()`
  helpers + `sigRangeChanged` connect moved to `__init__`.
- `software/src/squid/backend/drivers/filter_wheels/cephla.py` — diagnostic
  logs in `initialize`, `_configure_wheel`, `_move_to_position`, `home`.
- `software/tests/unit/squid/core/config/test_feature_flags.py` — added
  `test_per_acquisition_log_enabled_by_default`.

## Architecture Adaptations

- **fae3aa0a:** arch_v2 already reads `USE_EMISSION_FILTER_WHEEL` /
  `EMISSION_FILTER_WHEEL_TYPE` from `_def` (`squid/core/config/__init__.py`);
  the `.ini` token `filter_controller_enable` was inert. `_def`'s ConfigParser
  loader matches the lowercased option name, so the rename makes the `.ini`
  actually drive the feature. The `"SQUID"` value is JSON-parsed by
  `conf_attribute_reader` to the bare string `SQUID`.
- **345f1249:** arch_v2's `acquisition_yaml.py overlap_percent: float = 10.0`
  has no `ge/le` bound, so unlike upstream no pydantic/API change was needed —
  only the UI spinbox ranges. arch_v2 also has 4 extra overlap spinboxes in
  the unified `acquisition_setup.py`; all were updated for consistency.
- **6713d854:** arch_v2 routes the flag through a feature-flags registry that
  loads from `_def`; both the `_def` value and the registration default were
  set to True so they agree.
- **0771c850:** arch_v2's `SquidFilterWheel` uses a single `_config` +
  `_pos_index` (not upstream's `_configs`/`_positions` dicts) and moves in mm
  deltas (no `_target_pos_to_usteps`/`_home_wheel`/`skip_init`). Logs were
  adapted to those internals; the `skip_init` warning has no arch_v2 analog.

## Skips (documented in upstream-status.yaml)

| Hash | Reason | Justification |
|------|--------|---------------|
| 9067b2ff | superseded | arch_v2's `configuration_HCS_v2.ini` already uses crop 2048 (intentional divergence from upstream's 3000→2700); machine-specific value. |
| beaf48cc | not-applicable | arch_v2's `confocal.py` reads iris from `xlight.{illumination,emission}_iris` directly; the buggy `update_iris_from_config`/`confocal_hardware_settings` fallback-to-`slider.minimum()` path doesn't exist. |

## Deferred

`af71d2ef` / `74752073` / `9c8556db` (untrack machine configs + CI `.example`
+ README). arch_v2 already ignores `*.ini` via `software/.gitignore` and tracks
a richer machine-config set; an arch_v2-wide hygiene decision should drive this
rather than a piecemeal port. Tracked as pending.

## Tests

**Files:**
- `tests/unit/squid/config/test_emission_filter_wheel_config_token.py` — 17 cases
- `tests/unit/squid/core/config/test_feature_flags.py` — +1 case

Covers:
- Every shipped config uses the live token / lacks the dead token
- `ENABLE_PER_ACQUISITION_LOG` default resolves True

Verification: directly-affected suites pass (filter wheel, acquisition setup,
config, feature flags — 245+ tests); `ImageDisplayWindow` offscreen smoke test
(both LUT modes + new helpers). Note: the full `tests/unit` GUI suite hangs
locally on a test needing a real display — environment limitation, unrelated.

## Audit

- [x] Logic matches upstream (adapted to arch_v2)
- [x] arch_v2 patterns followed
- [x] Tests added
- [x] Skips documented with justification
- [ ] Full `tests/unit` GUI suite (blocked locally; relies on CI/xvfb)

# Preserve illumination_channel in Confocal Override (+ Deferrals)

**Our Commit:** b5d93313
**Date:** 2026-05-05
**Status:** PORTED (07ac6e55) + 4 DEFERRED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 07ac6e55 | fix: preserve illumination_channel when applying confocal override (#511) |

## Upstream Commits Skipped (Deferred)

| Hash | Title | Reason |
|------|-------|--------|
| d30b2fd9 | feat: Replace illumination timeout with serial watchdog (#506) | deferred — firmware + hardware testing required |
| 8d49273c | feat: Confocal model registry and per-channel iris persistence (#508) | deferred — 1207-line architectural change |
| 35321c92 | fix: speed up XLight iris commands and avoid response mismatch (#509) | deferred — depends on 8d49273c |
| b7a8826b | fix: sync iris sliders from acquisition config on startup (#510) | deferred — depends on 8d49273c |

## Summary

Ports upstream 07ac6e55 — same confocal-mode no-light bug existed in arch_v2.
`get_effective_settings()` replaced the entire `illumination_settings` with the
confocal override's copy, but objective files don't store `illumination_channel`,
so the override has `illumination_channel=None` and `turn_on_illumination()`
couldn't find the laser/LED to activate. Fixed by merging only `intensity` from
the override and preserving `illumination_channel` from the base.

## Files Created/Modified

### Created
- `conductor/tracks/upstream-port/plans/2026-05-05-confocal-iris-deferred.md`
- `conductor/tracks/upstream-port/plans/2026-05-05-watchdog-deferred.md`

### Modified
- `software/src/squid/core/config/models/acquisition_config.py`:
  `get_effective_settings()` now uses `model_copy(update={"intensity": ...})` so
  `illumination_channel` is preserved from the base channel; camera settings
  also `model_copy()` to avoid aliasing.
- `software/tests/unit/squid/core/config/test_models.py`:
  Added `test_effective_settings_preserves_illumination_channel`.

## Architecture Adaptations

Direct port — arch_v2's `get_effective_settings()` had the same structure as
upstream pre-fix.

## Tests

`tests/unit/squid/core/config/test_models.py` — 25 tests pass (including the new
regression test).

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Regression test added
- [x] Deferrals documented in plan files

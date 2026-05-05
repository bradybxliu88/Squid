# Headless Microscope API (Partial Port)

**Our Commit:** 81d194c8
**Date:** 2026-05-05
**Status:** PORTED (partial)

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 44c4f518 | feat: headless microscope API — hardware init, single-FOV acquisition, laser AF, profiles (#524) |

## Summary

Partial port of upstream's headless API additions to `Microscope`:
channel-name validation that raises `ValueError` with the list of available
channels (replacing the previous silent no-op), a `save_image()` helper that
auto-selects `.tiff` for `uint16` and creates parent directories, and an
`acquire_single_fov()` method that iterates a C+Z stack at the current
position and saves each image with the standard `0_0_{z}_{channel}` naming.

## Files Created/Modified

### Created
- `software/tests/integration/control/test_microscope_headless_api.py` (6 tests)
- `conductor/tracks/upstream-port/plans/2026-05-05-headless-api.md`

### Modified
- `software/src/squid/backend/microscope.py`:
  - Added `time`, `imageio`, `Path`, `List` imports
  - New `_get_channel_or_raise(objective, channel_name)` helper
  - `set_illumination_intensity` and `set_exposure_time` now use the helper
  - New `save_image(image, path)` method
  - New `acquire_single_fov(channel_names, save_path, NZ, deltaZ_mm, z_stacking_config)` method

## Architecture Adaptations

**Skipped (architectural mismatch):**
- Hardware init consolidation in `_prepare_for_use`: arch_v2's
  `application._initialize_hardware()` already handles stage limits, homing, and
  trigger mode setup through the service layer. Moving it to `Microscope` would
  duplicate logic and conflict with the service-based init flow.
- Lazy laser AF controller (`_ensure_laser_af_controller`): arch_v2's
  `LaserAutofocusController` takes service objects (`camera_service`,
  `stage_service`, `peripheral_service`, `piezo_service`), constructed by
  `ApplicationContext._build_laser_autofocus()`. Building it from `Microscope`
  with raw drivers (as upstream does) bypasses that architecture.
- `perform_laser_af`, `load_user_profile`, `set_objective` laser AF reload:
  Same reason. Headless callers should use `ApplicationContext(simulation=True)`
  which already builds the full graph including laser AF.

**Adapted:**
- `_get_channel_or_raise` uses `channel_configuration_manager` (arch_v2's
  manager-based API) rather than upstream's `live_controller.get_channel_by_name`.

## Tests

`tests/integration/control/test_microscope_headless_api.py` — 6 tests, all pass:
- `test_set_illumination_intensity_invalid_channel_raises`
- `test_set_exposure_time_invalid_channel_raises`
- `test_save_image_uint16_uses_tiff`
- `test_save_image_creates_parent_dir`
- `test_acquire_single_fov_invalid_z_stacking_config_raises`
- `test_acquire_single_fov_unknown_channel_raises`

Existing `tests/integration/control/test_microscope.py` (2 tests) still passes.

## Audit

- [x] Logic matches upstream for the ported portions
- [x] arch_v2 patterns followed (channel_configuration_manager API)
- [x] 6 new integration tests
- [x] Skipped portions documented and justified

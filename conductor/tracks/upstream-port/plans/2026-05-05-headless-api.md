# Headless Microscope API

**Status:** IN-PROGRESS
**Started:** 2026-05-05

## Upstream Commits

- [ ] `44c4f518` - feat: headless microscope API — hardware init, single-FOV acquisition, laser AF, profiles (#524)

## Implementation Notes

The upstream commit consolidated GUI hardware init into `Microscope._prepare_for_use`,
added headless acquisition methods, lazy laser AF, and profile management.

**What's already in arch_v2 (skip — different architecture):**
- Hardware init: arch_v2 puts stage limits, homing, trigger mode setup in
  `application._initialize_hardware()`. Moving it to `Microscope._prepare_for_use`
  would duplicate logic and conflict with the existing service-based init flow.
- Laser AF lazy creation: arch_v2's `LaserAutofocusController` is service-based
  (camera_service, stage_service, peripheral_service, piezo_service). Constructing
  it from `Microscope` directly bypasses the service layer; headless callers should
  use `ApplicationContext` which already builds the full graph.
- Profile management: `load_user_profile()` would require laser AF reload, which
  in arch_v2 is service-orchestrated.

**What's worth porting:**
- Channel validation: raise `ValueError` instead of silent no-op when channel
  not found (`set_illumination_intensity`, `set_exposure_time`).
- `save_image()` method.
- `acquire_single_fov()` for C+Z stack acquisition at current position.
- `_get_channel_or_raise()` helper.

## Implementation Checklist

### Phase 1: Channel validation
- [ ] `Microscope._get_channel_or_raise(objective, channel_name)` helper that raises ValueError with available channel list
- [ ] `Microscope.set_illumination_intensity` uses `_get_channel_or_raise`
- [ ] `Microscope.set_exposure_time` uses `_get_channel_or_raise`

### Phase 2: save_image / acquire_single_fov
- [ ] `Microscope.save_image(image, path)` writes uint16 as TIFF, others as configured format
- [ ] `Microscope.acquire_single_fov(channel_names, save_path, NZ, deltaZ_mm, z_stacking_config)` iterates z-planes × channels, saves each image
- [ ] Validate `z_stacking_config` is one of `FROM BOTTOM`, `FROM CENTER`, `FROM TOP`
- [ ] Always return Z to start position even on error

### Tests
- [ ] Existing microscope tests continue to pass
- [ ] Black formatting

## Notes

Skipped from this port:
- `_prepare_for_use` hardware init consolidation (arch_v2 has it in application.py)
- Laser AF lazy creation (architecturally service-based in arch_v2)
- Profile loading (architecturally service-based in arch_v2)

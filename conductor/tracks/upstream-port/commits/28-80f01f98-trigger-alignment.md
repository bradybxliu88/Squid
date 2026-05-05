# Trigger Mode Alignment for Tucsen Level Trigger

**Our Commit:** 80f01f98
**Date:** 2026-05-05
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 31b454c3 | fix: align liveController.trigger_mode with hardware setup for Tucsen level trigger (#533) |

## Summary

arch_v2 had the same Tucsen level-trigger pulse-width bug as upstream. The
camera and microcontroller were configured for hardware mode at startup, but
`LiveController.trigger_mode` stayed at its `SOFTWARE` default. `MultiPointWorker`
branches on `liveController.trigger_mode` and sent `illumination_time=None`
down the SOFTWARE path; `acquisition_camera_hw_trigger_fn` translates `None`
to `illumination_on_time_us=0`, so the firmware's level-trigger pulse width
(`strobe_delay + illumination_on_time`) collapsed to just `strobe_delay` —
fixed and independent of exposure.

The fix: in `application._initialize_hardware()` and
`_setup_camera_callbacks_only()`, after configuring the camera and MCU,
explicitly assign `live_controller.trigger_mode` to match. Also: set
`TriggerExposureType=1` (Width) on Tucsen GenICam cameras when entering
`HARDWARE_TRIGGER`, so the Aries follows the trigger pulse width rather
than its programmed `ExposureTime` — required for level-trigger acquisition.

## Files Created/Modified

### Created
- `conductor/tracks/upstream-port/plans/2026-05-05-trigger-alignment.md`

### Modified
- `software/src/squid/application.py` — both `_initialize_hardware()` and `_setup_camera_callbacks_only()` now assign `live_controller.trigger_mode` after configuring camera and MCU
- `software/src/squid/backend/drivers/cameras/tucsen.py` — adds `TriggerExposureType=1` for GenICam HARDWARE_TRIGGER

## Architecture Adaptations

Direct attribute assignment is used (`live_controller.trigger_mode = ...`)
rather than `set_trigger_mode()`, because the latter would call
`set_exposure_time(currentConfiguration.exposure_time)` and
`currentConfiguration` may still be `None` this early in init. This matches
upstream's approach.

## Tests

- Existing config and microcontroller tests pass
- No new tests added (hardware-specific behavior, requires real Tucsen camera)

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Both init paths covered (full init + skip-init)
- [x] Tucsen TriggerExposureType applied for GenICam variants only

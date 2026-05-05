# Trigger Mode Alignment for Tucsen Level Trigger

**Status:** IN-PROGRESS
**Started:** 2026-05-05

## Upstream Commits

- [ ] `31b454c3` - fix: align liveController.trigger_mode with hardware setup for Tucsen level trigger (#533)

## Implementation Checklist

### Phase 1: Align live_controller.trigger_mode in application init
arch_v2's `application.py:_initialize_hardware()` and `_setup_camera_callbacks_only()`
already set the camera acquisition mode and microcontroller trigger mode based on
`DEFAULT_TRIGGER_MODE`, but they leave `LiveController.trigger_mode` at its
`SOFTWARE` default. This causes the same Tucsen level-trigger pulse-width bug
upstream had: `MultiPointWorker` branches on `liveController.trigger_mode` and
sends `illumination_time=None` when SOFTWARE, which the firmware level-trigger
pulse width treats as 0 µs.

- [ ] `application.py:_initialize_hardware()` (lines ~240-249): After setting camera/MCU mode, assign `live_controller.trigger_mode = TriggerMode.HARDWARE` (HW path) or `TriggerMode.SOFTWARE` (SW path)
- [ ] `application.py:_setup_camera_callbacks_only()` (lines ~301-310): Same alignment
- Use direct attribute assignment, not `set_trigger_mode()` — the latter would call `set_exposure_time(currentConfiguration.exposure_time)` on a `None` config.

### Phase 2: Tucsen TriggerExposureType=1 for level mode
- [ ] `backend/drivers/cameras/tucsen.py:889-892`: When entering `HARDWARE_TRIGGER` on a GenICam Tucsen, also call `_set_genicam_parameter("TriggerExposureType", 1, TUELEM_TYPE.TU_ElemEnumeration.value)` so the Aries follows the trigger pulse width instead of programmed `ExposureTime`.

### Tests
- [ ] Run unit tests
- [ ] Black formatting

# May 2026 Misc Fixes

**Status:** IN-PROGRESS
**Started:** 2026-05-05

## Upstream Commits

- [ ] `cc677e2c` - fix: handle illumination sources without a hardware source code (#518)
- [ ] `36dc1444` - fix: LaserAFConfig defaults from _def.py & status bar height stability (#517)
- [ ] `30d95ea1` - fix: suppress Pydantic serializer int/float warning in LaserAFConfig (#523)
- [ ] `6e3f731a` - fix: fix logging format error and noisy backpressure warning (#512)

## Implementation Checklist

### Phase 1: cc677e2c — model -1 sentinel
arch_v2's `LiveController` does not have `_is_led_matrix()` (LED matrix dispatch
uses `ChannelType.LED_MATRIX` enum in `channel_definitions.py`). Apply only the
model API change to keep the contract aligned with upstream.

- [ ] `core/config/models/acquisition_config.py:189` `get_illumination_source_code()` returns `-1` when channel exists with no source code

### Phase 2: 36dc1444 — LaserAF defaults + status bar
- [ ] `core/config/models/laser_af_config.py`: Update all hardcoded defaults to `default_factory=lambda: _def.<CONST>` for: `width`, `height`, `pixel_to_um_calibration_distance`, `laser_af_range`, `laser_af_averaging_n`, `spot_detection_mode`, `displacement_success_window_um`, `spot_crop_size`, `correlation_threshold`, `y_window`, `x_window`, `min_peak_width`, `min_peak_distance`, `min_peak_prominence`, `spot_spacing`, `filter_sigma`, `focus_camera_exposure_time_ms`, `focus_camera_analog_gain`, `initialize_crop_width`, `initialize_crop_height`
- [ ] `ui/widgets/hardware/laser_autofocus.py:404-406`: Hide `MULTI_SECOND_RIGHT` from `spot_mode_combo` (skip in iteration)
- [ ] `ui/widgets/warning_error_widget.py`:
  - Set `label_text.setFixedHeight(max(text_height, label_icon.sizeHint().height()))`
  - In `_format_display_message`, collapse newlines with `" ".join(msg.split())`

### Phase 3: 30d95ea1 — Pydantic warnings=False
- [ ] `core/config/repository.py:131`: Add `warnings=False` to `model_dump(exclude_none=True, mode="json")` (the LaserAFConfig save path; need to verify which `model_dump` call this is)
- [ ] `backend/controllers/autofocus/laser_auto_focus_controller.py:282,476`: Add `warnings=False` to `self.laser_af_properties.model_dump()`

### Phase 4: 6e3f731a — microcontroller format string
arch_v2's `BackpressureMonitorWidget` (display/monitoring.py) is push-based via
`update_stats()` and does not have the `get_lock()` race; widget portion does
not apply.

- [ ] `backend/microcontroller.py:1024-1026`: Add `%s` placeholder to `log.warning(...)` for `last_command_aborted_error`

### Tests
- [ ] Run laser AF config tests
- [ ] Run warning error widget tests
- [ ] Run microcontroller tests
- [ ] Black formatting

## Notes

The upstream `_is_led_matrix()` change in `live_controller.py` is not applicable —
arch_v2 dispatches LED matrix via `ChannelType.LED_MATRIX` enum, not by checking
`source < 10`.

The upstream `BackpressureMonitorWidget` change in `widgets.py` is not applicable —
arch_v2's widget is push-based via `update_stats()`, not polling-based with
`get_lock()` calls. No race condition exists.

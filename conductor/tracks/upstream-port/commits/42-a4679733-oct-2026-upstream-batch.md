# October 2026 Upstream Batch: general fixes + laser AF, laser engine, Dragonfly, ORCA

**Our Commits:** `a4679733`..`6ac93846` (plus fork-only `d8f9a209`)
**Date:** 2026-10-11
**Status:** PORTED (18 upstream commits), PARTIAL (`8c62827a` Z clamp only)
**Plan:** `plans/2026-10-11-oct-upstream-batch.md`

## Upstream Commits Ported

| Upstream | Title | Our commit(s) |
|----------|-------|---------------|
| 5be72178 | fix: focus map KeyError, stage move timeouts 38-300x too long, focus-map double move | 7ed480d2 |
| 8c62827a | fix(turret): prevent Z-restore timeout at startup — **Z clamp only** | ccff0b3c |
| 0f789a45 | fix(stage): tolerate a corrupted cache/last_coords.txt | 787061da |
| f8fe36d9 + e34dec3d | fix(camera): don't reuse a pre-trigger frame (+ snapshot refactor) | 479bcf35 |
| e86a608f | fix(camera): all-zero ToupCam frames from read-buffer realloc race | 869a4cda |
| 875eda71 | feat(camera): sensor mode selection (Hamamatsu READOUTSPEED) | 42beae54 |
| 56db6965 | feat(camera): persist sensor mode in the camera settings cache | ccd86c13, 9e5e48a0 |
| 7385a179 | fix(live): hold live triggers while the MCU is busy (parts 1, 3, 5) | 3bb578e6, e6c077df, 6acd35a0, abb49ee2 |
| f3bca2cb | fix(af): LAPE focus measure without int16 overflow | dd470622 |
| 2a550c86 | fix(laser-af): .ini crop sizes on Initialize; reject unknown spot params | ad0b2bb5 |
| 6919470e | feat: per-channel z-offset for laser AF (parts 1+2) | fc84e31e, b2c39ae9 |
| ee7897c9 | fix: skip focus-map interpolation for single-FOV snap | a9ec2ff4 |
| 74824168 | fix(prefs): read ini values with inline comments | ef9b5df5, 9a22857f |
| aa9107d9 | fix file format menu and enable TIFF compression | fb505550, 84611f4c, 5a997b67, 1f120718 |
| 24276973 | fix(channel-config): persist settings for configurator-added channels | f9ec7fff |
| edc13253 | fix(illumination): apply config edits without restart | be74739b |
| 1f4130f2 | fix(laser-engine): bound serial write_timeout | ffe5c16a |
| 432772be | fix(dragonfly): make the Andor Dragonfly work (applicable parts) | 6ac93846 |

### Fork-only fixes (no upstream commit)

| Commit | Fix |
|--------|-----|
| d8f9a209 | `wellplate_multipoint` imported a stray `f` from `_def` that only exists when `cache/objective_and_sample_format.txt` exists → ImportError on a fresh install; also broke collection of 3 test modules |
| a4679733 | `AbstractStage.get_config()` restored. 47e20909 renamed it to a `config` property but CephlaStage, PriorStage, StageService and stage_utils still call `get_config()` → every blocking move on a real Cephla stage raised AttributeError (simulation unaffected: SimulatedStage defines its own) |
| fbf5a424 | `CephlaStage.home()` NameError: `squid` never bound in `backend/drivers/stages/cephla.py` (TYPE_CHECKING-only import) |
| 64aed6fb | Preferences Save raised AttributeError (`sim_microcontroller_checkbox` vs `sim_mcu_checkbox`), so nothing could be saved |
| b085d67c | Controller kept a by-region `_focus_map` after "Use Focus Map" was unchecked |

## Summary

Semantic ports of the general bug fixes from upstream `0a10ad1e..a77cbac3` (2026-06-13 → 2026-10-10) plus
the commits for this machine's hardware: laser reflection AF, Squid laser engine, Andor Dragonfly and
Hamamatsu ORCA. Each unit was ported on its own branch in an isolated worktree and then adversarially
reviewed. Two reviews pushed fixes: `abb49ee2`, which re-checks MCU busy after the CameraService lock
wait, and `1f120718`/`9a22857f`, which cover OverflowError on an `inf` compression level, test isolation
and a broken fake. The other six reviews found no blocker/major issues.

## Architecture Adaptations

- **875eda71:** the upstream `_trigger_lock` / `_send_trigger_imp` ABC template refactor is skipped.
  CameraService's RLock already serializes triggers against ROI/binning/pixel-format/sensor-mode changes.
  Sensor mode flows through `CameraService` plus a command/state event pair; the widget publishes commands
  only.
- **7385a179:**
  - A lock-free `PeripheralService.is_busy()` is used, because `wait_till_operation_is_completed` holds the
    service lock for up to 10 s.
  - Parts 2 and 4 are already covered by arch_v2 (CameraService lock, event-driven live buttons).
- **6919470e:** keeps the fork's piezo-only, ungated per-channel z-offset semantics. Orchestrator protocol
  `z_offset_um` overrides rely on it, so upstream's Reflection-AF + checkbox gate is not copied. The capture
  UI / Apply-in-Live / objective-sourced `z_offset_um` are deferred.
- **aa9107d9:** `SaveSettings` is snapshotted once per acquisition by the controller and shared with the
  worker and the NDViewer push decision. This removes the fork's split between an import-time
  `FILE_SAVING_OPTION` binding in the worker and live `_def` reads in the controller.
- **edc13253:**
  - `ChannelConfigService` reads the illumination config through the repository instead of snapshotting
    it at init.
  - The per-channel `max_output` cap is **not** ported, because the fork sets illumination in several
    places and has no single clamp point.
  - The configurator dialog still edits the cached general config in place on Cancel.
- **8c62827a:** only `AxisConfig.clamp_position` plus the clamp in `CephlaStage.move_z_to`. The turret
  de-energize / `restore_z` parts wait on the turret decision.

## Tests

New or extended tests, all failing on the base where they cover a fix. Details are in each commit body:

- **Stage:**
  - `tests/unit/squid/backend/drivers/stages/test_cephla_stage.py` (34)
  - `tests/unit/squid/core/config/test_axis_config.py`
  - `tests/integration/squid/test_stage_z_limit.py`
  - 15 cache tests in `tests/integration/squid/test_stage.py`
  - 3 MCU diagnostics tests in `tests/integration/control/test_microcontroller.py`
  - `tests/unit/squid/ui/widgets/test_focus_map_widget.py`
- **Cameras:**
  - `test_default_camera.py`
  - `test_toupcam_buffer_race.py`
  - `test_toupcam_trigger_state.py`
  - `test_sensor_mode.py`
  - `test_hamamatsu_sensor_mode.py`
  - `test_settings_cache.py`
  - `test_camera_sensor_mode_widgets.py`
  - CameraService sensor-mode tests
- **Live/MCU busy:**
  - `test_live_controller_mcu_busy_hold.py`
  - `test_snap_controller_mcu_busy.py`
  - PeripheralService busy tests
  - run_acquisition idle-wait tests
- **Autofocus:**
  - LAPE dtype tests in `tests/unit/control/test_utils.py`
  - `test_laser_af_settings.py`
  - `test_laser_spot.py::TestDetectSpot`
  - `test_multi_point_worker_z_offset.py` (12)
  - focus-map snap/stale tests
- **Preferences/saving:**
  - `test_preferences_dialog.py` (inline comments, file-format fallback, TIFF compression)
  - `test_save_settings.py`
  - `test_tiff_compression.py`
  - zarr / NDViewer snapshot tests
- **Config:**
  - `test_repository.py` (missing-channel seeding, edit copies)
  - `test_channel_config_service.py` (illumination not snapshotted)
  - `test_channel_config_dialogs.py`
- **Peripherals:**
  - laser engine write-timeout tests
  - `test_dragonfly.py` (12)
  - `test_dragonfly_confocal_widget.py` (9)
  - `test_microscope_close.py`

## Audit

- [x] Logic matches upstream (reviewer compared each diff line by line)
- [x] arch_v2 patterns followed (EventBus from UI, locks not held across publishes or long waits)
- [x] Tests added
- [x] `Ports-Upstream` trailers present for every ported commit
- [ ] On-hardware verification (none of the hardware paths are tested on real devices)
- [ ] Turret series (`8c62827a` turret parts, `a518249e`, `091bf4e8`, `a77cbac3`): awaiting hardware confirmation
- [ ] Feature ports not in this batch: `752c9bb8`/`f9305f9f` Snap button, `46a139b9`, `dd0c4cd2` + finishing `3bcdca38`

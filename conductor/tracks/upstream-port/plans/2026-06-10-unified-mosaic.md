# Unified Mosaic + Plate View (UnifiedMosaicWidget)

**Status:** IN-PROGRESS — approach **C then B** approved. Step 1 DONE (`f000bee3`); Step 2 is the follow-up.
**Started:** 2026-06-10
**Branch:** master (arch_v2)

## Upstream Commit

- [~] `3bcdca38` - feat: unify Mosaic + Plate View into single UnifiedMosaicWidget (#530) — partial (step 1/2)

## Progress

### Step 1 — DONE (`f000bee3`, additive, flag off)
- [x] `mosaic_utils.py` (calculate_overlap_pixels, downsample_tile/_pyrdown_chain, parse/format_well_id) + 33 tests
- [x] `ui/widgets/display/unified_mosaic.py` (UnifiedMosaicWidget, DisplayMode, blit_tiles_to_canvas, last-view-mode cache) — imports clean; pure helpers tested (6 tests); NOT wired into main_window
- [x] `_def`: `USE_UNIFIED_MOSAIC_VIEW` (flag, off) + `SAVE_DOWNSAMPLED_OVERVIEW`
- [x] `multi_point_utils.MosaicTileUpdate` (per-tile data contract for step 2)
- [x] `core/utils/hardware_utils.serialize_for_yaml`
- [x] Verified: 39 new pass; existing mosaic-worker + downsampled-view tests (47) still pass

### Step 2a — DONE (`236ef801`, in-GUI feed wired under flag, IPC bypassed not deleted)
- [x] Construct + dispatch `MosaicTileUpdate` from `main_window._on_stream_capture_mosaic` per tile
      (x/y from `CaptureInfo.position`, well_id from `region_id`, well_origin_mm from cached PlateViewInit)
- [x] `multi_point_worker`: emit `PlateViewInit` with `well_ids` + per-well origins; skip `DownsampledViewJob` when flag on
- [x] `multi_point_controller`: emit downsampled-view init (PlateViewInit) when unified flag on
- [x] `events.PlateViewInit`: add `well_ids` + `well_origins_mm` (defaults empty)
- [x] `main_window`: flag-gated `UnifiedMosaicWidget` tab; setPlateLayout on PlateViewInit; clear + well/fov-click signals; close on exit
- [x] Export `UnifiedMosaicWidget`; widget fed in-GUI for both modes
- [x] Verified: flag-off regression identical (23 pre-existing failures unchanged); core acquisition tests pass

### Step 2b — TODO (post-GUI-QA cleanup; needs on-hardware QA first)
- [ ] **On-hardware/GUI QA** of the flag-on path (mosaic fill, plate fill, mode toggle, save) — REQUIRED before deletion
- [ ] Wire manual-ROI shape drawing (`enable_shape_drawing`/`signal_shape_drawn`) + click-to-move (`signal_coordinates_clicked`) for the unified widget
- [ ] After QA: flip `USE_UNIFIED_MOSAIC_VIEW` default on (or remove flag); remove `napari_plate_view.py`
- [ ] Remove `DownsampledViewJob`/`DownsampledViewResult` (job_processing.py), `WellTileAccumulator`/`DownsampledViewManager` (downsampled_views.py), worker plate plumbing, `PlateViewUpdate` event + dead tests
- [ ] Update `test_multipoint_scenarios.py` (drop PlateViewUpdate), delete `test_downsampled_views.py`/`test_job_processing_downsampled.py`

**Note:** physical deletion deferred deliberately — removing the working IPC plate pipeline before
the in-GUI replacement is verified on real hardware would leave no plate view if anything is off.
The flag keeps both paths available; the IPC code is dead only when the flag is on.

Upstream stat: 27 files, +2157 / −5580. Replaces `NapariMosaicDisplayWidget` +
`NapariPlateViewWidget` + the IPC "downsampled view" subprocess pipeline with one
in-GUI `UnifiedMosaicWidget` (one canvas per channel, two modes via a toggle). Claimed
~53x memory reduction; eliminates the glibc fragmentation behind 59 GB RSS after long runs.

## CRITICAL: arch_v2 has already diverged

This is NOT a clean 1:1 port. Before #530, upstream's mosaic AND plate views both went
through heavy machinery. arch_v2 is partway to the destination already:

- **Mosaic view (arch_v2):** ALREADY in-GUI and memory-light. `napari_mosaic.py` has a
  `MosaicWorker`/`MosaicCompositor` (background QThread) that downsamples each tile with
  cv2 and feeds napari via a 10 FPS QTimer. Tiles arrive via `StreamHandler.capture` →
  `main_window._on_stream_capture_mosaic` → `updateMosaic`. **Most of upstream's mosaic win
  is already present here.**
- **Plate view (arch_v2):** STILL heavy. `napari_plate_view.py` is fed by the subprocess
  pipeline: `multi_point_worker` creates `DownsampledViewJob` (job_processing.py) →
  `WellTileAccumulator` (downsampled_views.py) stitches per-well in a subprocess →
  `DownsampledViewManager` holds the plate array → `PlateViewInit`/`PlateViewUpdate` events
  → `napari_plate_view.updatePlateView`. **This is where the real arch_v2 memory/IPC cost is.**

So upstream's "unify + delete IPC" maps in arch_v2 to: **replace the subprocess plate
pipeline with an in-GUI plate mode fed from the same per-tile path the mosaic already uses,
and (optionally) merge the two tabs into one with a Full/Plate toggle.**

## arch_v2 inventory (from code-explorer)

**Display:** `ui/widgets/display/napari_mosaic.py` (938 L), `napari_plate_view.py` (371 L).
**IPC pipeline (deletion candidates):**
- `backend/controllers/multipoint/downsampled_views.py` (737 L: `WellTileAccumulator`,
  `DownsampledViewManager`, overlap/stitch/downsample helpers, well-id parse/format).
- `backend/controllers/multipoint/job_processing.py` — `DownsampledViewJob` +
  `DownsampledViewResult` (~230 L) intermixed with the zarr-writer/backpressure JobRunner
  code that MUST be kept.
- `multi_point_worker.py` — `_initialize_downsampled_view_manager`, `get_plate_view`,
  `_process_downsampled_view_result`, `_summarize_runner_outputs` plate branch,
  `DownsampledViewJob` enqueue (~60+ L), plus `AcquisitionParameters` downsampled_* fields.
**Events:** `MosaicLayersInitialized`, `MosaicLayersCleared`, `PlateViewInit`, `PlateViewUpdate`.
**Config (`_def.py`):** `MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM`, `DISPLAY_PLATE_VIEW`,
  `SAVE_DOWNSAMPLED_WELL_IMAGES`, `DOWNSAMPLED_*` (resolutions, plate res, z-projection,
  interpolation, timeouts), `PLATE_VIEW_MIN_VISIBLE_PIXELS`, `PLATE_VIEW_MAX_ZOOM_FACTOR`.
**Tests (deletion/replacement):** `tests/unit/ui/widgets/display/test_napari_mosaic_worker.py`,
  `tests/unit/squid/controllers/multipoint/test_downsampled_views.py`,
  `tests/.../test_job_processing_downsampled.py`.

**Upstream new files:** `control/core/mosaic_utils.py` (223 L: `calculate_overlap_pixels`,
`downsample_tile`/`_pyrdown_chain`, `parse_well_id`/`format_well_id`),
`control/widgets_mosaic.py` (999 L: `UnifiedMosaicWidget`, `DisplayMode`, `blit_tiles_to_canvas`,
last-view-mode cache), plus tests `test_mosaic_utils.py` (403 L), `test_unified_mosaic_widget.py` (51 L).

Upstream widget constructor: `UnifiedMosaicWidget(objectiveStore, camera, contrastManager)`.
arch_v2 mosaic uses `(contrastManager, event_bus)` + `@handles` EventBus subscriptions and a
worker thread — so the widget body needs real adaptation, not transcription.

## Risk

- Touches the LIVE acquisition data path (`multi_point_worker`, `job_processing`). A defect
  breaks real imaging, and full verification needs GUI acquisition runs that can't be done
  headless here. Unit tests cover utils + widget logic but not the end-to-end plate fill.
- ~3500 lines across 27 files; largest port in this track by far.

## Proposed approaches (pick one)

### A. Full faithful port (high effort, high risk)
Build `UnifiedMosaicWidget` adapted to arch_v2 (EventBus/@handles/worker), replace BOTH
napari widgets, delete the entire subprocess plate pipeline, rewire `multi_point_worker` to
feed plate mode in-GUI from the per-tile path, port `mosaic_utils.py` + tests, update `_def`,
remove dead events. Matches upstream end state. Days of careful work; needs real-acquisition QA.

### B. Scoped: kill the plate-view IPC pipeline, keep two tabs (medium)
Keep arch_v2's already-good in-GUI mosaic. Replace ONLY the plate-view subprocess pipeline
with an in-GUI plate accumulator fed from the same tile path; delete
`DownsampledViewJob`/`WellTileAccumulator`/`DownsampledViewManager` and the
`PlateViewInit/Update` round-trip. Captures most of the memory win with far less blast radius;
does not merge the tabs/toggle UX. Smaller, more verifiable.

### C. Port utils + write the widget behind a feature flag (incremental)
Port `mosaic_utils.py` + `UnifiedMosaicWidget` + its unit tests now (no removal of the old
pipeline), wire it behind a flag (default off). Lets the new widget land and be tested without
destabilizing acquisition; the old pipeline removal + rewire happens in a follow-up once the
widget is proven. Safest staging.

### D. Defer (document why)
Record as deferred: high risk to live acquisition, needs hardware/GUI QA; arch_v2 already has
the in-GUI mosaic so the most acute memory issue is partially mitigated. Revisit when GUI
acquisition QA is available.

## Recommendation

**C then B** — land `mosaic_utils.py` + the widget (flagged, tested) first, then remove the
plate IPC pipeline and rewire. This keeps each step verifiable and avoids a single giant,
hard-to-QA change to the acquisition path. Full tab-merge UX (A) can follow.

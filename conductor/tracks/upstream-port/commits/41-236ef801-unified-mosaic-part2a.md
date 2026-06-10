# Unified Mosaic + Plate View — Step 2a (in-GUI feed wired under flag)

**Our Commit:** `236ef801`
**Date:** 2026-06-10
**Status:** PARTIAL (step 2a of 2 for upstream `3bcdca38`; follows step 1 `f000bee3`)

## Upstream Commit (partial)

| Hash | Title |
|------|-------|
| `3bcdca38` | feat: unify Mosaic + Plate View into single UnifiedMosaicWidget (#530) |

## Summary

Wires the `UnifiedMosaicWidget` (landed in step 1) as the mosaic+plate display, fed entirely
in-GUI, gated by `USE_UNIFIED_MOSAIC_VIEW` (default off). When the flag is on, the subprocess
plate pipeline (`DownsampledViewJob`) is bypassed and the widget fills both Full View (mosaic)
and Plate View modes from the per-tile stream. The IPC pipeline code is **not deleted** — it
remains the flag-off default until the in-GUI path is verified on real hardware (step 2b).

## Files Modified

- `ui/main_window.py` — flag-gated `UnifiedMosaicWidget` tab (napari mosaic + plate widgets → None
  when on); `_on_stream_capture_mosaic` builds a `MosaicTileUpdate` per tile (x/y from
  `CaptureInfo.position`, well_id from `region_id`, parsed row/col, well_origin_mm from cached
  `PlateViewInit`); `_on_plate_view_init` caches per-well origins + calls `setPlateLayout`; clear +
  well/fov-click signals connected; viewer closed on exit.
- `backend/controllers/multipoint/multi_point_worker.py` — `PlateViewInit` now carries `well_ids`
  + per-well origins (top-left of each well's FOV bbox); `_create_downsampled_view_job` returns
  `None` (after layout/PlateViewInit emit) when `USE_UNIFIED_MOSAIC_VIEW` — no subprocess jobs.
- `backend/controllers/multipoint/multi_point_controller.py` — `generate_downsampled_views` also
  true when the unified flag is on (so `PlateViewInit` is emitted for the widget's plate mode).
- `core/events.py` — `PlateViewInit`: add `well_ids: List[str]` + `well_origins_mm: Dict[...]`
  (defaults empty; the legacy IPC path ignores them). Imported `field`.
- `ui/widgets/__init__.py` + `display/__init__.py` — export `UnifiedMosaicWidget`.

## Architecture Adaptations

- arch_v2's mosaic is fed via `StreamHandler.capture` (data plane), not EventBus — image data
  stays off the EventBus. So `main_window` builds `MosaicTileUpdate` from the stream + a cached
  plate layout, rather than the worker emitting tile images over the bus (upstream's worker built
  the update directly because it had no separate data plane).
- Per-well origins are threaded through `PlateViewInit` (EventBus, no image data) so plate-mode
  tile placement uses stable non-negative offsets.

## Verification

- Flag-off (default) regression IDENTICAL with/without this change: `test_multipoint_scenarios.py`
  23 pre-existing failures unchanged (confirmed by stash comparison: 23 failed / 142 passed both
  ways); core acquisition tests (`basic_acquisition`, `image_count_calculation`,
  `disk_space_estimate`) pass (with sandbox off for `/tmp` writes).
- `mosaic_utils` (33) + `unified_mosaic` (6) tests pass; py_compile + import smoke clean.
- The 5 controller test failures are environmental (`/tmp` sandbox) or pre-existing
  (`set_af_flag` stale-test) — not from this change.

## Audit

- [x] In-GUI feed wired under flag; flag-off default fully intact (verified no regression)
- [x] Worker bypasses subprocess plate jobs when flag on; PlateViewInit carries layout + origins
- [x] arch_v2 data-plane convention respected (image via StreamHandler, not EventBus)
- [ ] **On-hardware GUI QA** of the flag-on path (mosaic/plate fill, mode toggle, save) — REQUIRED
- [ ] Step 2b: manual-ROI/click-to-move signal wiring; delete IPC pipeline + napari_plate_view;
      flip flag default; drop dead events/tests (after QA)

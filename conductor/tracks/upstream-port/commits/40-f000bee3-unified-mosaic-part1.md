# Unified Mosaic + Plate View — Step 1 (foundation, flagged off)

**Our Commit:** `f000bee3`
**Date:** 2026-06-10
**Status:** PARTIAL (step 1 of 2 for upstream `3bcdca38`)

## Upstream Commit (partial)

| Hash | Title |
|------|-------|
| `3bcdca38` | feat: unify Mosaic + Plate View into single UnifiedMosaicWidget (#530) |

## Approach

Approved approach **C then B** (see `plans/2026-06-10-unified-mosaic.md`). Upstream's #530 is
~3500 lines touching the live acquisition path; arch_v2 has already diverged (its mosaic view is
already in-GUI via `MosaicWorker`; only the *plate* view still uses the subprocess
`DownsampledViewJob` pipeline). To avoid destabilizing acquisition in one big change, this is
staged:

- **Step 1 (this commit):** land the new pure helpers + the `UnifiedMosaicWidget` behind a flag
  (default off), fully unit-tested, with NO removal of the existing pipeline. Zero acquisition risk.
- **Step 2 (follow-up):** remove the subprocess plate pipeline and rewire `multi_point_worker` to
  feed the unified widget in-GUI; wire it into `main_window` under the flag.

## Files Created/Modified

### Created
- `backend/controllers/multipoint/mosaic_utils.py` (223 L) — `calculate_overlap_pixels`,
  `downsample_tile`/`_pyrdown_chain`, `parse_well_id`/`format_well_id`.
- `ui/widgets/display/unified_mosaic.py` (998 L) — `UnifiedMosaicWidget` (one canvas per channel,
  Full View ↔ Plate View toggle), `DisplayMode`, `blit_tiles_to_canvas`, last-view-mode cache.
- `tests/unit/squid/controllers/multipoint/test_mosaic_utils.py` (33 tests; 2 perf tests opt-in).
- `tests/unit/ui/widgets/display/test_unified_mosaic_widget.py` (6 tests: blit + DisplayMode).

### Modified
- `src/_def.py` — `USE_UNIFIED_MOSAIC_VIEW` (flag, default False) + `SAVE_DOWNSAMPLED_OVERVIEW`.
- `backend/controllers/multipoint/multi_point_utils.py` — `MosaicTileUpdate` dataclass (the per-tile
  data contract the widget consumes; used by the step-2 feed rewire).
- `core/utils/hardware_utils.py` — `serialize_for_yaml` (yaml-safe serializer for per-well save sidecars).

## Architecture Adaptations

- Locations: `control/core/mosaic_utils.py` → `backend/controllers/multipoint/mosaic_utils.py`;
  `control/widgets_mosaic.py` → `ui/widgets/display/unified_mosaic.py`.
- Imports: `control._def` → `_def`; `control.utils` → `squid.core.utils.hardware_utils`;
  `control.utils_channel` → `squid.core.utils.utils_channel`; `squid.logging` → `squid.core.logging`.
- napari: `from napari.utils import Colormap` → `from napari.utils.colormaps import Colormap`
  (matches this repo's napari and the existing `napari_multichannel.py`/`napari_plate_view.py`).
- The widget retains its Qt `Signal`s (consistent with arch_v2's `napari_mosaic.py`, which also mixes
  Qt signals with EventBus `@handles`). It is NOT yet wired into `main_window` — step 2.

## Tests

- 39 new tests pass (33 mosaic_utils + 6 unified-widget; 2 perf tests skipped by default).
- Regression: existing `test_napari_mosaic_worker.py` + `test_downsampled_views.py` (47 tests) still pass.
- `unified_mosaic` module imports cleanly; shared-module additions verified.

## Audit

- [x] Pure helpers ported verbatim (imports adapted) and fully tested
- [x] Widget imports cleanly behind a flag; additive only, no removal
- [x] No regression (existing display/multipoint tests pass)
- [ ] Step 2: remove plate IPC pipeline + rewire feed + main_window wiring (follow-up)
- [ ] End-to-end plate-fill verification (needs GUI acquisition QA env)

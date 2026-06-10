# Click-to-Move Pixel-Size Safety + Ctrl+Scroll Z Navigation

**Our Commit:** 278b9fc8
**Date:** 2026-06-09
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 9bee0201 | feat: live view click-to-move fix and Ctrl+Scroll Z navigation (#532) |

## Summary

Two concerns from upstream #532, adapted to arch_v2's EventBus/controller
architecture (upstream did it inline in `gui_hcs.py`; arch_v2 routes clicks
through `ImageClickController`):

1. **Click-to-move pixel-size None-safety** — the controller resolves the
   displayed pixel size via a new None-safe `_get_pixel_size_um()` (folds
   `NotImplementedError`/`AttributeError` + missing factors into `None`) and
   skips the move with a warning instead of crashing.
2. **Ctrl+Scroll Z navigation (new feature)** — Ctrl+Wheel steps Z by
   `LIVE_VIEW_Z_STEP_UM`, Ctrl+Shift+Wheel by `LIVE_VIEW_Z_STEP_FAST_UM`.

## Files Created/Modified

### Created
- `tests/unit/squid/controllers/test_image_click_controller.py` (7)
- `tests/unit/squid/ui/test_image_display_eventfilter.py` (4)

### Modified
- `src/_def.py` — `LIVE_VIEW_Z_STEP_UM = 5.0`, `LIVE_VIEW_Z_STEP_FAST_UM = 40.0`.
- `src/squid/core/events.py` — new `ImageScrollZCommand(delta_um: float)`.
- `src/squid/ui/widgets/display/image_display.py` — `signal_z_um_delta` signal;
  event filter installed on the graphics viewport; `eventFilter` emits on
  Ctrl(+Shift)+Wheel and consumes the event, plain wheel falls through to zoom.
- `src/squid/ui/main_window.py` — bridge `signal_z_um_delta` → publish
  `ImageScrollZCommand`.
- `src/squid/backend/controllers/image_click_controller.py` — `_get_pixel_size_um()`
  None-safety (used by the click handler) + `@handles(ImageScrollZCommand)` Z
  handler (gate on click-to-move + mode gate, clamp to `SOFTWARE_POS_LIMIT.Z`,
  non-blocking `move_z`).

## Architecture Adaptations

- Upstream's inline `gui_hcs.move_z_from_scroll` / `move_from_click_image` map to
  arch_v2's `ImageClickController` (EventBus). The UI only emits a Qt signal;
  `main_window` bridges to an EventBus command; the controller does the move.
- Upstream's `Microscope.get_image_pixel_size_um()` None-contract is implemented
  as the controller's `_get_pixel_size_um()` (the controller already holds the
  objective store + camera service).
- **Deliberate scope decisions:**
  - Kept arch_v2's `ENABLE_CLICK_TO_MOVE_BY_DEFAULT` name (upstream renamed it to
    `ENABLE_CLICK_TO_MOVE` — cosmetic; used in 3 arch_v2 files).
  - Omitted upstream's preferences-dialog Z-step UI; the constants are already
    `.ini`-configurable via arch_v2's `_def` ConfigParser loader.
  - Stage-busy gating: arch_v2 StageService exposes no busy flag, so the handler
    relies on the global mode gate + controller lock + non-blocking move.

## Tests

11 tests, all green; `tests/integration/squid/test_application.py` (6) green
(full startup with the new signal bridge). Z handler: move within bounds, clamp
out-of-bounds, no-op when disabled / mode-gated. Click None-safety: ignored when
factor is None or the camera raises NotImplementedError; moves when available.
eventFilter: Ctrl/Ctrl+Shift emit ±step, zero-notch consumed, plain scroll
passes through.

## Audit

- [x] Logic matches upstream (both concerns)
- [x] arch_v2 EventBus/controller patterns followed
- [x] Tests added (11) + integration green
- [x] black-clean (added lines)
- [x] Scope decisions documented (no rename, no prefs UI, mode-gate vs busy)

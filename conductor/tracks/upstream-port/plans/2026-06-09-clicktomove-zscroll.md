# Click-to-Move pixel-size safety + Ctrl+Scroll Z navigation (9bee0201)

**Status:** COMPLETED (ported in 278b9fc8)
**Started:** 2026-06-09
**Completed:** 2026-06-09

Ports upstream #532. Two concerns, adapted to arch_v2's EventBus/controller
architecture (upstream did it inline in gui_hcs; arch_v2 routes through
ImageClickController).

## Upstream Commit
- [ ] `9bee0201` - feat: live view click-to-move fix and Ctrl+Scroll Z navigation (#532)

## Implementation Checklist

### Part A — click-to-move pixel-size None-safety
- [ ] `ImageClickController._on_image_clicked`: guard `get_pixel_size_factor()` /
      `get_pixel_size_binned_um()` returning None or raising
      NotImplementedError/AttributeError → skip the move with a warning
      (upstream's `Microscope.get_image_pixel_size_um()` None-contract, adapted
      into the controller).

### Part B — Ctrl+Scroll Z navigation (new feature)
- [ ] `_def.py`: add `LIVE_VIEW_Z_STEP_UM = 5.0`, `LIVE_VIEW_Z_STEP_FAST_UM = 40.0`
      (arch_v2's ConfigParser loader auto-applies `.ini` overrides for matching
      uppercase constants — no explicit load block needed).
- [ ] `core/events.py`: add `ImageScrollZCommand(Event)` with `delta_um: float`.
- [ ] `image_display.py ImageDisplayWindow`: `signal_z_um_delta = Signal(float)`;
      install an event filter on the graphics view's viewport; `eventFilter`
      emits `notches * step_um` on Ctrl+Wheel (Shift → fast step), consuming the
      event; plain wheel passes through (zoom).
- [ ] `main_window.py`: bridge `signal_z_um_delta` → publish `ImageScrollZCommand`.
- [ ] `ImageClickController`: `@handles(ImageScrollZCommand)` → gate on
      click-to-move + mode gate, clamp target to `SOFTWARE_POS_LIMIT.Z`, move_z
      non-blocking.

### Deliberate scope decisions
- **Constant NOT renamed:** keep arch_v2's `ENABLE_CLICK_TO_MOVE_BY_DEFAULT`
  (used in 3 files); upstream's rename to `ENABLE_CLICK_TO_MOVE` is cosmetic.
- **Preferences UI omitted:** upstream added a "Click to Move" preferences group
  (enable + Z step spinboxes). arch_v2's `.ini` loader already makes the Z-step
  constants configurable; the dialog is polish, deferred to keep scope focused.
- **Stage-busy gate:** arch_v2 StageService exposes no busy flag; rely on the
  existing mode gate + the controller lock + non-blocking move instead.

### Tests
- [ ] `ImageClickController` Z-scroll: moves within bounds, clamps out-of-bounds,
      no-op when click-to-move disabled. Plus pixel-size None-safety (no crash).
- [ ] `ImageDisplayWindow.eventFilter`: Ctrl/Ctrl+Shift wheel emits ±step;
      zero-notch consumed without emit; plain wheel passes through.

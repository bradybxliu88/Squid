# Squid Lite — Simplified Imaging Frontend (Design)

**Date:** 2026-06-09
**Status:** Design approved in brainstorming; pending spec review → implementation plan
**Branch:** `squid-lite-frontend` (new, replaces the existing frontend)

## Context

The current Squid frontend (`squid/ui/`) is a large, feature-dense High Content
Screening GUI: multipoint acquisition, wellplate navigation, stage control,
tracking, fluidics, focus lock, orchestration, and more. For routine bench work —
a lab worker taking multicolor pictures of a slide or a culture construct on a
simple epifluorescence scope — that surface area is overwhelming and slow to
operate.

**Squid Lite** is a brand-new, deliberately tiny frontend that does exactly one
thing well: live-view the available color channels, then capture and save a
multicolor image. Everything else is removed. It is touch- and mouse/keyboard-
friendly, visually modern ("Deep Focus" dark theme), and quick to operate.

The backend is already fully reusable: `ApplicationContext`
(`squid/application.py`) builds all hardware, services, and controllers with zero
Qt dependencies and already runs headless in the test harness. Squid Lite is
therefore almost pure UI — it attaches to the same backend through the EventBus
and a handful of services.

## Goals / Non-Goals

**Goals**
- Live view of one channel at a time, with instant channel switching.
- Per-channel exposure, analog gain, illumination intensity, and contrast
  (histogram), persisted to config across sessions.
- Multi-channel capture: check N channels, hit Capture, get one multicolor image.
- Capture result view: per-channel overlay toggles + per-channel contrast bounds,
  composite render, export overlay PNG.
- Auto-save raw 16-bit as a single multi-channel OME-TIFF, into a user-set folder
  with a user-set filename prefix — both reachable in one click from a header bar.
- Modern, simple, touchscreen-capable UI.

**Non-Goals (explicitly out of scope)**
- No stage/XY navigation, no focus/Z control, no autofocus.
- No multipoint/wellplate/tiling/z-stack acquisition.
- No tracking, fluidics, orchestration, confocal, or workflow scripting UI.
- No simultaneous multi-channel live (single-camera hardware → one channel lit at
  a time). Live always shows the single active channel.

## Decisions (locked in brainstorming)

| Topic | Decision |
|-------|----------|
| Toolkit | PyQt5, in-process, reuse existing backend |
| Theme | "Deep Focus" — near-black, glowing channel dots, instrument feel |
| Live model | One switchable live view (single active channel) |
| Channels | Dynamic from `channel_definitions.json` (405/488/561/638 + brightfield) |
| Binning | Global (one control, not per-channel) |
| Focus/stage | None |
| Settings persistence | Persist per-channel exposure/gain/intensity to config |
| Save format | One multi-channel OME-TIFF per capture (raw 16-bit) + optional overlay PNG |
| Save location | Header "save bar": directory chooser + filename prefix, always visible |
| Old UI | Removed; Squid Lite is the only frontend on this branch |

## The Two Screens

### Live screen (layout "B")
- Large live image (left) of the active channel, rendered with pyqtgraph.
- Channel panel (right): one row per enabled channel, each with:
  - a **checkbox** = "include in capture" (multi-select, independent set),
  - a **color dot** from the channel's `display_color`,
  - the channel name, and a **● live** marker on the active row.
  - Tapping a row makes it the active/live channel (switches illumination,
    applies its settings) and opens its controls below.
- Active-channel controls (right, below list): histogram with **auto/manual**
  contrast, plus exposure / gain / intensity sliders. Only the lit channel has a
  live histogram, so only the active channel's is shown here.
- Buttons: **▶ Live** (toggle streaming) and **📷 Capture N** (N = checked count).

### Capture / result screen
- Reached automatically after Capture; a **Live | Result** tab switch returns you.
- Multichannel composite (left), each channel pseudocolored by its `display_color`.
- Overlay panel (right): one row per captured channel with a **👁 visibility
  toggle** and a histogram with **two draggable handles** for contrast bounds.
- Raw OME-TIFF is already saved; an **Export overlay PNG** button writes the
  current rendered composite. Optional in-session "recent captures" thumbnail strip.

### Persistent header "save bar" (both screens)
- **Save directory** chooser (QFileDialog) + **filename prefix** text field, both
  always visible. Captures auto-name `{prefix}_{NNN}.ome.tiff` into that folder.
- Small right-aligned status: current objective + simulation indicator.

## Architecture

Squid Lite reuses the backend untouched and replaces only the UI layer.

```
main_lite.py  ──>  ApplicationContext (unchanged)
                       │  microscope / services / controllers / ui_event_bus
                       ▼
                 SquidLiteWindow (QMainWindow)   ← new
                   ├── SaveBar (dir + prefix)
                   ├── stacked views: LiveView | ResultView
                   │     ├── LiveImageWidget   (pyqtgraph, subscribes to frames)
                   │     ├── ChannelPanel      (chips: checkbox + active select)
                   │     ├── ChannelControls   (hist + exposure/gain/intensity)
                   │     └── ResultView        (composite + per-channel overlay)
                   └── CaptureController        (worker-thread snap loop)
```

### Attachment seam
- Modify `ApplicationContext.create_gui()` (`squid/application.py:1157`) to build
  `SquidLiteWindow(microscope, controllers, services, is_simulation)` instead of
  `HighContentScreeningGui`. `create_ui_event_bus()` is reused as-is (must be
  called on the Qt main thread after `QApplication`).
- New entry point `software/src/main_lite.py`, mirroring `main_hcs.py` for arg
  parsing (`--simulation`, `--skip-init`, `--verbose`) and QApplication setup.

### Reused UI infrastructure (keep these files)
- `squid/ui/ui_event_bus.py` — `UIEventBus` (thread-safe handler dispatch).
- `squid/ui/qt_event_dispatcher.py` — Qt-main-thread marshaling.
- `squid/ui/qt_stream_handler.py` — `QtStreamHandler`, exposes `image_to_display`
  Qt signal carrying the live numpy frame.
- `squid/ui/widgets/base.py` — `EventBusWidget` base (`@handles` subscriptions).

### Backend additions (land on `master` first, then build the UI on top)

To keep the UI thin and on-pattern (widgets publish commands / render state; no direct
service calls, threading, or file I/O), the capture orchestration and I/O live in the backend.
These have standalone value and target `master` independently of the frontend branch:

- **Fix** `ImageCaptureExecutor.apply_configuration` (`backend/controllers/multipoint/
  image_capture.py`): it reads `config.exposure_time_ms`, which no config model exposes
  (`ChannelMode.exposure_time`; `AcquisitionChannel.camera_settings.exposure_time_ms`). Align
  it with `MicroscopeModeController.apply_mode_for_acquisition` (`exposure_time` / `intensity`,
  hasattr-guarded). Latent today because callers set exposure separately — add a regression test.
- **`backend/io/writers/single_capture_ome_writer.py`** — multi-channel OME-TIFF writer for one
  snap (`CapturePlane`, `write_multichannel_ome_tiff`, `next_capture_path` numbering).
- **`backend/controllers/snap_controller.py`** — `SnapController.snap_multichannel(channel_names,
  objective) -> List[CameraFrame]` and a `@handles(CaptureMultichannelCommand)` handler that
  stops live → per-channel `apply_mode_for_acquisition` + illumination strobe → trigger/read →
  saves OME-TIFF → publishes `CaptureCompleted(save_path, channel_names)` → restarts live.
- **`core/events.py`** — `CaptureMultichannelCommand(channel_names, objective, save_directory,
  file_prefix)`, `CaptureCompleted(save_path, channel_names)`, `CaptureFailed(reason)`.
- **`channel_config_service`** — accessor returning `(name, display_color, ex_wavelength)` per
  enabled channel, so the UI doesn't reconcile `ChannelMode.color` vs `ChannelDefinition`.
- Wire `SnapController` into `Controllers` (`application.py`) and `_create_controllers_externally`.

The UI then just publishes `CaptureMultichannelCommand` and, on `CaptureCompleted`, reads the
saved OME-TIFF back for the result view. No numpy crosses `core` events; no I/O in widgets.

**Confirmed snap sequence (from spike, simulation):**
- Objective accessor: `microscope.objective_store.current_objective` (e.g. `"4x"`).
- Channels via `microscope.channel_config_service.get_configurations(objective, enabled_only=True)`
  → `ChannelMode` with `name, exposure_time, analog_gain, illumination_source,
  illumination_intensity, emission_filter_position, color`.
- Per-channel snap: `live.stop_live()` → `mode_controller.apply_mode_for_acquisition(name)` →
  `illumination.turn_on_channel(src)` → `camera.send_trigger()` (no `illumination_time` arg) →
  `camera.read_camera_frame()` → `illumination.turn_off_channel(src)`. `src` =
  `config.illumination_source`. Produces a lit frame (non-zero mean).
- Pixel format is **dtype-agnostic**: simulation yields MONO8 (uint8); real hardware MONO16
  (uint16). The writer must preserve whatever dtype the camera returns.
- Note: `BackendContext` exposes the manager as `microscope.channel_configuration_manager`;
  `microscope.channel_config_service` also resolves. Add the new accessor to the underlying
  `ChannelConfigurationManager`.

### New UI code (create)
- `squid/ui/lite/window.py` — `SquidLiteWindow`.
- `squid/ui/lite/save_bar.py` — directory + prefix header (pure UI state).
- `squid/ui/lite/live_image.py` — `LiveImageWidget` (pyqtgraph render).
- `squid/ui/lite/channel_panel.py` — `ChannelPanel` (include checkbox + active select).
- `squid/ui/lite/channel_controls.py` — `ChannelControls` (histogram + exposure/gain/intensity).
- `squid/ui/lite/result_view.py` — composite render + per-channel overlay controls.
- `squid/ui/lite/compositing.py` — pseudocolor LUT + contrast → RGB composite, PNG export.
- `squid/ui/lite/theme.qss` — "Deep Focus" stylesheet, applied at startup.

## Backend Reuse Map (concrete)

| Need | Use |
|------|-----|
| Live frames | `controllers.stream_handler` + `QtStreamHandler.image_to_display` |
| Start/stop live | `StartLiveCommand` / `StopLiveCommand` (or `controllers.live`) |
| Switch channel | `SetMicroscopeModeCommand(configuration_name, objective)` → `MicroscopeModeController` applies illumination/exposure/gain |
| Channel list + colors | `microscope.channel_config_service` → `get_configurations(objective, enabled_only=True)`, `get_channel_definitions()` (`display_color`, `ex_wavelength`) |
| Current objective | `microscope.objective_store` + `ObjectiveChanged` event |
| Exposure / gain / binning | `SetExposureTimeCommand`, `SetAnalogGainCommand`, `SetBinningCommand`; sync from `ExposureTimeChanged` / `AnalogGainChanged` / `BinningChanged` |
| Illumination intensity | `SetIlluminationCommand(channel, intensity, on)` or `IlluminationService.set_channel_power` / `turn_on_channel` |
| Persist channel settings | `UpdateChannelConfigurationCommand(objective_name, config_name, exposure_time_ms, analog_gain, illumination_intensity)` → `ChannelConfigurationManager` writes `channel_settings.json` |
| Snap one frame | `ImageCaptureExecutor` (`backend/controllers/multipoint/image_capture.py`): `apply_configuration(config)` + `capture_single_image()` → `CameraFrame.frame` (numpy) |
| Channel config object | `channel_config_service.get_channel_configuration_by_name(objective, name)` |

## Data Flow

**Live:** `StartLiveCommand` → LiveController streams → camera callback →
`StreamHandler` → `QtStreamHandler.image_to_display` → `LiveImageWidget` renders
active channel with its (auto/manual) contrast. Switching channels publishes
`SetMicroscopeModeCommand`; the controller relights/reconfigures; frames continue.

**Capture (worker thread, in `CaptureController`):**
1. Publish `StopLiveCommand` (camera needs exclusive triggered access).
2. For each checked channel: `executor.apply_configuration(config)` →
   `executor.capture_single_image()` → collect `frame.frame`.
3. Assemble channels → save one OME-TIFF (`{prefix}_{NNN}.ome.tiff`) via
   `lite_writer` with per-channel name/wavelength/color/exposure metadata.
4. Emit a Qt signal with the captured stack → `ResultView` displays it; switch to
   Result tab.
5. Restart live on the previously-active channel (`StartLiveCommand`).

**Contrast:** auto = per-channel percentile (e.g. 0.5–99.5%); manual = histogram
handle positions. Stored per channel; result view has its own independent bounds.

## Removal Plan (old UI)

Delete the domain widgets and main window; keep the reusable infra listed above.
- Remove: `squid/ui/main_window.py`, and `squid/ui/widgets/{camera,display,
  acquisition,stage,hardware,tracking,fluidics,orchestrator,workflow,wellplate}/`,
  plus `config.py`, `channel_config_dialogs.py`, and other HCS-only widgets.
- Leave the `ndviewer_light` submodule on disk but unreferenced.
- Update `ApplicationContext.create_gui()`; repurpose/replace `main_hcs.py` (new
  `main_lite.py` becomes the entry point).
- Remove or rewrite UI tests that import deleted widgets (`tests/unit`,
  `tests/integration`). Backend tests are unaffected.

## Testing & Verification

- **Manual (sim):** `cd software && python src/main_lite.py --simulation`
  - Verify: channels listed with colors; switch active channel changes live image;
    exposure/gain/intensity edits apply and persist after restart; check 3
    channels → Capture → Result shows composite; OME-TIFF written with correct
    name/prefix in chosen folder; overlay toggles + contrast handles work; Export
    PNG writes a file.
- **Unit:** `compositing` (pseudocolor + contrast → RGB), `lite_writer` (OME-TIFF
  shape/metadata), `save_bar` naming/increment, `CaptureController` loop with a
  mocked `ImageCaptureExecutor`.
- **Headless:** drive the capture path through the `BackendContext` harness
  (`tests/harness/core/backend_context.py`) to exercise stop-live → snap-loop →
  save without a GUI.

## Build Sequence (high level)

1. Branch `squid-lite-frontend`; add `main_lite.py`; stub `SquidLiteWindow`;
   wire `create_gui()`; confirm it launches against the backend in `--simulation`.
2. Live screen: `LiveImageWidget` (frame subscription + render) → `ChannelPanel`
   (list, active switch) → `ChannelControls` (histogram + sliders, persistence).
3. Capture path: `CaptureController` + `lite_writer`; auto-save; save bar.
4. Result screen: `compositing` + `ResultView` overlay controls + PNG export.
5. Theme (`theme.qss`); touch sizing pass.
6. Remove old UI; fix/trim tests; final sim walkthrough.

## Open Implementation Details (decide during build)

- Exact OME-TIFF assembly: reuse helpers in `utils_ome_tiff_writer.py` vs. a small
  self-contained `tifffile`-based writer (the multipoint writer is oriented around
  `CaptureInfo`/`AcquisitionInfo`; a single snap is simpler standalone).
- Whether capture should briefly engage the global mode gate / any acquisition
  mode the camera service expects when leaving live streaming.
- Brightfield rendering in the composite (grayscale vs. its `display_color`).

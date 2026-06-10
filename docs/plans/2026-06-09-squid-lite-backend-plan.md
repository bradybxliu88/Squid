# Squid Lite — Backend Capture Primitives (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable, headless-testable backend primitives for single multichannel "snap" capture — a `SnapController`, a single-capture OME-TIFF writer, supporting events, and a channel display-info accessor — and fix a latent config-apply bug. These land on `master` first; the Squid Lite frontend (separate plan) builds on top.

**Architecture:** Capture orchestration and file I/O live in the backend (layer 1), driven by EventBus commands so any frontend stays thin. `SnapController` reuses `MicroscopeModeController.apply_mode_for_acquisition` (synchronous mode-apply) and `LiveController.stop_live/start_live`, captures one frame per channel, writes one OME-TIFF, and publishes a completion event. Everything is verifiable headlessly via the `BackendContext` test harness.

**Tech Stack:** Python 3, numpy, tifffile 2023.2.28, existing `squid.core`/`squid.backend` (EventBus, services, controllers, `ImageCaptureExecutor`).

**Design spec:** `docs/plans/2026-06-09-squid-lite-frontend-design.md` (see "Backend additions").

**Branch / target:** Create `backend-snap-capture` off `master`; PR to `master` (NOT upstream/Cephla-Lab — per standing rule, no upstream PRs without explicit confirmation).

---

## Conventions

- Run from `software/`: `cd /Users/wea/src/allenlab/Squid/software`.
- Tests: `pytest tests/unit -v`, headless integration via `tests/harness/core/backend_context.py`.
- Known pre-existing failures (ignore): `test_checkpoint_contains_required_fields`,
  `test_resume_from_checkpoint_completes`, `test_camera_fault_*`.
- Verified facts:
  - `MicroscopeModeController.apply_mode_for_acquisition(mode: str)` synchronously sets
    exposure/gain/illumination-power/filter for a channel (`microscope_mode_controller.py:163`).
  - `LiveController.start_live(configuration=None)` / `stop_live()` are synchronous
    (`live_controller.py:304,373`); `is_live` reflects state.
  - `CameraService`: `send_trigger(illumination_time=None)`, `read_camera_frame() -> CameraFrame`
    (`.frame` ndarray), `get_exposure_time()`.
  - `IlluminationService`: `set_channel_power(channel, power)`, `turn_on_channel(channel)`,
    `turn_off_channel(channel)`.
  - `channel_config_service.get_configurations(objective, enabled_only=True) -> List[ChannelMode]`;
    `ChannelMode` has `name, exposure_time, analog_gain, illumination_source,
    illumination_intensity, emission_filter_position, color`.
  - `get_channel_definitions() -> ChannelDefinitionsConfig` with `numeric_channel_mapping` and a
    list of `ChannelDefinition` (`display_color`, `get_ex_wavelength(mapping)`).
  - Controllers take the global `event_bus` and subscribe via `@handles(...)`; built in
    `ApplicationContext._create_controllers_externally` (`application.py:387`) and registered in
    the `Controllers` dataclass (`application.py:56`).

---

## Task B0: Branch + baseline

- [ ] **Step 1: Branch from master**

```bash
cd /Users/wea/src/allenlab/Squid
git checkout master && git pull --ff-only
git checkout -b backend-snap-capture
```

- [ ] **Step 2: Confirm a clean baseline**

Run: `cd software && pytest -m "not slow" tests/unit -q`
Expected: passes except the known pre-existing failures above. Record the baseline count.

---

## Task B1: Capture-path spike (confirm the snap-with-illumination sequence)

Investigation only — determines the exact working sequence `SnapController` implements.

**Files:** Create (temporary): `software/scratch_snap_spike.py`

- [ ] **Step 1: Write the spike**

Create `software/scratch_snap_spike.py`:

```python
"""SPIKE (temporary): confirm the single-channel snap sequence in simulation."""
from squid.application import ApplicationContext

ctx = ApplicationContext(simulation=True)
try:
    scope = ctx.microscope
    cam = ctx.services.get("camera")
    illum = ctx.services.get("illumination")
    mode = ctx.controllers.microscope_mode
    live = ctx.controllers.live

    objective = scope.objective_store.current_objective       # confirm attribute name
    ccs = scope.channel_config_service
    configs = ccs.get_configurations(objective, enabled_only=True)
    print("OBJECTIVE:", objective, "CHANNELS:", [c.name for c in configs])

    live.stop_live()
    for c in configs[:2]:
        mode.apply_mode_for_acquisition(c.name)               # sets exp/gain/illum-power/filter
        src = c.illumination_source
        illum.turn_on_channel(src)                            # open shutter for the exposure
        cam.send_trigger()
        frame = cam.read_camera_frame()
        illum.turn_off_channel(src)
        print("SNAP:", c.name, None if frame is None else (frame.frame.shape, frame.frame.dtype,
              float(frame.frame.mean())))
finally:
    ctx.shutdown()
```

- [ ] **Step 2: Run and adjust until it works**

Run: `cd software && python scratch_snap_spike.py`
Expected: two `SNAP:` lines with non-None frames and a **non-zero mean** (proves illumination
reached the frame). If the mean is ~0, try `cam.send_trigger(illumination_time=<exposure_ms*1000>)`
or the live controller's strobing path; iterate until lit frames appear. Record:
- exact objective accessor, whether `apply_mode_for_acquisition` already opens the shutter
  (if so, drop the explicit `turn_on_channel`), and whether `send_trigger` needs `illumination_time`.

- [ ] **Step 3: Record findings + delete spike**

Append the confirmed sequence to the design spec's "Backend additions" notes, then:

```bash
rm software/scratch_snap_spike.py
git add docs/plans/2026-06-09-squid-lite-frontend-design.md
git commit -m "docs(snap): confirmed snap-per-channel sequence from spike"
```

---

## Task B2: Fix `ImageCaptureExecutor.apply_configuration` attribute inconsistency

**Files:**
- Modify: `software/src/squid/backend/controllers/multipoint/image_capture.py:145-183`
- Test: `software/tests/unit/backend/controllers/test_image_capture_apply_config.py`

- [ ] **Step 1: Write the failing test**

Create `software/tests/unit/backend/controllers/test_image_capture_apply_config.py`:

```python
from squid.backend.controllers.multipoint.image_capture import ImageCaptureExecutor


class _Cam:
    def __init__(self):
        self.exposure = None
        self.gain = None
    def set_exposure_time(self, v):
        self.exposure = v
    def set_analog_gain(self, v):
        self.gain = v


class _Cfg:
    """Mirrors ChannelMode's attribute names."""
    name = "Fluorescence 488 nm Ex"
    exposure_time = 33.0          # NOT exposure_time_ms
    analog_gain = 1.5
    illumination_source = 12
    illumination_intensity = 60.0
    emission_filter_position = None


def test_apply_configuration_sets_exposure_from_exposure_time():
    cam = _Cam()
    ex = ImageCaptureExecutor(cam, illumination_service=None, filter_wheel_service=None)
    ex.apply_configuration(_Cfg())
    assert cam.exposure == 33.0
    assert cam.gain == 1.5
```

- [ ] **Step 2: Run to verify failure**

Run: `cd software && pytest tests/unit/backend/controllers/test_image_capture_apply_config.py -v`
Expected: FAIL — `cam.exposure` is `None` (current code reads `exposure_time_ms`).
(Create `tests/unit/backend/controllers/__init__.py` only if the test tree uses package markers — match siblings.)

- [ ] **Step 3: Fix the exposure/gain block**

In `image_capture.py`, replace the camera-parameter block in `apply_configuration` (lines ~173-183):

```python
        # Set camera exposure and gain from config (align names with ChannelMode /
        # MicroscopeModeController.apply_mode_for_acquisition).
        try:
            exposure_ms = getattr(config, "exposure_time", None)
            if exposure_ms is None:
                exposure_ms = getattr(config, "exposure_time_ms", None)
            if exposure_ms is not None:
                self._camera.set_exposure_time(exposure_ms)

            gain = getattr(config, "analog_gain", None)
            if gain is not None:
                self._camera.set_analog_gain(gain)
        except Exception as e:
            _log.warning(f"Failed to set camera parameters: {e}")
```

Also align the illumination block (lines ~152-162) to accept either `illumination_intensity`
or `intensity`:

```python
                intensity = getattr(config, "illumination_intensity", None)
                if intensity is None:
                    intensity = getattr(config, "intensity", None)
                channel = getattr(config, "illumination_source", None)
```

- [ ] **Step 4: Run to verify pass + no regressions**

Run: `cd software && pytest tests/unit/backend/controllers/test_image_capture_apply_config.py -v`
Expected: PASS.
Run: `cd software && pytest -m "not slow" tests/unit -q`
Expected: same baseline as Task B0 (no new failures — confirms no caller depended on the old no-op).

- [ ] **Step 5: Commit**

```bash
git add software/src/squid/backend/controllers/multipoint/image_capture.py software/tests/unit/backend/controllers/test_image_capture_apply_config.py
git commit -m "fix(capture): apply_configuration reads exposure_time (was no-op exposure_time_ms)"
```

---

## Task B3: Single-capture OME-TIFF writer

**Files:**
- Create: `software/src/squid/backend/io/writers/single_capture_ome_writer.py`
- Test: `software/tests/unit/backend/io/test_single_capture_ome_writer.py`

- [ ] **Step 1: Write the failing tests**

Create `software/tests/unit/backend/io/test_single_capture_ome_writer.py`:

```python
import numpy as np
import tifffile

from squid.backend.io.writers.single_capture_ome_writer import (
    CapturePlane, write_multichannel_ome_tiff, next_capture_path,
)


def test_writes_cyx_stack(tmp_path):
    planes = [
        CapturePlane("405", np.zeros((4, 6), np.uint16), "#7C4DFF", 40.0, 405),
        CapturePlane("488", np.ones((4, 6), np.uint16), "#22D3EE", 20.0, 488),
    ]
    out = tmp_path / "slideA_000.ome.tiff"
    write_multichannel_ome_tiff(str(out), planes)
    arr = tifffile.imread(str(out))
    assert arr.shape == (2, 4, 6) and arr.dtype == np.uint16


def test_metadata_has_channel_names(tmp_path):
    planes = [CapturePlane("405", np.zeros((2, 2), np.uint16), "#7C4DFF", 40.0, 405),
              CapturePlane("BF", np.zeros((2, 2), np.uint16), "#FFFFFF", 5.0, None)]
    out = tmp_path / "x_000.ome.tiff"
    write_multichannel_ome_tiff(str(out), planes)
    with tifffile.TiffFile(str(out)) as tf:
        xml = tf.ome_metadata or ""
    assert "405" in xml and "BF" in xml


def test_next_capture_path_numbering(tmp_path):
    assert next_capture_path(str(tmp_path), "slideA").endswith("slideA_000.ome.tiff")
    (tmp_path / "slideA_000.ome.tiff").write_bytes(b"")
    assert next_capture_path(str(tmp_path), "slideA").endswith("slideA_001.ome.tiff")


def test_next_capture_path_sanitizes_blank_prefix(tmp_path):
    assert next_capture_path(str(tmp_path), "").endswith("capture_000.ome.tiff")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd software && pytest tests/unit/backend/io/test_single_capture_ome_writer.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `software/src/squid/backend/io/writers/single_capture_ome_writer.py`:

```python
"""Write a single multichannel capture as one OME-TIFF (C, Y, X)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import tifffile

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class CapturePlane:
    name: str
    data: np.ndarray            # 2D (Y, X)
    color: str                  # '#RRGGBB'
    exposure_ms: float
    wavelength: Optional[int]   # excitation nm; None for brightfield


def sanitize_prefix(prefix: str) -> str:
    cleaned = _SAFE.sub("_", (prefix or "").strip())
    return cleaned or "capture"


def next_capture_path(directory: str, prefix: str, ext: str = ".ome.tiff") -> str:
    """'<dir>/<prefix>_<NNN><ext>' with the lowest unused 3-digit index."""
    prefix = sanitize_prefix(prefix)
    n = 0
    while True:
        candidate = os.path.join(directory, f"{prefix}_{n:03d}{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def write_multichannel_ome_tiff(path: str, planes: List[CapturePlane]) -> None:
    if not planes:
        raise ValueError("write_multichannel_ome_tiff requires at least one plane")
    shape = planes[0].data.shape
    for p in planes:
        if p.data.shape != shape:
            raise ValueError(f"channel '{p.name}' shape {p.data.shape} != {shape}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    stack = np.stack([p.data for p in planes], axis=0)  # (C, Y, X)
    metadata = {"axes": "CYX", "Channel": {"Name": [p.name for p in planes]}}
    tifffile.imwrite(path, stack, photometric="minisblack", metadata=metadata, ome=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd software && pytest tests/unit/backend/io/test_single_capture_ome_writer.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add software/src/squid/backend/io/writers/single_capture_ome_writer.py software/tests/unit/backend/io/test_single_capture_ome_writer.py
git commit -m "feat(io): single-capture multichannel OME-TIFF writer"
```

---

## Task B4: Capture events

**Files:** Modify `software/src/squid/core/events.py` (commands section + state-events section).

- [ ] **Step 1: Add the events**

In `events.py`, in the command section (near `StartLiveCommand`), add:

```python
@dataclass
class CaptureMultichannelCommand(Event):
    """Command: capture one frame per named channel and save as a single OME-TIFF."""

    channel_names: List[str]
    objective: str
    save_directory: str
    file_prefix: str = "capture"
```

In the state-events section (near `LiveStateChanged`), add:

```python
@dataclass
class CaptureCompleted(Event):
    """Notification: a multichannel capture finished and was saved."""

    save_path: str
    channel_names: List[str]


@dataclass
class CaptureFailed(Event):
    """Notification: a multichannel capture failed."""

    reason: str
```

(Confirm `List` is imported at the top of `events.py`; it is used elsewhere, e.g.
`ChannelConfigurationsChanged`.)

- [ ] **Step 2: Import sanity check**

Run: `cd software && python -c "from squid.core.events import CaptureMultichannelCommand, CaptureCompleted, CaptureFailed; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add software/src/squid/core/events.py
git commit -m "feat(events): CaptureMultichannelCommand / CaptureCompleted / CaptureFailed"
```

---

## Task B5: Channel display-info accessor

**Files:**
- Modify: `software/src/squid/backend/managers/channel_configuration_manager.py`
- Test: `software/tests/unit/backend/managers/test_channel_display_info.py`

- [ ] **Step 1: Write the failing test (sim-backed)**

Create `software/tests/unit/backend/managers/test_channel_display_info.py`:

```python
from tests.harness.core.backend_context import BackendContext


def test_display_info_returns_color_and_wavelength():
    with BackendContext() as ctx:
        ccs = ctx.microscope.channel_config_service
        objective = ctx.microscope.objective_store.current_objective  # confirm accessor
        info = ccs.get_channel_display_info(objective)
        assert len(info) > 0
        for item in info:
            assert item.name
            assert item.color.startswith("#")
        # At least one fluorescence channel carries an excitation wavelength.
        assert any(i.ex_wavelength for i in info)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd software && pytest tests/unit/backend/managers/test_channel_display_info.py -v`
Expected: FAIL — `get_channel_display_info` does not exist.

- [ ] **Step 3: Implement**

In `channel_configuration_manager.py`, add (confirm the `ChannelDefinitionsConfig` field that
lists definitions — likely `.channels`; adjust if named differently):

```python
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ChannelDisplayInfo:
    name: str
    color: str
    ex_wavelength: Optional[int]


    # ... inside ChannelConfigurationManager:
    def get_channel_display_info(self, objective: str) -> List["ChannelDisplayInfo"]:
        """Per enabled channel: (name, display_color, excitation wavelength)."""
        defs = self.get_channel_definitions()
        mapping = defs.numeric_channel_mapping if defs else {}
        by_name = {c.name: c for c in (defs.channels if defs else [])}
        out: List[ChannelDisplayInfo] = []
        for mode in self.get_configurations(objective, enabled_only=True):
            cdef = by_name.get(mode.name)
            color = cdef.display_color if cdef else (getattr(mode, "color", None) or "#FFFFFF")
            wl = cdef.get_ex_wavelength(mapping) if cdef else None
            out.append(ChannelDisplayInfo(name=mode.name, color=color, ex_wavelength=wl))
        return out
```

- [ ] **Step 4: Run to verify pass**

Run: `cd software && pytest tests/unit/backend/managers/test_channel_display_info.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add software/src/squid/backend/managers/channel_configuration_manager.py software/tests/unit/backend/managers/test_channel_display_info.py
git commit -m "feat(channels): get_channel_display_info accessor (name/color/wavelength)"
```

---

## Task B6: `SnapController`

**Files:**
- Create: `software/src/squid/backend/controllers/snap_controller.py`
- Test: `software/tests/integration/test_snap_controller.py`

- [ ] **Step 1: Implement the controller**

Create `software/src/squid/backend/controllers/snap_controller.py`. Mirror
`microscope_mode_controller.py` for the base class + `@handles` subscription (copy its
`BaseController` import and `__init__` event-bus wiring exactly). Implement the **confirmed
sequence from Task B1** (drop the explicit shutter calls if the spike showed
`apply_mode_for_acquisition` already opens them):

```python
"""Single multichannel snap orchestration (stop live → snap each → save → resume)."""
from __future__ import annotations

from typing import List, Optional, Tuple

import squid.core.logging
from squid.core.events import (
    event_bus, handles,
    CaptureMultichannelCommand, CaptureCompleted, CaptureFailed,
)
# Base class: match MicroscopeModeController's base + import path.
from squid.backend.controllers.microscope_mode_controller import BaseController  # adjust import
from squid.backend.io.writers.single_capture_ome_writer import (
    CapturePlane, write_multichannel_ome_tiff, next_capture_path,
)

_log = squid.core.logging.get_logger(__name__)


class SnapController(BaseController):
    def __init__(self, camera_service, illumination_service, mode_controller,
                 live_controller, channel_config_service, event_bus=event_bus):
        super().__init__(event_bus)  # match MicroscopeModeController's super().__init__ signature
        self._camera = camera_service
        self._illumination = illumination_service
        self._mode = mode_controller
        self._live = live_controller
        self._ccs = channel_config_service

    def snap_multichannel(self, channel_names: List[str], objective: str) -> List[Tuple[str, object]]:
        """Return [(channel_name, CameraFrame), ...] for each successfully captured channel."""
        was_live = bool(getattr(self._live, "is_live", False))
        resume_to = getattr(self._mode, "current_mode", None)
        self._live.stop_live()
        results: List[Tuple[str, object]] = []
        try:
            for name in channel_names:
                self._mode.apply_mode_for_acquisition(name)
                src = self._illumination_source(objective, name)
                if src is not None:
                    self._illumination.turn_on_channel(src)
                try:
                    self._camera.send_trigger()
                    frame = self._camera.read_camera_frame()
                finally:
                    if src is not None:
                        self._illumination.turn_off_channel(src)
                if frame is not None:
                    results.append((name, frame))
                else:
                    _log.warning("Snap returned no frame for channel %s", name)
        finally:
            if was_live and resume_to:
                self._live.start_live(configuration=resume_to)
        return results

    def _illumination_source(self, objective: str, name: str) -> Optional[int]:
        cfg = self._ccs.get_channel_configuration_by_name(objective, name)
        return getattr(cfg, "illumination_source", None) if cfg else None

    @handles(CaptureMultichannelCommand)
    def _on_capture(self, cmd: CaptureMultichannelCommand) -> None:
        try:
            frames = self.snap_multichannel(cmd.channel_names, cmd.objective)
            if not frames:
                self._publish(CaptureFailed(reason="no frames captured"))
                return
            info = {i.name: i for i in self._ccs.get_channel_display_info(cmd.objective)}
            planes = []
            for name, frame in frames:
                di = info.get(name)
                cfg = self._ccs.get_channel_configuration_by_name(cmd.objective, name)
                planes.append(CapturePlane(
                    name=name,
                    data=frame.frame,
                    color=(di.color if di else "#FFFFFF"),
                    exposure_ms=float(getattr(cfg, "exposure_time", 0.0)) if cfg else 0.0,
                    wavelength=(di.ex_wavelength if di else None),
                ))
            path = next_capture_path(cmd.save_directory, cmd.file_prefix)
            write_multichannel_ome_tiff(path, planes)
            _log.info("Capture saved to %s", path)
            self._publish(CaptureCompleted(save_path=path, channel_names=[n for n, _ in frames]))
        except Exception as e:  # noqa: BLE001
            _log.exception("Multichannel capture failed")
            self._publish(CaptureFailed(reason=str(e)))
```

> Reconcile imports with `microscope_mode_controller.py`: use the same `BaseController` import
> path, the same `super().__init__(...)` form, and the same `self._publish`/`handles`/`event_bus`
> sources. If `MicroscopeModeController` imports `handles`/`event_bus` from a different module
> than `squid.core.events`, match it.

- [ ] **Step 2: Write the integration test**

Create `software/tests/integration/test_snap_controller.py`:

```python
import os
import tifffile

from tests.harness.core.backend_context import BackendContext
from squid.backend.controllers.snap_controller import SnapController


def _make_controller(ctx):
    return SnapController(
        camera_service=ctx.camera_service,
        illumination_service=ctx.microscope.services_or_illumination,  # adjust to harness accessor
        mode_controller=ctx.controllers.microscope_mode,              # adjust to harness accessor
        live_controller=ctx.controllers.live,                         # adjust to harness accessor
        channel_config_service=ctx.microscope.channel_config_service,
    )


def test_snap_multichannel_writes_ome_tiff(tmp_path):
    with BackendContext() as ctx:
        objective = ctx.microscope.objective_store.current_objective
        names = [c.name for c in
                 ctx.microscope.channel_config_service.get_configurations(objective, enabled_only=True)][:2]
        snap = _make_controller(ctx)
        frames = snap.snap_multichannel(names, objective)
        assert len(frames) == len(names)

        # Drive the event path end to end.
        from squid.core.events import CaptureMultichannelCommand
        snap._on_capture(CaptureMultichannelCommand(
            channel_names=names, objective=objective,
            save_directory=str(tmp_path), file_prefix="itest",
        ))
        files = [f for f in os.listdir(tmp_path) if f.endswith(".ome.tiff")]
        assert files, "no OME-TIFF written"
        arr = tifffile.imread(str(tmp_path / files[0]))
        assert arr.shape[0] == len(names)
```

> The harness accessors above (`ctx.camera_service`, `ctx.controllers.*`, illumination) are
> best-effort — open `tests/harness/core/backend_context.py` and use its real attributes. If the
> harness doesn't expose `microscope_mode`/`live`/`illumination`, construct them from
> `ctx.microscope`/`ctx`'s service registry, mirroring `ApplicationContext`.

- [ ] **Step 3: Run the integration test**

Run: `cd software && pytest tests/integration/test_snap_controller.py -v`
Expected: PASS — two frames captured, an OME-TIFF with shape `(2, H, W)` on disk.

- [ ] **Step 4: Commit**

```bash
git add software/src/squid/backend/controllers/snap_controller.py software/tests/integration/test_snap_controller.py
git commit -m "feat(snap): SnapController — multichannel snap + save, event-driven"
```

---

## Task B7: Wire `SnapController` into the application

**Files:** Modify `software/src/squid/application.py` (`Controllers` dataclass + `_create_controllers_externally`).

- [ ] **Step 1: Add to the `Controllers` dataclass**

In the `Controllers` dataclass (`application.py:56`), add:

```python
    snap: Optional["SnapController"] = None
```
Add a TYPE_CHECKING import for `SnapController` alongside the other controller imports.

- [ ] **Step 2: Construct it in `_create_controllers_externally`**

After `microscope_mode_controller` and `peripherals_controller` are created and before the
`Controllers(...)` container is built, add:

```python
        from squid.backend.controllers.snap_controller import SnapController
        snap_controller = SnapController(
            camera_service=camera_service,
            illumination_service=illumination_service,
            mode_controller=microscope_mode_controller,
            live_controller=live_controller,
            channel_config_service=self._microscope.channel_config_service,
            event_bus=event_bus,
        )
```
Then pass `snap=snap_controller` into the `Controllers(...)` constructor.

- [ ] **Step 3: Verify it builds + subscribes**

Run: `cd software && python -c "from squid.application import ApplicationContext; c=ApplicationContext(simulation=True); print(type(c.controllers.snap).__name__); c.shutdown()"`
Expected: prints `SnapController`, clean shutdown, no errors.

- [ ] **Step 4: Commit**

```bash
git add software/src/squid/application.py
git commit -m "feat(snap): wire SnapController into ApplicationContext"
```

---

## Task B8: End-to-end event test via BackendContext

**Files:** Test `software/tests/integration/test_capture_command_e2e.py`

- [ ] **Step 1: Write the test (publish command → file + CaptureCompleted)**

Create `software/tests/integration/test_capture_command_e2e.py`:

```python
import os
from squid.application import ApplicationContext
from squid.core.events import (
    event_bus, CaptureMultichannelCommand, CaptureCompleted, CaptureFailed,
)


def test_capture_command_writes_file_and_emits_completed(tmp_path):
    ctx = ApplicationContext(simulation=True)
    received = []
    event_bus.subscribe(CaptureCompleted, received.append)
    event_bus.subscribe(CaptureFailed, received.append)
    try:
        objective = ctx.microscope.objective_store.current_objective
        names = [c.name for c in
                 ctx.microscope.channel_config_service.get_configurations(objective, enabled_only=True)][:2]
        event_bus.publish(CaptureMultichannelCommand(
            channel_names=names, objective=objective,
            save_directory=str(tmp_path), file_prefix="e2e",
        ))
        # EventBus dispatches on a background thread — wait briefly for completion.
        import time
        for _ in range(100):
            if received:
                break
            time.sleep(0.05)
        assert received, "no CaptureCompleted/CaptureFailed received"
        assert isinstance(received[-1], CaptureCompleted), getattr(received[-1], "reason", "")
        assert os.path.exists(received[-1].save_path)
    finally:
        ctx.shutdown()
```

- [ ] **Step 2: Run**

Run: `cd software && pytest tests/integration/test_capture_command_e2e.py -v`
Expected: PASS — a `CaptureCompleted` arrives and its `save_path` exists.
(If `event_bus` isn't importable from `squid.core.events`, use the same accessor the controllers
use — match `microscope_mode_controller.py`'s import.)

- [ ] **Step 3: Commit**

```bash
git add software/tests/integration/test_capture_command_e2e.py
git commit -m "test(snap): end-to-end CaptureMultichannelCommand via EventBus"
```

---

## Task B9: Final checks + PR to master

- [ ] **Step 1: Full backend test pass**

Run: `cd software && pytest -m "not slow" tests/unit tests/integration -q`
Expected: baseline + the new tests pass; no new failures beyond the known pre-existing set.

- [ ] **Step 2: Push and open the PR (only when the user asks)**

```bash
git push -u origin backend-snap-capture
# Open a PR targeting master (NOT upstream/Cephla-Lab). Do this only on explicit request.
```

- [ ] **Step 3: Upstream tracking**

Note for the maintainer: this touches backend capture paths — follow the repo's normal
`conductor/tracks/upstream-port/` bookkeeping if/when relevant. Do not auto-open upstream PRs.

---

## Self-Review notes (coverage map)

| Backend requirement | Task |
|---------------------|------|
| Confirmed snap-with-illumination sequence | B1 |
| Fix config-apply exposure/gain bug + regression test | B2 |
| Single-capture multichannel OME-TIFF writer + numbering | B3 |
| Capture command/state events | B4 |
| Channel display-info (name/color/wavelength) accessor | B5 |
| `SnapController` (snap loop + save + event-driven) | B6 |
| Wire into ApplicationContext/Controllers | B7 |
| End-to-end EventBus capture test (headless) | B8 |
| Regression pass + PR to master | B9 |

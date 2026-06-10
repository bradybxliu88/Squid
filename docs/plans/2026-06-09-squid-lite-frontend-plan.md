# Squid Lite Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build "Squid Lite", a tiny PyQt5 frontend for routine multicolor imaging — switchable single-channel live view, multi-channel capture, and a result/overlay view — as a thin UI over the backend capture primitives (separate backend plan).

**Architecture:** The UI is pure-EventBus: it publishes `CaptureMultichannelCommand` and renders on `CaptureCompleted` (reading the saved OME-TIFF back for display). All orchestration and file I/O live in the backend `SnapController` / writer. Display-only logic (pseudocolor compositing, contrast) is unit-tested; PyQt widgets are verified manually in `--simulation`.

**Tech Stack:** Python 3, PyQt5/qtpy, pyqtgraph (live image + histograms), numpy (compositing), tifffile (read-back), existing Squid backend (EventBus, services, `SnapController`).

**Design spec:** `docs/plans/2026-06-09-squid-lite-frontend-design.md`
**Prerequisite:** the backend plan (`docs/plans/2026-06-09-squid-lite-backend-plan.md`) is merged to `master` — `SnapController`, the capture events, the single-capture OME writer, and `get_channel_display_info` must exist.

---

## Conventions

- Run from `software/`: `cd /Users/wea/src/allenlab/Squid/software`. App: `python src/main_lite.py --simulation`.
- New UI under `src/squid/ui/lite/`. Reuse (keep): `ui/ui_event_bus.py`, `qt_event_dispatcher.py`,
  `qt_stream_handler.py`, `widgets/base.py`.
- Verified facts (backend already provides):
  - `QtStreamHandler(handler=controllers.stream_handler)` → `image_to_display: Signal(np.ndarray)`.
  - Commands: `StartLiveCommand(configuration=None)`, `StopLiveCommand()`,
    `SetMicroscopeModeCommand(configuration_name, objective)`, `SetExposureTimeCommand(exposure_time_ms)`,
    `SetAnalogGainCommand(gain)`, `SetBinningCommand(binning_x, binning_y)`,
    `SetIlluminationCommand(channel, intensity, on)`,
    `UpdateChannelConfigurationCommand(objective_name, config_name, exposure_time_ms=, analog_gain=, illumination_intensity=)`,
    `CaptureMultichannelCommand(channel_names, objective, save_directory, file_prefix)`.
  - State events: `CaptureCompleted(save_path, channel_names)`, `CaptureFailed(reason)`,
    `MicroscopeModeChanged(...)`, `ExposureTimeChanged(exposure_time_ms)`.
  - `microscope.channel_config_service`: `get_configurations(objective, enabled_only=True)`,
    `get_channel_configuration_by_name(objective, name)`, `get_channel_display_info(objective)`
    → `[ChannelDisplayInfo(name, color, ex_wavelength)]`.
  - `microscope.objective_store.current_objective` (confirm exact accessor — surfaced in backend spike).
  - `UIEventBus.subscribe(EventType, handler)` marshals handlers to the Qt main thread.

---

## Task F0: Branch, entry point, window skeleton, attachment seam

**Files:**
- Create: `software/src/main_lite.py`, `software/src/squid/ui/lite/__init__.py`, `software/src/squid/ui/lite/window.py`
- Modify: `software/src/squid/application.py` (`create_gui`, ~line 1157)

- [ ] **Step 1: Branch from the updated master**

```bash
cd /Users/wea/src/allenlab/Squid
git checkout master && git pull --ff-only      # includes the merged backend plan
git checkout -b squid-lite-frontend
mkdir -p software/src/squid/ui/lite
```

- [ ] **Step 2: Package marker**

Create `software/src/squid/ui/lite/__init__.py`:

```python
"""Squid Lite — simplified imaging frontend."""
```

- [ ] **Step 3: Minimal window**

Create `software/src/squid/ui/lite/window.py`:

```python
"""Top-level window for the Squid Lite frontend."""
from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtWidgets import QMainWindow, QLabel, QWidget, QVBoxLayout

import squid.core.logging

if TYPE_CHECKING:
    from squid.backend.microscope import Microscope
    from squid.application import Controllers
    from squid.backend.services import ServiceRegistry
    from squid.ui.ui_event_bus import UIEventBus

_log = squid.core.logging.get_logger(__name__)


class SquidLiteWindow(QMainWindow):
    """Minimal, touch-friendly frontend for live view + multichannel capture."""

    def __init__(self, microscope: "Microscope", controllers: "Controllers",
                 services: "ServiceRegistry", event_bus: "UIEventBus",
                 is_simulation: bool = False) -> None:
        super().__init__()
        self._microscope = microscope
        self._controllers = controllers
        self._services = services
        self._bus = event_bus
        self._is_simulation = is_simulation

        self.setWindowTitle("Squid Lite" + (" — SIMULATION" if is_simulation else ""))
        self.resize(1280, 800)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel("Squid Lite — skeleton (live view next)"))
        self.setCentralWidget(central)
        _log.info("SquidLiteWindow constructed")
```

- [ ] **Step 4: Attachment seam**

Replace the body of `ApplicationContext.create_gui` (`application.py:1157`):

```python
    def create_gui(self) -> "SquidLiteWindow":
        """Create the Squid Lite frontend with pre-built backend components."""
        from squid.ui.lite.window import SquidLiteWindow

        assert self._microscope is not None, "Microscope must be built before GUI"
        event_bus = self.create_ui_event_bus()
        self._log.info("Creating Squid Lite GUI...")
        gui = SquidLiteWindow(
            microscope=self._microscope, controllers=self._controllers,
            services=self._services, event_bus=event_bus, is_simulation=self._simulation,
        )
        self._gui = gui
        return gui
```

- [ ] **Step 5: Entry point**

Create `software/src/main_lite.py`:

```python
"""Entry point for the Squid Lite frontend."""
import argparse
import sys

from qtpy.QtWidgets import QApplication

import squid.core.logging
from squid.application import ApplicationContext

_log = squid.core.logging.get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Squid Lite — simple imaging frontend")
    parser.add_argument("--simulation", action="store_true")
    parser.add_argument("--skip-init", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    context = ApplicationContext(simulation=args.simulation, skip_init=args.skip_init)
    try:
        gui = context.create_gui()
        gui.show()
        return app.exec_()
    finally:
        context.shutdown()


if __name__ == "__main__":
    sys.exit(main())
```
(Copy `main_hcs.py`'s exact `--verbose` log-level line if it has one.)

- [ ] **Step 6: Verify launch**

Run: `cd software && python src/main_lite.py --simulation`
Expected: window "Squid Lite — SIMULATION" with skeleton label, no exceptions. Close it.

- [ ] **Step 7: Commit**

```bash
git add software/src/main_lite.py software/src/squid/ui/lite/ software/src/squid/application.py
git commit -m "feat(lite): window skeleton + entry point + create_gui seam"
```

---

## Task F1: `compositing.py` — pseudocolor + contrast + screen blend (TDD)

**Files:** Create `software/src/squid/ui/lite/compositing.py`; Test `software/tests/unit/ui/lite/test_compositing.py`

- [ ] **Step 1: Write the failing tests**

Create `software/tests/unit/ui/lite/test_compositing.py`:

```python
import numpy as np
import pytest
from squid.ui.lite import compositing as C


def test_hex_to_rgb_pure_red():
    assert C.hex_to_rgb("#FF0000") == (1.0, 0.0, 0.0)


def test_apply_contrast_linear_scale():
    out = C.apply_contrast(np.array([[0, 5, 10]], np.uint16), 0.0, 10.0)
    np.testing.assert_allclose(out, [[0.0, 0.5, 1.0]], atol=1e-6)


def test_apply_contrast_degenerate_bounds_zeros():
    out = C.apply_contrast(np.array([[3, 7]], np.uint16), 5.0, 5.0)
    np.testing.assert_array_equal(out, np.zeros((1, 2), np.float32))


def test_colorize_red_only():
    rgb = C.colorize(np.array([[10]], np.uint16), "#FF0000", 0.0, 10.0)
    np.testing.assert_allclose(rgb, [[[1.0, 0.0, 0.0]]], atol=1e-6)


def test_composite_red_plus_green_is_yellow():
    red = C.colorize(np.full((1, 1), 10, np.uint16), "#FF0000", 0.0, 10.0)
    green = C.colorize(np.full((1, 1), 10, np.uint16), "#00FF00", 0.0, 10.0)
    np.testing.assert_allclose(C.composite([red, green]), [[[1.0, 1.0, 0.0]]], atol=1e-6)


def test_composite_empty_raises():
    with pytest.raises(ValueError):
        C.composite([])


def test_to_uint8_clips():
    np.testing.assert_array_equal(
        C.to_uint8(np.array([[[0.0, 0.5, 1.5]]], np.float32)), [[[0, 128, 255]]])


def test_auto_contrast_bounds_ordered():
    lo, hi = C.auto_contrast_bounds(np.arange(100, dtype=np.uint16).reshape(10, 10))
    assert lo < hi
```

- [ ] **Step 2: Run to verify failure**

Run: `cd software && pytest tests/unit/ui/lite/test_compositing.py -v`
Expected: FAIL — module not found. Create package markers if the test tree uses them:
`mkdir -p tests/unit/ui/lite && touch tests/unit/ui/__init__.py tests/unit/ui/lite/__init__.py`
(only if siblings under `tests/unit` use `__init__.py`).

- [ ] **Step 3: Implement**

Create `software/src/squid/ui/lite/compositing.py`:

```python
"""Pure-numpy pseudocolor compositing for the Squid Lite result view."""
from __future__ import annotations

from typing import List, Tuple

import numpy as np


def hex_to_rgb(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def apply_contrast(image: np.ndarray, low: float, high: float) -> np.ndarray:
    img = image.astype(np.float32)
    if high <= low:
        return np.zeros(img.shape, dtype=np.float32)
    return np.clip((img - low) / (high - low), 0.0, 1.0)


def colorize(image: np.ndarray, hex_color: str, low: float, high: float) -> np.ndarray:
    norm = apply_contrast(image, low, high)
    r, g, b = hex_to_rgb(hex_color)
    return np.stack([norm * r, norm * g, norm * b], axis=-1)


def composite(layers: List[np.ndarray]) -> np.ndarray:
    if not layers:
        raise ValueError("composite() requires at least one layer")
    out = np.zeros(layers[0].shape, dtype=np.float32)
    for layer in layers:
        out = 1.0 - (1.0 - out) * (1.0 - layer)
    return np.clip(out, 0.0, 1.0)


def to_uint8(rgb: np.ndarray) -> np.ndarray:
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def auto_contrast_bounds(image: np.ndarray, low_pct: float = 0.5, high_pct: float = 99.5) -> Tuple[float, float]:
    lo = float(np.percentile(image, low_pct))
    hi = float(np.percentile(image, high_pct))
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi
```

- [ ] **Step 4: Run to verify pass**

Run: `cd software && pytest tests/unit/ui/lite/test_compositing.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add software/src/squid/ui/lite/compositing.py software/tests/unit/ui/lite/
git commit -m "feat(lite): pseudocolor compositing module"
```

---

## Task F2: `SaveBar` widget (directory + prefix; backend owns numbering)

**Files:** Create `software/src/squid/ui/lite/save_bar.py`

- [ ] **Step 1: Implement**

Create `software/src/squid/ui/lite/save_bar.py`:

```python
"""Persistent header: save directory chooser + filename prefix (UI state only)."""
from __future__ import annotations

from typing import Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog


class SaveBar(QWidget):
    """Holds the save directory + filename prefix; passed into CaptureMultichannelCommand."""

    directory_changed = Signal(str)

    def __init__(self, initial_directory: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._directory = initial_directory
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        self._dir_button = QPushButton("📁 Save to…")
        self._dir_button.clicked.connect(self._choose_directory)
        self._dir_label = QLabel(self._directory)
        self._dir_label.setObjectName("saveDirLabel")
        layout.addWidget(self._dir_button)
        layout.addWidget(self._dir_label, stretch=1)
        layout.addWidget(QLabel("Prefix:"))
        self._prefix_edit = QLineEdit("capture")
        self._prefix_edit.setMaximumWidth(220)
        layout.addWidget(self._prefix_edit)

    def _choose_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose save folder", self._directory)
        if chosen:
            self._directory = chosen
            self._dir_label.setText(chosen)
            self.directory_changed.emit(chosen)

    @property
    def directory(self) -> str:
        return self._directory

    @property
    def prefix(self) -> str:
        return self._prefix_edit.text()
```

- [ ] **Step 2: Smoke test (import)**

Run: `cd software && python -c "from squid.ui.lite.save_bar import SaveBar; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add software/src/squid/ui/lite/save_bar.py
git commit -m "feat(lite): save bar (directory + filename prefix)"
```

---

## Task F3: `LiveImageWidget` — pyqtgraph render + frame subscription

UI widget — verified manually.

**Files:** Create `software/src/squid/ui/lite/live_image.py`; Modify `window.py`

- [ ] **Step 1: Implement**

Create `software/src/squid/ui/lite/live_image.py`:

```python
"""Live image display for Squid Lite, backed by pyqtgraph."""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pyqtgraph as pg
from qtpy.QtWidgets import QWidget, QVBoxLayout


class LiveImageWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = pg.GraphicsLayoutWidget()
        self._vb = self._view.addViewBox()
        self._vb.setAspectLocked(True)
        self._vb.invertY(True)
        self._img = pg.ImageItem(axisOrder="row-major")
        self._vb.addItem(self._img)
        layout.addWidget(self._view)
        self._levels: Optional[Tuple[float, float]] = None
        self._auto = True

    @property
    def image_item(self) -> pg.ImageItem:
        return self._img

    def set_auto_contrast(self, enabled: bool) -> None:
        self._auto = enabled

    def set_levels(self, low: float, high: float) -> None:
        self._auto = False
        self._levels = (low, high)
        self._img.setLevels((low, high))

    def update_frame(self, frame: np.ndarray) -> None:
        """Slot for QtStreamHandler.image_to_display (runs on the GUI thread)."""
        if self._auto:
            lo = float(np.percentile(frame, 0.5))
            hi = float(np.percentile(frame, 99.5))
            if hi <= lo:
                hi = lo + 1.0
            self._levels = (lo, hi)
            self._img.setImage(frame, autoLevels=False, levels=(lo, hi))
        else:
            self._img.setImage(frame, autoLevels=False, levels=self._levels)
```

- [ ] **Step 2: Wire into the window**

In `window.py`, import and connect:

```python
from squid.ui.qt_stream_handler import QtStreamHandler
from squid.ui.lite.live_image import LiveImageWidget
```
In `__init__`, replace the placeholder central widget:

```python
        self._live_image = LiveImageWidget()
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._live_image)
        self.setCentralWidget(central)
        self._qt_stream = QtStreamHandler(handler=controllers.stream_handler)
        self._qt_stream.image_to_display.connect(self._live_image.update_frame)
```

- [ ] **Step 3: Verify**

Run: `cd software && python src/main_lite.py --simulation`
Expected: window shows an empty pyqtgraph viewbox, no exceptions. (Live frames appear after
Task F4 starts live.)

- [ ] **Step 4: Commit**

```bash
git add software/src/squid/ui/lite/live_image.py software/src/squid/ui/lite/window.py
git commit -m "feat(lite): live image widget wired to stream handler"
```

---

## Task F4: `ChannelPanel` — list, include-set, active switch

UI widget — verified manually.

**Files:** Create `software/src/squid/ui/lite/channel_panel.py`; Modify `window.py`

- [ ] **Step 1: Implement**

Create `software/src/squid/ui/lite/channel_panel.py`:

```python
"""Channel list: include-in-capture checkboxes + active-channel selection."""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton, QLabel


class ChannelRow(QWidget):
    activated = Signal(str)
    include_toggled = Signal(str, bool)

    def __init__(self, name: str, color_hex: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._name = name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self._include = QCheckBox()
        self._include.toggled.connect(lambda on: self.include_toggled.emit(self._name, on))
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color_hex}; font-size: 16px;")
        self._select = QPushButton(name)
        self._select.setFlat(True)
        self._select.clicked.connect(lambda: self.activated.emit(self._name))
        self._live = QLabel("")
        self._live.setObjectName("liveFlag")
        layout.addWidget(self._include)
        layout.addWidget(dot)
        layout.addWidget(self._select, stretch=1)
        layout.addWidget(self._live)

    def set_active(self, active: bool) -> None:
        self._live.setText("● live" if active else "")
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)


class ChannelPanel(QWidget):
    active_changed = Signal(str)
    capture_set_changed = Signal(set)

    def __init__(self, channels: List[dict], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: Dict[str, ChannelRow] = {}
        self._included: Set[str] = set()
        self._active: Optional[str] = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Channels"))
        for ch in channels:
            row = ChannelRow(ch["name"], ch["color"])
            row.activated.connect(self._on_activate)
            row.include_toggled.connect(self._on_include)
            layout.addWidget(row)
            self._rows[ch["name"]] = row
        layout.addStretch(1)

    def _on_activate(self, name: str) -> None:
        self._active = name
        for n, row in self._rows.items():
            row.set_active(n == name)
        self.active_changed.emit(name)

    def _on_include(self, name: str, on: bool) -> None:
        (self._included.add if on else self._included.discard)(name)
        self.capture_set_changed.emit(set(self._included))

    @property
    def capture_set(self) -> Set[str]:
        return set(self._included)

    @property
    def active(self) -> Optional[str]:
        return self._active
```

- [ ] **Step 2: Build the panel + start live in the window**

In `window.py`:

```python
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QSplitter
from squid.core.events import SetMicroscopeModeCommand, StartLiveCommand
from squid.ui.lite.channel_panel import ChannelPanel
```
```python
        self._objective = self._microscope.objective_store.current_objective
        ccs = self._microscope.channel_config_service
        info = ccs.get_channel_display_info(self._objective)
        channels = [{"name": i.name, "color": i.color} for i in info]
        self._channel_panel = ChannelPanel(channels)
        self._channel_panel.active_changed.connect(self._on_active_channel)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._live_image)
        splitter.addWidget(self._channel_panel)
        splitter.setStretchFactor(0, 1)
        self.setCentralWidget(splitter)   # (replaced by full layout in Task F7)
```
```python
    def _on_active_channel(self, name: str) -> None:
        self._bus.publish(SetMicroscopeModeCommand(configuration_name=name, objective=self._objective))
        self._bus.publish(StartLiveCommand(configuration=name))
```

- [ ] **Step 3: Verify**

Run: `cd software && python src/main_lite.py --simulation`
Expected: channels listed with colored dots; tapping one starts live and the **image updates**;
the row shows "● live"; checking boxes updates the capture set.

- [ ] **Step 4: Commit**

```bash
git add software/src/squid/ui/lite/channel_panel.py software/src/squid/ui/lite/window.py
git commit -m "feat(lite): channel panel with active switch + capture set"
```

---

## Task F5: `ChannelControls` — histogram + exposure/gain/intensity + persistence

UI widget + one unit test for the persistence command.

**Files:** Create `software/src/squid/ui/lite/channel_controls.py`; Test `.../test_channel_controls.py`; Modify `window.py`

- [ ] **Step 1: Write the failing persistence test**

Create `software/tests/unit/ui/lite/test_channel_controls.py`:

```python
from squid.ui.lite.channel_controls import build_persist_command
from squid.core.events import UpdateChannelConfigurationCommand


def test_build_persist_command_sets_fields():
    cmd = build_persist_command("10x", "Fluorescence 488 nm Ex", 33.0, 1.5, 60.0)
    assert isinstance(cmd, UpdateChannelConfigurationCommand)
    assert cmd.objective_name == "10x"
    assert cmd.config_name == "Fluorescence 488 nm Ex"
    assert cmd.exposure_time_ms == 33.0
    assert cmd.analog_gain == 1.5
    assert cmd.illumination_intensity == 60.0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd software && pytest tests/unit/ui/lite/test_channel_controls.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement**

Create `software/src/squid/ui/lite/channel_controls.py`:

```python
"""Active-channel controls: histogram + exposure/gain/intensity, with persistence."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Signal
from qtpy.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox, QPushButton

from squid.core.events import (
    UpdateChannelConfigurationCommand, SetExposureTimeCommand,
    SetAnalogGainCommand, SetIlluminationCommand,
)


def build_persist_command(objective, config_name, exposure_ms, gain, intensity):
    return UpdateChannelConfigurationCommand(
        objective_name=objective, config_name=config_name,
        exposure_time_ms=exposure_ms, analog_gain=gain, illumination_intensity=intensity)


class ChannelControls(QWidget):
    contrast_changed = Signal(float, float)

    def __init__(self, publish, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._publish = publish
        self._objective: Optional[str] = None
        self._channel: Optional[str] = None
        self._illum_source: Optional[int] = None
        layout = QVBoxLayout(self)
        self._title = QLabel("—")
        layout.addWidget(self._title)
        self._hist = pg.HistogramLUTWidget()
        self._hist.item.sigLevelsChanged.connect(self._on_levels_changed)
        layout.addWidget(self._hist)
        self._exposure = self._spin("Exposure (ms)", 0.01, 5000.0, layout)
        self._gain = self._spin("Gain", 0.0, 100.0, layout)
        self._intensity = self._spin("Intensity (%)", 0.0, 100.0, layout)
        self._exposure.valueChanged.connect(self._on_exposure)
        self._gain.valueChanged.connect(self._on_gain)
        self._intensity.valueChanged.connect(self._on_intensity)
        auto = QPushButton("Auto contrast")
        auto.clicked.connect(self._auto_contrast)
        layout.addWidget(auto)

    def _spin(self, label, lo, hi, layout) -> QDoubleSpinBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        row.addWidget(spin)
        layout.addLayout(row)
        return spin

    def attach_image_item(self, image_item) -> None:
        self._hist.setImageItem(image_item)

    def set_channel(self, objective: str, channel_config) -> None:
        self._objective = objective
        self._channel = channel_config.name
        self._illum_source = channel_config.illumination_source
        self._title.setText(channel_config.name)
        for spin, val in ((self._exposure, channel_config.exposure_time),
                          (self._gain, channel_config.analog_gain),
                          (self._intensity, channel_config.illumination_intensity)):
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

    def _on_exposure(self, v):
        self._publish(SetExposureTimeCommand(exposure_time_ms=v)); self._persist()

    def _on_gain(self, v):
        self._publish(SetAnalogGainCommand(gain=v)); self._persist()

    def _on_intensity(self, v):
        if self._illum_source is not None:
            self._publish(SetIlluminationCommand(channel=self._illum_source, intensity=v, on=True))
        self._persist()

    def _persist(self):
        if self._objective and self._channel:
            self._publish(build_persist_command(self._objective, self._channel,
                self._exposure.value(), self._gain.value(), self._intensity.value()))

    def _on_levels_changed(self):
        lo, hi = self._hist.item.getLevels()
        self.contrast_changed.emit(float(lo), float(hi))

    def _auto_contrast(self):
        img = self._hist.item.imageItem()
        if img is not None and img.image is not None:
            lo = float(np.percentile(img.image, 0.5))
            hi = float(np.percentile(img.image, 99.5))
            self._hist.item.setLevels(lo, max(hi, lo + 1.0))
```

- [ ] **Step 4: Run the unit test**

Run: `cd software && pytest tests/unit/ui/lite/test_channel_controls.py -v`
Expected: PASS.

- [ ] **Step 5: Wire under the panel**

In `window.py`: construct `ChannelControls(self._bus.publish)`, place below the channel panel,
`attach_image_item(self._live_image.image_item)`, and on `active_changed` load the config via
`ccs.get_channel_configuration_by_name(self._objective, name)` → `set_channel(...)`; connect
`contrast_changed` → `self._live_image.set_levels`.

- [ ] **Step 6: Verify**

Run: `cd software && python src/main_lite.py --simulation`
Expected: selecting a channel loads its exposure/gain/intensity; changing exposure changes live
brightness; histogram tracks live; "Auto contrast" rescales. Restart → an edited exposure
persisted (written to `channel_settings.json`).

- [ ] **Step 7: Commit**

```bash
git add software/src/squid/ui/lite/channel_controls.py software/tests/unit/ui/lite/test_channel_controls.py software/src/squid/ui/lite/window.py
git commit -m "feat(lite): active-channel controls with live publish + persistence"
```

---

## Task F6: `ResultView` — composite + per-channel overlay + export PNG

UI widget — verified manually; reuses `compositing`.

**Files:** Create `software/src/squid/ui/lite/result_view.py`

- [ ] **Step 1: Implement**

Create `software/src/squid/ui/lite/result_view.py`:

```python
"""Capture result: composite render + per-channel overlay toggles + PNG export."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import imageio
import numpy as np
import pyqtgraph as pg
from qtpy.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QCheckBox, QPushButton, QFileDialog,
)

from squid.ui.lite import compositing as C


@dataclass
class CapturedChannel:
    name: str
    data: np.ndarray
    color: str
    low: float
    high: float
    visible: bool = True


class ResultView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._channels: List[CapturedChannel] = []
        self._rows: Dict[str, QWidget] = {}
        root = QHBoxLayout(self)
        self._view = pg.GraphicsLayoutWidget()
        vb = self._view.addViewBox()
        vb.setAspectLocked(True)
        vb.invertY(True)
        self._img = pg.ImageItem(axisOrder="row-major")
        vb.addItem(self._img)
        root.addWidget(self._view, stretch=1)
        panel_widget = QWidget()
        self._panel = QVBoxLayout(panel_widget)
        self._panel.addWidget(QLabel("Overlay channels"))
        panel_widget.setMaximumWidth(280)
        root.addWidget(panel_widget)
        self._export = QPushButton("⬇ Export overlay PNG")
        self._export.clicked.connect(self._export_png)
        self._panel.addWidget(self._export)

    def set_capture(self, channels: List[CapturedChannel]) -> None:
        self._channels = channels
        for w in self._rows.values():
            w.setParent(None)
        self._rows.clear()
        for ch in channels:
            row = QWidget()
            h = QHBoxLayout(row)
            cb = QCheckBox()
            cb.setChecked(ch.visible)
            cb.toggled.connect(lambda on, c=ch: self._set_visible(c, on))
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {ch.color}; font-size: 16px;")
            h.addWidget(cb)
            h.addWidget(dot)
            h.addWidget(QLabel(ch.name), stretch=1)
            self._panel.insertWidget(self._panel.count() - 1, row)  # above export
            self._rows[ch.name] = row
        self._render()

    def _set_visible(self, ch: CapturedChannel, on: bool) -> None:
        ch.visible = on
        self._render()

    def _render(self) -> None:
        layers = [C.colorize(c.data, c.color, c.low, c.high) for c in self._channels if c.visible]
        if not layers:
            self._img.clear()
            return
        self._img.setImage(C.to_uint8(C.composite(layers)), autoLevels=False)

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export overlay PNG", "overlay.png", "PNG (*.png)")
        if not path:
            return
        layers = [C.colorize(c.data, c.color, c.low, c.high) for c in self._channels if c.visible]
        if layers:
            imageio.imwrite(path, C.to_uint8(C.composite(layers)))
```

- [ ] **Step 2: Smoke test (import)**

Run: `cd software && python -c "from squid.ui.lite.result_view import ResultView, CapturedChannel; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add software/src/squid/ui/lite/result_view.py
git commit -m "feat(lite): result view with overlay toggles + PNG export"
```

---

## Task F7: Assemble window — save bar, tabs, capture command, result on completion

**Files:** Modify `software/src/squid/ui/lite/window.py`

- [ ] **Step 1: Layout — save bar on top, Live/Result stack**

Rebuild the central widget: `SaveBar` across the top; a `QStackedWidget` with page 0 = live
screen (`QSplitter`: live image | [channel panel + controls + a "Capture N" button]) and page 1
= `ResultView`; a Live/Result segmented toggle drives the stack. Default the save directory to
`os.path.expanduser("~/SquidLite")`.

```python
import os
from qtpy.QtWidgets import QStackedWidget, QWidget, QVBoxLayout, QPushButton
from squid.core.events import (
    CaptureMultichannelCommand, CaptureCompleted, CaptureFailed,
)
from squid.ui.lite.save_bar import SaveBar
from squid.ui.lite.result_view import ResultView, CapturedChannel
from squid.ui.lite import compositing as C
```

- [ ] **Step 2: Capture button → publish command**

Add a "Capture" button whose label tracks the capture-set count
(`capture_set_changed` → `btn.setText(f"📷 Capture {len(s)}")`). On click:

```python
    def _on_capture_clicked(self) -> None:
        names = sorted(self._channel_panel.capture_set)
        if not names:
            return
        self._bus.publish(CaptureMultichannelCommand(
            channel_names=names,
            objective=self._objective,
            save_directory=self._save_bar.directory,
            file_prefix=self._save_bar.prefix,
        ))
```

- [ ] **Step 3: Subscribe to completion (read back + show result)**

In `__init__`, subscribe via the marshaling bus (handlers run on the GUI thread):

```python
        self._bus.subscribe(CaptureCompleted, self._on_capture_completed)
        self._bus.subscribe(CaptureFailed, self._on_capture_failed)
        self._color_by_name = {i.name: i.color for i in info}  # from get_channel_display_info
```
```python
    def _on_capture_completed(self, evt) -> None:
        import tifffile
        stack = tifffile.imread(evt.save_path)          # (C, Y, X)
        if stack.ndim == 2:
            stack = stack[None, ...]
        captured = []
        for idx, name in enumerate(evt.channel_names):
            data = stack[idx]
            lo, hi = C.auto_contrast_bounds(data)
            captured.append(CapturedChannel(name, data, self._color_by_name.get(name, "#FFFFFF"), lo, hi))
        self._result_view.set_capture(captured)
        self._show_result_tab()

    def _on_capture_failed(self, evt) -> None:
        from qtpy.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Capture failed", evt.reason)
```

> The backend (`SnapController`) stops live, captures, saves, and restarts live on its own — the
> UI does no orchestration or threading. It only publishes the command and renders the result.

- [ ] **Step 4: Full simulation walkthrough**

Run: `cd software && python src/main_lite.py --simulation`
Verify end to end:
1. Tap a channel → live updates; adjust exposure/intensity → live responds.
2. Check 3 channels → button reads "Capture 3" → click.
3. App switches to Result; composite shows 3 pseudocolored channels.
4. Toggle a channel off → composite updates; Export PNG writes a file.
5. `{prefix}_{NNN}.ome.tiff` exists in `~/SquidLite`; verify:
   `python -c "import tifffile; print(tifffile.imread('<path>').shape)"` → `(3, H, W)`.
6. Back to Live → live resumed on the previously active channel.

- [ ] **Step 5: Commit**

```bash
git add software/src/squid/ui/lite/window.py
git commit -m "feat(lite): assemble window — save bar, tabs, capture command + result"
```

---

## Task F8: Theme + touch sizing + global binning control

**Files:** Create `software/src/squid/ui/lite/theme.qss`; Modify `main_lite.py`, `window.py`

- [ ] **Step 1: "Deep Focus" stylesheet**

Create `software/src/squid/ui/lite/theme.qss` — near-black background, teal accent `#2dd4bf`,
control `min-height: 36px` for touch, rounded corners, a `[active="true"]` rule for the live
channel row, and `#liveFlag` styling. Keep it concise.

- [ ] **Step 2: Apply at startup**

In `main_lite.py`, after `app = QApplication(...)`:

```python
    import os
    from squid.ui.lite import __file__ as _lite_init
    qss_path = os.path.join(os.path.dirname(_lite_init), "theme.qss")
    with open(qss_path, "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())
```

- [ ] **Step 3: Add the global binning control**

In `window.py`, add a small binning dropdown to the save bar row or a corner of the live screen:

```python
from qtpy.QtWidgets import QComboBox
from squid.core.events import SetBinningCommand
# options from camera_service.get_binning_options(); on change:
#   b = combo.currentData(); self._bus.publish(SetBinningCommand(binning_x=b[0], binning_y=b[1]))
```
Populate from `self._services.get("camera").get_binning_options()`.

- [ ] **Step 4: Verify**

Run: `cd software && python src/main_lite.py --simulation`
Expected: dark instrument theme; glowing channel dots; touch-sized controls; active row
highlighted; binning dropdown changes binning (live frame size updates).

- [ ] **Step 5: Commit**

```bash
git add software/src/squid/ui/lite/theme.qss software/src/main_lite.py software/src/squid/ui/lite/window.py
git commit -m "feat(lite): Deep Focus theme, touch sizing, global binning control"
```

---

## Task F9: Remove the old frontend

**Files:**
- Delete: `software/src/squid/ui/main_window.py`, `software/src/squid/ui/widgets/{camera,display,acquisition,stage,hardware,tracking,fluidics,orchestrator,workflow,wellplate}/`, HCS-only widgets (`config.py`, `channel_config_dialogs.py`, `image_saver.py` if unused).
- Keep: `ui/ui_event_bus.py`, `qt_event_dispatcher.py`, `qt_stream_handler.py`, `widgets/base.py`, the new `lite/` package.
- Modify: `main_hcs.py` (delete or shim to `main_lite`), `application.py` (drop `HighContentScreeningGui` refs), `CLAUDE.md` "Run" section → `main_lite.py`.

- [ ] **Step 1: Find remaining references**

Run: `cd software && grep -rl "ui.main_window\|HighContentScreeningGui\|ui.widgets.camera\|ui.widgets.display\|ui.widgets.acquisition" src tests`
Record every hit.

- [ ] **Step 2: Delete old widgets + window (verify each path exists first)**

```bash
cd software
git rm src/squid/ui/main_window.py
git rm -r src/squid/ui/widgets/camera src/squid/ui/widgets/display src/squid/ui/widgets/acquisition \
         src/squid/ui/widgets/stage src/squid/ui/widgets/hardware src/squid/ui/widgets/tracking \
         src/squid/ui/widgets/fluidics src/squid/ui/widgets/orchestrator src/squid/ui/widgets/workflow \
         src/squid/ui/widgets/wellplate
```
Leave `widgets/base.py`, `widgets/__init__.py`, and the `ndviewer_light` submodule in place.

- [ ] **Step 3: Fix lingering imports**

Clean `widgets/__init__.py` and every grep hit; drop the `HighContentScreeningGui` TYPE_CHECKING
import in `application.py`. Delete or shim `main_hcs.py` (`git rm`, or replace its body with
`from main_lite import main; ...`). Update `CLAUDE.md` "Run" commands to `main_lite.py`.

- [ ] **Step 4: Trim UI tests**

Delete/rewrite tests importing removed widgets (grep from Step 1, in `tests/unit`,
`tests/integration`). Leave backend tests untouched.

- [ ] **Step 5: Verify clean**

```bash
cd software
grep -rl "HighContentScreeningGui\|ui.main_window" src tests   # expect no output
python src/main_lite.py --simulation                            # still launches + full flow
pytest tests/unit/ui/lite -v                                    # lite unit tests pass
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(lite): remove legacy HCS frontend; Squid Lite is the only UI"
```

---

## Task F10: Final integration check

- [ ] **Step 1: Lite unit tests**

Run: `cd software && pytest tests/unit/ui/lite -v` → all PASS.

- [ ] **Step 2: Backend regression**

Run: `cd software && pytest -m "not slow" tests/unit tests/integration -q`
Expected: no new failures beyond the known pre-existing set.

- [ ] **Step 3: Final manual walkthrough**

Repeat Task F7 Step 4 end-to-end: live switching, per-channel persistence across a restart,
multi-channel capture + OME-TIFF on disk, overlay toggles, PNG export, return to live.

- [ ] **Step 4: Final commit**

```bash
git add -A && git commit -m "chore(lite): final integration cleanup"
```

---

## Self-Review notes (coverage map)

| Spec requirement | Task |
|------------------|------|
| Entry point + attachment seam | F0 |
| Pseudocolor compositing | F1, F6 |
| Save bar: directory + prefix | F2, F7 |
| Live view (switchable, single active channel) | F3, F4 |
| Channel list: include-checkbox + active select | F4 |
| Per-channel exposure/gain/intensity + persistence | F5 |
| Multi-channel capture via backend (command/event) | F7 |
| Capture result: overlay toggles + per-channel contrast + Export PNG | F6, F7 |
| Live/Result tab switch | F7 |
| Global binning | F8 |
| Deep Focus theme + touch sizing | F8 |
| Remove old UI | F9 |
| Verification (unit + sim + backend regression) | F10 |

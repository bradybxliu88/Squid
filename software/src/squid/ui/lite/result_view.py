"""Capture result: composite render + per-channel overlay toggles + PNG export."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import imageio
import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QCheckBox, QPushButton, QFileDialog,
    QFrame,
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


def _color_dot(color_hex: str) -> QLabel:
    dot = QLabel()
    dot.setFixedSize(11, 11)
    dot.setStyleSheet(f"background-color: {color_hex}; border-radius: 5px;")
    return dot


class ResultView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("root")
        self._channels: List[CapturedChannel] = []
        self._rows: List[QWidget] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # framed composite
        image_wrap = QWidget()
        iw = QVBoxLayout(image_wrap)
        iw.setContentsMargins(14, 14, 8, 14)
        frame = QWidget()
        frame.setObjectName("imageFrame")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        self._view = pg.GraphicsLayoutWidget()
        vb = self._view.addViewBox()
        vb.setAspectLocked(True)
        vb.invertY(True)
        self._img = pg.ImageItem(axisOrder="row-major")
        vb.addItem(self._img)
        fl.addWidget(self._view)
        iw.addWidget(frame)
        root.addWidget(image_wrap, 1)

        # rail
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(310)
        rv = QVBoxLayout(rail)
        rv.setContentsMargins(18, 16, 18, 18)
        rv.setSpacing(10)
        title = QLabel("OVERLAY CHANNELS")
        title.setObjectName("sectionLabel")
        rv.addWidget(title)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(6)
        rv.addLayout(self._rows_box)
        rv.addStretch(1)
        div = QFrame()
        div.setObjectName("divider")
        div.setFrameShape(QFrame.HLine)
        rv.addWidget(div)
        self._export = QPushButton("⬇   Export overlay PNG")
        self._export.setObjectName("liveToggle")
        self._export.setCursor(Qt.PointingHandCursor)
        self._export.clicked.connect(self._export_png)
        rv.addWidget(self._export)
        root.addWidget(rail)

    def set_capture(self, channels: List[CapturedChannel]) -> None:
        self._channels = channels
        for w in self._rows:
            w.setParent(None)
        self._rows = []
        for ch in channels:
            row = QWidget()
            row.setObjectName("overlayRow")
            h = QHBoxLayout(row)
            h.setContentsMargins(11, 8, 12, 8)
            h.setSpacing(10)
            cb = QCheckBox()
            cb.setChecked(ch.visible)
            cb.setCursor(Qt.PointingHandCursor)
            cb.toggled.connect(lambda on, c=ch: self._set_visible(c, on))
            name = QLabel(ch.name)
            name.setObjectName("overlayName")
            h.addWidget(cb)
            h.addWidget(_color_dot(ch.color))
            h.addWidget(name, 1)
            self._rows_box.addWidget(row)
            self._rows.append(row)
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

"""Capture result: composite + per-channel colormap + per-channel histogram + PNG export."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import imageio
import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QCheckBox, QPushButton, QFileDialog,
    QFrame, QComboBox, QScrollArea, QSizePolicy, QListView,
)

from squid.ui.lite import compositing as C
from squid.ui.lite.histogram import LiteHistogram


@dataclass
class CapturedChannel:
    name: str
    data: np.ndarray
    color: str
    low: float
    high: float
    visible: bool = True


# Single-hue pseudocolor options for the overlay.
_CMAPS = [
    ("Red", "#FF3B30"), ("Orange", "#FF9F0A"), ("Yellow", "#FFD60A"),
    ("Green", "#34C759"), ("Cyan", "#22D3EE"), ("Blue", "#3B82F6"),
    ("Magenta", "#E040FB"), ("Gray", "#FFFFFF"),
]


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
        self._row_widgets: List[QWidget] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # framed composite
        image_wrap = QWidget()
        iw = QVBoxLayout(image_wrap)
        iw.setContentsMargins(10, 10, 6, 10)
        frame = QWidget()
        frame.setObjectName("imageFrame")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        self._view = pg.GraphicsLayoutWidget()
        self._view.setBackground(QColor("#04060a"))
        vb = self._view.addViewBox()
        vb.setAspectLocked(True)
        vb.invertY(True)
        vb.setMenuEnabled(False)
        vb.setMouseEnabled(False, False)
        self._img = pg.ImageItem(axisOrder="row-major")
        vb.addItem(self._img)
        fl.addWidget(self._view)
        iw.addWidget(frame)
        root.addWidget(image_wrap, 1)

        # scrollable rail with per-channel rows
        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(9)
        title = QLabel("OVERLAY CHANNELS")
        title.setObjectName("sectionLabel")
        box.addWidget(title)
        self._rows_holder = QVBoxLayout()
        self._rows_holder.setSpacing(8)
        box.addLayout(self._rows_holder)
        box.addStretch(1)
        self._export = QPushButton("⬇   Export overlay PNG")
        self._export.setObjectName("liveToggle")
        self._export.setCursor(Qt.PointingHandCursor)
        self._export.clicked.connect(self._export_png)
        box.addWidget(self._export)

        scroll = QScrollArea()
        scroll.setObjectName("rail")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(324)
        scroll.setWidget(inner)
        root.addWidget(scroll)

    def set_capture(self, channels: List[CapturedChannel]) -> None:
        self._channels = channels
        for w in self._row_widgets:
            w.setParent(None)
        self._row_widgets = []
        for ch in channels:
            row = self._build_row(ch)
            self._rows_holder.addWidget(row)
            self._row_widgets.append(row)
        self._render()

    def _build_row(self, ch: CapturedChannel) -> QWidget:
        row = QWidget()
        row.setObjectName("overlayRow")
        row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        v = QVBoxLayout(row)
        v.setContentsMargins(10, 8, 10, 9)
        v.setSpacing(7)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(0)
        cb = QCheckBox()
        cb.setChecked(ch.visible)
        cb.setCursor(Qt.PointingHandCursor)
        cb.toggled.connect(lambda on, c=ch: self._set_visible(c, on))
        dot = _color_dot(ch.color)
        name = QLabel(ch.name)
        name.setObjectName("overlayName")
        cmap = QComboBox()
        cmap.setView(QListView())
        cmap.setObjectName("cmapCombo")
        cmap.setCursor(Qt.PointingHandCursor)
        cmap.setMinimumWidth(88)
        cmap.view().setMinimumWidth(120)
        cmap.addItem("Auto", ch.color)
        for nm, hx in _CMAPS:
            cmap.addItem(nm, hx)
        cmap.setCurrentIndex(0)
        cmap.currentIndexChanged.connect(
            lambda _i, c=ch, combo=cmap, d=dot: self._set_color(c, combo.currentData(), d))
        head.addWidget(cb)
        head.addSpacing(11)
        head.addWidget(dot)
        head.addSpacing(11)
        head.addWidget(name, 1)
        head.addSpacing(8)
        head.addWidget(cmap)
        v.addLayout(head)

        hist = LiteHistogram()
        hist.set_data(ch.data)
        hist.set_levels(ch.low, ch.high)
        hist.levels_changed.connect(lambda lo, hi, c=ch: self._set_levels(c, lo, hi))
        v.addWidget(hist)
        return row

    def _set_visible(self, ch: CapturedChannel, on: bool) -> None:
        ch.visible = on
        self._render()

    def _set_color(self, ch: CapturedChannel, hx, dot) -> None:
        if hx:
            ch.color = hx
            dot.setStyleSheet(f"background-color: {hx}; border-radius: 5px;")
            self._render()

    def _set_levels(self, ch: CapturedChannel, lo, hi) -> None:
        ch.low = float(lo)
        ch.high = float(hi)
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

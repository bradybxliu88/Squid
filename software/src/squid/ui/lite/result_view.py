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

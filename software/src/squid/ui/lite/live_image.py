"""Live image display for Squid Lite, backed by pyqtgraph."""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QWidget, QVBoxLayout, QLabel


class LiveImageWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = pg.GraphicsLayoutWidget()
        self._view.setBackground(QColor("#070b12"))
        self._vb = self._view.addViewBox()
        self._vb.setAspectLocked(True)
        self._vb.invertY(True)
        self._vb.setMenuEnabled(False)
        self._img = pg.ImageItem(axisOrder="row-major")
        self._vb.addItem(self._img)
        layout.addWidget(self._view)

        # Empty-state placeholder shown until the first frame arrives.
        self._placeholder = QLabel(self)
        self._placeholder.setText(
            "<div style='font-size:34px; color:#1d3f3a;'>◇</div>"
            "<div style='font-size:13px; color:#3c485e; margin-top:14px; letter-spacing:0.3px;'>"
            "Select a channel, then press "
            "<span style='color:#5a6c7e; font-weight:600;'>▶ Start Live</span></div>"
        )
        self._placeholder.setObjectName("livePlaceholder")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._levels: Optional[Tuple[float, float]] = None
        self._auto = True

    @property
    def image_item(self) -> pg.ImageItem:
        return self._img

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._placeholder.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)

    def set_auto_contrast(self, enabled: bool) -> None:
        self._auto = enabled

    def set_levels(self, low: float, high: float) -> None:
        self._auto = False
        self._levels = (low, high)
        self._img.setLevels((low, high))

    def update_frame(self, frame: np.ndarray) -> None:
        """Slot for QtStreamHandler.image_to_display (runs on the GUI thread)."""
        self._placeholder.hide()
        if self._auto:
            lo = float(np.percentile(frame, 0.5))
            hi = float(np.percentile(frame, 99.5))
            if hi <= lo:
                hi = lo + 1.0
            self._levels = (lo, hi)
            self._img.setImage(frame, autoLevels=False, levels=(lo, hi))
        else:
            self._img.setImage(frame, autoLevels=False, levels=self._levels)

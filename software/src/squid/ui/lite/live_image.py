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

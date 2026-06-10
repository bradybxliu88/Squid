"""Live image display for Squid Lite: pyqtgraph view + instrument overlay."""
from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Qt, QRect
from qtpy.QtGui import QColor, QFont, QPainter, QPen
from qtpy.QtWidgets import QWidget, QVBoxLayout, QLabel

_BRACKET = QColor(38, 86, 80)   # dim teal viewfinder
_READOUT = QColor(132, 152, 176)


def _nice_length(target: float) -> float:
    """Largest 1/2/5 × 10^k that is <= target."""
    if target <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(target))
    for m in (5, 2, 1):
        if m * p <= target:
            return m * p
    return p


class _LiveHud(QWidget):
    """Transparent overlay: viewfinder corner brackets + monospace readout."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._readout = ""
        self._font = QFont("SF Mono", 10)
        self._font.setStyleHint(QFont.Monospace)

    def set_readout(self, text: str) -> None:
        self._readout = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        m, L = 16, 22
        p.setPen(QPen(_BRACKET, 1.3))
        for (cx, sx) in ((m, 1), (w - m, -1)):
            for (cy, sy) in ((m, 1), (h - m, -1)):
                p.drawLine(cx, cy, cx + sx * L, cy)
                p.drawLine(cx, cy, cx, cy + sy * L)
        if self._readout:
            p.setFont(self._font)
            p.setPen(_READOUT)
            p.drawText(QRect(m + 6, h - m - 26, w - 2 * m, 20),
                       Qt.AlignLeft | Qt.AlignVCenter, self._readout)
        p.end()


class LiveImageWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = pg.GraphicsLayoutWidget()
        self._view.setBackground(QColor("#04060a"))
        self._vb = self._view.addViewBox()
        self._vb.setAspectLocked(True)
        self._vb.invertY(True)
        self._vb.setMenuEnabled(False)
        self._vb.setMouseEnabled(False, False)
        self._img = pg.ImageItem(axisOrder="row-major")
        self._vb.addItem(self._img)
        layout.addWidget(self._view)

        self._hud = _LiveHud(self)
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
        self._pixel_size_um: Optional[float] = None
        self._scalebar: Optional[pg.ScaleBar] = None
        self._bar_img_w = 0

    @property
    def image_item(self) -> pg.ImageItem:
        return self._img

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._hud.setGeometry(0, 0, self.width(), self.height())
        self._placeholder.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)

    # ---- public API -----------------------------------------------------
    def set_pixel_size(self, um: Optional[float]) -> None:
        self._pixel_size_um = um if um and um > 0 else None

    def set_readout(self, text: str) -> None:
        self._hud.set_readout(text)

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
        self._ensure_scalebar(frame.shape[1] if frame.ndim >= 2 else 0)

    # ---- scale bar ------------------------------------------------------
    def _ensure_scalebar(self, img_w: int) -> None:
        if not self._pixel_size_um or not img_w:
            return
        if self._scalebar is not None and img_w == self._bar_img_w:
            return
        self._bar_img_w = img_w
        fov_um = img_w * self._pixel_size_um
        length_um = _nice_length(fov_um / 5.0)
        size_px = length_um / self._pixel_size_um
        if self._scalebar is not None:
            try:
                self._scalebar.setParentItem(None)
            except Exception:
                pass
            self._scalebar = None
        bar = pg.ScaleBar(size=size_px, width=4,
                          brush=pg.mkBrush(223, 231, 242, 235), pen=pg.mkPen(None))
        bar.setParentItem(self._vb)
        bar.anchor((1, 1), (1, 1), offset=(-24, -22))
        label = f"{length_um / 1000:g} mm" if length_um >= 1000 else f"{length_um:g} µm"
        bar.text.setText(label, color=(223, 231, 242))
        self._scalebar = bar

"""Compact horizontal histogram with two draggable contrast handles.

Replaces pyqtgraph's HistogramLUTWidget (which renders vertically and looks
broken in a horizontal slot). Pure QPainter; emits ``levels_changed(low, high)``.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from qtpy.QtCore import Qt, Signal, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen, QPolygonF
from qtpy.QtWidgets import QWidget

_ACCENT = QColor(45, 212, 191)
_BG = QColor(9, 13, 20)
_GRAB = 5  # px hit radius around a handle


class LiteHistogram(QWidget):
    """A small log-scaled histogram with low/high contrast handles."""

    levels_changed = Signal(float, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(70)
        self.setMaximumHeight(70)
        self.setCursor(Qt.SizeHorCursor)
        self._counts: Optional[np.ndarray] = None
        self._data_min = 0.0
        self._data_max = 255.0
        self._low = 0.0
        self._high = 255.0
        self._drag: Optional[str] = None

    # ---- public API -----------------------------------------------------
    def set_range(self, dmin: float, dmax: float) -> None:
        self._data_min = float(dmin)
        self._data_max = float(dmax) if dmax > dmin else float(dmin) + 1.0

    def set_levels(self, low: float, high: float) -> None:
        self._low, self._high = float(low), float(high)
        self.update()

    def levels(self) -> Tuple[float, float]:
        return self._low, self._high

    def set_data(self, image: Optional[np.ndarray]) -> None:
        if image is None or image.size == 0:
            return
        flat = (image[::4, ::4] if image.ndim == 2 else image).ravel()
        self.set_range(float(flat.min()), float(flat.max()))
        hist, _ = np.histogram(flat, bins=128, range=(self._data_min, self._data_max))
        self._counts = np.log1p(hist.astype(np.float32))
        self.update()

    # ---- geometry helpers ----------------------------------------------
    def _x_for_value(self, v: float) -> float:
        span = self._data_max - self._data_min
        return 0.0 if span <= 0 else (v - self._data_min) / span * self.width()

    def _value_for_x(self, x: float) -> float:
        w = max(1, self.width())
        return self._data_min + (x / w) * (self._data_max - self._data_min)

    # ---- painting -------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), _BG)

        # Empty state: faint baseline + hint, no floating handles.
        if self._counts is None:
            p.setPen(QPen(QColor(40, 49, 70), 1))
            p.drawLine(QPointF(8, h - 5), QPointF(w - 8, h - 5))
            p.setPen(QColor(74, 84, 102))
            p.drawText(self.rect(), Qt.AlignCenter, "no signal")
            p.end()
            return

        if self._counts is not None and float(self._counts.max()) > 0:
            n = len(self._counts)
            cmax = float(self._counts.max())
            poly = QPolygonF()
            poly.append(QPointF(0, h))
            for i, c in enumerate(self._counts):
                x = i / (n - 1) * w
                y = h - (float(c) / cmax) * (h - 8) - 4
                poly.append(QPointF(x, y))
            poly.append(QPointF(w, h))
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, QColor(45, 212, 191, 165))
            grad.setColorAt(1.0, QColor(45, 212, 191, 28))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPolygon(poly)

        xl = self._x_for_value(self._low)
        xh = self._x_for_value(self._high)
        # selected window
        p.fillRect(QRectF(xl, 0, max(0.0, xh - xl), h), QColor(255, 255, 255, 14))
        # handles
        for x in (xl, xh):
            p.setPen(QPen(_ACCENT, 1.4))
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            p.setPen(Qt.NoPen)
            p.setBrush(_ACCENT)
            p.drawPolygon(QPolygonF([QPointF(x - 4, 0), QPointF(x + 4, 0), QPointF(x, 8)]))
            p.drawPolygon(QPolygonF([QPointF(x - 4, h), QPointF(x + 4, h), QPointF(x, h - 8)]))
        p.end()

    # ---- interaction ----------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        x = float(event.pos().x())
        xl, xh = self._x_for_value(self._low), self._x_for_value(self._high)
        self._drag = "low" if abs(x - xl) <= abs(x - xh) else "high"
        self._apply(x)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag:
            self._apply(float(event.pos().x()))

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag = None

    def _apply(self, x: float) -> None:
        v = self._value_for_x(max(0.0, min(float(self.width()), x)))
        eps = (self._data_max - self._data_min) * 1e-3 or 1e-3
        if self._drag == "low":
            self._low = min(v, self._high - eps)
        else:
            self._high = max(v, self._low + eps)
        self.update()
        self.levels_changed.emit(self._low, self._high)

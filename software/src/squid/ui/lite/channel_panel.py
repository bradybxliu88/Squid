"""Channel list: compact chips with an include-in-capture checkbox + active select."""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel


def _color_dot(color_hex: str) -> QLabel:
    dot = QLabel()
    dot.setFixedSize(11, 11)
    dot.setStyleSheet(f"background-color: {color_hex}; border-radius: 5px;")
    return dot


class ChannelRow(QWidget):
    """One compact channel chip. Click the chip to make it active; check to include."""

    activated = Signal(str)
    include_toggled = Signal(str, bool)

    def __init__(self, name: str, color_hex: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._name = name
        self.setObjectName("channelChip")
        self.setProperty("active", False)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 5, 12, 5)
        lay.setSpacing(0)

        self._check = QCheckBox()
        self._check.setCursor(Qt.PointingHandCursor)
        self._check.toggled.connect(lambda on: self.include_toggled.emit(self._name, on))
        self._name_lbl = QLabel(name)
        self._name_lbl.setObjectName("chipName")
        self._live = QLabel("")
        self._live.setObjectName("liveFlag")

        lay.addWidget(self._check)
        lay.addSpacing(11)
        lay.addWidget(_color_dot(color_hex))
        lay.addSpacing(11)
        lay.addWidget(self._name_lbl, 1)
        lay.addWidget(self._live)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.activated.emit(self._name)
        super().mousePressEvent(event)

    def set_active(self, active: bool) -> None:
        self._live.setText("●" if active else "")
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)


class ChannelPanel(QWidget):
    """List of channel chips; manages the active channel + the capture set."""

    active_changed = Signal(str)
    capture_set_changed = Signal(set)

    def __init__(self, channels: List[dict], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: Dict[str, ChannelRow] = {}
        self._included: Set[str] = set()
        self._active: Optional[str] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for ch in channels:
            row = ChannelRow(ch["name"], ch["color"])
            row.activated.connect(self._on_activate)
            row.include_toggled.connect(self._on_include)
            lay.addWidget(row)
            self._rows[ch["name"]] = row

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

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

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

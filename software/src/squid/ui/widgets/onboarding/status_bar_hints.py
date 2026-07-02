"""Rotating status bar hints for idle guidance.

Shows helpful tips in the status bar that rotate periodically.
Hides when warnings/errors are displayed or when permanently dismissed.
"""

from qtpy.QtCore import QTimer, Qt
from qtpy.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from squid.ui.user_preferences import load_preferences, save_preferences

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)

_HINTS = [
    "Press Ctrl+L to toggle the live camera view",
    "Press Ctrl+Shift+P to toggle Performance Mode",
    "Use 'Copy Pos' in Navigation to save stage coordinates to your clipboard",
    "Set a save path in Acquisition before starting — your data goes there",
    "The disk space indicator turns red when free space is below 10 GB",
    "Hover over any control to see a tooltip explaining what it does",
    "Use the channel selector to switch between fluorescence channels",
    "The Z-stack option captures images at multiple focus planes",
    "Check 'Skip Saving' to preview an acquisition without writing files",
    "Adjust exposure time to control image brightness",
    "The progress bar shows acquisition status including FOV count",
    "Use Settings > Preferences to customize acquisition and display options",
    "Quick Scan runs a fast mosaic at the current position — no files saved",
    "The time estimate updates as you configure channels and positions",
    "Run Current FOV acquires all channels at just the current stage position",
]


class StatusBarHintWidget(QWidget):
    """Rotating hint display for the main window status bar."""

    def __init__(self, warning_widget=None, parent=None):
        super().__init__(parent)
        self._warning_widget = warning_widget
        self._hint_index = 0
        self._prefs = load_preferences()

        self._setup_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(30000)
        self._timer.timeout.connect(self._next_hint)

        if self._prefs.show_status_hints:
            self._show_current_hint()
            self._timer.start()
        else:
            self.setVisible(False)

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)

        self._hint_label = QLabel()
        self._hint_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._hint_label, 1)

        self._dismiss_btn = QPushButton("x")
        self._dismiss_btn.setFixedSize(16, 16)
        self._dismiss_btn.setFlat(True)
        self._dismiss_btn.setStyleSheet(
            "QPushButton { color: gray; font-size: 10px; border: none; }"
            "QPushButton:hover { color: black; }"
        )
        self._dismiss_btn.setToolTip("Hide hints permanently")
        self._dismiss_btn.clicked.connect(self._dismiss_permanently)
        layout.addWidget(self._dismiss_btn)

    def _show_current_hint(self) -> None:
        """Display the current hint, hiding if warnings are visible."""
        if self._warning_widget and self._warning_widget.isVisible():
            self.setVisible(False)
            return
        self.setVisible(True)
        hint = _HINTS[self._hint_index % len(_HINTS)]
        self._hint_label.setText(f"Tip: {hint}")

    def _next_hint(self) -> None:
        """Advance to the next hint."""
        self._hint_index += 1
        self._show_current_hint()

    def _dismiss_permanently(self) -> None:
        """Permanently hide status bar hints."""
        self._timer.stop()
        self.setVisible(False)
        try:
            self._prefs = self._prefs.with_update(show_status_hints=False)
            save_preferences(self._prefs)
        except Exception:
            _log.debug("Failed to save hint dismissal preference", exc_info=True)

"""Event-triggered contextual tips for new users.

Shows a floating tip near the status bar when the user performs
an action for the first time. Tips auto-hide and can be permanently dismissed.
"""

from typing import Optional

from qtpy.QtCore import QTimer, Qt
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from squid.core.events import (
    AcquisitionStateChanged,
    LiveStateChanged,
    StagePositionChanged,
    handles,
)
from squid.ui.user_preferences import load_preferences, save_preferences, UserPreferences
from squid.ui.widgets.base import EventBusWidget

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


# (tip_id, message) — trigger events are handled by @handles methods below
_TIP_DEFINITIONS = {
    "first_live": "Adjust exposure and gain in the Camera section above. Use the channel dropdown to switch imaging modes.",
    "first_move": "Use the Copy Pos button to save coordinates to your clipboard. Click to Move lets you navigate by clicking the image.",
    "first_acquisition": "Your data is being saved to the folder shown in the Acquisition panel. Check progress in the progress bar.",
}


class ContextualTipManager(EventBusWidget):
    """Manages and displays contextual tips triggered by user actions."""

    def __init__(self, event_bus, parent=None):
        super().__init__(event_bus, parent)
        self._prefs = load_preferences()
        self._tip_widget: Optional[_TipOverlay] = None
        self._tip_queue: list[tuple[str, str]] = []
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(12000)
        self._auto_hide_timer.timeout.connect(self._hide_current_tip)

    def _should_show_tip(self, tip_id: str) -> bool:
        """Check if a tip should be shown based on preferences."""
        if not self._prefs.show_tips:
            return False
        return not self._prefs.is_tip_dismissed(tip_id)

    def _show_tip(self, tip_id: str) -> None:
        """Queue and display a contextual tip."""
        if not self._should_show_tip(tip_id):
            return
        message = _TIP_DEFINITIONS.get(tip_id)
        if not message:
            return

        # Dismiss this tip so it only shows once
        self._prefs = self._prefs.dismiss_tip(tip_id)
        save_preferences(self._prefs)

        if self._tip_widget is not None:
            self._tip_queue.append((tip_id, message))
            return

        self._display_tip(message)

    def _display_tip(self, message: str) -> None:
        """Create and show the tip overlay."""
        parent = self.window()
        if parent is None:
            return

        self._tip_widget = _TipOverlay(message, parent)
        self._tip_widget.dismissed.connect(self._on_tip_dismissed)
        self._tip_widget.disable_all.connect(self._disable_all_tips)
        self._tip_widget.show()
        self._position_tip()
        self._auto_hide_timer.start()

    def _position_tip(self) -> None:
        """Position the tip near the bottom of the window."""
        if self._tip_widget is None:
            return
        parent = self.window()
        if parent is None:
            return
        pw = parent.width()
        ph = parent.height()
        tw = min(self._tip_widget.sizeHint().width(), pw - 40)
        th = self._tip_widget.sizeHint().height()
        x = (pw - tw) // 2
        y = ph - th - 40  # Above status bar
        self._tip_widget.setGeometry(x, y, tw, th)

    def _hide_current_tip(self) -> None:
        """Hide the current tip and show the next queued one."""
        self._auto_hide_timer.stop()
        if self._tip_widget is not None:
            self._tip_widget.close()
            self._tip_widget = None
        if self._tip_queue:
            _, message = self._tip_queue.pop(0)
            self._display_tip(message)

    def _on_tip_dismissed(self) -> None:
        """Handle manual dismissal of a tip."""
        self._hide_current_tip()

    def _disable_all_tips(self) -> None:
        """Permanently disable all contextual tips."""
        self._prefs = self._prefs.with_update(show_tips=False)
        save_preferences(self._prefs)
        self._tip_queue.clear()
        self._hide_current_tip()

    @handles(LiveStateChanged)
    def _on_live(self, event: LiveStateChanged) -> None:
        if getattr(event, "camera", "main") != "main":
            return
        if event.is_live:
            self._show_tip("first_live")

    @handles(StagePositionChanged)
    def _on_stage_moved(self, _event: StagePositionChanged) -> None:
        self._show_tip("first_move")

    @handles(AcquisitionStateChanged)
    def _on_acquisition(self, event: AcquisitionStateChanged) -> None:
        if event.in_progress and not event.is_aborting:
            self._show_tip("first_acquisition")


from qtpy.QtCore import Signal


class _TipOverlay(QFrame):
    """Floating styled tip frame."""

    dismissed = Signal()
    disable_all = Signal()

    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setStyleSheet(
            "QFrame { background-color: #e3f2fd; border: 1px solid #90caf9; "
            "border-radius: 6px; padding: 8px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        icon_label = QLabel("Tip")
        icon_label.setStyleSheet("font-weight: bold; color: #1565c0; font-size: 12px;")
        top_row.addWidget(icon_label)
        top_row.addStretch()

        close_btn = QPushButton("x")
        close_btn.setFixedSize(18, 18)
        close_btn.setFlat(True)
        close_btn.setStyleSheet(
            "QPushButton { color: #666; font-size: 12px; border: none; }"
            "QPushButton:hover { color: black; }"
        )
        close_btn.clicked.connect(self.dismissed.emit)
        top_row.addWidget(close_btn)
        layout.addLayout(top_row)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("color: #333; font-size: 11px;")
        layout.addWidget(msg_label)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        disable_link = QPushButton("Don't show tips")
        disable_link.setFlat(True)
        disable_link.setStyleSheet(
            "QPushButton { color: #999; font-size: 10px; border: none; text-decoration: underline; }"
            "QPushButton:hover { color: #666; }"
        )
        disable_link.clicked.connect(self.disable_all.emit)
        bottom_row.addWidget(disable_link)
        layout.addLayout(bottom_row)

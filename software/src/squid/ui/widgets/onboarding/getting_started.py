"""Getting Started checklist panel for new users.

Auto-completes items as the user performs key actions.
Subscribes to EventBus events to track progress.
"""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from squid.core.events import (
    AcquisitionStateChanged,
    ExposureTimeChanged,
    LiveStateChanged,
    StagePositionChanged,
    handles,
)
from squid.ui.user_preferences import load_preferences, save_preferences
from squid.ui.widgets.base import EventBusWidget

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


_CHECKLIST_ITEMS = [
    ("live", "Start Live View", "Click 'Start Live' or press Ctrl+L to see your sample"),
    ("move", "Move the Stage", "Use the X/Y/Z navigation controls to move to a position"),
    ("exposure", "Adjust Exposure", "Change the exposure time to control image brightness"),
    ("acquisition", "Run an Acquisition", "Configure channels and positions, then start an acquisition"),
]


class GettingStartedWidget(EventBusWidget):
    """Checklist panel that auto-completes as the user performs key actions."""

    def __init__(self, event_bus, parent=None):
        super().__init__(event_bus, parent)
        self._prefs = load_preferences()
        self._completed = set()
        self._checkboxes = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        header = QLabel("Complete these steps to get familiar with Squid:")
        header.setStyleSheet("color: gray; font-size: 11px;")
        header.setWordWrap(True)
        header_row.addWidget(header, 1)

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setFlat(True)
        dismiss_btn.setStyleSheet(
            "QPushButton { color: #1976d2; font-size: 11px; border: none; text-decoration: underline; }"
            "QPushButton:hover { color: #1565c0; }"
        )
        dismiss_btn.clicked.connect(self._dismiss)
        header_row.addWidget(dismiss_btn)
        layout.addLayout(header_row)

        for item_id, title, description in _CHECKLIST_ITEMS:
            row = QHBoxLayout()
            row.setSpacing(6)

            cb = QCheckBox()
            cb.setEnabled(False)  # Read-only — auto-checked by events
            cb.setToolTip(description)
            self._checkboxes[item_id] = cb
            row.addWidget(cb)

            label = QLabel(title)
            label.setStyleSheet("font-size: 12px;")
            label.setToolTip(description)
            row.addWidget(label, 1)

            layout.addLayout(row)

    def _mark_completed(self, item_id: str) -> None:
        """Mark a checklist item as complete."""
        if item_id in self._completed:
            return
        self._completed.add(item_id)
        cb = self._checkboxes.get(item_id)
        if cb:
            cb.setChecked(True)

    @handles(LiveStateChanged)
    def _on_live(self, event: LiveStateChanged) -> None:
        if getattr(event, "camera", "main") != "main":
            return
        if event.is_live:
            self._mark_completed("live")

    @handles(StagePositionChanged)
    def _on_stage_moved(self, _event: StagePositionChanged) -> None:
        self._mark_completed("move")

    @handles(ExposureTimeChanged)
    def _on_exposure(self, _event: ExposureTimeChanged) -> None:
        self._mark_completed("exposure")

    @handles(AcquisitionStateChanged)
    def _on_acquisition(self, event: AcquisitionStateChanged) -> None:
        if event.in_progress:
            self._mark_completed("acquisition")

    def _dismiss(self) -> None:
        """Hide the Getting Started panel permanently."""
        self.setVisible(False)
        try:
            self._prefs = self._prefs.with_update(show_getting_started=False)
            save_preferences(self._prefs)
        except Exception:
            _log.debug("Failed to save getting-started dismissal", exc_info=True)

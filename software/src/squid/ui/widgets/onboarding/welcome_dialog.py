"""Welcome dialog shown on first application launch.

Introduces key concepts and lets the user configure tip preferences.
"""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from squid.ui.user_preferences import UserPreferences, save_preferences


class WelcomeDialog(QDialog):
    """Modal welcome dialog for first-time users."""

    def __init__(self, parent=None, preferences: UserPreferences = None):
        super().__init__(parent)
        self._prefs = preferences or UserPreferences()
        self.setWindowTitle("Welcome to Squid")
        self.setMinimumWidth(520)
        self.setModal(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header
        header = QLabel("Welcome to Squid Microscope Control")
        header.setStyleSheet("font-size: 18px; font-weight: bold; padding: 8px 0;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        subtitle = QLabel(
            "Here's a quick overview to help you get started.\n"
            "You can always access these settings later in Settings > Preferences."
        )
        subtitle.setStyleSheet("color: gray; font-size: 12px;")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Concept cards
        cards = [
            (
                "Live View",
                "Start the camera feed to see your sample in real time. "
                "Select an imaging channel, then click 'Start Live' (or press Ctrl+L).",
            ),
            (
                "Navigation",
                "Move the stage using the X/Y/Z controls in the Navigation panel. "
                "Use 'Copy Pos' to save coordinates to your clipboard.",
            ),
            (
                "Acquisition",
                "Set up multi-position imaging in the Acquisition panel. "
                "Configure channels, Z-stacks, and positions, then click 'Start Acquisition'.",
            ),
            (
                "Configuration",
                "Access microscope settings, channel definitions, and filter wheel "
                "configuration from the Settings menu.",
            ),
        ]

        for title, description in cards:
            card = self._create_card(title, description)
            layout.addWidget(card)

        # Tip preferences
        self._show_tips_cb = QCheckBox("Show helpful tips as I use the software")
        self._show_tips_cb.setChecked(True)
        layout.addWidget(self._show_tips_cb)

        self._dont_show_cb = QCheckBox("Don't show this welcome screen again")
        self._dont_show_cb.setChecked(True)
        layout.addWidget(self._dont_show_cb)

        # Get Started button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_start = QPushButton("Get Started")
        btn_start.setMinimumHeight(36)
        btn_start.setMinimumWidth(140)
        btn_start.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; "
            "font-weight: bold; border-radius: 4px; padding: 8px 24px; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        btn_start.clicked.connect(self._on_get_started)
        btn_layout.addWidget(btn_start)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _create_card(self, title: str, description: str) -> QFrame:
        """Create a styled concept card."""
        card = QFrame()
        card.setFrameStyle(QFrame.StyledPanel | QFrame.Plain)
        card.setStyleSheet(
            "QFrame { background-color: palette(base); border: 1px solid palette(mid); "
            "border-radius: 4px; padding: 8px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 6, 10, 6)
        card_layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        card_layout.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: gray; font-size: 11px;")
        card_layout.addWidget(desc_label)

        return card

    def _on_get_started(self) -> None:
        """Save preferences and close dialog."""
        show_tips = self._show_tips_cb.isChecked()
        first_run_done = self._dont_show_cb.isChecked()
        updated = self._prefs.with_update(
            first_run_completed=first_run_done,
            show_tips=show_tips,
            show_status_hints=show_tips,
            show_getting_started=True,
        )
        save_preferences(updated)
        self.accept()

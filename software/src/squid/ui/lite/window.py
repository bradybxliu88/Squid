"""Top-level window for the Squid Lite frontend."""
from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtWidgets import QMainWindow, QLabel, QWidget, QVBoxLayout

import squid.core.logging

if TYPE_CHECKING:
    from squid.backend.microscope import Microscope
    from squid.application import Controllers
    from squid.backend.services import ServiceRegistry
    from squid.ui.ui_event_bus import UIEventBus

_log = squid.core.logging.get_logger(__name__)


class SquidLiteWindow(QMainWindow):
    """Minimal, touch-friendly frontend for live view + multichannel capture."""

    def __init__(self, microscope: "Microscope", controllers: "Controllers",
                 services: "ServiceRegistry", event_bus: "UIEventBus",
                 is_simulation: bool = False) -> None:
        super().__init__()
        self._microscope = microscope
        self._controllers = controllers
        self._services = services
        self._bus = event_bus
        self._is_simulation = is_simulation

        self.setWindowTitle("Squid Lite" + (" — SIMULATION" if is_simulation else ""))
        self.resize(1280, 800)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel("Squid Lite — skeleton (live view next)"))
        self.setCentralWidget(central)
        _log.info("SquidLiteWindow constructed")

"""Top-level window for the Squid Lite frontend."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QStackedWidget,
    QPushButton, QButtonGroup, QMessageBox, QComboBox, QLabel,
)

import squid.core.logging
from squid.core.events import (
    SetMicroscopeModeCommand, StartLiveCommand,
    CaptureMultichannelCommand, CaptureCompleted, CaptureFailed,
    SetBinningCommand,
)
from squid.ui.qt_stream_handler import QtStreamHandler
from squid.ui.lite.save_bar import SaveBar
from squid.ui.lite.live_image import LiveImageWidget
from squid.ui.lite.channel_panel import ChannelPanel
from squid.ui.lite.channel_controls import ChannelControls
from squid.ui.lite.result_view import ResultView, CapturedChannel
from squid.ui.lite import compositing as C

if TYPE_CHECKING:
    from squid.backend.microscope import Microscope
    from squid.application import Controllers
    from squid.backend.services import ServiceRegistry
    from squid.ui.ui_event_bus import UIEventBus

_log = squid.core.logging.get_logger(__name__)


class SquidLiteWindow(QMainWindow):
    """Minimal frontend: switchable live view + multichannel capture."""

    def __init__(self, microscope, controllers, services, event_bus, is_simulation=False) -> None:
        super().__init__()
        self._microscope = microscope
        self._controllers = controllers
        self._services = services
        self._bus = event_bus
        self._is_simulation = is_simulation
        self.setWindowTitle("Squid Lite" + (" — SIMULATION" if is_simulation else ""))
        self.resize(1280, 800)

        self._objective = microscope.objective_store.current_objective
        self._ccm = microscope.channel_configuration_manager
        info = self._ccm.get_channel_display_info(self._objective)
        self._color_by_name = {i.name: i.color for i in info}
        channels = [{"name": i.name, "color": i.color} for i in info]

        self._default_dir = os.path.expanduser("~/SquidLite")

        self._save_bar = SaveBar(initial_directory=self._default_dir)
        self._live_image = LiveImageWidget()
        self._channel_panel = ChannelPanel(channels)
        self._channel_controls = ChannelControls(self._bus.publish)
        self._channel_controls.attach_image_item(self._live_image.image_item)
        self._result_view = ResultView()
        self._capture_btn = QPushButton("📷 Capture 0")
        self._binning_combo = self._build_binning_combo()

        self._qt_stream = QtStreamHandler(handler=controllers.stream_handler)
        self._qt_stream.image_to_display.connect(self._live_image.update_frame)

        self._channel_panel.active_changed.connect(self._on_active_channel)
        self._channel_panel.capture_set_changed.connect(self._on_capture_set_changed)
        self._channel_controls.contrast_changed.connect(self._live_image.set_levels)
        self._capture_btn.clicked.connect(self._on_capture_clicked)

        self._bus.subscribe(CaptureCompleted, self._on_capture_completed)
        self._bus.subscribe(CaptureFailed, self._on_capture_failed)

        self._build_layout()
        _log.info("SquidLiteWindow constructed")

    def _build_layout(self) -> None:
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.addWidget(self._channel_panel)
        rlay.addWidget(self._channel_controls)
        rlay.addWidget(self._capture_btn)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._live_image)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)

        self._stack = QStackedWidget()
        self._stack.addWidget(splitter)            # 0 = live
        self._stack.addWidget(self._result_view)   # 1 = result

        tabs = QWidget()
        tlay = QHBoxLayout(tabs)
        self._live_tab = QPushButton("◐ Live")
        self._result_tab = QPushButton("▣ Result")
        for b in (self._live_tab, self._result_tab):
            b.setCheckable(True)
        self._live_tab.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self._live_tab)
        grp.addButton(self._result_tab)
        self._live_tab.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._result_tab.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        tlay.addWidget(self._live_tab)
        tlay.addWidget(self._result_tab)
        tlay.addStretch(1)
        tlay.addWidget(QLabel("Binning"))
        tlay.addWidget(self._binning_combo)

        central = QWidget()
        clay = QVBoxLayout(central)
        clay.addWidget(self._save_bar)
        clay.addWidget(tabs)
        clay.addWidget(self._stack, stretch=1)
        self.setCentralWidget(central)

    def _on_active_channel(self, name: str) -> None:
        self._bus.publish(SetMicroscopeModeCommand(configuration_name=name, objective=self._objective))
        self._bus.publish(StartLiveCommand(configuration=name))
        cfg = self._ccm.get_channel_configuration_by_name(self._objective, name)
        if cfg is not None:
            self._channel_controls.set_channel(self._objective, cfg)

    def _on_capture_set_changed(self, s) -> None:
        self._capture_btn.setText(f"📷 Capture {len(s)}")

    def _on_capture_clicked(self) -> None:
        names = sorted(self._channel_panel.capture_set)
        if not names:
            return
        os.makedirs(self._save_bar.directory, exist_ok=True)
        self._bus.publish(CaptureMultichannelCommand(
            channel_names=names, objective=self._objective,
            save_directory=self._save_bar.directory, file_prefix=self._save_bar.prefix))

    def _on_capture_completed(self, evt) -> None:
        import tifffile
        stack = tifffile.imread(evt.save_path)
        if stack.ndim == 2:
            stack = stack[None, ...]
        captured = []
        for idx, name in enumerate(evt.channel_names):
            data = stack[idx]
            lo, hi = C.auto_contrast_bounds(data)
            captured.append(CapturedChannel(name, data, self._color_by_name.get(name, "#FFFFFF"), lo, hi))
        self._result_view.set_capture(captured)
        self._result_tab.setChecked(True)
        self._stack.setCurrentIndex(1)

    def _on_capture_failed(self, evt) -> None:
        QMessageBox.warning(self, "Capture failed", evt.reason)

    def _build_binning_combo(self) -> QComboBox:
        combo = QComboBox()
        cam = self._services.get("camera")
        options = []
        try:
            options = list(cam.get_binning_options()) if cam is not None else []
        except Exception:
            options = []
        if not options:
            options = [(1, 1)]
        current = None
        try:
            current = tuple(cam.get_binning()) if cam is not None else None
        except Exception:
            current = None
        for opt in options:
            bx, by = int(opt[0]), int(opt[1])
            combo.addItem(f"{bx}×{by}", (bx, by))
        if current is not None:
            idx = combo.findData((int(current[0]), int(current[1])))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        # Connect after population + setCurrentIndex to avoid spurious publish
        combo.currentIndexChanged.connect(self._on_binning_changed)
        return combo

    def _on_binning_changed(self, _idx: int) -> None:
        data = self._binning_combo.currentData()
        if data is not None:
            self._bus.publish(SetBinningCommand(binning_x=int(data[0]), binning_y=int(data[1])))

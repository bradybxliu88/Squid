"""Top-level window for the Squid Lite frontend."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from qtpy.QtCore import Qt
from qtpy.QtGui import QKeySequence, QColor
from qtpy.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QStackedWidget,
    QPushButton, QButtonGroup, QMessageBox, QLabel, QFrame, QScrollArea, QComboBox,
    QLineEdit, QFileDialog, QApplication, QGraphicsDropShadowEffect,
)

import squid.core.logging
from squid.core.events import (
    SetMicroscopeModeCommand, StartLiveCommand, StopLiveCommand, SetBinningCommand,
    CaptureMultichannelCommand, CaptureCompleted, CaptureFailed,
)
from squid.ui.qt_stream_handler import QtStreamHandler
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
        self._streaming = False
        self.setWindowTitle("Squid Lite")
        self.resize(1440, 940)

        self._objective = microscope.objective_store.current_objective
        self._ccm = microscope.channel_configuration_manager
        info = self._ccm.get_channel_display_info(self._objective)
        self._color_by_name = {i.name: i.color for i in info}
        channels = [{"name": i.name, "color": i.color} for i in info]
        self._save_dir = os.path.expanduser("~/SquidLite")

        # core widgets
        self._live_image = LiveImageWidget()
        self._pixel_size_um = self._compute_pixel_size()
        self._live_image.set_pixel_size(self._pixel_size_um)
        self._channel_panel = ChannelPanel(channels)
        self._channel_controls = ChannelControls(self._bus.publish)
        self._result_view = ResultView()

        self._live_toggle = QPushButton("▶  Start Live")
        self._live_toggle.setObjectName("liveToggle")
        self._live_toggle.setCursor(Qt.PointingHandCursor)
        self._capture_btn = QPushButton("Capture")
        self._capture_btn.setObjectName("captureBtn")
        self._capture_btn.setCursor(Qt.PointingHandCursor)
        self._capture_btn.setEnabled(False)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 3)
        self._capture_btn.setGraphicsEffect(shadow)
        self._binning_combo = self._build_binning_combo()

        self._save_btn = QPushButton()
        self._save_btn.setObjectName("saveFolderBtn")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.clicked.connect(self._choose_dir)
        self._update_save_btn()
        self._prefix_edit = QLineEdit("capture")

        # frame routing
        self._qt_stream = QtStreamHandler(handler=controllers.stream_handler)
        self._qt_stream.image_to_display.connect(self._live_image.update_frame)
        self._qt_stream.image_to_display.connect(self._channel_controls.update_histogram)

        # wiring
        self._channel_panel.active_changed.connect(self._on_active_channel)
        self._channel_panel.capture_set_changed.connect(self._on_capture_set_changed)
        self._channel_controls.contrast_changed.connect(self._live_image.set_levels)
        self._live_toggle.clicked.connect(self._toggle_live)
        self._capture_btn.clicked.connect(self._on_capture_clicked)
        self._bus.subscribe(CaptureCompleted, self._on_capture_completed)
        self._bus.subscribe(CaptureFailed, self._on_capture_failed)

        self._build_menu()
        self._build_layout()

    # ===================================================================
    # construction
    # ===================================================================
    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("Squid Lite")
        act = menu.addAction("Quit Squid Lite")
        try:
            act.setShortcut(QKeySequence.Quit)
        except Exception:
            act.setShortcut("Ctrl+Q")
        act.triggered.connect(self._quit)

    def _build_binning_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("binningCombo")
        combo.setCursor(Qt.PointingHandCursor)
        cam = self._services.get("camera")
        try:
            options = list(cam.get_binning_options()) if cam is not None else []
        except Exception:
            options = []
        if not options:
            options = [(1, 1)]
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
        combo.currentIndexChanged.connect(self._on_binning_changed)
        return combo

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def _divider(self) -> QFrame:
        f = QFrame()
        f.setObjectName("divider")
        f.setFrameShape(QFrame.HLine)
        return f

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(54)
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 0, 14, 0)
        h.setSpacing(11)

        dot = QLabel("◆")
        dot.setObjectName("wordmarkDot")
        wm = QLabel("SQUID LITE")
        wm.setObjectName("wordmark")
        obj = QLabel(str(self._objective).upper())
        obj.setObjectName("objBadge")
        h.addWidget(dot)
        h.addWidget(wm)
        h.addSpacing(2)
        h.addWidget(obj)
        if self._is_simulation:
            sim = QLabel("SIMULATION")
            sim.setObjectName("simBadge")
            h.addWidget(sim)
        h.addStretch(1)

        seg = QWidget()
        seg.setObjectName("segGroup")
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(3, 3, 3, 3)
        sh.setSpacing(2)
        self._live_tab = QPushButton("◐  Live")
        self._result_tab = QPushButton("▦  Result")
        grp = QButtonGroup(self)
        for b in (self._live_tab, self._result_tab):
            b.setObjectName("segTab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            grp.addButton(b)
            sh.addWidget(b)
        self._live_tab.setChecked(True)
        self._live_tab.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._result_tab.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        h.addWidget(seg)
        h.addStretch(1)

        blabel = QLabel("Binning")
        blabel.setObjectName("fieldLabel")
        h.addWidget(blabel)
        h.addWidget(self._binning_combo)
        quit_btn = QPushButton("⏻")
        quit_btn.setObjectName("iconBtn")
        quit_btn.setCursor(Qt.PointingHandCursor)
        quit_btn.setToolTip("Quit  ⌘Q")
        quit_btn.clicked.connect(self._quit)
        h.addSpacing(4)
        h.addWidget(quit_btn)
        return bar

    def _build_rail(self) -> QScrollArea:
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(18, 16, 18, 18)
        v.setSpacing(13)
        v.addWidget(self._live_toggle)
        v.addWidget(self._divider())
        v.addWidget(self._section("CHANNELS"))
        v.addWidget(self._channel_panel)
        v.addWidget(self._divider())
        v.addWidget(self._section("ADJUST"))
        v.addWidget(self._channel_controls)
        v.addWidget(self._divider())
        v.addWidget(self._section("SAVE"))
        v.addWidget(self._save_btn)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        plabel = QLabel("Prefix")
        plabel.setObjectName("fieldLabel")
        prow.addWidget(plabel)
        prow.addWidget(self._prefix_edit, 1)
        v.addLayout(prow)
        v.addSpacing(2)
        v.addWidget(self._capture_btn)
        v.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("rail")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(350)
        scroll.setWidget(inner)
        return scroll

    def _build_layout(self) -> None:
        image_wrap = QWidget()
        iw = QVBoxLayout(image_wrap)
        iw.setContentsMargins(14, 14, 8, 14)
        frame = QWidget()
        frame.setObjectName("imageFrame")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        fl.addWidget(self._live_image)
        iw.addWidget(frame)

        live_page = QSplitter(Qt.Horizontal)
        live_page.setHandleWidth(0)
        live_page.addWidget(image_wrap)
        live_page.addWidget(self._build_rail())
        live_page.setStretchFactor(0, 1)
        live_page.setStretchFactor(1, 0)

        self._stack = QStackedWidget()
        self._stack.addWidget(live_page)
        self._stack.addWidget(self._result_view)

        root = QWidget()
        root.setObjectName("root")
        rv = QVBoxLayout(root)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._build_top_bar())
        rv.addWidget(self._stack, 1)
        self.setCentralWidget(root)

    # ===================================================================
    # behavior
    # ===================================================================
    def _set_streaming(self, on: bool) -> None:
        self._streaming = on
        self._live_toggle.setText("⏸  Stop Live" if on else "▶  Start Live")
        self._live_toggle.setProperty("streaming", on)
        self._live_toggle.style().unpolish(self._live_toggle)
        self._live_toggle.style().polish(self._live_toggle)

    def _toggle_live(self) -> None:
        if self._streaming:
            self._bus.publish(StopLiveCommand())
            self._set_streaming(False)
            return
        ch = self._channel_panel.active or next(iter(self._channel_panel._rows), None)
        if ch is None:
            return
        if self._channel_panel.active != ch:
            self._channel_panel._on_activate(ch)
        else:
            self._bus.publish(StartLiveCommand(configuration=ch))
            self._set_streaming(True)

    def _on_active_channel(self, name: str) -> None:
        self._bus.publish(SetMicroscopeModeCommand(configuration_name=name, objective=self._objective))
        cfg = self._ccm.get_channel_configuration_by_name(self._objective, name)
        if cfg is not None:
            self._channel_controls.set_channel(self._objective, cfg)
        self._live_image.set_readout(self._readout_text(name))
        self._bus.publish(StartLiveCommand(configuration=name))
        self._set_streaming(True)

    def _compute_pixel_size(self):
        # Effective sample-plane pixel size = sensor pixel (binned) × magnification factor.
        try:
            cam = self._services.get("camera")
            factor = self._microscope.objective_store.get_pixel_size_factor()
            sensor = cam.get_pixel_size_binned_um() if cam is not None else None
            if sensor and factor:
                return float(sensor) * float(factor)
        except Exception:
            pass
        return None

    def _readout_text(self, name: str) -> str:
        parts = [name]
        if self._pixel_size_um:
            parts.append(f"{self._pixel_size_um:.2f} µm/px")
        return "    ".join(parts)

    def _on_capture_set_changed(self, channels) -> None:
        n = len(channels)
        self._capture_btn.setText(f"Capture  ·  {n}" if n else "Capture")
        self._capture_btn.setEnabled(n > 0)

    def _on_capture_clicked(self) -> None:
        names = sorted(self._channel_panel.capture_set)
        if not names:
            return
        os.makedirs(self._save_dir, exist_ok=True)
        self._bus.publish(CaptureMultichannelCommand(
            channel_names=names, objective=self._objective,
            save_directory=self._save_dir, file_prefix=self._prefix_edit.text()))

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

    def _on_binning_changed(self, _idx: int) -> None:
        data = self._binning_combo.currentData()
        if data is not None:
            self._bus.publish(SetBinningCommand(binning_x=int(data[0]), binning_y=int(data[1])))

    def _choose_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose save folder", self._save_dir)
        if chosen:
            self._save_dir = chosen
            self._update_save_btn()

    def _update_save_btn(self) -> None:
        home = os.path.expanduser("~")
        disp = self._save_dir.replace(home, "~", 1) if self._save_dir.startswith(home) else self._save_dir
        self._save_btn.setText(f"📁   {disp}")

    def _quit(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

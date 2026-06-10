"""Active-channel controls: histogram + exposure/gain/intensity, with persistence."""
from __future__ import annotations

from typing import Optional

import numpy as np
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox, QPushButton,
)

from squid.core.events import (
    UpdateChannelConfigurationCommand, SetExposureTimeCommand,
    SetAnalogGainCommand, SetIlluminationCommand,
)
from squid.ui.lite.histogram import LiteHistogram


def build_persist_command(objective, config_name, exposure_ms, gain, intensity):
    return UpdateChannelConfigurationCommand(
        objective_name=objective, config_name=config_name,
        exposure_time_ms=exposure_ms, analog_gain=gain, illumination_intensity=intensity)


class ChannelControls(QWidget):
    """Edits the active channel; publishes live commands + persists to config."""

    contrast_changed = Signal(float, float)

    def __init__(self, publish, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._publish = publish
        self._objective: Optional[str] = None
        self._channel: Optional[str] = None
        self._illum_source: Optional[int] = None
        self._last_frame: Optional[np.ndarray] = None
        self._frame_tick = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(11)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = QLabel("No channel selected")
        self._title.setObjectName("activeChannelName")
        self._auto = QPushButton("AUTO")
        self._auto.setObjectName("miniBtn")
        self._auto.setCursor(Qt.PointingHandCursor)
        self._auto.clicked.connect(self._auto_contrast)
        header.addWidget(self._title, 1)
        header.addWidget(self._auto)
        lay.addLayout(header)

        self._hist = LiteHistogram()
        self._hist.levels_changed.connect(self._on_levels)
        lay.addWidget(self._hist)

        self._exposure = self._spin(0.01, 5000.0)
        self._gain = self._spin(0.0, 100.0)
        self._intensity = self._spin(0.0, 100.0)
        lay.addLayout(self._field_row("Exposure", "ms", self._exposure))
        lay.addLayout(self._field_row("Gain", "×", self._gain))
        lay.addLayout(self._field_row("Intensity", "%", self._intensity))
        self._exposure.valueChanged.connect(self._on_exposure)
        self._gain.valueChanged.connect(self._on_gain)
        self._intensity.valueChanged.connect(self._on_intensity)

    # ---- builders -------------------------------------------------------
    def _spin(self, lo: float, hi: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setButtonSymbols(QDoubleSpinBox.NoButtons)
        s.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        s.setFixedWidth(112)
        return s

    def _field_row(self, label: str, unit: str, spin: QDoubleSpinBox) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(f"{label} <span style='color:#5a6477'>({unit})</span>")
        lbl.setObjectName("fieldLabel")
        lbl.setTextFormat(Qt.RichText)
        row.addWidget(lbl, 1)
        row.addWidget(spin)
        return row

    # ---- frame / histogram ---------------------------------------------
    def update_histogram(self, frame: np.ndarray) -> None:
        self._last_frame = frame
        self._frame_tick += 1
        if self._frame_tick % 4 == 0:
            self._hist.set_data(frame)

    # ---- channel load ---------------------------------------------------
    def set_channel(self, objective: str, channel_config) -> None:
        self._objective = objective
        self._channel = channel_config.name
        self._illum_source = channel_config.illumination_source
        self._title.setText(channel_config.name)
        for spin, val in ((self._exposure, channel_config.exposure_time),
                          (self._gain, channel_config.analog_gain),
                          (self._intensity, channel_config.illumination_intensity)):
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

    # ---- emit -----------------------------------------------------------
    def _on_exposure(self, v):
        self._publish(SetExposureTimeCommand(exposure_time_ms=v)); self._persist()

    def _on_gain(self, v):
        self._publish(SetAnalogGainCommand(gain=v)); self._persist()

    def _on_intensity(self, v):
        if self._illum_source is not None:
            self._publish(SetIlluminationCommand(channel=self._illum_source, intensity=v, on=True))
        self._persist()

    def _persist(self):
        if self._objective and self._channel:
            self._publish(build_persist_command(self._objective, self._channel,
                self._exposure.value(), self._gain.value(), self._intensity.value()))

    def _on_levels(self, lo, hi):
        self.contrast_changed.emit(float(lo), float(hi))

    def _auto_contrast(self):
        if self._last_frame is None:
            return
        lo = float(np.percentile(self._last_frame, 0.5))
        hi = float(np.percentile(self._last_frame, 99.5))
        if hi <= lo:
            hi = lo + 1.0
        self._hist.set_data(self._last_frame)
        self._hist.set_levels(lo, hi)
        self.contrast_changed.emit(lo, hi)

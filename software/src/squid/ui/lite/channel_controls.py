"""Active-channel controls: histogram + exposure/gain/intensity, with persistence."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Signal
from qtpy.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox, QPushButton

from squid.core.events import (
    UpdateChannelConfigurationCommand, SetExposureTimeCommand,
    SetAnalogGainCommand, SetIlluminationCommand,
)


def build_persist_command(objective, config_name, exposure_ms, gain, intensity):
    return UpdateChannelConfigurationCommand(
        objective_name=objective, config_name=config_name,
        exposure_time_ms=exposure_ms, analog_gain=gain, illumination_intensity=intensity)


class ChannelControls(QWidget):
    contrast_changed = Signal(float, float)

    def __init__(self, publish, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._publish = publish
        self._objective: Optional[str] = None
        self._channel: Optional[str] = None
        self._illum_source: Optional[int] = None
        layout = QVBoxLayout(self)
        self._title = QLabel("—")
        layout.addWidget(self._title)
        self._hist = pg.HistogramLUTWidget()
        self._hist.item.sigLevelsChanged.connect(self._on_levels_changed)
        layout.addWidget(self._hist)
        self._exposure = self._spin("Exposure (ms)", 0.01, 5000.0, layout)
        self._gain = self._spin("Gain", 0.0, 100.0, layout)
        self._intensity = self._spin("Intensity (%)", 0.0, 100.0, layout)
        self._exposure.valueChanged.connect(self._on_exposure)
        self._gain.valueChanged.connect(self._on_gain)
        self._intensity.valueChanged.connect(self._on_intensity)
        auto = QPushButton("Auto contrast")
        auto.clicked.connect(self._auto_contrast)
        layout.addWidget(auto)

    def _spin(self, label, lo, hi, layout) -> QDoubleSpinBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        row.addWidget(spin)
        layout.addLayout(row)
        return spin

    def attach_image_item(self, image_item) -> None:
        self._hist.setImageItem(image_item)

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

    def _on_levels_changed(self):
        lo, hi = self._hist.item.getLevels()
        self.contrast_changed.emit(float(lo), float(hi))

    def _auto_contrast(self):
        img = self._hist.item.imageItem()
        if img is not None and img.image is not None:
            lo = float(np.percentile(img.image, 0.5))
            hi = float(np.percentile(img.image, 99.5))
            self._hist.item.setLevels(lo, max(hi, lo + 1.0))

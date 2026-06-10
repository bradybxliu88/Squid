"""Active-channel controls: histogram + exposure + intensity (per-channel)."""
from __future__ import annotations

from typing import Optional

import numpy as np
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox, QPushButton, QSlider,
)

from squid.core.events import (
    UpdateChannelConfigurationCommand, SetExposureTimeCommand, SetIlluminationCommand,
)
from squid.ui.lite.histogram import LiteHistogram


def build_persist_command(objective, config_name, exposure_ms, gain, intensity):
    return UpdateChannelConfigurationCommand(
        objective_name=objective, config_name=config_name,
        exposure_time_ms=exposure_ms, analog_gain=gain, illumination_intensity=intensity)


class ChannelControls(QWidget):
    """Edits the active channel (exposure, intensity, contrast); persists to config."""

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
        lay.setSpacing(8)

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

        self._exposure = QDoubleSpinBox()
        self._exposure.setRange(0.01, 5000.0)
        self._exposure.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self._exposure.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._exposure.setFixedWidth(96)
        self._exposure.valueChanged.connect(self._on_exposure)
        erow = QHBoxLayout()
        erow.setSpacing(8)
        elbl = QLabel("Exposure <span style='color:#5a6477'>(ms)</span>")
        elbl.setObjectName("fieldLabel")
        elbl.setTextFormat(Qt.RichText)
        erow.addWidget(elbl, 1)
        erow.addWidget(self._exposure)
        lay.addLayout(erow)

        self._intensity = QSlider(Qt.Horizontal)
        self._intensity.setRange(0, 100)
        self._intensity.setCursor(Qt.PointingHandCursor)
        self._intensity.valueChanged.connect(self._on_intensity)
        self._intensity_val = QLabel("0%")
        self._intensity_val.setObjectName("sliderVal")
        self._intensity_val.setFixedWidth(40)
        self._intensity_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        irow = QHBoxLayout()
        irow.setSpacing(8)
        ilbl = QLabel("Intensity")
        ilbl.setObjectName("fieldLabel")
        irow.addWidget(ilbl)
        irow.addWidget(self._intensity, 1)
        irow.addWidget(self._intensity_val)
        lay.addLayout(irow)

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, on: bool) -> None:
        for wdg in (self._exposure, self._intensity, self._auto, self._intensity_val):
            wdg.setEnabled(on)

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
        self._set_controls_enabled(True)
        self._exposure.blockSignals(True)
        self._exposure.setValue(channel_config.exposure_time)
        self._exposure.blockSignals(False)
        self._intensity.blockSignals(True)
        self._intensity.setValue(int(round(channel_config.illumination_intensity)))
        self._intensity.blockSignals(False)
        self._intensity_val.setText(f"{int(round(channel_config.illumination_intensity))}%")

    # ---- emit -----------------------------------------------------------
    def _on_exposure(self, v):
        self._publish(SetExposureTimeCommand(exposure_time_ms=v)); self._persist()

    def _on_intensity(self, v):
        self._intensity_val.setText(f"{int(v)}%")
        if self._illum_source is not None:
            self._publish(SetIlluminationCommand(channel=self._illum_source, intensity=float(v), on=True))
        self._persist()

    def _persist(self):
        if self._objective and self._channel:
            self._publish(build_persist_command(
                self._objective, self._channel, self._exposure.value(), None, float(self._intensity.value())))

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

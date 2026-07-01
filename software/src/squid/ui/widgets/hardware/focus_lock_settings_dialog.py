"""Non-modal settings dialog for the focus lock.

Edits camera, spot detection, tolerance, and lock-loop parameters live while
the focus lock is running. All edits publish UpdateLaserAFLiveParamsCommand or
SetFocusLockParamsCommand on the event bus; the controllers apply changes on
the next frame / loop iteration without invalidating initialization.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import squid.core.logging
from _def import SpotDetectionMode
from squid.core.config.focus_lock import FocusLockConfig
from squid.core.events import (
    LaserAFPropertiesChanged,
    SetFocusLockParamsCommand,
    UpdateLaserAFLiveParamsCommand,
    auto_subscribe,
    auto_unsubscribe,
    handles,
)

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


class FocusLockSettingsDialog(QDialog):
    """Live-edit camera + spot detection + lock-loop parameters.

    Designed to stay open beside the FocusLockStatusWidget while the user tunes
    the focus lock. Spinboxes use keyboardTracking=False so each edit commits
    only on Enter / focus-out / arrow click.
    """

    def __init__(
        self,
        event_bus: "UIEventBus",
        initial_laser_af_properties: Dict[str, Any],
        initial_focus_lock_config: Optional[FocusLockConfig] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus
        self._laser_af_properties: Dict[str, Any] = dict(initial_laser_af_properties)
        self._focus_lock_config = initial_focus_lock_config or FocusLockConfig()
        self._suppress_emit = False
        self._subscriptions = []

        self.setWindowTitle("Focus Lock Settings")
        self.setModal(False)
        # Independent top-level window so it stays alongside the dock.
        self.setWindowFlag(Qt.Window, True)
        self.setMinimumWidth(360)

        self._laser_af_spinboxes: Dict[str, QDoubleSpinBox] = {}
        self._lock_loop_spinboxes: Dict[str, QDoubleSpinBox] = {}
        self._spot_mode_combo: Optional[QComboBox] = None

        self._build_ui()
        self._subscriptions = auto_subscribe(self, self._event_bus)
        self.destroyed.connect(self._on_destroyed)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_camera_group())
        root.addWidget(self._build_spot_detection_group())
        root.addWidget(self._build_tolerance_group())
        root.addWidget(self._build_lock_loop_group())

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._close_btn = QPushButton("Close")
        self._close_btn.setAutoDefault(False)
        self._close_btn.clicked.connect(self.close)
        button_row.addWidget(self._close_btn)
        root.addLayout(button_row)

    def _build_camera_group(self) -> QGroupBox:
        group = QGroupBox("Camera")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)

        self._add_laser_af_double(
            form, "Exposure (ms):", "focus_camera_exposure_time_ms",
            minimum=0.1, maximum=1000.0, decimals=2, step=0.1,
        )
        self._add_laser_af_double(
            form, "Analog gain:", "focus_camera_analog_gain",
            minimum=0.0, maximum=24.0, decimals=2, step=0.5,
        )
        return group

    def _build_spot_detection_group(self) -> QGroupBox:
        group = QGroupBox("Spot Detection")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)

        self._spot_mode_combo = QComboBox()
        for mode in SpotDetectionMode:
            self._spot_mode_combo.addItem(mode.value, mode)
        current_mode = self._laser_af_properties.get(
            "spot_detection_mode", SpotDetectionMode.SINGLE
        )
        if isinstance(current_mode, str):
            try:
                current_mode = SpotDetectionMode(current_mode)
            except Exception:
                current_mode = SpotDetectionMode.SINGLE
        idx = self._spot_mode_combo.findData(current_mode)
        self._spot_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._spot_mode_combo.currentIndexChanged.connect(self._on_spot_mode_changed)
        form.addRow("Mode:", self._spot_mode_combo)

        self._add_laser_af_double(
            form, "Y window (px):", "y_window",
            minimum=1, maximum=500, decimals=0, step=1,
        )
        self._add_laser_af_double(
            form, "X window (px):", "x_window",
            minimum=1, maximum=500, decimals=0, step=1,
        )
        self._add_laser_af_double(
            form, "Min peak width:", "min_peak_width",
            minimum=1, maximum=100, decimals=1, step=0.5,
        )
        self._add_laser_af_double(
            form, "Min peak distance:", "min_peak_distance",
            minimum=1, maximum=100, decimals=1, step=0.5,
        )
        self._add_laser_af_double(
            form, "Min peak prominence:", "min_peak_prominence",
            minimum=0.01, maximum=1.0, decimals=2, step=0.05,
        )
        self._add_laser_af_double(
            form, "Spot spacing (px):", "spot_spacing",
            minimum=1, maximum=1000, decimals=1, step=1,
        )
        self._add_laser_af_double(
            form, "Filter sigma:", "filter_sigma",
            minimum=0, maximum=100, decimals=1, step=0.5, allow_none=True,
        )
        return group

    def _build_tolerance_group(self) -> QGroupBox:
        group = QGroupBox("Detection Tolerance")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)

        self._add_laser_af_double(
            form, "Averaging N:", "laser_af_averaging_n",
            minimum=1, maximum=100, decimals=0, step=1,
        )
        self._add_laser_af_double(
            form, "Success window (μm):", "displacement_success_window_um",
            minimum=0.1, maximum=10.0, decimals=2, step=0.1,
        )
        self._add_laser_af_double(
            form, "Correlation threshold:", "correlation_threshold",
            minimum=0.1, maximum=1.0, decimals=2, step=0.05,
        )
        self._add_laser_af_double(
            form, "AF range (μm):", "laser_af_range",
            minimum=1, maximum=1000, decimals=1, step=1,
        )
        return group

    def _build_lock_loop_group(self) -> QGroupBox:
        group = QGroupBox("Lock Loop")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)

        self._add_lock_loop_int(
            form, "Buffer length (good readings):", "buffer_length",
            minimum=1, maximum=100,
        )
        self._add_lock_loop_int(
            form, "Recovery attempts:", "recovery_attempts",
            minimum=1, maximum=100,
        )
        self._add_lock_loop_double(
            form, "Min spot SNR:", "min_spot_snr",
            minimum=0.0, maximum=1000.0, decimals=2, step=0.5,
        )
        self._add_lock_loop_double(
            form, "Acquire threshold (μm):", "acquire_threshold_um",
            minimum=0.01, maximum=100.0, decimals=2, step=0.05,
        )
        self._add_lock_loop_double(
            form, "Maintain threshold (μm):", "maintain_threshold_um",
            minimum=0.01, maximum=100.0, decimals=2, step=0.05,
        )
        return group

    # -------------------------------------------------------- spinbox helpers

    def _add_laser_af_double(
        self,
        form: QFormLayout,
        label: str,
        property_name: str,
        *,
        minimum: float,
        maximum: float,
        decimals: int,
        step: float,
        allow_none: bool = False,
    ) -> None:
        spinbox = QDoubleSpinBox()
        spinbox.setKeyboardTracking(False)
        if allow_none:
            spinbox.setRange(minimum - step, maximum)
            spinbox.setSpecialValueText("None")
        else:
            spinbox.setRange(minimum, maximum)
        spinbox.setDecimals(decimals)
        spinbox.setSingleStep(step)
        current = self._laser_af_properties.get(property_name)
        if allow_none and current is None:
            spinbox.setValue(minimum - step)
        elif current is not None:
            spinbox.setValue(float(current))
        spinbox.valueChanged.connect(
            lambda value, name=property_name, allow=allow_none, mn=minimum, st=step:
            self._on_laser_af_value_changed(name, value, allow, mn - st)
        )
        self._laser_af_spinboxes[property_name] = spinbox
        form.addRow(label, spinbox)

    def _add_lock_loop_double(
        self,
        form: QFormLayout,
        label: str,
        field_name: str,
        *,
        minimum: float,
        maximum: float,
        decimals: int,
        step: float,
    ) -> None:
        spinbox = QDoubleSpinBox()
        spinbox.setKeyboardTracking(False)
        spinbox.setRange(minimum, maximum)
        spinbox.setDecimals(decimals)
        spinbox.setSingleStep(step)
        current = getattr(self._focus_lock_config, field_name, None)
        if current is not None:
            spinbox.setValue(float(current))
        spinbox.valueChanged.connect(
            lambda value, name=field_name: self._on_lock_loop_value_changed(name, value)
        )
        self._lock_loop_spinboxes[field_name] = spinbox
        form.addRow(label, spinbox)

    def _add_lock_loop_int(
        self,
        form: QFormLayout,
        label: str,
        field_name: str,
        *,
        minimum: int,
        maximum: int,
    ) -> None:
        spinbox = QSpinBox()
        spinbox.setKeyboardTracking(False)
        spinbox.setRange(minimum, maximum)
        current = getattr(self._focus_lock_config, field_name, None)
        if current is not None:
            spinbox.setValue(int(current))
        spinbox.valueChanged.connect(
            lambda value, name=field_name: self._on_lock_loop_value_changed(name, value)
        )
        self._lock_loop_spinboxes[field_name] = spinbox
        form.addRow(label, spinbox)

    # ------------------------------------------------------------- handlers

    def _on_laser_af_value_changed(
        self,
        property_name: str,
        value: float,
        allow_none: bool,
        none_sentinel: float,
    ) -> None:
        if self._suppress_emit:
            return
        if allow_none and value <= none_sentinel + 1e-9:
            applied: Any = None
        elif property_name in {
            "y_window",
            "x_window",
            "laser_af_averaging_n",
        }:
            applied = int(round(value))
        else:
            applied = float(value)
        self._laser_af_properties[property_name] = applied
        self._event_bus.publish(
            UpdateLaserAFLiveParamsCommand(updates={property_name: applied})
        )

    def _on_lock_loop_value_changed(self, field_name: str, value: float) -> None:
        if self._suppress_emit:
            return
        kwargs: Dict[str, Any] = {}
        if field_name in {"buffer_length", "recovery_attempts"}:
            kwargs[field_name] = int(value)
        else:
            kwargs[field_name] = float(value)
        self._event_bus.publish(SetFocusLockParamsCommand(**kwargs))

    def _on_spot_mode_changed(self, _index: int) -> None:
        if self._suppress_emit or self._spot_mode_combo is None:
            return
        mode = self._spot_mode_combo.currentData()
        if mode is None:
            return
        self._laser_af_properties["spot_detection_mode"] = mode
        self._event_bus.publish(
            UpdateLaserAFLiveParamsCommand(updates={"spot_detection_mode": mode})
        )

    @handles(LaserAFPropertiesChanged)
    def _on_properties_changed(self, event: LaserAFPropertiesChanged) -> None:
        """Sync spinboxes if properties were changed elsewhere."""
        if not event.properties:
            return
        self._laser_af_properties.update(event.properties)
        self._suppress_emit = True
        try:
            for name, value in event.properties.items():
                spinbox = self._laser_af_spinboxes.get(name)
                if spinbox is not None and value is not None:
                    spinbox.setValue(float(value))
                elif spinbox is not None and value is None:
                    spinbox.setValue(spinbox.minimum())
                if name == "spot_detection_mode" and self._spot_mode_combo is not None:
                    mode = value
                    if isinstance(mode, str):
                        try:
                            mode = SpotDetectionMode(mode)
                        except Exception:
                            mode = None
                    if mode is not None:
                        idx = self._spot_mode_combo.findData(mode)
                        if idx >= 0:
                            self._spot_mode_combo.setCurrentIndex(idx)
        finally:
            self._suppress_emit = False

    # ----------------------------------------------------------- lifecycle

    def _on_destroyed(self, _obj: object = None) -> None:
        try:
            auto_unsubscribe(self._subscriptions, self._event_bus)
        except Exception:
            self._log.exception("Failed to unsubscribe focus lock settings dialog")

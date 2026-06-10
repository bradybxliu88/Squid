"""GUI tab showing live status of the Squid laser engine (EventBus-driven)."""

from __future__ import annotations

from typing import Dict, Optional, TYPE_CHECKING

from qtpy.QtGui import QColor
from qtpy.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from squid.backend.drivers.lighting.squid_laser_engine import (
    LASER_CHANNEL_ORDER,
    LaserChannelState,
    SquidLaserEngineStatus,
)
from squid.core.events import (
    LaserEngineConnectionLost,
    LaserEngineStatusUpdated,
    SleepAllLasersCommand,
    WakeAllLasersCommand,
)

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


def _engine_summary_color(status: Optional[SquidLaserEngineStatus], connection_lost: bool) -> QColor:
    if connection_lost or status is None:
        return QColor("#c0392b")
    if status.any_error():
        return QColor("#c0392b")
    states = [info.display_state for info in status.channels.values()]
    if any(s in (LaserChannelState.SLEEP, LaserChannelState.PREPARE_SLEEP) for s in states):
        return QColor("#888888")
    if all(s == LaserChannelState.ACTIVE for s in states):
        return QColor("#2e8b57")
    return QColor("#daa520")


def _engine_summary_label(status: Optional[SquidLaserEngineStatus], connection_lost: bool) -> str:
    if connection_lost:
        return "Disconnected"
    if status is None:
        return "Waiting…"
    if status.any_error():
        return "Error"
    states = [info.display_state for info in status.channels.values()]
    if all(s == LaserChannelState.ACTIVE for s in states):
        return "Ready"
    if any(s in (LaserChannelState.SLEEP, LaserChannelState.PREPARE_SLEEP) for s in states):
        return "Sleeping"
    return "Warming up"


def _format_temp(info) -> str:
    return "/".join(f"{m.temperature_c:.1f}" for m in info.modules) + " °C"


def _format_diff(info) -> str:
    return "/".join(f"{m.setpoint_diff_c:+.1f}" for m in info.modules)


class LaserEngineWidget(QWidget):
    """Tab content showing live status of the Squid laser engine.

    Status arrives via the EventBus (LaserEngineStatusUpdated / ...ConnectionLost);
    Wake/Sleep buttons publish commands the LaserEngineService handles.
    """

    def __init__(self, event_bus: "UIEventBus", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._event_bus = event_bus
        self._last_status: Optional[SquidLaserEngineStatus] = None
        self._connection_lost = False
        self._channel_lines: Dict[str, QLabel] = {}
        self._build_ui()
        if event_bus is not None:
            event_bus.subscribe(LaserEngineStatusUpdated, self._on_status_updated)
            event_bus.subscribe(LaserEngineConnectionLost, self._on_connection_lost)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self._engine_dot = QLabel("●")
        self._engine_dot.setStyleSheet("font-size: 16pt; color: #888888;")
        self._engine_label = QLabel("Engine: Waiting…")
        self._wake_btn = QPushButton("Wake All")
        self._sleep_btn = QPushButton("Sleep All")
        self._wake_btn.clicked.connect(self._on_wake_all_clicked)
        self._sleep_btn.clicked.connect(self._on_sleep_all_clicked)
        top_row.addWidget(self._engine_dot)
        top_row.addWidget(self._engine_label)
        top_row.addStretch(1)
        top_row.addWidget(self._wake_btn)
        top_row.addWidget(self._sleep_btn)
        layout.addLayout(top_row)

        self._banner = QLabel("")
        self._banner.setStyleSheet("background-color: #c0392b; color: white; padding: 4px;")
        self._banner.setVisible(False)
        layout.addWidget(self._banner)

        for key in LASER_CHANNEL_ORDER:
            line = QLabel("—")
            line.setStyleSheet("font-family: monospace;")
            self._channel_lines[key] = line
            layout.addWidget(line)

        layout.addStretch(1)

    def _on_wake_all_clicked(self) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(WakeAllLasersCommand())

    def _on_sleep_all_clicked(self) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(SleepAllLasersCommand())

    def _on_status_updated(self, event: LaserEngineStatusUpdated) -> None:
        self._last_status = event.status
        # A fresh status means the link is back; clear any stale disconnect banner.
        self._connection_lost = False
        self._banner.setVisible(False)
        self._refresh_channel_lines()
        self._refresh_summary()

    def _on_connection_lost(self, event: LaserEngineConnectionLost) -> None:
        self._connection_lost = True
        self._banner.setText(f"Laser engine disconnected: {event.message}")
        self._banner.setVisible(True)
        self._refresh_summary()

    def _refresh_channel_lines(self) -> None:
        if self._last_status is None:
            return
        for key in LASER_CHANNEL_ORDER:
            info = self._last_status.channels.get(key)
            if info is None:
                continue
            state = info.display_state
            on_off = "ON" if info.laser_ttl_on else "OFF"
            self._channel_lines[key].setText(
                f"{key:>4}  {on_off:<3}  {state.name:<14}  {_format_temp(info)}  ΔT {_format_diff(info)}"
            )

    def _refresh_summary(self) -> None:
        color = _engine_summary_color(self._last_status, self._connection_lost)
        self._engine_dot.setStyleSheet(f"font-size: 16pt; color: {color.name()};")
        self._engine_label.setText(f"Engine: {_engine_summary_label(self._last_status, self._connection_lost)}")

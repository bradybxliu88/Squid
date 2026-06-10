"""Tests for LaserEngineWidget — summary helpers + construction (ports #536)."""

import pytest

from squid.backend.drivers.lighting.squid_laser_engine import (
    LaserChannelInfo,
    LaserChannelState,
    SquidLaserEngineStatus,
    TcmModuleInfo,
)
from squid.core.events import LaserEngineStatusUpdated
from squid.ui.widgets.hardware.laser_engine import (
    LaserEngineWidget,
    _engine_summary_color,
    _engine_summary_label,
)


def _module(state, idx=0):
    return TcmModuleInfo(
        module_index=idx,
        state=state,
        temperature_c=25.0,
        setpoint_c=25.0,
        setpoint_diff_c=0.0,
        tec_voltage=0.5,
        tec_current=0.1,
        hi_temp_setpoint_c=99.7,
    )


def _status(state):
    channels = {
        key: LaserChannelInfo(key=key, laser_ttl_on=False, modules=(_module(state),))
        for key in ("405", "470", "55x", "638", "730")
    }
    return SquidLaserEngineStatus(channels=channels, timestamp_s=0.0)


def test_summary_label_states():
    assert _engine_summary_label(None, False) == "Waiting…"
    assert _engine_summary_label(None, True) == "Disconnected"
    assert _engine_summary_label(_status(LaserChannelState.ACTIVE), False) == "Ready"
    assert _engine_summary_label(_status(LaserChannelState.WARMING_UP), False) == "Warming up"
    assert _engine_summary_label(_status(LaserChannelState.SLEEP), False) == "Sleeping"
    assert _engine_summary_label(_status(LaserChannelState.ERROR), False) == "Error"


def test_summary_color_states():
    assert _engine_summary_color(None, True).name() == "#c0392b"  # disconnected → red
    assert _engine_summary_color(_status(LaserChannelState.ACTIVE), False).name() == "#2e8b57"  # ready → green
    assert _engine_summary_color(_status(LaserChannelState.ERROR), False).name() == "#c0392b"  # error → red
    assert _engine_summary_color(_status(LaserChannelState.SLEEP), False).name() == "#888888"  # sleeping → grey


def test_widget_renders_status_event(qapp):
    w = LaserEngineWidget(event_bus=None)
    # Drive a status directly (no bus) and confirm the summary updates.
    w._on_status_updated(LaserEngineStatusUpdated(status=_status(LaserChannelState.ACTIVE)))
    assert "Ready" in w._engine_label.text()
    assert "405" in w._channel_lines["405"].text()
    w.close()


def test_widget_shows_disconnect_banner(qapp):
    from squid.core.events import LaserEngineConnectionLost

    w = LaserEngineWidget(event_bus=None)
    w._on_connection_lost(LaserEngineConnectionLost(message="boom"))
    # isHidden() reflects the explicit setVisible flag (isVisible() needs a shown parent).
    assert not w._banner.isHidden()
    assert "boom" in w._banner.text()
    # A fresh status clears the banner.
    w._on_status_updated(LaserEngineStatusUpdated(status=_status(LaserChannelState.ACTIVE)))
    assert w._banner.isHidden()
    w.close()


def test_widget_shows_and_clears_warning(qapp):
    from squid.core.events import LaserEngineWarning

    w = LaserEngineWidget(event_bus=None)
    w._on_warning(LaserEngineWarning(message="Laser warming up (channel 470)"))
    assert not w._warning_label.isHidden()
    assert "470" in w._warning_label.text()
    # A fresh status clears the transient warning.
    w._on_status_updated(LaserEngineStatusUpdated(status=_status(LaserChannelState.ACTIVE)))
    assert w._warning_label.isHidden()
    w.close()

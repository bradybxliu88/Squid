"""Sensor mode UI: CameraSettingsWidget dropdown and LiveControlWidget exposure-limit refresh.

Ports the GUI behavior of upstream 875eda71 to the EventBus pattern: the dropdown publishes
SetSensorModeCommand (never touching the camera), and both widgets react to SensorModeChanged.
"""

from dataclasses import dataclass
from typing import Any, List, Tuple

import pytest

from squid.core.events import (
    SensorModeChanged,
    SetExposureTimeCommand,
    SetSensorModeCommand,
    UpdateChannelConfigurationCommand,
)


class FakeBus:
    def __init__(self) -> None:
        self.published: List[Any] = []
        self.subscriptions: List[Tuple[type, Any]] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)

    def subscribe(self, event_type: type, handler: Any) -> None:
        self.subscriptions.append((event_type, handler))

    def unsubscribe(self, event_type: type, handler: Any) -> None:
        pass


def _settings_widget(bus, sensor_modes=None, current_sensor_mode=None, exposure_limits=(0.1, 1000.0)):
    from squid.ui.widgets.camera.settings import CameraSettingsWidget

    return CameraSettingsWidget(
        event_bus=bus,
        exposure_limits=exposure_limits,
        pixel_format_names=["MONO8"],
        binning_options=[(1, 1)],
        current_binning=(1, 1),
        sensor_modes=sensor_modes,
        current_sensor_mode=current_sensor_mode,
    )


class TestCameraSettingsWidgetSensorMode:
    def test_dropdown_shows_modes_and_current_mode(self, qapp):
        widget = _settings_widget(FakeBus(), ["Ultra Quiet", "Standard", "Fast"], "Standard")

        dropdown = widget.dropdown_sensorMode
        assert dropdown is not None
        assert [dropdown.itemText(i) for i in range(dropdown.count())] == ["Ultra Quiet", "Standard", "Fast"]
        assert dropdown.currentText() == "Standard"

    def test_no_dropdown_when_camera_has_no_modes(self, qapp):
        widget = _settings_widget(FakeBus(), sensor_modes=[])

        assert widget.dropdown_sensorMode is None

    def test_selection_publishes_command(self, qapp):
        bus = FakeBus()
        widget = _settings_widget(bus, ["Ultra Quiet", "Standard", "Fast"], "Standard")
        bus.published.clear()

        widget.dropdown_sensorMode.setCurrentText("Ultra Quiet")

        assert bus.published == [SetSensorModeCommand(sensor_mode="Ultra Quiet")]

    def test_subscribes_to_sensor_mode_changed(self, qapp):
        bus = FakeBus()
        _settings_widget(bus, ["standard", "fast"], "standard")

        assert SensorModeChanged in {event_type for event_type, _ in bus.subscriptions}

    def test_state_event_syncs_dropdown_without_publishing(self, qapp):
        """A rejected selection is reverted by the service's SensorModeChanged."""
        bus = FakeBus()
        widget = _settings_widget(bus, ["Ultra Quiet", "Standard", "Fast"], "Standard")
        widget.dropdown_sensorMode.setCurrentText("Fast")
        bus.published.clear()

        widget._on_sensor_mode_changed(SensorModeChanged(sensor_mode="Standard", exposure_limits_ms=(0.1, 1000.0)))

        assert widget.dropdown_sensorMode.currentText() == "Standard"
        assert not any(isinstance(e, SetSensorModeCommand) for e in bus.published)

    def test_state_event_refreshes_exposure_limits(self, qapp):
        bus = FakeBus()
        widget = _settings_widget(bus, ["standard", "fast"], "standard")
        widget.entry_exposureTime.setValue(1.0)
        bus.published.clear()

        widget._on_sensor_mode_changed(SensorModeChanged(sensor_mode="fast", exposure_limits_ms=(5.0, 500.0)))

        assert widget.entry_exposureTime.minimum() == pytest.approx(5.0)
        assert widget.entry_exposureTime.maximum() == pytest.approx(500.0)
        # The clamped exposure propagates like a normal edit.
        assert bus.published == [SetExposureTimeCommand(exposure_time_ms=pytest.approx(5.0))]


@dataclass
class FakeChannelMode:
    name: str
    exposure_time: float
    analog_gain: float
    illumination_intensity: float


class FakeStreamHandler:
    def set_display_fps(self, fps: float) -> None:
        self.fps = fps


def _live_widget(bus, exposure_time=1.0):
    from squid.ui.widgets.camera.live_control import LiveControlWidget

    return LiveControlWidget(
        event_bus=bus,
        streamHandler=FakeStreamHandler(),
        initial_configuration=FakeChannelMode("BF", exposure_time, 0.0, 50.0),
        initial_objective="20x",
        initial_channel_configs=["BF"],
        exposure_limits=(0.1, 1000.0),
    )


class TestLiveControlWidgetSensorMode:
    def test_subscribes_to_sensor_mode_changed(self, qapp):
        bus = FakeBus()
        _live_widget(bus)

        assert SensorModeChanged in {event_type for event_type, _ in bus.subscriptions}

    def test_new_limits_clamp_exposure_into_channel_config(self, qapp):
        bus = FakeBus()
        widget = _live_widget(bus, exposure_time=1.0)
        bus.published.clear()

        widget._on_sensor_mode_changed(SensorModeChanged(sensor_mode="Ultra Quiet", exposure_limits_ms=(5.0, 500.0)))

        assert widget.entry_exposureTime.minimum() == pytest.approx(5.0)
        assert widget.entry_exposureTime.maximum() == pytest.approx(500.0)
        assert widget.entry_exposureTime.value() == pytest.approx(5.0)
        updates = [e for e in bus.published if isinstance(e, UpdateChannelConfigurationCommand)]
        assert len(updates) == 1
        assert updates[0].config_name == "BF"
        assert updates[0].exposure_time_ms == pytest.approx(5.0)

    def test_limits_without_clamp_publish_nothing(self, qapp):
        bus = FakeBus()
        widget = _live_widget(bus, exposure_time=20.0)
        bus.published.clear()

        widget._on_sensor_mode_changed(SensorModeChanged(sensor_mode="Fast", exposure_limits_ms=(0.01, 2000.0)))

        assert widget.entry_exposureTime.maximum() == pytest.approx(2000.0)
        assert widget.entry_exposureTime.value() == pytest.approx(20.0)
        assert bus.published == []

    def test_event_without_limits_is_ignored(self, qapp):
        bus = FakeBus()
        widget = _live_widget(bus)

        widget._on_sensor_mode_changed(SensorModeChanged(sensor_mode="Fast"))

        assert widget.entry_exposureTime.minimum() == pytest.approx(0.1)
        assert widget.entry_exposureTime.maximum() == pytest.approx(1000.0)

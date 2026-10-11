"""Tests for DragonflyConfocalWidget: what it shows must match the Dragonfly (ports upstream 432772be)."""

import logging

from squid.backend.drivers.lighting.dragonfly import Dragonfly_Simulation
from squid.backend.drivers.peripherals.serial_base import SerialDeviceError
from squid.core.events import SetConfocalModeCommand
from squid.ui.widgets.hardware.confocal import DragonflyConfocalWidget


class HardwareNamedDragonfly(Dragonfly_Simulation):
    """A simulated Dragonfly that names its components the way the hardware reports them.

    The stock simulation lists bare position numbers, which hid that the widget matched the
    reported position against the entry text instead of selecting it by index.
    """

    def get_emission_filter_info(self, port):
        names = ["445/45", "525/50", "600/50", "700/75", "Empty", "Empty", "Empty", "Empty"]
        return [f"{i}:{name}" for i, name in enumerate(names, start=1)]

    def get_field_aperture_info(self):
        return [f"Aperture {i}" for i in range(1, 11)]

    def get_port_selection_dichroic_info(self):
        return ["100% Pass", "Dichroic 1", "Dichroic 2", "100% Reflect"]


class MuteDragonfly(HardwareNamedDragonfly):
    """A Dragonfly that does not answer the modality query."""

    def get_modality(self):
        raise SerialDeviceError("Max attempts reached without receiving response.")


class StuckDragonfly(HardwareNamedDragonfly):
    """A Dragonfly that reports its state but refuses to switch modality."""

    def set_modality(self, modality):
        raise SerialDeviceError("Dragonfly command failed: AT_MODALITY,CONFOCAL -> :N")


class NoPort2WheelDragonfly(HardwareNamedDragonfly):
    """A Dragonfly with no emission filter wheel on camera port 2."""

    def get_emission_filter(self, port):
        if port == 2:
            raise SerialDeviceError("Dragonfly command failed: AT_FW_POS,2,? -> :N")
        return super().get_emission_filter(port)


class RecordingBus:
    """Minimal stand-in for UIEventBus: records published events, accepts subscriptions."""

    def __init__(self):
        self.published = []

    def publish(self, event):
        self.published.append(event)

    def subscribe(self, event_type, handler):
        pass

    def unsubscribe(self, event_type, handler):
        pass


def make_widget(qtbot, dragonfly, event_bus=None, initial_objective=None):
    widget = DragonflyConfocalWidget(dragonfly, event_bus=event_bus, initial_objective=initial_objective)
    qtbot.addWidget(widget)
    return widget


def test_dropdowns_show_the_positions_the_unit_reports(qtbot):
    sim = HardwareNamedDragonfly()
    sim.dichroic_position = 3
    sim.emission_filter_positions[1] = 4
    sim.emission_filter_positions[2] = 8
    sim.field_aperture_positions[1] = 7
    sim.disk_motor_running = True
    sim.current_modality = "CONFOCAL"

    widget = make_widget(qtbot, sim)

    assert widget.dropdown_dichroic.currentIndex() == 2
    assert widget.dropdown_port1_emission_filter.currentIndex() == 3
    assert widget.dropdown_port2_emission_filter.currentIndex() == 7
    assert widget.dropdown_field_aperture.currentIndex() == 6
    assert widget.btn_disk_motor.isChecked()
    assert widget.confocal_mode is True
    assert widget.btn_toggle_confocal.text() == "Switch to Widefield"


def test_showing_the_initial_positions_does_not_move_anything(qtbot):
    sim = HardwareNamedDragonfly()
    sim.dichroic_position = 3
    sim.field_aperture_positions[1] = 7
    moved = []
    sim.set_port_selection_dichroic = lambda position: moved.append(("dichroic", position))
    sim.set_emission_filter = lambda port, position: moved.append(("filter", port, position))
    sim.set_field_aperture_wheel_position = lambda position: moved.append(("aperture", position))

    make_widget(qtbot, sim)

    assert moved == []


def test_an_out_of_range_position_selects_no_entry(qtbot):
    sim = HardwareNamedDragonfly()
    sim.field_aperture_positions[1] = 99

    widget = make_widget(qtbot, sim)

    assert widget.dropdown_field_aperture.currentIndex() == -1


def test_widget_builds_when_the_unit_cannot_report_its_modality(qtbot, caplog):
    with caplog.at_level(logging.ERROR):
        widget = make_widget(qtbot, MuteDragonfly())

    assert widget.confocal_mode is False
    assert widget.btn_toggle_confocal.text() == "Switch to Confocal"
    assert any("modality" in record.getMessage() for record in caplog.records)


def test_one_unreadable_wheel_does_not_hide_the_rest_of_the_state(qtbot):
    sim = NoPort2WheelDragonfly()
    sim.dichroic_position = 4
    sim.field_aperture_positions[1] = 7
    sim.disk_motor_running = True

    widget = make_widget(qtbot, sim)

    assert widget.dropdown_port2_emission_filter.currentIndex() == -1
    assert widget.dropdown_dichroic.currentIndex() == 3
    assert widget.dropdown_field_aperture.currentIndex() == 6
    assert widget.btn_disk_motor.isChecked()


def test_toggle_switches_the_unit_and_publishes_the_new_mode(qtbot):
    sim = HardwareNamedDragonfly()
    bus = RecordingBus()
    widget = make_widget(qtbot, sim, event_bus=bus, initial_objective="20x")
    bus.published.clear()  # drop the initial-state publish

    widget.btn_toggle_confocal.click()

    assert sim.get_modality() == "CONFOCAL"
    assert bus.published == [SetConfocalModeCommand(objective_name="20x", is_confocal=True)]
    assert widget.btn_toggle_confocal.text() == "Switch to Widefield"


def test_failed_modality_switch_keeps_widefield_and_logs(qtbot, caplog):
    bus = RecordingBus()
    widget = make_widget(qtbot, StuckDragonfly(), event_bus=bus, initial_objective="20x")
    bus.published.clear()

    with caplog.at_level(logging.ERROR):
        widget.btn_toggle_confocal.click()

    assert widget.confocal_mode is False
    assert bus.published == []
    assert widget.btn_toggle_confocal.isEnabled()
    assert any("AT_MODALITY,CONFOCAL" in record.getMessage() for record in caplog.records)


def test_field_aperture_dropdown_moves_the_wheel(qtbot):
    """Regression: the simulation's aperture setter took (port, position), so the widget's
    one-argument call raised TypeError and the aperture never moved in simulation."""
    sim = Dragonfly_Simulation()
    widget = make_widget(qtbot, sim)

    widget.dropdown_field_aperture.setCurrentIndex(4)

    assert sim.get_field_aperture_wheel_position() == 5


def test_emission_filter_dropdowns_move_their_own_port(qtbot):
    sim = HardwareNamedDragonfly()
    widget = make_widget(qtbot, sim)

    widget.dropdown_port1_emission_filter.setCurrentIndex(2)
    widget.dropdown_port2_emission_filter.setCurrentIndex(7)

    assert sim.get_emission_filter(1) == 3
    assert sim.get_emission_filter(2) == 8

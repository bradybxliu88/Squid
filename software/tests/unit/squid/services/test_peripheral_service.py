# tests/squid/services/test_peripheral_service.py
"""Tests for PeripheralService."""

import threading
from unittest.mock import Mock

import pytest


class TestPeripheralService:
    """Test suite for PeripheralService."""

    def test_set_dac_calls_hardware(self):
        """set_dac should call microcontroller.analog_write_onboard_DAC."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()

        service = PeripheralService(mock_mcu, bus)
        service.set_dac(channel=0, percentage=50.0)

        # 50% of 65535 = 32767 (rounded)
        mock_mcu.analog_write_onboard_DAC.assert_called_once_with(0, 32768)

    def test_set_dac_clamps_percentage(self):
        """set_dac should clamp percentage to 0-100."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        # Over 100%
        service.set_dac(channel=0, percentage=150.0)
        mock_mcu.analog_write_onboard_DAC.assert_called_with(0, 65535)

        # Under 0%
        service.set_dac(channel=1, percentage=-10.0)
        mock_mcu.analog_write_onboard_DAC.assert_called_with(1, 0)

    def test_set_dac_publishes_event(self):
        """set_dac should publish DACValueChanged event."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, DACValueChanged

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        received = []
        bus.subscribe(DACValueChanged, lambda e: received.append(e))

        service.set_dac(channel=0, percentage=50.0)
        bus.drain()

        assert len(received) == 1
        assert received[0].channel == 0
        assert received[0].value == 50.0

    def test_handles_set_dac_command(self):
        """Should respond to SetDACCommand events."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, SetDACCommand

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        # Publish command
        bus.publish(SetDACCommand(channel=1, value=75.0))
        bus.drain()

        # Should have called hardware (75% of 65535 = 49151.25 → 49151)
        mock_mcu.analog_write_onboard_DAC.assert_called_once_with(1, 49151)

    def test_handles_start_trigger_command(self):
        """Should respond to StartCameraTriggerCommand events."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, StartCameraTriggerCommand

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        bus.publish(StartCameraTriggerCommand())
        bus.drain()

        mock_mcu.start_camera_trigger.assert_called_once()

    def test_handles_stop_trigger_command(self):
        """Should respond to StopCameraTriggerCommand events."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, StopCameraTriggerCommand

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        bus.publish(StopCameraTriggerCommand())
        bus.drain()

        mock_mcu.stop_camera_trigger.assert_called_once()

    def test_handles_set_trigger_frequency_command(self):
        """Should respond to SetCameraTriggerFrequencyCommand events."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, SetCameraTriggerFrequencyCommand

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        bus.publish(SetCameraTriggerFrequencyCommand(fps=12.5))
        bus.drain()

        mock_mcu.set_camera_trigger_frequency.assert_called_once_with(12.5)

    def test_handles_af_laser_commands(self):
        """Should respond to AF laser on/off commands and wait when requested."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import (
            EventBus,
            TurnOnAFLaserCommand,
            TurnOffAFLaserCommand,
        )

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        bus.publish(TurnOnAFLaserCommand())
        bus.publish(TurnOffAFLaserCommand())
        bus.drain()

        mock_mcu.turn_on_AF_laser.assert_called_once()
        mock_mcu.turn_off_AF_laser.assert_called_once()
        assert mock_mcu.wait_till_operation_is_completed.call_count == 2

    def test_af_laser_mode_gate_bypass(self):
        """Bypass should skip mode gate checks for backend controllers."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus

        class _Gate:
            def blocked_for_ui_hardware_commands(self):
                return True

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus, mode_gate=_Gate())

        service.turn_on_af_laser()
        service.turn_off_af_laser()
        assert mock_mcu.turn_on_AF_laser.call_count == 0
        assert mock_mcu.turn_off_AF_laser.call_count == 0

        service.turn_on_af_laser(bypass_mode_gate=True)
        service.turn_off_af_laser(bypass_mode_gate=True)
        assert mock_mcu.turn_on_AF_laser.call_count == 1
        assert mock_mcu.turn_off_AF_laser.call_count == 1

    def test_add_joystick_button_listener(self):
        """add_joystick_button_listener should delegate to microcontroller."""
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        listener = Mock()
        service.add_joystick_button_listener(listener)

        mock_mcu.add_joystick_button_listener.assert_called_once_with(listener)


class TestPeripheralServiceBusy:
    """is_busy() feeds the live trigger hold; wait_for_idle() guards snap and acquisition start."""

    @staticmethod
    def _service(mcu):
        from squid.backend.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus

        return PeripheralService(mcu, EventBus())

    def test_is_busy_passes_through_the_microcontroller_flag(self):
        mock_mcu = Mock()
        service = self._service(mock_mcu)

        mock_mcu.is_busy.return_value = True
        assert service.is_busy() is True
        mock_mcu.is_busy.return_value = False
        assert service.is_busy() is False

    def test_is_busy_without_microcontroller_is_false(self):
        assert self._service(None).is_busy() is False

    def test_is_busy_does_not_wait_for_the_service_lock(self):
        # wait_till_operation_is_completed() holds the service lock for up to its whole timeout;
        # the live trigger timer polls is_busy() every frame and must not stall behind it.
        mock_mcu = Mock()
        mock_mcu.is_busy.return_value = True
        service = self._service(mock_mcu)

        lock_held = threading.Event()
        release = threading.Event()

        def hold_lock():
            with service._lock:
                lock_held.set()
                release.wait(5)

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        try:
            assert lock_held.wait(5)
            answer = []
            reader = threading.Thread(target=lambda: answer.append(service.is_busy()), daemon=True)
            reader.start()
            reader.join(1)
            assert answer == [True], "is_busy() blocked on the service lock"
        finally:
            release.set()
            holder.join(5)

    def test_wait_for_idle_raises_when_microcontroller_stays_busy(self):
        mock_mcu = Mock()
        mock_mcu.wait_till_operation_is_completed.side_effect = TimeoutError("busy")
        service = self._service(mock_mcu)

        with pytest.raises(TimeoutError):
            service.wait_for_idle(timeout_s=1.5)
        mock_mcu.wait_till_operation_is_completed.assert_called_once_with(1.5)

    def test_wait_for_idle_treats_a_latched_aborted_command_as_idle(self):
        # An earlier aborted command stays latched until the next send; the MCU is not executing
        # it, so it must not block a snap or an acquisition start.
        from squid.backend.microcontroller import CommandAborted

        mock_mcu = Mock()
        mock_mcu.wait_till_operation_is_completed.side_effect = CommandAborted(command_id=7, reason="no ack")
        service = self._service(mock_mcu)

        service.wait_for_idle(timeout_s=1.0)  # must not raise

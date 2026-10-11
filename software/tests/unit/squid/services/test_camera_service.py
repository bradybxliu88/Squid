# tests/squid/services/test_camera_service.py
"""Tests for CameraService."""

import threading
from unittest.mock import MagicMock, Mock

import pytest


class TestCameraService:
    """Test suite for CameraService."""

    def test_set_exposure_time_calls_camera(self):
        """set_exposure_time should call camera.set_exposure_time."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)
        service.set_exposure_time(100.0)

        mock_camera.set_exposure_time.assert_called_once_with(100.0)

    def test_set_exposure_clamps_to_limits(self):
        """set_exposure_time should clamp to camera limits."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (1.0, 500.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        # Over max
        service.set_exposure_time(1000.0)
        mock_camera.set_exposure_time.assert_called_with(500.0)

        # Under min
        service.set_exposure_time(0.1)
        mock_camera.set_exposure_time.assert_called_with(1.0)

    def test_set_exposure_publishes_event(self):
        """set_exposure_time should publish ExposureTimeChanged."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus, ExposureTimeChanged

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(ExposureTimeChanged, lambda e: received.append(e))

        service.set_exposure_time(100.0)
        bus.drain()

        assert len(received) == 1
        assert received[0].exposure_time_ms == 100.0

    def test_handles_set_exposure_command(self):
        """Should respond to SetExposureTimeCommand events."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus, SetExposureTimeCommand

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        CameraService(mock_camera, bus)

        bus.publish(SetExposureTimeCommand(exposure_time_ms=200.0))
        bus.drain()

        mock_camera.set_exposure_time.assert_called_once_with(200.0)

    def test_handles_set_analog_gain_command(self):
        """Should respond to SetAnalogGainCommand events."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus, SetAnalogGainCommand

        mock_camera = Mock()
        mock_gain_range = Mock()
        mock_gain_range.min_gain = 0.0
        mock_gain_range.max_gain = 24.0
        mock_camera.get_gain_range.return_value = mock_gain_range
        bus = EventBus()

        CameraService(mock_camera, bus)

        bus.publish(SetAnalogGainCommand(gain=5.0))
        bus.drain()

        mock_camera.set_analog_gain.assert_called_once_with(5.0)

    def test_get_exposure_time(self):
        """get_exposure_time should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_time.return_value = 50.0
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        assert service.get_exposure_time() == 50.0

    def test_set_analog_gain(self):
        """set_analog_gain should call camera and publish event."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus, AnalogGainChanged

        mock_camera = Mock()
        mock_gain_range = Mock()
        mock_gain_range.min_gain = 0.0
        mock_gain_range.max_gain = 24.0
        mock_camera.get_gain_range.return_value = mock_gain_range
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(AnalogGainChanged, lambda e: received.append(e))

        service.set_analog_gain(12.0)
        bus.drain()

        mock_camera.set_analog_gain.assert_called_once_with(12.0)
        assert len(received) == 1
        assert received[0].gain == 12.0

    # ============================================================
    # Task 1.1: ROI methods
    # ============================================================

    def test_set_region_of_interest(self):
        """set_region_of_interest should call camera and publish event."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus, ROIChanged

        mock_camera = Mock()
        mock_camera.get_resolution.return_value = (2048, 2048)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(ROIChanged, lambda e: received.append(e))

        service.set_region_of_interest(100, 100, 800, 600)
        bus.drain()

        mock_camera.set_region_of_interest.assert_called_once_with(100, 100, 800, 600)
        assert len(received) == 1
        assert received[0].x_offset == 100
        assert received[0].y_offset == 100
        assert received[0].width == 800
        assert received[0].height == 600

    def test_get_region_of_interest(self):
        """get_region_of_interest should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_region_of_interest.return_value = (0, 0, 1024, 768)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_region_of_interest()

        assert result == (0, 0, 1024, 768)

    def test_get_resolution(self):
        """get_resolution should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_resolution.return_value = (2048, 2048)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_resolution()

        assert result == (2048, 2048)

    # ============================================================
    # Task 1.2: Binning methods
    # ============================================================

    def test_set_binning(self):
        """set_binning should call camera and publish event."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus, BinningChanged

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(BinningChanged, lambda e: received.append(e))

        service.set_binning(2, 2)
        bus.drain()

        mock_camera.set_binning.assert_called_once_with(2, 2)
        assert len(received) == 1
        assert received[0].binning_x == 2
        assert received[0].binning_y == 2

    def test_get_binning(self):
        """get_binning should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_binning.return_value = (2, 2)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_binning()

        assert result == (2, 2)

    def test_get_binning_options(self):
        """get_binning_options should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_binning_options.return_value = [(1, 1), (2, 2), (4, 4)]
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_binning_options()

        assert result == [(1, 1), (2, 2), (4, 4)]

    # ============================================================
    # Task 1.3: Pixel format methods
    # ============================================================

    def test_set_pixel_format(self):
        """set_pixel_format should call camera and publish event."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus, PixelFormatChanged
        from squid.core.config import CameraPixelFormat

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(PixelFormatChanged, lambda e: received.append(e))

        service.set_pixel_format(CameraPixelFormat.MONO16)
        bus.drain()

        mock_camera.set_pixel_format.assert_called_once_with(CameraPixelFormat.MONO16)
        assert len(received) == 1
        assert received[0].pixel_format == CameraPixelFormat.MONO16

    def test_get_pixel_format(self):
        """get_pixel_format should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus
        from squid.core.config import CameraPixelFormat

        mock_camera = Mock()
        mock_camera.get_pixel_format.return_value = CameraPixelFormat.MONO8
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_pixel_format()

        assert result == CameraPixelFormat.MONO8

    def test_get_available_pixel_formats(self):
        """get_available_pixel_formats should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus
        from squid.core.config import CameraPixelFormat

        mock_camera = Mock()
        mock_camera.get_available_pixel_formats.return_value = [
            CameraPixelFormat.MONO8,
            CameraPixelFormat.MONO16,
        ]
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_available_pixel_formats()

        assert result == [CameraPixelFormat.MONO8, CameraPixelFormat.MONO16]

    # ============================================================
    # Task 1.4: Temperature methods
    # ============================================================

    def test_set_temperature(self):
        """set_temperature should call camera."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        service.set_temperature(-20.0)

        mock_camera.set_temperature.assert_called_once_with(-20.0)

    def test_set_temperature_reading_callback(self):
        """set_temperature_reading_callback should call camera."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        callback = Mock()
        service.set_temperature_reading_callback(callback)

        mock_camera.set_temperature_reading_callback.assert_called_once_with(callback)

    # ============================================================
    # Task 1.5: White balance methods
    # ============================================================

    def test_set_white_balance_gains(self):
        """set_white_balance_gains should call camera."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        service.set_white_balance_gains(1.2, 1.0, 1.5)

        mock_camera.set_white_balance_gains.assert_called_once_with(1.2, 1.0, 1.5)

    def test_get_white_balance_gains(self):
        """get_white_balance_gains should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_white_balance_gains.return_value = (1.2, 1.0, 1.5)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_white_balance_gains()

        assert result == (1.2, 1.0, 1.5)

    def test_set_auto_white_balance(self):
        """set_auto_white_balance should call camera."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        service.set_auto_white_balance(True)

        mock_camera.set_auto_white_balance_gains.assert_called_once_with(on=True)

    # ============================================================
    # Task 1.6: Black level method
    # ============================================================

    def test_set_black_level(self):
        """set_black_level should call camera."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        service.set_black_level(10.0)

        mock_camera.set_black_level.assert_called_once_with(10.0)

    # ============================================================
    # Task 2: Read-only camera properties
    # ============================================================

    def test_get_gain_range(self):
        """get_gain_range should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_gain_range = Mock()
        mock_gain_range.min_gain = 0.0
        mock_gain_range.max_gain = 24.0
        mock_camera.get_gain_range.return_value = mock_gain_range
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_gain_range()

        assert result.min_gain == 0.0
        assert result.max_gain == 24.0

    def test_get_acquisition_mode(self):
        """get_acquisition_mode should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus
        from squid.core.abc import CameraAcquisitionMode

        mock_camera = Mock()
        mock_camera.get_acquisition_mode.return_value = (
            CameraAcquisitionMode.SOFTWARE_TRIGGER
        )
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_acquisition_mode()

        assert result == CameraAcquisitionMode.SOFTWARE_TRIGGER

    def test_get_pixel_size_binned_um(self):
        """get_pixel_size_binned_um should return camera value."""
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus

        mock_camera = Mock()
        mock_camera.get_pixel_size_binned_um.return_value = 6.5
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_pixel_size_binned_um()

        assert result == 6.5


class TestCameraServiceSensorMode:
    """Sensor mode get/set, command handling and state events (ports upstream 875eda71)."""

    @staticmethod
    def _make(mode_gate=None):
        from squid.backend.services.camera_service import CameraService
        from squid.core.events import EventBus, SensorModeChanged

        mock_camera = Mock()
        mock_camera.get_available_sensor_modes.return_value = ["Ultra Quiet", "Standard", "Fast"]
        mock_camera.get_sensor_mode.return_value = "Standard"
        mock_camera.get_exposure_limits.return_value = (0.5, 1000.0)
        bus = EventBus()
        service = CameraService(mock_camera, bus, mode_gate=mode_gate)
        received = []
        bus.subscribe(SensorModeChanged, received.append)
        return service, mock_camera, bus, received

    def test_getters_delegate_to_camera(self):
        service, _, _, _ = self._make()

        assert service.get_available_sensor_modes() == ["Ultra Quiet", "Standard", "Fast"]
        assert service.get_sensor_mode() == "Standard"

    def test_set_sensor_mode_publishes_mode_and_exposure_limits(self):
        service, mock_camera, bus, received = self._make()

        def apply(mode):
            mock_camera.get_sensor_mode.return_value = mode
            mock_camera.get_exposure_limits.return_value = (2.0, 1000.0)

        mock_camera.set_sensor_mode.side_effect = apply

        service.set_sensor_mode("Ultra Quiet")
        bus.drain()

        mock_camera.set_sensor_mode.assert_called_once_with("Ultra Quiet")
        assert len(received) == 1
        assert received[0].sensor_mode == "Ultra Quiet"
        assert received[0].exposure_limits_ms == (2.0, 1000.0)

    def test_set_sensor_mode_failure_publishes_actual_mode_and_reraises(self):
        service, mock_camera, bus, received = self._make()
        mock_camera.set_sensor_mode.side_effect = ValueError("unknown mode")

        with pytest.raises(ValueError):
            service.set_sensor_mode("Warp")
        bus.drain()

        # The camera stayed in its mode; the UI uses this to revert its selection.
        assert [e.sensor_mode for e in received] == ["Standard"]

    def test_handles_set_sensor_mode_command(self):
        from squid.core.events import SetSensorModeCommand

        service, mock_camera, bus, received = self._make()

        bus.publish(SetSensorModeCommand(sensor_mode="Fast"))
        bus.drain()

        mock_camera.set_sensor_mode.assert_called_once_with("Fast")
        assert len(received) == 1

    def test_command_failure_is_logged_not_raised(self):
        from squid.core.abc import CameraError
        from squid.core.events import SetSensorModeCommand

        service, mock_camera, bus, received = self._make()
        mock_camera.set_sensor_mode.side_effect = CameraError("READOUTSPEED write failed")

        # Called directly (not via the bus) so an escaping exception would fail the test.
        service._on_set_sensor_mode_command(SetSensorModeCommand(sensor_mode="Fast"))
        bus.drain()

        assert [e.sensor_mode for e in received] == ["Standard"]

    def test_command_blocked_by_mode_gate_republishes_current_mode(self):
        from squid.core.events import SetSensorModeCommand
        from squid.core.mode_gate import GlobalModeGate

        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = True
        service, mock_camera, bus, received = self._make(mode_gate=mode_gate)

        bus.publish(SetSensorModeCommand(sensor_mode="Fast"))
        bus.drain()

        mock_camera.set_sensor_mode.assert_not_called()
        assert [e.sensor_mode for e in received] == ["Standard"]

    def test_mode_change_is_serialized_with_triggers(self):
        """The driver pauses streaming during a mode change; CameraService's lock keeps a
        concurrent trigger from reaching the camera until the change (and the exposure/strobe
        refresh) has finished. This is why upstream's per-camera trigger lock is not needed."""
        service, mock_camera, _, _ = self._make()
        in_mode_change = threading.Event()
        release_mode_change = threading.Event()
        order = []

        def slow_set_sensor_mode(mode):
            order.append("mode_change_start")
            in_mode_change.set()
            assert release_mode_change.wait(5.0)
            order.append("mode_change_end")

        mock_camera.set_sensor_mode.side_effect = slow_set_sensor_mode
        mock_camera.send_trigger.side_effect = lambda illumination_time=None: order.append("trigger")

        setter = threading.Thread(target=service.set_sensor_mode, args=("Fast",))
        setter.start()
        assert in_mode_change.wait(5.0)
        trigger = threading.Thread(target=service.send_trigger)
        trigger.start()
        trigger.join(0.2)
        assert trigger.is_alive(), "trigger reached the camera while the mode change was in flight"

        release_mode_change.set()
        setter.join(5.0)
        trigger.join(5.0)
        assert order == ["mode_change_start", "mode_change_end", "trigger"]

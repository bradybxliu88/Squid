"""Toupcam must forget an in-flight trigger when its stream stops.

The frame that trigger was waiting for is lost with the stream (e.g. a binning or
pixel-format change pauses and restarts it), so keeping _trigger_sent would make the
next live trigger wait out the ~4 s readiness timeout.
"""

import time
from unittest.mock import MagicMock

import pytest

import squid.core.logging

try:
    from squid.backend.drivers.cameras.toupcam import ToupcamCamera
except (ImportError, OSError) as e:  # toupcam shared library not present
    pytest.skip(f"Toupcam SDK not available: {e}", allow_module_level=True)


def _streaming_toupcam_with_trigger_in_flight() -> ToupcamCamera:
    cam = object.__new__(ToupcamCamera)  # skip __init__: no SDK device needed
    cam._camera = MagicMock()
    cam._camera.get_ExpoTime.return_value = 20_000  # us
    cam._log = squid.core.logging.get_logger("test_toupcam_trigger_state")
    cam._raw_camera_stream_started = True
    cam._trigger_sent = True
    cam._last_trigger_timestamp = time.time() - 0.1  # well inside the readiness timeout
    return cam


def test_trigger_in_flight_is_not_ready():
    assert _streaming_toupcam_with_trigger_in_flight().get_ready_for_trigger() is False


def test_stop_streaming_forgets_the_in_flight_trigger():
    cam = _streaming_toupcam_with_trigger_in_flight()

    cam.stop_streaming()

    cam._camera.Stop.assert_called_once()
    assert cam.get_is_streaming() is False
    assert cam.get_ready_for_trigger() is True


def test_settings_change_stream_pause_forgets_the_in_flight_trigger():
    # Binning / pixel-format / ROI changes run under _pause_streaming(), which stops and restarts the stream.
    cam = _streaming_toupcam_with_trigger_in_flight()

    with cam._pause_streaming():
        pass

    cam._camera.Stop.assert_called_once()
    assert cam.get_ready_for_trigger() is True

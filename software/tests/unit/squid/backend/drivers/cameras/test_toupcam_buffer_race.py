"""Regression tests for the ToupcamCamera internal-read-buffer realloc race.

``_update_internal_settings`` runs on the acquisition thread on every ``set_exposure_time`` (i.e.
every channel switch) and used to replace ``self._internal_read_buffer`` with a fresh zero-filled
buffer without the frame callback lock. ``_on_frame_callback`` read that attribute twice --
``PullImageV2`` fills it, then ``np.frombuffer`` re-reads it -- so a swap landing between the two
reads produced an entirely zero saved frame. These tests drive the real ``_on_frame_callback`` /
``_process_raw_frame`` / ``_update_internal_settings`` and fake only the SDK boundary.
"""

import threading
from typing import Dict, List, Optional

import numpy as np
import pytest

import squid.backend.drivers.cameras.toupcam as camera_toupcam
import squid.core.logging
from squid.core.abc import CameraAcquisitionMode, CameraFrame, CameraFrameFormat, CameraPixelFormat
from squid.core.config import CameraConfig, CameraVariant

_W, _H = 8, 4
_ITEMSIZE = 2  # uint16
_SIZE = _W * _H * _ITEMSIZE
_FILL_BYTE = 0xAB  # a filled frame is never all-zero
_FILL_PIXEL = int.from_bytes(bytes([_FILL_BYTE]) * _ITEMSIZE, "little")


class _RecordingLock:
    """A Lock that records how many times it was acquired."""

    def __init__(self):
        self._lock = threading.Lock()
        self.acquire_count = 0

    def __enter__(self):
        self._lock.acquire()
        self.acquire_count += 1
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False


class _FakeSDK:
    """The bits of the toupcam SDK object the callback and _update_internal_settings touch."""

    def __init__(self, roi=(0, 0, _W, _H), on_pull=None):
        self.roi = roi
        self.on_pull = on_pull
        self.exposure_us: Optional[int] = None

    def PullImageV2(self, buffer, bits, info):
        # The real PullImageV2 fills `buffer` in C; the tests pre-fill a sentinel instead and use
        # on_pull to model the acquisition thread acting while the pull is in flight.
        if self.on_pull is not None:
            self.on_pull()

    def get_Roi(self):
        return self.roi

    def put_ExpoTime(self, exposure_us):
        self.exposure_us = exposure_us


def _make_camera(on_pull=None) -> "camera_toupcam.ToupcamCamera":
    """Build a minimal ToupcamCamera without __init__ (which opens hardware).

    Only the hardware boundary (the SDK object, ROI/format getters, strobe calc) is faked; the real
    _on_frame_callback, _process_raw_frame and _update_internal_settings run. Every attribute they
    touch is set explicitly, so a miss surfaces as a failed assertion rather than silently passing.
    """
    cam = object.__new__(camera_toupcam.ToupcamCamera)

    cam._raw_frame_callback_lock = threading.Lock()
    cam._internal_read_buffer = bytes([_FILL_BYTE]) * _SIZE  # a "filled" frame
    cam._current_frame = None
    cam._trigger_sent = True
    cam._raw_camera_stream_started = False
    cam._config = CameraConfig(camera_type=CameraVariant.TOUPCAM, default_pixel_format=CameraPixelFormat.MONO16)
    cam._software_crop_width_ratio = 1.0
    cam._software_crop_height_ratio = 1.0
    cam._binning = (1, 1)
    cam._pixel_format = CameraPixelFormat.MONO16
    cam._exposure_time = 10.0
    cam._hw_set_strobe_delay_ms_fn = None
    cam._capabilities = None
    cam._strobe_info = camera_toupcam.StrobeInfo(strobe_time_us=0, trigger_delay_us=0)
    cam._log = squid.core.logging.get_logger("test_toupcam_buffer_race")

    cam._camera = _FakeSDK(on_pull=on_pull)
    cam._get_pixel_size_in_bytes = lambda: _ITEMSIZE
    cam.get_frame_format = lambda: CameraFrameFormat.RAW
    cam.get_acquisition_mode = lambda: CameraAcquisitionMode.SOFTWARE_TRIGGER
    return cam


def _capture_frames(cam) -> List[CameraFrame]:
    frames: List[CameraFrame] = []
    cam._propogate_frame = frames.append
    return frames


@pytest.fixture
def fake_strobe_calc(monkeypatch):
    """_calculate_strobe_info queries the SDK for bandwidth/resolution; return a fixed result."""
    monkeypatch.setattr(
        camera_toupcam.ToupcamCamera,
        "_calculate_strobe_info",
        staticmethod(lambda **_: camera_toupcam.StrobeInfo(strobe_time_us=0, trigger_delay_us=0)),
    )


@pytest.mark.parametrize("reassign_buffer_during_pull", [False, True], ids=["no_realloc", "realloc_during_pull"])
def test_callback_frame_not_zeroed_by_concurrent_buffer_realloc(reassign_buffer_during_pull):
    """The delivered frame must be the buffer PullImageV2 filled, realloc or not."""
    holder: Dict[str, Optional["camera_toupcam.ToupcamCamera"]] = {"cam": None}

    def realloc_mid_pull():
        # Models the acquisition thread's unlocked realloc landing between the fill and the view.
        if reassign_buffer_during_pull:
            holder["cam"]._internal_read_buffer = bytes(_SIZE)

    cam = _make_camera(on_pull=realloc_mid_pull)
    holder["cam"] = cam
    frames = _capture_frames(cam)

    cam._on_frame_callback()

    assert len(frames) == 1
    frame = frames[0].frame
    assert frame.shape == (_H, _W)
    # An all-zero frame here is the production symptom: the callback viewed the freshly
    # zero-filled buffer the concurrent realloc swapped in, not the one that was filled.
    assert np.all(frame == _FILL_PIXEL), "delivered frame is all zeros: buffer-realloc race corrupted it"


def test_update_internal_settings_keeps_buffer_when_size_unchanged(fake_strobe_calc):
    """set_exposure_time (every channel switch) must not reallocate (and zero) a same-size buffer."""
    cam = _make_camera()
    cam._raw_frame_callback_lock = _RecordingLock()
    buffer_before = cam._internal_read_buffer

    cam.set_exposure_time(25.0)

    assert cam._camera.exposure_us == 25_000  # the exposure change itself still went through
    assert cam._internal_read_buffer is buffer_before
    assert cam._raw_frame_callback_lock.acquire_count == 0


def test_update_internal_settings_reallocates_under_lock_when_size_changes(fake_strobe_calc):
    """A real size change (e.g. ROI) reallocates, and does the swap under the frame callback lock."""
    cam = _make_camera()
    cam._raw_frame_callback_lock = _RecordingLock()
    new_w, new_h = _W * 2, _H * 2
    cam._camera.roi = (0, 0, new_w, new_h)

    cam._update_internal_settings(send_exposure=False)

    assert len(cam._internal_read_buffer) == new_w * new_h * _ITEMSIZE
    assert cam._raw_frame_callback_lock.acquire_count == 1


def test_update_internal_settings_first_call_allocates(fake_strobe_calc):
    """__init__'s first call runs before the attribute exists and must allocate it."""
    cam = _make_camera()
    del cam._internal_read_buffer

    cam._update_internal_settings(send_exposure=False)

    assert len(cam._internal_read_buffer) == _SIZE


def test_size_change_realloc_waits_for_in_flight_callback(fake_strobe_calc):
    """A size-changing realloc issued mid-pull must not land until the callback has finished."""
    pull_started = threading.Event()
    release_pull = threading.Event()

    def block_in_pull():
        pull_started.set()
        release_pull.wait(timeout=5)

    cam = _make_camera(on_pull=block_in_pull)
    # The in-flight frame was captured at the old ROI; only _update_internal_settings (which
    # queries the SDK directly) sees the new one.
    cam.get_region_of_interest = lambda: (0, 0, _W, _H)
    frames = _capture_frames(cam)
    filled_buffer = cam._internal_read_buffer

    callback_thread = threading.Thread(target=cam._on_frame_callback, daemon=True)
    realloc_thread = threading.Thread(
        target=cam._update_internal_settings, kwargs={"send_exposure": False}, daemon=True
    )
    try:
        callback_thread.start()
        assert pull_started.wait(timeout=5)

        # Acquisition thread changes ROI while the callback is inside PullImageV2.
        cam._camera.roi = (0, 0, _W * 2, _H * 2)
        realloc_thread.start()
        realloc_thread.join(timeout=0.2)
        assert realloc_thread.is_alive(), "realloc did not wait for the in-flight frame callback"
        assert cam._internal_read_buffer is filled_buffer
    finally:
        release_pull.set()
        callback_thread.join(timeout=5)
        if realloc_thread.ident is not None:  # started
            realloc_thread.join(timeout=5)
    assert not callback_thread.is_alive() and not realloc_thread.is_alive()

    assert len(frames) == 1
    assert np.all(frames[0].frame == _FILL_PIXEL)
    assert len(cam._internal_read_buffer) == _W * 2 * _H * 2 * _ITEMSIZE

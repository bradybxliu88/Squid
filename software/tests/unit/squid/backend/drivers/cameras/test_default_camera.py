"""Tests for DefaultCamera (Daheng/gxipy) read_camera_frame cached-frame reuse.

read_camera_frame has a fast path that returns the cached _current_frame when it is younger than
exposure + strobe. In a trigger -> read workflow (e.g. laser autofocus: measure -> piezo move ->
verify), reusing a frame captured *before* the trigger hands the caller the pre-move image. These
tests drive the real read_camera_frame/send_trigger and fake only the SDK boundary.
"""

import importlib
import sys
import threading
import time
import types
from types import SimpleNamespace
from typing import Iterator, Optional
from unittest.mock import MagicMock

import numpy as np
import pytest

from squid.core.abc import CameraAcquisitionMode, CameraFrame, CameraFrameFormat, CameraPixelFormat
import squid.core.logging

_GX_MODULE = "squid.backend.drivers.gxipy"
_BASE_MODULE = "squid.backend.drivers.cameras.base"

# Reuse window = exposure + strobe (strobe is 0 here). Large enough that a frame from a few ms
# ago is "fresh" even on a loaded machine, small enough that the read timeout
# ((4 * window + 100) ms) keeps the trigger tests quick.
_EXPOSURE_MS = 100.0
# For tests that expect the fast path: a wide window so scheduling jitter can't age the frame out.
_LONG_EXPOSURE_MS = 1000.0


@pytest.fixture(scope="module")
def default_camera_cls():
    """Import DefaultCamera, stubbing the gxipy SDK wrapper when it cannot load on this platform.

    DefaultCamera only references gx in annotations, class-level enum tables and SDK calls these
    tests never make, so any gx.<Name> it asks for resolves to an inert MagicMock.
    """
    patcher = pytest.MonkeyPatch()
    stubbed = False
    try:
        importlib.import_module(_GX_MODULE)
    except Exception:
        stub = types.ModuleType(_GX_MODULE)

        def _placeholder(name: str):
            if name.startswith("__"):
                raise AttributeError(name)
            return MagicMock(name=f"gx.{name}")

        stub.__getattr__ = _placeholder
        patcher.setitem(sys.modules, _GX_MODULE, stub)
        stubbed = True
    try:
        yield importlib.import_module(_BASE_MODULE).DefaultCamera
    finally:
        if stubbed:
            # Don't leak a DefaultCamera bound to the stub into other tests.
            sys.modules.pop(_BASE_MODULE, None)
            cameras_pkg = sys.modules.get("squid.backend.drivers.cameras")
            if cameras_pkg is not None and hasattr(cameras_pkg, "base"):
                delattr(cameras_pkg, "base")
        patcher.undo()


def _frame(frame_id: int, timestamp: float) -> CameraFrame:
    return CameraFrame(
        frame_id=frame_id,
        timestamp=timestamp,
        frame=np.full((2, 2), frame_id, dtype=np.uint8),
        frame_format=CameraFrameFormat.RAW,
        frame_pixel_format=CameraPixelFormat.MONO8,
    )


class _FakeGxCamera:
    """The bits of a gx.Device that read_camera_frame/send_trigger touch."""

    def __init__(self):
        self.data_stream = [SimpleNamespace(acquisition_flag=True)]
        self.TriggerSoftware = SimpleNamespace(send_command=lambda: None)


def _make_camera(cls, cached_frame: Optional[CameraFrame], exposure_ms: float = _EXPOSURE_MS):
    """Build a DefaultCamera without __init__ (which opens hardware); set what the read path uses."""
    cam = object.__new__(cls)
    cam._log = squid.core.logging.get_logger("test_default_camera")
    cam._camera = _FakeGxCamera()
    cam._exposure_time_ms = exposure_ms
    cam._strobe_delay_us = 0
    cam._in_trigger = False
    cam._last_trigger_timestamp = 0
    cam._frame_lock = threading.Lock()
    cam._current_frame = cached_frame
    cam.get_acquisition_mode = lambda: CameraAcquisitionMode.SOFTWARE_TRIGGER
    return cam


def _deliver_frame_later(cam, frame_id: int, delay_s: float) -> None:
    """Model the SDK callback thread delivering a new frame (as _frame_callback does)."""

    def deliver():
        with cam._frame_lock:
            cam._current_frame = _frame(frame_id, time.time())

    timer = threading.Timer(delay_s, deliver)
    timer.daemon = True
    timer.start()


def test_fresh_frame_reused_when_no_trigger_sent(default_camera_cls):
    """Continuous/live mode: no per-read trigger, so a fresh cached frame takes the fast path."""
    cached = _frame(1, time.time())
    cam = _make_camera(default_camera_cls, cached, exposure_ms=_LONG_EXPOSURE_MS)

    assert cam.read_camera_frame() is cached


def test_fresh_post_trigger_frame_is_reused(default_camera_cls):
    """A frame that arrived after the last trigger and is still fresh is returned immediately."""
    cam = _make_camera(default_camera_cls, None, exposure_ms=_LONG_EXPOSURE_MS)
    cam._last_trigger_timestamp = time.time() - 0.010
    cached = _frame(1, time.time())
    cam._current_frame = cached

    assert cam.read_camera_frame() is cached


def test_pre_trigger_frame_not_reused_after_send_trigger(default_camera_cls):
    """send_trigger -> read must return the triggered frame, not the fresh-but-pre-trigger cache."""
    # Captured 5 ms ago: well inside the reuse window, but before the trigger below.
    pre_trigger = _frame(1, time.time() - 0.005)
    cam = _make_camera(default_camera_cls, pre_trigger)
    cam._camera.TriggerSoftware.send_command = lambda: _deliver_frame_later(cam, frame_id=2, delay_s=0.020)

    cam.send_trigger()
    frame = cam.read_camera_frame()

    assert frame is not None
    assert frame is not pre_trigger
    assert frame.frame_id == 2
    assert frame.timestamp >= cam._last_trigger_timestamp


def test_pre_trigger_frame_not_returned_when_triggered_frame_never_arrives(default_camera_cls):
    """If the triggered frame never comes, time out (None) rather than return the stale frame."""
    pre_trigger = _frame(1, time.time() - 0.005)
    cam = _make_camera(default_camera_cls, pre_trigger)

    cam.send_trigger()

    assert cam.read_camera_frame() is None


def test_fast_path_returns_the_frame_it_checked(default_camera_cls):
    """The reuse checks and the returned object must come from one read of _current_frame.

    Models the callback thread swapping _current_frame mid-evaluation: the first read sees a
    fresh post-trigger frame, every later read a pre-trigger one. The fast path must return the
    frame that passed its guard, never a different object read afterwards.
    """
    trigger_time = time.time() - 0.010
    checked = _frame(2, time.time())
    swapped_in = _frame(1, trigger_time - 0.005)
    reads: Iterator[CameraFrame] = iter([checked])

    class _SwappingDefaultCamera(default_camera_cls):
        @property
        def _current_frame(self):
            return next(reads, swapped_in)

        @_current_frame.setter
        def _current_frame(self, value):
            pass

        def get_frame_id(self):
            # Don't consume reads of _current_frame before the fast path runs.
            return checked.frame_id

    cam = _make_camera(_SwappingDefaultCamera, None, exposure_ms=_LONG_EXPOSURE_MS)
    cam._last_trigger_timestamp = trigger_time

    assert cam.read_camera_frame() is checked

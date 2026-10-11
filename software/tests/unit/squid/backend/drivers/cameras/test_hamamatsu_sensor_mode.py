"""HamamatsuCamera sensor mode (DCAM READOUTSPEED) and streaming-flag behavior.

The DCAM SDK is not installed in CI: dcamapi4 loads the vendor library (and imports
ctypes.windll) at import time. The loaders are stubbed so the real driver, dcam.py and the
real DCAM enums/structures import, and a fake Dcam handle stands in for the camera.
"""

import ctypes
import importlib
import sys
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

import squid.core.config
from squid.core.abc import CameraError

_DCAM_MODULES = (
    "squid.backend.drivers.cameras.dcamapi4",
    "squid.backend.drivers.cameras.dcam",
    "squid.backend.drivers.cameras.hamamatsu",
)

# Line interval [s] per READOUTSPEED value: slower readout -> longer line interval.
_LINE_INTERVAL_BY_SPEED = {1.0: 40e-6, 2.0: 10e-6, 3.0: 5e-6}


@pytest.fixture
def hama(monkeypatch):
    """Import the real Hamamatsu driver modules with the DCAM shared library stubbed out."""
    monkeypatch.setattr(ctypes, "windll", MagicMock(), raising=False)
    monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda *args, **kwargs: MagicMock())
    saved = {name: sys.modules.pop(name) for name in _DCAM_MODULES if name in sys.modules}
    try:
        yield importlib.import_module("squid.backend.drivers.cameras.hamamatsu")
    finally:
        # Do not leak the stub-backed modules into other tests.
        package = sys.modules["squid.backend.drivers.cameras"]
        for name in _DCAM_MODULES:
            sys.modules.pop(name, None)
            attr = name.rpartition(".")[2]
            if name in saved:
                setattr(package, attr, saved[name])
            elif hasattr(package, attr):
                delattr(package, attr)
        sys.modules.update(saved)


class FakeDcam:
    """Minimal stand-in for dcam.Dcam, recording the calls that matter for ordering."""

    def __init__(self, hama_module, readout_speed_range: Optional[Tuple[float, float, float]] = (1.0, 3.0, 1.0)):
        self._ids = hama_module.DCAM_IDPROP
        self._dcamprop = hama_module.DCAMPROP
        self._attr_cls = sys.modules["squid.backend.drivers.cameras.dcamapi4"].DCAMPROP_ATTR
        self.readout_speed_range = readout_speed_range
        self.calls: List[tuple] = []
        self.fail_set_props: set = set()
        self.on_cap_stop = None
        self.props: Dict[int, float] = {
            self._ids.READOUTSPEED: 2.0,
            self._ids.TRIGGERSOURCE: float(self._dcamprop.TRIGGERSOURCE.SOFTWARE),
            self._ids.EXPOSURETIME: 0.02,
            self._ids.TRIGGERDELAY: 0.0,
        }

    def _attr(self, valuemin, valuemax, valuestep):
        attr = self._attr_cls()
        attr.valuemin, attr.valuemax, attr.valuestep = valuemin, valuemax, valuestep
        return attr

    def prop_getattr(self, prop):
        if prop == self._ids.READOUTSPEED:
            return self._attr(*self.readout_speed_range) if self.readout_speed_range else False
        if prop == self._ids.EXPOSURETIME:
            return self._attr(0.0001, 10.0, 0.0)
        return False

    def prop_getvalue(self, prop):
        if prop == self._ids.INTERNAL_LINEINTERVAL:
            return _LINE_INTERVAL_BY_SPEED[self.props[self._ids.READOUTSPEED]]
        return self.props.get(prop, False)

    def prop_setvalue(self, prop, value):
        self.calls.append(("set", prop, value))
        if prop in self.fail_set_props:
            return False
        self.props[prop] = value
        return True

    def lasterr(self):
        return 0

    def cap_stop(self):
        self.calls.append(("cap_stop",))
        if self.on_cap_stop:
            self.on_cap_stop()
        return True

    def buf_release(self):
        self.calls.append(("buf_release",))
        return True

    def buf_alloc(self, count):
        return True

    def cap_start(self):
        self.calls.append(("cap_start",))
        return True


def _make_camera(hama, monkeypatch, fake: FakeDcam, default_sensor_mode=None, strobe_fn=None):
    capabilities = hama.HamamatsuCapabilities(binning_to_resolution={(1, 1): (2304, 2304)})
    monkeypatch.setattr(hama.HamamatsuCamera, "_open", staticmethod(lambda index=None, sn=None: (fake, capabilities)))
    config = squid.core.config.get_camera_config().model_copy(update={"default_sensor_mode": default_sensor_mode})
    camera = hama.HamamatsuCamera(config, hw_trigger_fn=MagicMock(), hw_set_strobe_delay_ms_fn=strobe_fn)
    # Never spawn the DCAM read thread in unit tests.
    monkeypatch.setattr(camera, "_ensure_read_thread_running", lambda: True)
    return camera


def test_discovers_readout_speeds_as_named_modes(hama, monkeypatch):
    camera = _make_camera(hama, monkeypatch, FakeDcam(hama))

    assert camera.get_available_sensor_modes() == ["Ultra Quiet", "Standard", "Fast"]
    assert camera.get_sensor_mode() == "Standard"


def test_unknown_readout_speed_values_are_named_by_number(hama, monkeypatch):
    camera = _make_camera(hama, monkeypatch, FakeDcam(hama, readout_speed_range=(1.0, 4.0, 0.5)))

    # A fractional step must not crash range(); it is clamped to 1.
    assert camera.get_available_sensor_modes() == ["Ultra Quiet", "Standard", "Fast", "4"]


def test_camera_without_readout_speed_reports_unsupported(hama, monkeypatch):
    camera = _make_camera(hama, monkeypatch, FakeDcam(hama, readout_speed_range=None))

    assert camera.get_available_sensor_modes() == []
    assert camera.get_sensor_mode() is None
    with pytest.raises(NotImplementedError):
        camera.set_sensor_mode("Fast")


def test_set_sensor_mode_pauses_streaming_and_reapplies_exposure(hama, monkeypatch):
    fake = FakeDcam(hama)
    camera = _make_camera(hama, monkeypatch, fake)
    camera.set_exposure_time(35.0)
    camera.start_streaming()
    fake.calls.clear()

    camera.set_sensor_mode("Ultra Quiet")

    ids = hama.DCAM_IDPROP
    readout_set = ("set", ids.READOUTSPEED, 1)
    assert fake.calls[0] == ("cap_stop",)
    assert fake.calls.index(readout_set) < fake.calls.index(("cap_start",))
    # Exposure is re-applied after the readout speed change (strobe/line interval refresh).
    assert ("set", ids.EXPOSURETIME, 0.035) in fake.calls[fake.calls.index(readout_set) :]
    assert camera.get_is_streaming()
    assert camera.get_sensor_mode() == "Ultra Quiet"


def test_set_sensor_mode_refreshes_hardware_trigger_strobe_delay(hama, monkeypatch):
    fake = FakeDcam(hama)
    strobe_fn = MagicMock()
    camera = _make_camera(hama, monkeypatch, fake, strobe_fn=strobe_fn)
    fake.props[hama.DCAM_IDPROP.TRIGGERSOURCE] = float(hama.DCAMPROP.TRIGGERSOURCE.EXTERNAL)
    strobe_fn.reset_mock()

    camera.set_sensor_mode("Ultra Quiet")

    # Strobe time = line interval * rows; Ultra Quiet's line interval is 40 us -> 92.16 ms.
    strobe_fn.assert_called_once()
    assert strobe_fn.call_args.args[0] == pytest.approx(40e-6 * 2304 * 1000.0)


def test_set_sensor_mode_rejects_unknown_mode(hama, monkeypatch):
    fake = FakeDcam(hama)
    camera = _make_camera(hama, monkeypatch, fake)
    fake.calls.clear()

    with pytest.raises(ValueError):
        camera.set_sensor_mode("Warp")
    assert fake.calls == []


def test_set_sensor_mode_failure_raises_camera_error_and_resumes_streaming(hama, monkeypatch):
    fake = FakeDcam(hama)
    camera = _make_camera(hama, monkeypatch, fake)
    camera.start_streaming()
    fake.fail_set_props.add(hama.DCAM_IDPROP.READOUTSPEED)

    with pytest.raises(CameraError):
        camera.set_sensor_mode("Fast")
    assert camera.get_is_streaming()
    assert camera.get_sensor_mode() == "Standard"


def test_default_sensor_mode_applied_at_init(hama, monkeypatch):
    fake = FakeDcam(hama)
    camera = _make_camera(hama, monkeypatch, fake, default_sensor_mode="Fast")

    assert camera.get_sensor_mode() == "Fast"


def test_invalid_default_sensor_mode_fails_loudly(hama, monkeypatch):
    with pytest.raises(ValueError):
        _make_camera(hama, monkeypatch, FakeDcam(hama), default_sensor_mode="Bogus")


def test_stop_streaming_clears_streaming_flag_before_teardown(hama, monkeypatch):
    fake = FakeDcam(hama)
    camera = _make_camera(hama, monkeypatch, fake)
    camera.start_streaming()
    streaming_during_teardown = []
    fake.on_cap_stop = lambda: streaming_during_teardown.append(camera.get_is_streaming())

    assert camera.stop_streaming()
    assert streaming_during_teardown == [False]
    assert not camera.get_is_streaming()

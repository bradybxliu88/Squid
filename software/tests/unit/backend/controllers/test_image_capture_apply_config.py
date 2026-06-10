from squid.backend.controllers.multipoint.image_capture import ImageCaptureExecutor


class _Cam:
    def __init__(self):
        self.exposure = None
        self.gain = None
    def set_exposure_time(self, v):
        self.exposure = v
    def set_analog_gain(self, v):
        self.gain = v


class _Cfg:
    """Mirrors ChannelMode's attribute names."""
    name = "Fluorescence 488 nm Ex"
    exposure_time = 33.0          # NOT exposure_time_ms
    analog_gain = 1.5
    illumination_source = 12
    illumination_intensity = 60.0
    emission_filter_position = None


def test_apply_configuration_sets_exposure_from_exposure_time():
    cam = _Cam()
    ex = ImageCaptureExecutor(cam, illumination_service=None, filter_wheel_service=None)
    ex.apply_configuration(_Cfg())
    assert cam.exposure == 33.0
    assert cam.gain == 1.5

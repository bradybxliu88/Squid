"""Laser AF must use the saved Min Peak Width / Distance / Prominence, and the machine config's crop sizes.

Ported regression tests for upstream 2a550c86. The controller runs with dummy services; the focus camera returns a
synthetic image of the laser spots.
"""

from typing import List, Optional, Tuple

import numpy as np
import pytest

import _def
from _def import SpotDetectionMode
from squid.backend.controllers.autofocus.laser_auto_focus_controller import LaserAutofocusController
from squid.core.abc import CameraAcquisitionMode
from squid.core.config.models import LaserAFConfig
from squid.core.events import EventBus

SPOT_X_PX = 522.0  # 10 px right of the reference (512) = 2 um at 0.2 um/px


def spots_image(spots, shape=(256, 1024), y_px=128) -> np.ndarray:
    """spots: (x_px, amplitude, sigma_x_px) for each spot on one row, like the AF laser's reflections."""
    x = np.arange(shape[1])
    profile = sum(amplitude * np.exp(-((x - x_px) ** 2) / (2 * sigma_px**2)) for x_px, amplitude, sigma_px in spots)
    gy = np.exp(-((np.arange(shape[0]) - y_px) ** 2) / (2 * 5.0**2))
    return np.round(255 * np.outer(gy, profile) / profile.max()).astype(np.uint8)


class _FakeCameraService:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.roi: Optional[Tuple[int, int, int, int]] = None

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        return CameraAcquisitionMode.SOFTWARE_TRIGGER

    def send_trigger(self, illumination_time=None) -> None:  # noqa: ARG002
        pass

    def read_frame(self) -> np.ndarray:
        return self.frame

    def enable_callbacks(self, enabled: bool) -> None:  # noqa: ARG002
        pass

    def get_exposure_time(self) -> float:
        return 10.0

    def set_exposure_time(self, exposure_ms: float) -> None:  # noqa: ARG002
        pass

    def set_analog_gain(self, gain: float) -> None:  # noqa: ARG002
        pass

    def set_region_of_interest(self, x: int, y: int, width: int, height: int) -> None:
        self.roi = (x, y, width, height)


class _FakeStageService:
    def __init__(self) -> None:
        self.z_moves_mm: List[float] = []

    def move_z(self, distance_mm: float) -> None:
        self.z_moves_mm.append(distance_mm)


class _FakePeripheralService:
    def turn_on_af_laser(self, wait_for_completion: bool = True) -> None:  # noqa: ARG002
        pass

    def turn_off_af_laser(self, wait_for_completion: bool = True) -> None:  # noqa: ARG002
        pass


class _FakeObjectiveStore:
    current_objective = "20x"


class _FakeSettingsManager:
    """Keeps the simulated calibration out of any on-disk profile."""

    def __init__(self) -> None:
        self.saved_objectives: List[str] = []

    def get_laser_af_settings(self) -> dict:
        return {}

    def update_laser_af_settings(self, objective: str, updates: dict, crop_image=None) -> None:  # noqa: ARG002
        pass

    def save_configurations(self, objective: str) -> None:
        self.saved_objectives.append(objective)


def _build_controller(frame: np.ndarray) -> LaserAutofocusController:
    return LaserAutofocusController(
        camera_service=_FakeCameraService(frame),
        stage_service=_FakeStageService(),
        peripheral_service=_FakePeripheralService(),
        event_bus=EventBus(),
        objectiveStore=_FakeObjectiveStore(),
        laserAFSettingManager=_FakeSettingsManager(),
    )


@pytest.mark.parametrize(
    "setting, saved_value, spots",
    [
        # one spot narrower than the default min width (10 px)
        ("min_peak_width", 3, [(SPOT_X_PX, 1.0, 1.5)]),
        # a second, weaker spot that stands out more than the default prominence (0.2) but less than the saved one
        ("min_peak_prominence", 0.8, [(SPOT_X_PX, 1.0, 5.0), (700.0, 0.5, 5.0)]),
        # a second, weaker spot farther than the default min distance (10 px) but closer than the saved one
        ("min_peak_distance", 60, [(SPOT_X_PX, 1.0, 5.0), (SPOT_X_PX + 40, 0.6, 5.0)]),
    ],
)
def test_saved_peak_setting_reaches_spot_detection(setting, saved_value, spots):
    """The default settings reject each image, so the measurement succeeds only if the saved value is used."""
    controller = _build_controller(spots_image(spots))
    controller.laser_af_properties = LaserAFConfig(
        x_reference=512.0,
        pixel_to_um=0.2,
        has_reference=True,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
        **{setting: saved_value},
    )

    assert controller.measure_displacement() == pytest.approx(2.0, abs=0.05)


@pytest.mark.parametrize(
    "setting, saved_value, spots",
    [
        ("min_peak_width", 3, [(SPOT_X_PX, 1.0, 1.5)]),
        ("min_peak_prominence", 0.8, [(SPOT_X_PX, 1.0, 5.0), (700.0, 0.5, 5.0)]),
        ("min_peak_distance", 60, [(SPOT_X_PX, 1.0, 5.0), (SPOT_X_PX + 40, 0.6, 5.0)]),
    ],
)
def test_default_peak_settings_reject_the_test_images(setting, saved_value, spots):  # noqa: ARG001
    """Guard for the test above: with the _def defaults, each image yields no measurement."""
    controller = _build_controller(spots_image(spots))
    controller.laser_af_properties = LaserAFConfig(
        x_reference=512.0,
        pixel_to_um=0.2,
        has_reference=True,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
    )

    assert np.isnan(controller.measure_displacement())


def test_initialize_takes_the_crop_sizes_from_the_machine_config(monkeypatch):
    """A loaded profile carries the crop sizes it was saved with; Initialize must use the .ini ones."""
    monkeypatch.setattr(_def, "LASER_AF_CROP_WIDTH", 1024)
    monkeypatch.setattr(_def, "LASER_AF_CROP_HEIGHT", 128)
    monkeypatch.setattr(_def, "LASER_AF_INITIALIZE_CROP_WIDTH", 2800)
    monkeypatch.setattr(_def, "LASER_AF_INITIALIZE_CROP_HEIGHT", 1800)
    monkeypatch.setattr(_def, "MULTIPOINT_PIEZO_DELAY_MS", 0)
    # outside the saved initialize crop (1200 x 800 around the sensor centre), inside the .ini one
    controller = _build_controller(spots_image([(604.0, 1.0, 5.0)], shape=(2064, 3088), y_px=401))
    controller.laser_af_properties = LaserAFConfig(
        width=1536,
        height=256,
        initialize_crop_width=1200,
        initialize_crop_height=800,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
    )

    assert controller.initialize_auto()
    # ROI of the .ini size around the spot (offsets snapped to multiples of 8 / 2 by initialize_manual)
    assert controller._camera_service.roi == (88, 336, 1024, 128)
    props = controller.laser_af_properties
    assert (props.width, props.height) == (1024, 128)
    assert (props.initialize_crop_width, props.initialize_crop_height) == (2800, 1800)
    assert controller.laserAFSettingManager.saved_objectives == ["20x"]

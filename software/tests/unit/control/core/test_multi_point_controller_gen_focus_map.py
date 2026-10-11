from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Optional

import pytest

from squid.core.events import (
    EventBus,
    AcquisitionWorkerFinished,
    AutofocusMode,
    SetAcquisitionParametersCommand,
)
from squid.core.mode_gate import GlobalModeGate, GlobalMode
from squid.backend.controllers.multipoint import multi_point_controller
from squid.backend.controllers.multipoint.multi_point_controller import MultiPointController
from squid.backend.controllers.multipoint.focus_operations import AutofocusExecutor
from squid.backend.managers.focus_map import FocusMap


@dataclass
class _Pos:
    x_mm: float
    y_mm: float
    z_mm: float


class _FakeStageService:
    def __init__(self) -> None:
        self._pos = _Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0)

    def get_position(self) -> _Pos:
        return self._pos

    def move_x_to(self, _x_mm: float) -> None:
        return None

    def move_y_to(self, _y_mm: float) -> None:
        return None

    def move_z_to(self, _z_mm: float) -> None:
        return None


class _FakeCameraService:
    def __init__(self) -> None:
        self._callbacks_enabled = False

    def get_callbacks_enabled(self) -> bool:
        return self._callbacks_enabled

    def enable_callbacks(self, enabled: bool) -> None:
        self._callbacks_enabled = enabled

    def stop_streaming(self) -> None:
        return None

    def get_pixel_size_binned_um(self) -> float:
        return 1.0

    def get_crop_size(self) -> tuple[int, int]:
        return (64, 64)


class _FakePeripheralService:
    def wait_for_idle(self, timeout_s: float) -> None:
        return None  # the MCU is idle


class _FakeLaserAFController:
    def __init__(self, *, has_reference: bool) -> None:
        self.laser_af_properties = type(
            "_LaserAFProps",
            (),
            {"has_reference": has_reference},
        )()


class _FakeFocusLockController:
    def __init__(self, status: str = "ready") -> None:
        self.status = status


class _FakeLiveController:
    def __init__(self) -> None:
        self.is_live = False
        self.currentConfiguration = object()
        self.trigger_mode = None
        self.enable_channel_auto_filter_switching = False

    def stop_live(self) -> None:
        self.is_live = False

    def start_live(self) -> None:
        self.is_live = True

    def set_microscope_mode(self, _mode) -> None:
        return None


class _FakeAutoFocusController:
    def __init__(self) -> None:
        self.use_focus_map = False
        self.focus_map_coords = []
        self._sample_focus_map_calls = 0
        self._focus_map_surface = None
        self.sampled_coords = []

    def sample_focus_map_points(self, coords) -> None:
        self._sample_focus_map_calls += 1
        self.sampled_coords = list(coords)
        self.focus_map_coords = [(x, y, 0.0) for x, y in coords]

    def set_focus_map_use(self, enabled: bool) -> None:
        self.use_focus_map = enabled

    def set_focus_map_surface(self, focus_map) -> None:
        self._focus_map_surface = focus_map

    @property
    def focus_map_surface(self):
        return self._focus_map_surface

    def clear_focus_map(self) -> None:
        self.focus_map_coords = []


class _FakeScanCoordinates:
    def __init__(self) -> None:
        self.region_centers = {}
        self.region_fov_coordinates = {}
        self.objectiveStore = object()
        self.stage = object()
        self.camera = object()

    def get_scan_bounds(self):
        return None


class _FakeObjectiveStore:
    current_objective = "10x"
    objectives_dict = {}


class _FakeChannelConfigurationManager:
    def write_configuration_selected(self, *_args, **_kwargs) -> None:
        return None


class _FakeGlobalScanCoordinates:
    def __init__(self) -> None:
        self.objectiveStore = object()
        self.stage = object()
        self.camera = object()
        self.update_calls: list[tuple[str, int, float]] = []

    def update_fov_z_level(self, region_id: str, index: int, z_mm: float) -> None:
        self.update_calls.append((region_id, index, z_mm))


class _FakeFocusMap:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float, str]] = []

    def interpolate(self, x_mm: float, y_mm: float, region_id: str) -> float:
        self.calls.append((x_mm, y_mm, region_id))
        return 1.23


def test_gen_focus_map_invalid_bounds_publishes_failure(tmp_path: Path) -> None:
    bus = EventBus()
    bus.start()
    try:
        finished: list[AcquisitionWorkerFinished] = []
        finished_ready = threading.Event()

        def _on_finished(e: AcquisitionWorkerFinished) -> None:
            finished.append(e)
            finished_ready.set()

        bus.subscribe(AcquisitionWorkerFinished, _on_finished)

        live = _FakeLiveController()
        af = _FakeAutoFocusController()
        stage_service = _FakeStageService()
        camera_service = _FakeCameraService()
        peripheral_service = _FakePeripheralService()
        mode_gate = GlobalModeGate(bus)

        controller = MultiPointController(
            live_controller=live,
            autofocus_controller=af,
            objective_store=_FakeObjectiveStore(),
            channel_configuration_manager=_FakeChannelConfigurationManager(),
            camera_service=camera_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            event_bus=bus,
            scan_coordinates=_FakeScanCoordinates(),
            mode_gate=mode_gate,
        )

        controller.set_base_path(str(tmp_path))
        controller.start_new_experiment("unit_test")
        controller.set_gen_focus_map_flag(True)
        controller.set_reflection_af_flag(False)

        controller.run_acquisition()

        assert finished_ready.wait(timeout=2.0), "Timed out waiting for AcquisitionWorkerFinished"
        assert len(finished) == 1
        assert finished[0].experiment_id == controller.experiment_ID
        assert finished[0].experiment_id.startswith("unit_test_")
        assert finished[0].success is False
        assert finished[0].error == "Invalid scan bounds"
        assert af._sample_focus_map_calls == 0
        assert controller.state.name == "IDLE"
        assert mode_gate.get_mode() == GlobalMode.IDLE
    finally:
        bus.stop()


def test_generate_focus_map_builds_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFocusMap:
        def __init__(self) -> None:
            self.is_fitted = False
            self.fit_points = None

        def fit(self, points) -> None:
            self.fit_points = points
            self.is_fitted = True

        def interpolate(self, _x_mm, _y_mm):
            return 0.0

    monkeypatch.setattr("squid.backend.managers.focus_map.FocusMap", _FakeFocusMap)

    af = _FakeAutoFocusController()
    executor = AutofocusExecutor(autofocus_controller=af)

    center = executor.generate_focus_map_for_acquisition(
        {"x": (0.0, 6.0), "y": (0.0, 6.0)},
        dx_mm=3.0,
        dy_mm=3.0,
    )

    assert center == (3.0, 3.0)
    assert af._sample_focus_map_calls == 1
    assert len(af.sampled_coords) >= 4
    x1, y1 = af.sampled_coords[0]
    x2, y2 = af.sampled_coords[1]
    x3, y3 = af.sampled_coords[2]
    detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    assert detT != 0
    assert af.focus_map_surface is not None
    assert af.use_focus_map is True


def test_reflection_af_requires_laser_controller() -> None:
    bus = EventBus()
    bus.start()
    try:
        controller = MultiPointController(
            live_controller=_FakeLiveController(),
            autofocus_controller=_FakeAutoFocusController(),
            objective_store=_FakeObjectiveStore(),
            channel_configuration_manager=_FakeChannelConfigurationManager(),
            camera_service=_FakeCameraService(),
            stage_service=_FakeStageService(),
            peripheral_service=_FakePeripheralService(),
            event_bus=bus,
            scan_coordinates=_FakeScanCoordinates(),
            laser_autofocus_controller=None,
        )
        controller.set_autofocus_mode(AutofocusMode.LASER_REFLECTION)

        assert controller.validate_acquisition_settings() is False
    finally:
        bus.stop()


def test_focus_lock_allows_locked_run_without_laser_reference() -> None:
    bus = EventBus()
    bus.start()
    try:
        scan_coordinates = _FakeScanCoordinates()
        scan_coordinates.region_fov_coordinates = {"region_1": [(0.0, 0.0, 0.0)]}
        controller = MultiPointController(
            live_controller=_FakeLiveController(),
            autofocus_controller=_FakeAutoFocusController(),
            objective_store=_FakeObjectiveStore(),
            channel_configuration_manager=_FakeChannelConfigurationManager(),
            camera_service=_FakeCameraService(),
            stage_service=_FakeStageService(),
            peripheral_service=_FakePeripheralService(),
            event_bus=bus,
            scan_coordinates=scan_coordinates,
            laser_autofocus_controller=_FakeLaserAFController(has_reference=False),
            focus_lock_controller=_FakeFocusLockController(status="locked"),
            piezo_service=type("_Piezo", (), {"is_available": True})(),
        )
        controller.set_autofocus_mode(AutofocusMode.FOCUS_LOCK)

        assert controller.validate_acquisition_settings() is True
    finally:
        bus.stop()


class _LocalScanCoordinates:
    """Stands in for the temporary ScanCoordinates that run_acquisition builds for a current-FOV snap."""

    last_instance: Optional["_LocalScanCoordinates"] = None

    def __init__(self, objectiveStore, stage, camera, event_bus=None) -> None:
        self.objectiveStore = objectiveStore
        self.stage = stage
        self.camera = camera
        self.region_centers: dict = {}
        self.region_fov_coordinates: dict = {}
        self.update_calls: list[tuple[str, int, float]] = []
        type(self).last_instance = self

    def add_single_fov_region(self, region_id: str, center_x: float, center_y: float, center_z: float) -> None:
        self.region_centers[region_id] = [center_x, center_y, center_z]
        self.region_fov_coordinates[region_id] = [(center_x, center_y)]

    def update_fov_z_level(self, region_id: str, index: int, z_mm: float) -> None:
        self.update_calls.append((region_id, index, z_mm))


class _NoOpWorker:
    def __init__(self, *, acquisition_parameters, event_bus, **_kwargs) -> None:
        self._event_bus = event_bus
        self._experiment_id = acquisition_parameters.experiment_ID

    def set_current_round_index(self, _index: int) -> None:
        return None

    def run(self) -> None:
        if self._event_bus:
            self._event_bus.publish(
                AcquisitionWorkerFinished(
                    experiment_id=self._experiment_id,
                    success=True,
                    final_fov_count=0,
                )
            )


class _RecordingFocusMap(FocusMap):
    """A real fit-by-region FocusMap (one constant-plane region, "A1") that records interpolate() calls."""

    def __init__(self) -> None:
        super().__init__()
        self.set_method("constant")
        self.set_fit_by_region(True)
        self.fit({"A1": [(1.0, 2.0, 1.23)]})
        self.calls: list[str] = []

    def interpolate(self, x, y, region_id=None):
        self.calls.append(region_id)
        return super().interpolate(x, y, region_id)


def _run_with_focus_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, acquire_current_fov: bool):
    monkeypatch.setattr(multi_point_controller, "ScanCoordinates", _LocalScanCoordinates)
    monkeypatch.setattr(multi_point_controller, "MultiPointWorker", _NoOpWorker)
    _LocalScanCoordinates.last_instance = None

    bus = EventBus()
    bus.start()
    try:
        global_scan = _FakeGlobalScanCoordinates()
        global_scan.region_centers = {"A1": [1.0, 2.0, 0.0]}
        global_scan.region_fov_coordinates = {"A1": [(1.0, 2.0)]}
        controller = MultiPointController(
            live_controller=_FakeLiveController(),
            autofocus_controller=_FakeAutoFocusController(),
            objective_store=_FakeObjectiveStore(),
            channel_configuration_manager=_FakeChannelConfigurationManager(),
            camera_service=_FakeCameraService(),
            stage_service=_FakeStageService(),
            peripheral_service=_FakePeripheralService(),
            event_bus=bus,
            scan_coordinates=global_scan,
        )
        controller.set_base_path(str(tmp_path))
        controller.start_new_experiment("unit_test")
        focus_map = _RecordingFocusMap()
        controller.set_focus_map(focus_map)

        started = controller.run_acquisition(acquire_current_fov=acquire_current_fov)
        bus.drain(timeout_s=1.0)
        if controller.thread:
            controller.thread.join(timeout=1.0)
        return started, focus_map, global_scan
    finally:
        bus.stop()


def test_acquire_current_fov_skips_focus_map_interpolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A snap must not interpolate a fit-by-region focus map: its "current" region has no fit (upstream ee7897c9)."""
    started, focus_map, global_scan = _run_with_focus_map(tmp_path, monkeypatch, acquire_current_fov=True)

    assert started is True
    assert focus_map.calls == []
    assert global_scan.update_calls == []
    local_scan = _LocalScanCoordinates.last_instance
    assert local_scan is not None
    assert local_scan.update_calls == []


def test_regular_acquisition_still_interpolates_focus_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started, focus_map, global_scan = _run_with_focus_map(tmp_path, monkeypatch, acquire_current_fov=False)

    assert started is True
    assert focus_map.calls == ["A1"]
    assert global_scan.update_calls == [("A1", 0, pytest.approx(1.23))]


def test_unchecked_use_focus_map_clears_a_stale_focus_map() -> None:
    """The widgets send use_focus_map=False when "Use Focus Map" is unchecked; that must drop an earlier map."""
    bus = EventBus()
    bus.start()
    try:
        controller = MultiPointController(
            live_controller=_FakeLiveController(),
            autofocus_controller=_FakeAutoFocusController(),
            objective_store=_FakeObjectiveStore(),
            channel_configuration_manager=_FakeChannelConfigurationManager(),
            camera_service=_FakeCameraService(),
            stage_service=_FakeStageService(),
            peripheral_service=_FakePeripheralService(),
            event_bus=bus,
            scan_coordinates=_FakeScanCoordinates(),
        )
        focus_map = _FakeFocusMap()

        bus.publish(SetAcquisitionParametersCommand(focus_map=focus_map, use_focus_map=True))
        bus.drain(timeout_s=1.0)
        assert controller.focus_map is focus_map

        # Commands that do not mention the focus map leave it alone.
        bus.publish(SetAcquisitionParametersCommand(n_x=2))
        bus.drain(timeout_s=1.0)
        assert controller.focus_map is focus_map

        bus.publish(SetAcquisitionParametersCommand(focus_map=None, use_focus_map=False))
        bus.drain(timeout_s=1.0)
        assert controller.focus_map is None

        # Without use_focus_map, a focus_map alone still sets it (TCP / MCP / older callers).
        bus.publish(SetAcquisitionParametersCommand(focus_map=focus_map))
        bus.drain(timeout_s=1.0)
        assert controller.focus_map is focus_map
    finally:
        bus.stop()

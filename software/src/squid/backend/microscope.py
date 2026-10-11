import time
from pathlib import Path
from typing import List, Optional, TypeVar

import imageio
import numpy as np

import _def
from squid.backend.managers import ChannelConfigurationManager
from squid.backend.managers import ChannelConfigService
from squid.backend.managers import ConfigurationManager
from squid.core.config.repository import ConfigRepository
from squid.backend.managers import ContrastManager
from squid.backend.controllers.autofocus import LaserAFSettingManager
from squid.backend.controllers.live_controller import LiveController
from squid.backend.services.laser_engine_service import LaserEngineService
from squid.backend.managers import ObjectiveStore
from squid.backend.io.stream_handler import (
    StreamHandler,
    StreamHandlerFunctions,
    NoOpStreamHandlerFunctions,
)

from squid.backend.drivers.lighting.led import (
    LightSourceType,
    IntensityControlMode,
    ShutterControlMode,
    IlluminationController,
)
from squid.backend.microcontroller import Microcontroller
from squid.backend.drivers.peripherals.piezo import PiezoStage
from squid.backend.drivers.lighting import SciMicroscopyLEDArray
from squid.core.abc import (
    CameraAcquisitionMode,
    AbstractCamera,
    AbstractStage,
    AbstractFilterWheelController,
)
from squid.core.events import event_bus
from squid.backend.drivers.stages.stage_utils import get_stage
import squid.backend.drivers.lighting.celesta
import squid.backend.drivers.lighting.illumination_andor
import squid.backend.microcontroller
import squid.backend.drivers.lighting as serial_peripherals
import squid.backend.drivers.cameras.camera_utils
import squid.core.config
import squid.backend.drivers.filter_wheels.utils
import squid.core.logging
import squid.backend.drivers.stages.cephla
import squid.backend.drivers.stages.stage_utils

if _def.USE_XERYON:
    from squid.backend.drivers.peripherals.objective_changer import (
        ObjectiveChanger2PosController,
        ObjectiveChanger2PosController_Simulation,
    )
else:
    ObjectiveChanger2PosController = TypeVar("ObjectiveChanger2PosController")

# Legacy Fluidics import removed - now using FluidicsService from ApplicationContext
# The Fluidics parameter in MicroscopeAddons is deprecated and always None
Fluidics = TypeVar("Fluidics")  # Type stub for backward compatibility

if _def.ENABLE_NL5:
    import squid.backend.drivers.peripherals.nl5 as NL5
else:
    NL5 = TypeVar("NL5")


class MicroscopeAddons:
    @staticmethod
    def build_from_global_config(
        stage: AbstractStage, micro: Optional[Microcontroller], simulated: bool = False
    ) -> "MicroscopeAddons":
        from squid.backend.microscope_factory import build_microscope_addons

        return build_microscope_addons(stage, micro, simulated=simulated)

    def __init__(
        self,
        xlight: Optional[serial_peripherals.XLight] = None,
        dragonfly: Optional[serial_peripherals.Dragonfly] = None,
        nl5: Optional[NL5] = None,
        cellx: Optional[serial_peripherals.CellX] = None,
        emission_filter_wheel: Optional[AbstractFilterWheelController] = None,
        objective_changer: Optional[ObjectiveChanger2PosController] = None,
        camera_focus: Optional[AbstractCamera] = None,
        fluidics: Optional[Fluidics] = None,
        piezo_stage: Optional[PiezoStage] = None,
        sci_microscopy_led_array: Optional[SciMicroscopyLEDArray] = None,
        squid_laser_engine: Optional["LaserEngineService"] = None,
    ):
        self.xlight: Optional[serial_peripherals.XLight] = xlight
        self.dragonfly: Optional[serial_peripherals.Dragonfly] = dragonfly
        self.nl5: Optional[NL5] = nl5
        self.cellx: Optional[serial_peripherals.CellX] = cellx
        self.emission_filter_wheel = emission_filter_wheel
        self.objective_changer = objective_changer
        self.camera_focus: Optional[AbstractCamera] = camera_focus
        self.fluidics = fluidics
        self.piezo_stage = piezo_stage
        self.sci_microscopy_led_array = sci_microscopy_led_array
        # LaserEngineService (wraps the driver + bridges to EventBus); None unless
        # USE_SQUID_LASER_ENGINE. Exposes the readiness API used by LiveController
        # and MultiPointWorker.
        self.squid_laser_engine: Optional["LaserEngineService"] = squid_laser_engine

    def prepare_for_use(self) -> None:
        """
        Prepare all the addon hardware for immediate use.
        """
        if self.emission_filter_wheel:
            fw_config = squid.core.config.get_filter_wheel_config()
            self.emission_filter_wheel.initialize(fw_config.indices)
            self.emission_filter_wheel.home()
        if self.piezo_stage:
            self.piezo_stage.home()
        # Objective turret: home once and move to the default objective. The Xeryon
        # path is positioned later as a side effect of the objectives widget's initial
        # selection and is intentionally left unchanged here.
        if _def.USE_OBJECTIVE_TURRET and self.objective_changer is not None:
            self.objective_changer.home()
            try:
                self.objective_changer.move_to_objective(_def.DEFAULT_OBJECTIVE)
            except KeyError as e:
                raise RuntimeError(
                    f"DEFAULT_OBJECTIVE={_def.DEFAULT_OBJECTIVE!r} is not configured for the objective turret"
                ) from e
        if self.squid_laser_engine:
            # start() may raise if the USB device is missing — intentional hard fail
            # when USE_SQUID_LASER_ENGINE=True so we don't silently disable it.
            self.squid_laser_engine.start()
            self.squid_laser_engine.wake_up_all()  # fire-and-forget


class LowLevelDrivers:
    @staticmethod
    def build_from_global_config(simulated: bool = False) -> "LowLevelDrivers":
        from squid.backend.microscope_factory import build_low_level_drivers

        return build_low_level_drivers(simulated=simulated)

    def __init__(self, microcontroller: Optional[Microcontroller] = None):
        self.microcontroller: Optional[Microcontroller] = microcontroller

    def prepare_for_use(self) -> None:
        if self.microcontroller and _def.HAS_OBJECTIVE_PIEZO:
            # Configure DAC gains for objective piezo
            _def.OUTPUT_GAINS.CHANNEL7_GAIN = (
                _def.OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE == 5
            )
            div = 1 if _def.OUTPUT_GAINS.REFDIV else 0
            gains = sum(
                getattr(_def.OUTPUT_GAINS, f"CHANNEL{i}_GAIN") << i
                for i in range(8)
            )
            self.microcontroller.configure_dac80508_refdiv_and_gain(div, gains)


class Microscope:
    @staticmethod
    def build_from_global_config(
        simulated: bool = False, skip_controller_creation: bool = False
    ) -> "Microscope":
        from squid.backend.microscope_factory import build_microscope

        return build_microscope(
            simulated=simulated,
            skip_controller_creation=skip_controller_creation,
        )

    def __init__(
        self,
        stage: AbstractStage,
        camera: AbstractCamera,
        illumination_controller: IlluminationController,
        addons: MicroscopeAddons,
        low_level_drivers: LowLevelDrivers,
        stream_handler_callbacks: Optional[
            StreamHandlerFunctions
        ] = NoOpStreamHandlerFunctions,
        simulated: bool = False,
        skip_prepare_for_use: bool = False,
        skip_controller_creation: bool = False,
    ):
        super().__init__()
        self._log = squid.core.logging.get_logger(self.__class__.__name__)

        self.stage: AbstractStage = stage
        self.camera: AbstractCamera = camera
        self.illumination_controller: IlluminationController = illumination_controller

        self.addons = addons
        self.low_level_drivers = low_level_drivers

        self._simulated = simulated

        # These are always created by Microscope (simple data managers)
        self.objective_store: ObjectiveStore = ObjectiveStore()
        self.channel_configuration_manager: ChannelConfigurationManager = (
            ChannelConfigurationManager(configurations_path=_def.PROJECT_ROOT / "configurations")
        )

        # New YAML-based config system
        software_dir = _def.PROJECT_ROOT
        self.config_repo: ConfigRepository = ConfigRepository(base_path=software_dir)

        # Auto-migrate from JSON/XML configs if no YAML profile exists yet
        _default_profile = "default_profile"
        default_profile_path = self.config_repo.user_profiles_path / _default_profile
        if not default_profile_path.exists():
            try:
                import importlib.util
                _spec = importlib.util.spec_from_file_location(
                    "migrate_to_yaml_configs",
                    software_dir / "tools" / "migrate_to_yaml_configs.py",
                )
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                _mod.migrate(software_dir)
            except Exception:
                pass  # Migration may fail if no JSON configs exist
        # If migration didn't create the profile, create a minimal one
        if not default_profile_path.exists():
            self.config_repo.create_profile(_default_profile)
        self.config_repo.set_profile(_default_profile)

        self.channel_config_service: ChannelConfigService = ChannelConfigService(
            self.config_repo, event_bus
        )

        self.laser_af_settings_manager: Optional[LaserAFSettingManager] = None
        if _def.SUPPORT_LASER_AUTOFOCUS:
            self.laser_af_settings_manager = LaserAFSettingManager(config_repo=self.config_repo)

        self.configuration_manager: ConfigurationManager = ConfigurationManager(
            self.channel_configuration_manager, self.laser_af_settings_manager
        )
        self.contrast_manager: ContrastManager = ContrastManager()

        # Controllers can be created externally and injected
        # Initialize to None; will be set below or by ApplicationContext
        self.stream_handler: Optional[StreamHandler] = None
        self.stream_handler_focus: Optional[StreamHandler] = None
        self.live_controller_focus: Optional[LiveController] = None
        self.live_controller: Optional[LiveController] = None

        if not skip_controller_creation:
            # Default behavior: create controllers internally
            self._create_controllers(stream_handler_callbacks)

        if not skip_prepare_for_use:
            self._prepare_for_use()

    def _create_controllers(
        self, stream_handler_callbacks: Optional[StreamHandlerFunctions] = None
    ) -> None:
        """Create core non-Qt components internally.

        Controllers are created by `ApplicationContext` so they can be constructed
        with service dependencies and without UI coupling.
        """
        if stream_handler_callbacks is None:
            stream_handler_callbacks = NoOpStreamHandlerFunctions

        self.stream_handler = StreamHandler(handler_functions=stream_handler_callbacks)

        if self.addons.camera_focus:
            self.stream_handler_focus = StreamHandler(
                handler_functions=NoOpStreamHandlerFunctions
            )
        # Live controllers are created externally by ApplicationContext.

    def _prepare_for_use(self) -> None:
        self.low_level_drivers.prepare_for_use()
        self.addons.prepare_for_use()

        self.camera.set_pixel_format(
            squid.core.config.CameraPixelFormat.from_string(
                _def.CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT
            )
        )
        self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

        if self.addons.camera_focus:
            self.addons.camera_focus.set_pixel_format(
                squid.core.config.CameraPixelFormat.from_string("MONO8")
            )
            self.addons.camera_focus.set_acquisition_mode(
                CameraAcquisitionMode.SOFTWARE_TRIGGER
            )

    def update_camera_functions(self, functions: StreamHandlerFunctions) -> None:
        self.stream_handler.set_functions(functions)

    def update_camera_focus_functions(self, functions: StreamHandlerFunctions):
        if not self.addons.camera_focus:
            raise ValueError(
                "No focus camera, cannot change its stream handler functions."
            )

        self.stream_handler_focus.set_functions(functions)

    def initialize_core_components(self) -> None:
        if self.addons.piezo_stage:
            self.addons.piezo_stage.home()

    def setup_hardware(self) -> None:
        self.camera.add_frame_callback(self.stream_handler.on_new_frame)
        self.camera.enable_callbacks(True)

        if self.addons.camera_focus:
            self.addons.camera_focus.add_frame_callback(
                self.stream_handler_focus.on_new_frame
            )
            self.addons.camera_focus.enable_callbacks(True)
            self.addons.camera_focus.start_streaming()

    def acquire_image(self) -> np.ndarray:
        """Acquire a single image from the camera.

        Turns on illumination, triggers the camera, reads the frame, and turns off
        illumination. The trigger mode (software vs hardware) is determined by the
        live controller configuration.

        Returns:
            The acquired image as a numpy array.

        Raises:
            RuntimeError: If the camera fails to return a frame.
        """
        using_software_trigger = self.live_controller.trigger_mode == _def.TriggerMode.SOFTWARE

        # turn on illumination and send trigger
        if using_software_trigger:
            self.live_controller.turn_on_illumination()
            self.waitForMicrocontroller()
            self.camera.send_trigger()
        elif self.live_controller.trigger_mode == _def.TriggerMode.HARDWARE:
            self.low_level_drivers.microcontroller.send_hardware_trigger(
                control_illumination=True,
                illumination_on_time_us=self.camera.get_exposure_time() * 1000,
            )

        try:
            # read a frame from camera
            image = self.camera.read_frame()
            if image is None:
                self._log.error("camera.read_frame() returned None")
                raise RuntimeError("Failed to acquire image: camera.read_frame() returned None")
            return image
        finally:
            # always turn off illumination when using software trigger
            if using_software_trigger:
                self.live_controller.turn_off_illumination()

    def home_xyz(self) -> None:
        if _def.HOMING_ENABLED_Z:
            self.stage.home(x=False, y=False, z=True, theta=False)
        if _def.HOMING_ENABLED_X and _def.HOMING_ENABLED_Y:
            # The plate clamp actuation post can get in the way of homing if we start with
            # the stage in "just the wrong" position.  Blindly moving the Y out 20, then home x
            # and move x over 20 , guarantees we'll clear the post for homing.  If we are <20mm
            # from the end travel of either axis, we'll just stop at the extent without consequence.
            #
            # The one odd corner case is if the system gets shut down in the loading position.
            # in that case, we drive off of the loading position and the clamp closes quickly.
            # This doesn't seem to cause problems, and there isn't a clean way to avoid the corner
            # case.
            self._log.info(
                "Moving y+20, then x->home->+50 to make sure system is clear for homing."
            )
            self.stage.move_y(20)
            self.stage.home(x=True, y=False, z=False, theta=False)
            self.stage.move_x(50)

            self._log.info("Homing the Y axis...")
            self.stage.home(x=False, y=True, z=False, theta=False)

    def move_x(self, distance: float, blocking: bool = True) -> None:
        self.stage.move_x(distance, blocking=blocking)

    def move_y(self, distance: float, blocking: bool = True) -> None:
        self.stage.move_y(distance, blocking=blocking)

    def move_x_to(self, position: float, blocking: bool = True) -> None:
        self.stage.move_x_to(position, blocking=blocking)

    def move_y_to(self, position: float, blocking: bool = True) -> None:
        self.stage.move_y_to(position, blocking=blocking)

    def get_x(self) -> float:
        return self.stage.get_pos().x_mm

    def get_y(self) -> float:
        return self.stage.get_pos().y_mm

    def get_z(self) -> float:
        return self.stage.get_pos().z_mm

    def move_z_to(self, z_mm: float, blocking: bool = True) -> None:
        self.stage.move_z_to(z_mm, blocking=blocking)

    def start_live(self) -> None:
        self.live_controller.start_live()

    def stop_live(self) -> None:
        if self.live_controller is not None:
            self.live_controller.stop_live()

    def waitForMicrocontroller(
        self, timeout: float = 5.0, error_message: Optional[str] = None
    ) -> None:
        try:
            self.low_level_drivers.microcontroller.wait_till_operation_is_completed(
                timeout
            )
        except TimeoutError as e:
            self._log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def close(self) -> None:
        self.stop_live()
        self.low_level_drivers.microcontroller.close()
        if self.addons.emission_filter_wheel:
            self.addons.emission_filter_wheel.close()
        if self.addons.camera_focus:
            self.addons.camera_focus.close()
        if self.addons.squid_laser_engine:
            try:
                self.addons.squid_laser_engine.close()
            except Exception as e:
                self._log.warning(f"Error closing squid laser engine: {e}")
        # Release the Dragonfly serial port so a restarted process can open it.
        # ApplicationContext.shutdown() reaches hardware teardown through here.
        if self.addons.dragonfly is not None:
            try:
                self.addons.dragonfly.close()
            except Exception as e:
                self._log.warning(f"Error closing Dragonfly: {e}")
        self.camera.close()

    def move_to_position(self, x: float, y: float, z: float) -> None:
        self.move_x_to(x)
        self.move_y_to(y)
        self.move_z_to(z)

    def set_objective(self, objective: str) -> None:
        self.objective_store.set_current_objective(objective)

    def _get_channel_or_raise(self, objective: str, channel_name: str):
        """Look up a channel config; raise ValueError with available names if missing.

        Ports upstream 44c4f518: replace silent no-op with explicit error.
        """
        channel_config = self.channel_configuration_manager.get_channel_configuration_by_name(
            objective, channel_name
        )
        if channel_config is None:
            try:
                available = [
                    ch.name
                    for ch in self.channel_configuration_manager.get_channel_configurations_for_objective(objective)
                ]
            except Exception:
                available = []
            raise ValueError(
                f"Channel '{channel_name}' not found for objective '{objective}'. Available: {available}"
            )
        return channel_config

    def set_illumination_intensity(
        self, channel: str, intensity: float, objective: Optional[str] = None
    ) -> None:
        if objective is None:
            objective = self.objective_store.current_objective
        channel_config = self._get_channel_or_raise(objective, channel)
        channel_config.illumination_intensity = intensity
        self.live_controller.set_microscope_mode(channel_config)

    def set_exposure_time(
        self, channel: str, exposure_time: float, objective: Optional[str] = None
    ) -> None:
        if objective is None:
            objective = self.objective_store.current_objective
        channel_config = self._get_channel_or_raise(objective, channel)
        channel_config.exposure_time = exposure_time
        self.live_controller.set_microscope_mode(channel_config)

    def save_image(self, image: np.ndarray, path: str) -> str:
        """Save an image to the given path.

        Extension is determined by dtype: tiff for uint16, otherwise
        ``Acquisition.IMAGE_FORMAT``. Any existing extension in path is replaced.

        Ports upstream 44c4f518.

        Args:
            image: Image array to save.
            path: Output file path (extension is overridden).

        Returns:
            The actual path the image was saved to.
        """
        extension = "tiff" if image.dtype == np.uint16 else _def.Acquisition.IMAGE_FORMAT
        p = Path(path).with_suffix(f".{extension}")
        p.parent.mkdir(parents=True, exist_ok=True)
        imageio.imwrite(str(p), image)
        self._log.info(f"Image saved to {p}")
        return str(p)

    def acquire_single_fov(
        self,
        channel_names: List[str],
        save_path: str,
        NZ: int = 1,
        deltaZ_mm: float = 0.0,
        z_stacking_config: str = "FROM BOTTOM",
    ) -> List[str]:
        """Acquire a C+Z stack at the current position.

        Iterates over z-planes and channels, saving each image to ``save_path``.
        File naming follows the acquisition pipeline convention:
        ``0_0_{z}_{channel_name}.{ext}``

        Ports upstream 44c4f518.

        Args:
            channel_names: List of illumination channel names to acquire.
            save_path: Directory to save images into (created if needed).
            NZ: Number of z-planes.
            deltaZ_mm: Z step size in mm (positive = upward).
            z_stacking_config: "FROM BOTTOM", "FROM CENTER", or "FROM TOP".

        Returns:
            List of saved file paths.
        """
        objective = self.objective_store.current_objective
        configs = [self._get_channel_or_raise(objective, name) for name in channel_names]

        VALID_Z_STACKING_CONFIGS = {"FROM BOTTOM", "FROM CENTER", "FROM TOP"}
        if z_stacking_config not in VALID_Z_STACKING_CONFIGS:
            raise ValueError(
                f"Invalid z_stacking_config '{z_stacking_config}'. Must be one of: {VALID_Z_STACKING_CONFIGS}"
            )

        Path(save_path).mkdir(parents=True, exist_ok=True)

        deltaZ = deltaZ_mm
        if z_stacking_config == "FROM TOP":
            deltaZ = -abs(deltaZ_mm)

        z_start = self.stage.get_pos().z_mm

        # Move to start of z-stack
        if NZ > 1 and z_stacking_config == "FROM CENTER":
            self.stage.move_z(-deltaZ * round((NZ - 1) / 2.0))
            time.sleep(_def.SCAN_STABILIZATION_TIME_MS_Z / 1000)

        saved_paths = []
        try:
            for z_level in range(NZ):
                for config in configs:
                    self.live_controller.set_microscope_mode(config)
                    self.waitForMicrocontroller()

                    image = self.acquire_image()

                    channel_name_safe = config.name.replace(" ", "_")
                    file_id = f"0_0_{z_level}_{channel_name_safe}"
                    saved = self.save_image(image, str(Path(save_path) / file_id))
                    saved_paths.append(saved)

                if z_level < NZ - 1:
                    self.stage.move_z(deltaZ)
                    time.sleep(_def.SCAN_STABILIZATION_TIME_MS_Z / 1000)
        finally:
            # Always return Z to starting position
            try:
                self.stage.move_z_to(z_start)
            except Exception as e:
                self._log.error(f"Failed to return Z to start position {z_start} mm: {e}")

        self._log.info(f"Acquired {len(saved_paths)} images ({len(configs)} channels x {NZ} z-planes)")
        return saved_paths

# set QT_API environment variable
from __future__ import annotations
import os

from squid.backend.controllers.autofocus import AutoFocusController
from squid.backend.controllers.autofocus import LaserAutofocusController
from squid.backend.drivers.cameras.settings_cache import (
    save_camera_settings,
    load_camera_settings,
)
from squid.backend.services import ServiceRegistry

os.environ["QT_API"] = "pyqt5"
from typing import Any, Optional

# qt libraries
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QAction,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
)
from qtpy.QtGui import QCloseEvent, QKeySequence, QShortcut

from _def import (
    ENABLE_NDVIEWER,
    ENABLE_NL5,
    ENABLE_WELLPLATE_MULTIPOINT,
    HOMING_ENABLED_X,
    HOMING_ENABLED_Y,
    HOMING_ENABLED_Z,
    IS_HCS,
    LIVE_ONLY_MODE,
    RUN_FLUIDICS,
    SIMULATED_DISK_IO_ENABLED,
    SIMULATION_FORCE_SAVE_IMAGES,
    SUPPORT_SCIMICROSCOPY_LED_ARRAY,
    USE_NAPARI_WELL_SELECTION,
    USE_PRIOR_STAGE,
    USE_TEMPLATE_MULTIPOINT,
    WELLPLATE_FORMAT,
)
from squid.core.config.feature_flags import get_feature_flags

# app specific libraries
from squid.ui.widgets.nl5 import NL5Widget
from squid.backend.managers import ChannelConfigService
from squid.backend.managers import ConfigurationManager
from squid.backend.managers import ContrastManager
from squid.backend.controllers.live_controller import LiveController
from squid.backend.managers import ObjectiveStore
from squid.backend.io.stream_handler import StreamHandler
from squid.backend.microcontroller import Microcontroller
from squid.core.abc import AbstractCamera, AbstractFilterWheelController
import squid.backend.microscope
import squid.ui.widgets as widgets
import pyqtgraph.dockarea as dock
import squid.core.logging
from squid.core.events import (
    auto_subscribe,
    auto_unsubscribe,
    handles,
    event_bus,
    AcquisitionCoordinates,
    AcquisitionStarted,
    MoveStageToCommand,
    StartLiveCommand,
    StopLiveCommand,
    ImageCoordinateClickedCommand,
    ClickToMoveEnabledChanged,
    WellplateFormatChanged,
    AcquisitionStateChanged,
    AcquisitionWorkerFinished,
    ActiveAcquisitionTabChanged,
    AutoLevelCommand,
    LiveStateChanged,
    StagePositionChanged,
    PlateViewInit,
    PlateViewUpdate,
)
from squid.core.abc import Pos

from squid.backend.controllers.workflow_runner.state import WorkflowRunnerStateChanged

log = squid.core.logging.get_logger(__name__)
_FEATURE_FLAGS = get_feature_flags()

if USE_PRIOR_STAGE:
    import squid.backend.drivers.stages.prior
else:
    import squid.backend.drivers.stages.cephla
from squid.backend.drivers.peripherals.piezo import PiezoStage

# control.core.core was a shim - import classes directly
from squid.ui.qt_stream_handler import QtStreamHandler
from squid.ui.widgets.display.image_display import ImageDisplay, ImageDisplayWindow, ImageArrayDisplayWindow
from squid.ui.widgets.display.navigation_viewer import NavigationViewer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.backend.controllers.tracking_controller import TrackingControllerCore
import squid.backend.drivers.lighting as serial_peripherals

if _FEATURE_FLAGS.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
    pass  # Legacy displacement_measurement module removed

SINGLE_WINDOW = True  # set to False if use separate windows for display and control


# Legacy Fluidics import removed - now using FluidicsService from ApplicationContext
# See: squid/backend/services/fluidics_service.py

# Import the custom widget
from squid.ui.widgets.custom_multipoint import TemplateMultiPointWidget


# Import helper modules for widget creation, layout, and signal connections
from squid.ui.gui import widget_factory, layout_builder

if TYPE_CHECKING:
    from squid.application import Controllers


class HighContentScreeningGui(QMainWindow):
    fps_software_trigger = 100
    LASER_BASED_FOCUS_TAB_NAME = "Laser-Based Focus"

    def __init__(
        self,
        microscope: squid.backend.microscope.Microscope,
        controllers: Optional["Controllers"] = None,
        services: ServiceRegistry = None,  # ServiceRegistry from ApplicationContext
        is_simulation: bool = False,
        live_only_mode: bool = False,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        if services is None:
            raise ValueError(
                "HighContentScreeningGui requires a ServiceRegistry. "
                "Pass ApplicationContext.services."
        )

        self.log = squid.core.logging.get_logger(self.__class__.__name__)
        self._feature_flags = get_feature_flags()
        self._services = services  # Store for passing to widgets
        self._controllers = controllers
        self._is_simulation = is_simulation
        # Use the registry's bus if it exposes one, otherwise fall back to the global instance
        self._event_bus = getattr(services, "_event_bus", None) or event_bus

        # Create UIEventBus for thread-safe widget subscriptions
        # Must be done in main thread after QApplication exists
        self._ui_event_bus = services.ui_event_bus
        if self._ui_event_bus is None:
            # Fallback: create from global event_bus
            from squid.ui.qt_event_dispatcher import QtEventDispatcher
            from squid.ui.ui_event_bus import UIEventBus
            self._qt_dispatcher = QtEventDispatcher()
            self._ui_event_bus = UIEventBus(event_bus, self._qt_dispatcher)
            self.log.info("Created UIEventBus for thread-safe widget updates (fallback)")
        else:
            self._qt_dispatcher = None  # Owned by ApplicationContext
            self.log.info("Using UIEventBus from ServiceRegistry")
        self._subscriptions = []

        self.microscope: squid.backend.microscope.Microscope = microscope
        self.camera: AbstractCamera = microscope.camera

        # Restore cached camera settings from previous session
        self._restore_cached_camera_settings()

        self.microcontroller: Microcontroller = (
            microscope.low_level_drivers.microcontroller
        )

        self.xlight: Optional[serial_peripherals.XLight] = microscope.addons.xlight
        self.dragonfly: Optional[serial_peripherals.Dragonfly] = (
            microscope.addons.dragonfly
        )
        self.nl5: Optional[Any] = microscope.addons.nl5
        self.emission_filter_wheel: Optional[AbstractFilterWheelController] = (
            microscope.addons.emission_filter_wheel
        )
        self.objective_changer: Optional[Any] = microscope.addons.objective_changer
        # Legacy self.fluidics removed - FluidicsService now accessed via self._services.get("fluidics")
        self.piezo: Optional[PiezoStage] = microscope.addons.piezo_stage

        self.channelConfigurationManager: ChannelConfigService = (
            microscope.channel_config_service
        )
        self.configurationManager: ConfigurationManager = (
            microscope.configuration_manager
        )
        self.contrastManager: ContrastManager = microscope.contrast_manager
        self.liveController: LiveController = microscope.live_controller
        self.objectiveStore: ObjectiveStore = microscope.objective_store

        if self._controllers:
            if self._controllers.live:
                self.liveController = self._controllers.live
            if self._controllers.objective_store:
                self.objectiveStore = self._controllers.objective_store
            if self._controllers.channel_config_service:
                self.channelConfigurationManager = self._controllers.channel_config_service

        self.liveController_focus_camera: Optional[LiveController] = None
        self.streamHandler_focus_camera: Optional[StreamHandler] = None
        self.imageDisplayWindow_focus: Optional[ImageDisplayWindow] = None
        self.laserAutofocusController: Optional[LaserAutofocusController] = None

        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            if self._controllers and self._controllers.live_focus:
                self.liveController_focus_camera = self._controllers.live_focus
            else:
                self.liveController_focus_camera = self.microscope.live_controller_focus
            core_focus_stream = (
                self._controllers.stream_handler_focus
                if (self._controllers and self._controllers.stream_handler_focus)
                else None
            )
            self.streamHandler_focus_camera = QtStreamHandler(
                # Gate on focus camera live view - Focus Lock widget has its own preview
                accept_new_frame_fn=lambda: self.liveController_focus_camera.is_live if self.liveController_focus_camera else False,
                handler=core_focus_stream,
            )
            self.imageDisplayWindow_focus = ImageDisplayWindow(
                event_bus=self._ui_event_bus, show_LUT=False, autoLevels=False
            )
            core_laser_af = (
                self._controllers.laser_autofocus
                if self._controllers
                else None
            )
            if core_laser_af:
                self.laserAutofocusController = core_laser_af
            else:
                raise RuntimeError(
                    "LaserAutofocusController must be constructed in ApplicationContext"
                )

        self.live_only_mode = live_only_mode or LIVE_ONLY_MODE
        self.performance_mode = False
        self.napari_connections = {}
        self.well_selector_visible = (
            False  # Add this line to track well selector visibility
        )

        self.streamHandler: QtStreamHandler = None
        self.autofocusController: AutoFocusController = None
        self.imageDisplay: ImageDisplay = ImageDisplay()
        self.trackingController: Optional["TrackingControllerCore"] = None
        self.navigationViewer: NavigationViewer = None
        self.load_objects(is_simulation=is_simulation)

        # Pre-declare and give types to all our widgets so type hinting tools work.  You should
        # add to this as you add widgets.
        self.spinningDiskConfocalWidget: Optional[
            widgets.SpinningDiskConfocalWidget
        ] = None
        self.nl5Wdiget: Optional[NL5Widget] = None
        self.cameraSettingWidget: Optional[widgets.CameraSettingsWidget] = None
        self.profileWidget: Optional[widgets.ProfileWidget] = None
        self.liveControlWidget: Optional[widgets.LiveControlWidget] = None
        self.navigationWidget: Optional[widgets.NavigationWidget] = None
        self.stageUtils: Optional[widgets.StageUtils] = None
        self.dacControlWidget: Optional[widgets.DACControWidget] = None
        self.autofocusWidget: Optional[widgets.AutoFocusWidget] = None
        self.piezoWidget: Optional[widgets.PiezoWidget] = None
        self.objectivesWidget: Optional[widgets.ObjectivesWidget] = None
        self.filterControllerWidget: Optional[widgets.FilterControllerWidget] = None
        self.wellplateFormatWidget: Optional[widgets.WellplateFormatWidget] = None
        self.wellSelectionWidget: Optional[widgets.WellSelectionWidget] = None
        self.focusMapWidget: Optional[widgets.FocusMapWidget] = None
        self.cameraSettingWidget_focus_camera: Optional[
            widgets.CameraSettingsWidget
        ] = None
        self.laserAutofocusSettingWidget: Optional[
            widgets.LaserAutofocusSettingWidget
        ] = None
        self.laserAutofocusControlWidget: Optional[
            widgets.LaserAutofocusControlWidget
        ] = None
        self.fluidicsWidget: Optional[widgets.FluidicsWidget] = None
        self.flexibleMultiPointWidget: Optional[widgets.FlexibleMultiPointWidget] = None
        self.wellplateMultiPointWidget: Optional[widgets.WellplateMultiPointWidget] = (
            None
        )
        self.templateMultiPointWidget: Optional[TemplateMultiPointWidget] = None
        self.acquisitionSetupWidget: Optional[widgets.AcquisitionSetupWidget] = None
        self.sampleSettingsWidget: Optional[widgets.SampleSettingsWidget] = None
        self.trackingControlWidget: Optional[widgets.TrackingControllerWidget] = None
        self.napariLiveWidget: Optional[widgets.NapariLiveWidget] = None
        self.alignmentWidget: Optional[widgets.AlignmentWidget] = None
        self.imageDisplayWindow: Optional[ImageDisplayWindow] = None
        self.imageDisplayWindow_focus: Optional[ImageDisplayWindow] = None
        self.napariMultiChannelWidget: Optional[widgets.NapariMultiChannelWidget] = None
        self.imageArrayDisplayWindow: Optional[ImageArrayDisplayWindow] = None
        self.zPlotWidget: Optional[widgets.SurfacePlotWidget] = None

        self.recordTabWidget: QTabWidget = QTabWidget()
        self.cameraTabWidget: QTabWidget = QTabWidget()

        # Warning banner for simulated disk I/O mode (only if force save is not enabled)
        self.simulated_io_warning_banner: Optional[QLabel] = None
        if SIMULATED_DISK_IO_ENABLED and not SIMULATION_FORCE_SAVE_IMAGES:
            self.simulated_io_warning_banner = QLabel(
                "SIMULATED DISK I/O: Images are NOT being saved to disk!"
            )
            self.simulated_io_warning_banner.setStyleSheet(
                "background-color: #cc0000; color: white; font-weight: bold; "
                "padding: 8px; font-size: 14px;"
            )
            self.simulated_io_warning_banner.setAlignment(Qt.AlignCenter)

        # Warning/Error display widget (auto-hides when empty)
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget
        self.warningErrorWidget = WarningErrorWidget()
        self.warningErrorWidget.setVisible(False)
        self._warning_handler = None

        self.load_widgets()
        self.setup_layout()
        self.make_connections()

        if self._ui_event_bus is not None:
            self._subscriptions = auto_subscribe(self, self._ui_event_bus)

        if HOMING_ENABLED_X and HOMING_ENABLED_Y and HOMING_ENABLED_Z:
            # Hardware-owned cached-position restore runs in ApplicationContext._initialize_hardware().
            # Widgets may still need to initialize their own Z tracking state.
            if ENABLE_WELLPLATE_MULTIPOINT:
                self.wellplateMultiPointWidget.init_z()
            self.flexibleMultiPointWidget.init_z()

        # Create the menu bar
        # On macOS, disable native menu bar so it appears in the window
        # (native macOS menu bars can be unreliable with PyQt5)
        menubar = self.menuBar()
        menubar.setNativeMenuBar(False)

        # Settings menu (exposed as self.settings_menu so entry-point scripts can add items)
        self.settings_menu = menubar.addMenu("Settings")

        config_action = QAction("Preferences...", self)
        config_action.setMenuRole(QAction.NoRole)
        config_action.triggered.connect(self.openPreferences)
        self.settings_menu.addAction(config_action)

        if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            led_matrix_action = QAction("LED Matrix", self)
            led_matrix_action.triggered.connect(self.openLedMatrixSettings)
            self.settings_menu.addAction(led_matrix_action)

        # Channel configuration editors
        self.settings_menu.addSeparator()

        illum_action = QAction("Illumination Channels...", self)
        illum_action.triggered.connect(self._open_illumination_config)
        self.settings_menu.addAction(illum_action)

        acq_action = QAction("Acquisition Channels...", self)
        acq_action.triggered.connect(self._open_acquisition_config)
        self.settings_menu.addAction(acq_action)

        filter_action = QAction("Filter Wheels...", self)
        filter_action.triggered.connect(self._open_filter_wheel_config)
        self.settings_menu.addAction(filter_action)

        # Add warning/error widget to status bar
        if self.warningErrorWidget is not None:
            self.statusBar().addWidget(self.warningErrorWidget)

        # --- Status bar hints (onboarding) ---
        from squid.ui.user_preferences import load_preferences
        self._user_prefs = load_preferences()
        from squid.ui.widgets.onboarding.status_bar_hints import StatusBarHintWidget
        self._status_hints = StatusBarHintWidget(
            warning_widget=self.warningErrorWidget, parent=self
        )
        self.statusBar().addPermanentWidget(self._status_hints)

        # --- Welcome dialog (first run) ---
        if not self._user_prefs.first_run_completed:
            from squid.ui.widgets.onboarding.welcome_dialog import WelcomeDialog
            dialog = WelcomeDialog(self, self._user_prefs)
            dialog.exec_()
            self._user_prefs = load_preferences()  # Reload after dialog saves

        # --- Keyboard shortcuts ---
        self._shortcut_live = QShortcut(QKeySequence("Ctrl+L"), self)
        self._shortcut_live.activated.connect(self._toggle_live_shortcut)
        self._shortcut_perf = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        self._shortcut_perf.activated.connect(self._toggle_performance_shortcut)

        # --- Periodic stage position auto-save (crash recovery) ---
        self._last_stage_pos: Optional[tuple] = None  # (x_mm, y_mm, z_mm)
        self._stage_pos_changed_since_save: bool = False
        # Capture stage config before dropping service references
        stage_service = self._services.get("stage") if self._services else None
        self._stage_config = getattr(stage_service, "_stage_config", None) if stage_service else None
        if self._stage_config is None and hasattr(self.microscope, "stage"):
            self._stage_config = getattr(self.microscope.stage, "_stage_config", None)
        self._stage_autosave_timer = QTimer(self)
        self._stage_autosave_timer.setInterval(60000)
        self._stage_autosave_timer.timeout.connect(self._autosave_stage_position)
        self._stage_autosave_timer.start()

        # Capture stage service reference for alignment widget before dropping registries.
        self._stage_service = self._services.get("stage") if self._services else None

        # Main window should act as a UI container: widgets talk via UIEventBus.
        # Drop direct access to the controllers/services registries after initialization.
        self._controllers = None
        self._services = None

    def load_objects(self, is_simulation: bool) -> None:
        core_stream_handler = (
            self._controllers.stream_handler if self._controllers else None
        )
        self.streamHandler = QtStreamHandler(
            accept_new_frame_fn=lambda: self.liveController.is_live,
            handler=core_stream_handler,
        )
        if self._controllers and self._controllers.autofocus:
            self.autofocusController = self._controllers.autofocus
        else:
            raise RuntimeError(
                "AutoFocusController must be constructed in ApplicationContext"
            )
        if self._feature_flags.is_enabled("ENABLE_TRACKING"):
            # TrackingControllerCore is constructed in ApplicationContext; UI only publishes commands.
            tracking = getattr(self._controllers, "tracking", None) if self._controllers else None
            if tracking is None:
                raise RuntimeError(
                    "TrackingControllerCore must be constructed in ApplicationContext"
                )
            self.trackingController = tracking
        if WELLPLATE_FORMAT == "glass slide" and IS_HCS:
            self.navigationViewer = NavigationViewer(
                self.objectiveStore, self.camera, sample="4 glass slide",
                event_bus=self._ui_event_bus,
            )
        else:
            self.navigationViewer = NavigationViewer(
                self.objectiveStore, self.camera, sample=WELLPLATE_FORMAT,
                event_bus=self._ui_event_bus,
            )
        # Acquisition display frames are routed through StreamHandler.capture (no separate acquisition stream).

    def load_widgets(self) -> None:
        # Initialize all GUI widgets using helper functions
        widget_factory.create_hardware_widgets(self)
        widget_factory.create_wellplate_widgets(self)
        widget_factory.create_laser_autofocus_widgets(self)
        widget_factory.create_fluidics_widget(self)

        # Setup image display tabs
        self.imageDisplayTabs = QTabWidget(parent=self)
        if self.live_only_mode:
            if self._feature_flags.is_enabled("ENABLE_TRACKING"):
                self.imageDisplayWindow = ImageDisplayWindow(
                    contrastManager=self.contrastManager, event_bus=self._ui_event_bus
                )
                self.imageDisplayWindow.show_ROI_selector()
            else:
                self.imageDisplayWindow = ImageDisplayWindow(
                    contrastManager=self.contrastManager,
                    event_bus=self._ui_event_bus,
                    show_LUT=True,
                    autoLevels=True,
                )
            self.imageDisplayTabs = self.imageDisplayWindow.widget
            self.napariMosaicDisplayWidget = None
        else:
            self.setupImageDisplayTabs()

        # Create acquisition widgets (depends on napariMosaicDisplayWidget from above)
        widget_factory.create_acquisition_widgets(self)

        # Getting Started widget (onboarding)
        from squid.ui.user_preferences import load_preferences as _load_prefs
        _prefs = _load_prefs()
        if _prefs.show_getting_started:
            from squid.ui.widgets.onboarding.getting_started import GettingStartedWidget
            self.gettingStartedWidget = GettingStartedWidget(self._ui_event_bus)
        else:
            self.gettingStartedWidget = None

        # Contextual tips (onboarding)
        if _prefs.show_tips:
            from squid.ui.widgets.onboarding.contextual_tips import ContextualTipManager
            self._contextual_tips = ContextualTipManager(self._ui_event_bus, parent=self)
        else:
            self._contextual_tips = None

        self.setupRecordTabWidget()
        self.setupCameraTabWidget()
        # Onboarding: explain what each tab is for / when to use it on hover.
        self._apply_onboarding_tab_tooltips()

    def setupImageDisplayTabs(self) -> None:
        if self._feature_flags.is_enabled("USE_NAPARI_FOR_LIVE_VIEW"):
            # Get exposure limits from camera service for widget initialization
            camera_service = self._services.get("camera") if self._services else None
            exposure_limits = camera_service.get_exposure_limits() if camera_service else (0.1, 1000.0)
            # Seed initial state for the event-driven widget
            initial_config = self.liveController.currentConfiguration
            objective_name = getattr(self.objectiveStore, "current_objective", None)
            channel_configs = (
                self.channelConfigurationManager.get_configurations(objective_name)
                if objective_name
                else []
            )
            if initial_config is None and channel_configs:
                initial_config = channel_configs[0]
            initial_channel_names = [mode.name for mode in channel_configs]

            self.napariLiveWidget = widgets.NapariLiveWidget(
                self._ui_event_bus,
                self.streamHandler,
                self.contrastManager,
                exposure_limits=exposure_limits,
                initial_configuration=initial_config,
                initial_objective=objective_name,
                initial_channel_configs=initial_channel_names,
                wellSelectionWidget=self.wellSelectionWidget,
            )
            self.imageDisplayTabs.addTab(self.napariLiveWidget, "Live View")

            # Create alignment widget for sample registration (uses napari viewer)
            self._setup_alignment_widget()
        else:
            if self._feature_flags.is_enabled("ENABLE_TRACKING"):
                self.imageDisplayWindow = ImageDisplayWindow(
                    contrastManager=self.contrastManager, event_bus=self._ui_event_bus
                )
                self.imageDisplayWindow.show_ROI_selector()
            else:
                self.imageDisplayWindow = ImageDisplayWindow(
                    contrastManager=self.contrastManager,
                    event_bus=self._ui_event_bus,
                    show_LUT=True,
                    autoLevels=True,
                )
            self.imageDisplayTabs.addTab(self.imageDisplayWindow.widget, "Live View")

        if not self.live_only_mode:
            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MULTIPOINT"):
                # Get initial values for pixel size calculation
                camera_service = self._services.get("camera")
                initial_pixel_size_binned = (
                    camera_service.get_pixel_size_binned_um()
                    if camera_service
                    else 1.0
                )
                initial_pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
                self.napariMultiChannelWidget = widgets.NapariMultiChannelWidget(
                    event_bus=self._ui_event_bus,
                    contrastManager=self.contrastManager,
                    initial_pixel_size_factor=initial_pixel_size_factor,
                    initial_pixel_size_binned_um=initial_pixel_size_binned,
                )
                self.imageDisplayTabs.addTab(
                    self.napariMultiChannelWidget, "Multichannel Acquisition"
                )
            else:
                self.imageArrayDisplayWindow = ImageArrayDisplayWindow()
                self.imageDisplayTabs.addTab(
                    self.imageArrayDisplayWindow.widget, "Multichannel Acquisition"
                )

            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MOSAIC_DISPLAY"):
                self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(
                    contrastManager=self.contrastManager,
                    event_bus=self._ui_event_bus,
                )
                self.imageDisplayTabs.addTab(
                    self.napariMosaicDisplayWidget, "Mosaic View"
                )

            # Plate view for well-based acquisitions (only if enabled)
            if self._feature_flags.is_enabled("DISPLAY_PLATE_VIEW"):
                self.napariPlateViewWidget = widgets.NapariPlateViewWidget(
                    contrastManager=self.contrastManager,
                )
                self.imageDisplayTabs.addTab(
                    self.napariPlateViewWidget, "Plate View"
                )

            # Embedded NDViewer - initialized AFTER napari widgets because
            # NDV and napari both use vispy for OpenGL rendering. Initializing NDV first
            # can cause OpenGL context conflicts since both libraries share vispy state.
            self.ndviewerTab: Optional[widgets.NDViewerTab] = None
            if ENABLE_NDVIEWER:
                try:
                    self.ndviewerTab = widgets.NDViewerTab(event_bus=self._ui_event_bus)
                    self.imageDisplayTabs.addTab(self.ndviewerTab, "NDViewer")
                except ImportError:
                    self.log.warning("NDViewer tab unavailable: ndviewer_light module not installed")
                except (RuntimeError, OSError) as e:
                    self.log.exception(f"Failed to initialize NDViewer tab due to system error: {e}")
                except Exception:
                    self.log.exception("Failed to initialize NDViewer tab - unexpected error")

            # Connect plate view double-click to NDViewer navigation
            if (
                getattr(self, "napariPlateViewWidget", None) is not None
                and self.ndviewerTab is not None
            ):
                if hasattr(self.napariPlateViewWidget, "signal_well_fov_clicked"):
                    self.napariPlateViewWidget.signal_well_fov_clicked.connect(
                        self._on_plate_view_fov_clicked
                    )

            # z plot
            self.zPlotWidget = widgets.SurfacePlotWidget()
            dock_surface_plot = dock.Dock("Z Plot", autoOrientation=False)
            dock_surface_plot.showTitleBar()
            dock_surface_plot.addWidget(self.zPlotWidget)
            dock_surface_plot.setStretch(x=100, y=100)

            surface_plot_dockArea = dock.DockArea()
            surface_plot_dockArea.addDock(dock_surface_plot)

            self.imageDisplayTabs.addTab(surface_plot_dockArea, "Plots")

            # Connect the point clicked signal to move the stage
            self.zPlotWidget.signal_point_clicked.connect(self.move_to_mm)

        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            dock_laserfocus_image_display = dock.Dock(
                "Focus Camera Image Display", autoOrientation=False
            )
            dock_laserfocus_image_display.showTitleBar()
            dock_laserfocus_image_display.addWidget(
                self.imageDisplayWindow_focus.widget
            )
            dock_laserfocus_image_display.setStretch(x=100, y=100)

            dock_laserfocus_liveController = dock.Dock(
                "Laser Autofocus Settings", autoOrientation=False
            )
            dock_laserfocus_liveController.showTitleBar()
            dock_laserfocus_liveController.addWidget(self.laserAutofocusSettingWidget)
            dock_laserfocus_liveController.setStretch(x=100, y=100)
            dock_laserfocus_liveController.setFixedWidth(
                self.laserAutofocusSettingWidget.minimumSizeHint().width()
            )

            laserfocus_dockArea = dock.DockArea()
            laserfocus_dockArea.addDock(dock_laserfocus_image_display)
            laserfocus_dockArea.addDock(
                dock_laserfocus_liveController,
                "right",
                relativeTo=dock_laserfocus_image_display,
            )

            self.imageDisplayTabs.addTab(
                laserfocus_dockArea, self.LASER_BASED_FOCUS_TAB_NAME
            )

        if RUN_FLUIDICS and self.fluidicsWidget is not None:
            self.imageDisplayTabs.addTab(self.fluidicsWidget, "Fluidics")

        # Orchestrator tab for multi-round experiment automation
        self._setup_orchestrator_tab()

    def _setup_orchestrator_tab(self) -> None:
        """Create and add the Orchestrator tab for multi-round experiment automation."""
        # Only create if orchestrator controller is available
        if self._controllers is None:
            self.log.debug("Orchestrator tab not created: controllers is None")
            return
        if self._controllers.orchestrator is None:
            self.log.info("Orchestrator tab not created: orchestrator controller not available")
            return

        from squid.ui.widgets.orchestrator.orchestrator_widget import (
            OrchestratorControlPanel,
            OrchestratorWorkflowTree,
        )
        from squid.ui.widgets.orchestrator.warning_panel import WarningPanel
        from squid.ui.widgets.orchestrator.parameter_panel import ParameterInspectionPanel

        if self._ui_event_bus is None:
            self.log.warning("UIEventBus not available for OrchestratorWidget")
            return

        # Create the control panel (status, buttons, progress)
        self.orchestratorControlPanel = OrchestratorControlPanel(
            event_bus=self._ui_event_bus,
            orchestrator=self._controllers.orchestrator,
            parent=self,
        )

        # Create the workflow tree
        self.orchestratorWorkflowTree = OrchestratorWorkflowTree(
            event_bus=self._ui_event_bus,
            parent=self,
        )

        # Create the warning panel
        self.orchestratorWarningPanel = WarningPanel(
            event_bus=self._ui_event_bus,
            parent=self,
        )

        # Create the parameter inspection panel
        self.orchestratorParameterPanel = ParameterInspectionPanel(
            parent=self,
        )

        # Wire the parameter panel into the workflow tree so live protocol pushes work
        self.orchestratorWorkflowTree.set_param_panel(self.orchestratorParameterPanel)

        # Connect control panel to workflow tree
        self.orchestratorControlPanel.fov_positions_changed.connect(
            self.orchestratorWorkflowTree.set_fov_positions
        )
        self.orchestratorControlPanel.protocol_loaded.connect(
            self.orchestratorWorkflowTree.populate_from_protocol
        )

        # Connect workflow tree start position to control panel
        self.orchestratorWorkflowTree.start_position_changed.connect(
            self.orchestratorControlPanel.set_start_position
        )
        self.orchestratorWorkflowTree.start_from_requested.connect(
            self.orchestratorControlPanel.start_from_round
        )
        self.orchestratorWorkflowTree.run_single_round_requested.connect(
            self.orchestratorControlPanel.run_single_round
        )

        # Connect workflow tree selection to parameter panel
        self.orchestratorWorkflowTree.tree.itemClicked.connect(
            self._on_workflow_item_clicked
        )

        # Connect warning panel navigation to workflow tree
        self.orchestratorWarningPanel.navigate_to_fov.connect(
            self._on_warning_navigate_to_fov
        )

        # Create docks
        dock_workflow = dock.Dock("Workflow", autoOrientation=False)
        dock_workflow.showTitleBar()
        dock_workflow.addWidget(self.orchestratorWorkflowTree)
        dock_workflow.setStretch(x=100, y=100)

        # "Run Controls" (not just "Controls") to disambiguate from the main
        # window's right-hand "Controls" dock.
        dock_controls = dock.Dock("Run Controls", autoOrientation=False)
        dock_controls.showTitleBar()
        dock_controls.addWidget(self.orchestratorControlPanel)
        dock_controls.setStretch(x=100, y=100)

        dock_params = dock.Dock("Parameters", autoOrientation=False)
        dock_params.showTitleBar()
        dock_params.addWidget(self.orchestratorParameterPanel)
        dock_params.setStretch(x=100, y=50)

        dock_warnings = dock.Dock("Warnings", autoOrientation=False)
        dock_warnings.showTitleBar()
        dock_warnings.addWidget(self.orchestratorWarningPanel)
        dock_warnings.setStretch(x=100, y=50)

        # Onboarding tooltips on the nested dock title bars (the whole run happens
        # in these unlabeled nested tabs).
        for _dk, _tip in (
            (dock_workflow, "Rounds -> steps -> FOVs for the loaded protocol. "
                            "Pick a round to inspect it or set the resume point."),
            (dock_controls, "Load -> Validate -> Start the run; pause / resume / abort; "
                            "live status, progress, and resume-from-round."),
            (dock_params, "Read-only details of the round / step / FOV selected in the Workflow tree."),
            (dock_warnings, "Warnings raised during a run; click one to jump to its FOV."),
        ):
            try:
                _dk.label.setToolTip(_tip)
            except Exception:
                pass

        # Create dock area and arrange docks
        # Layout: Workflow (left) | Controls (top-right) / Parameters (mid-right) / Warnings (bottom-right)
        orchestrator_dockArea = dock.DockArea()
        orchestrator_dockArea.addDock(dock_workflow)
        orchestrator_dockArea.addDock(dock_controls, "right", relativeTo=dock_workflow)
        orchestrator_dockArea.addDock(dock_params, "bottom", relativeTo=dock_controls)
        orchestrator_dockArea.addDock(dock_warnings, "bottom", relativeTo=dock_params)

        # --- Additive: in-app pre-run checklist + protocol/config file editor ---
        # Wrapped so a failure here can NEVER prevent the Orchestrator tab or the
        # app from loading. These panels only render state and write files; they
        # do not touch the acquisition execution path.
        try:
            from squid.ui.widgets.orchestrator.prerun_checklist_panel import (
                PreRunChecklistPanel,
            )
            from squid.ui.widgets.orchestrator.protocol_editor_panel import (
                ProtocolEditorPanel,
            )

            self.preRunChecklistPanel = PreRunChecklistPanel(
                event_bus=self._ui_event_bus, parent=self
            )
            self.protocolEditorPanel = ProtocolEditorPanel(
                event_bus=self._ui_event_bus,
                control_panel=self.orchestratorControlPanel,
                parent=self,
            )

            # "protocol loaded" / "fov positions" are Qt signals (not bus events),
            # so wire them from the control panel into the new panels here.
            self.orchestratorControlPanel.protocol_loaded.connect(
                self.preRunChecklistPanel.on_protocol_loaded
            )
            self.orchestratorControlPanel.fov_positions_changed.connect(
                self.preRunChecklistPanel.on_fov_positions_changed
            )
            self.orchestratorControlPanel.protocol_loaded.connect(
                self.protocolEditorPanel.on_protocol_loaded
            )
            # Keep the informational checklist in sync with the authoritative Start
            # gate: an in-app edit invalidates validation.
            self.orchestratorControlPanel.validation_invalidated.connect(
                self.preRunChecklistPanel.on_validation_invalidated
            )
            # EventBus does not replay, so seed the current objective (no
            # ObjectiveChanged is emitted for the default objective at startup).
            try:
                self.preRunChecklistPanel.seed_objective(
                    getattr(self.objectiveStore, "current_objective", None)
                )
            except Exception:
                pass

            dock_checklist = dock.Dock("Pre-Run Checklist", autoOrientation=False)
            dock_checklist.showTitleBar()
            dock_checklist.addWidget(self.preRunChecklistPanel)
            dock_checklist.setStretch(x=100, y=100)

            dock_editor = dock.Dock("File Editor", autoOrientation=False)
            dock_editor.showTitleBar()
            dock_editor.addWidget(self.protocolEditorPanel)
            dock_editor.setStretch(x=100, y=100)

            # Stack as tabs on the runtime-info docks using "below" so the
            # safety-relevant Warnings/Parameters docks stay foreground by
            # default; the operator clicks the new tabs during setup.
            orchestrator_dockArea.addDock(
                dock_checklist, "below", relativeTo=dock_warnings
            )
            orchestrator_dockArea.addDock(
                dock_editor, "below", relativeTo=dock_params
            )
            for _dk, _tip in (
                (dock_checklist, "Pre-run readiness: software items auto-check; tick the "
                                 "physical setup items (oil, coverslip, reagents, waste, "
                                 "primed, piezo)."),
                (dock_editor, "Edit the loaded protocol / fluidics / imaging / config "
                              "files in-app (atomic save + .bak). Disabled during a run."),
            ):
                try:
                    _dk.label.setToolTip(_tip)
                except Exception:
                    pass
            # Foreground the checklist so the operator actually sees it during
            # setup (it was previously hidden behind Warnings).
            try:
                dock_checklist.raiseDock()
            except Exception:
                pass
            self.log.info(
                "Orchestrator: Pre-Run Checklist + File Editor panels created OK"
            )
        except Exception:
            self.log.warning(
                "Pre-run checklist / file editor panels not created", exc_info=True
            )

        # --- Run visibility + safety wiring (guarded; never blocks tab load) ---
        try:
            self._orchestrator_dockArea = orchestrator_dockArea
            self._orchestrator_warning_dock = dock_warnings
            # Snapshot the default arrangement so a dragged-away / closed dock can
            # be restored from the Settings menu.
            try:
                self._orchestrator_dock_state = orchestrator_dockArea.saveState()
            except Exception:
                self._orchestrator_dock_state = None

            # Persistent run-state indicator in the main status bar, visible from
            # any tab so an unattended run's state / intervention isn't buried in
            # a background dock.
            from qtpy.QtWidgets import QLabel as _QLabel

            self._orch_status_label = _QLabel("Orchestrator: idle")
            self._orch_status_label.setStyleSheet("color: #888; font-size: 11px;")
            self._orch_status_label.setToolTip(
                "Current automated-run state (see the Orchestrator tab)."
            )
            try:
                self.statusBar().addPermanentWidget(self._orch_status_label)
            except Exception:
                pass

            self.orchestratorControlPanel.state_changed.connect(
                self._on_orch_state_changed
            )
            self.orchestratorControlPanel.intervention_required.connect(
                self._on_orch_intervention
            )

            # Soft-gate Start on the Pre-Run Checklist (advisory reminder only).
            if hasattr(self, "preRunChecklistPanel"):
                self.orchestratorControlPanel.set_readiness_provider(
                    self.preRunChecklistPanel.readiness
                )

            # Keep the resume spinbox in sync with tree round selection.
            self.orchestratorWorkflowTree.start_position_changed.connect(
                lambda r, s, f: self.orchestratorControlPanel.set_start_position_from_ui(r)
            )

            # "Reset Orchestrator Panels" action (recover a hidden / moved dock).
            menu = getattr(self, "settings_menu", None)
            if menu is not None:
                menu.addSeparator()
                reset_action = QAction("Reset Orchestrator Panels", self)
                reset_action.setMenuRole(QAction.NoRole)
                reset_action.triggered.connect(self._reset_orchestrator_panels)
                menu.addAction(reset_action)
            self.log.info("Orchestrator: run-visibility + safety wiring OK")
        except Exception:
            self.log.warning(
                "Orchestrator run-visibility wiring not created", exc_info=True
            )

        self.imageDisplayTabs.addTab(orchestrator_dockArea, "Orchestrator")

    def _on_orch_state_changed(self, old_state: str, new_state: str) -> None:
        """Mirror the orchestrator run state into the always-visible status bar."""
        _ = old_state
        label = getattr(self, "_orch_status_label", None)
        if label is None:
            return
        colors = {
            "IDLE": "#888",
            "RUNNING": "#66BB6A",
            "PAUSED": "#FFA726",
            "WAITING_INTERVENTION": "#EF5350",
            "COMPLETED": "#66BB6A",
            "FAILED": "#EF5350",
            "ABORTED": "#EF5350",
        }
        pretty = (new_state or "").replace("_", " ").title() or "Idle"
        try:
            label.setText(f"Orchestrator: {pretty}")
            label.setStyleSheet(
                f"color: {colors.get(new_state, '#888')}; font-size: 11px;"
            )
        except Exception:
            pass

    def _on_orch_intervention(self, _event=None) -> None:
        """Bring the Orchestrator tab + Warnings dock forward when a run needs an
        operator, so the request isn't buried in a background dock of another tab."""
        try:
            area = getattr(self, "_orchestrator_dockArea", None)
            if area is not None and hasattr(self, "imageDisplayTabs"):
                self.imageDisplayTabs.setCurrentWidget(area)
            wdock = getattr(self, "_orchestrator_warning_dock", None)
            if wdock is not None:
                wdock.raiseDock()
        except Exception:
            self.log.debug("intervention auto-raise failed", exc_info=True)
        label = getattr(self, "_orch_status_label", None)
        if label is not None:
            try:
                label.setText("Orchestrator: NEEDS ATTENTION")
                label.setStyleSheet(
                    "color: #EF5350; font-weight: bold; font-size: 11px;"
                )
            except Exception:
                pass

    def _reset_orchestrator_panels(self) -> None:
        """Restore the default Orchestrator dock arrangement (recovery action)."""
        area = getattr(self, "_orchestrator_dockArea", None)
        state = getattr(self, "_orchestrator_dock_state", None)
        if area is None or state is None:
            return
        try:
            area.restoreState(state)
            self.log.info("Orchestrator panels reset to default layout")
        except Exception:
            self.log.warning("Failed to reset orchestrator panels", exc_info=True)

    def _on_workflow_item_clicked(self, item, column) -> None:
        """Handle workflow tree item click to show parameters."""
        _ = column  # Unused
        if not hasattr(self, "orchestratorParameterPanel"):
            return

        # Get the item data to determine what type it is
        item_data = item.data(0, Qt.UserRole)
        if item_data is None:
            return

        if isinstance(item_data, dict):
            item_type = item_data.get("type", "")
            if item_type == "round":
                # Round item
                self.orchestratorParameterPanel.show_round(
                    item_data["round_index"],
                    item_data["round_data"],
                )
            elif item_type == "operation":
                # Operation item
                self.orchestratorParameterPanel.show_operation(
                    item_data.get("round_data", {}),
                    item_data["operation_data"],
                    item_data.get("op_index", 0),
                )
            elif item_type == "fov":
                # FOV item
                self.orchestratorParameterPanel.show_fov_summary(
                    fov_id=item_data["fov_id"],
                    region_id=item_data.get("region_id", ""),
                    fov_index=item_data.get("fov_index", 0),
                    x_mm=item_data.get("x_mm", 0.0),
                    y_mm=item_data.get("y_mm", 0.0),
                    status=item_data.get("status", "PENDING"),
                    z_mm=item_data.get("z_mm", 0.0),
                )

    def _on_warning_navigate_to_fov(self, fov_id: str) -> None:
        """Handle warning panel navigation request."""
        if hasattr(self, "orchestratorWorkflowTree"):
            # Try to find and select the FOV in the tree
            tree = self.orchestratorWorkflowTree.tree
            for i in range(tree.topLevelItemCount()):
                round_item = tree.topLevelItem(i)
                for j in range(round_item.childCount()):
                    op_item = round_item.child(j)
                    for k in range(op_item.childCount()):
                        fov_item = op_item.child(k)
                        item_data = fov_item.data(0, Qt.UserRole)
                        if isinstance(item_data, dict) and item_data.get("fov_id") == fov_id:
                            tree.setCurrentItem(fov_item)
                            tree.scrollToItem(fov_item)
                            return

    def setupRecordTabWidget(self) -> None:
        # Unified acquisition setup tab (always available)
        self.recordTabWidget.addTab(
            self.acquisitionSetupWidget, "Acquisition Setup"
        )
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.recordTabWidget.addTab(
                self.wellplateMultiPointWidget, "Wellplate Multipoint"
            )
        if self._feature_flags.is_enabled("ENABLE_FLEXIBLE_MULTIPOINT"):
            self.recordTabWidget.addTab(
                self.flexibleMultiPointWidget, "Flexible Multipoint"
            )
        if USE_TEMPLATE_MULTIPOINT:
            self.recordTabWidget.addTab(
                self.templateMultiPointWidget, "Template Multipoint"
            )
        if self._feature_flags.is_enabled("ENABLE_TRACKING"):
            self.recordTabWidget.addTab(self.trackingControlWidget, "Tracking")
        self.recordTabWidget.currentChanged.connect(
            lambda: self.resizeCurrentTab(self.recordTabWidget)
        )
        self.resizeCurrentTab(self.recordTabWidget)

    def setupCameraTabWidget(self) -> None:
        if not self._feature_flags.is_enabled("USE_NAPARI_FOR_LIVE_CONTROL") or self.live_only_mode:
            self.cameraTabWidget.addTab(self.navigationWidget, "Stages")
        if self.piezoWidget:
            self.cameraTabWidget.addTab(self.piezoWidget, "Piezo")
        if ENABLE_NL5:
            self.cameraTabWidget.addTab(self.nl5Wdiget, "NL5")
        if self._feature_flags.is_enabled("ENABLE_SPINNING_DISK_CONFOCAL"):
            self.cameraTabWidget.addTab(self.spinningDiskConfocalWidget, "Confocal")
        if self.emission_filter_wheel:
            self.cameraTabWidget.addTab(self.filterControllerWidget, "Emission Filter")
        self.cameraTabWidget.addTab(self.cameraSettingWidget, "Camera")
        self.cameraTabWidget.addTab(self.autofocusWidget, "Contrast AF")
        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            self.cameraTabWidget.addTab(self.laserAutofocusControlWidget, "Laser AF")
        self.cameraTabWidget.addTab(self.focusMapWidget, "Focus Map")
        self.cameraTabWidget.currentChanged.connect(
            lambda: self.resizeCurrentTab(self.cameraTabWidget)
        )
        self.resizeCurrentTab(self.cameraTabWidget)

    # Onboarding tab tooltips: short "what is this / when do I use it" hints keyed
    # by the exact tab title, so they attach to whichever tabs a given microscope
    # configuration actually shows (many tabs are feature-flag gated).
    _ONBOARDING_TAB_TOOLTIPS = {
        # Acquisition mode tabs (recordTabWidget)
        "Acquisition Setup": (
            "Standard acquisition setup. Pick imaging channels, optional Z-stack and "
            "autofocus, then choose how field-of-view (FOV) positions are defined "
            "(whole wells, drawn ROIs, a manual point list, or a CSV) and press Start. "
            "Also hosts Quick Scan (fast unsaved preview) and Run Current FOV (single "
            "field). Best starting point for most acquisitions."
        ),
        "Wellplate Multipoint": (
            "Scan a multi-well plate. Select wells on the plate map and set scan "
            "size/overlap; each selected well is tiled automatically. Use when imaging "
            "defined wells of a plate."
        ),
        "Flexible Multipoint": (
            "Build a manual list of exact stage positions (add the current XYZ with "
            "'Add' or the ';' key). Use to image specific, arbitrary points rather "
            "than whole wells."
        ),
        "Template Multipoint": (
            "Acquire a repeating position template. Use for regular, template-defined "
            "layouts."
        ),
        "Tracking": (
            "Acquire while tracking a moving object. Use for live object-tracking "
            "experiments."
        ),
        # Camera / hardware / focus tabs (cameraTabWidget)
        "Stages": "Move the XY stage and Z, navigate to positions, and click-to-move on the image.",
        "Piezo": (
            "Fine objective-piezo Z control (sub-micron). On piezo-equipped systems, "
            "Z-stacks are captured with the piezo."
        ),
        "NL5": "NL5 confocal unit control.",
        "Confocal": "Spinning-disk confocal control.",
        "Emission Filter": "Select the emission filter-wheel position.",
        "Camera": "Camera settings: exposure time, gain, binning, ROI, and pixel format.",
        "Contrast AF": (
            "Software (contrast-based) autofocus — finds focus by maximizing image "
            "sharpness. Good general-purpose autofocus; slower than laser AF. "
            "(Focus family: Contrast AF = sharpness, Laser AF = fast reflective, "
            "Focus Map = surface, Focus Lock dock = maintain focus during a run.)"
        ),
        "Laser AF": (
            "Laser reflection autofocus — uses a reflected laser spot to find focus "
            "fast and reproducibly. Requires laser-AF hardware; calibrate before use. "
            "This is the autofocus an unattended Orchestrator run relies on; the "
            "Focus Lock dock keeps it locked during the run."
        ),
        "Focus Map": (
            "Define a focus surface across the sample so each position uses an "
            "interpolated focus. Use for large, tilted, or uneven samples. "
            "(Not the same as Laser/Contrast AF, which focus one position at a time.)"
        ),
        # Main view tabs (imageDisplayTabs)
        "Live View": (
            "Real-time camera feed. Use to find your sample, set exposure, and choose "
            "channels before acquiring."
        ),
        "Multichannel Acquisition": "Per-channel images from the most recent acquisition.",
        "Mosaic View": "Stitched mosaic of acquired fields, assembled as the scan runs.",
        "Plate View": "Whole-plate overview of acquired wells.",
        "NDViewer": "Browse an acquired N-dimensional dataset (channels, Z, time, positions).",
        "Plots": "Focus / Z surface and diagnostic plots.",
        "Laser-Based Focus": "Live view from the laser-autofocus camera and its settings.",
        "Fluidics": (
            "Manual fluidics control & status — prime lines, run a pump/valve by hand. "
            "For fluidics interleaved with imaging across multiple rounds, use the "
            "Orchestrator (automated runs)."
        ),
        "Orchestrator": (
            "Automated multi-round experiments: load a protocol, validate, then run "
            "unattended imaging + fluidics with progress, retries, and operator "
            "interventions."
        ),
    }

    def _apply_onboarding_tab_tooltips(self) -> None:
        """Attach 'what is this tab / when to use it' tooltips to tab strips.

        Matches by visible tab title so only the tabs a given configuration shows
        get tooltips, and never fails startup if a display is not a tab widget
        (e.g. live-only mode replaces imageDisplayTabs with a plain widget).
        """
        for tab_widget in (
            getattr(self, "recordTabWidget", None),
            getattr(self, "cameraTabWidget", None),
            getattr(self, "imageDisplayTabs", None),
        ):
            if tab_widget is None or not hasattr(tab_widget, "setTabToolTip") or not hasattr(tab_widget, "count"):
                continue
            try:
                for i in range(tab_widget.count()):
                    tip = self._ONBOARDING_TAB_TOOLTIPS.get(tab_widget.tabText(i))
                    if tip:
                        tab_widget.setTabToolTip(i, tip)
            except Exception:
                self.log.debug("Could not apply onboarding tab tooltips", exc_info=True)

    def setup_layout(self):
        # Setup the control panel layout
        layout_builder.setup_control_panel_layout(self)

        # Setup single or multi window layout
        if SINGLE_WINDOW:
            layout_builder.setup_single_window_layout(self)
        else:
            layout_builder.setup_multi_window_layout(self)

    def make_connections(self) -> None:
        # Core stream handler connections
        # Napari connections (complex, kept as method)
        self.makeNapariConnections()
        self._connect_tab_signals()
        self._connect_well_selector_button()
        self._connect_laser_autofocus_signals()
        # Confocal widgets publish commands/events directly.

    def _connect_tab_signals(self) -> None:
        self.recordTabWidget.currentChanged.connect(self.onTabChanged)
        if not self.live_only_mode:
            self.imageDisplayTabs.currentChanged.connect(self.onDisplayTabChanged)
        # Fire for the initial tab so _is_active_tab is set correctly from startup
        self.onTabChanged(self.recordTabWidget.currentIndex())

    def _connect_well_selector_button(self) -> None:
        if hasattr(self.imageDisplayWindow, "btn_well_selector"):
            self.imageDisplayWindow.btn_well_selector.clicked.connect(
                lambda: self.toggleWellSelector(not self.dock_wellSelection.isVisible())
            )

    def _connect_laser_autofocus_signals(self) -> None:
        if not self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            return

        if self.cameraSettingWidget_focus_camera:
            self.laserAutofocusSettingWidget.signal_newExposureTime.connect(
                self.cameraSettingWidget_focus_camera.set_exposure_time
            )
            self.laserAutofocusSettingWidget.signal_newAnalogGain.connect(
                self.cameraSettingWidget_focus_camera.set_analog_gain
            )
        self.laserAutofocusSettingWidget.signal_apply_settings.connect(
            self.laserAutofocusControlWidget.update_init_state
        )
        self.laserAutofocusSettingWidget.update_exposure_time(
            self.laserAutofocusSettingWidget.exposure_spinbox.value()
        )
        self.laserAutofocusSettingWidget.update_analog_gain(
            self.laserAutofocusSettingWidget.analog_gain_spinbox.value()
        )
        self.streamHandler_focus_camera.image_to_display.connect(
            self.imageDisplayWindow_focus.display_image
        )

    def makeNapariConnections(self) -> None:
        """Initialize all Napari connections in one place"""
        self.napari_connections = {
            "napariLiveWidget": [],
            "napariMultiChannelWidget": [],
            "napariMosaicDisplayWidget": [],
        }
        # NOTE: Avoid connecting Qt signals to lambdas for cross-thread delivery:
        # lambdas are not QObjects, so Qt may execute them on the emitter thread.
        # Keep capture fanout as QObject methods to ensure queued delivery on the GUI thread.

        # Setup live view connections
        if self._feature_flags.is_enabled("USE_NAPARI_FOR_LIVE_VIEW") and not self.live_only_mode:
            self.napari_connections["napariLiveWidget"] = [
                (
                    self.streamHandler.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(
                        image, from_autofocus=False
                    ),
                ),
            ]

            if self._feature_flags.is_enabled("USE_NAPARI_FOR_LIVE_CONTROL"):
                self.napari_connections["napariLiveWidget"].extend(
                    [
                        # Napari live control publishes commands/events directly.
                    ]
                )
        else:
            # Non-Napari display connections
            self.streamHandler.image_to_display.connect(self.imageDisplay.enqueue)
            self.imageDisplay.image_to_display.connect(
                self.imageDisplayWindow.display_image
            )
            self.imageDisplayWindow.image_click_coordinates.connect(
                lambda x, y, w, h: self._ui_event_bus.publish(
                    ImageCoordinateClickedCommand(
                        x_pixel=x,
                        y_pixel=y,
                        image_width=w,
                        image_height=h,
                        from_napari=False,
                    )
                )
            )

        if not self.live_only_mode:
            # Setup multichannel widget connections
            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MULTIPOINT"):
                self.napari_connections["napariMultiChannelWidget"] = [
                    (
                        self.streamHandler.capture,
                        self._on_stream_capture_multichannel,
                    )
                ]
            else:
                self.streamHandler.capture.connect(
                    lambda image, info: self.imageArrayDisplayWindow.display_image(
                        image, info.configuration.illumination_source
                    )
                )

            # Setup mosaic display widget connections
            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MOSAIC_DISPLAY"):
                self.napari_connections["napariMosaicDisplayWidget"] = []
                self.napari_connections["napariMosaicDisplayWidget"].append(
                    (
                        self.streamHandler.capture,
                        self._on_stream_capture_mosaic,
                    )
                )
                self.napari_connections["napariMosaicDisplayWidget"].extend(
                    [
                        (
                            self.napariMosaicDisplayWidget.signal_clear_viewer,
                            self.navigationViewer.clear_overlay,
                        ),
                    ]
                )

            # Make initial connections
            self.updateNapariConnections()

    def _layer_name_from_capture_info(self, info) -> str:
        try:
            objective_mag = str(
                int(self.objectiveStore.get_current_objective_info()["magnification"])
            )
            return objective_mag + "x " + info.configuration.name
        except Exception:
            return getattr(getattr(info, "configuration", None), "name", "Unknown")

    def _on_stream_capture_mosaic(self, image, info) -> None:
        if getattr(self, "napariMosaicDisplayWidget", None) is None:
            log.warning("_on_stream_capture_mosaic: napariMosaicDisplayWidget is None")
            return
        channel_name = self._layer_name_from_capture_info(info)
        self.napariMosaicDisplayWidget.updateMosaic(image, info, channel_name)

    def _on_stream_capture_multichannel(self, image, info) -> None:
        if getattr(self, "napariMultiChannelWidget", None) is None:
            return
        channel_name = self._layer_name_from_capture_info(info)
        self.napariMultiChannelWidget.updateLayers(image, 0, 0, info.z_index, channel_name)

    def updateNapariConnections(self) -> None:
        # Update Napari connections based on performance mode. Live widget connections are preserved
        for widget_name, connections in self.napari_connections.items():
            if (
                widget_name != "napariLiveWidget"
            ):  # Always keep the live widget connected
                widget = getattr(self, widget_name, None)
                if widget:
                    for signal, slot in connections:
                        if self.performance_mode:
                            try:
                                signal.disconnect(slot)
                            except TypeError:
                                # Connection might not exist, which is fine
                                pass
                        else:
                            try:
                                signal.connect(slot)
                            except TypeError:
                                # Connection might already exist, which is fine
                                pass

    def toggleNapariTabs(self) -> None:
        # Enable/disable Napari tabs based on performance mode
        for i in range(1, self.imageDisplayTabs.count()):
            if self.imageDisplayTabs.tabText(i) != self.LASER_BASED_FOCUS_TAB_NAME:
                self.imageDisplayTabs.setTabEnabled(i, not self.performance_mode)

        if self.performance_mode:
            # Switch to the NapariLiveWidget tab if it exists
            for i in range(self.imageDisplayTabs.count()):
                if isinstance(
                    self.imageDisplayTabs.widget(i), widgets.NapariLiveWidget
                ):
                    self.imageDisplayTabs.setCurrentIndex(i)
                    break

    def togglePerformanceMode(self) -> None:
        self.performance_mode = self.performanceModeToggle.isChecked()
        button_txt = "Disable" if self.performance_mode else "Enable"
        self.performanceModeToggle.setText(button_txt + " Performance Mode")
        self.updateNapariConnections()
        self.toggleNapariTabs()
        print(f"Performance mode {'enabled' if self.performance_mode else 'disabled'}")

    def openLedMatrixSettings(self) -> None:
        if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            dialog = widgets.LedMatrixSettingsDialog(self.liveController.led_array)
            dialog.exec_()

    def openPreferences(self) -> None:
        from configparser import ConfigParser
        from _def import CACHED_CONFIG_FILE_PATH
        import os

        if CACHED_CONFIG_FILE_PATH and os.path.exists(CACHED_CONFIG_FILE_PATH):
            config = ConfigParser()
            config.read(CACHED_CONFIG_FILE_PATH)
            dialog = widgets.PreferencesDialog(config, CACHED_CONFIG_FILE_PATH, self)
            dialog.exec_()
        else:
            self.log.warning("No configuration file found")

    def _open_illumination_config(self) -> None:
        from squid.ui.widgets.channel_config_dialogs import IlluminationChannelConfiguratorDialog

        config_repo = self.channelConfigurationManager.config_repo
        dialog = IlluminationChannelConfiguratorDialog(config_repo, self)
        dialog.signal_channels_updated.connect(self._on_channel_config_changed)
        dialog.exec_()

    def _open_acquisition_config(self) -> None:
        from squid.ui.widgets.channel_config_dialogs import AcquisitionChannelConfiguratorDialog

        config_repo = self.channelConfigurationManager.config_repo
        dialog = AcquisitionChannelConfiguratorDialog(config_repo, self)
        dialog.signal_channels_updated.connect(self._on_channel_config_changed)
        dialog.exec_()

    def _open_filter_wheel_config(self) -> None:
        from squid.ui.widgets.channel_config_dialogs import FilterWheelConfiguratorDialog

        config_repo = self.channelConfigurationManager.config_repo
        dialog = FilterWheelConfiguratorDialog(config_repo, self)
        dialog.signal_config_updated.connect(self._on_channel_config_changed)
        dialog.exec_()

    def _on_channel_config_changed(self) -> None:
        """Refresh channel configs after dialog save."""
        from squid.core.events import ChannelConfigurationsChanged

        self.channelConfigurationManager.config_repo.clear_all_cache()
        objective = getattr(self.objectiveStore, "current_objective", None) or ""
        configs = self.channelConfigurationManager.get_configurations(objective)
        self._ui_event_bus.publish(
            ChannelConfigurationsChanged(
                objective_name=objective,
                configuration_names=[ch.name for ch in configs],
            )
        )

    def onTabChanged(self, index: int) -> None:
        is_setup = (
            index == self.recordTabWidget.indexOf(self.acquisitionSetupWidget)
            if self.acquisitionSetupWidget is not None
            else False
        )
        is_flexible_acquisition = (
            (index == self.recordTabWidget.indexOf(self.flexibleMultiPointWidget))
            if self._feature_flags.is_enabled("ENABLE_FLEXIBLE_MULTIPOINT")
            else False
        )
        is_wellplate_acquisition = (
            (index == self.recordTabWidget.indexOf(self.wellplateMultiPointWidget))
            if ENABLE_WELLPLATE_MULTIPOINT
            else False
        )
        self.toggleWellSelector(
            is_wellplate_acquisition
            and self.wellSelectionWidget.format != "glass slide"
        )
        active_tab = (
            "setup"
            if is_setup
            else "wellplate"
            if is_wellplate_acquisition
            else "flexible"
            if is_flexible_acquisition
            else "other"
        )
        self._ui_event_bus.publish(ActiveAcquisitionTabChanged(active_tab=active_tab))

    def resizeCurrentTab(self, tabWidget: QTabWidget) -> None:
        current_widget = tabWidget.currentWidget()
        if current_widget:
            total_height = (
                current_widget.sizeHint().height() + tabWidget.tabBar().height()
            )
            tabWidget.resize(tabWidget.width(), total_height)
            tabWidget.setMaximumHeight(total_height)
            tabWidget.updateGeometry()
            self.updateGeometry()

    def onDisplayTabChanged(self, index: int) -> None:
        current_widget = self.imageDisplayTabs.widget(index)
        if hasattr(current_widget, "viewer"):
            current_widget.activate()

        # Stop focus camera live if not on laser focus tab
        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            is_laser_focus_tab = (
                self.imageDisplayTabs.tabText(index) == self.LASER_BASED_FOCUS_TAB_NAME
            )

            if hasattr(self, "dock_wellSelection"):
                self.dock_wellSelection.setVisible(not is_laser_focus_tab)

            if not is_laser_focus_tab:
                self.laserAutofocusSettingWidget.stop_live()

        # Only show well selector in Live View tab if it was previously shown
        if self.imageDisplayTabs.tabText(index) == "Live View":
            self.toggleWellSelector(
                self.well_selector_visible
            )  # Use stored visibility state
        else:
            self.toggleWellSelector(False)

    @handles(WellplateFormatChanged)
    def _on_wellplate_format_changed(self, event: WellplateFormatChanged) -> None:
        """Handle WellplateFormatChanged from EventBus.

        This is the event-driven entry point for wellplate format changes.
        Widgets should publish this event instead of using Qt signals.
        """
        format_name = event.format_name

        if format_name == "glass slide":
            self.toggleWellSelector(False)
            self.stageUtils.is_wellplate = False
        else:
            self.toggleWellSelector(True)
            self.stageUtils.is_wellplate = True

        # Swap well selector widget type for 1536 vs other formats (UI-only).
        if format_name == "1536 well plate" and not isinstance(
            self.wellSelectionWidget, widgets.Well1536SelectionWidget
        ):
            self.replaceWellSelectionWidget(widgets.Well1536SelectionWidget(self._ui_event_bus))
        elif format_name != "1536 well plate" and isinstance(
            self.wellSelectionWidget, widgets.Well1536SelectionWidget
        ):
            self.replaceWellSelectionWidget(
                widgets.WellSelectionWidget(
                    self._ui_event_bus,
                    format_name,
                    rows=event.rows,
                    cols=event.cols,
                    well_spacing_mm=event.well_spacing_mm,
                    well_size_mm=event.well_size_mm,
                    a1_x_mm=event.a1_x_mm,
                    a1_y_mm=event.a1_y_mm,
                    a1_x_pixel=event.a1_x_pixel,
                    a1_y_pixel=event.a1_y_pixel,
                    number_of_skip=event.number_of_skip,
                )
            )

    def replaceWellSelectionWidget(
        self, new_widget: widgets.WellSelectionWidget
    ) -> None:
        old_widget = self.wellSelectionWidget
        if hasattr(old_widget, "_cleanup_subscriptions"):
            old_widget._cleanup_subscriptions()
        old_widget.setParent(None)
        old_widget.deleteLater()
        self.wellSelectionWidget = new_widget
        if (
            USE_NAPARI_WELL_SELECTION
            and not self.performance_mode
            and not self.live_only_mode
        ):
            self.napariLiveWidget.replace_well_selector(self.wellSelectionWidget)
        else:
            self.dock_wellSelection.addWidget(self.wellSelectionWidget)

    def toggleWellSelector(self, show: bool, remember_state: bool = True) -> None:
        if (
            show
            and self.imageDisplayTabs.tabText(self.imageDisplayTabs.currentIndex())
            == "Live View"
        ):
            self.dock_wellSelection.setVisible(True)
        else:
            self.dock_wellSelection.setVisible(False)

        # Only update visibility state if we're in Live View tab and we want to remember the state
        # remember_state is False when we're toggling the well selector for starting/stopping an acquisition
        if (
            self.imageDisplayTabs.tabText(self.imageDisplayTabs.currentIndex())
            == "Live View"
            and remember_state
        ):
            self.well_selector_visible = show

        # Update button text
        if hasattr(self.imageDisplayWindow, "btn_well_selector"):
            self.imageDisplayWindow.btn_well_selector.setText(
                "Hide Well Selector" if show else "Show Well Selector"
            )

    @handles(AcquisitionStateChanged)
    def _on_acquisition_state_changed(self, event: AcquisitionStateChanged) -> None:
        """Handle backend acquisition state changes (UI truth-from-backend)."""
        self._apply_acquisition_ui_state(event.in_progress)
        # Clear z-plot when a new acquisition starts (not on abort)
        if event.in_progress and not event.is_aborting:
            if getattr(self, "zPlotWidget", None) is not None:
                self.zPlotWidget.clear()

    @handles(LiveStateChanged)
    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Switch to live tab when main camera goes live and enable alignment widget."""
        if getattr(event, "camera", "main") != "main":
            return
        if hasattr(self, "imageDisplayTabs") and event.is_live:
            self.imageDisplayTabs.setCurrentIndex(0)
        # Enable/disable alignment widget based on live state
        if self.alignmentWidget is not None:
            if event.is_live:
                self.alignmentWidget.enable()
            else:
                self.alignmentWidget.disable()

    @handles(StagePositionChanged)
    def _on_stage_position_for_autosave(self, event: StagePositionChanged) -> None:
        """Cache stage position for periodic auto-save (crash recovery)."""
        self._last_stage_pos = (event.x_mm, event.y_mm, event.z_mm)
        self._stage_pos_changed_since_save = True

    def _toggle_live_shortcut(self) -> None:
        """Keyboard shortcut handler: toggle live view."""
        if self.liveControlWidget is not None:
            self.liveControlWidget.btn_live.click()

    def _toggle_performance_shortcut(self) -> None:
        """Keyboard shortcut handler: toggle performance mode."""
        if hasattr(self, "performanceModeToggle"):
            self.performanceModeToggle.click()

    def _autosave_stage_position(self) -> None:
        """Periodically save stage position for crash recovery."""
        if not self._stage_pos_changed_since_save or self._last_stage_pos is None:
            return
        try:
            from squid.backend.drivers.stages.stage_utils import cache_position

            x_mm, y_mm, z_mm = self._last_stage_pos
            pos = Pos(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, theta_rad=None)
            cache_position(pos, self._stage_config)
            self._stage_pos_changed_since_save = False
        except Exception:
            self.log.debug("Stage position auto-save failed (non-critical)", exc_info=True)

    @handles(AcquisitionCoordinates)
    def _on_acquisition_coordinates(self, event: AcquisitionCoordinates) -> None:
        if getattr(self, "zPlotWidget", None) is None:
            return
        self.zPlotWidget.add_point(event.x_mm, event.y_mm, event.z_mm, event.region_id)

    @handles(AcquisitionWorkerFinished)
    def _on_acquisition_worker_finished(self, _event: AcquisitionWorkerFinished) -> None:
        if getattr(self, "zPlotWidget", None) is None:
            return
        self.zPlotWidget.plot()

    @handles(PlateViewInit)
    def _on_plate_view_init(self, event: PlateViewInit) -> None:
        if not getattr(self, "napariPlateViewWidget", None):
            return
        self.napariPlateViewWidget.initPlateLayout(
            event.num_rows,
            event.num_cols,
            event.well_slot_shape,
            event.fov_grid_shape,
            event.channel_names,
        )

    @handles(PlateViewUpdate)
    def _on_plate_view_update(self, event: PlateViewUpdate) -> None:
        if not getattr(self, "napariPlateViewWidget", None):
            return
        self.napariPlateViewWidget.updatePlateView(
            event.channel_idx,
            event.channel_name,
            event.plate_image,
        )

    @handles(AcquisitionStarted)
    def _on_acquisition_started(self, event: AcquisitionStarted) -> None:
        """Handle acquisition start without forcing file-mode dataset loading.

        NDViewer receives push-mode start/image events directly from the acquisition
        pipeline. Eagerly calling set_dataset_path() here can trigger expensive
        file-system scans on the UI thread while the run is starting.
        """
        if getattr(self, "ndviewerTab", None) is None:
            return

        try:
            base_path = event.base_path
            experiment_id = event.experiment_id
            self.log.debug(
                "_on_acquisition_started: base_path=%s, experiment_id=%s (defer file-mode load)",
                base_path,
                experiment_id,
            )
        except Exception:
            self.log.exception("Failed to update NDViewer tab for new acquisition")

    def _on_plate_view_fov_clicked(self, well_id: str, fov_index: int) -> None:
        """Handle double-click on plate view: navigate NDViewer to FOV and switch tab."""
        if getattr(self, "ndviewerTab", None) is None:
            self.log.debug("FOV click ignored: NDViewer tab not available")
            return

        if not self.ndviewerTab.go_to_fov(well_id, fov_index):
            self.log.debug(f"Could not navigate to FOV well={well_id}, fov={fov_index} - may not exist in dataset")
            return

        # Switch to NDViewer tab
        ndviewer_tab_idx = self.imageDisplayTabs.indexOf(self.ndviewerTab)
        if ndviewer_tab_idx >= 0:
            self.imageDisplayTabs.setCurrentIndex(ndviewer_tab_idx)
        else:
            self.log.warning("NDViewer tab exists but not found in tab widget")

    def _apply_acquisition_ui_state(self, acquisition_started: bool) -> None:
        """Apply acquisition start/stop UI state changes from backend truth."""
        self.log.debug(f"_apply_acquisition_ui_state({acquisition_started=})")
        if acquisition_started:
            self.log.info("STARTING ACQUISITION")
        else:
            self.log.info("FINISHED ACQUISITION")

        # Click to move off during acquisition
        # Update both the widget and publish event for ImageClickController
        self.navigationWidget.set_click_to_move(not acquisition_started)
        self._ui_event_bus.publish(ClickToMoveEnabledChanged(enabled=not acquisition_started))

        # disable other acquisition tabs during acquisition
        current_index = self.recordTabWidget.currentIndex()
        for index in range(self.recordTabWidget.count()):
            self.recordTabWidget.setTabEnabled(
                index, not acquisition_started or index == current_index
            )

        # disable autolevel once acquisition started
        if acquisition_started:
            self._ui_event_bus.publish(AutoLevelCommand(enabled=False))

        # hide well selector during acquisition
        is_wellplate_acquisition = (
            (
                current_index
                == self.recordTabWidget.indexOf(self.wellplateMultiPointWidget)
            )
            if ENABLE_WELLPLATE_MULTIPOINT
            else False
        )
        if (
            is_wellplate_acquisition
            and self.wellSelectionWidget.format != "glass slide"
        ):
            self.toggleWellSelector(not acquisition_started, remember_state=False)
        else:
            self.toggleWellSelector(False)

        # Progress bars are driven by AcquisitionStateChanged in the acquisition widgets.

    def move_to_mm(self, x_mm: float, y_mm: float) -> None:
        self._ui_event_bus.publish(MoveStageToCommand(x_mm=x_mm, y_mm=y_mm))

    def _restore_cached_camera_settings(self) -> None:
        """Restore cached camera settings (binning, pixel format) from previous session.

        Silently returns if no cached settings exist. Errors are logged but do not
        prevent application startup.
        """
        cached_settings = load_camera_settings()
        if not cached_settings:
            return

        # Apply binning
        try:
            self.camera.set_binning(*cached_settings.binning)
            self.log.info(f"Restored camera binning: {cached_settings.binning}")
        except ValueError as e:
            self.log.warning(f"Cannot restore binning {cached_settings.binning} - not supported: {e}")
        except Exception as e:
            self.log.error(f"Error restoring camera binning: {e}")

        # Apply pixel format if available
        if cached_settings.pixel_format:
            try:
                from squid.core.config import CameraPixelFormat
                pixel_format = CameraPixelFormat.from_string(cached_settings.pixel_format)
                self.camera.set_pixel_format(pixel_format)
                self.log.info(f"Restored camera pixel format: {cached_settings.pixel_format}")
            except (KeyError, ValueError) as e:
                self.log.warning(f"Cannot restore pixel format '{cached_settings.pixel_format}': {e}")
            except Exception as e:
                self.log.error(f"Error restoring camera pixel format: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Alignment Widget Setup
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_alignment_widget(self) -> None:
        """Setup the alignment widget for sample registration with previous acquisitions.

        Only created when using napari for live view.
        """
        if self.napariLiveWidget is None:
            return

        try:
            napari_viewer = self.napariLiveWidget.viewer
            if napari_viewer is None:
                self.log.warning("Napari viewer not available for alignment widget")
                return

            self.alignmentWidget = widgets.AlignmentWidget(napari_viewer)

            # Connect signals
            self.alignmentWidget.signal_move_to_position.connect(self._alignment_move_to)
            self.alignmentWidget.signal_request_current_position.connect(
                self._alignment_provide_position
            )
            self.alignmentWidget.signal_offset_set.connect(
                lambda x, y: self.log.info(f"Alignment offset set: ({x:.4f}, {y:.4f}) mm")
            )
            self.alignmentWidget.signal_offset_cleared.connect(
                lambda: self.log.info("Alignment offset cleared")
            )

            # Add alignment widget to napari viewer as a dock widget
            napari_viewer.window.add_dock_widget(
                self.alignmentWidget,
                name="Alignment",
                area="left",
            )

            # Set alignment widget on multipoint controller
            multipoint = getattr(self._controllers, "multipoint", None) if self._controllers else None
            if multipoint is not None:
                multipoint.set_alignment_widget(self.alignmentWidget)
            else:
                self.log.debug("MultiPoint controller not available for alignment widget")

            self.log.info("Alignment widget created and connected")
        except Exception:
            self.log.exception("Failed to setup alignment widget")
            self.alignmentWidget = None

    def _alignment_move_to(self, x_mm: float, y_mm: float) -> None:
        """Handle alignment widget request to move stage to position."""
        self._ui_event_bus.publish(MoveStageToCommand(x_mm=x_mm, y_mm=y_mm))
        self.log.debug(f"Alignment: published move to ({x_mm:.4f}, {y_mm:.4f})")

    def _alignment_provide_position(self) -> None:
        """Provide current stage position to alignment widget."""
        try:
            if self._stage_service is not None and self.alignmentWidget is not None:
                x_mm = self._stage_service.x_mm
                y_mm = self._stage_service.y_mm
                self.alignmentWidget.set_current_position(x_mm, y_mm)
                self.log.debug(f"Alignment: provided position ({x_mm:.4f}, {y_mm:.4f})")
            else:
                self.log.warning("Stage service or alignment widget not available")
        except Exception:
            self.log.exception("Error providing alignment position")

    def showEvent(self, event) -> None:
        """Connect warning/error handler when window is shown."""
        super().showEvent(event)
        self._connect_warning_handler()

    def _connect_warning_handler(self) -> None:
        """Connect logging handler to warning/error widget."""
        if self.warningErrorWidget is None:
            return

        self._warning_handler = squid.core.logging.BufferingHandler()
        squid.core.logging.get_logger().addHandler(self._warning_handler)
        self.warningErrorWidget.connect_handler(self._warning_handler)
        self.log.debug("Warning/error widget: connected logging handler")

    def _disconnect_warning_handler(self) -> None:
        """Disconnect logging handler from warning/error widget.

        Uses robust error handling to ensure cleanup completes even if
        individual operations fail (e.g., handler already removed).
        """
        if self._warning_handler is not None:
            try:
                squid.core.logging.get_logger().removeHandler(self._warning_handler)
            except Exception:
                pass
            if self.warningErrorWidget is not None:
                self.warningErrorWidget.disconnect_handler()
            self._warning_handler = None
            self.log.debug("Warning/error widget: disconnected logging handler")

    # ========================================================================
    # Workflow Runner
    # ========================================================================

    def _open_workflow_runner(self) -> None:
        """Open the Workflow Runner dialog (lazy creation)."""
        if not hasattr(self, "_workflow_runner_dialog") or self._workflow_runner_dialog is None:
            from squid.ui.widgets.workflow.workflow_runner_dialog import WorkflowRunnerDialog

            self._workflow_runner_dialog = WorkflowRunnerDialog(
                event_bus=self._ui_event_bus, parent=self
            )
        self._workflow_runner_dialog.show()
        self._workflow_runner_dialog.raise_()
        self._workflow_runner_dialog.activateWindow()

    @handles(WorkflowRunnerStateChanged)
    def _on_workflow_runner_state_changed(self, event: "WorkflowRunnerStateChanged") -> None:
        """Enable/disable main window controls during workflow execution."""
        new_state = event.new_state
        if new_state in ("RUNNING_SCRIPT", "RUNNING_ACQUISITION"):
            self._set_workflow_controls_enabled(False)
        elif new_state == "PAUSED":
            self._set_workflow_controls_enabled(True)
        elif new_state in ("COMPLETED", "FAILED", "ABORTED", "IDLE"):
            self._set_workflow_controls_enabled(True)

    def _set_workflow_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable main window controls during workflow execution."""
        for widget_name in (
            "navigationWidget",
            "liveControlWidget",
            "autofocusWidget",
            "objectivesWidget",
            "recordTabWidget",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not getattr(self, "_skip_close_confirmation", False):
            reply = QMessageBox.question(
                self,
                "Confirm Exit",
                "Are you sure you want to exit the software?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return

        if self._subscriptions and self._ui_event_bus is not None:
            auto_unsubscribe(self._subscriptions, self._ui_event_bus)
            self._subscriptions.clear()

        # Disconnect warning/error widget logging handler
        self._disconnect_warning_handler()

        # UI publishes commands only; hardware cleanup is handled by ApplicationContext.shutdown().
        self._ui_event_bus.publish(StopLiveCommand())

        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS") and self.imageDisplayWindow_focus is not None:
            try:
                self.imageDisplayWindow_focus.close()
            except Exception:
                pass

        # Clean up NDViewer resources (file handles, timers)
        if getattr(self, "ndviewerTab", None) is not None:
            try:
                self.ndviewerTab.cleanup()
            except Exception:
                self.log.exception("Error closing NDViewer tab during shutdown")

        # Close napari viewers to prevent vispy/OpenGL thread hangs on exit
        for attr in ("napariLiveWidget", "napariMultiChannelWidget",
                     "napariMosaicDisplayWidget", "napariPlateViewWidget"):
            widget = getattr(self, attr, None)
            if widget is not None and hasattr(widget, "viewer"):
                try:
                    widget.viewer.close()
                except Exception:
                    pass

        try:
            self.imageDisplay.close()
        except Exception:
            pass

        # Save camera settings for next session
        try:
            save_camera_settings(self.camera)
        except Exception as e:
            self.log.warning(f"Could not save camera settings: {e}")

        event.accept()

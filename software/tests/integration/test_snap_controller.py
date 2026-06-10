"""Integration test for SnapController: multichannel snap + OME-TIFF save."""

import os

import tifffile

from squid.application import ApplicationContext
from squid.core.events import CaptureMultichannelCommand
from squid.backend.controllers.snap_controller import SnapController


def test_snap_multichannel_writes_ome_tiff(tmp_path):
    ctx = ApplicationContext(simulation=True)
    try:
        objective = ctx.microscope.objective_store.current_objective
        ccm = ctx.microscope.channel_configuration_manager
        names = [c.name for c in ccm.get_configurations(objective, enabled_only=True)][:2]

        snap = SnapController(
            camera_service=ctx.services.get("camera"),
            illumination_service=ctx.services.get("illumination"),
            mode_controller=ctx.controllers.microscope_mode,
            live_controller=ctx.controllers.live,
            channel_config_service=ccm,
        )

        frames = snap.snap_multichannel(names, objective)
        assert len(frames) == len(names)

        snap._on_capture(
            CaptureMultichannelCommand(
                channel_names=names,
                objective=objective,
                save_directory=str(tmp_path),
                file_prefix="itest",
            )
        )

        files = [f for f in os.listdir(tmp_path) if f.endswith(".ome.tiff")]
        assert files, "no OME-TIFF written"

        arr = tifffile.imread(str(tmp_path / files[0]))
        assert arr.shape[0] == len(names)
    finally:
        ctx.shutdown()

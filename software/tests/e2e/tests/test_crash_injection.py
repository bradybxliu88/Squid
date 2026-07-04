"""
E2E crash-injection tests (B1.1).

Kill a running experiment at a fault point, then assert it resumes from the
on-disk checkpoint with no prior-data loss and no duplicated coordinates. These
use the L1 "soft crash" (graceful worker abort + fresh resume), which faithfully
models the fresh-process, empty-cache resume the backend performs.

Scope note: the atomicity ("MID-WRITE") fault point is proven deterministically
at unit level in tests/unit/core/utils/test_atomic_io.py. The destructive
zarr/OME resume baselines (which currently LOSE prior timepoints) are added as
xfail here when B1.4/B1.5 land the resume-aware writers -- they need per-run save
mode control that does not exist yet (see plan corrections C1/C2).
"""

from __future__ import annotations

import pytest

from tests.harness import BackendContext
from tests.e2e.harness import (
    OrchestratorSimulator,
    assert_checkpoint_valid,
    assert_no_prior_data_loss,
    assert_no_duplicate_coordinates,
)
from squid.core.protocol import ProtocolLoader


@pytest.mark.e2e
@pytest.mark.checkpoint
@pytest.mark.slow
class TestCrashInjectionResume:
    """Resume after a mid-round / mid-timepoint soft crash."""

    def test_resume_after_crash_at_round_completes(
        self,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
        experiment_output_dir,
    ):
        center = e2e_backend_ctx.get_stage_center()

        sim = OrchestratorSimulator(e2e_backend_ctx)
        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        started = sim.start(
            base_path=str(experiment_output_dir),
            experiment_id="crash_at_round",
        )
        assert started

        # Run into the experiment, then crash as round 1 begins.
        sim.crash_at_round(1, timeout_s=60)

        experiment_path = sim.orchestrator._experiment_path
        assert experiment_path is not None

        # B1.0: the checkpoint survived the crash and is loadable.
        checkpoint = assert_checkpoint_valid(experiment_path)

        # Resume from the on-disk checkpoint (fresh coordinates, same folder).
        sim.clear_coordinates()
        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        protocol = ProtocolLoader().load(multi_round_fish_protocol)
        expected_remaining = len(protocol.rounds) - checkpoint.round_index

        result = sim.run_and_wait(
            base_path=experiment_path,
            timeout_s=120,
            resume_from_checkpoint=True,
        )

        assert result.success, f"Resume failed: {result.error}"
        assert result.completed_rounds == expected_remaining
        assert_no_prior_data_loss(experiment_path)
        assert_no_duplicate_coordinates(experiment_path)
        sim.cleanup()

    def test_resume_after_crash_mid_fovs(
        self,
        e2e_backend_ctx: BackendContext,
        single_round_imaging_protocol: str,
        experiment_output_dir,
    ):
        center = e2e_backend_ctx.get_stage_center()

        sim = OrchestratorSimulator(e2e_backend_ctx)
        sim.load_protocol(single_round_imaging_protocol)
        sim.add_grid_region("region_1", center=center, n_x=3, n_y=3)

        started = sim.start(
            base_path=str(experiment_output_dir),
            experiment_id="crash_mid_fovs",
        )
        assert started

        # Crash partway through the FOV grid.
        sim.crash_after_n_fovs(2, timeout_s=60)

        experiment_path = sim.orchestrator._experiment_path
        assert experiment_path is not None
        assert_checkpoint_valid(experiment_path)

        sim.clear_coordinates()
        sim.load_protocol(single_round_imaging_protocol)
        sim.add_grid_region("region_1", center=center, n_x=3, n_y=3)

        result = sim.run_and_wait(
            base_path=experiment_path,
            timeout_s=120,
            resume_from_checkpoint=True,
        )

        assert result.success, f"Resume failed: {result.error}"
        assert_no_prior_data_loss(experiment_path)
        assert_no_duplicate_coordinates(experiment_path)
        sim.cleanup()

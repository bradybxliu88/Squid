"""Unit tests for ImagingExecutor."""

import csv
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from squid.backend.controllers.orchestrator.imaging_executor import (
    ImagingExecutor,
    _FocusLockRunMonitor,
)
from squid.backend.controllers.orchestrator.state import AddWarningCommand
from squid.core.events import (
    AcquisitionFinished,
    AutofocusMode,
    EventBus,
    FocusLockMetricsUpdated,
    FocusLockPiezoLimitCritical,
    FocusLockStatusChanged,
    FocusLockWarning,
    ImagingProtocolReadbackReady,
    RequestImagingProtocolReadback,
)
from squid.core.protocol import ImagingProtocol
from squid.core.utils.cancel_token import CancelToken


def test_execute_with_config_resets_start_fov_index():
    event_bus = MagicMock()
    multipoint = MagicMock()
    multipoint.update_config = MagicMock()
    multipoint.run_acquisition = MagicMock(return_value=False)
    multipoint.set_start_fov_index = MagicMock()
    multipoint.set_current_round_index = MagicMock()
    multipoint._autofocus_executor = None

    executor = ImagingExecutor(
        event_bus=event_bus,
        multipoint_controller=multipoint,
        channel_config_manager=None,
    )

    protocol = ImagingProtocol(channels=["DAPI"])

    executor.execute_with_config(
        imaging_config=protocol,
        output_path="/tmp",
        cancel_token=MagicMock(),
        round_index=0,
        resume_fov_index=3,
        experiment_id="round_000",
    )

    # Resume index should be set for this run and reset afterwards.
    assert multipoint.set_start_fov_index.call_args_list[0][0][0] == 3
    assert multipoint.set_start_fov_index.call_args_list[-1][0][0] == 0


def test_execute_with_config_passes_run_scan_coordinates_override():
    event_bus = MagicMock()
    multipoint = MagicMock()
    multipoint.update_config = MagicMock()
    multipoint.run_acquisition = MagicMock(return_value=False)
    multipoint.set_start_fov_index = MagicMock()
    multipoint.set_current_round_index = MagicMock()
    multipoint._autofocus_executor = None

    executor = ImagingExecutor(
        event_bus=event_bus,
        multipoint_controller=multipoint,
        channel_config_manager=None,
    )

    protocol = ImagingProtocol(channels=["DAPI"])
    scan_coordinates = MagicMock()

    executor.execute_with_config(
        imaging_config=protocol,
        output_path="/tmp",
        cancel_token=MagicMock(),
        round_index=0,
        experiment_id="round_000",
        scan_coordinates_override=scan_coordinates,
    )

    multipoint.run_acquisition.assert_called_once_with(
        acquire_current_fov=False,
        scan_coordinates_override=scan_coordinates,
    )


def test_execute_with_focus_lock_enables_piezo_zstack():
    event_bus = MagicMock()
    multipoint = MagicMock()
    multipoint.update_config = MagicMock()
    multipoint.run_acquisition = MagicMock(return_value=False)
    multipoint.set_start_fov_index = MagicMock()
    multipoint.set_current_round_index = MagicMock()
    multipoint._autofocus_executor = None

    executor = ImagingExecutor(
        event_bus=event_bus,
        multipoint_controller=multipoint,
        channel_config_manager=None,
    )

    protocol = ImagingProtocol(
        acquisition={
            "channels": ["DAPI"],
            "z_stack": {"planes": 3, "step_um": 0.5, "direction": "from_center"},
        },
        focus_gate={"mode": AutofocusMode.FOCUS_LOCK},
    )

    executor.execute_with_config(
        imaging_config=protocol,
        output_path="/tmp",
        cancel_token=MagicMock(),
        round_index=0,
        resume_fov_index=0,
        experiment_id="round_000",
    )

    assert multipoint.update_config.call_args_list[0].kwargs["zstack.use_piezo"] is True


def test_focus_lock_run_monitor_writes_qc_artifacts_and_warning_commands(tmp_path):
    event_bus = EventBus()
    event_bus.start()
    warnings = []
    event_bus.subscribe(AddWarningCommand, warnings.append)

    monitor = _FocusLockRunMonitor(
        event_bus=event_bus,
        output_dir=tmp_path,
        round_index=0,
        round_name="Round 1",
    )
    monitor.start()
    monitor.update_progress(fov_index=2, total_fovs=8)

    try:
        event_bus.publish(
            FocusLockStatusChanged(
                is_locked=True,
                status="locked",
                lock_buffer_fill=4,
                lock_buffer_length=4,
            )
        )
        event_bus.publish(
            FocusLockMetricsUpdated(
                z_error_um=0.05,
                z_position_um=150.0,
                spot_snr=9.5,
                spot_intensity=120.0,
                z_error_rms_um=0.08,
                drift_rate_um_per_s=0.02,
                is_good_reading=True,
                correlation=0.93,
                spot_offset_px=1.0,
                piezo_delta_um=0.3,
                lock_buffer_fill=4,
                lock_buffer_length=4,
                lock_quality=0.95,
            )
        )
        event_bus.publish(FocusLockWarning(warning_type="snr_low", message="Spot SNR below threshold"))
        event_bus.publish(
            FocusLockStatusChanged(
                is_locked=False,
                status="searching",
                lock_buffer_fill=0,
                lock_buffer_length=4,
            )
        )
        event_bus.publish(
            FocusLockPiezoLimitCritical(
                direction="high",
                position_um=295.0,
                limit_um=300.0,
                margin_um=10.0,
            )
        )
        event_bus.drain()
    finally:
        monitor.stop()
        event_bus.stop()

    summary_path = Path(tmp_path) / "focus_lock_summary.json"
    timeseries_path = Path(tmp_path) / "focus_lock_timeseries.csv"

    assert summary_path.exists()
    assert timeseries_path.exists()

    summary = json.loads(summary_path.read_text())
    assert summary["status_transition_counts"]["searching"] >= 1
    assert summary["warning_counts"]["snr_low"] >= 1
    assert summary["max_abs_z_error_um"] == 0.05

    with timeseries_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert any(row["event_type"] == "warning" for row in rows)
    assert any(row["event_type"] == "status" and row["status"] == "searching" for row in rows)
    assert any(cmd.category == "FOCUS" and cmd.fov_index == 2 for cmd in warnings)


def test_readback_handshake_returns_protocol():
    """Verify request_gui_readback publishes request and receives response."""
    event_bus = EventBus()
    event_bus.start()
    multipoint = MagicMock()
    multipoint._autofocus_executor = None

    executor = ImagingExecutor(
        event_bus=event_bus,
        multipoint_controller=multipoint,
        channel_config_manager=None,
    )

    gui_protocol = ImagingProtocol(channels=["Fluorescence 488 nm Ex"])
    requests_received = []

    def simulate_widget(request: RequestImagingProtocolReadback):
        requests_received.append(request.request_id)
        event_bus.publish(ImagingProtocolReadbackReady(
            request_id=request.request_id,
            protocol=gui_protocol,
        ))

    event_bus.subscribe(RequestImagingProtocolReadback, simulate_widget)

    try:
        cancel_token = CancelToken()
        result = executor.request_gui_readback(cancel_token, timeout_s=5.0)
        assert result is not None
        assert result.get_channel_names() == ["Fluorescence 488 nm Ex"]
        assert len(requests_received) == 1
    finally:
        executor.shutdown()
        event_bus.stop()


def test_readback_returns_none_on_timeout():
    """Verify request_gui_readback returns None when no response arrives."""
    event_bus = EventBus()
    event_bus.start()
    multipoint = MagicMock()
    multipoint._autofocus_executor = None

    executor = ImagingExecutor(
        event_bus=event_bus,
        multipoint_controller=multipoint,
        channel_config_manager=None,
    )

    try:
        cancel_token = CancelToken()
        result = executor.request_gui_readback(cancel_token, timeout_s=0.1)
        assert result is None
    finally:
        executor.shutdown()
        event_bus.stop()


def test_readback_ignores_stale_request_id():
    """Verify readback handler ignores responses with wrong request_id."""
    event_bus = EventBus()
    event_bus.start()
    multipoint = MagicMock()
    multipoint._autofocus_executor = None

    executor = ImagingExecutor(
        event_bus=event_bus,
        multipoint_controller=multipoint,
        channel_config_manager=None,
    )

    # Simulate a response with a wrong request_id arriving
    def send_wrong_response(request: RequestImagingProtocolReadback):
        event_bus.publish(ImagingProtocolReadbackReady(
            request_id="wrong_id",
            protocol=ImagingProtocol(channels=["DAPI"]),
        ))

    event_bus.subscribe(RequestImagingProtocolReadback, send_wrong_response)

    try:
        cancel_token = CancelToken()
        result = executor.request_gui_readback(cancel_token, timeout_s=0.3)
        assert result is None  # Should time out because IDs don't match
    finally:
        executor.shutdown()
        event_bus.stop()


# ---------------------------------------------------------------------------
# Acquisition completion watchdog (bug H3)
#
# NOTE: pytest-timeout is not installed. Each watchdog test that could hang
# (if the fix regressed) injects a tiny stall bound and arms an in-test
# threading.Timer that cancels the CancelToken as a self-terminating safety
# limit, so check_point() raises and the loop cannot block CI forever. The
# whole file should still be run under an OS-level `timeout` wrapper as an
# outer guard.
# ---------------------------------------------------------------------------


def _make_stalling_executor():
    """Build an executor whose acquisition starts but never publishes finished.

    event_bus is a MagicMock, so no events are dispatched to
    _on_acquisition_finished -- the acquisition_complete event is never set,
    producing a permanent stall that only the watchdog can break.
    """
    event_bus = MagicMock()
    multipoint = MagicMock()
    multipoint._autofocus_executor = None
    multipoint.run_acquisition = MagicMock(return_value=True)

    executor = ImagingExecutor(
        event_bus=event_bus,
        multipoint_controller=multipoint,
        channel_config_manager=None,
    )
    return executor, multipoint


def test_execute_with_config_watchdog_aborts_when_finished_never_published(tmp_path):
    """Watchdog trips when AcquisitionFinished never arrives: aborts + returns False."""
    executor, multipoint = _make_stalling_executor()
    executor._watchdog_stall_timeout_override_s = 0.2

    token = CancelToken()
    # Self-terminating guard: if the watchdog is broken, this cancels the token
    # so check_point() raises and the test terminates instead of hanging.
    safety = threading.Timer(10.0, lambda: token.cancel("test safety"))
    safety.start()
    try:
        t0 = time.monotonic()
        result = executor.execute_with_config(
            imaging_config=ImagingProtocol(channels=["DAPI"]),
            output_path=str(tmp_path),
            cancel_token=token,
            round_index=0,
            experiment_id="round_000",
        )
        elapsed = time.monotonic() - t0
    finally:
        safety.cancel()

    assert result is False
    assert elapsed < 5.0
    multipoint.request_abort_aquisition.assert_called_once()
    assert "watchdog" in (executor.last_error or "").lower()


def test_execute_with_config_returns_true_when_finished_published(tmp_path):
    """Happy path over a REAL EventBus: watchdog does NOT fire; returns True."""
    event_bus = EventBus()
    event_bus.start()
    try:
        multipoint = MagicMock()
        multipoint._autofocus_executor = None

        def _run(**kwargs):
            event_bus.publish(
                AcquisitionFinished(
                    success=True,
                    experiment_id="round_000",
                    error=None,
                )
            )
            return True

        multipoint.run_acquisition = MagicMock(side_effect=_run)

        executor = ImagingExecutor(
            event_bus=event_bus,
            multipoint_controller=multipoint,
            channel_config_manager=None,
        )

        try:
            result = executor.execute_with_config(
                imaging_config=ImagingProtocol(channels=["DAPI"]),
                output_path=str(tmp_path),
                cancel_token=CancelToken(),
                round_index=0,
                experiment_id="round_000",
            )
        finally:
            executor.shutdown()

        assert result is True
        multipoint.request_abort_aquisition.assert_not_called()
    finally:
        event_bus.stop()


def test_watchdog_no_flakiness_under_repetition(tmp_path):
    """Stress: repeatedly trip the watchdog; every run aborts once within bound."""
    N = 50
    overall_start = time.monotonic()
    for i in range(N):
        executor, multipoint = _make_stalling_executor()
        executor._watchdog_stall_timeout_override_s = 0.05

        token = CancelToken()
        safety = threading.Timer(5.0, lambda: token.cancel("test safety"))
        safety.start()
        try:
            t0 = time.monotonic()
            result = executor.execute_with_config(
                imaging_config=ImagingProtocol(channels=["DAPI"]),
                output_path=str(tmp_path),
                cancel_token=token,
                round_index=0,
                experiment_id="round_000",
            )
            elapsed = time.monotonic() - t0
        finally:
            safety.cancel()

        assert result is False, f"iteration {i} did not return False"
        multipoint.request_abort_aquisition.assert_called_once()
        assert elapsed < 2.0, f"iteration {i} took {elapsed:.2f}s"

    total = time.monotonic() - overall_start
    assert total < 60.0

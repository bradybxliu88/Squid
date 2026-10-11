"""Per-channel z-offset handling in MultiPointWorker (robustness half of upstream 6919470e).

The fork applies a channel's z_offset on the piezo, ungated (orchestrator protocol overrides rely on that), and never
on stage z. These tests cover delta tracking from the piezo's returned (clamped) position, undo in try/finally, the
settle delay, and the warning when no piezo exists.
"""

from __future__ import annotations

import logging
import math
import threading
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from _def import MULTIPOINT_PIEZO_DELAY_MS, TriggerMode
from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker
from squid.core.events import AutofocusMode

BASELINE_UM = 150.0


class _FakePiezoService:
    """Clamps to its range and returns the resulting position, like PiezoService."""

    def __init__(self, position_um: float = BASELINE_UM, range_um: tuple = (0.0, 300.0)) -> None:
        self.position_um = position_um
        self.range_um = range_um
        self.relative_moves: List[float] = []
        self.fail_on_move: Optional[int] = None  # index of the relative move that raises

    def get_position(self) -> float:
        return self.position_um

    def move_relative(self, delta_um: float) -> float:
        if self.fail_on_move is not None and len(self.relative_moves) == self.fail_on_move:
            self.fail_on_move = None
            raise RuntimeError("piezo move failed")
        self.relative_moves.append(delta_um)
        lo, hi = self.range_um
        self.position_um = max(lo, min(hi, self.position_um + delta_um))
        return self.position_um


def _channel(name: str, z_offset: Optional[float]) -> SimpleNamespace:
    return SimpleNamespace(name=name, z_offset=z_offset)


def _build_worker(
    piezo: Optional[_FakePiezoService],
    channels: list,
    *,
    nz: int = 1,
    trigger_mode: TriggerMode = TriggerMode.HARDWARE,
) -> MultiPointWorker:
    worker = MultiPointWorker.__new__(MultiPointWorker)
    worker._log = logging.getLogger("test.multi_point_worker.z_offset")
    worker._piezo_service = piezo
    worker._position_controller = MagicMock()  # stage z must never move for an offset
    worker._trigger_mode = trigger_mode
    worker._current_z_offset_um = 0.0
    worker.selected_configurations = channels
    worker.NZ = nz
    worker._sleep = MagicMock()

    # Just enough state for _acquire_channel_first / _acquire_z_first.
    worker.autofocus_mode = AutofocusMode.NONE
    worker.Nt = 1
    worker.time_point = 0
    worker.total_scans = 1
    worker._last_time_point_z_pos = {}
    worker._stage_service = SimpleNamespace(get_position=lambda: SimpleNamespace(x_mm=0.0, y_mm=0.0, z_mm=1.0))
    worker._timing = MagicMock()
    worker._progress_tracker = MagicMock()
    worker._abort_requested = threading.Event()
    worker._save_laser_af_characterization = lambda *args: None
    worker.update_coordinates_dataframe = lambda *args, **kwargs: None

    # Record where the piezo is at each capture and at each z move.
    worker.events = []

    def _capture(config, *args, **kwargs):
        worker.events.append(("capture", config.name, piezo.position_um if piezo else None))

    worker.acquire_camera_image = _capture
    worker.move_z_for_stack = lambda: worker.events.append(("z_step", piezo.position_um if piezo else None))
    worker.move_z_back_after_stack = lambda: worker.events.append(
        ("stack_return", piezo.position_um if piezo else None)
    )
    worker.prepare_z_stack = lambda: None
    return worker


def test_delta_tracking_moves_only_the_difference_between_channels():
    piezo = _FakePiezoService()
    channels = [_channel("A", 2.0), _channel("B", 2.0), _channel("C", -1.0), _channel("D", 0.0)]
    worker = _build_worker(piezo, channels)

    worker._acquire_channel_first("R0", "/tmp", fov=0, fov_id=None, attempt=1)

    assert worker.events == [
        ("capture", "A", BASELINE_UM + 2.0),
        ("capture", "B", BASELINE_UM + 2.0),
        ("capture", "C", BASELINE_UM - 1.0),
        ("capture", "D", BASELINE_UM),
    ]
    assert piezo.relative_moves == pytest.approx([2.0, -3.0, 1.0])
    assert piezo.position_um == BASELINE_UM
    assert worker._current_z_offset_um == 0.0
    worker._position_controller.move_z_relative.assert_not_called()


def test_offset_is_undone_after_the_last_channel_and_before_each_z_step():
    piezo = _FakePiezoService()
    worker = _build_worker(piezo, [_channel("A", 0.0), _channel("B", 3.0)], nz=2)

    worker._acquire_channel_first("R0", "/tmp", fov=0, fov_id=None, attempt=1)

    assert worker.events == [
        ("capture", "A", BASELINE_UM),
        ("capture", "B", BASELINE_UM + 3.0),
        ("z_step", BASELINE_UM),
        ("capture", "A", BASELINE_UM),
        ("capture", "B", BASELINE_UM + 3.0),
    ]
    assert piezo.position_um == BASELINE_UM


def test_offset_is_undone_when_a_capture_raises():
    piezo = _FakePiezoService()
    worker = _build_worker(piezo, [_channel("A", 4.0), _channel("B", 0.0)])

    def _failing_capture(config, *args, **kwargs):
        raise RuntimeError("camera failed")

    worker.acquire_camera_image = _failing_capture

    with pytest.raises(RuntimeError, match="camera failed"):
        worker._acquire_channel_first("R0", "/tmp", fov=0, fov_id=None, attempt=1)

    assert piezo.position_um == BASELINE_UM
    assert worker._current_z_offset_um == 0.0


def test_clamped_offset_is_tracked_so_the_undo_returns_exactly_to_baseline(caplog):
    piezo = _FakePiezoService(position_um=298.0, range_um=(0.0, 300.0))
    worker = _build_worker(piezo, [_channel("A", 5.0)])

    with caplog.at_level(logging.WARNING):
        worker._apply_channel_z_offset(_channel("A", 5.0))
    assert piezo.position_um == 300.0
    assert worker._current_z_offset_um == pytest.approx(2.0)
    assert any("clamped" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)

    worker._reset_channel_z_offset()
    assert piezo.position_um == pytest.approx(298.0)
    assert worker._current_z_offset_um == pytest.approx(0.0)


def test_non_finite_and_none_offsets_count_as_zero():
    piezo = _FakePiezoService()
    worker = _build_worker(piezo, [])

    worker._apply_channel_z_offset(_channel("A", 2.0))
    worker._apply_channel_z_offset(_channel("B", math.nan))  # back to the un-offset z
    assert piezo.position_um == BASELINE_UM
    worker._apply_channel_z_offset(_channel("C", None))
    worker._apply_channel_z_offset(_channel("D", math.inf))
    assert piezo.relative_moves == pytest.approx([2.0, -2.0])


def test_failed_undo_keeps_the_outstanding_offset_for_the_next_reset(caplog):
    piezo = _FakePiezoService()
    worker = _build_worker(piezo, [])
    worker._apply_channel_z_offset(_channel("A", 3.0))
    piezo.fail_on_move = 1

    with caplog.at_level(logging.ERROR):
        worker._reset_channel_z_offset()  # must not raise
    assert worker._current_z_offset_um == pytest.approx(3.0)
    assert any("Failed to undo channel z-offset" in r.getMessage() for r in caplog.records)

    worker._reset_channel_z_offset()
    assert piezo.position_um == BASELINE_UM
    assert worker._current_z_offset_um == 0.0


def test_settle_delay_only_with_software_trigger():
    piezo = _FakePiezoService()
    worker = _build_worker(piezo, [], trigger_mode=TriggerMode.SOFTWARE)
    worker._apply_channel_z_offset(_channel("A", 1.0))
    worker._sleep.assert_called_once_with(MULTIPOINT_PIEZO_DELAY_MS / 1000)

    worker = _build_worker(_FakePiezoService(), [], trigger_mode=TriggerMode.HARDWARE)
    worker._apply_channel_z_offset(_channel("A", 1.0))
    worker._sleep.assert_not_called()


def test_z_first_undoes_offset_before_each_z_step_and_stack_return():
    piezo = _FakePiezoService()
    worker = _build_worker(piezo, [_channel("A", 2.0), _channel("B", -1.0)], nz=2)

    worker._acquire_z_first("R0", "/tmp", fov=0, fov_id=None, attempt=1)

    assert worker.events == [
        ("capture", "A", BASELINE_UM + 2.0),
        ("z_step", BASELINE_UM),
        ("capture", "A", BASELINE_UM + 2.0),
        ("stack_return", BASELINE_UM),
        ("capture", "B", BASELINE_UM - 1.0),
        ("z_step", BASELINE_UM),
        ("capture", "B", BASELINE_UM - 1.0),
    ]
    assert piezo.position_um == BASELINE_UM


def test_z_first_single_z_moves_only_deltas_between_channels():
    piezo = _FakePiezoService()
    worker = _build_worker(piezo, [_channel("A", 2.0), _channel("B", 3.0)])

    worker._acquire_z_first("R0", "/tmp", fov=0, fov_id=None, attempt=1)

    assert piezo.relative_moves == pytest.approx([2.0, 1.0, -3.0])
    assert piezo.position_um == BASELINE_UM


def test_abort_handler_undoes_a_stranded_offset(tmp_path):
    piezo = _FakePiezoService()
    worker = _build_worker(piezo, [])
    worker.coordinates_pd = MagicMock()
    worker._peripheral_service = MagicMock()
    worker._wait_for_outstanding_callback_images = lambda: None
    worker._apply_channel_z_offset(_channel("A", 5.0))

    worker.handle_acquisition_abort(str(tmp_path))

    assert piezo.position_um == BASELINE_UM
    assert worker._current_z_offset_um == 0.0


def test_without_piezo_offsets_are_skipped_and_a_warning_is_logged_once(caplog):
    worker = _build_worker(None, [_channel("A", 2.0), _channel("B", 0.0)])

    with caplog.at_level(logging.DEBUG):
        worker._log_channel_z_offsets()
        worker._acquire_channel_first("R0", "/tmp", fov=0, fov_id=None, attempt=1)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "No piezo" in warnings[0] and "A: +2.00 um" in warnings[0]
    assert not any("via piezo" in r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert worker._current_z_offset_um == 0.0
    worker._position_controller.move_z_relative.assert_not_called()


def test_start_summary_with_piezo_lists_applied_and_non_finite_offsets(caplog):
    worker = _build_worker(_FakePiezoService(), [_channel("A", 1.5), _channel("B", math.nan), _channel("C", 0.0)])

    with caplog.at_level(logging.INFO):
        worker._log_channel_z_offsets()

    messages = {r.levelno: r.getMessage() for r in caplog.records}
    assert "will be applied via piezo" in messages[logging.INFO] and "A: +1.50 um" in messages[logging.INFO]
    assert "non-finite" in messages[logging.WARNING] and "B" in messages[logging.WARNING]

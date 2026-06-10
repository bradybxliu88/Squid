"""Tests for the rebuilt ImageSaver (ports upstream #535).

Exercises the per-recording metadata sidecar (frames.csv), channel-aware
filenames, and the default-channel fallback. The saver thread writes files
directly, so no Qt event loop is needed beyond a live QApplication (qapp).
"""

import csv
import os
from dataclasses import dataclass

import numpy as np
import pytest

from squid.ui.image_saver import ImageSaver


@dataclass
class _FakeChannel:
    name: str = "Fluorescence 488 nm Ex"
    exposure_time: float = 12.5
    analog_gain: float = 1.0
    illumination_intensity: float = 30.0


@pytest.fixture
def saver(qapp):
    s = ImageSaver()
    yield s
    s.close()


def _read_csv(experiment_dir):
    with open(os.path.join(experiment_dir, "frames.csv"), newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_records_frames_with_channel_named_files_and_csv(saver, tmp_path):
    saver.set_base_path(str(tmp_path))
    saver.set_channel_provider(lambda: _FakeChannel())
    saver.start_new_experiment("rec", add_timestamp=False)

    saver.enqueue(np.zeros((4, 4), dtype=np.uint16), frame_id=1, timestamp=1000.0)
    saver.enqueue(np.zeros((4, 4), dtype=np.uint16), frame_id=2, timestamp=1001.0)
    saver.stop_experiment()

    experiment_dir = tmp_path / "rec"
    files = sorted(os.listdir(experiment_dir / "0"))
    # Channel name spaces are sanitized to underscores; uint16 → tiff.
    assert files == ["0_Fluorescence_488_nm_Ex.tiff", "1_Fluorescence_488_nm_Ex.tiff"]

    rows = _read_csv(experiment_dir)
    assert rows[0] == ["frame_id", "timestamp_iso", "channel", "exposure_ms", "gain", "illumination_intensity", "file"]
    assert len(rows) == 3  # header + 2 frames
    assert rows[1][0] == "1" and rows[1][2] == "Fluorescence 488 nm Ex"
    assert rows[1][3] == "12.5" and rows[1][4] == "1.0" and rows[1][5] == "30.0"
    # The recorded path is relative to the experiment dir and points at a real file.
    assert rows[1][6] == os.path.join("0", "0_Fluorescence_488_nm_Ex.tiff")
    assert (experiment_dir / rows[1][6]).exists()


def test_no_channel_provider_tags_frames_live(saver, tmp_path):
    saver.set_base_path(str(tmp_path))
    saver.start_new_experiment("rec", add_timestamp=False)

    saver.enqueue(np.zeros((2, 2), dtype=np.uint16), frame_id=1, timestamp=5.0)
    saver.stop_experiment()

    assert (tmp_path / "rec" / "0" / "0_live.tiff").exists()
    rows = _read_csv(tmp_path / "rec")
    assert rows[1][2] == "live"


def test_stop_experiment_is_idempotent_and_clears_id(saver, tmp_path):
    saver.set_base_path(str(tmp_path))
    saver.start_new_experiment("rec", add_timestamp=False)
    saver.enqueue(np.zeros((2, 2), dtype=np.uint16), frame_id=1, timestamp=5.0)
    saver.stop_experiment()
    assert saver.experiment_ID == ""
    # Second call must be a harmless no-op.
    saver.stop_experiment()
    assert saver.experiment_ID == ""


def test_start_new_experiment_resets_counter(saver, tmp_path):
    saver.set_base_path(str(tmp_path))
    saver.set_channel_provider(lambda: _FakeChannel(name="ch"))
    saver.start_new_experiment("a", add_timestamp=False)
    saver.enqueue(np.zeros((2, 2), dtype=np.uint16), frame_id=1, timestamp=5.0)
    saver.stop_experiment()

    saver.start_new_experiment("b", add_timestamp=False)
    saver.enqueue(np.zeros((2, 2), dtype=np.uint16), frame_id=1, timestamp=6.0)
    saver.stop_experiment()
    # Counter reset: second recording's first file is file_id 0 again.
    assert (tmp_path / "b" / "0" / "0_ch.tiff").exists()

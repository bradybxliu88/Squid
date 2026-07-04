"""Unit coverage for the B1.1 filesystem assertion helpers.

The helpers are pure functions over the experiment folder, so they can be
exercised without the (slow) simulated backend. This locks in their behavior
before the writer phases (B1.4/B1.5) rely on them as the resume oracle.
"""

import json

import pytest

from tests.e2e.harness.e2e_assertions import (
    assert_checkpoint_valid,
    assert_no_prior_data_loss,
    assert_no_duplicate_coordinates,
)
from squid.backend.controllers.orchestrator.checkpoint import save_checkpoint, CHECKPOINT_FILENAME
from squid.backend.controllers.orchestrator.state import Checkpoint, CheckpointCorruptError

pytestmark = pytest.mark.unit


def _checkpoint(round_index=1):
    from datetime import datetime

    return Checkpoint(
        protocol_name="p",
        protocol_version="1.0",
        experiment_id="exp",
        experiment_path="/tmp/exp",
        round_index=round_index,
        created_at=datetime.now(),
    )


def _write_coords(path, rows):
    header = "region,x (mm),y (mm),z (mm)\n"
    body = "".join(f"{r[0]},{r[1]},{r[2]},{r[3]}\n" for r in rows)
    path.write_text(header + body)


# --- assert_checkpoint_valid ---------------------------------------------


def test_checkpoint_valid_happy(tmp_path):
    save_checkpoint(_checkpoint(round_index=3), str(tmp_path))
    loaded = assert_checkpoint_valid(str(tmp_path))
    assert loaded.round_index == 3


def test_checkpoint_valid_recovers_from_bak(tmp_path):
    save_checkpoint(_checkpoint(round_index=2), str(tmp_path))
    save_checkpoint(_checkpoint(round_index=4), str(tmp_path))  # .bak now holds round 2
    (tmp_path / CHECKPOINT_FILENAME).write_text("torn{{")
    # Recoverable via .bak -> still considered valid.
    loaded = assert_checkpoint_valid(str(tmp_path))
    assert loaded.round_index == 2


def test_checkpoint_valid_raises_when_unrecoverable(tmp_path):
    save_checkpoint(_checkpoint(), str(tmp_path))
    (tmp_path / CHECKPOINT_FILENAME).write_text("torn")
    with pytest.raises(CheckpointCorruptError):
        assert_checkpoint_valid(str(tmp_path))


def test_checkpoint_valid_raises_when_absent(tmp_path):
    with pytest.raises(AssertionError):
        assert_checkpoint_valid(str(tmp_path))


# --- assert_no_duplicate_coordinates -------------------------------------


def test_no_duplicate_coordinates_passes(tmp_path):
    rd = tmp_path / "round_000"
    rd.mkdir()
    _write_coords(rd / "coordinates.csv", [("r1", 1.0, 2.0, 0.0), ("r1", 3.0, 4.0, 0.0)])
    assert_no_duplicate_coordinates(str(tmp_path))  # no raise


def test_no_duplicate_coordinates_detects_dup(tmp_path):
    rd = tmp_path / "round_000"
    rd.mkdir()
    _write_coords(rd / "coordinates.csv", [("r1", 1.0, 2.0, 0.0), ("r1", 1.0, 2.0, 0.0)])
    with pytest.raises(AssertionError, match="Duplicate"):
        assert_no_duplicate_coordinates(str(tmp_path))


# --- assert_no_prior_data_loss -------------------------------------------


def _make_round(root, name, n_images):
    rd = root / name / "region_1"
    rd.mkdir(parents=True)
    for i in range(n_images):
        (rd / f"img_{i}.tiff").write_bytes(b"\x00")
    return rd


def test_no_prior_data_loss_passes(tmp_path):
    _make_round(tmp_path, "round_000", 4)
    _make_round(tmp_path, "round_001", 4)
    assert_no_prior_data_loss(str(tmp_path), min_round_dirs=2, min_image_files=8)


def test_no_prior_data_loss_flags_empty_round(tmp_path):
    _make_round(tmp_path, "round_000", 2)
    (tmp_path / "round_001").mkdir()  # empty -> data loss
    with pytest.raises(AssertionError, match="empty"):
        assert_no_prior_data_loss(str(tmp_path))


def test_no_prior_data_loss_flags_missing_rounds(tmp_path):
    _make_round(tmp_path, "round_000", 2)
    with pytest.raises(AssertionError, match="round dirs"):
        assert_no_prior_data_loss(str(tmp_path), min_round_dirs=3)


def test_no_prior_data_loss_flags_missing_images(tmp_path):
    _make_round(tmp_path, "round_000", 1)
    with pytest.raises(AssertionError, match="image files"):
        assert_no_prior_data_loss(str(tmp_path), min_image_files=5)

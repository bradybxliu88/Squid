"""Unit tests for CheckpointManager."""

import pytest
import tempfile
import os
import json
from datetime import datetime

from squid.backend.controllers.orchestrator.checkpoint import (
    CheckpointManager,
    CHECKPOINT_FILENAME,
    save_checkpoint,
    load_checkpoint,
)
from squid.backend.controllers.orchestrator.state import Checkpoint, CheckpointCorruptError


@pytest.fixture
def checkpoint_manager():
    """Create a CheckpointManager for testing."""
    return CheckpointManager()


@pytest.fixture
def sample_checkpoint():
    """Create a sample checkpoint for testing."""
    return Checkpoint(
        protocol_name="test_protocol",
        protocol_version="1.0.0",
        experiment_id="test_experiment_001",
        experiment_path="/tmp/experiments/test_001",
        round_index=2,
        step_index=1,
        imaging_fov_index=5,
        current_attempt=3,
        elapsed_seconds=123.4,
        paused_seconds=5.6,
        effective_run_seconds=117.8,
        created_at=datetime.now(),
    )


class TestCheckpointCreation:
    """Tests for checkpoint creation."""

    def test_create_checkpoint(self, checkpoint_manager):
        """Test creating a checkpoint from parameters."""
        checkpoint = checkpoint_manager.create_checkpoint(
            protocol_name="my_protocol",
            protocol_version="2.0.0",
            experiment_id="exp_123",
            experiment_path="/data/experiments/exp_123",
            round_index=3,
            step_index=2,
            imaging_fov_index=10,
        )

        assert checkpoint.protocol_name == "my_protocol"
        assert checkpoint.protocol_version == "2.0.0"
        assert checkpoint.experiment_id == "exp_123"
        assert checkpoint.experiment_path == "/data/experiments/exp_123"
        assert checkpoint.round_index == 3
        assert checkpoint.step_index == 2
        assert checkpoint.imaging_fov_index == 10
        assert checkpoint.current_attempt == 1
        assert checkpoint.elapsed_seconds == 0.0
        assert checkpoint.created_at is not None

    def test_create_checkpoint_with_defaults(self, checkpoint_manager):
        """Test creating a checkpoint with default values."""
        checkpoint = checkpoint_manager.create_checkpoint(
            protocol_name="minimal_protocol",
            protocol_version="1.0",
            experiment_id="exp_minimal",
            experiment_path="/tmp/minimal",
            round_index=0,
        )

        assert checkpoint.step_index == 0
        assert checkpoint.imaging_fov_index == 0


class TestCheckpointPersistence:
    """Tests for checkpoint save/load functionality."""

    def test_save_and_load_checkpoint(
        self, checkpoint_manager, sample_checkpoint
    ):
        """Test saving and loading a checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save checkpoint
            checkpoint_manager.save(sample_checkpoint, tmpdir)

            # Verify file exists
            checkpoint_file = os.path.join(tmpdir, "checkpoint.json")
            assert os.path.exists(checkpoint_file)

            # Load checkpoint
            loaded = checkpoint_manager.load(tmpdir)

            assert loaded is not None
            assert loaded.protocol_name == sample_checkpoint.protocol_name
            assert loaded.protocol_version == sample_checkpoint.protocol_version
            assert loaded.experiment_id == sample_checkpoint.experiment_id
            assert loaded.round_index == sample_checkpoint.round_index
            assert loaded.step_index == sample_checkpoint.step_index
            assert loaded.imaging_fov_index == sample_checkpoint.imaging_fov_index
            assert loaded.current_attempt == sample_checkpoint.current_attempt
            assert loaded.elapsed_seconds == sample_checkpoint.elapsed_seconds
            assert loaded.paused_seconds == sample_checkpoint.paused_seconds
            assert loaded.effective_run_seconds == sample_checkpoint.effective_run_seconds

    def test_load_nonexistent_checkpoint(self, checkpoint_manager):
        """Test loading from a path with no checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loaded = checkpoint_manager.load(tmpdir)
            assert loaded is None

    def test_clear_checkpoint(self, checkpoint_manager, sample_checkpoint):
        """Test clearing a checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save checkpoint
            checkpoint_manager.save(sample_checkpoint, tmpdir)
            checkpoint_file = os.path.join(tmpdir, "checkpoint.json")
            assert os.path.exists(checkpoint_file)

            # Clear checkpoint
            checkpoint_manager.clear(tmpdir)

            # Verify file is gone
            assert not os.path.exists(checkpoint_file)

    def test_clear_nonexistent_checkpoint_no_error(self, checkpoint_manager):
        """Test that clearing nonexistent checkpoint doesn't error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Should not raise
            checkpoint_manager.clear(tmpdir)


class TestCheckpointFileFormat:
    """Tests for checkpoint file format."""

    def test_checkpoint_file_is_valid_json(
        self, checkpoint_manager, sample_checkpoint
    ):
        """Test that saved checkpoint is valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_manager.save(sample_checkpoint, tmpdir)

            checkpoint_file = os.path.join(tmpdir, "checkpoint.json")
            with open(checkpoint_file, "r") as f:
                data = json.load(f)

            assert "protocol_name" in data
            assert "round_index" in data
            assert "created_at" in data

    def test_checkpoint_contains_all_fields(
        self, checkpoint_manager, sample_checkpoint
    ):
        """Test that checkpoint file contains all expected fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_manager.save(sample_checkpoint, tmpdir)

            checkpoint_file = os.path.join(tmpdir, "checkpoint.json")
            with open(checkpoint_file, "r") as f:
                data = json.load(f)

            expected_fields = [
                "protocol_name",
                "protocol_version",
                "experiment_id",
                "experiment_path",
                "round_index",
                "step_index",
                "imaging_fov_index",
                "current_attempt",
                "elapsed_seconds",
                "paused_seconds",
                "effective_run_seconds",
                "created_at",
            ]

            for field in expected_fields:
                assert field in data, f"Missing field: {field}"


class TestCheckpointCorruption:
    """B1.0: load must distinguish corrupt-vs-missing and recover from .bak."""

    def _make(self, round_index):
        return Checkpoint(
            protocol_name="p",
            protocol_version="1.0",
            experiment_id="exp",
            experiment_path="/tmp/exp",
            round_index=round_index,
            created_at=datetime.now(),
        )

    def test_atomic_save_leaves_no_temp_files(self, sample_checkpoint):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_checkpoint(sample_checkpoint, tmpdir)
            leftovers = [f for f in os.listdir(tmpdir) if f.endswith(".tmp")]
            assert leftovers == [], f"temp files not cleaned: {leftovers}"

    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert load_checkpoint(tmpdir) is None

    def test_corrupt_without_backup_raises(self, sample_checkpoint):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_checkpoint(sample_checkpoint, tmpdir)
            cp = os.path.join(tmpdir, CHECKPOINT_FILENAME)
            with open(cp, "w") as f:
                f.write("{ this is not valid json")
            with pytest.raises(CheckpointCorruptError):
                load_checkpoint(tmpdir)

    def test_corrupt_recovers_from_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # First save -> primary=A (no .bak yet). Second save -> primary=B, .bak=A.
            save_checkpoint(self._make(round_index=2), tmpdir)
            save_checkpoint(self._make(round_index=5), tmpdir)
            cp = os.path.join(tmpdir, CHECKPOINT_FILENAME)
            assert os.path.exists(cp + ".bak")

            # Corrupt the primary; loader should fall back to the last-good .bak (round 2).
            with open(cp, "w") as f:
                f.write("not json at all")
            recovered = load_checkpoint(tmpdir)
            assert recovered is not None
            assert recovered.round_index == 2

    def test_corrupt_primary_and_backup_raises(self, sample_checkpoint):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_checkpoint(sample_checkpoint, tmpdir)
            save_checkpoint(sample_checkpoint, tmpdir)  # creates .bak
            cp = os.path.join(tmpdir, CHECKPOINT_FILENAME)
            with open(cp, "w") as f:
                f.write("garbage")
            with open(cp + ".bak", "w") as f:
                f.write("also garbage")
            with pytest.raises(CheckpointCorruptError):
                load_checkpoint(tmpdir)

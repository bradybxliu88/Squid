"""
Checkpoint management for experiment recovery.

Saves and loads checkpoints to enable resuming experiments after
pauses, crashes, or system restarts.
"""

import json
import os
from datetime import datetime
from typing import Optional

import squid.core.logging
from squid.core.utils.atomic_io import atomic_write_json
from squid.backend.controllers.orchestrator.state import Checkpoint, CheckpointCorruptError

# Re-exported so callers using ``checkpoint as ckpt`` can catch
# ``ckpt.CheckpointCorruptError`` without importing state directly.
__all__ = [
    "CHECKPOINT_FILENAME",
    "Checkpoint",
    "CheckpointCorruptError",
    "CheckpointManager",
    "save_checkpoint",
    "load_checkpoint",
    "clear_checkpoint",
    "create_checkpoint",
]

_log = squid.core.logging.get_logger(__name__)

CHECKPOINT_FILENAME = "checkpoint.json"


def save_checkpoint(checkpoint: Checkpoint, experiment_path: str) -> str:
    """Save a checkpoint to the experiment directory.

    Args:
        checkpoint: Checkpoint to save
        experiment_path: Path to experiment directory

    Returns:
        Path to the saved checkpoint file
    """
    os.makedirs(experiment_path, exist_ok=True)
    checkpoint_path = os.path.join(experiment_path, CHECKPOINT_FILENAME)

    data = {
        "protocol_name": checkpoint.protocol_name,
        "protocol_version": checkpoint.protocol_version,
        "experiment_id": checkpoint.experiment_id,
        "experiment_path": checkpoint.experiment_path,
        "round_index": checkpoint.round_index,
        "step_index": checkpoint.step_index,
        "imaging_fov_index": checkpoint.imaging_fov_index,
        "imaging_z_index": checkpoint.imaging_z_index,
        "imaging_channel_index": checkpoint.imaging_channel_index,
        "created_at": checkpoint.created_at.isoformat(),
        "paused_at": checkpoint.paused_at.isoformat() if checkpoint.paused_at else None,
        "metadata": checkpoint.metadata,
        "current_attempt": checkpoint.current_attempt,
        "elapsed_seconds": checkpoint.elapsed_seconds,
        "paused_seconds": checkpoint.paused_seconds,
        "effective_run_seconds": checkpoint.effective_run_seconds,
    }

    # Atomic write + a .bak of the previous good checkpoint, so a crash mid-write
    # can never destroy the last recoverable checkpoint (the whole point of B1).
    atomic_write_json(checkpoint_path, data, keep_backup=True)

    _log.info(f"Saved checkpoint to {checkpoint_path}")
    return checkpoint_path


def _checkpoint_from_dict(data: dict) -> Checkpoint:
    """Build a Checkpoint from a parsed checkpoint.json dict."""
    return Checkpoint(
        protocol_name=data["protocol_name"],
        protocol_version=data["protocol_version"],
        experiment_id=data["experiment_id"],
        experiment_path=data["experiment_path"],
        round_index=data["round_index"],
        step_index=data.get("step_index", 0),
        imaging_fov_index=data.get("imaging_fov_index", 0),
        imaging_z_index=data.get("imaging_z_index", 0),
        imaging_channel_index=data.get("imaging_channel_index", 0),
        created_at=datetime.fromisoformat(data["created_at"]),
        paused_at=datetime.fromisoformat(data["paused_at"]) if data.get("paused_at") else None,
        metadata=data.get("metadata", {}),
        current_attempt=data.get("current_attempt", 1),
        elapsed_seconds=data.get("elapsed_seconds", 0.0),
        paused_seconds=data.get("paused_seconds", 0.0),
        effective_run_seconds=data.get("effective_run_seconds", 0.0),
    )


def _read_checkpoint_file(path: str) -> Checkpoint:
    """Read + parse a single checkpoint file. Raises on missing/corrupt/incomplete."""
    with open(path, "r") as f:
        data = json.load(f)
    return _checkpoint_from_dict(data)


def load_checkpoint(experiment_path: str) -> Optional[Checkpoint]:
    """Load a checkpoint from the experiment directory.

    Distinguishes three cases so a resumed run does not silently start over on a
    torn checkpoint:
      - no checkpoint file present -> return ``None`` (nothing to resume);
      - checkpoint present but corrupt, with a readable ``.bak`` -> recover from
        the backup (logged);
      - checkpoint present but corrupt with no usable backup -> raise
        ``CheckpointCorruptError``.

    Args:
        experiment_path: Path to experiment directory

    Returns:
        Checkpoint if found and readable, None if no checkpoint exists.

    Raises:
        CheckpointCorruptError: checkpoint exists but neither it nor its backup
            can be parsed.
    """
    checkpoint_path = os.path.join(experiment_path, CHECKPOINT_FILENAME)

    if not os.path.exists(checkpoint_path):
        return None

    try:
        checkpoint = _read_checkpoint_file(checkpoint_path)
        _log.info(f"Loaded checkpoint from {checkpoint_path}")
        return checkpoint
    except Exception as primary_err:
        # Present but unparseable/incomplete. Try the last-good backup.
        backup_path = checkpoint_path + ".bak"
        if os.path.exists(backup_path):
            try:
                checkpoint = _read_checkpoint_file(backup_path)
                _log.warning(
                    f"Checkpoint {checkpoint_path} was corrupt ({primary_err}); "
                    f"recovered from backup {backup_path}"
                )
                return checkpoint
            except Exception as backup_err:
                raise CheckpointCorruptError(
                    f"Checkpoint at {checkpoint_path} is corrupt ({primary_err}) and "
                    f"backup {backup_path} is also unreadable ({backup_err})"
                ) from primary_err
        raise CheckpointCorruptError(
            f"Checkpoint at {checkpoint_path} is present but could not be parsed: {primary_err}"
        ) from primary_err


def clear_checkpoint(experiment_path: str) -> bool:
    """Clear the checkpoint for an experiment.

    Called when an experiment completes successfully.

    Args:
        experiment_path: Path to experiment directory

    Returns:
        True if checkpoint was cleared, False if no checkpoint existed
    """
    checkpoint_path = os.path.join(experiment_path, CHECKPOINT_FILENAME)

    if not os.path.exists(checkpoint_path):
        return False

    try:
        os.remove(checkpoint_path)
        _log.info(f"Cleared checkpoint from {checkpoint_path}")
        return True
    except Exception as e:
        _log.warning(f"Failed to clear checkpoint: {e}")
        return False


def create_checkpoint(
    protocol_name: str,
    protocol_version: str,
    experiment_id: str,
    experiment_path: str,
    round_index: int,
    step_index: int = 0,
    imaging_fov_index: int = 0,
    imaging_z_index: int = 0,
    imaging_channel_index: int = 0,
    metadata: Optional[dict] = None,
    current_attempt: int = 1,
    elapsed_seconds: float = 0.0,
    paused_seconds: float = 0.0,
    effective_run_seconds: float = 0.0,
) -> Checkpoint:
    """Create a new checkpoint object.

    Args:
        protocol_name: Name of the protocol
        protocol_version: Version of the protocol
        experiment_id: Experiment identifier
        experiment_path: Path to experiment directory
        round_index: Current round index
        step_index: Step position within round's steps list
        imaging_fov_index: Current imaging FOV index
        imaging_z_index: Current z-plane index
        imaging_channel_index: Current channel index
        metadata: Additional metadata

    Returns:
        New Checkpoint object
    """
    return Checkpoint(
        protocol_name=protocol_name,
        protocol_version=protocol_version,
        experiment_id=experiment_id,
        experiment_path=experiment_path,
        round_index=round_index,
        step_index=step_index,
        imaging_fov_index=imaging_fov_index,
        imaging_z_index=imaging_z_index,
        imaging_channel_index=imaging_channel_index,
        metadata=metadata or {},
        current_attempt=current_attempt,
        elapsed_seconds=elapsed_seconds,
        paused_seconds=paused_seconds,
        effective_run_seconds=effective_run_seconds,
    )


# Backwards compatibility
class CheckpointManager:
    """Thin wrapper providing the old class-based API.

    Delegates to module-level functions.  New code should call the
    functions directly.
    """

    CHECKPOINT_FILENAME = CHECKPOINT_FILENAME

    def __init__(self):
        pass

    def save(self, checkpoint: Checkpoint, experiment_path: str) -> str:
        return save_checkpoint(checkpoint, experiment_path)

    def load(self, experiment_path: str) -> Optional[Checkpoint]:
        return load_checkpoint(experiment_path)

    def clear(self, experiment_path: str) -> bool:
        return clear_checkpoint(experiment_path)

    def exists(self, experiment_path: str) -> bool:
        checkpoint_path = os.path.join(experiment_path, CHECKPOINT_FILENAME)
        return os.path.exists(checkpoint_path)

    def get_checkpoint_path(self, experiment_path: str) -> str:
        return os.path.join(experiment_path, CHECKPOINT_FILENAME)

    def create_checkpoint(self, **kwargs) -> Checkpoint:
        return create_checkpoint(**kwargs)

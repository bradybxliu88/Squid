"""Resume-side durability verification (B1.2a).

On crash-resume the orchestrator trusts a checkpoint ``imaging_fov_index`` that was
recorded at ACQUIRE time, not durable-write time: image saves are asynchronous and
fire-and-forget into a separate JobRunner process, so a FOV below the resume index
may have been marked "complete" in the checkpoint yet never actually flushed to
disk. Before skipping such FOVs, verify their frames really exist on disk and
re-acquire any that don't.

This module is intentionally pure (no backend/hardware deps) and covers the
INDIVIDUAL_IMAGES save mode only -- the mode B1.2 ships resume for first. Its
failure mode is safe: if the filename convention ever drifts, files simply read as
"missing" -> the FOV is re-acquired (overwritten), never silently skipped.
"""

from __future__ import annotations

import glob
import os
from typing import Callable, Optional, Sequence


def fov_frames_present(
    save_dir: str,
    fov_id: str,
    n_z: int,
    channel_names: Sequence[str],
    z_padding: int,
    *,
    min_size: int = 1,
    validate: Optional[Callable[[str], bool]] = None,
) -> bool:
    """Return True iff every expected INDIVIDUAL_IMAGES frame for this FOV is on disk.

    Mirrors the acquisition filename convention exactly --
    ``{fov_id}_{z:0{z_padding}}_{channel_safe}.{ext}`` (see
    ``multi_point_worker._make_file_id`` for attempt==1 and
    ``io.utils_acquisition.get_image_filepath``) -- globbing the stem so the
    uint16(``.tiff``) vs 8-bit(``IMAGE_FORMAT``) extension branch doesn't matter.

    Note: this checks attempt==1 filenames. A FOV that only completed on a retry
    (``_attemptNN_`` names) reads as missing and is re-acquired -- safe, if slightly
    wasteful, and rare.

    Args:
        save_dir: Directory the frames were saved to (CaptureInfo.save_directory).
        fov_id: Stable FOV id ("{region_id}_{index:04d}"), i.e. ``FovTask.fov_id``.
        n_z: Number of z-planes (worker ``NZ``).
        channel_names: Selected channel names (``config.name`` for each).
        z_padding: Zero-pad width for the z index (``FILE_ID_PADDING``).
        min_size: A file must be at least this many bytes to count (rejects 0-byte stubs).
        validate: Optional per-file predicate (e.g. a TIFF-header read) to catch a
            mid-write truncation on the boundary FOV. Off by default.

    Returns:
        True only if, for every (z, channel), at least one matching non-empty file
        exists (and passes ``validate`` when supplied).
    """
    for z in range(max(n_z, 1)):
        file_id = f"{fov_id}_{z:0{z_padding}}"
        for ch in channel_names:
            channel_safe = str(ch).replace(" ", "_")
            stem = f"{file_id}_{channel_safe}"
            matches = [
                m
                for m in glob.glob(os.path.join(save_dir, glob.escape(stem) + ".*"))
                if os.path.isfile(m)
            ]
            ok = any(os.path.getsize(m) >= min_size for m in matches)
            if ok and validate is not None:
                ok = any(validate(m) for m in matches)
            if not ok:
                return False
    return True

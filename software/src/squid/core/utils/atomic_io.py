"""
Atomic JSON file writes.

Factored from the atomic-write patterns already present in the repo
(``multipoint/checkpoint.py`` and ``orchestrator/protocol_editor_model.safe_write``)
so the orchestrator checkpoint, experiment metadata, and multipoint checkpoint
all share one implementation.

Contract: after ``atomic_write_json(path, data)`` returns, ``path`` holds either
the previous complete file or the new complete file -- never a half-written /
torn file -- even if the process dies mid-write. A crash between the temp write
and the rename leaves ``path`` untouched. This is what makes crash-resume safe:
a checkpoint you can load is a checkpoint you can trust.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional, Union

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)

# Test-only fault seam (B1.1 "MID-WRITE" crash-injection point). When set to a
# callable, it fires after the temp file is fully written + fsync'd but BEFORE
# os.replace, letting tests simulate a power loss in the exact window that used
# to corrupt files. Production leaves this None.
# See tests/unit/core/utils/test_atomic_io.py.
_FAULT_BEFORE_REPLACE: Optional[Callable[[], None]] = None


def atomic_write_json(
    path: Union[str, os.PathLike],
    data: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    keep_backup: bool = False,
) -> Optional[str]:
    """Atomically write ``data`` as JSON to ``path``.

    Writes to a temp file in the same directory, flushes + ``os.fsync``-es it,
    then ``os.replace``-s it over ``path`` (atomic on POSIX and Windows). On any
    failure the temp file is removed and ``path`` is left untouched.

    Args:
        path: Destination file path (parent dirs are created).
        data: JSON-serializable object.
        indent: ``json.dump`` indent.
        sort_keys: ``json.dump`` sort_keys. Callers that relied on sorted output
            (e.g. ``experiment_metadata.json``) pass ``True``.
        keep_backup: If True, copy the previous ``path`` to ``path + '.bak'``
            before replacing, so a last-good copy survives a later torn write.

    Returns:
        The backup path if one was written, else ``None``.

    Raises:
        Whatever ``json.dump`` / OS IO raises -- after cleaning up the temp file
        and leaving the original + backup untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    backup_path: Optional[str] = None
    if keep_backup and path.exists() and path.is_file():
        backup_path = str(path) + ".bak"
        try:
            # Copy (not rename) so the primary stays valid until os.replace.
            with open(path, "rb") as src, open(backup_path, "wb") as dst:
                dst.write(src.read())
        except OSError as e:
            # A missing backup is not fatal -- the primary is still protected by
            # the atomic replace below.
            _log.warning(f"Could not write backup {backup_path}: {e}")
            backup_path = None

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix="." + path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, sort_keys=sort_keys)
            f.flush()
            os.fsync(f.fileno())
        if _FAULT_BEFORE_REPLACE is not None:
            _FAULT_BEFORE_REPLACE()
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up the temp file on ANY failure (including injected faults and
        # KeyboardInterrupt). The original + backup are untouched.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return backup_path

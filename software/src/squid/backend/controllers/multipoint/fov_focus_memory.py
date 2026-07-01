"""Per-FOV piezo Z position memory for focus lock persistence across rounds.

When the focus gate mode is FOCUS_LOCK_PER_FOV, the laser autofocus is turned
off during fluidics (to avoid wrestling with unstable signal during flow), then
re-engaged per FOV before imaging. Each FOV's last successfully-locked piezo Z
position is cached so subsequent rounds can seed the piezo near the expected
focal plane — making re-acquisition fast and reliable.

This module provides:
- :class:`FOVFocusMemory` — the in-memory store, thread-safe
- Serialization helpers for persisting the store through checkpoints

Design notes
------------
- The key is ``(region_id, fov_index)``; region_id is a string (well label or
  user region name), fov_index is the FOV ordinal within that region.
- Values are absolute piezo positions in micrometres (``float``). The caller is
  responsible for supplying the piezo's coordinate frame — we don't interpret it.
- :meth:`get_seed` falls back to a configured ``default_z_um`` if a FOV has no
  cached value. This is the round-1 seed: typically the piezo position at the
  moment imaging first begins.
- Updates are atomic and the store is safe to call from worker threads.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

FOVKey = Tuple[str, int]


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


@dataclass
class FOVFocusMemory:
    """Thread-safe store of per-FOV piezo Z positions.

    Attributes:
        default_z_um: The fallback Z position used when a FOV has no cached
            value yet (typically the round-1 seed from the initial manual focus).
            ``None`` means "no seed available" — callers should then refrain
            from issuing a piezo move and let the lock engage at wherever the
            piezo currently is.
    """

    default_z_um: Optional[float] = None
    _positions: Dict[FOVKey, float] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    # ---------------------------------------------------------------------
    # Core accessors
    # ---------------------------------------------------------------------
    def set_default(self, z_um: Optional[float]) -> None:
        """Update the round-1 seed. ``None`` clears it."""
        with self._lock:
            if z_um is None:
                self.default_z_um = None
            elif not _is_finite(z_um):
                raise ValueError(f"default_z_um must be finite, got {z_um!r}")
            else:
                self.default_z_um = float(z_um)

    def remember(self, region_id: str, fov_index: int, z_um: float) -> None:
        """Record a successful lock's piezo Z for (region_id, fov_index).

        Raises ValueError for non-finite values or invalid indices.
        """
        if not isinstance(region_id, str) or not region_id:
            raise ValueError(f"region_id must be a non-empty string, got {region_id!r}")
        if not isinstance(fov_index, int) or fov_index < 0:
            raise ValueError(f"fov_index must be a non-negative int, got {fov_index!r}")
        if not _is_finite(z_um):
            raise ValueError(f"z_um must be finite, got {z_um!r}")
        with self._lock:
            self._positions[(region_id, int(fov_index))] = float(z_um)

    def get(self, region_id: str, fov_index: int) -> Optional[float]:
        """Return the cached Z for a FOV, or ``None`` if not set."""
        with self._lock:
            return self._positions.get((region_id, int(fov_index)))

    def get_seed(self, region_id: str, fov_index: int) -> Optional[float]:
        """Return the cached Z for a FOV, falling back to the default seed.

        Returns ``None`` if neither a cached value nor a default has been set.
        """
        with self._lock:
            cached = self._positions.get((region_id, int(fov_index)))
            if cached is not None:
                return cached
            return self.default_z_um

    def forget(self, region_id: str, fov_index: int) -> None:
        """Remove a cached FOV entry (no-op if absent)."""
        with self._lock:
            self._positions.pop((region_id, int(fov_index)), None)

    def clear(self) -> None:
        """Remove all cached FOV entries (leaves ``default_z_um`` intact)."""
        with self._lock:
            self._positions.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._positions)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        with self._lock:
            return key in self._positions

    def items(self) -> list[Tuple[FOVKey, float]]:
        """Return a snapshot of all (key, z_um) pairs."""
        with self._lock:
            return list(self._positions.items())

    # ---------------------------------------------------------------------
    # Serialization (for checkpointing)
    # ---------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        with self._lock:
            return {
                "default_z_um": self.default_z_um,
                # tuple keys serialized as "region_id::fov_index" strings
                "positions": {
                    f"{rid}::{int(fov)}": float(z)
                    for (rid, fov), z in self._positions.items()
                },
            }

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "FOVFocusMemory":
        """Restore from a to_dict() payload. ``None`` / missing fields give an empty memory."""
        if not data:
            return cls()
        default = data.get("default_z_um")
        memory = cls(default_z_um=float(default) if _is_finite(default) else None)
        positions = data.get("positions") or {}
        if not isinstance(positions, Mapping):
            return memory
        for key_str, z_value in positions.items():
            if not isinstance(key_str, str) or "::" not in key_str:
                continue
            region_id, _, fov_str = key_str.rpartition("::")
            try:
                fov_index = int(fov_str)
                z_um = float(z_value)
            except (TypeError, ValueError):
                continue
            if not region_id or not _is_finite(z_um) or fov_index < 0:
                continue
            memory._positions[(region_id, fov_index)] = z_um
        return memory

    # ---------------------------------------------------------------------
    # Bulk ops
    # ---------------------------------------------------------------------
    def update(self, entries: Iterable[Tuple[str, int, float]]) -> None:
        """Bulk-insert (region_id, fov_index, z_um) entries."""
        with self._lock:
            for region_id, fov_index, z_um in entries:
                if (
                    isinstance(region_id, str)
                    and region_id
                    and isinstance(fov_index, int)
                    and fov_index >= 0
                    and _is_finite(z_um)
                ):
                    self._positions[(region_id, int(fov_index))] = float(z_um)

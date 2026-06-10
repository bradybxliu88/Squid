"""Write a single multichannel capture as one OME-TIFF (C, Y, X)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import tifffile

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class CapturePlane:
    name: str
    data: np.ndarray            # 2D (Y, X)
    color: str                  # '#RRGGBB'
    exposure_ms: float
    wavelength: Optional[int]   # excitation nm; None for brightfield


def sanitize_prefix(prefix: str) -> str:
    cleaned = _SAFE.sub("_", (prefix or "").strip())
    return cleaned or "capture"


def next_capture_path(directory: str, prefix: str, ext: str = ".ome.tiff") -> str:
    """'<dir>/<prefix>_<NNN><ext>' with the lowest unused 3-digit index."""
    prefix = sanitize_prefix(prefix)
    n = 0
    while True:
        candidate = os.path.join(directory, f"{prefix}_{n:03d}{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def write_multichannel_ome_tiff(path: str, planes: List[CapturePlane]) -> None:
    if not planes:
        raise ValueError("write_multichannel_ome_tiff requires at least one plane")
    shape = planes[0].data.shape
    for p in planes:
        if p.data.shape != shape:
            raise ValueError(f"channel '{p.name}' shape {p.data.shape} != {shape}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    stack = np.stack([p.data for p in planes], axis=0)  # (C, Y, X)
    metadata = {"axes": "CYX", "Channel": {"Name": [p.name for p in planes]}}
    tifffile.imwrite(path, stack, photometric="minisblack", metadata=metadata, ome=True)

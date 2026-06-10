"""Pure-numpy pseudocolor compositing for the Squid Lite result view."""
from __future__ import annotations

from typing import List, Tuple

import numpy as np


def hex_to_rgb(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def apply_contrast(image: np.ndarray, low: float, high: float) -> np.ndarray:
    img = image.astype(np.float32)
    if high <= low:
        return np.zeros(img.shape, dtype=np.float32)
    return np.clip((img - low) / (high - low), 0.0, 1.0)


def colorize(image: np.ndarray, hex_color: str, low: float, high: float) -> np.ndarray:
    norm = apply_contrast(image, low, high)
    r, g, b = hex_to_rgb(hex_color)
    return np.stack([norm * r, norm * g, norm * b], axis=-1)


def composite(layers: List[np.ndarray]) -> np.ndarray:
    if not layers:
        raise ValueError("composite() requires at least one layer")
    out = np.zeros(layers[0].shape, dtype=np.float32)
    for layer in layers:
        out = 1.0 - (1.0 - out) * (1.0 - layer)
    return np.clip(out, 0.0, 1.0)


def to_uint8(rgb: np.ndarray) -> np.ndarray:
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def auto_contrast_bounds(image: np.ndarray, low_pct: float = 0.5, high_pct: float = 99.5) -> Tuple[float, float]:
    lo = float(np.percentile(image, low_pct))
    hi = float(np.percentile(image, high_pct))
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi

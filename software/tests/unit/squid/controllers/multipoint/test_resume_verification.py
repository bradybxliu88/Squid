"""Unit tests for B1.2a resume-side durability verification (fov_frames_present).

Pure filesystem checks -- no camera/stage/JobRunner/simulated backend.
"""

import pytest

from squid.backend.controllers.multipoint.resume_verification import fov_frames_present

pytestmark = pytest.mark.unit

PAD = 4
CHANNELS = ["Fluorescence 405 nm Ex", "Fluorescence 638 nm Ex"]


def _write_frame(dir_path, fov_id, z, channel, *, ext="tiff", content=b"\x00\x01"):
    """Write one frame file exactly as the acquisition pipeline names it."""
    channel_safe = channel.replace(" ", "_")
    name = f"{fov_id}_{z:0{PAD}}_{channel_safe}.{ext}"
    p = dir_path / name
    p.write_bytes(content)
    return p


def _write_all(dir_path, fov_id, n_z, channels, **kw):
    for z in range(n_z):
        for ch in channels:
            _write_frame(dir_path, fov_id, z, ch, **kw)


def test_all_present(tmp_path):
    _write_all(tmp_path, "A1_0003", 3, CHANNELS)
    assert fov_frames_present(str(tmp_path), "A1_0003", 3, CHANNELS, PAD) is True


def test_missing_one_frame(tmp_path):
    _write_all(tmp_path, "A1_0003", 3, CHANNELS)
    # Remove exactly one (z, channel) file -> the FOV is NOT durable.
    (tmp_path / f"A1_0003_0001_{CHANNELS[1].replace(' ', '_')}.tiff").unlink()
    assert fov_frames_present(str(tmp_path), "A1_0003", 3, CHANNELS, PAD) is False


def test_zero_byte_frame_rejected(tmp_path):
    _write_all(tmp_path, "A1_0003", 2, CHANNELS)
    # Truncate one to 0 bytes -> min_size guard fails it.
    (tmp_path / f"A1_0003_0000_{CHANNELS[0].replace(' ', '_')}.tiff").write_bytes(b"")
    assert fov_frames_present(str(tmp_path), "A1_0003", 2, CHANNELS, PAD) is False


def test_extension_agnostic(tmp_path):
    # 8-bit path saves as IMAGE_FORMAT (e.g. .png), not .tiff -> still counts.
    _write_all(tmp_path, "A1_0000", 2, CHANNELS, ext="png")
    assert fov_frames_present(str(tmp_path), "A1_0000", 2, CHANNELS, PAD) is True


def test_mixed_extensions(tmp_path):
    _write_frame(tmp_path, "A1_0000", 0, CHANNELS[0], ext="tiff")
    _write_frame(tmp_path, "A1_0000", 0, CHANNELS[1], ext="png")
    assert fov_frames_present(str(tmp_path), "A1_0000", 1, CHANNELS, PAD) is True


def test_validate_hook_flags_truncation(tmp_path):
    _write_all(tmp_path, "A1_0000", 1, CHANNELS)
    bad = str((tmp_path / f"A1_0000_0000_{CHANNELS[0].replace(' ', '_')}.tiff"))
    # Simulate a corrupt/truncated header for one file.
    assert fov_frames_present(
        str(tmp_path), "A1_0000", 1, CHANNELS, PAD, validate=lambda m: m != bad
    ) is False
    # Without the validate hook (existence+size only), it passes.
    assert fov_frames_present(str(tmp_path), "A1_0000", 1, CHANNELS, PAD) is True


def test_nz_zero_treated_as_one(tmp_path):
    _write_all(tmp_path, "A1_0000", 1, CHANNELS)
    assert fov_frames_present(str(tmp_path), "A1_0000", 0, CHANNELS, PAD) is True


def test_empty_dir_is_missing(tmp_path):
    assert fov_frames_present(str(tmp_path), "A1_0003", 2, CHANNELS, PAD) is False


def test_other_fov_files_do_not_count(tmp_path):
    # Only a DIFFERENT fov's frames are present -> target fov reads as missing.
    _write_all(tmp_path, "A1_0011", 2, CHANNELS)
    assert fov_frames_present(str(tmp_path), "A1_0001", 2, CHANNELS, PAD) is False

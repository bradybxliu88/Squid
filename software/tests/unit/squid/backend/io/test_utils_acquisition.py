"""Tests for filepath construction in utils_acquisition (ports upstream #535)."""

import numpy as np

from squid.backend.io.utils_acquisition import get_image_filepath


def test_spaces_in_channel_name_become_underscores():
    path = get_image_filepath("/tmp/exp", "0", "BF LED matrix full", np.uint16)
    assert path.endswith("0_BF_LED_matrix_full.tiff")


def test_unsafe_path_chars_are_sanitized():
    # Path separators and Windows-reserved characters must not survive into the
    # filename, or the write would target a wrong/invalid path.
    path = get_image_filepath("/tmp/exp", "3", 'Fluo/488:ex*?"<>|', np.uint16)
    leaf = path.rsplit("/", 1)[-1]
    for ch in '/\\:*?"<>|':
        assert ch not in leaf
    assert leaf.startswith("3_")
    assert leaf.endswith(".tiff")


def test_uint16_uses_tiff_extension():
    assert get_image_filepath("/d", "0", "c", np.uint16).endswith(".tiff")


def test_uint8_uses_runtime_image_format():
    # uint8 images use the (config-overridable) Acquisition.IMAGE_FORMAT.
    import _def

    expected_ext = _def.Acquisition.IMAGE_FORMAT
    assert get_image_filepath("/d", "0", "c", np.uint8).endswith(f".{expected_ext}")


def test_default_image_format_source_is_tiff():
    """The in-code default for Acquisition.IMAGE_FORMAT is 'tiff' (was 'bmp').

    INI files with [ACQUISITION] image_format=... override this at module load
    time, so we assert against the source rather than the runtime value.
    """
    import inspect

    import _def

    source = inspect.getsource(_def.Acquisition)
    assert 'IMAGE_FORMAT = "tiff"' in source

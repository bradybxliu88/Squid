import numpy as np
import pytest
from squid.ui.lite import compositing as C


def test_hex_to_rgb_pure_red():
    assert C.hex_to_rgb("#FF0000") == (1.0, 0.0, 0.0)


def test_apply_contrast_linear_scale():
    out = C.apply_contrast(np.array([[0, 5, 10]], np.uint16), 0.0, 10.0)
    np.testing.assert_allclose(out, [[0.0, 0.5, 1.0]], atol=1e-6)


def test_apply_contrast_degenerate_bounds_zeros():
    out = C.apply_contrast(np.array([[3, 7]], np.uint16), 5.0, 5.0)
    np.testing.assert_array_equal(out, np.zeros((1, 2), np.float32))


def test_colorize_red_only():
    rgb = C.colorize(np.array([[10]], np.uint16), "#FF0000", 0.0, 10.0)
    np.testing.assert_allclose(rgb, [[[1.0, 0.0, 0.0]]], atol=1e-6)


def test_composite_red_plus_green_is_yellow():
    red = C.colorize(np.full((1, 1), 10, np.uint16), "#FF0000", 0.0, 10.0)
    green = C.colorize(np.full((1, 1), 10, np.uint16), "#00FF00", 0.0, 10.0)
    np.testing.assert_allclose(C.composite([red, green]), [[[1.0, 1.0, 0.0]]], atol=1e-6)


def test_composite_empty_raises():
    with pytest.raises(ValueError):
        C.composite([])


def test_to_uint8_clips():
    np.testing.assert_array_equal(
        C.to_uint8(np.array([[[0.0, 0.5, 1.5]]], np.float32)), [[[0, 128, 255]]])


def test_auto_contrast_bounds_ordered():
    lo, hi = C.auto_contrast_bounds(np.arange(100, dtype=np.uint16).reshape(10, 10))
    assert lo < hi

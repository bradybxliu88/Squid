"""Regression tests for H1: brightfield / LED-matrix illumination routing.

Before the fix, IlluminationController._resolve_channel() only understood
fluorescence D-codes (11-15), so LED-array codes (0-9, brightfield/darkfield)
and the external FET (20) resolved to None and set_intensity/turn_on silently
did nothing -- the LED matrix was never driven. These tests lock the corrected
routing and guard the fluorescence path against regression.

All tests use a MagicMock microcontroller (no hardware, no threads) so they
complete instantly and cannot hang.
"""

import logging
from unittest.mock import MagicMock

import pytest

from _def import (
    ILLUMINATION_CODE,
    LED_MATRIX_R_FACTOR,
    LED_MATRIX_G_FACTOR,
    LED_MATRIX_B_FACTOR,
)
from squid.backend.drivers.lighting.led import IlluminationController


def _make_controller():
    """Default TTL + SquidControllerDAC controller with a mock microcontroller."""
    mc = MagicMock()
    ctrl = IlluminationController(mc, disable_intensity_calibration=True)
    return ctrl, mc


def test_brightfield_led_matrix_is_driven():
    ctrl, mc = _make_controller()
    src = ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_FULL  # 0
    ctrl.set_intensity(src, 50.0)
    ctrl.turn_on_illumination(src)
    mc.set_illumination_led_matrix.assert_called_once_with(
        src,
        r=0.5 * LED_MATRIX_R_FACTOR,
        g=0.5 * LED_MATRIX_G_FACTOR,
        b=0.5 * LED_MATRIX_B_FACTOR,
    )
    mc.turn_on_illumination.assert_called_once()
    mc.set_illumination.assert_not_called()
    assert ctrl.is_on[src] is True


def test_darkfield_pattern_routes_to_led_matrix():
    ctrl, mc = _make_controller()
    src = ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR  # 3
    ctrl.set_intensity(src, 100.0)
    args, kwargs = mc.set_illumination_led_matrix.call_args
    assert args[0] == src
    # Compare against the runtime factors (machine config may override the source
    # defaults; on a monochrome-blue matrix r=g=0, on a white matrix r=g=b).
    assert kwargs["b"] == 1.0 * LED_MATRIX_B_FACTOR
    assert kwargs["r"] == 1.0 * LED_MATRIX_R_FACTOR
    assert kwargs["g"] == 1.0 * LED_MATRIX_G_FACTOR
    mc.set_illumination.assert_not_called()


def test_external_fet_uses_dac_set_illumination():
    ctrl, mc = _make_controller()
    src = ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_EXTERNAL_FET  # 20
    ctrl.set_intensity(src, 60.0)
    ctrl.turn_on_illumination(src)
    mc.set_illumination.assert_called_once_with(src, 60.0)
    mc.set_illumination_led_matrix.assert_not_called()
    mc.turn_on_illumination.assert_called_once()
    assert ctrl.is_on[src] is True


def test_led_matrix_turn_off_issues_global_off():
    """Symmetry: a BF/DF channel must actually turn the matrix off (was a no-op)."""
    ctrl, mc = _make_controller()
    src = ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_FULL  # 0
    ctrl.turn_off_illumination(src)
    mc.turn_off_illumination.assert_called_once()
    assert ctrl.is_on[src] is False


def test_fluorescence_still_uses_set_illumination():
    """Regression guard: D1 (11) still drives DAC set_illumination, never the matrix."""
    ctrl, mc = _make_controller()
    ctrl.set_intensity(ILLUMINATION_CODE.ILLUMINATION_D1, 40.0)  # 11
    ctrl.turn_on_illumination(ILLUMINATION_CODE.ILLUMINATION_D1)
    mc.set_illumination.assert_called_once_with(11, 40.0)
    mc.set_illumination_led_matrix.assert_not_called()
    mc.turn_on_illumination.assert_called_once()


def test_unknown_code_is_skipped(caplog):
    ctrl, mc = _make_controller()
    with caplog.at_level(logging.WARNING):
        ctrl.set_intensity(99, 30.0)
        ctrl.turn_on_illumination(99)
    mc.set_illumination.assert_not_called()
    mc.set_illumination_led_matrix.assert_not_called()
    mc.turn_on_illumination.assert_not_called()


def test_boundary_code_9_is_led_matrix_but_10_is_not():
    """Lock the <10 threshold: code 9 -> LED matrix; code 10 -> unmapped/skip."""
    ctrl9, mc9 = _make_controller()
    ctrl9.set_intensity(9, 100.0)
    mc9.set_illumination_led_matrix.assert_called_once()

    ctrl10, mc10 = _make_controller()
    ctrl10.set_intensity(10, 100.0)
    mc10.set_illumination_led_matrix.assert_not_called()
    mc10.set_illumination.assert_not_called()


@pytest.mark.parametrize(
    "intensity,expected_frac",
    [(150.0, 1.0), (-20.0, 0.0), (0.0, 0.0), (25.0, 0.25)],
)
def test_led_matrix_intensity_clamped(intensity, expected_frac):
    ctrl, mc = _make_controller()
    ctrl.set_intensity(ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_FULL, intensity)
    _, kwargs = mc.set_illumination_led_matrix.call_args
    assert kwargs["b"] == expected_frac * LED_MATRIX_B_FACTOR


def test_bool_channel_not_treated_as_led_source():
    """Hardening: True must not be mis-treated as LED source code 1."""
    ctrl, mc = _make_controller()
    ctrl.set_intensity(True, 50.0)
    mc.set_illumination_led_matrix.assert_not_called()

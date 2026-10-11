"""Tests for AxisConfig helpers."""

import pytest

from squid.core.config import AxisConfig, DirectionSign


def _axis(min_position: float, max_position: float) -> AxisConfig:
    return AxisConfig(
        MOVEMENT_SIGN=DirectionSign(1),
        USE_ENCODER=False,
        ENCODER_SIGN=DirectionSign(1),
        ENCODER_STEP_SIZE=1.0,
        FULL_STEPS_PER_REV=200,
        SCREW_PITCH=1.0,
        MICROSTEPS_PER_STEP=8,
        MAX_SPEED=2.0,
        MAX_ACCELERATION=100.0,
        MIN_POSITION=min_position,
        MAX_POSITION=max_position,
        PID=None,
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.0, 0.05),  # below the floor
        (-3.0, 0.05),
        (0.05, 0.05),  # on the limits
        (6.0, 6.0),
        (1.25, 1.25),  # in range
        (11.0, 6.0),  # above the ceiling
    ],
)
def test_clamp_position(value, expected):
    assert _axis(0.05, 6.0).clamp_position(value) == pytest.approx(expected)


def test_clamp_position_with_infinite_limits_passes_values_through():
    axis = _axis(float("-inf"), float("inf"))

    assert axis.clamp_position(-1e9) == -1e9
    assert axis.clamp_position(1e9) == 1e9

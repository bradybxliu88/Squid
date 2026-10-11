"""Unit tests for CephlaStage driven by a mocked Microcontroller (no serial device, no simulator)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from squid.backend.drivers.stages.cephla import CephlaStage
from squid.backend.microcontroller import HomingDirection
from squid.core.config import AxisConfig, DirectionSign, StageConfig


def _axis(
    movement_sign: int = 1,
    max_speed: float = 20.0,
    min_position: float = 0.0,
    max_position: float = 100.0,
) -> AxisConfig:
    return AxisConfig(
        MOVEMENT_SIGN=DirectionSign(movement_sign),
        USE_ENCODER=False,
        ENCODER_SIGN=DirectionSign(1),
        ENCODER_STEP_SIZE=1.0,
        FULL_STEPS_PER_REV=200,
        SCREW_PITCH=1.0,
        MICROSTEPS_PER_STEP=8,
        MAX_SPEED=max_speed,
        MAX_ACCELERATION=100.0,
        MIN_POSITION=min_position,
        MAX_POSITION=max_position,
        PID=None,
    )


def _stage_config(**axis_overrides: AxisConfig) -> StageConfig:
    axes = {
        "X_AXIS": _axis(movement_sign=1),
        "Y_AXIS": _axis(movement_sign=1),
        "Z_AXIS": _axis(movement_sign=-1, max_speed=2.0, min_position=0.05, max_position=6.0),
        "THETA_AXIS": _axis(movement_sign=1, max_speed=1.0),
    }
    axes.update(axis_overrides)
    return StageConfig(**axes)


def _make_stage(config: StageConfig | None = None) -> tuple[CephlaStage, MagicMock]:
    micro = MagicMock(name="Microcontroller")
    micro.get_pos.return_value = (0, 0, 0, 0)
    return CephlaStage(microcontroller=micro, stage_config=config or _stage_config()), micro


# --- get_config() / blocking moves ------------------------------------------------------------------


def test_get_config_returns_the_stage_config():
    config = _stage_config()
    stage, _ = _make_stage(config)

    assert stage.get_config() is config
    assert stage.config is config


@pytest.mark.parametrize(
    "move, arg",
    [
        ("move_x", 1.0),
        ("move_y", 1.0),
        ("move_z", 0.5),
        ("move_x_to", 10.0),
        ("move_y_to", 10.0),
        ("move_z_to", 1.0),
    ],
)
def test_blocking_move_waits_for_the_microcontroller(move, arg):
    """Regression: CephlaStage computes its wait timeout from self.get_config(), which AbstractStage no
    longer provided, so every blocking move sent its command and then raised AttributeError."""
    stage, micro = _make_stage()

    getattr(stage, move)(arg, blocking=True)

    micro.wait_till_operation_is_completed.assert_called_once()
    assert micro.wait_till_operation_is_completed.call_args.args[0] >= 3


def test_stage_service_get_config_with_cephla_stage():
    from squid.backend.services.stage_service import StageService
    from squid.core.events import EventBus

    config = _stage_config()
    stage, _ = _make_stage(config)

    assert StageService(stage, EventBus()).get_config() is config


def _homing_calls(micro: MagicMock) -> list:
    """The home_* and wait calls, in order, ignoring anything else the stage asked of the mock."""
    return [c for c in micro.mock_calls if c[0].startswith("home_") or c[0] == "wait_till_operation_is_completed"]


# --- home() -----------------------------------------------------------------------------------------


def test_home_xyz_sends_homing_commands_in_order():
    """Regression: home() referenced squid.backend.microcontroller without importing it, so it raised
    NameError before any homing command reached the microcontroller."""
    stage, micro = _make_stage()

    stage.home(x=True, y=True, z=True, theta=False)

    micro.home_xy.assert_called_once_with(
        homing_direction_x=HomingDirection.HOMING_DIRECTION_BACKWARD,
        homing_direction_y=HomingDirection.HOMING_DIRECTION_BACKWARD,
    )
    # Z has MOVEMENT_SIGN -1 in this config, which maps to the forward homing direction.
    micro.home_z.assert_called_once_with(homing_direction=HomingDirection.HOMING_DIRECTION_FORWARD)
    micro.home_x.assert_not_called()
    micro.home_y.assert_not_called()
    micro.home_theta.assert_not_called()

    names = [c[0] for c in _homing_calls(micro)]
    assert names[:4] == [
        "home_xy",
        "wait_till_operation_is_completed",
        "home_z",
        "wait_till_operation_is_completed",
    ]


@pytest.mark.parametrize(
    "x, y, expected",
    [
        (True, False, "home_x"),
        (False, True, "home_y"),
    ],
)
def test_home_single_xy_axis_uses_single_axis_command(x, y, expected):
    stage, micro = _make_stage()

    stage.home(x=x, y=y, z=False, theta=False)

    getattr(micro, expected).assert_called_once_with(homing_direction=HomingDirection.HOMING_DIRECTION_BACKWARD)
    micro.home_xy.assert_not_called()
    micro.home_z.assert_not_called()


def test_home_theta_uses_theta_movement_sign():
    config = _stage_config(THETA_AXIS=_axis(movement_sign=-1, max_speed=1.0))
    stage, micro = _make_stage(config)

    stage.home(x=False, y=False, z=False, theta=True)

    micro.home_theta.assert_called_once_with(homing_direction=HomingDirection.HOMING_DIRECTION_FORWARD)


def test_home_non_blocking_does_not_wait():
    stage, micro = _make_stage()

    stage.home(x=True, y=True, z=True, theta=False, blocking=False)

    micro.home_xy.assert_called_once()
    micro.home_z.assert_called_once()
    micro.wait_till_operation_is_completed.assert_not_called()


def test_home_blocking_waits_with_positive_timeouts():
    stage, micro = _make_stage()

    stage.home(x=True, y=True, z=True, theta=True)

    waits = [c for c in _homing_calls(micro) if c[0] == "wait_till_operation_is_completed"]
    assert len(waits) == 3  # one each for xy, z and theta
    assert all(wait.args[0] >= 3 for wait in waits)  # _calc_move_timeout's floor

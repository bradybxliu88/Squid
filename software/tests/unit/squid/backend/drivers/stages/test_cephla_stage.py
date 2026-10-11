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


# --- _calc_move_timeout() ---------------------------------------------------------------------------


def test_calc_move_timeout_scales_with_axis_speed():
    """Regression: the divisor was min(0.1, speed) instead of max(0.1, speed), which pinned it at 0.1 mm/s
    and made every timeout far longer than the intended 3x naive move time (250x for X/Y and 20x for Z at
    the default 25 mm/s and 2 mm/s max speeds)."""
    timeout = CephlaStage._calc_move_timeout

    assert timeout(60.0, 30.0) == pytest.approx(6.0)  # 3 * 60 mm / 30 mm/s
    assert timeout(-60.0, 30.0) == pytest.approx(6.0)  # direction does not matter
    assert timeout(60.0, -30.0) == pytest.approx(6.0)  # nor does the sign of the speed
    assert timeout(0.5, 2.0) == 3.0  # short moves get the 3 s floor
    assert timeout(1.0, 0.0) == pytest.approx(30.0)  # zero speed falls back to 0.1 mm/s instead of dividing by zero


def test_blocking_move_x_to_timeout_uses_the_axis_max_speed():
    stage, micro = _make_stage(_stage_config(X_AXIS=_axis(max_speed=25.0)))

    stage.move_x_to(60.0)  # the mocked microcontroller reports x=0

    micro.wait_till_operation_is_completed.assert_called_once_with(pytest.approx(3 * 60.0 / 25.0))


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


# --- move_z_to() soft-limit clamp -------------------------------------------------------------------


def _z_usteps(stage: CephlaStage, z_mm: float) -> int:
    return stage.get_config().Z_AXIS.convert_real_units_to_ustep(z_mm)


@pytest.mark.parametrize(
    "target_mm, expected_mm",
    [
        (0.0, 0.05),  # the post-home Z=0.0 is below the 0.05 mm floor
        (-2.0, 0.05),
        (11.0, 6.0),  # above the 6 mm ceiling
        (1.5, 1.5),  # in range: untouched
    ],
)
def test_move_z_to_clamps_target_into_soft_limits(target_mm, expected_mm):
    """Upstream 8c62827a: an absolute MOVETO_Z outside the soft limits never completes in firmware, so the
    stage must never command one."""
    stage, micro = _make_stage()  # mocked microcontroller reports z=0

    stage.move_z_to(target_mm)

    commanded = [c.args[0] for c in micro.move_z_to_usteps.call_args_list]
    assert commanded[-1] == _z_usteps(stage, expected_mm)


def test_move_z_to_below_floor_from_above_keeps_backlash_premove_in_range():
    stage, micro = _make_stage()
    micro.get_pos.return_value = (0, 0, _z_usteps(stage, 3.0), 0)

    stage.move_z_to(-1.0)

    floor = _z_usteps(stage, stage.get_config().Z_AXIS.MIN_POSITION)
    commanded = [c.args[0] for c in micro.move_z_to_usteps.call_args_list]
    assert commanded == [floor, floor]  # backlash pre-move and final move both stop at the floor


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

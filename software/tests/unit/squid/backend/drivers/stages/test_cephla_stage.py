"""Unit tests for CephlaStage driven by a mocked Microcontroller (no serial device, no simulator)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from squid.backend.drivers.stages.cephla import CephlaStage
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

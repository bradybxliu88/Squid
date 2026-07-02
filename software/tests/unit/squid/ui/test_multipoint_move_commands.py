"""Regression tests for multipoint stage-move / region commands.

These cover three bug fixes that previously raised TypeError at runtime because
the widgets constructed events with the wrong class / field names:

- H4: WellplateMultiPointWidget._move_z_to must publish MoveStageToCommand
      (MoveStageCommand has no z_mm field -> TypeError on goto_z_min/goto_z_max).
- H5: FlexibleMultiPointWidget._move_stage_to must publish MoveStageToCommand
      (MoveStageCommand has no x_mm/y_mm/z_mm fields -> TypeError on go_to).
- H6: FlexibleMultiPointWidget._load_positions_from_yaml must construct
      AddFlexibleRegionCommand with center_x_mm/center_y_mm/center_z_mm/n_x/n_y/
      overlap_percent (old kwargs raised TypeError: unexpected keyword).

Widgets are built with object.__new__(...) to bypass the heavy QFrame __init__;
the exercised methods only touch attributes we inject.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from squid.core.events import (
    MoveStageToCommand,
    MoveStageCommand,
    AddFlexibleRegionCommand,
    ClearScanCoordinatesCommand,
)
from squid.ui.widgets.acquisition.wellplate_multipoint import WellplateMultiPointWidget
from squid.ui.widgets.acquisition.flexible_multipoint import FlexibleMultiPointWidget


def test_move_z_to_publishes_move_stage_to_command():
    """H4: _move_z_to publishes a Z-only absolute move (x_mm/y_mm None)."""
    w = WellplateMultiPointWidget.__new__(WellplateMultiPointWidget)
    w._publish = MagicMock()

    w._move_z_to(1.5)

    evt = w._publish.call_args.args[0]
    assert isinstance(evt, MoveStageToCommand)
    assert evt.z_mm == 1.5
    assert evt.x_mm is None
    assert evt.y_mm is None


def test_move_stage_command_has_no_z_mm_field():
    """H4 (negative): documents the old bug - MoveStageCommand(z_mm=...) raises."""
    with pytest.raises(TypeError):
        MoveStageCommand(z_mm=1.5)


def test_move_stage_to_publishes_move_stage_to_command():
    """H5: _move_stage_to publishes a full XYZ absolute move."""
    w = FlexibleMultiPointWidget.__new__(FlexibleMultiPointWidget)
    w._publish = MagicMock()

    w._move_stage_to(1.0, 2.0, 3.0)

    evt = w._publish.call_args.args[0]
    assert isinstance(evt, MoveStageToCommand)
    assert (evt.x_mm, evt.y_mm, evt.z_mm) == (1.0, 2.0, 3.0)


def test_load_positions_from_yaml_publishes_valid_add_flexible_region():
    """H6: _load_positions_from_yaml builds a valid AddFlexibleRegionCommand.

    First publishes ClearScanCoordinatesCommand, then an AddFlexibleRegionCommand
    with the correct field names. Pre-fix this raised TypeError at construction.
    """
    w = FlexibleMultiPointWidget.__new__(FlexibleMultiPointWidget)
    w._publish = MagicMock()
    w.location_list = np.empty((0, 3), dtype=float)
    w.location_ids = np.empty((0,), dtype="<U20")
    w.use_overlap = True
    w._cached_z_mm = 0.0
    w._update_location_display = MagicMock()
    w.entry_NX = MagicMock()
    w.entry_NX.value.return_value = 2
    w.entry_NY = MagicMock()
    w.entry_NY.value.return_value = 3
    w.entry_overlap = MagicMock()
    w.entry_overlap.value.return_value = 10.0

    w._load_positions_from_yaml([{"name": "P1", "center_mm": [1.0, 2.0, 3.0]}])

    published = [c.args[0] for c in w._publish.call_args_list]
    assert isinstance(published[0], ClearScanCoordinatesCommand)
    add = next(e for e in published if isinstance(e, AddFlexibleRegionCommand))
    assert add.region_id == "P1"
    assert add.center_x_mm == 1.0
    assert add.center_y_mm == 2.0
    assert add.center_z_mm == 3.0
    assert add.n_x == 2
    assert add.n_y == 3
    assert add.overlap_percent == 10.0

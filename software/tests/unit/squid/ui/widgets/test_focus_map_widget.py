"""Tests for FocusMapWidget point navigation."""

from unittest.mock import MagicMock

import pytest

from squid.core.events import MoveStageToCommand


class _RecordingEventBus:
    """Minimal UIEventBus stand-in that records published events."""

    def __init__(self):
        self.published_events = []

    def subscribe(self, event_type, callback):
        pass

    def unsubscribe(self, event_type, callback):
        pass

    def publish(self, event):
        self.published_events.append(event)


@pytest.fixture
def widget(qtbot):
    from squid.ui.widgets.display.focus_map import FocusMapWidget

    bus = _RecordingEventBus()
    w = FocusMapWidget(focusMap=MagicMock(), event_bus=bus)
    qtbot.addWidget(w)
    return w, bus


def test_goto_next_point_moves_stage_once_per_click(widget):
    """Regression: setCurrentIndex emitted currentIndexChanged -> goto_selected_point, and then
    goto_next_point called goto_selected_point again, so every click published two moves."""
    w, bus = widget
    points = [("A1", 1.0, 2.0, 3.0), ("A1", 4.0, 5.0, 6.0), ("B2", 7.0, 8.0, 9.0)]
    w.focus_points = list(points)
    w.update_point_list()
    w.enabled = True

    for _, x, y, z in points[1:] + points[:1]:  # three clicks: 1, 2, then wrap back to 0
        bus.published_events.clear()

        w.goto_next_point()

        moves = [e for e in bus.published_events if isinstance(e, MoveStageToCommand)]
        assert moves == [MoveStageToCommand(x_mm=x, y_mm=y, z_mm=z)]


def test_selecting_a_point_in_the_combo_still_moves_the_stage(widget):
    """Blocking the signal in goto_next_point must not disconnect manual selection from navigation."""
    w, bus = widget
    w.focus_points = [("A1", 1.0, 2.0, 3.0), ("A1", 4.0, 5.0, 6.0)]
    w.update_point_list()
    w.enabled = True
    bus.published_events.clear()

    w.point_combo.setCurrentIndex(1)

    moves = [e for e in bus.published_events if isinstance(e, MoveStageToCommand)]
    assert moves == [MoveStageToCommand(x_mm=4.0, y_mm=5.0, z_mm=6.0)]

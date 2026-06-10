"""Tests for ImageDisplayWindow.eventFilter — Ctrl+Scroll Z navigation (ports #532)."""

from unittest.mock import MagicMock

import pytest
from qtpy.QtCore import QEvent, Qt

import _def
from squid.ui.widgets.display.image_display import ImageDisplayWindow


@pytest.fixture
def window(qapp):
    w = ImageDisplayWindow(show_LUT=False)
    yield w
    w.close()


def _wheel_event(notches_y, modifiers):
    """A mock QWheelEvent: only the attributes eventFilter reads."""
    ev = MagicMock()
    ev.type.return_value = QEvent.Wheel
    ev.modifiers.return_value = modifiers
    ev.angleDelta.return_value.y.return_value = notches_y * 120
    return ev


def _collect(window):
    emitted = []
    window.signal_z_um_delta.connect(emitted.append)
    return emitted


def test_ctrl_scroll_emits_fine_step(window):
    emitted = _collect(window)
    handled = window.eventFilter(None, _wheel_event(1, Qt.ControlModifier))
    assert handled is True
    assert emitted == [pytest.approx(_def.LIVE_VIEW_Z_STEP_UM)]


def test_ctrl_shift_scroll_emits_fast_step_and_sign(window):
    emitted = _collect(window)
    handled = window.eventFilter(None, _wheel_event(-1, Qt.ControlModifier | Qt.ShiftModifier))
    assert handled is True
    assert emitted == [pytest.approx(-_def.LIVE_VIEW_Z_STEP_FAST_UM)]


def test_zero_notch_consumed_without_emit(window):
    emitted = _collect(window)
    handled = window.eventFilter(None, _wheel_event(0, Qt.ControlModifier))
    assert handled is True  # consumed so it doesn't zoom
    assert emitted == []


def test_plain_scroll_passes_through_without_emit(window):
    emitted = _collect(window)
    # A non-wheel event must not be treated as a Z scroll and must not emit.
    window.eventFilter(window, QEvent(QEvent.MouseButtonPress))
    assert emitted == []

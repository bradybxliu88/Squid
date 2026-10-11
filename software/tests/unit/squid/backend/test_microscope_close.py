"""Microscope.close() releases the add-on hardware (Dragonfly part ports upstream 432772be)."""

from unittest.mock import MagicMock

import squid.core.logging
from squid.backend.drivers.lighting.dragonfly import Dragonfly_Simulation
from squid.backend.microscope import Microscope, MicroscopeAddons


class RecordingDragonfly(Dragonfly_Simulation):
    def __init__(self, fail: bool = False):
        super().__init__()
        self.closed = False
        self._fail = fail

    def close(self):
        self.closed = True
        if self._fail:
            raise RuntimeError("port already gone")


def _bare_microscope(addons: MicroscopeAddons) -> Microscope:
    """A Microscope with mocked core hardware, so close() can run without building the full stack."""
    scope = Microscope.__new__(Microscope)
    scope._log = squid.core.logging.get_logger("test_microscope_close")
    scope.live_controller = MagicMock()
    scope.low_level_drivers = MagicMock()
    scope.camera = MagicMock()
    scope.addons = addons
    return scope


def test_close_releases_the_dragonfly():
    dragonfly = RecordingDragonfly()
    scope = _bare_microscope(MicroscopeAddons(dragonfly=dragonfly))

    scope.close()

    assert dragonfly.closed
    scope.camera.close.assert_called_once()


def test_dragonfly_close_failure_does_not_stop_the_rest_of_shutdown():
    dragonfly = RecordingDragonfly(fail=True)
    scope = _bare_microscope(MicroscopeAddons(dragonfly=dragonfly))

    scope.close()

    assert dragonfly.closed
    scope.camera.close.assert_called_once()


def test_close_without_a_dragonfly():
    scope = _bare_microscope(MicroscopeAddons())

    scope.close()

    scope.camera.close.assert_called_once()

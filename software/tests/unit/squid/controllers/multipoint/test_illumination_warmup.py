"""Tests for the per-round excitation-laser warm-up (MultiPointWorker).

Wakes lasers that drop to standby between rounds so the first FOVs are not dark.
Pulses each distinct excitation source once, at LOW power, before the FOV loop.
Exercised against a minimal stand-in ``self`` to avoid building a full worker.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker


class FakeIllumination:
    def __init__(self):
        self.calls: list[tuple] = []

    def set_channel_power(self, source, power):
        self.calls.append(("power", int(source), float(power)))

    def turn_on_channel(self, source):
        self.calls.append(("on", int(source)))

    def turn_off_channel(self, source):
        self.calls.append(("off", int(source)))


def _cfg(source, intensity):
    return SimpleNamespace(illumination_source=source, illumination_intensity=intensity)


class _Stub:
    def __init__(self, illum, configs, *, enabled=True, s=0.001, frac=0.1):
        self._illumination_service = illum
        self.selected_configurations = configs
        self._illumination_warmup_enabled = enabled
        self._illumination_warmup_s = s
        self._illumination_warmup_fraction = frac
        self._log = logging.getLogger("test")


def _run(stub):
    MultiPointWorker._warmup_illumination(stub)


def test_warmup_pulses_each_source_low_power_on_then_off():
    illum = FakeIllumination()
    stub = _Stub(illum, [_cfg(1, 80.0), _cfg(2, 40.0)], frac=0.1)

    _run(stub)

    # Each source: set low power, on, off — in order.
    assert illum.calls == [
        ("power", 1, 8.0),
        ("on", 1),
        ("off", 1),
        ("power", 2, 4.0),
        ("on", 2),
        ("off", 2),
    ]


def test_warmup_dedups_repeated_sources():
    illum = FakeIllumination()
    # Two channels share source 1; source 2 appears once.
    stub = _Stub(illum, [_cfg(1, 100.0), _cfg(1, 50.0), _cfg(2, 60.0)])

    _run(stub)

    warmed_sources = [s for (kind, s) in ((c[0], c[1]) for c in illum.calls) if kind == "on"]
    assert warmed_sources == [1, 2]  # source 1 warmed once, not twice


def test_warmup_power_is_fraction_of_intensity():
    illum = FakeIllumination()
    stub = _Stub(illum, [_cfg(3, 80.0)], frac=0.25)

    _run(stub)

    power_calls = [c for c in illum.calls if c[0] == "power"]
    assert power_calls == [("power", 3, 20.0)]  # 80 * 0.25


def test_warmup_disabled_is_noop():
    illum = FakeIllumination()
    stub = _Stub(illum, [_cfg(1, 80.0)], enabled=False)
    _run(stub)
    assert illum.calls == []


def test_warmup_zero_duration_is_noop():
    illum = FakeIllumination()
    stub = _Stub(illum, [_cfg(1, 80.0)], s=0.0)
    _run(stub)
    assert illum.calls == []


def test_warmup_no_service_is_noop():
    stub = _Stub(None, [_cfg(1, 80.0)])
    _run(stub)  # must not raise


def test_warmup_turns_off_even_if_on_raises():
    """A failure mid-warm-up must still turn the laser back off (finally)."""

    class FlakyIllum(FakeIllumination):
        def turn_on_channel(self, source):
            self.calls.append(("on", int(source)))
            raise RuntimeError("engine hiccup")

    illum = FlakyIllum()
    stub = _Stub(illum, [_cfg(1, 80.0)])
    _run(stub)  # must not raise
    assert ("off", 1) in illum.calls

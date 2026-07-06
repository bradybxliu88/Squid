"""Laser-safety: the microcontroller must force every laser OFF on init, on
close, and on demand — without hanging when the device is unresponsive.

Background: the Teensy latches its last-commanded laser state and is NOT reset
when the USB serial port opens/closes, so a laser left on keeps emitting after
the control software exits. turn_off_all_lasers() is the belt-and-suspenders that
drives the laser-AF diode (pin 15) and all five excitation TTL ports low; it is
wired into __init__ (self-heal a latched beam on launch) and close() (last-resort
on teardown). It suppresses serial reconnect so a disconnected Teensy fails fast
instead of hanging shutdown, and it reports whether the controller acknowledged.
"""

from unittest.mock import patch

from _def import ILLUMINATION_CODE
from squid.backend.drivers.stages.serial import get_microcontroller_serial_device
from squid.backend.microcontroller import Microcontroller


def _make_micro() -> Microcontroller:
    # reset_and_initialize=False: no stage homing/moves, just the link.
    dev = get_microcontroller_serial_device(simulated=True)
    return Microcontroller(serial_device=dev, reset_and_initialize=False)


def test_turn_off_all_lasers_covers_af_and_every_excitation_source():
    micro = _make_micro()
    try:
        with patch.object(micro, "turn_off_AF_laser", wraps=micro.turn_off_AF_laser) as af, patch.object(
            micro, "set_illumination", wraps=micro.set_illumination
        ) as si, patch.object(
            micro, "turn_off_illumination", wraps=micro.turn_off_illumination
        ) as ti, patch.object(
            micro, "turn_off_all_ports", wraps=micro.turn_off_all_ports
        ) as ap:
            micro.turn_off_all_lasers(wait=False)

        # Laser-autofocus diode (the beam not covered by the firmware interlock).
        af.assert_called_once()
        # Every excitation source selected at intensity 0, each followed by an off.
        selected = [c.args[0] for c in si.call_args_list]
        assert selected == [
            ILLUMINATION_CODE.ILLUMINATION_D1,  # 405 nm
            ILLUMINATION_CODE.ILLUMINATION_D2,  # 488 nm
            ILLUMINATION_CODE.ILLUMINATION_D3,  # 561 nm
            ILLUMINATION_CODE.ILLUMINATION_D4,  # 638 nm (red)
            ILLUMINATION_CODE.ILLUMINATION_D5,  # 730 nm
        ]
        assert all(c.args[1] == 0 for c in si.call_args_list)
        assert ti.call_count == 5
        # Multi-port firmware path is gated on capability.
        if micro.supports_multi_port():
            ap.assert_called_once()
        else:
            ap.assert_not_called()
    finally:
        micro.close()


def test_turn_off_all_lasers_is_best_effort_and_never_raises():
    micro = _make_micro()
    try:
        # Even if the FIRST command errors, the rest must still run and the call
        # must not raise — a safety shutoff can't be aborted by one bad command.
        with patch.object(micro, "turn_off_AF_laser", side_effect=RuntimeError("boom")), patch.object(
            micro, "turn_off_illumination", wraps=micro.turn_off_illumination
        ) as ti:
            micro.turn_off_all_lasers(wait=False)
        assert ti.call_count == 5  # reached despite the earlier failure
    finally:
        micro.close()


def test_turn_off_all_lasers_returns_true_when_acknowledged():
    micro = _make_micro()
    try:
        # On a responsive (simulated) controller the commands are acknowledged.
        assert micro.turn_off_all_lasers(wait=True) is True
    finally:
        micro.close()


def test_turn_off_all_lasers_fails_fast_without_reconnect_on_dead_device():
    micro = _make_micro()
    try:
        seen_reconnect_tries = []

        def dead_write(data, reconnect_tries=0):
            seen_reconnect_tries.append(reconnect_tries)
            raise IOError("device gone")

        with patch.object(micro._serial, "write", side_effect=dead_write):
            result = micro.turn_off_all_lasers(wait=True)

        # Nothing reached the device -> cannot confirm -> False (not a crash/hang).
        assert result is False
        # Crucially, every safety-off write used reconnect_tries=0, so a
        # disconnected Teensy fails instantly instead of a per-command reconnect
        # storm that would hang shutdown for minutes.
        assert seen_reconnect_tries, "expected write attempts"
        assert all(t == 0 for t in seen_reconnect_tries)
        # The suppress flag is restored afterwards (normal commands reconnect again).
        assert micro._suppress_reconnect is False
    finally:
        micro.close()


def test_close_forces_lasers_off():
    micro = _make_micro()
    with patch.object(micro, "turn_off_all_lasers") as off:
        micro.close()
    off.assert_called_once()


def test_init_self_heals_by_forcing_lasers_off_on_construction():
    dev = get_microcontroller_serial_device(simulated=True)
    with patch.object(Microcontroller, "turn_off_all_lasers") as off:
        micro = Microcontroller(serial_device=dev, reset_and_initialize=False)
        # Constructing the link must have driven lasers off (self-heal), so a beam
        # latched on while the software was closed is extinguished on launch.
        off.assert_called()
    micro.close()

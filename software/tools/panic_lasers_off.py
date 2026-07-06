#!/usr/bin/env python3
"""Emergency "all lasers OFF" — connect to the Teensy and force every laser dark.

WHY THIS EXISTS
---------------
The microcontroller latches its last-commanded laser state and is NOT reset when
the USB serial port is opened or closed. So if a laser (the laser-autofocus diode
on pin 15, or any of the 405/488/561/638/730 nm excitation lasers) was left on —
by a crash, a hard kill, or an old build that didn't turn lasers off on exit — it
keeps emitting from the objective even with the control software closed.

This script sends the off commands the firmware understands, without launching the
full GUI. It does NOT home or move the stage (reset_and_initialize=False).

USAGE
-----
Close the main application first (only one process can own the serial port), then::

    cd software
    python tools/panic_lasers_off.py

If it cannot find or open the controller (e.g. the GUI still holds the port, or the
Teensy is unplugged), the fastest physical remedy is to POWER-CYCLE the controller
box — a fresh boot re-runs the firmware init, which drives every laser pin LOW.
"""

import os
import sys

# Make `import _def` and `import squid...` work when run from anywhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def main() -> int:
    import _def
    from squid.backend.microcontroller import (
        Microcontroller,
        get_microcontroller_serial_device,
    )

    print("Connecting to the microcontroller...")
    try:
        serial_device = get_microcontroller_serial_device(
            version=_def.CONTROLLER_VERSION, sn=_def.CONTROLLER_SN
        )
    except Exception as exc:
        print(f"ERROR: could not open the controller serial port: {exc}")
        print(
            "Is the main application still running (it owns the port)?\n"
            "If so, close it and re-run. If the controller is unresponsive,\n"
            "POWER-CYCLE the controller box — booting resets every laser pin to OFF."
        )
        return 2

    # reset_and_initialize=False: do NOT home/move the stage. Construction alone
    # already forces lasers off (see Microcontroller.__init__); we also call it
    # explicitly with wait=True below so the off commands are acknowledged.
    try:
        micro = Microcontroller(serial_device=serial_device, reset_and_initialize=False)
    except Exception as exc:
        print(f"ERROR: the controller did not respond while connecting: {exc}")
        print(
            "Could not command the laser off. POWER-CYCLE the controller box —\n"
            "booting re-runs the firmware init, which drives every laser pin to OFF."
        )
        return 3

    try:
        acknowledged = micro.turn_off_all_lasers(wait=True)
        if acknowledged:
            print("All lasers commanded OFF and acknowledged (laser-AF diode + 405/488/561/638/730 nm).")
            print(
                "If a red beam is STILL visible, it is not driven by this "
                "controller — check for a separate laser supply / key switch."
            )
            return 0
        # Commands went out but the controller never confirmed — do NOT claim success.
        print(
            "WARNING: laser-off commands were SENT but the controller did NOT "
            "acknowledge them.\nDo not assume the beam is off — POWER-CYCLE the "
            "controller box to be certain."
        )
        return 4
    finally:
        try:
            micro.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

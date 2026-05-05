# Illumination Serial Watchdog (Deferred)

**Status:** DEFERRED
**Started:** 2026-05-05

## Upstream Commits

- [ ] `d30b2fd9` - feat: Replace illumination timeout with serial watchdog (#506) — DEFERRED

## Why Deferred

The watchdog port involves:
- **Firmware changes** to `firmware/controller/`: new commands `SET_WATCHDOG_TIMEOUT` (40)
  and `HEARTBEAT` (42), monitoring of `last_serial_message_time`, one-shot
  auto-shutoff logic. The firmware would need to be compiled with the Teensy
  toolchain and flashed to the actual MCU, then verified on hardware.
- **Software protocol changes** to `microcontroller.py`: a new heartbeat daemon
  thread that sends periodic keepalive commands so the firmware doesn't trigger
  the watchdog.
- **Microscope-level integration**: enabling the watchdog on startup.

Risks:
- arch_v2's firmware never had upstream's per-port timeout (PR #486) that this
  watchdog was a replacement for, so the "Reverts PR #486" portion of the diff
  doesn't apply cleanly.
- Adding a new firmware feature without hardware testing risks shipping broken
  software-firmware interactions.
- The heartbeat thread adds ongoing serial traffic that could interfere with
  arch_v2's other MCU command flows in subtle ways under load.

Best done as a focused effort by someone with:
- Squid hardware available (Teensy 4.1 + serial connection)
- Ability to flash custom firmware and test the watchdog auto-shutoff path
- Time to verify the heartbeat doesn't impact acquisition timing

## Files Touched by Upstream

- `firmware/controller/main_controller_teensy41.ino`
- `firmware/controller/src/commands/commands.cpp`
- `firmware/controller/src/commands/light_commands.{cpp,h}`
- `firmware/controller/src/constants.h`
- `firmware/controller/src/constants_protocol.h`
- `firmware/controller/src/functions.cpp`
- `firmware/controller/src/globals.{cpp,h}`
- `firmware/controller/src/serial_communication.cpp`
- `software/control/_def.py`
- `software/control/microcontroller.py` (large changes — 234 modified lines)
- `software/control/microscope.py`
- `software/tests/test_watchdog.py` (new, 132 lines, 12 tests)
- `software/tests/test_multiport_illumination_*.py` (3 files, minor adjustments)

## Test Plan When Picking This Up

- [ ] Compile and flash modified firmware to a Teensy 4.1
- [ ] With software running, verify "Illumination watchdog enabled" appears in startup logs
- [ ] Turn on a laser, kill the software process, verify auto-shutoff after the timeout
- [ ] Run a full acquisition with the heartbeat thread active; verify no timing regressions
- [ ] Confirm the new tests in `test_watchdog.py` all pass

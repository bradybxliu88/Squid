# Firmware Correctness Pair — Deferred (Suite D)

**Status:** DEFERRED
**Reviewed:** 2026-06-09

Two real firmware correctness bugs that are present in arch_v2 but deferred:
they require firmware (C++) changes that cannot be compiled (no PlatformIO in
the dev environment) or validated (no Teensy hardware), and they partially
depend on firmware infrastructure that arch_v2's diverged firmware lacks. Same
disposition as the watchdog suite (`plans/2026-05-05-watchdog-deferred.md`).

User context (2026-06-09): does not use a filter wheel, so the filter-wheel
fix (`f2a2cef0`) is low priority. Note `19b883eb` is **illumination strobe**,
not filter-wheel — see below.

## Upstream Commits

- [ ] `19b883eb` - fix(firmware): close strobe ISR race that leaves old channel HIGH on switch (#549)
- [ ] `f2a2cef0` - fix: firmware reports failed moves; filter wheel uses absolute moveto (#540)

---

## `f2a2cef0` — failed-move reporting + absolute filter-wheel moveto

**Bug (CONFIRMED present in arch_v2):** `firmware/controller/src/commands/stage_commands.cpp`
`callback_move_to_w()` sets `mcu_cmd_execution_in_progress = true` only inside
the `if (tmc4361A_moveTo(...) == 0)` success branch. On a failed move (or the
`enable_filterwheel == false` early path) the flag stays false, so the next
status packet reports `COMPLETED_WITHOUT_ERRORS` for the new cmd_id even though
no motion happened. With no W encoder (`HAS_ENCODER_W = False`) the host's
tracked position silently drifts → every subsequent filter switch is off-by-one
until a manual re-home. (X/Y/Z don't manifest this — firmware broadcasts their
actual position every status packet.)

**Upstream fix has two parts:**
1. Firmware: add `mark_move_failed()` / `report_move_error()` and call them on
   the failure / early-return paths of X/Y/Z/W move callbacks so a failed move
   reports `CMD_EXECUTION_ERROR` instead of a fake completion. Refactors the
   filter-wheel move path into a shared `dispatch_filterwheel_move` (relative +
   absolute, W + W2).
2. Host: rewrite `cephla.py SquidFilterWheel` to use **absolute** `MOVETO_W` /
   `MOVETO_W2` addressing (containment: a dropped move no longer compounds).

**arch_v2 blockers:**
- **No `MOVETO_W2`** in arch_v2 firmware (`constants_protocol.h` /
  `stage_commands.cpp`) — the absolute-moveto host rewrite for the 2nd wheel
  has no firmware command to target.
- Host absolute-moveto also assumes firmware homing resets W position to 0;
  needs verification against arch_v2 firmware homing behavior.
- Firmware refactor touches every axis' move callback + serial status path —
  broad, and unbuildable/untestable here.

**Adaptation notes for a hardware session:**
- The firmware failed-move fix (set `CMD_EXECUTION_ERROR` + unwind
  `mcu_cmd_execution_in_progress` on failure) is contained and clearly correct;
  it could land first, with the host learning to handle the new error status
  (`microcontroller.py` move-completion path).
- Absolute moveto needs `MOVETO_W2` added to arch_v2 firmware first, or restrict
  the host change to W only.
- arch_v2 host driver is `software/src/squid/backend/drivers/filter_wheels/cephla.py`
  (single `_config`/`_pos_index`, relative `_move_wheel` mm deltas) — a full
  port replaces this with absolute `_target_pos_to_usteps` / `_move_to_usteps`.

---

## `19b883eb` — strobe ISR race (ILLUMINATION, not filter-wheel)

**Bug (CONFIRMED present in arch_v2):** `firmware/controller/src/functions.cpp`
`ISR_strobeTimer()` calls `turn_on_illumination()` at strobe start and
`turn_off_illumination()` at strobe end, both reading the global
`illumination_source`. Python switches channels via
`turn_off → set_illumination(new) → turn_on`; if `set_illumination(new)` lands
between strobe start and end, strobe-end turns off the *new* port and leaves the
*old* one stuck HIGH. Result: two illumination sources lit at once during live
HW-triggered acquisition (e.g. 640 nm stays on after switching to 405 nm).

**Upstream fix:** latch `strobe_active_source[camera_channel] =
illumination_source` at strobe start; use the latched value at strobe end so the
ISR turns off the same port it lit. Mark `illumination_source` /
`illumination_is_on` `volatile`. Extract symmetric
`turn_on_illumination_source(int)` / `turn_off_illumination_source(int)` helpers,
with a `default:` → `turn_off_all_ports()` fallback. Firmware version 1.2 → 1.3.

**arch_v2 blockers:**
- arch_v2 firmware is on the **older single-source illumination model** — no
  multi-port infra (`illumination_source_to_port_index`,
  `illumination_port_is_on`, `NUM_ILLUMINATION_PORTS`, `turn_off_all_ports`),
  which the upstream helper extraction + `default:` fallback depend on.
- arch_v2 firmware has **no `FIRMWARE_VERSION_MAJOR/MINOR` defines**, so the
  1.2→1.3 version bump + host version gating don't map.
- Unbuildable (no PlatformIO) / untestable (no hardware) here.

**Adaptation notes:** the *core* idea ports cleanly even to the older model —
add `static int strobe_active_source[6]`, latch it at strobe start in
`ISR_strobeTimer()`, pass it to source-specific on/off that take an explicit
source arg (or inline the existing switch on the latched value), and mark the
two globals `volatile`. The helper extraction / multi-port `default:` fallback
should be dropped for arch_v2. This is the higher-value of the two for a
multi-channel HW-triggered fluorescence user.

## Why deferred (not skipped)

Both bugs are real and reproduce in arch_v2 — they are not not-applicable. They
are deferred strictly on the unbuildable/untestable-firmware + diverged-tree
constraint, to be executed in a session with PlatformIO + Teensy 4.1 available.

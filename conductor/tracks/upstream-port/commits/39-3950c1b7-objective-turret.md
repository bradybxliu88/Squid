# Objective Turret (NiMotion 4-position, Modbus-RTU)

**Our Commit:** `3950c1b7`
**Date:** 2026-06-10
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| `cbdc84c5` | feat: integrate motorized 4-position objective turret (#528) |

## Summary

Ports a motorized 4-position objective turret (NiMotion RS-485 stepper over Modbus-RTU)
alongside the existing Xeryon 2-position changer. A microscope has exactly one of
turret / Xeryon / manual, enforced by a mutual-exclusion guard. A unified
`move_to_objective(name)` interface is added to both controllers so the widget and
startup share one call path.

## Files Created/Modified

### Created
- `backend/drivers/peripherals/modbus_rtu.py` (538 lines) — pyserial Modbus-RTU client
  (CRC-16 table, frame builders, FC03/04/06/16, transient-busy retry with backoff).
- `backend/drivers/peripherals/objective_turret.py` (504 lines) —
  `ObjectiveTurret4PosController` (real) + `ObjectiveTurret4PosController_Simulation`.
- `tests/unit/squid/backend/drivers/peripherals/__init__.py`
- `tests/unit/squid/backend/drivers/peripherals/test_modbus_rtu.py` (13 tests)
- `tests/unit/squid/backend/drivers/peripherals/test_objective_turret.py` (14 tests)
- `tests/unit/squid/backend/drivers/peripherals/test_objective_changer.py` (4 tests)
- `tests/unit/squid/config/test_objective_changer_config.py` (5 tests)

### Modified
- `backend/drivers/peripherals/objective_changer.py` — `move_to_objective(name)` dispatcher
  on `ObjectiveChanger2PosController` and its `_Simulation`.
- `src/_def.py` — turret constants (`USE_OBJECTIVE_TURRET`, `OBJECTIVE_TURRET_*`) +
  `_validate_objective_changer_flags`, called after machine-config load.
- `backend/microscope_factory.py` — `elif _def.USE_OBJECTIVE_TURRET:` construction branch (real/sim).
- `backend/microscope.py` — `prepare_for_use()` homes + moves the turret to `DEFAULT_OBJECTIVE`.
- `application.py` — `_shutdown_hardware()` closes the turret (release serial port, de-energize).
- `ui/widgets/hardware/objectives.py` — unified `move_to_objective` with `KeyError` warn + dropdown revert.
- `ui/main_window.py` — "Reset Objective Turret" Settings action (gated) + `resetObjectiveTurret()`.

## Architecture Adaptations

- **Locations:** `control/*` → `squid.backend.drivers.peripherals.*`; tests → arch_v2 layout.
- **Imports:** `squid.abc`→`squid.core.abc`; `squid.logging`→`squid.core.logging`;
  `control._def`→`_def`. Stdlib `logging` in modbus_rtu → `squid.core.logging`.
- **Sim class name:** `ObjectiveTurret4PosController_Simulation` (underscore) to match arch_v2's
  `ObjectiveChanger2PosController_Simulation` (upstream used no underscore); ported tests updated.
- **Startup home:** arch_v2 doesn't home the changer at startup (Xeryon positions only via the
  widget's initial selection). New homing is scoped to the turret in `prepare_for_use()`;
  the Xeryon path is unchanged.
- **Shutdown:** turret `close()` added to `_shutdown_hardware()` (direct driver access; the
  service exposes no `close()`), parallel to the existing Xeryon block.
- **Reset action:** implemented as a direct-call method on `main_window`, matching upstream's
  `gui_hcs.py` and arch_v2's existing direct-driver pattern (the `ObjectiveChangerService` path
  expects ABC methods the 2-pos controller doesn't implement and is effectively unused).
- **Validation hook:** single call after both `.ini` and legacy `.txt` config branches.

## Tests

**Files:** 4 new (see above). **Count:** 38 tests, all passing.

Cover: Modbus CRC known vectors + round-trip + short-frame rejection, all frame builders
(FC03/04/06/16); turret sim open/home/move/alias/close/context-manager/after-close-raises and
Z retract+restore (with/without stage, HOMING_ENABLED_Z gating, aliased slots); Xeryon
`move_to_objective` dispatch to pos1/pos2 + short-circuit + unknown-name KeyError;
mutual-exclusion guard + positions-table shape.

Regression: ran factory/peripherals/GUI suites — 50 passed. The 3 `test_gui_core_widgets.py`
failures (template-multipoint `_event_bus`, live-control exposure, recording `base_path_is_set`)
are PRE-EXISTING — confirmed identical with this port's changes stashed.

## Audit

- [x] Logic matches upstream (drivers ported near-verbatim; only imports/logging/class-name adapted)
- [x] arch_v2 patterns followed (driver/factory/widget/menu/shutdown placement)
- [x] Tests added (38) and passing
- [x] No regression (pre-existing failures verified independent of this change)
- [ ] Real-hardware bench test (NiMotion turret + Xeryon) — NOT possible in dev env (no hardware)
- [ ] `black --check` — black not installed locally; line lengths verified ≤120 manually; CI enforces

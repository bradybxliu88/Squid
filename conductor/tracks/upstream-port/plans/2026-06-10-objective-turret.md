# Objective Turret (NiMotion 4-position, Modbus-RTU)

**Status:** COMPLETED (ported in `3950c1b7`)
**Started:** 2026-06-10
**Branch:** master (arch_v2)

## Upstream Commits

- [x] `cbdc84c5` - feat: integrate motorized 4-position objective turret (#528) → `3950c1b7`

## Context

Upstream adds a motorized 4-position objective turret (NiMotion RS-485 stepper over
Modbus-RTU) alongside the existing Xeryon 2-position changer. A microscope has exactly
one of turret / Xeryon / manual (mutual-exclusion guard). A unified `move_to_objective(name)`
interface is added to BOTH controllers so the widget and startup use one call path.

### arch_v2 mapping

| Upstream (monolithic `control/`) | arch_v2 (3-layer) |
|----------------------------------|-------------------|
| `control/modbus_rtu.py` | `backend/drivers/peripherals/modbus_rtu.py` |
| `control/objective_turret_controller.py` | `backend/drivers/peripherals/objective_turret.py` |
| `control/objective_changer_2_pos_controller.py` | `backend/drivers/peripherals/objective_changer.py` |
| `control/_def.py` | `src/_def.py` (top-level module) |
| `control/microscope.py` (addons construction) | `backend/microscope_factory.py` |
| `control/microscope.py` (startup home/move) | `backend/microscope.py` `MicroscopeAddons.prepare_for_use()` |
| `control/gui_hcs.py` (shutdown close) | `application.py` `_shutdown_hardware()` |
| `control/gui_hcs.py` `resetObjectiveTurret` | `ui/main_window.py` (direct driver call) |
| `main_hcs.py` Utils menu action | `ui/main_window.py` Settings menu |
| `control/widgets.py` ObjectivesWidget | `ui/widgets/hardware/objectives.py` |
| `tests/control/test_*` | `tests/unit/squid/backend/drivers/peripherals/` + config test |

### Key arch_v2 differences / decisions

- **Import adaptations:** `control.modbus_rtu`→`squid.backend.drivers.peripherals.modbus_rtu`;
  `import squid.abc`→`import squid.core.abc`; `import squid.logging`→`import squid.core.logging`;
  `from control._def`→`from _def`.
- **Sim class name:** use `ObjectiveTurret4PosController_Simulation` (underscore) to match
  arch_v2's existing `ObjectiveChanger2PosController_Simulation`. Upstream used no underscore;
  ported tests updated accordingly.
- **Startup home/position:** arch_v2 does NOT home the objective changer at startup today
  (Xeryon is positioned only as a side effect of the widget's initial `setCurrentText`). To
  stay minimal, scope new startup homing to the TURRET only, in `prepare_for_use()`:
  `home()` + `move_to_objective(DEFAULT_OBJECTIVE)`. Xeryon path unchanged.
- **Shutdown:** `_shutdown_hardware()` already has a `USE_XERYON` block. Add a parallel
  `USE_OBJECTIVE_TURRET` block that calls `addons.objective_changer.close()` directly
  (release serial port + de-energize). Direct driver access (service has no `close()`).
- **Reset menu action:** arch_v2's ObjectivesWidget and main_window already access the driver
  directly (the ObjectiveChangerService path expects ABC methods the 2-pos controller doesn't
  implement and is effectively unused). So implement `resetObjectiveTurret()` as a direct-call
  method on `main_window`, matching upstream's gui_hcs.py and arch_v2's existing direct pattern.
  Add the action to the Settings menu, gated on `USE_OBJECTIVE_TURRET`.
- **Widget:** replace the `USE_XERYON`/`moveToPosition*` block with `move_to_objective(name)` +
  `KeyError` handling (QMessageBox warn + revert dropdown via blockSignals). Keep the existing
  `ObjectiveChanged` publish.
- **`_def.py` validation hook:** call `_validate_objective_changer_flags(USE_XERYON,
  USE_OBJECTIVE_TURRET)` right after the machine-config `.ini` is applied (line ~1361 region).

## Implementation Checklist

### Phase 1: Drivers (testable)
- [x] Create `backend/drivers/peripherals/modbus_rtu.py` (port verbatim; logging → `squid.core.logging`)
- [x] Create `backend/drivers/peripherals/objective_turret.py` with `ObjectiveTurret4PosController`
      + `ObjectiveTurret4PosController_Simulation` (adapt imports/logging)
- [x] Add `move_to_objective(name)` to `ObjectiveChanger2PosController` + `_Simulation` in
      `backend/drivers/peripherals/objective_changer.py`

### Phase 2: Config (`_def.py`)
- [x] Add `USE_OBJECTIVE_TURRET`, `OBJECTIVE_TURRET_SERIAL_NUMBER`, `OBJECTIVE_TURRET_SLAVE_ID`,
      `OBJECTIVE_TURRET_BAUDRATE`, `OBJECTIVE_TURRET_POSITIONS` after the XERYON block
- [x] Add `_validate_objective_changer_flags(use_xeryon, use_turret)` function
- [x] Call the validation hook after machine-config load

### Phase 3: Wiring
- [x] `microscope_factory.py`: add `elif _def.USE_OBJECTIVE_TURRET:` construction branch
      (real vs sim via `objective_changer_simulated`)
- [x] `microscope.py`: import turret types under `USE_OBJECTIVE_TURRET`; relax
      `MicroscopeAddons.objective_changer` type hint; add turret home+position in `prepare_for_use()`
- [x] `application.py` `_shutdown_hardware()`: add `USE_OBJECTIVE_TURRET` close() block
- [x] `ui/widgets/hardware/objectives.py`: unified `move_to_objective` + KeyError revert
- [x] `ui/main_window.py`: `resetObjectiveTurret()` method + Settings-menu action (gated)

### Phase 4: Tests
- [x] `tests/unit/squid/backend/drivers/peripherals/test_modbus_rtu.py` (port; fix imports)
- [x] `tests/unit/squid/backend/drivers/peripherals/test_objective_turret.py` (port; sim class name)
- [x] `tests/unit/squid/backend/drivers/peripherals/test_objective_changer.py` (port dispatcher tests)
- [x] `tests/unit/squid/config/test_objective_changer_config.py` (mutual-exclusion + positions shape)
- [x] Run new tests + a broad `pytest -m "not slow"` sanity to ensure no regression

### Verification
- [x] `python -c "import _def"` clean with turret defaults (USE_OBJECTIVE_TURRET=False)
- [x] Factory builds turret sim when `USE_OBJECTIVE_TURRET=True` + simulated (smoke test)
- [x] Style: `black` not installed in dev env; verified manually that all added lines are ≤120 chars (the 4 over-120 lines flagged are pre-existing, not from this port). CI runs `black --check`.
- [x] Full unit suite passes (no regression)

## Notes

- Real-hardware paths (Modbus serial, real turret `__init__` calibration, startup homing,
  shutdown close) cannot be exercised in the dev env (no NiMotion hardware). Tests cover the
  simulation twin, pure Modbus frame/CRC logic, the Xeryon dispatcher, and the mutual-exclusion
  guard — matching upstream's test surface.
- Latent arch_v2 quirk (not fixed here): `_shutdown_hardware()` calls
  `objective_service.set_position(0)` for Xeryon, but the 2-pos controller has no `set_position`;
  the call is swallowed by try/except. Out of scope.

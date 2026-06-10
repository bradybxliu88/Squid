# Reconciliation: Feb-07 in-progress ports → ported

**Date:** 2026-06-10
**Status:** RECONCILED (bookkeeping only — no code changes)

## Context

Four upstream commits were implemented and committed to arch_v2 on 2026-02-07, but
their `upstream-status.yaml` entries were left at `in-progress` and the implementing
commits were created without `Ports-Upstream:` git trailers. This file reconciles the
tracker: the code is present and committed; only the status metadata was stale.

No source code was changed during this reconciliation.

## Upstream Commits Reconciled

| Upstream | Title | Implemented in (our hash) | Plan |
|----------|-------|---------------------------|------|
| `be019383` | refactor: Backpressure system lifecycle enforcement and test coverage (#488) | `9daf295b` | `plans/2026-02-07-backpressure-lifecycle.md` |
| `833ae523` | feat: Workflow Runner - Edit button and multiple Acquisition sequences (#493) | `ad6c2d64` | `plans/2026-02-07-workflow-runner-enhancements.md` |
| `644ac383` | feat: Open Chamber fluidics support and FluidicsWidget enhancements (#489) | `7fe26bfb` (+ abort in `9daf295b`) | `plans/2026-02-07-open-chamber-fluidics.md` |
| `88c0065c` | feat: Wellplate calibration improvements - center point method and parameter editing (#490) | `34ff57bc` | `plans/2026-02-07-wellplate-calibration.md` |

## Verification (code present in arch_v2)

- **be019383** — `backend/controllers/multipoint/backpressure.py` has `_BYTES_PER_MB`,
  `create_backpressure_values()`, `is_closed`, `_warn_if_closed`, monotonic clock,
  atomic `get_stats()`. Pre-warming deliberately not ported (arch_v2 worker creates
  runners internally).
- **833ae523** — `WorkflowLoadConfigRequest`/`WorkflowLoadConfigResponse` events exist in
  `backend/controllers/workflow_runner/state.py`; handled in
  `workflow_runner_controller.py` and `ui/widgets/workflow/workflow_runner_dialog.py`.
  `SequenceItem.config_path` present.
- **644ac383** — `backend/drivers/fluidics/fluidics.py` carries Open Chamber validation;
  FluidicsWidget Save Log + control-disable present; abort stops fluidics.
- **88c0065c** — `ui/widgets/wellplate/calibration.py` has `center_point_widget`,
  `toggle_calibration_method`, update-parameters-without-recalibration.

## Audit

- [x] Implementing commit identified for each upstream hash
- [x] Code confirmed present in arch_v2 working tree
- [x] YAML status flipped `in-progress` → `ported` with `ported_in` + notes
- [x] Plan files marked COMPLETED
- [ ] `Ports-Upstream:` trailers — NOT added (would require rewriting Feb-07 history;
      implementing hashes recorded in YAML `ported_in` and here instead).

## Note on `verify`

`upstream_tracking.py verify` reports "ported but no git trailer" for these (and others);
this is the known branch-drift noise documented in project memory (verify scans the stale
`multipoint-refactor` target while ports land on `master`). The `ported_in` field is the
authoritative link.

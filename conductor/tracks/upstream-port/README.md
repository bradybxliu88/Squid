# Upstream Commit Porting

Port commits from `upstream/master` (Cephla-Lab/Squid) to the current architecture branch.

## Current Status

| Metric | Count |
|--------|-------|
| **Total tracked** | 210 |
| **Ported** | 136 |
| **Skipped** | 64 |
| **Pending** | 8 |
| **In-progress** | 2 |

*Last updated: 2026-10-11*

### October 2026 batch

61 upstream commits (2026-06-13 → 2026-10-10) triaged; 18 ported plus the Z-clamp part of `8c62827a`.
See `plans/2026-10-11-oct-upstream-batch.md` and `commits/42-a4679733-oct-2026-upstream-batch.md`.

**Still open:**
- `3bcdca38` - feat: unify Mosaic + Plate View into single UnifiedMosaicWidget (#530)
- `dd0c4cd2` - fix(mosaic): keep Full View tiles across magnification changes (#566)
- `8c62827a` - fix(turret): prevent Z-restore timeout at startup; de-energize when idle (#567)
- `a518249e` - feat(turret): per-machine objective turret pulse offset (#575)
- `752c9bb8` - feat(live): add a Snap button for single-frame capture (#616)
- `f9305f9f` - fix(live): don't widen the panel for the Snap button; rename live button to Live/Stop (#620)
- `c05bef3a` - test: clean up simulated-acquisition output instead of littering /tmp (#640)
- `091bf4e8` - Pr turret (#609)
- `46a139b9` - Remove napari live view; port overexposure indicator and alignment (with auto-align) to the pyqtgraph viewer (#627)
- `a77cbac3` - fix(turret): a turret only homes with Z retracted (#702)

## CRITICAL: Semantic Ports Only

**NEVER merge or cherry-pick directly from upstream.** The arch_v2 architecture has diverged significantly:

- Different directory structure (3-layer: core/backend/ui)
- Different patterns (EventBus, Services, RLock threading)
- Different file locations and module organization

**All ports must be semantic reimplementations** - understand what the upstream commit does, then implement the equivalent functionality following arch_v2 patterns.

**Cutoff date: 2025-12-12** - Only commits on or after this date are tracked. Earlier commits are from before the arch_v2 divergence and are not relevant.

## Tracking System

**Canonical status is tracked in `upstream-status.yaml`** - a machine-parseable YAML file that is automatically maintained by the `/upstream-check` skill.

### Quick Commands

```bash
cd software

# Check current status
python tools/upstream_tracking.py summary

# Fetch upstream and discover new commits
python tools/upstream_tracking.py add-pending --fetch

# List pending commits
python tools/upstream_tracking.py list --status pending

# Verify consistency
python tools/upstream_tracking.py verify
```

### Status Types

| Status | Meaning |
|--------|---------|
| `pending` | Needs to be ported or explicitly skipped |
| `in-progress` | Currently being ported |
| `ported` | Successfully ported to arch_v2 |
| `skipped` | Intentionally not porting (with documented justification) |

## Workflow

Use the `/upstream-check` skill to manage upstream ports. The skill:

1. **Auto-discovers** new upstream commits and adds them as pending
2. **Tracks decisions** - every commit must be ported or skipped with justification
3. **Enforces conventions** - proper commit message format with trailers
4. **Verifies consistency** - checks that YAML and git history match

See `.claude/skills/upstream-check/SKILL.md` for detailed workflow.

## Git Commit Convention

### Port Commits

```
feat: Port <feature> from upstream (<hash>)

<description>

Ports-Upstream: <hash>
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

### Skip Documentation

```
docs(upstream): Skip <hash> - <reason>

<justification>

Skips-Upstream: <hash>
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

## Files

| File | Purpose |
|------|---------|
| `upstream-status.yaml` | **Canonical status** - auto-maintained by skill |
| `commits/` | Detailed analysis files for complex ports |
| `port-log.md` | **DEPRECATED** - legacy tracking (kept for history) |

## Key File Mappings

When porting, use these mappings to find the correct arch_v2 location:

| Upstream Path | arch_v2 Path |
|---------------|--------------|
| `control/widgets.py` | `src/squid/ui/widgets/<domain>/` |
| `control/gui_hcs.py` | `src/squid/ui/main_window.py` |
| `control/core/multi_point_controller.py` | `src/squid/backend/controllers/multipoint/` |
| `control/core/multi_point_worker.py` | `src/squid/backend/controllers/multipoint/` |
| `squid/logging.py` | `src/squid/core/logging.py` |
| `squid/camera/utils.py` | `src/squid/backend/drivers/cameras/` |
| `firmware/` | `firmware/` (direct copy) |

See `.claude/skills/upstream-check/MAPPING.md` for complete mapping reference.

## Recent Ports (2026-01-09 to 2026-01-12)

All ports audited and verified. **736 tests passed.**

| Our Commit | Feature | Upstream Commits | Tests | Status |
|------------|---------|------------------|-------|--------|
| f5130544 | Backpressure/RAM management | 081fd7e9, c28b372b, 97f85d1b, c3322bb1, e9c6249b, 6bffd2d3 | - | PASS |
| 6c9cb672 | Simulation/throttling UI | 5ad9252a, 57378358 | - | PASS |
| 6acb9b35 | Persistence features | fc57e3da, 98c7fbd6, cc205460 | - | PASS |
| 8e032023 | Acquisition YAML save/load | 88db4da8 | 19 | PASS |
| 47b385e0 | TCP YAML command | f8c05d0d | 15 | PASS |
| d9966007 | NDViewer tab | 4234d34b | 15 | PASS |
| b4aa7255 | Alignment button | 47e7aff7 | 24 | PASS |
| a8121d30 | Simulated disk I/O | b91694f1 | 18 | PASS |

**Total: 8 port commits covering 16 upstream commits**

### Architecture Adaptations

These ports demonstrate proper arch_v2 patterns:

- **TCP YAML command**: Uses EventBus commands instead of direct widget manipulation
- **NDViewer tab**: Uses event subscription instead of controller polling
- **Alignment button**: Uses Signals for decoupled widget communication
- **Backpressure suite**: Uses multiprocessing primitives for cross-process job tracking
- **Simulated disk I/O**: Placed in `backend/io/` with lazy imports to avoid circular deps
- **Acquisition YAML**: Backend handles persistence, UI handles drag-drop loading

### Known Issues

1. **Missing tests for backpressure** - Upstream had 720 lines of tests; port needs dedicated test file
2. **Missing tests for persistence** - No tests for cache.py or settings_cache.py

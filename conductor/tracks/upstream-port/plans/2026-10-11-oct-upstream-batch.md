# October 2026 Upstream Batch (61 commits since 2026-06-13)

**Status:** COMPLETED (batch ported; turret series and feature ports remain pending)
**Started:** 2026-10-11
**Branch:** claude/eager-wozniak-u7wjyf (from master `9e1ddfb7`)

Triage of all 61 untracked upstream commits (`0a10ad1e`..`a77cbac3`) was done by
per-group analyst + adversarial verifier agents against the fork code. This plan
covers the commits approved for porting: general bug fixes (any hardware) plus
the user's hardware (laser reflection AF, Squid laser engine, Andor Dragonfly,
Hamamatsu ORCA). Turret (NiMotion) commits are on hold until the user confirms
whether their machine has one. No 1536-well plates, no emission filter wheel.

## Upstream Commits

### Port (general)
- [x] `5be72178` - fix: focus map KeyError on loaded coordinates, stage move timeouts 38-300x too long, focus-map double move
- [x] `8c62827a` - fix(turret): prevent Z-restore timeout at startup (**Z clamp part only**; turret de-energize deferred with turret series)
- [x] `0f789a45` - fix(stage): tolerate a corrupted cache/last_coords.txt at startup
- [x] `f8fe36d9` - fix(camera): don't reuse a pre-trigger frame in DefaultCamera.read_camera_frame
- [x] `e34dec3d` - refactor(camera): snapshot cached frame in reuse guard
- [x] `e86a608f` - fix(camera): prevent intermittent all-zero ToupCam frames
- [x] `7385a179` - fix(live): hold live triggers during stage motion (parts 1, 3, 5)
- [x] `f3bca2cb` - fix(af): LAPE focus measure without int16 overflow
- [x] `ee7897c9` - fix: skip focus-map interpolation for single-FOV snap acquisitions
- [x] `24276973` - fix(channel-config): persist settings for configurator-added channels
- [x] `edc13253` - fix(illumination): apply config edits without restart; per-channel max output cap
- [x] `74824168` - fix(prefs): read ini values with inline comments
- [x] `aa9107d9` - fix file format menu and enable TIFF compression

### Port (user hardware)
- [x] `6919470e` - feat: per-channel z-offset for laser AF (**parts 1+2**: AF-failure bool + worker robustness; capture UI deferred)
- [x] `2a550c86` - fix(laser-af): .ini crop sizes + unknown-key check (part 1 already present)
- [x] `1f4130f2` - fix(laser-engine): bound serial write_timeout
- [x] `432772be` - fix(dragonfly): make the Andor Dragonfly work (applicable parts)
- [x] `875eda71` - feat(camera): Hamamatsu sensor mode (READOUTSPEED)
- [x] `56db6965` - feat(camera): persist sensor mode in camera settings cache (+ empty-cache guard)

### Fork-only fixes (no upstream commit)
- [x] `CephlaStage.home()` NameError: `squid` never bound in `backend/drivers/stages/cephla.py` (fbf5a424)
- [x] `AbstractStage.get_config()` missing → every blocking Cephla stage move raised AttributeError (a4679733)
- [x] Stray `f` import from `_def` in `wellplate_multipoint` → ImportError on fresh install (d8f9a209)
- [x] Preferences Save crashed on a nonexistent simulation checkbox (64aed6fb)
- [x] Controller kept a stale by-region focus map after "Use Focus Map" unchecked (b085d67c)

### On hold (turret — awaiting user)
`8c62827a` (turret parts), `a518249e`, `091bf4e8`, `a77cbac3`

### Not in this batch (feature ports, separate decision)
`752c9bb8` Snap button, `f9305f9f`, `46a139b9` overexposure/auto-align, `dd0c4cd2` + finishing `3bcdca38`

## Implementation Units

Each unit is ported on its own branch, reviewed adversarially, then cherry-picked here.

- [x] U1 stage: fork NameError fix; `5be72178`; Z clamp from `8c62827a`; `0f789a45`
- [x] U2 camera frames: `f8fe36d9` + `e34dec3d`; `e86a608f`
- [x] U3 Hamamatsu sensor mode: `875eda71` + `56db6965`
- [x] U4 live/MCU busy: `7385a179` parts 1, 3, 5
- [x] U5 autofocus: `f3bca2cb`; `2a550c86`; `6919470e` parts 1+2; `ee7897c9`
- [x] U6 preferences/saving: `74824168`; `aa9107d9`
- [x] U7 channel/illumination config: `24276973`; `edc13253`
- [x] U8 peripherals: `1f4130f2`; `432772be`

### Tests
- [x] Each unit adds unit tests mirroring upstream's where they adapt
- [ ] Full `pytest tests/unit` after integration; compare against baseline failures

### Tracking
- [x] Record all 61 commits in `upstream-status.yaml` (ported / skipped with justification / pending)
- [x] `commits/42-a4679733-oct-2026-upstream-batch.md`
- [x] Trailers checked for all 18 new ported entries (`tools/upstream_tracking.py verify` itself compares 7-char git hashes against 8-char status keys, so it flags every ported entry — pre-existing tool bug)

## Notes

- Fork `firmware/` is a stale snapshot of upstream `6651393e` (#401) + def headers; several
  "ported" firmware commits in the YAML only had their host side ported. Firmware commits
  in this batch are skipped (no Teensy to test; user does not use a filter wheel).
- `594440a3` (Dragonfly configurator wheels / Refresh) skipped: the fork never applies
  confocal wheel positions per channel, so listing them would let users set values nothing uses.
- `8bf3c7d0` (Hamamatsu frame stamps) skipped: no fork consumer checks frame-id continuity.

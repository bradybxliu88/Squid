# Tucsen Callback-Based Frame Delivery

**Our Commit:** 47ef3027
**Date:** 2026-06-09
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| cf4bba2a | feat(tucsen): callback-based frame delivery (TUCAM_Buf_DataCallBack) (#552) |

## Summary

Ports the Tucsen camera driver from a polling read-thread (looping on
`TUCAM_Buf_WaitForFrame`) to a registered TUCam buffer callback
(`TUCAM_Buf_DataCallBack`). Frames are delivered on the SDK's internal thread
as soon as they arrive. arch_v2's `tucsen.py` had the identical pre-fix
structure and `tucam_sdk.py` already exposed every required binding, so this is
a faithful structural port.

## Files Created/Modified

### Created
- `software/tests/unit/squid/backend/drivers/cameras/__init__.py`
- `software/tests/unit/squid/backend/drivers/cameras/test_tucsen.py` (74 lines) —
  ported from upstream; covers `_convert_header_to_numpy`.

### Modified
- `software/src/squid/backend/drivers/cameras/tucsen.py` — callback delivery
  (see Summary). Imports: added `BUFFER_CALLBACK`, `TUCAM_Buf_DataCallBack`,
  `TUCAM_Buf_GetData`, `TUCAM_RAWIMG_HEADER`; dropped now-dead
  `TUCAM_Buf_WaitForFrame`, `c_int32`.

## Architecture Adaptations

None structurally — arch_v2's driver mirrored upstream's pre-fix layout and the
SDK wrapper (`tucam_sdk.py`) already had the callback bindings (`BUFFER_CALLBACK`,
`TUCAM_Buf_DataCallBack`, `TUCAM_Buf_GetData`, `TUCAM_RAWIMG_HEADER`,
`TUCAM_Buf_AbortWait`). Only the upstream module path (`control/camera_tucsen.py`,
`control/TUCam.py`) was remapped to arch_v2 (`backend/drivers/cameras/tucsen.py`,
`tucam_sdk.py`) and the test imports adjusted (`squid.core.abc.CameraError`).

## Tests

**File:** `tests/unit/squid/backend/drivers/cameras/test_tucsen.py`
**Count:** 4 tests (module-level skip when the TUCam SDK `.so`/`.dll` is absent).

Covers `_convert_header_to_numpy`:
- pixel roundtrip
- copy semantics (array owns its data; survives source-buffer mutation)
- short-buffer rejection (`uiImgSize < w*h*2`)
- padded-frame rejection (`usXPadding`/`usYPadding`)

Verification: the converter logic was also validated standalone (roundtrip,
copy, short, padding all correct). The driver's hardware path is untestable
here (no Tucsen camera / no `libTUCam.so.1`); the test + driver both skip
cleanly without the SDK, matching CI behavior.

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed (SDK bindings already present)
- [x] Tests added (ported + standalone-validated converter)
- [x] black-clean (added lines)
- [ ] Hardware validation (needs a Tucsen camera; runs on CI with SDK)

# Camera Drivers (Suite E)

**Status:** COMPLETED (cf4bba2a → 47ef3027; a894eda5 → 4658668d)
**Started:** 2026-06-09
**Completed:** 2026-06-09

## Upstream Commits

- [x] `cf4bba2a` - feat(tucsen): callback-based frame delivery (TUCAM_Buf_DataCallBack) (#552) — ported 47ef3027
- [x] `a894eda5` - fix: legacy RecordingWidget + Toupcam continuous-mode FPS + Live illumination (#535) — ported 4658668d (all 3 parts; Toupcam debug instrumentation omitted)

## cf4bba2a — Tucsen callback frame delivery

Converts the Tucsen driver from a polling read-thread
(`_wait_for_frame` looping on `TUCAM_Buf_WaitForFrame`) to a registered SDK
buffer callback (`TUCAM_Buf_DataCallBack`). arch_v2's `tucsen.py` has the
identical pre-fix structure and `tucam_sdk.py` already exposes all required
bindings (`BUFFER_CALLBACK`, `TUCAM_Buf_DataCallBack`, `TUCAM_Buf_GetData`,
`TUCAM_RAWIMG_HEADER`), so this ports cleanly.

### Implementation Checklist
- [x] Imports: add `TUCAM_Buf_DataCallBack`, `TUCAM_Buf_GetData`,
      `TUCAM_RAWIMG_HEADER`, `BUFFER_CALLBACK`; drop now-dead
      `TUCAM_Buf_WaitForFrame`, `c_int32`.
- [x] `__init__`: drop `_read_thread*` state; add
      `self._frame_callback_fn = BUFFER_CALLBACK(self._on_frame_callback)`
      (keep CFUNCTYPE alive so the SDK's borrowed pointer stays valid).
- [x] `start_streaming`: register callback (per-cycle) right before `Cap_Start`;
      on failure release buffer + reset `_m_frame=None` so retry re-allocates.
- [x] `stop_streaming`: clear `_is_streaming`/`_trigger_sent` first, then vendor
      order `Buf_AbortWait → Cap_Stop`.
- [x] `close`: best-effort `stop_streaming()` first (a stop failure must not
      abort handle/SDK teardown).
- [x] Replace `_wait_for_frame`/`_ensure_read_thread_running`/
      `_cleanup_read_thread`/`_convert_frame_to_numpy` with `_on_frame_callback`
      + static `_convert_header_to_numpy` (rejects padded frames + size check).
- [x] `read_camera_frame`: drop the `_read_thread_running` check.
- [x] `_update_internal_settings`: hold `_frame_lock` across `Buf_Release` +
      `_allocate_buffer` (callback holds the same lock to read pImgData).

### Tests
- [x] Port upstream `test_camera_tucsen.py` → arch_v2 path; covers
      `_convert_header_to_numpy` (valid frame, padded-frame rejection, size
      check). Skips without the SDK; converter logic validated standalone.
      Hardware path itself untestable (no Tucsen camera).

## a894eda5 — SEPARATE ANALYSIS (not in this batch)

Large multi-part commit (camera_toupcam +111, core.py +280, widgets,
gui_hcs, utils_acquisition, live_controller). Three independent concerns:
1. **Legacy RecordingWidget** `base_path_is_set` fix — arch_v2's
   `ui/widgets/camera/recording.py` already has the flag; needs check whether
   the init-path bug exists in arch_v2.
2. **ImageSaver rebuild** (cv2 + `frames.csv` sidecar + `channel_provider`) —
   arch_v2's `ui/image_saver.py` ALREADY uses `cv2.imwrite`; only the
   `frames.csv` metadata + channel tracking would be new.
3. **Toupcam continuous-mode FPS + live illumination** — needs arch_v2
   `toupcam.py` / `live_controller` assessment.

Recommend splitting into per-part decisions in a follow-up rather than a
wholesale port (arch_v2 has diverged implementations of RecordingWidget +
ImageSaver).

## Notes

Camera drivers can't be hardware-tested here; verification is import/compile +
the static-method unit test + structural fidelity to upstream.

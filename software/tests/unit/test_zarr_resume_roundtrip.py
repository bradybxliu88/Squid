"""B1.5 END-TO-END validation: resume-aware Zarr writer against REAL tensorstore.

These are the tests the tensorstore-free unit tests (test_zarr_resume_writer.py)
could not run: they actually open a tensorstore file kvstore, simulate a crash
(no finalize), reopen with allow_resume=True, and assert the create=False reopen
is NON-DESTRUCTIVE (already-written chunks survive) and that a completed store is
refused. This exercises the tensorstore codec/metadata byte-match risk directly.

Skipped automatically when tensorstore is not installed.
"""

import numpy as np
import pytest

pytest.importorskip("tensorstore")

from squid.backend.io.writers.zarr_writer import (  # noqa: E402
    RefuseToOverwriteError,
    ZarrAcquisitionConfig,
    ZarrWriter,
    build_per_fov_zarr_path,
)

SHAPE = (2, 1, 1, 4, 4)  # T, C, Z, Y, X  (2 timepoints, 1 channel, 1 z)
DTYPE = np.uint16


def _out_path(tmp_path):
    # Realistic non-HCS per-FOV array path: .../fov_0.ome.zarr/0 (group metadata
    # with the _squid ledger lives at the parent fov_0.ome.zarr/zarr.json).
    import os

    return os.path.join(build_per_fov_zarr_path(str(tmp_path), "region_0", 0), "0")


def _config(tmp_path, *, allow_resume=False, force_overwrite=False, shape=SHAPE, dtype=DTYPE):
    return ZarrAcquisitionConfig(
        output_path=_out_path(tmp_path),
        shape=shape,
        dtype=np.dtype(dtype),
        pixel_size_um=0.5,
        channel_names=["BF"],
        allow_resume=allow_resume,
        force_overwrite=force_overwrite,
    )


def _read(writer, t, c, z):
    return np.asarray(writer._dataset[t, c, z, :, :].read().result())


def test_resume_is_non_destructive_and_continues(tmp_path):
    frame_t0 = np.full((4, 4), 7, dtype=DTYPE)
    frame_t1 = np.full((4, 4), 9, dtype=DTYPE)

    # --- Pre-crash writer: write t=0, then "crash" (never finalize). ---
    w1 = ZarrWriter(_config(tmp_path))
    w1.initialize()
    w1.write_frame(frame_t0, t=0, c=0, z=0)
    w1.wait_for_pending()
    w1._cleanup_event_loop()
    del w1

    # --- Resume writer: allow_resume must open create=False (no wipe). ---
    w2 = ZarrWriter(_config(tmp_path, allow_resume=True))
    w2.initialize()
    # The pre-crash plane must SURVIVE the reopen (delete_existing would zero it).
    assert np.array_equal(_read(w2, 0, 0, 0), frame_t0), "resume wiped pre-crash data!"
    # Continue the acquisition and finalize.
    w2.write_frame(frame_t1, t=1, c=0, z=0)
    w2.finalize()
    assert np.array_equal(_read(w2, 0, 0, 0), frame_t0)
    assert np.array_equal(_read(w2, 1, 0, 0), frame_t1)
    w2._cleanup_event_loop()


def test_resume_refuses_completed_store(tmp_path):
    frame = np.full((4, 4), 3, dtype=DTYPE)
    w1 = ZarrWriter(_config(tmp_path))
    w1.initialize()
    w1.write_frame(frame, t=0, c=0, z=0)
    w1.finalize()  # marks acquisition_complete=True
    w1._cleanup_event_loop()
    del w1

    w2 = ZarrWriter(_config(tmp_path, allow_resume=True))
    with pytest.raises(RefuseToOverwriteError):
        w2.initialize()


def test_force_overwrite_bypasses_refuse(tmp_path):
    frame = np.full((4, 4), 3, dtype=DTYPE)
    w1 = ZarrWriter(_config(tmp_path))
    w1.initialize()
    w1.write_frame(frame, t=0, c=0, z=0)
    w1.finalize()
    w1._cleanup_event_loop()
    del w1

    # force_overwrite permits clobbering a completed store (fresh create).
    w2 = ZarrWriter(_config(tmp_path, allow_resume=True, force_overwrite=True))
    w2.initialize()
    # Fresh store -> the old plane is gone (fill_value 0).
    assert int(_read(w2, 0, 0, 0).max()) == 0
    w2._cleanup_event_loop()


def test_resume_shape_mismatch_raises(tmp_path):
    frame = np.full((4, 4), 3, dtype=DTYPE)
    w1 = ZarrWriter(_config(tmp_path))
    w1.initialize()
    w1.write_frame(frame, t=0, c=0, z=0)
    w1.wait_for_pending()  # incomplete (no finalize)
    w1._cleanup_event_loop()
    del w1

    # A resume whose expected shape differs from the on-disk ledger must refuse.
    w2 = ZarrWriter(_config(tmp_path, allow_resume=True, shape=(5, 1, 1, 4, 4)))
    with pytest.raises(RuntimeError, match="shape mismatch"):
        w2.initialize()

"""B1.4 tests: resume-aware OME-TIFF writer helpers.

Unit coverage for the pieces that make OME resume non-destructive: the relocated
+ atomic plane tracker, the shape/dtype adopt gate, the written-plane rebuild
from disk, the shared MULTI_PAGE stack path, and the resume-safe legacy janitor.
(The full resume flow through SaveOMETiffJob needs a real acquisition and is
validated on the sim/hardware; OME resume stays GUI-gated until then.)
"""

import json
import os
import tempfile
from types import SimpleNamespace

import numpy as np
import tifffile

from squid.backend.io.writers import utils_ome_tiff_writer as ome
from squid.backend.controllers.multipoint import job_processing


def _acq(experiment_path):
    return SimpleNamespace(experiment_path=experiment_path)


def _info(save_directory, region_id="A1", fov=3):
    return SimpleNamespace(save_directory=save_directory, region_id=region_id, fov=fov)


def _make_ome_stack(path, *, shape=(1, 2, 1, 4, 4), dtype=np.uint16, written=()):
    tifffile.imwrite(path, shape=shape, dtype=dtype, ome=True)
    arr = tifffile.memmap(path, mode="r+")
    for (t, z, c) in written:
        arr[t, z, c] = 7
    arr.flush()
    ome._close_memmap(arr)


def test_plane_tracker_path_colocated_with_data(tmp_path):
    acq = _acq(str(tmp_path / "exp"))
    info = _info(str(tmp_path / "exp" / "0"))
    p = ome.plane_tracker_path(acq, info, "A1_0003")
    assert p == os.path.join(
        str(tmp_path / "exp"), "ome_tiff", ".plane_trackers", "A1_0003_metadata.json"
    )


def test_metadata_temp_path_unchanged(tmp_path):
    acq = _acq(str(tmp_path / "exp"))
    info = _info(str(tmp_path / "exp" / "0"))
    p = ome.metadata_temp_path(acq, info, "A1_0003")
    assert p.startswith(tempfile.gettempdir())
    assert os.path.basename(p).startswith("squid_ome_")
    assert p.endswith("_metadata.json")


def test_write_metadata_atomic_roundtrip_creates_parent(tmp_path):
    path = str(tmp_path / "sub" / ".plane_trackers" / "x_metadata.json")
    ome.write_metadata(path, {"a": 1, ome.WRITTEN_INDICES_KEY: ["0-0-0"]})
    loaded = ome.load_metadata(path)
    assert loaded["a"] == 1
    assert loaded[ome.WRITTEN_INDICES_KEY] == ["0-0-0"]
    # No torn temp files left behind by the atomic write.
    leftovers = [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")]
    assert leftovers == []


def test_memmap_matches_shape_and_dtype(tmp_path):
    path = str(tmp_path / "s.ome.tiff")
    _make_ome_stack(path, shape=(1, 2, 1, 4, 4), dtype=np.uint16)
    assert ome.memmap_matches(path, [1, 2, 1, 4, 4], np.uint16) is True
    assert ome.memmap_matches(path, [1, 3, 1, 4, 4], np.uint16) is False
    assert ome.memmap_matches(path, [1, 2, 1, 4, 4], np.uint8) is False


def test_memmap_matches_missing_file_is_false(tmp_path):
    assert ome.memmap_matches(str(tmp_path / "nope.ome.tiff"), [1, 1, 1, 4, 4], np.uint16) is False


def test_rebuild_written_indices_from_nonzero_planes(tmp_path):
    path = str(tmp_path / "s.ome.tiff")
    # T=1, Z=2, C=1: write only plane (t=0, z=0, c=0); leave (0,1,0) zero.
    _make_ome_stack(path, shape=(1, 2, 1, 4, 4), dtype=np.uint16, written=((0, 0, 0),))
    meta = {ome.SHAPE_KEY: [1, 2, 1, 4, 4], ome.WRITTEN_INDICES_KEY: [], ome.SAVED_COUNT_KEY: 0}
    ome.rebuild_written_indices_from_memmap(path, meta)
    # Key order is T-C-Z.
    assert meta[ome.WRITTEN_INDICES_KEY] == ["0-0-0"]
    assert meta[ome.SAVED_COUNT_KEY] == 1


def test_multipage_stack_path_matches_convention(tmp_path):
    import _def

    info = _info(str(tmp_path), region_id="A1", fov=3)
    p = job_processing.multipage_stack_path(info)
    assert p == os.path.join(str(tmp_path), f"A1_{3:0{_def.FILE_ID_PADDING}}_stack.tiff")


def test_cleanup_keeps_incomplete_removes_completed(tmp_path, monkeypatch):
    # Isolate the OS-temp glob to this tmp_path.
    monkeypatch.setattr(ome.tempfile, "gettempdir", lambda: str(tmp_path))
    completed = tmp_path / "squid_ome_aaa_metadata.json"
    completed.write_text(json.dumps({ome.COMPLETED_KEY: True}))
    incomplete = tmp_path / "squid_ome_bbb_metadata.json"
    incomplete.write_text(json.dumps({ome.COMPLETED_KEY: False}))

    removed = ome.cleanup_stale_metadata_files()

    assert str(completed) in removed
    assert not completed.exists()
    # Incomplete tracker is resume fuel -> KEPT.
    assert incomplete.exists()

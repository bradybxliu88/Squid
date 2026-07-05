"""B1.5 tests: resume-aware Zarr v3 writer (tensorstore-free parts).

tensorstore is not installed in CI, so the actual create=False reopen is
validated on the sim/hardware and Zarr resume stays GUI-gated. Here we cover the
safety-critical, tensorstore-free logic: the resume-precondition guard
(refuse-completed / shape+dtype mismatch), the prior-ledger read, the resume
flag defaults + plumbing, and the C4 derivation from _start_fov_index.
"""

import json
import logging
import os

import numpy as np
import pytest

from squid.backend.io.writers.zarr_writer import (
    RefuseToOverwriteError,
    ZarrAcquisitionConfig,
    ZarrWriter,
)
from squid.backend.controllers.multipoint.job_processing import ZarrWriterInfo


def _config(tmp_path, *, allow_resume=False, force_overwrite=False, shape=(2, 1, 3, 4, 4), dtype=np.uint16):
    return ZarrAcquisitionConfig(
        output_path=str(tmp_path / "fov.ome.zarr" / "0"),
        shape=shape,
        dtype=np.dtype(dtype),
        pixel_size_um=0.5,
        allow_resume=allow_resume,
        force_overwrite=force_overwrite,
    )


def test_refuse_error_is_runtime_error():
    assert issubclass(RefuseToOverwriteError, RuntimeError)


def test_config_resume_flags_default_false(tmp_path):
    cfg = _config(tmp_path)
    assert cfg.allow_resume is False
    assert cfg.force_overwrite is False


def test_writer_info_resume_flags_default_false():
    info = ZarrWriterInfo(base_path="/tmp", t_size=1, c_size=1, z_size=1)
    assert info.allow_resume is False
    assert info.force_overwrite is False


def test_precondition_refuses_completed_store(tmp_path):
    writer = ZarrWriter(_config(tmp_path, allow_resume=True))
    prior = {"acquisition_complete": True, "shape": [2, 1, 3, 4, 4], "dtype": "uint16"}
    with pytest.raises(RefuseToOverwriteError):
        writer._check_resume_preconditions(prior)


def test_precondition_force_overwrite_allows_completed(tmp_path):
    writer = ZarrWriter(_config(tmp_path, allow_resume=True, force_overwrite=True))
    prior = {"acquisition_complete": True, "shape": [2, 1, 3, 4, 4], "dtype": "uint16"}
    # Should NOT raise -- force_overwrite explicitly permits clobbering.
    writer._check_resume_preconditions(prior)


def test_precondition_incomplete_matching_ok(tmp_path):
    writer = ZarrWriter(_config(tmp_path, allow_resume=True))
    prior = {"acquisition_complete": False, "shape": [2, 1, 3, 4, 4], "dtype": "uint16"}
    writer._check_resume_preconditions(prior)  # matching geometry -> no raise


def test_precondition_shape_mismatch_raises(tmp_path):
    writer = ZarrWriter(_config(tmp_path, allow_resume=True, shape=(2, 1, 3, 4, 4)))
    prior = {"acquisition_complete": False, "shape": [9, 1, 3, 4, 4], "dtype": "uint16"}
    with pytest.raises(RuntimeError, match="shape mismatch"):
        writer._check_resume_preconditions(prior)


def test_precondition_dtype_mismatch_raises(tmp_path):
    writer = ZarrWriter(_config(tmp_path, allow_resume=True, dtype=np.uint16))
    prior = {"acquisition_complete": False, "shape": [2, 1, 3, 4, 4], "dtype": "uint8"}
    with pytest.raises(RuntimeError, match="dtype mismatch"):
        writer._check_resume_preconditions(prior)


def test_read_prior_squid_absent_is_none(tmp_path):
    writer = ZarrWriter(_config(tmp_path, allow_resume=True))
    assert writer._read_prior_squid(str(tmp_path / "nope" / "zarr.json")) is None


def test_read_prior_squid_returns_block(tmp_path):
    writer = ZarrWriter(_config(tmp_path, allow_resume=True))
    zjson = tmp_path / "zarr.json"
    zjson.write_text(
        json.dumps(
            {
                "zarr_format": 3,
                "node_type": "group",
                "attributes": {"_squid": {"acquisition_complete": True, "shape": [2, 1, 3, 4, 4]}},
            }
        )
    )
    block = writer._read_prior_squid(str(zjson))
    assert block["acquisition_complete"] is True
    assert block["shape"] == [2, 1, 3, 4, 4]


def test_read_prior_squid_tolerates_garbage(tmp_path):
    writer = ZarrWriter(_config(tmp_path, allow_resume=True))
    zjson = tmp_path / "zarr.json"
    zjson.write_text("{ not json")
    assert writer._read_prior_squid(str(zjson)) is None


def test_set_start_fov_index_derives_allow_resume():
    # C4: allow_resume is derived from _start_fov_index>0 by mutating the shared
    # ZarrWriterInfo. Exercise the method against a minimal stand-in self so we
    # avoid constructing a full MultiPointWorker.
    from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker

    class _Stub:
        def __init__(self, zwi):
            self._zarr_writer_info = zwi
            self._start_fov_index = 0
            self._log = logging.getLogger("test")

    zwi = ZarrWriterInfo(base_path="/tmp", t_size=1, c_size=1, z_size=1)
    stub = _Stub(zwi)

    MultiPointWorker.set_start_fov_index(stub, 3)
    assert stub._start_fov_index == 3
    assert stub._zarr_writer_info.allow_resume is True

    MultiPointWorker.set_start_fov_index(stub, 0)
    assert stub._zarr_writer_info.allow_resume is False

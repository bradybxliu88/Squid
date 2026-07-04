"""Unit tests for atomic_write_json (B1.0 atomic-write foundation).

These are the B1.1 "MID-WRITE" crash-injection fault point at the unit level:
they drive the fault seam (or monkeypatch os.replace) to prove that an
interrupted write never leaves a torn file and always leaves the prior good
content -- or nothing -- behind.
"""

import json
import os

import pytest

from squid.core.utils import atomic_io
from squid.core.utils.atomic_io import atomic_write_json


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_fault_seam():
    """Ensure the test-only fault seam never leaks across tests."""
    atomic_io._FAULT_BEFORE_REPLACE = None
    yield
    atomic_io._FAULT_BEFORE_REPLACE = None


def _raise_power_loss():
    raise RuntimeError("simulated power loss before replace")


def _tmp_files(d):
    return [f for f in os.listdir(d) if f.endswith(".tmp")]


# --- happy path -----------------------------------------------------------


def test_writes_valid_json(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1, "b": [1, 2]})
    assert json.loads(p.read_text()) == {"a": 1, "b": [1, 2]}
    assert _tmp_files(tmp_path) == []


def test_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "deep" / "x.json"
    atomic_write_json(p, {"ok": True})
    assert json.loads(p.read_text()) == {"ok": True}


def test_sort_keys(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"b": 1, "a": 2}, sort_keys=True)
    text = p.read_text()
    assert text.index('"a"') < text.index('"b"')


def test_overwrite_is_all_or_nothing(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"v": "A"})
    atomic_write_json(p, {"v": "B"})
    assert json.loads(p.read_text()) == {"v": "B"}


# --- MID-WRITE fault point ------------------------------------------------


def test_torn_write_leaves_prior_content_intact(tmp_path):
    """A crash between temp-write and replace must leave the previous complete
    file untouched (never a torn/partial file), with no leftover temp files."""
    p = tmp_path / "x.json"
    atomic_write_json(p, {"v": "A"})

    atomic_io._FAULT_BEFORE_REPLACE = _raise_power_loss
    with pytest.raises(RuntimeError, match="power loss"):
        atomic_write_json(p, {"v": "B"})

    assert json.loads(p.read_text()) == {"v": "A"}
    assert _tmp_files(tmp_path) == []


def test_torn_first_write_leaves_no_target(tmp_path):
    """If the very first write is interrupted, no partial target is created."""
    p = tmp_path / "x.json"
    atomic_io._FAULT_BEFORE_REPLACE = _raise_power_loss
    with pytest.raises(RuntimeError):
        atomic_write_json(p, {"v": "A"})
    assert not p.exists()
    assert _tmp_files(tmp_path) == []


def test_replace_failure_cleans_temp(tmp_path, monkeypatch):
    """Monkeypatching os.replace to fail models the same window a different way."""
    p = tmp_path / "x.json"
    atomic_write_json(p, {"v": "A"})

    def failing_replace(*_a, **_k):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        atomic_write_json(p, {"v": "B"})

    # os.unlink is untouched, so the temp is still cleaned up.
    assert json.loads(p.read_text()) == {"v": "A"}
    assert _tmp_files(tmp_path) == []


def test_non_serializable_leaves_prior_intact(tmp_path):
    """A json.dump failure must not corrupt the existing file."""
    p = tmp_path / "x.json"
    atomic_write_json(p, {"v": "A"})
    with pytest.raises(TypeError):
        atomic_write_json(p, {"bad": object()})
    assert json.loads(p.read_text()) == {"v": "A"}
    assert _tmp_files(tmp_path) == []


# --- backup ---------------------------------------------------------------


def test_keep_backup_no_prior_writes_no_bak(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"v": "A"}, keep_backup=True)
    assert not (tmp_path / "x.json.bak").exists()


def test_keep_backup_preserves_previous_good(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"v": "A"}, keep_backup=True)          # no prior -> no bak
    ret = atomic_write_json(p, {"v": "B"}, keep_backup=True)    # prior A -> bak holds A
    bak = tmp_path / "x.json.bak"
    assert bak.exists()
    assert ret == str(bak)
    assert json.loads(bak.read_text()) == {"v": "A"}
    assert json.loads(p.read_text()) == {"v": "B"}

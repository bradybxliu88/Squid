"""Unit tests for the Qt-free protocol/config editor model."""

import json
from pathlib import Path

import pytest

from squid.ui.widgets.orchestrator import protocol_editor_model as m


# --------------------------------------------------------------- role inference
def test_role_for_path():
    assert m.role_for_path("proto.yaml") == m.ROLE_PROTOCOL
    assert m.role_for_path("my_fluidics_protocols.yaml") == m.ROLE_FLUIDICS_PROTOCOLS
    assert m.role_for_path("imaging_4ch.yaml") == m.ROLE_IMAGING
    assert m.role_for_path("MERFISH_config.json") == m.ROLE_FLUIDICS_CONFIG
    assert m.role_for_path("fov_positions.csv") == m.ROLE_FOV
    assert m.role_for_path("notes.txt") == m.ROLE_OTHER


# --------------------------------------------------------------- file discovery
def _make_bundle(tmp_path):
    proto = tmp_path / "protocol.yaml"
    fluidics = tmp_path / "fluidics.yaml"
    config = tmp_path / "MERFISH_config.json"
    fov = tmp_path / "fov.csv"
    imaging = tmp_path / "imaging_4ch.yaml"
    for p in (proto, fluidics, config, fov, imaging):
        p.write_text("x: 1\n")
    protocol_data = {
        "resources": {
            "fluidics_protocols_file": str(fluidics),
            "fluidics_config_file": str(config),
            "fov_file": str(fov),
        },
        "rounds": [
            {"steps": [
                {"step_type": "fluidics", "protocol": "wash"},
                {"step_type": "imaging", "protocol": "imaging_4ch.yaml"},
            ]},
        ],
    }
    return str(proto), protocol_data


def test_list_protocol_files_enumerates_bundle(tmp_path):
    proto_path, data = _make_bundle(tmp_path)
    files = m.list_protocol_files(proto_path, data)
    roles = {f.role for f in files}
    assert m.ROLE_PROTOCOL in roles
    assert m.ROLE_FLUIDICS_PROTOCOLS in roles
    assert m.ROLE_FLUIDICS_CONFIG in roles
    assert m.ROLE_FOV in roles
    assert m.ROLE_IMAGING in roles
    assert all(f.exists for f in files)
    # No duplicate paths
    paths = [f.path.lower() for f in files]
    assert len(paths) == len(set(paths))


def test_list_protocol_files_marks_missing(tmp_path):
    proto_path, data = _make_bundle(tmp_path)
    # Point the imaging step at a non-existent file
    data["rounds"][0]["steps"][1]["protocol"] = "does_not_exist.yaml"
    files = m.list_protocol_files(proto_path, data)
    imaging = [f for f in files if f.role == m.ROLE_IMAGING][0]
    assert imaging.exists is False


def test_list_protocol_files_handles_none():
    assert m.list_protocol_files(None, None) == []


# --------------------------------------------------------------------- read_file
def test_read_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        m.read_file(str(tmp_path / "nope.yaml"))


def test_read_file_rejects_binary(tmp_path):
    p = tmp_path / "b.bin"
    p.write_bytes(b"abc\x00def")
    with pytest.raises(ValueError):
        m.read_file(str(p))


def test_read_file_rejects_oversized(tmp_path, monkeypatch):
    p = tmp_path / "big.yaml"
    p.write_text("hello world")
    monkeypatch.setattr(m, "MAX_EDIT_BYTES", 3)
    with pytest.raises(ValueError):
        m.read_file(str(p))


def test_read_file_ok(tmp_path):
    p = tmp_path / "ok.yaml"
    p.write_bytes(b"name: t\n")  # exact bytes; read_file preserves line endings
    assert m.read_file(str(p)) == "name: t\n"


# ------------------------------------------------------------------- validation
VALID_PROTOCOL = """\
name: T
version: "3.0"
rounds:
  - name: R1
    steps:
      - step_type: fluidics
        protocol: wash
"""


def test_validate_protocol_valid(tmp_path):
    ok, errors, _ = m.validate_content(m.ROLE_PROTOCOL, VALID_PROTOCOL, str(tmp_path))
    assert ok is True
    assert errors == []


def test_validate_protocol_bad_yaml():
    ok, errors, _ = m.validate_content(m.ROLE_PROTOCOL, ":\n  - : ][")
    assert ok is False
    assert errors


def test_validate_protocol_schema_error(tmp_path):
    # Parseable YAML, but no rounds -> schema error (still parseable => ok True)
    ok, errors, _ = m.validate_content(m.ROLE_PROTOCOL, "name: T\n", str(tmp_path))
    assert ok is True
    assert errors


def test_validate_fluidics_config_valid():
    cfg = {
        "microcontroller": {"serial_number": "X"},
        "syringe_pump": {"volume_ul": 5000},
        "selector_valves": {},
        "solution_port_mapping": {"Wash Buffer": 26},
    }
    ok, errors, _ = m.validate_content(m.ROLE_FLUIDICS_CONFIG, json.dumps(cfg))
    assert ok is True
    assert errors == []


def test_validate_fluidics_config_bad_json():
    ok, errors, _ = m.validate_content(m.ROLE_FLUIDICS_CONFIG, "{not json")
    assert ok is False
    assert errors


def test_validate_fluidics_config_missing_section():
    ok, errors, _ = m.validate_content(m.ROLE_FLUIDICS_CONFIG, json.dumps({"microcontroller": {}}))
    assert ok is True
    assert any("Missing required config section" in e for e in errors)


def test_validate_fov_valid():
    ok, errors, _ = m.validate_content(m.ROLE_FOV, "region,x_mm,y_mm,z_mm\n5,1.0,2.0,0.0\n")
    assert ok is True
    assert errors == []


def test_validate_fov_missing_columns():
    ok, errors, _ = m.validate_content(m.ROLE_FOV, "a,b,c\n1,2,3\n")
    assert ok is True
    assert errors


def test_validate_fov_empty():
    ok, errors, _ = m.validate_content(m.ROLE_FOV, "   \n")
    assert ok is False


def test_validate_fluidics_protocols_valid():
    text = (
        "protocols:\n"
        "  wash:\n"
        "    steps:\n"
        "      - operation: wash\n"
        "        solution: Wash Buffer\n"
        "        volume_ul: 1000\n"
        "        flow_rate_ul_per_min: 300\n"
    )
    ok, errors, _ = m.validate_content(m.ROLE_FLUIDICS_PROTOCOLS, text)
    assert ok is True
    assert errors == []


def test_validate_imaging_broken_structure():
    ok, errors, _ = m.validate_content(m.ROLE_IMAGING, "acquisition: [not, a, mapping]\n")
    assert ok is True
    assert errors


# -------------------------------------------------------------------- safe_write
def test_safe_write_refuses_when_run_active(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("original\n")
    with pytest.raises(RuntimeError):
        m.safe_write(str(p), "new content", run_active=True)
    assert p.read_text() == "original\n"  # untouched


def test_safe_write_new_file_no_backup(tmp_path):
    p = tmp_path / "new.yaml"
    backup = m.safe_write(str(p), "hello\n", run_active=False)
    assert backup is None
    assert p.read_text() == "hello\n"
    assert not (tmp_path / "new.yaml.bak").exists()
    assert not (tmp_path / "new.yaml.tmp").exists()


def test_safe_write_existing_file_makes_backup(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("old\n")
    backup = m.safe_write(str(p), "new\n", run_active=False)
    assert backup == str(p) + ".bak"
    assert Path(backup).read_text() == "old\n"
    assert p.read_text() == "new\n"
    assert not (tmp_path / "p.yaml.tmp").exists()  # atomic: no temp left behind


def test_safe_write_none_text(tmp_path):
    with pytest.raises(ValueError):
        m.safe_write(str(tmp_path / "p.yaml"), None, run_active=False)


def test_safe_write_missing_dir(tmp_path):
    with pytest.raises(ValueError):
        m.safe_write(str(tmp_path / "nope" / "p.yaml"), "x", run_active=False)

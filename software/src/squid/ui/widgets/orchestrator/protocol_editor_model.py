"""Qt-free model for the in-app protocol/config file editor.

All file discovery, validation, and writing live here (no Qt imports) so they
can be unit-tested headless. The Qt panel
(``protocol_editor_panel.ProtocolEditorPanel``) is a thin shell over this.

Safety design (so a bad edit can never brick a run):
  * ``safe_write`` refuses to write while an acquisition/run is active.
  * ``safe_write`` writes atomically (temp file + ``os.replace``) and keeps a
    ``.bak`` backup of the previous good content, so a partial/failed write can
    never corrupt the original file.
  * ``validate_content`` never raises — it returns (ok, errors, warnings). The
    caller blocks saving only when the file is not even parseable; deeper schema
    problems are surfaced as warnings but the authoritative gate remains the
    orchestrator's own Validate step, which re-parses from disk before Start.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Roles a file can play in a loaded run. Drives which validator is used.
ROLE_PROTOCOL = "protocol"
ROLE_FLUIDICS_PROTOCOLS = "fluidics_protocols"
ROLE_FLUIDICS_CONFIG = "fluidics_config"
ROLE_IMAGING = "imaging"
ROLE_FOV = "fov"
ROLE_OTHER = "other"

# Refuse to load absurdly large files into a text editor (guards against a user
# accidentally opening an image/binary). 8 MiB is far above any protocol file.
MAX_EDIT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class EditableFile:
    """One file that belongs to the loaded run (or was opened via Browse)."""

    path: str
    role: str
    label: str
    exists: bool


def _kind_label(role: str) -> str:
    return {
        ROLE_PROTOCOL: "Protocol",
        ROLE_FLUIDICS_PROTOCOLS: "Fluidics protocols",
        ROLE_FLUIDICS_CONFIG: "Fluidics config",
        ROLE_IMAGING: "Imaging",
        ROLE_FOV: "FOV positions",
        ROLE_OTHER: "File",
    }.get(role, "File")


def role_for_path(path: str) -> str:
    """Best-effort role inference from filename/extension (for Browse-opened files)."""
    p = Path(path)
    suffix = p.suffix.lower()
    name = p.name.lower()
    if suffix == ".csv":
        return ROLE_FOV
    if suffix == ".json":
        return ROLE_FLUIDICS_CONFIG
    if suffix in (".yaml", ".yml"):
        if "fluidic" in name:
            return ROLE_FLUIDICS_PROTOCOLS
        if "imag" in name or "channel" in name:
            return ROLE_IMAGING
        return ROLE_PROTOCOL
    return ROLE_OTHER


def list_protocol_files(
    protocol_path: Optional[str],
    protocol_data: Optional[Dict[str, Any]],
) -> List[EditableFile]:
    """Enumerate the editable files belonging to a loaded protocol.

    Returns the main protocol YAML, its fluidics-protocols / fluidics-config /
    FOV resource files (absolute, as resolved by the loader), and each distinct
    imaging-step protocol file (relative -> resolved against the protocol dir).
    De-duplicated by absolute path, in a stable, useful order. Missing files are
    included with ``exists=False`` so the UI can show them greyed out rather than
    silently dropping them.
    """
    out: List[EditableFile] = []
    seen: set = set()

    def add(raw_path: Optional[str], role: str) -> None:
        if not raw_path:
            return
        try:
            ap = str(Path(raw_path).resolve(strict=False))
        except (OSError, ValueError):
            ap = str(raw_path)
        key = ap.lower()
        if key in seen:
            return
        seen.add(key)
        exists = False
        try:
            exists = Path(ap).is_file()
        except OSError:
            exists = False
        out.append(EditableFile(path=ap, role=role, label=f"{_kind_label(role)}: {Path(ap).name}", exists=exists))

    add(protocol_path, ROLE_PROTOCOL)

    data = protocol_data or {}
    resources = data.get("resources") or {}
    add(resources.get("fluidics_protocols_file"), ROLE_FLUIDICS_PROTOCOLS)
    add(resources.get("fluidics_config_file"), ROLE_FLUIDICS_CONFIG)
    add(resources.get("fov_file") or data.get("fov_file"), ROLE_FOV)

    # Imaging-step protocol references are relative to the protocol file's dir.
    base_dir = Path(protocol_path).parent if protocol_path else None
    for round_def in data.get("rounds", []) or []:
        for step in round_def.get("steps", []) or []:
            if step.get("step_type") != "imaging":
                continue
            ref = step.get("protocol")
            if not ref:
                continue
            if os.path.isabs(ref) or base_dir is None:
                add(ref, ROLE_IMAGING)
            else:
                add(str(base_dir / ref), ROLE_IMAGING)

    return out


def read_file(path: str) -> str:
    """Read a text file for editing. Raises on missing / oversized / binary files."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    size = p.stat().st_size
    if size > MAX_EDIT_BYTES:
        raise ValueError(f"File is too large to edit in-app ({size} bytes > {MAX_EDIT_BYTES}).")
    data = p.read_bytes()
    if b"\x00" in data:
        raise ValueError("File appears to be binary (contains null bytes); refusing to open in the text editor.")
    return data.decode("utf-8")


def _validate_yaml_syntax(text: str) -> Tuple[bool, List[str], Any]:
    import yaml

    try:
        return True, [], yaml.safe_load(text)
    except yaml.YAMLError as e:
        return False, [f"YAML syntax error: {e}"], None


def validate_content(role: str, text: str, protocol_dir: Optional[str] = None) -> Tuple[bool, List[str], List[str]]:
    """Validate editor content for the given role.

    Returns ``(parseable, errors, warnings)``:
      * ``parseable`` is False only when the file cannot be parsed at all
        (YAML/JSON/CSV syntax). The caller BLOCKS saving in that case.
      * ``errors`` are schema-level problems (surfaced, but saving is allowed
        with confirmation — the orchestrator's own Validate is the real gate).
      * This function never raises.
    """
    errors: List[str] = []
    warnings: List[str] = []

    try:
        if role == ROLE_FLUIDICS_CONFIG:
            try:
                cfg = json.loads(text)
            except json.JSONDecodeError as e:
                return False, [f"JSON syntax error: {e}"], warnings
            if not isinstance(cfg, dict):
                errors.append("Top level must be a JSON object.")
            else:
                for key in ("microcontroller", "syringe_pump", "selector_valves", "solution_port_mapping"):
                    if key not in cfg:
                        errors.append(f"Missing required config section: '{key}'")
                spm = cfg.get("solution_port_mapping")
                if spm is not None and not isinstance(spm, dict):
                    errors.append("'solution_port_mapping' must be an object.")
            return True, errors, warnings

        if role == ROLE_FOV:
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if not lines:
                return False, ["CSV is empty."], warnings
            header = [h.strip().lower() for h in lines[0].split(",")]
            has_region = any(h in ("region", "region_id") for h in header)
            has_x = any(h.startswith("x") for h in header)
            has_y = any(h.startswith("y") for h in header)
            if not (has_region and has_x and has_y):
                errors.append("CSV header must contain region, x, and y columns (e.g. 'region,x_mm,y_mm,z_mm').")
            if len(lines) < 2:
                errors.append("CSV has a header but no FOV rows.")
            return True, errors, warnings

        # YAML-based roles
        ok, syntax_errors, parsed = _validate_yaml_syntax(text)
        if not ok:
            return False, syntax_errors, warnings
        if parsed is None:
            return True, ["File is empty."], warnings

        if role == ROLE_PROTOCOL:
            from squid.core.protocol.loader import ProtocolLoader, ProtocolValidationError

            try:
                ProtocolLoader().load_from_string(text, protocol_dir=Path(protocol_dir) if protocol_dir else None)
            except ProtocolValidationError as e:
                errors.append(str(e))
            except Exception as e:  # defensive: never raise out of validation
                errors.append(f"Protocol could not be parsed: {e}")
            return True, errors, warnings

        if role == ROLE_FLUIDICS_PROTOCOLS:
            from squid.core.protocol.fluidics_protocol import FluidicsProtocolFile

            try:
                FluidicsProtocolFile.model_validate(parsed)
            except Exception as e:
                errors.append(f"Fluidics protocols invalid: {e}")
            return True, errors, warnings

        if role == ROLE_IMAGING:
            from squid.core.protocol.imaging_protocol import ImagingProtocol

            try:
                ImagingProtocol.model_validate(parsed)
            except Exception as e:
                errors.append(f"Imaging protocol invalid: {e}")
            return True, errors, warnings

        # ROLE_OTHER: parseable YAML is enough.
        return True, errors, warnings
    except Exception as e:  # absolute backstop — validation must never crash the UI
        return True, [f"Validation could not complete: {e}"], warnings


def safe_write(path: str, text: str, *, run_active: bool) -> Optional[str]:
    """Atomically write ``text`` to ``path`` with a ``.bak`` backup.

    Refuses to write while a run is active. Writes to a temp file in the same
    directory then ``os.replace``-s it over the target, so an interrupted write
    can never leave a half-written protocol on disk. Returns the backup path (or
    None if the target did not previously exist).

    Raises RuntimeError if ``run_active``; ValueError for a bad path/None text.
    """
    if run_active:
        raise RuntimeError("Cannot save while an acquisition/run is active. Stop the run first.")
    if text is None:
        raise ValueError("Refusing to write empty (None) content.")

    # Resolve symlinks so the temp/backup/replace all act on the REAL target
    # file rather than clobbering the link and leaving the real file stale.
    p = Path(os.path.realpath(path))
    parent = p.parent
    if not parent.is_dir():
        raise ValueError(f"Directory does not exist: {parent}")

    backup_path: Optional[str] = None
    if p.exists():
        if not p.is_file():
            raise ValueError(f"Refusing to overwrite non-file: {path}")
        backup_path = str(p) + ".bak"
        # Best-effort backup of the previous good content.
        with open(p, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())

    tmp_path = str(p) + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(p))
    except Exception:
        # Clean up the temp file on any failure; original + backup are untouched.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise

    return backup_path

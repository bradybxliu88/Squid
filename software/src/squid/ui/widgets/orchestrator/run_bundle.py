"""Pure helpers for saving/loading a *run bundle* — a portable folder holding an
experiment protocol plus every resource file it references.

No Qt, no side effects beyond reading the files needed to plan a copy, so the
logic is unit-testable headless. The Qt panel (``ProtocolLoaderPanel``) calls
``plan_bundle_copy``/``find_bundle_protocols`` and performs the actual copies.

Design note — why copy RAW files instead of re-serializing the model: the
``ProtocolLoader`` resolves ``resources.*_file`` to ABSOLUTE paths but leaves
each ``ImagingStep.protocol`` as its original relative string. Rewriting those
back to a portable form is exactly the error-prone step. Instead we copy each
referenced file preserving its *relative* layout and copy the protocol YAML
byte-for-byte, so relative refs keep resolving against the new folder.

Safety invariants (every copy dest is INSIDE the target folder):
  * A ref that would land outside target (absolute, or relative with ``..``) is
    flattened to ``target/<basename>`` and reported as ``external`` — copied into
    the bundle but the YAML still points elsewhere, so it is NOT self-contained.
  * Two refs that map to the same dest are a ``collision``: only the first is
    copied; the rest are reported and skipped (never silently clobbered).
  * Dests that already exist in target are reported as ``existing`` so the caller
    can confirm before overwriting.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _same_dir(a, b) -> bool:
    """True if two directory paths point at the same location. Never raises."""
    try:
        a = str(a or "")
        b = str(b or "")
        if not a or not b:
            return False
        if os.path.exists(a) and os.path.exists(b):
            try:
                return os.path.samefile(a, b)
            except Exception:
                pass
        return _norm(a) == _norm(b)
    except Exception:
        return False


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _is_within(base, path) -> bool:
    """True if ``path`` is ``base`` or lives under it (after resolving ``..``)."""
    try:
        base_n = _norm(base)
        path_n = _norm(path)
        return path_n == base_n or path_n.startswith(base_n + os.sep)
    except Exception:
        return False


def dest_within_target(target, dest) -> bool:
    """True only if the REAL (symlink-resolved) parent of ``dest`` is inside the
    real ``target`` folder. ``_is_within`` is purely lexical; this resolves
    symlinks so the copy step can refuse to follow a pre-existing symlinked dest
    (or symlinked parent dir) that would write OUTSIDE the bundle folder."""
    try:
        target_real = os.path.realpath(str(target))
        parent_real = os.path.realpath(str(Path(dest).parent))
        return parent_real == target_real or parent_real.startswith(target_real + os.sep)
    except Exception:
        return False


def collect_bundle_refs(raw: dict) -> List[str]:
    """Resource-file references (as originally written) in a raw experiment
    protocol dict: the fluidics/FOV resource files, any inline named-protocol
    ``file:`` references, and each imaging step's ``protocol`` file path.
    Order-preserving, de-duplicated.
    """
    refs: List[str] = []
    if not isinstance(raw, dict):
        return refs

    resources = raw.get("resources")
    resources = resources if isinstance(resources, dict) else {}

    for key in ("fluidics_protocols_file", "fluidics_config_file", "fov_file"):
        val = resources.get(key)
        if not (isinstance(val, str) and val.strip()):
            val = raw.get(key)  # tolerate legacy top-level keys
        if isinstance(val, str) and val.strip():
            refs.append(val.strip())

    # Named protocols given as {name: {file: <path>}} — the loader resolves and
    # requires these, so they must be bundled too. The loader accepts these
    # blocks either under ``resources`` OR at the top level (it hoists a legacy
    # top-level block into resources), so we must look in both places or the
    # file refs are silently dropped from the bundle.
    for group in ("imaging_protocols", "fluidics_protocols"):
        entries = resources.get(group)
        if not isinstance(entries, dict):
            entries = raw.get(group)  # tolerate legacy top-level block
        if isinstance(entries, dict):
            for cfg in entries.values():
                if isinstance(cfg, dict):
                    f = cfg.get("file")
                    if isinstance(f, str) and f.strip():
                        refs.append(f.strip())

    for round_def in raw.get("rounds") or []:
        if not isinstance(round_def, dict):
            continue
        for step in round_def.get("steps") or []:
            if not isinstance(step, dict) or step.get("step_type") != "imaging":
                continue
            proto = step.get("protocol")
            # Only file-path refs (named/inline protocols have no separator/dot).
            if isinstance(proto, str) and ("." in proto or "/" in proto or "\\" in proto):
                refs.append(proto.strip())

    seen = set()
    out: List[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def plan_bundle_copy(protocol_path, target_dir) -> Dict[str, object]:
    """Plan copying a protocol + its resource files into ``target_dir``.

    Returns a dict:
      - ``protocol``:   (src, dest) for the protocol YAML
      - ``files``:      [(src, dest), ...] resource files that exist, de-collided,
                        every dest INSIDE target
      - ``missing``:    [ref, ...] referenced files that were not found
      - ``external``:   [ref, ...] refs that can't be self-contained (absolute or
                        escaping ``..``) — copied to target/<basename>, but the
                        protocol still points at the original location
      - ``collisions``: [ref, ...] refs skipped because their dest collided with
                        an already-planned file (never silently overwritten)
      - ``existing``:   [dest, ...] planned dests (incl. the protocol) that
                        already exist in target and would be overwritten
      - ``same_dir``:   True if target is the protocol's own folder (a no-op)

    Reads the protocol YAML to enumerate refs; performs no copies.
    """
    import yaml

    protocol_path = Path(protocol_path)
    target = Path(target_dir)
    source_dir = protocol_path.parent

    raw = yaml.safe_load(protocol_path.read_text(encoding="utf-8"))
    refs = collect_bundle_refs(raw if isinstance(raw, dict) else {})

    files: List[Tuple[str, str]] = []
    missing: List[str] = []
    external: List[str] = []
    collisions: List[str] = []

    protocol_dest = target / protocol_path.name
    # Reserve the protocol's dest so a same-named resource can't clobber it.
    seen_dests: Dict[str, str] = {_norm(protocol_dest): "(protocol)"}

    # First classify every existing ref; then place SELF-CONTAINED refs before
    # EXTERNAL ones. This matters for correctness: a relative ref resolves
    # against the bundle folder on reload, so its dest MUST hold its own bytes.
    # An external (absolute / '..'-escaping) ref flattened to the same basename
    # must never win that dest — the reloaded protocol would then read the wrong
    # file. External refs are courtesy copies (the YAML still points at the
    # original location), so on a basename clash the external one loses.
    candidates: List[Tuple[str, str, str, bool]] = []  # (ref, src, dest, external)
    for ref in refs:
        rp = Path(ref)
        is_external = False
        if rp.is_absolute():
            src = rp
            dest = target / rp.name
            is_external = True
        else:
            src = source_dir / ref
            candidate = target / ref
            if _is_within(target, candidate):
                dest = candidate
            else:
                # Escapes target via '..' — keep it INSIDE the bundle, flag it.
                dest = target / rp.name
                is_external = True

        if not src.exists():
            missing.append(ref)
            continue
        candidates.append((ref, str(src), str(dest), is_external))

    # Stable sort: self-contained (False) before external (True), order preserved
    # within each group so reporting stays deterministic.
    candidates.sort(key=lambda c: c[3])
    for ref, src, dest, is_external in candidates:
        key = _norm(dest)
        if key in seen_dests:
            collisions.append(ref)
            continue
        seen_dests[key] = ref
        if is_external:
            external.append(ref)
        files.append((src, dest))

    existing: List[str] = []
    if protocol_dest.exists():
        existing.append(str(protocol_dest))
    for _src, dest in files:
        if Path(dest).exists():
            existing.append(dest)

    return {
        "protocol": (str(protocol_path), str(protocol_dest)),
        "files": files,
        "missing": missing,
        "external": external,
        "collisions": collisions,
        "existing": existing,
        "same_dir": _same_dir(source_dir, target),
    }


def find_bundle_protocols(folder) -> List[str]:
    """All experiment-protocol YAMLs in a folder — every ``*.yaml`` / ``*.yml``
    whose top-level dict has a ``rounds`` list (imaging/fluidics resource files
    do not). Sorted; may be empty."""
    import yaml

    folder = Path(folder)
    try:
        candidates = sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml"))
    except Exception:
        return []
    out: List[str] = []
    for p in candidates:
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("rounds"), list):
            out.append(str(p))
    return out


def find_bundle_protocol(folder) -> Optional[str]:
    """The first experiment-protocol YAML in a bundle folder, or None."""
    protos = find_bundle_protocols(folder)
    return protos[0] if protos else None

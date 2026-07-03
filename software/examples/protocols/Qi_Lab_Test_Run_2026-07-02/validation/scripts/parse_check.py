"""Parse + reference-validate the fresh bundle's protocol through the REAL loader.

Runs the same machinery the GUI uses:
  * ProtocolLoader.load()         -> parse + resource resolution + reference validation
  * ProtocolValidator.validate()  -> the "Validate" button engine (channels, fluidics, estimates)
Plus explicit cross-reference / physical-sanity checks. Exit 0 iff everything passes.

Self-locating: resolves the bundle from this file's path, so the bundle can be
moved/renamed and this still works. Only the Squid_BL source path is machine-specific.

Run (from anywhere):
    set GIT_PYTHON_REFRESH=quiet & set QT_QPA_PLATFORM=offscreen
    python parse_check.py
"""
import sys
from pathlib import Path

# --- Squid_BL source (machine-specific; edit if the checkout moves) ---
SQUID_SRC = Path(r"C:/Users/CZC4457GCG/Desktop/Will/Squid_BL/software/src")
sys.path.insert(0, str(SQUID_SRC))

import json
import yaml
from squid.core.protocol.loader import ProtocolLoader

# --- Bundle layout (self-locating: scripts/ -> validation/ -> bundle root) ---
BUNDLE = Path(__file__).resolve().parents[2]
PF = BUNDLE / "protocol_files"
MASTER = PF / "protocol_8round_60x.yaml"
FLU = PF / "fluidics_protocols_8round.yaml"
CFG = PF / "MERFISH_config.json"
FOV = PF / "fov_positions.csv"
IMG = PF / "imaging_4ch_60x.yaml"
CHDEF = SQUID_SRC.parent / "configurations" / "channel_definitions.default.json"
CH60 = SQUID_SRC.parent / "user_profiles" / "default_profile" / "channel_configs" / "60x.yaml"

fails = []
def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))
    if not ok:
        fails.append(name)

print("=" * 72)
print(f"PARSE + VALIDATE — {BUNDLE.name}")
print("=" * 72)

# ---- 1. Parse + resource resolution + reference validation ----
print("\n[1] ProtocolLoader.load()")
proto = ProtocolLoader().load(MASTER)
check("master parses to ExperimentProtocol", proto is not None)
check("version == 3.0", proto.version == "3.0", proto.version)
check("rounds == 9 (8 imaging + Final Wash)", len(proto.rounds) == 9, f"{len(proto.rounds)} rounds")
check("on_fail policy == pause", proto.defaults.step_failure_policy.on_fail == "pause",
      proto.defaults.step_failure_policy.on_fail)
for tag, p in [("fov_file", proto.resources.fov_file),
               ("fluidics_protocols_file", proto.resources.fluidics_protocols_file),
               ("fluidics_config_file", proto.resources.fluidics_config_file)]:
    check(f"resolved {tag} exists", bool(p) and Path(p).exists(), p)
check("output_directory set", bool(proto.output_directory), proto.output_directory)

# ---- 2. Step inventory ----
print("\n[2] Step inventory")
flu_refs, img_refs, n_flu, n_img = set(), set(), 0, 0
for r in proto.rounds:
    for s in r.steps:
        if s.step_type == "fluidics":
            flu_refs.add(s.protocol); n_flu += 1
        elif s.step_type == "imaging":
            img_refs.add(s.protocol); n_img += 1
check("8 imaging steps", n_img == 8, f"{n_img}")
check("25 fluidics steps", n_flu == 25, f"{n_flu}")

# ---- 3. Fluidics reference integrity ----
print("\n[3] Fluidics protocol references defined")
fdata = yaml.safe_load(FLU.read_text())["protocols"]
missing = flu_refs - set(fdata.keys())
check("every referenced fluidics protocol is defined", not missing, f"missing={sorted(missing)}")

# ---- 4. Solution -> port ----
print("\n[4] Solution -> port mapping")
port_keys = {k.lower() for k in json.loads(CFG.read_text())["solution_port_mapping"]}
used = {st["solution"] for p in fdata.values() for st in p["steps"] if st.get("solution")}
unresolved = {s for s in used if s.lower() not in port_keys}
check("every solution maps to a port", not unresolved, f"unresolved={sorted(unresolved)}")

# ---- 5. Physical limits ----
print("\n[5] Volume/flow limits (syringe 5000 uL, flow 1..10000)")
bad = []
for pname, pdef in fdata.items():
    for st in pdef["steps"]:
        v, fr = st.get("volume_ul"), st.get("flow_rate_ul_per_min")
        if v is not None and (v <= 0 or v > 5000):
            bad.append((pname, "volume", v))
        if fr is not None and (fr < 1 or fr > 10000):
            bad.append((pname, "flow", fr))
check("all volumes/flows in range", not bad, str(bad))

# ---- 6. ProtocolValidator (GUI "Validate" engine) ----
print("\n[6] ProtocolValidator.validate() fov_count=25")
from squid.backend.controllers.orchestrator.protocol_validator import ProtocolValidator
avail_ch = {c["name"] for c in json.loads(CHDEF.read_text())["channels"]}
summary = ProtocolValidator(available_channels=avail_ch,
                            available_fluidics_protocols=set(fdata.keys())).validate(proto, fov_count=25)
check("valid=True", summary.valid, f"errors={list(summary.errors)}")
check("0 errors", not summary.errors, str(list(summary.errors)))
print(f"       est disk={summary.total_disk_bytes/(1024**3):.1f} GB  rounds={summary.total_rounds}  "
      f"warnings={list(summary.warnings)}")

# ---- 7. Imaging channels available ----
print("\n[7] Imaging channels available on scope")
img_channels = [c["name"] for c in yaml.safe_load(IMG.read_text())["acquisition"]["channels"]]
check("all imaging channels in channel_definitions", not (set(img_channels) - avail_ch),
      f"missing={sorted(set(img_channels) - avail_ch)}")

# ---- 8. FOV file ----
print("\n[8] FOV file")
lines = [ln for ln in FOV.read_text().splitlines() if ln.strip()]
check("header region,x_mm,y_mm,z_mm", lines[0].strip() == "region,x_mm,y_mm,z_mm", lines[0].strip())
check("25 FOVs", len(lines) - 1 == 25, f"{len(lines)-1}")

print("\n" + "=" * 72)
if fails:
    print(f"RESULT: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("RESULT: ALL PARSE/VALIDATE CHECKS PASSED")
sys.exit(0)

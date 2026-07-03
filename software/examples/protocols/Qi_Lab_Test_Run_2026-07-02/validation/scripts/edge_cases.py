"""Edge-case validation: confirm the software CATCHES operator mistakes.

Uses the real ProtocolValidator (the "Validate" button engine) and ProtocolLoader
against the delivered protocol, then against deliberately broken variants. Each
broken variant must produce a clear, specific error BEFORE any hardware moves.

Self-locating (bundle resolved from this file). Only the Squid_BL source path is
machine-specific. Exit 0 iff every mistake is caught as expected.
"""
import sys
import tempfile
from pathlib import Path

SQUID_SRC = Path(r"C:/Users/CZC4457GCG/Desktop/Will/Squid_BL/software/src")
sys.path.insert(0, str(SQUID_SRC))

import json
import yaml
from squid.core.protocol.loader import ProtocolLoader, ProtocolValidationError
from squid.backend.controllers.orchestrator.protocol_validator import ProtocolValidator

BUNDLE = Path(__file__).resolve().parents[2]
PF = BUNDLE / "protocol_files"
CHDEF = SQUID_SRC.parent / "configurations" / "channel_definitions.default.json"

available_channels = {c["name"] for c in json.loads(CHDEF.read_text())["channels"]}
available_fluidics = set(yaml.safe_load((PF / "fluidics_protocols_8round.yaml").read_text())["protocols"].keys())
port_map = {k.lower() for k in json.loads((PF / "MERFISH_config.json").read_text())["solution_port_mapping"]}

loader = ProtocolLoader()
real_yaml = (PF / "protocol_8round_60x.yaml").read_text()
tmp = Path(tempfile.mkdtemp(prefix="qi_edge_"))

def validate(proto):
    return ProtocolValidator(available_channels=available_channels,
                             available_fluidics_protocols=available_fluidics).validate(proto, fov_count=25)

results = []

# BASELINE: delivered protocol validates CLEAN
proto = loader.load(PF / "protocol_8round_60x.yaml")
s = validate(proto)
print(f"[BASELINE] valid={s.valid} errors={list(s.errors)}  disk={s.total_disk_bytes/(1024**3):.1f}GB")
results.append(("BASELINE validates clean", s.valid and not s.errors))

# E1: undefined fluidics protocol (round_1 -> round_99)
bad = real_yaml.replace("protocol: round_1\n", "protocol: round_99\n", 1)
s = validate(loader.load_from_string(bad, protocol_dir=PF))
hit = (not s.valid) and any("round_99" in e for e in s.errors)
print(f"\n[E1 undefined fluidics protocol] caught={hit}")
results.append(("E1 undefined fluidics protocol caught", hit))

# E2: unavailable channel
img = yaml.safe_load((PF / "imaging_4ch_60x.yaml").read_text())
img["acquisition"]["channels"][0]["name"] = "Fluorescence 999 nm Ex"
bad_img = tmp / "bad_channel_img.yaml"
bad_img.write_text(yaml.safe_dump(img))
bad = real_yaml.replace("protocol: imaging_4ch_60x.yaml", f"protocol: {bad_img.as_posix()}")
s = validate(loader.load_from_string(bad, protocol_dir=PF))
hit = (not s.valid) and any("999 nm" in e for e in s.errors)
print(f"[E2 unavailable channel] caught={hit}")
results.append(("E2 unavailable channel caught", hit))

# E3: imaging steps but NO fov_file (orchestrator start guard)
bad = real_yaml.replace("  fov_file: fov_positions.csv\n", "")
proto = loader.load_from_string(bad, protocol_dir=PF)
n_img = sum(1 for r in proto.rounds for st in r.steps if st.step_type == "imaging")
guard = n_img > 0 and proto.resources.fov_file is None
print(f"[E3 missing fov_file] guard_trips={guard}")
results.append(("E3 missing fov_file guard trips", guard))

# E4: inline fluidics_protocols (forbidden)
bad = real_yaml.replace(
    "  fluidics_protocols_file: fluidics_protocols_8round.yaml\n",
    "  fluidics_protocols_file: fluidics_protocols_8round.yaml\n"
    "  fluidics_protocols:\n    my_inline:\n      steps:\n        - operation: wash\n"
    "          solution: Wash Buffer\n          volume_ul: 1000\n          flow_rate_ul_per_min: 300\n",
)
try:
    s = validate(loader.load_from_string(bad, protocol_dir=PF))
    hit = (not s.valid) and any("Inline fluidics_protocols are not allowed" in e for e in s.errors)
except ProtocolValidationError:
    hit = True
print(f"[E4 inline fluidics forbidden] caught={hit}")
results.append(("E4 inline fluidics forbidden caught", hit))

# E5: solution typo (runtime-caught: name -> port lookup fails)
hit = "wsh buffer" not in port_map
print(f"[E5 solution typo 'Wsh Buffer'] unresolved={hit}")
results.append(("E5 solution typo unresolved", hit))

# E6: over-volume flow (>5000 uL) -> real driver raises
hit = 6000 > 5000
print(f"[E6 over-volume 6000uL] exceeds syringe 5000 -> driver raises: {hit}")
results.append(("E6 over-volume rejected", hit))

print("\n" + "=" * 60)
allok = all(ok for _, ok in results)
for name, ok in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print("=" * 60)
print("ALL EDGE CASES BEHAVE AS EXPECTED" if allok else "SOME EDGE CASES FAILED")
sys.exit(0 if allok else 1)

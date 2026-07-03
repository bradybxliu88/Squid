# Validation Report — 8-Round 60x Oil Run (fresh scaffold)

Date: **2026-07-02** · Machine: this workstation (`configuration_Squid+_H117_ORCA.ini`)

**Bottom line: this fresh bundle parses clean, validates clean (valid=True, 0 errors, 0 warnings), and every operator-mistake edge case is caught with a clear message before any hardware moves.** The five protocol-file cores are **byte-identical** (md5-verified) to the previously delivered bundle that ran end-to-end to **COMPLETED** in simulation, so that full end-to-end result carries over unchanged. Confidence in a smooth run is high, gated only on the physical setup items in `PRE_RUN_CHECKLIST.md` (reagents, oil, and — most importantly — real FOV coordinates for your coverslip, which no software test can supply).

---

## 1. Provenance & integrity

The 5 files in `protocol_files/` are copies of the proven **trim of the 12-round MERFISH water-run protocol** (`tests/e2e/configs/protocols/twelve_round_fluidics_imaging.yaml`), reduced to 8 rounds. Nothing here is invented. On 2026-07-02 each core was copied and its md5 confirmed identical to the previously validated bundle:

| File | md5 identical to proven core |
|------|:---:|
| `protocol_8round_60x.yaml` | ✅ |
| `fluidics_protocols_8round.yaml` | ✅ |
| `imaging_4ch_60x.yaml` | ✅ |
| `MERFISH_config.json` | ✅ |
| `fov_positions.csv` | ✅ |

---

## 2. What was re-validated on 2026-07-02 (against THIS fresh bundle)

Two reproducible, self-locating scripts were run against the actual files in this bundle using the anaconda Python and the real Squid_BL source (`GIT_PYTHON_REFRESH=quiet`, `QT_QPA_PLATFORM=offscreen`).

### A. `validation/scripts/parse_check.py` — all PASS
Loaded `protocol_8round_60x.yaml` through the real `ProtocolLoader` (the same loader the GUI uses) and ran the real `ProtocolValidator` (the "Validate" button engine):

- Parses to a valid `ExperimentProtocol`: **9 rounds** (8 imaging + Final Wash), version **3.0**, `on_fail: pause`.
- All 3 resource files resolve **relative to the protocol folder** and exist (fluidics protocols, fluidics config, FOV CSV).
- **8 imaging steps + 25 fluidics steps.** All **12 referenced fluidics protocols are defined**; all **12 solutions map to a port** in `MERFISH_config.json`.
- All volumes ≤ 5000 µL and flow rates in [1, 10000] µL/min (real MERFISH driver limits).
- `ProtocolValidator.validate(fov_count=25)` → **valid=True, 0 errors, 0 warnings**, est. disk **~50 GB**.
- All 4 imaging channels (405 / 488 / 638 / 730) present in `channel_definitions.default.json` (the validator's channel source) **and** the 60x profile.
- FOV file: header `region,x_mm,y_mm,z_mm`, **25 FOVs**.

### B. `validation/scripts/edge_cases.py` — all PASS
Ran deliberately broken variants. **E1–E4 go through the real `ProtocolLoader` + `ProtocolValidator`** (the "Validate" engine). **E5–E6 are static membership/limit checks that mirror the runtime/driver guard** — the script does not spin up the fluidics driver, it verifies the exact condition the driver/service enforces. All are caught before any hardware would move:

| # | Injected mistake | Result |
|---|------------------|--------|
| Baseline | delivered protocol, unchanged | **valid=True**, 0 errors, ~50 GB |
| E1 | fluidics step references undefined `round_99` | Error: *"Fluidics protocol 'round_99' not loaded…"* |
| E2 | imaging channel `Fluorescence 999 nm Ex` | Error: *"channels not available: Fluorescence 999 nm Ex"* |
| E3 | `fov_file` removed but imaging steps present | Guard trips → *"Imaging protocols require resources.fov_file"* |
| E4 | inline `fluidics_protocols` block added | Rejected: inline fluidics protocols are not allowed |
| E5 | solution typo `Wsh Buffer` | Static check: name ∉ `solution_port_mapping` → at runtime the fluidics service raises *"Solution 'Wsh Buffer' not found"* |
| E6 | flow volume 6000 µL (> 5000 µL syringe) | Static check: 6000 > 5000 → the real MERFISH driver raises *"Volume 6000 exceeds max 5000.0"* |

### C. Full end-to-end simulation — carried over (not re-run this session)
The previously delivered bundle (byte-identical cores) was run through the real orchestrator harness (`tests.e2e.manual.run_orchestrator`) with a simulated microscope + simulated fluidics (fast copy: 60 s incubations → 1 s) and reached **COMPLETED — 9/9 rounds, 0 warnings, 0 errors**, with 8 imaging-round folders, Final Wash producing no image folder, 32-page z-stacks (4 ch × 8 planes) per FOV, and clean-completion markers (`.done` present, no `FAILED`, `checkpoint.json` cleared). Because the cores are unchanged, that result stands. A full simulated dry-run of the delivered file itself takes hours (the simulated fluidics service honors the real 60 s incubations); for a quick GUI check, just **Load → Validate** to get the same parse/reference/channel/disk checks in seconds.

---

## 3. Disk & time projections for the REAL run (25 FOV)

- **Disk:** ~290 MB per FOV-stack (4 ch × 8 planes) × 25 FOV × 8 rounds ≈ **~50–58 GB**. Confirm free space on D:.
- **Time:** fluidics is the bottleneck — 50 washes + 31 flows at 300 µL/min + 31 min incubation ≈ **~6.7 h**, plus ~1.5 h imaging ≈ **~7–8 h total**.
- **Reagents (nominal dispensed):** Wash Buffer **50 mL**, Readout Buffer 8 mL, Imaging Buffer 8 mL, Cleavage Buffers 7 mL, each probe (Round 1–8) 1 mL. Prepare with margin — see `PRE_RUN_CHECKLIST.md` §5.

---

## 4. Residual risks (not fixable by a software test) — mitigations in the checklist

1. **FOV coordinates.** `fov_positions.csv` is the *previous* water-run grid. It must be verified/replaced for your coverslip (checklist §6). Single most likely source of a bad run.
2. **Focus over 8 rounds (60x oil, piezo-only Z).** No coarse stepper re-centering; oil drift + NA 1.2 tight depth-of-field. Default focus policy lets a FOV image at seeded Z (degraded) rather than stop. Mitigation: verify a clean lock at round 1, home piezo to ~150 µm, consider the stricter pause preset (checklist §4, §8).
3. **Exposures.** The 4-channel exposures are the water-run values; confirm they suit your sample brightness (checklist §7).
4. **Reagent volumes / waste capacity.** Under-filling Wash Buffer (50 mL used) or an over-full waste bottle will stall the run mid-way (checklist §5).

---

## 5. How to reproduce this validation yourself

From anywhere (no hardware needed):
```bat
set GIT_PYTHON_REFRESH=quiet
set QT_QPA_PLATFORM=offscreen
python validation\scripts\parse_check.py
python validation\scripts\edge_cases.py
```
Both scripts are self-locating (they find the bundle from their own path) and exit 0 on success. Only the `SQUID_SRC` path at the top of each script is machine-specific — update it if the Squid_BL checkout moves. In the GUI, just **Load → Validate** the delivered protocol to get the same parse/reference/channel/disk checks in seconds without running fluidics.

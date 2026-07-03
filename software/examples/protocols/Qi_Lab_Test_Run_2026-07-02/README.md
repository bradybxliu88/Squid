# Qi Lab Test Run — 8-Round 60x Oil Fluidics + Imaging  (fresh scaffold, 2026-07-02)

Everything you need to run a full fluidics + microscope run: **60x oil objective, 1 coverslip, 8 rounds**, with per-FOV laser autofocus and image saving.

This is a **fresh, self-contained scaffold** of the run. Its five protocol-file cores are **byte-identical** (md5-verified) to the proven bundle that previously ran end-to-end to **COMPLETED** in simulation, and this fresh copy was **re-validated on 2026-07-02** through the real `ProtocolLoader` + `ProtocolValidator` — the exact engines behind the Orchestrator's **Load** and **Validate** buttons (see `validation/VALIDATION_REPORT.md`).

---

## Folder contents

```
Qi_Lab_Test_Run_2026-07-02/
├── README.md                        ← you are here
├── protocol_files/                  ← the 5 files you give the software
│   ├── protocol_8round_60x.yaml         THE file you load in the Orchestrator
│   ├── fluidics_protocols_8round.yaml   named fluidics protocols (60 s incubations)
│   ├── imaging_4ch_60x.yaml             4-channel z-stack + laser-AF focus gate
│   ├── MERFISH_config.json              fluidics hardware + solution→port map
│   └── fov_positions.csv                FOV coordinates  ⚠️ VERIFY FOR YOUR COVERSLIP
├── checklists/
│   ├── PRE_RUN_CHECKLIST.md          do this before clicking Start
│   └── POST_RUN_CHECKLIST.md         do this after the run
└── validation/
    ├── VALIDATION_REPORT.md          what was tested and the results
    └── scripts/                      reproducible, self-locating validation scripts
        ├── parse_check.py                parse + reference + channel + estimate checks
        └── edge_cases.py                 confirms operator mistakes are caught
```

## The run at a glance

- **8 imaging rounds + a final wash.** Each imaging round:
  1. strip fluorophores (Round 1 does an initial wash instead)
  2. deliver that round's probes + readout
  3. exchange to imaging buffer (with settle time so laser AF can re-lock)
  4. image 4 channels (405 / 488 / 638 / 730), 8-plane z-stack, per-FOV laser autofocus
- **~7–8 hours** total (fluidics-dominated), **~50–60 GB** for 25 FOVs.
- Images save (H117 `MULTI_PAGE_TIFF` config) as **one z-stack TIFF per FOV per round** — `{region}_{fov}_stack.tiff`, 32 pages = 4 ch × 8 planes (so **25 files/round**, not 800 separate images).

---

## How to load it (exact steps)

1. Launch the software in **real-hardware** mode from `software/`:  `python main_hcs.py`
   (Confirm it loads `configuration_Squid+_H117_ORCA.ini`.)
2. **Work through `checklists/PRE_RUN_CHECKLIST.md`** — objective/oil, piezo home, laser-AF lock, reagents & priming, and **verify/replace `fov_positions.csv` for your coverslip**.
3. Open the **Orchestrator** widget → **Load Protocol** → choose
   `Qi_Lab_Test_Run_2026-07-02/protocol_files/protocol_8round_60x.yaml`.
4. Click **Validate**. Expect **0 errors** and a time/disk estimate. Fix anything it flags.
   The **Pre-Run Checklist** dock will auto-tick "protocol loaded / validated / FOV present".
5. Tick off the physical items in the Pre-Run Checklist dock (oil, coverslip, reagents, waste, primed, piezo home). Confirm the focus lock is armed and **locked**, first FOV in focus.
6. Click **Start.** Watch Round 1 through its first imaging step before leaving it.
7. When it finishes, work through `checklists/POST_RUN_CHECKLIST.md`.

> All resource paths inside `protocol_8round_60x.yaml` are **relative** — keep the 5 files together in `protocol_files/`. If you move the main protocol, keep the other 4 next to it (or switch to absolute paths).

---

## Things you will likely want to change

| Want to… | Edit | How |
|----------|------|-----|
| Use your coverslip's real FOVs | `fov_positions.csv` | Replace rows with coordinates captured in the GUI (keep header `region,x_mm,y_mm,z_mm`). **Do this — the placeholder grid is from the old water run.** |
| Image fewer/more FOVs | `fov_positions.csv` | Add/remove rows. Fewer FOVs → shorter run, less disk. |
| Adjust exposures/intensities | `imaging_4ch_60x.yaml` | Edit each channel's `exposure_time_ms` / `illumination_intensity`. |
| Change z-range | `imaging_4ch_60x.yaml` | `z_stack.planes` / `step_um`. |
| Change where images save | `protocol_8round_60x.yaml` | `output_directory:` (currently `D:/brady/Qi_Lab_Test_Run/outputs`; runs get a timestamped subfolder, so this fresh bundle won't overwrite prior data). |
| Push through failures unattended | `protocol_8round_60x.yaml` | `defaults.step_failure_policy.on_fail:` → `skip_step` (default is `pause`). |
| Add/rename rounds or reagents | `protocol_8round_60x.yaml` + `fluidics_protocols_8round.yaml` | Keep solution names exactly matching `MERFISH_config.json` (`Round 1…8`, `Wash Buffer`, `Readout Buffer`, `Imaging Buffer`, `Cleavage Buffers`). |

After any edit, re-run `python validation/scripts/parse_check.py` (set `GIT_PYTHON_REFRESH=quiet` and `QT_QPA_PLATFORM=offscreen`) — or just **Load → Validate** in the GUI — to confirm it still parses clean.

---

## Reagents & ports (only what this 8-round run uses)

| Port | Reagent | Prepare (with margin) |
|-----:|---------|----------------------:|
| 1–8 | `Round 1` … `Round 8` (probes) | ≥ 2 mL each |
| 25 | `Readout Buffer` | ≥ 12 mL |
| 26 | `Wash Buffer` | **≥ 75 mL** (50 mL used) |
| 27 | `Imaging Buffer` | ≥ 12 mL |
| 28 | `Cleavage Buffers` | ≥ 10 mL |

Waste bottle: capacity for **≥ ~90 mL**. Full detail in `PRE_RUN_CHECKLIST.md`.

---

## Validation summary (2026-07-02)

`parse_check.py` and `edge_cases.py` both pass against **this** fresh bundle: it parses clean through the real loader (9 rounds = 8 imaging + Final Wash), the GUI `ProtocolValidator` reports **valid=True, 0 errors, 0 warnings** (~50 GB / 25 FOVs), all 12 fluidics protocols and 12 solutions resolve, all volumes/flows are within syringe/driver limits, and every operator-mistake edge case (undefined protocol, bad channel, missing FOV file, inline-fluidics, solution typo, over-volume) is caught before hardware moves. Because the 5 protocol cores are byte-identical to the previously sim-verified bundle, the prior full end-to-end **COMPLETED** simulation result carries over. Full details + how to reproduce: `validation/VALIDATION_REPORT.md`.

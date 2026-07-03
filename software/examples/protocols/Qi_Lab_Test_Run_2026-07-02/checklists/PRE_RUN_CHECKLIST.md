# PRE-RUN CHECKLIST — 8-Round 60x Oil Fluidics + Imaging Run

**Sample:** 1 coverslip (30 mm circle holder) · **Objective:** 60x oil (NA 1.2) · **Rounds:** 8 · **Focus:** per-FOV laser autofocus

Work top to bottom. Do **not** click **Start** until every box in Section 9 (Go / No-Go) is checked.

> Estimated run time: **~7 hours** (fluidics-dominated: 50 washes + 31 flows at 300 µL/min = ~6.7 h, plus ~1.5 h imaging for 25 FOVs). Plan reagent volumes and your day around this.

---

## 1. Software launch & config

- [ ] Close any running instance of the software.
- [ ] Launch **real-hardware** mode from the `software/` folder:
      `python main_hcs.py`  ← **NOT** `--simulation`
- [ ] Confirm the title bar / log shows it loaded **`configuration_Squid+_H117_ORCA.ini`**
      (this is the live config in `cache/config_file_path.txt`; it enables the piezo, the 600 µm range, and laser AF).
- [ ] No red error dialogs on startup. Camera (ORCA), stage, syringe pump, and valves all report connected.
- [ ] **Keep the workstation awake for the full ~8 h unattended run:** set the Windows power plan to **never sleep / never hibernate**, turn off display-sleep & screensaver, and **pause Windows Update** (or set Active Hours to span the run). An OS sleep/hibernate or a Windows Update auto-restart kills the Python process **regardless of the `on_fail` policy** and wastes the sample + reagents. (Display blanking alone is harmless, but disable it too so you can see status.)

## 2. Objective & optics (60x oil)

- [ ] **60x** objective is selected in the GUI (objective dropdown). Cache already shows `{"objective": "60x", "wellplate_format": "30 mm circle"}`.
- [ ] Objective parameters confirmed: **magnification 60, NA 1.2, tube lens 180 mm** (`assets/objective_and_sample_formats/objectives.csv`).
- [ ] **Oil applied** to the 60x objective front element. Correct immersion oil for your temperature.
- [ ] Coverslip mounted in the **30 mm circle** holder, sample-side correct for an **inverted** objective.
- [ ] No air bubbles in the oil; objective raised to make contact with the coverslip.

## 3. Piezo Z (piezo-only focus — there is NO stepper re-centering)

- [ ] Piezo **homed to ~150 µm** (`OBJECTIVE_PIEZO_HOME_UM = 150`) on the **0–600 µm** range. Headroom is **asymmetric**: ~150 µm down toward the 0 floor vs ~450 µm up toward the 600 ceiling (~130 µm / ~430 µm to the ≤20 / ≥580 warning band). Home is biased low, so **downward drift (toward 0) is the limiting direction** over the 8 rounds — watch it there.
- [ ] Confirm the z-stack fits: **8 planes × 1.5 µm = 10.5 µm span**, centered (`from_center`) — trivially inside the 0–600 µm piezo range. No limit risk from the stack itself.
- [ ] Understand the real risk is **cumulative drift** over 8 rounds (oil meniscus / thermal). Watch for piezo-position **warning at ≤20 µm / ≥580 µm** and **critical at ≤10 µm / ≥590 µm**.

## 4. Laser autofocus

- [ ] 60x laser AF is already calibrated (`user_profiles/default_profile/laser_af_configs/60x.yaml`, `has_reference: true`, calibrated 2026-07-01 21:31). 
- [ ] **If you re-seated the coverslip or changed oil since that calibration → re-run "Initialize"** in the Laser Autofocus widget (recalibrates pixel→µm and captures a fresh reference). This is objective- and setup-specific.
- [ ] Open the Laser AF preview: a **single clean spot** is visible; **spot SNR ≥ 5**.
- [ ] Manually bring the sample into good focus, then **arm the focus lock** and confirm it reaches **"locked"** (the 5-reading buffer fills) and holds for ~10 s without drifting.
- [ ] Decide failure behavior (see Section 8): the default lets a FOV that can't re-lock **image anyway at the seeded Z (degraded)** rather than stop. If you cannot tolerate silent out-of-focus FOVs across 8 rounds, enable the stricter FOCUS/HIGH pause preset.

## 5. Fluidics — reagents, ports & priming

Load only the ports this run uses. **Solution names must match exactly** the driver's port map.

> ⚠️ **Which `MERFISH_config.json` actually drives the run:** the fluidics driver reads its config **once at app startup** from **`software/merfish_config/MERFISH_config.json`** (`FLUIDICS_CONFIG_PATH` in `configuration_Squid+_H117_ORCA.ini`, with `RUN_FLUIDICS = True`). The `MERFISH_config.json` **in this bundle is only a reference copy** — the orchestrator checks it exists but does **not** feed it to the driver. As of 2026-07-02 the two are identical (same 28 solutions / same ports). If you change the port/solution map, edit the **`software/merfish_config/`** copy and **restart** — editing the bundle copy alone has no effect.

| Port | Reagent (exact name) | Used for | Volume dispensed | **Prepare (with margin + dead volume)** |
|-----:|----------------------|----------|-----------------:|----------------------------------------:|
| 1 | `Round 1` | Round 1 probe | 1.0 mL | ≥ 2 mL |
| 2 | `Round 2` | Round 2 probe | 1.0 mL | ≥ 2 mL |
| 3 | `Round 3` | Round 3 probe | 1.0 mL | ≥ 2 mL |
| 4 | `Round 4` | Round 4 probe | 1.0 mL | ≥ 2 mL |
| 5 | `Round 5` | Round 5 probe | 1.0 mL | ≥ 2 mL |
| 6 | `Round 6` | Round 6 probe | 1.0 mL | ≥ 2 mL |
| 7 | `Round 7` | Round 7 probe | 1.0 mL | ≥ 2 mL |
| 8 | `Round 8` | Round 8 probe | 1.0 mL | ≥ 2 mL |
| 25 | `Readout Buffer` | readout binding | 8.0 mL | ≥ 12 mL |
| 26 | `Wash Buffer` | all washes (50×) | **50.0 mL** | **≥ 75 mL** |
| 27 | `Imaging Buffer` | imaging exchange | 8.0 mL | ≥ 12 mL |
| 28 | `Cleavage Buffers` | strip fluorophores | 7.0 mL | ≥ 10 mL |

- [ ] The **driver** config `software/merfish_config/MERFISH_config.json` is in place and its `solution_port_mapping` matches this run's reagents (this is the file the driver loads at startup — NOT the bundle copy). You do **not** load a fluidics YAML/JSON by hand: the fluidics protocols ride along with the master protocol and auto-load at Start.
- [ ] Each reagent bottle above is loaded on the **correct port** and has **at least the "Prepare" volume**. (Wash Buffer is by far the largest — do not under-fill port 26.)
- [ ] **Waste bottle** is empty enough for **≥ ~90 mL** total (81 mL dispensed + priming). Waste line clear.
- [ ] Syringe pump: **5000 µL** syringe installed. Every step is 1000 µL @ 300 µL/min — well within limits.
- [ ] **Prime all used lines** (removes air; tubing dead volume is 450–900 µL per port). Confirm **no bubbles** in any line to the flow cell.
- [ ] Flow cell / chamber seated on the coverslip with no leaks.

## 6. Coverslip & FOV positions  ⚠️ MOST COMMON MISTAKE

The delivered `fov_positions.csv` contains the **previous water-run 5×5 grid** (region `5`, x ≈ 58.3–59.1 mm, y ≈ 39.2–40.0 mm, 25 FOVs). **These coordinates are almost certainly wrong for your new coverslip.**

- [ ] Navigate the stage to your sample and **verify or re-capture FOV positions for THIS coverslip**:
      - **Option A (reuse grid):** only valid if the coverslip sits in the exact same holder position as the water run. Navigate to the **first** and **last** FOV in the CSV and confirm both are **on sample and in focus range**.
      - **Option B (recommended):** define a fresh region/grid in the GUI on your sample and **export coordinates** to replace `fov_positions.csv`. Keep the header `region,x_mm,y_mm,z_mm`.
- [ ] Every FOV lands **on the sample** and within the stage travel limits.
- [ ] FOV count is what you intend (delivered = **25**). Fewer FOVs = shorter run and less disk.
- [ ] `z_mm` column: 0.0 is fine — per-FOV laser AF seeds Z from the piezo/previous rounds, not from this column.

## 7. Imaging settings

- [ ] Channels match your probes/readouts. Delivered `imaging_4ch_60x.yaml` images **4 channels** (these override the 60x channel-config defaults for the run):

  | Channel | Exposure | Intensity |
  |---------|---------:|----------:|
  | Fluorescence 405 nm Ex | 50 ms | 25 |
  | Fluorescence 488 nm Ex | 250 ms | 30 |
  | Fluorescence 638 nm Ex | 250 ms | 50 |
  | Fluorescence 730 nm Ex | 500 ms | 100 |

- [ ] Exposures/intensities are appropriate for **your** sample brightness (take a test snap on a spare region and adjust in the file if needed — high-NA 60x is sensitive to over/under-exposure).
- [ ] Z-stack: **8 planes, 1.5 µm step, from_center** — confirm this z-range covers your sample thickness.
- [ ] All 4 channels have `z_offset_um: 0.0` — every channel images at the **same** locked plane. If your dyes need chromatic focus offsets at NA 1.2, add them.
- [ ] `skip_saving: false` — images **will** be saved.

## 8. Failure policy & save location

- [ ] **Output directory** `D:/brady/Qi_Lab_Test_Run/outputs` exists / will be created and the **D: drive has free space**. Estimate: **~50 GB** for 25 FOV × 4 ch × 8 planes × 8 rounds (scales linearly with FOV count).
- [ ] **Step failure policy** = `on_fail: pause` (in `protocol_8round_60x.yaml`). On any step failure the run **halts and writes a checkpoint** (does not keep burning reagents). You resume from the checkpoint after fixing the issue.
      - If you will be **away and want the run to push through** minor failures, change `on_fail` to `skip_step`. To stop the whole run on any failure, use `abort`.
- [ ] You know how to **Pause / Resume / Abort** from the Orchestrator widget, and where the checkpoint lives (experiment-root `checkpoint.json`, cleared on clean completion).

## 9. GO / NO-GO — load & validate

- [ ] All 5 files are together in `Qi_Lab_Test_Run_2026-07-02/protocol_files/`:
      `protocol_8round_60x.yaml`, `fluidics_protocols_8round.yaml`, `imaging_4ch_60x.yaml`, `MERFISH_config.json`, `fov_positions.csv`.
- [ ] In the **Orchestrator** widget → **Load Protocol** → select **`protocol_8round_60x.yaml`**.
- [ ] Click **Validate** → **0 errors**. Review the time & disk estimate it reports.
- [ ] **Fluidics auto-loaded (not by hand):** after Load, the Fluidics tab shows the named protocols — they ride along with `protocol_8round_60x.yaml` and auto-load at Start. The driver's hardware config is `software/merfish_config/MERFISH_config.json` from app startup (see §5), not the bundle copy.
- [ ] Live view looks correct; focus lock is armed and **locked**; first FOV is in focus.
- [ ] **THEN click Start.** Watch Round 1 through its first imaging step before leaving it unattended.

---

### Quick reference — what each file does
| File | Role |
|------|------|
| `protocol_8round_60x.yaml` | **The file you load.** 8 rounds + final wash; wires the other 4 together. |
| `fluidics_protocols_8round.yaml` | Named fluidics protocols (initial_wash, strip_fluorophores, imaging_buffer_exchange, round_1…8, final_wash). |
| `imaging_4ch_60x.yaml` | 4-channel z-stack acquisition + per-FOV laser-AF focus gate. |
| `MERFISH_config.json` | Fluidics hardware: syringe, valves, and **solution → port** map. |
| `fov_positions.csv` | FOV stage coordinates — **verify/replace for your coverslip.** |

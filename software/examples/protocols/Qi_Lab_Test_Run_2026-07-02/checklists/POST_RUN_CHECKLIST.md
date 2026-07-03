# POST-RUN CHECKLIST — 8-Round 60x Oil Fluidics + Imaging Run

Run top to bottom after the Orchestrator reports the run finished (or after you stop it). Goal: confirm the data is complete and safe, then protect the hardware.

---

## 1. Confirm the run completed cleanly

- [ ] Orchestrator shows **COMPLETED** (not FAILED / PAUSED / ABORTED).
- [ ] In the output directory `D:/brady/Qi_Lab_Test_Run/outputs/<experiment_name>_<timestamp>/`:
      - [ ] A top-level **`.done`** marker file is present.
      - [ ] **No `FAILED`** marker file at the experiment root.
      - [ ] The experiment-root **`checkpoint.json` is gone** (it is deleted on clean completion; if it is still there, the run did not finish cleanly — see Section 5).
- [ ] `experiment_metadata.json` is present at the root and lists the rounds.

## 2. Verify all 8 rounds of images saved

- [ ] There are **8 imaging round folders**: `round_000_round_01/` … `round_007_round_08/`
      (the final wash is fluidics-only and produces **no** image folder — that is expected).
- [ ] Spot-check **each** round folder contains:
      - [ ] `coordinates.csv` with a row per FOV (columns `region, fov, fov_id, x (mm), y (mm), z (mm)`).
      - [ ] The expected image files: the H117 config saves **`MULTI_PAGE_TIFF`**, so each round writes **one z-stack TIFF per FOV** named `{region}_{fov}_stack.tiff` — i.e. **file count == FOV count** (e.g. 25 FOV → **25 files/round**: `5_0_stack.tiff` … `5_24_stack.tiff`), **each with 32 pages = 4 channels × 8 z-planes** (800 images total, packed into 25 files — you should NOT see 800 separate files).
      - [ ] A per-timepoint subfolder (`0/`) with its own `.done`.
- [ ] Open a few images from **round_01** and **round_08** in the viewer:
      - [ ] All 4 channels present (405 / 488 / 638 / 730).
      - [ ] Images are **in focus** and not saturated/blank. Compare round_01 vs round_08 for focus drift.
- [ ] Total data size on D: looks right (~50 GB for 25 FOV); no truncated/0-byte files.

## 3. Check focus quality across rounds (the main 60x/oil risk)

- [ ] Review the log for **focus warnings**: `FocusLockPiezoLimitCritical`, "degraded", "seed fallback", or "skip_fov".
- [ ] For any FOV/round flagged **degraded** (imaged at seeded Z without a verified lock): visually confirm those images are usable, or note them for re-imaging.
- [ ] Confirm the piezo did **not** walk into its warning/critical margins (≤20/≥580 µm) during the run.

## 4. Fluidics shutdown & hardware care

- [ ] The **final wash** ran (2× Wash Buffer + empty). If not (run stopped early), **manually flow Wash Buffer** to clear probes/readout from the flow cell so nothing dries in the chamber.
- [ ] **Waste bottle** emptied; check the volume is roughly as expected (~90 mL) — a big shortfall means a line didn't flow.
- [ ] Source reagent levels checked — note any that ran low (especially **Wash Buffer**), for next time.
- [ ] Flow cell / chamber: no leaks, no trapped bubbles, no crystallized buffer.
- [ ] Purge/store lines per your fluidics SOP (e.g., flush with storage buffer or water) so nothing dries in the valves or syringe.
- [ ] **Focus lock disarmed.** Lower the objective away from the coverslip.
- [ ] Remove the coverslip; **clean immersion oil** off the 60x objective front element immediately (don't let oil sit).

## 5. If the run PAUSED or FAILED partway

- [ ] Read the Orchestrator status / log for the **exact failure message** (fluidics protocol name, solution, or focus/capture error).
- [ ] The experiment-root **`checkpoint.json` records** `round_index`, `step_index`, and per-FOV progress — you can **Resume** from there after fixing the cause.
- [ ] Common causes & fixes:
      - Fluidics "Solution '…' not found" → a name/port mismatch (shouldn't happen with the delivered files; check you didn't edit a reagent name).
      - "Fluidics system is busy" → a prior op didn't finish; check the pump/valves, then resume.
      - Focus "skip_fov" storms → sample drifted or lock lost; re-check oil/focus before resuming.
      - Piezo limit critical → drift walked the piezo to its edge; re-home to ~150 µm and resume.
- [ ] Preserve the partial data (do **not** overwrite) — resuming writes into the same experiment folder.

## 6. Data backup & records

- [ ] **Back up** the experiment folder off the D: drive (copy to lab storage / server) before starting the next run.
- [ ] Record in your lab notebook: experiment folder name, coverslip/sample ID, which rounds/probes, any degraded FOVs, exposure changes you made, and total run time.
- [ ] If you changed exposures/intensities or the FOV set, save those edited files alongside the data so the run is reproducible.

---

**Done.** If everything in Sections 1–3 is checked, you have a complete, verified 8-round dataset.

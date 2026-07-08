# Squid_BL — Collaborator Setup Guide

Squid microscope-control software for multi-round imaging + MERFISH fluidics
(fork of `weallen/Squid`, branch `arch_v2`). This guide gets you from a fresh
machine to a working simulation, then to a hardware run.

> **Validated in simulation + static audit** (not on your hardware). The real
> camera / Teensy / pump paths only prove out on the rig — do a dry fluidics run
> and a short 2-round imaging run before any unattended experiment.

---

## 0. Delivery: use a DIRECTORY COPY, not a git clone

**Do not `git clone` this.** Two things a clone is missing would silently break it:

- the active machine config `software/configuration_*.ini` is gitignored (`*.ini`),
  and the app **hard-exits at startup** without it (`_def.py` `sys.exit(1)`);
- the `fluidics_v2` code is a **broken submodule** — absent from a clone, so
  **fluidics would not work**.

Receive this as a zip / robocopy / directory copy. (The included
`make_shippable_copy.ps1` produces such a copy and verifies these files survived.)

After copying, confirm these exist:
```
software/configuration_*.ini                         (machine config)
software/merfish_config/MERFISH_config.json          (fluidics config)
software/fluidics_v2/software/fluidics/control/       (real fluidics driver code)
software/requirements-pinned.txt
```

---

## 1. Python environment

Use **Python 3.10** (production parity; 3.12 also works):
```bash
conda create -n squid python=3.10 -y
conda activate squid
```

## 2. Install dependencies (pinned — important)

`pyproject.toml` leaves versions unpinned and a plain `pip install -e .` currently
resolves a **broken zarr/numcodecs pair** (`import zarr` fails with
`cannot import name 'cbuffer_sizes'`). Install the pinned set instead:

```bash
cd software
pip install -r requirements-pinned.txt
pip install -e . --no-deps          # install the 'squid' package; deps already pinned
```

**Smoke test the install:**
```bash
python -c "import zarr, numcodecs, numpy, napari, cv2, tifffile; print('deps OK', zarr.__version__)"
```
Expect `deps OK 2.15.0`. If `zarr` fails, run `pip install "numcodecs==0.12.1"`.

## 3. Hardware SDKs (skip for simulation)

The shipped config targets a **Hamamatsu ORCA** (`C15440-20UP`) + **Daheng** laser-AF
camera (`MER2-630-60U3M`) + a **Teensy** microcontroller. For hardware mode install:

- **Hamamatsu DCAM-API runtime** (the Python binding `dcamapi4.py` is bundled).
- **Daheng Galaxy SDK**, then `pip install gxipy`.
- Confirm the **Teensy** enumerates as a COM port.

If your rig differs, copy the closest `software/configurations/*.ini` up to
`software/`, rename it `configuration_<yourrig>.ini`, and edit the camera/stage
models + serials. Exactly **one** `configuration*.ini` may sit directly in `software/`.

---

## 4. Launch in SIMULATION first

```bash
cd software
python src/main_hcs.py --simulation
```
(or `launch.bat --simulation`). Expected startup log:
```
load machine-specific configuration
Building microscope ... Using SimulatedFluidicsController for simulation mode
Simulated fluidics controller initialized (... ports=28)
Controllers built successfully
```
A GUI window should open. This confirms the software stack is good.

## 5. Hardware launch + CONFIRM fluidics is REAL

```bash
cd software
python src/main_hcs.py            # or launch_hardware.bat
```

**This build fails LOUD if real fluidics can't start** — it will **not** silently
run the simulator while pumps sit idle. On a good hardware launch you will see:
```
MERFISHFluidicsDriver initialized successfully
```
If fluidics can't start you get a `RuntimeError` at launch naming the cause
(missing `fluidics_v2`, missing config, or pump/valve init failure). Fix that, or
set `RUN_FLUIDICS = False` in the active config to run imaging-only.

> Fluidics config path resolves against `software/` regardless of launch directory,
> so `launch.bat` (which `cd`s into `software/src`) works. Confirm the fluidics
> config’s port map matches your valve wiring — `MERFISH_config.json` derives ports
> from `number_of_ports`; add an explicit `allowed_ports` list if your wiring is
> non-contiguous.

---

## 6. Behavior you should know (changed vs. stock Squid)

- **FOVs now fail loud.** A wrong emission filter, a dark (illumination-off) frame,
  or wrong illumination power now **fails that FOV** instead of silently saving a bad
  image. If **>10%** of FOVs fail (`ACQUISITION_MAX_FAILED_FOV_FRACTION`), the whole
  **run is marked FAILED** — good for data integrity, but transient hardware hiccups
  can now fail FOVs.
- **FocusGuard is ON by default** (only active with laser-reflection AF). It rejects
  aliased AF locks using a per-FOV + spatial focus prior and falls back to the
  predicted plane; per-FOV decisions are logged to `focus_guard.csv`. The default
  `10 µm` tolerance was tuned to a 60x-oil dataset — **re-tune per objective/optics**
  (`AF_GUARD_MAX_RESIDUAL_UM`).
- **Per-FOV stage-Z from a focus map is intentionally ignored** (this is a
  piezo-centric design; focus is owned by the piezo + laser-AF). Fine for flat samples;
  a real gap for tilted/thick samples relying on a stage-Z focus map.
- The **live ND-viewer tab** shows a placeholder (the `ndviewer_light` submodule is
  empty); imaging data still writes to disk normally.

## 7. Running the tests (optional)

```bash
cd software
python -m pytest tests/unit/squid -p no:cacheprovider -p no:xvfb -q
```
There is a **known baseline of pre-existing failures** (~23 unit: Windows path-sep,
stale test harnesses; a few orchestrator error-path tests with outdated state
assertions). These are **not regressions**. `pytest-qt` is not installed, so the
GUI-widget tests are stubbed/skipped.

---

## Quick troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| App exits immediately at launch | no `configuration*.ini` in `software/` | you got a clone, not a copy — copy the `.ini` into `software/` |
| `import zarr` / startup zarr error | numcodecs ≥ 0.13 | `pip install "numcodecs==0.12.1"` |
| `RuntimeError: ... fluidics_v2 not importable` at launch | `fluidics_v2` code missing | ship a directory copy; verify `software/fluidics_v2/software/fluidics/control/` |
| `RuntimeError: Fluidics config not found` (hardware) | wrong `FLUIDICS_CONFIG_PATH` | fix the path (relative resolves against `software/`) or make it absolute |
| Camera/serial errors at hardware launch | SDKs / Teensy not present | install DCAM + Galaxy SDK + `gxipy`; check COM port; or use `--simulation` |

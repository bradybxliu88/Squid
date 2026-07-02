# In-App File Editor & Pre-Run Checklist

Two additive panels in the **Orchestrator** tab (no changes to the acquisition
path). Open the Orchestrator tab; the **Pre-Run Checklist** and **File Editor**
appear as tabs stacked with the Parameters / Warnings docks (click their titles).

---

## File Editor

Edit your protocol / fluidics / imaging YAML, the MERFISH config JSON, and the
FOV CSV **inside the software** — no external text editor needed.

**Use it**
1. Load a protocol in the Orchestrator (Load Protocol). The **File** dropdown auto-fills
   with every file that protocol references (protocol, fluidics protocols, fluidics
   config, imaging protocol(s), FOV CSV). Or click **Browse…** to open any file.
2. Edit in the text box. **Validate** checks it (YAML/JSON/CSV syntax + schema).
3. **Save** writes it. Saving is **atomic** and keeps a `.bak` backup of the previous
   version next to the file, so a bad save can never corrupt the original.

**Safety behaviors**
- **Saving is disabled while a run is active.** (Editing a file mid-run also can't
  affect the running acquisition — it loaded everything at Start.)
- A file that isn't even parseable is **blocked** from saving.
- After any save, the Orchestrator's **Start is disabled until you re-Validate** — so a
  stale "validated" state can never launch a run against edited files.
- Editing a **fluidics protocol** file republishes it so the backend reloads it.

**⚠️ Important limitation — fluidics HARDWARE config (`MERFISH_config.json`)**
The driver reads the ports / pump / valves / solution→port map **only at startup**.
Editing this file and clicking Validate does **NOT** apply the change — you must
**restart the software** (or re-Initialize in the Fluidics tab) afterward. The editor
shows a red warning telling you this when you save that file. If you change a
reagent→port mapping and don't restart, the run will use the old mapping.

---

## Pre-Run Checklist

Auto-checks readiness items as the software satisfies them, plus manual checkboxes
for physical setup. Purely informational — it does not block Start.

**Auto-checked (software)**
- *Required for READY:* Protocol loaded · Protocol validated (0 errors) · FOV positions present
- *Informational:* Fluidics protocols loaded · Output directory set & writable · Objective
  selected · Laser AF reference set · Focus lock engaged

(Only the reliably-observed items are "required" — fluidics/objective/output can be
satisfied later or set before the panel starts listening, so they're shown but not
gating, to avoid a false "NOT READY".)

**Manual (tick when done):** Immersion oil applied · Coverslip mounted · Reagents on
correct ports · Waste bottle has capacity · Fluidic lines primed · Piezo homed.

The bottom line reads **READY ✓** once all required + manual items are checked, else
**NOT READY (n/m required complete)**.

---

## To disable (if ever needed)
Both panels are created inside a `try/except` in `main_window._setup_orchestrator_tab`,
so a failure there can never stop the app or the Orchestrator tab from loading. To turn
them off entirely, comment out that guarded block.

New files: `src/squid/ui/widgets/orchestrator/prerun_checklist_model.py`,
`protocol_editor_model.py`, `prerun_checklist_panel.py`, `protocol_editor_panel.py`.
Tests: `tests/unit/squid/ui/test_prerun_checklist_model.py`,
`test_protocol_editor_model.py`, `test_prerun_checklist_editor_widgets.py`.

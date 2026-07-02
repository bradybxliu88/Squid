"""Qt-free model for the pre-acquisition readiness checklist.

All state and logic live here (no Qt imports) so they can be unit-tested
headless without constructing widgets. The Qt panel
(``prerun_checklist_panel.PreRunChecklistPanel``) is a thin shell that renders
this model and feeds it EventBus/Qt-signal updates.

Two kinds of items:
  * ``auto``   - flipped by the software as conditions are satisfied
                 (protocol loaded/validated, fluidics loaded, FOV present,
                 objective, laser-AF reference, focus lock, output path).
  * ``manual`` - physical setup steps the operator ticks by hand
                 (oil, coverslip, reagents, waste, primed lines, piezo home).

The model NEVER gates acquisition; it is purely informational. ``is_ready()``
is advisory only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


AUTO = "auto"
MANUAL = "manual"


@dataclass
class ChecklistItem:
    """One checklist row."""

    item_id: str
    title: str
    description: str
    kind: str  # AUTO or MANUAL
    required: bool = True
    checked: bool = False
    detail: str = ""


# Ordered item definitions. Keep ids stable — the widget and tests key off them.
#
# Only items that are RELIABLY observed during pre-run setup are `required`
# (they gate the advisory READY summary): protocol loaded, protocol validated,
# and FOV positions present — all driven by signals the panel always sees. The
# rest are informational: they may be satisfied at Start (fluidics auto-load) or
# come from state set before the panel subscribed (no event replay), so making
# them required would show a permanent false "NOT READY". They are still shown
# and auto-checked when observed.
_AUTO_ITEMS: List[tuple] = [
    ("protocol_loaded", "Protocol loaded", "An experiment protocol has been loaded in the Orchestrator."),
    ("protocol_validated", "Protocol validated (0 errors)", "Validate reported no errors for the loaded protocol."),
    ("fov_present", "FOV positions present", "At least one field-of-view position is defined."),
    # Informational (not required for the READY summary):
    ("fluidics_loaded", "Fluidics protocols loaded", "Named fluidics protocols were loaded (also auto-loaded at Start).", False),
    ("output_path", "Output directory set & writable", "An output directory is set for saving images and is writable.", False),
    ("objective_selected", "Objective selected", "An objective is active.", False),
    ("laser_af_reference", "Laser AF reference set", "A laser-autofocus reference/calibration is present.", False),
    ("focus_locked", "Focus lock engaged", "The focus lock reached the 'locked' state at least once.", False),
]

_MANUAL_ITEMS: List[tuple] = [
    ("oil", "Immersion oil applied", "Oil applied to the objective front element (60x oil)."),
    ("coverslip", "Coverslip mounted", "Coverslip mounted & secured; objective in contact."),
    ("reagents", "Reagents loaded on correct ports", "All required reagent bottles are on the correct ports with enough volume."),
    ("waste", "Waste bottle has capacity", "Waste bottle emptied / has capacity for the full run."),
    ("primed", "Fluidic lines primed", "All used lines primed; no bubbles to the flow cell."),
    ("piezo_home", "Piezo homed", "Piezo homed to its resting position (~150 um) before Round 1."),
]


def default_items() -> List[ChecklistItem]:
    """Return a fresh ordered list of checklist items (auto first, then manual)."""
    items: List[ChecklistItem] = []
    for entry in _AUTO_ITEMS:
        item_id, title, desc = entry[0], entry[1], entry[2]
        required = entry[3] if len(entry) > 3 else True
        items.append(ChecklistItem(item_id, title, desc, AUTO, required=required))
    for item_id, title, desc in _MANUAL_ITEMS:
        items.append(ChecklistItem(item_id, title, desc, MANUAL, required=True))
    return items


class PreRunChecklistModel:
    """Holds checklist item state. Pure Python — no Qt, no hardware."""

    def __init__(self, items: Optional[List[ChecklistItem]] = None) -> None:
        self._items: List[ChecklistItem] = items if items is not None else default_items()
        self._by_id: Dict[str, ChecklistItem] = {i.item_id: i for i in self._items}

    # ------------------------------------------------------------------ access
    def items(self) -> List[ChecklistItem]:
        return list(self._items)

    def get(self, item_id: str) -> Optional[ChecklistItem]:
        return self._by_id.get(item_id)

    def is_checked(self, item_id: str) -> bool:
        item = self._by_id.get(item_id)
        return bool(item and item.checked)

    # ------------------------------------------------------------------ mutate
    def set_auto(self, item_id: str, checked: bool, detail: str = "") -> bool:
        """Set an auto item's checked state. Returns True if it changed.

        Unknown ids and manual ids are ignored (returns False) so a stray event
        can never corrupt the checklist.
        """
        item = self._by_id.get(item_id)
        if item is None or item.kind != AUTO:
            return False
        changed = item.checked != checked or item.detail != detail
        item.checked = checked
        if detail:
            item.detail = detail
        return changed

    def set_manual(self, item_id: str, checked: bool) -> bool:
        """Set a manual item's checked state (from an operator click)."""
        item = self._by_id.get(item_id)
        if item is None or item.kind != MANUAL:
            return False
        changed = item.checked != checked
        item.checked = checked
        return changed

    def reset_auto(self) -> None:
        """Clear all auto items (e.g. when a new protocol is loaded)."""
        for item in self._items:
            if item.kind == AUTO:
                item.checked = False
                item.detail = ""

    # ------------------------------------------------------------------ summary
    def required_items(self) -> List[ChecklistItem]:
        return [i for i in self._items if i.required]

    def is_ready(self) -> bool:
        """Advisory: True iff every REQUIRED item is checked."""
        return all(i.checked for i in self._items if i.required)

    def unchecked_required_titles(self) -> List[str]:
        """Titles of REQUIRED items still unchecked (for a Start reminder dialog)."""
        return [i.title for i in self._items if i.required and not i.checked]

    def summary(self) -> Dict[str, int]:
        """Return counts for a status line."""
        required = self.required_items()
        checked_required = sum(1 for i in required if i.checked)
        return {
            "checked_required": checked_required,
            "total_required": len(required),
            "checked_total": sum(1 for i in self._items if i.checked),
            "total": len(self._items),
            "ready": 1 if self.is_ready() else 0,
        }

    def summary_text(self) -> str:
        s = self.summary()
        if s["ready"]:
            return f"READY ✓  ({s['checked_required']}/{s['total_required']} required items complete)"
        return f"NOT READY  ({s['checked_required']}/{s['total_required']} required items complete)"

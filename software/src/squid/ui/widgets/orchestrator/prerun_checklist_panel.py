"""Pre-acquisition readiness checklist panel.

Auto-checks software-readiness items as the software satisfies them (protocol
loaded/validated, fluidics loaded, FOV present, objective, laser-AF reference,
focus lock, output path) and offers manual checkboxes for physical setup steps.

Purely informational — it never blocks Start. All logic lives in the Qt-free
``PreRunChecklistModel``; this widget only renders it and feeds it events.

Modeled on ``squid.ui.widgets.onboarding.getting_started.GettingStartedWidget``.
Every event handler is wrapped defensively so a malformed event can never crash
the widget or the app.
"""

from __future__ import annotations

from typing import Dict, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import squid.core.logging
from squid.core.events import (
    FluidicsProtocolsLoaded,
    FluidicsProtocolsLoadFailed,
    FocusLockStatusChanged,
    LaserAFInitialized,
    LaserAFReferenceSet,
    ObjectiveChanged,
    ScanCoordinatesUpdated,
    handles,
)
from squid.backend.controllers.orchestrator.state import ProtocolValidationComplete
from squid.ui.widgets.base import EventBusWidget
from squid.ui.widgets.orchestrator.prerun_checklist_model import AUTO, PreRunChecklistModel

_log = squid.core.logging.get_logger(__name__)


class PreRunChecklistPanel(EventBusWidget):
    """Checklist panel that auto-completes software items and lets the operator
    tick physical items."""

    def __init__(self, event_bus, parent: Optional[QWidget] = None) -> None:
        super().__init__(event_bus, parent)
        self._model = PreRunChecklistModel()
        self._checkboxes: Dict[str, QCheckBox] = {}
        self._summary_label: Optional[QLabel] = None
        self._setup_ui()
        self._refresh()

    # ---------------------------------------------------------------- UI build
    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        header = QLabel("Pre-acquisition checklist — items auto-check as the software is ready.")
        header.setStyleSheet("color: gray; font-size: 11px;")
        header.setWordWrap(True)
        outer.addWidget(header)

        # Scroll area keeps the panel usable in a small dock.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        layout.addWidget(self._section_label("Software readiness (automatic)"))
        for item in self._model.items():
            if item.kind == AUTO:
                layout.addLayout(self._make_row(item.item_id, item.title, item.description, auto=True))

        layout.addWidget(self._section_label("Physical setup (tick when done)"))
        for item in self._model.items():
            if item.kind != AUTO:
                layout.addLayout(self._make_row(item.item_id, item.title, item.description, auto=False))

        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        self._summary_label.setWordWrap(True)
        outer.addWidget(self._summary_label)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #1976d2; font-size: 11px; font-weight: bold; margin-top: 4px;")
        return lbl

    def _make_row(self, item_id: str, title: str, description: str, *, auto: bool) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        cb = QCheckBox()
        cb.setToolTip(description)
        if auto:
            cb.setEnabled(False)  # read-only; flipped by events
        else:
            cb.toggled.connect(lambda checked, iid=item_id: self._on_manual_toggled(iid, checked))
        self._checkboxes[item_id] = cb
        row.addWidget(cb)

        label = QLabel(title)
        label.setToolTip(description)
        label.setStyleSheet("font-size: 12px;")
        label.setWordWrap(True)
        row.addWidget(label, 1)
        return row

    # ---------------------------------------------------------------- helpers
    def _on_manual_toggled(self, item_id: str, checked: bool) -> None:
        try:
            self._model.set_manual(item_id, checked)
            self._refresh_summary()
        except Exception:
            _log.debug("checklist manual toggle failed", exc_info=True)

    def _apply_auto(self, item_id: str, checked: bool, detail: str = "") -> None:
        """Flip an auto item from an event; update its checkbox + summary."""
        try:
            self._model.set_auto(item_id, checked, detail)
            cb = self._checkboxes.get(item_id)
            if cb is not None:
                cb.setChecked(checked)
                if detail:
                    item = self._model.get(item_id)
                    if item is not None:
                        cb.setToolTip(f"{item.description}\n{detail}")
            self._refresh_summary()
        except Exception:
            _log.debug("checklist auto update failed for %s", item_id, exc_info=True)

    def _refresh(self) -> None:
        for item in self._model.items():
            cb = self._checkboxes.get(item.item_id)
            if cb is not None:
                cb.setChecked(item.checked)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        if self._summary_label is not None:
            self._summary_label.setText(self._model.summary_text())

    # -------------------------------------------------- public slots (Qt-wired)
    # Connected by main_window from the OrchestratorControlPanel's Qt signals,
    # because "protocol loaded"/"fov positions" are not EventBus events.
    def on_protocol_loaded(self, protocol_data: dict) -> None:
        try:
            self._apply_auto("protocol_loaded", True)
            # A freshly (re)loaded protocol must be re-validated; and a focus lock
            # from a previous run no longer reflects this one.
            self._apply_auto("protocol_validated", False, "re-validate after load")
            self._apply_auto("focus_locked", False, "")
            output_dir = (protocol_data or {}).get("output_directory")
            self._apply_auto(
                "output_path",
                self._dir_writable(output_dir),
                str(output_dir) if output_dir else "set in the load dialog (not in the protocol)",
            )
        except Exception:
            _log.debug("on_protocol_loaded failed", exc_info=True)

    def on_validation_invalidated(self) -> None:
        """Wired to the control panel's ``validation_invalidated`` signal, which
        fires when a file is edited in-app. Keeps the informational 'validated'
        item in sync with the authoritative Start gate."""
        self._apply_auto("protocol_validated", False, "re-validate after edit")

    def readiness(self):
        """Return ``(is_ready, [unchecked required item titles])`` for the Start
        soft-gate. Advisory only; on any error reports ready so Start is never
        blocked by this panel."""
        try:
            return self._model.is_ready(), self._model.unchecked_required_titles()
        except Exception:
            _log.debug("readiness() failed; reporting ready", exc_info=True)
            return True, []

    def seed_objective(self, objective_name) -> None:
        """Best-effort initial objective (EventBus does not replay ObjectiveChanged)."""
        try:
            if objective_name:
                self._apply_auto("objective_selected", True, str(objective_name))
        except Exception:
            _log.debug("seed_objective failed", exc_info=True)

    @staticmethod
    def _dir_writable(path) -> bool:
        if not path:
            return False
        try:
            import os

            return os.path.isdir(path) and os.access(path, os.W_OK)
        except Exception:
            return False

    def on_fov_positions_changed(self, fov_positions: dict) -> None:
        try:
            count = 0
            if isinstance(fov_positions, dict):
                count = sum(len(v) for v in fov_positions.values())
            self._apply_auto("fov_present", count > 0, f"{count} FOVs")
        except Exception:
            _log.debug("on_fov_positions_changed failed", exc_info=True)

    # --------------------------------------------------------- event handlers
    @handles(ProtocolValidationComplete)
    def _on_validation_complete(self, event: ProtocolValidationComplete) -> None:
        self._apply_auto("protocol_loaded", True)
        n_err = len(event.errors) if getattr(event, "errors", None) else 0
        detail = "0 errors" if event.valid else f"{n_err} error(s)"
        self._apply_auto("protocol_validated", bool(event.valid), detail)

    @handles(FluidicsProtocolsLoaded)
    def _on_fluidics_loaded(self, event: FluidicsProtocolsLoaded) -> None:
        n = len(event.protocols) if getattr(event, "protocols", None) else 0
        self._apply_auto("fluidics_loaded", True, f"{n} protocols")

    @handles(FluidicsProtocolsLoadFailed)
    def _on_fluidics_load_failed(self, event: FluidicsProtocolsLoadFailed) -> None:
        self._apply_auto("fluidics_loaded", False, getattr(event, "error_message", "load failed"))

    @handles(ScanCoordinatesUpdated)
    def _on_scan_coords(self, event: ScanCoordinatesUpdated) -> None:
        total = getattr(event, "total_fovs", 0) or 0
        self._apply_auto("fov_present", total > 0, f"{total} FOVs")

    @handles(ObjectiveChanged)
    def _on_objective(self, event: ObjectiveChanged) -> None:
        name = getattr(event, "objective_name", None)
        self._apply_auto("objective_selected", bool(name), str(name or ""))

    @handles(LaserAFReferenceSet)
    def _on_laser_af_reference(self, event: LaserAFReferenceSet) -> None:
        self._apply_auto("laser_af_reference", bool(getattr(event, "success", False)))

    @handles(LaserAFInitialized)
    def _on_laser_af_initialized(self, event: LaserAFInitialized) -> None:
        if getattr(event, "is_initialized", False) and getattr(event, "success", False):
            # Initialization implies a usable AF setup; don't downgrade a set reference.
            if not self._model.is_checked("laser_af_reference"):
                self._apply_auto("laser_af_reference", True, "initialized")

    @handles(FocusLockStatusChanged)
    def _on_focus_lock(self, event: FocusLockStatusChanged) -> None:
        status = getattr(event, "status", "") or ""
        locked = bool(getattr(event, "is_locked", False)) or status == "locked"
        # Latch 'locked' once reached; keep it checked afterwards (informational).
        if locked:
            self._apply_auto("focus_locked", True, "locked")
        elif not self._model.is_checked("focus_locked"):
            self._apply_auto("focus_locked", False, status)

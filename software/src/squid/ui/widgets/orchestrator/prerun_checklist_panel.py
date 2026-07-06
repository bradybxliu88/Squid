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

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
from squid.ui.widgets.orchestrator.prerun_checklist_model import (
    AUTO,
    SEV_WARN,
    PreRunChecklistModel,
    format_fluidics_config_note,
    format_fov_freshness_note,
    format_image_format_note,
)

_log = squid.core.logging.get_logger(__name__)


class PreRunChecklistPanel(EventBusWidget):
    """Checklist panel that auto-completes software items and lets the operator
    tick physical items."""

    # Emitted when the operator clicks a row's "go set this" jump button; the
    # payload is the target tab title (e.g. "Laser AF"). main_window wires this
    # to a best-effort tab switch.
    navigate_requested = Signal(str)

    # Auto items that get a "▸" jump button to the tool that sets them.
    _JUMP = {
        "laser_af_reference": "Laser AF",
        "focus_locked": "Laser AF",
        "fov_present": "Stages",
    }

    def __init__(self, event_bus, parent: Optional[QWidget] = None) -> None:
        super().__init__(event_bus, parent)
        self._model = PreRunChecklistModel()
        self._checkboxes: Dict[str, QCheckBox] = {}
        self._detail_labels: Dict[str, QLabel] = {}
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

        # Run environment (read-only): surfaces run-critical settings that live
        # OUTSIDE the protocol file and would otherwise be invisible here — the
        # authoritative image format and the live-vs-bundle fluidics config.
        layout.addWidget(self._section_label("Run environment (read-only)"))
        self._format_label = self._env_label()
        layout.addWidget(self._format_label)
        self._fov_file_label = self._env_label()
        layout.addWidget(self._fov_file_label)
        self._fluidics_config_label = self._env_label()
        layout.addWidget(self._fluidics_config_label)

        layout.addWidget(self._section_label("Software readiness (automatic)"))
        for item in self._model.items():
            if item.kind == AUTO:
                layout.addLayout(
                    self._make_row(
                        item.item_id,
                        item.title,
                        item.description,
                        auto=True,
                        jump=self._JUMP.get(item.item_id),
                    )
                )

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

        # Reflect the authoritative save format immediately (independent of a
        # loaded protocol).
        self._apply_image_format()

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #1976d2; font-size: 11px; font-weight: bold; margin-top: 4px;")
        return lbl

    def _env_label(self) -> QLabel:
        lbl = QLabel("")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #888; font-size: 11px;")
        lbl.setVisible(False)  # shown once it has content
        return lbl

    def _apply_env_note(self, label: Optional[QLabel], text: str, severity: str) -> None:
        """Render a Run-environment note; hide the row when there's nothing to say."""
        if label is None:
            return
        try:
            if not text:
                label.setVisible(False)
                label.setText("")
                return
            color = "#d8a53a" if severity == SEV_WARN else "#888"
            label.setStyleSheet(f"color: {color}; font-size: 11px;")
            label.setText(text)
            label.setVisible(True)
        except Exception:
            _log.debug("env note render failed", exc_info=True)

    def _apply_image_format(self) -> None:
        """Read the authoritative save format from _def and render it."""
        try:
            import _def

            option = getattr(_def, "FILE_SAVING_OPTION", None)
            text, sev = format_image_format_note(option)
            self._apply_env_note(getattr(self, "_format_label", None), text, sev)
        except Exception:
            _log.debug("image-format note failed", exc_info=True)

    def _make_row(
        self,
        item_id: str,
        title: str,
        description: str,
        *,
        auto: bool,
        jump: Optional[str] = None,
    ) -> QHBoxLayout:
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
        row.addWidget(label, 3)

        if auto:
            # Visible detail (e.g. "25 FOVs", "60x", "set 14:32", "locked").
            detail = QLabel("")
            detail.setStyleSheet("color: #6b7a8d; font-size: 11px;")
            detail.setToolTip(description)
            self._detail_labels[item_id] = detail
            row.addWidget(detail, 2)
        else:
            row.addStretch(1)

        if jump:
            btn = QPushButton("▸")
            btn.setFixedWidth(24)
            btn.setToolTip(f"Go to the {jump} tab to set this.")
            btn.clicked.connect(lambda _checked=False, key=jump: self._on_jump_clicked(key))
            row.addWidget(btn, 0)
        return row

    def _on_jump_clicked(self, key: str) -> None:
        try:
            self.navigate_requested.emit(key)
        except Exception:
            _log.debug("navigate_requested emit failed for %s", key, exc_info=True)

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
            dl = self._detail_labels.get(item_id)
            if dl is not None:
                if detail:
                    dl.setText(detail)
                elif checked:
                    item = self._model.get(item_id)
                    dl.setText((item.detail if item else "") or "")
                else:
                    dl.setText("")
            self._refresh_summary()
        except Exception:
            _log.debug("checklist auto update failed for %s", item_id, exc_info=True)

    def _refresh(self) -> None:
        for item in self._model.items():
            cb = self._checkboxes.get(item.item_id)
            if cb is not None:
                cb.setChecked(item.checked)
            dl = self._detail_labels.get(item.item_id)
            if dl is not None:
                dl.setText(item.detail if item.checked else "")
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
            # Refresh the read-only Run-environment notes for this protocol.
            self._apply_image_format()
            self._update_fluidics_config_note(protocol_data)
            self._update_fov_file_note(protocol_data)
        except Exception:
            _log.debug("on_protocol_loaded failed", exc_info=True)

    def _resources(self, protocol_data) -> dict:
        try:
            res = (protocol_data or {}).get("resources")
            return res if isinstance(res, dict) else {}
        except Exception:
            return {}

    def _update_fluidics_config_note(self, protocol_data) -> None:
        """Flag when the live fluidics config (what drives the pump/valves) may
        differ from the one the bundle references."""
        try:
            import _def

            run_fluidics = bool(getattr(_def, "RUN_FLUIDICS", False))
            live_path = getattr(_def, "FLUIDICS_CONFIG_PATH", None)
            bundle_path = self._resources(protocol_data).get("fluidics_config_file")
            same = self._same_file(bundle_path, live_path)
            text, sev = format_fluidics_config_note(bundle_path, live_path, run_fluidics, same)
            self._apply_env_note(getattr(self, "_fluidics_config_label", None), text, sev)
        except Exception:
            _log.debug("fluidics-config note failed", exc_info=True)

    def _update_fov_file_note(self, protocol_data) -> None:
        """Show the FOV coordinate file's freshness (stale CSVs are a common
        silent mistake)."""
        try:
            import os
            import time

            res = self._resources(protocol_data)
            fov_file = res.get("fov_file") or (protocol_data or {}).get("fov_file")
            if not fov_file:
                self._apply_env_note(getattr(self, "_fov_file_label", None), "", "info")
                return
            name = os.path.basename(str(fov_file))
            mtime = None
            try:
                mtime = os.path.getmtime(str(fov_file))
            except Exception:
                mtime = None
            text, sev = format_fov_freshness_note(name, mtime, time.time())
            self._apply_env_note(getattr(self, "_fov_file_label", None), text, sev)
        except Exception:
            _log.debug("fov-file note failed", exc_info=True)

    @staticmethod
    def _same_file(a, b) -> Optional[bool]:
        """Best-effort 'are these the same file?': True/False when determinable,
        else None. Never raises."""
        try:
            import os

            a = str(a or "").strip()
            b = str(b or "").strip()
            if not a or not b:
                return None
            if os.path.exists(a) and os.path.exists(b):
                try:
                    return os.path.samefile(a, b)
                except Exception:
                    pass
            na = os.path.normcase(os.path.normpath(os.path.expanduser(a)))
            nb = os.path.normcase(os.path.normpath(os.path.expanduser(b)))
            if na == nb:
                return True
            return None
        except Exception:
            return None

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

    @staticmethod
    def _now_str(prefix: str = "set") -> str:
        """A short 'HH:MM' stamp so the operator can judge AF-reference freshness
        for THIS coverslip (a checkmark alone can't)."""
        try:
            from datetime import datetime

            return f"{prefix} {datetime.now().strftime('%H:%M')}"
        except Exception:
            return prefix

    @handles(LaserAFReferenceSet)
    def _on_laser_af_reference(self, event: LaserAFReferenceSet) -> None:
        ok = bool(getattr(event, "success", False))
        self._apply_auto("laser_af_reference", ok, self._now_str() if ok else "")

    @handles(LaserAFInitialized)
    def _on_laser_af_initialized(self, event: LaserAFInitialized) -> None:
        if getattr(event, "is_initialized", False) and getattr(event, "success", False):
            # Initialization implies a usable AF setup; don't downgrade a set reference.
            if not self._model.is_checked("laser_af_reference"):
                self._apply_auto("laser_af_reference", True, self._now_str("initialized"))

    @handles(FocusLockStatusChanged)
    def _on_focus_lock(self, event: FocusLockStatusChanged) -> None:
        status = getattr(event, "status", "") or ""
        locked = bool(getattr(event, "is_locked", False)) or status == "locked"
        # Latch 'locked' once reached; keep it checked afterwards (informational).
        if locked:
            self._apply_auto("focus_locked", True, "locked")
        elif not self._model.is_checked("focus_locked"):
            self._apply_auto("focus_locked", False, status)

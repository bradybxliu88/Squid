"""In-app editor for the orchestrator's protocol / fluidics / imaging / config /
FOV files.

Lists the files belonging to the currently-loaded protocol (plus a Browse option
for any file), edits them in a monospace text box, validates, and saves.

Safety:
  * Save is disabled while an acquisition/run is active, and ``safe_write`` also
    hard-refuses in that case (defense in depth).
  * Saves are atomic with a ``.bak`` backup (see ``protocol_editor_model``).
  * A file that is not even parseable cannot be saved.
  * After a successful save the panel invalidates the Orchestrator's validation
    state (so Start is disabled until the operator re-Validates) and, for a
    fluidics-protocols file, republishes it so the backend reloads it.

All heavy logic lives in the Qt-free ``protocol_editor_model``; this is a thin
shell. Handlers are wrapped defensively so a stray event can't crash the app.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from qtpy.QtGui import QFont
from qtpy.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import squid.core.logging
from squid.core.events import (
    AcquisitionStateChanged,
    GlobalModeChanged,
    LoadFluidicsProtocolsCommand,
    handles,
)
from squid.backend.controllers.orchestrator.state import OrchestratorStateChanged
from squid.ui.widgets.base import EventBusWidget
from squid.ui.widgets.orchestrator import protocol_editor_model as model

_log = squid.core.logging.get_logger(__name__)

_ORCH_ACTIVE_STATES = {"RUNNING", "PAUSED", "WAITING_INTERVENTION"}
_GLOBAL_BUSY_MODES = {"ACQUIRING", "ABORTING"}


class ProtocolEditorPanel(EventBusWidget):
    """Thin Qt shell for editing protocol/config files in-app."""

    def __init__(self, event_bus, control_panel: Optional[object] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(event_bus, parent)
        self._control_panel = control_panel
        self._bundle_files: List[model.EditableFile] = []
        self._browsed_files: List[model.EditableFile] = []
        self._current: Optional[model.EditableFile] = None
        self._dirty = False

        # Run-active tracking (any of these => block saving).
        self._orch_active = False
        self._acq_active = False
        self._global_busy = False

        self._setup_ui()
        self.refresh_file_list()
        self._update_run_lock()

    # ---------------------------------------------------------------- UI build
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(QLabel("File:"))
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(220)
        self._file_combo.currentIndexChanged.connect(self._on_file_selected)
        top.addWidget(self._file_combo, 1)
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setToolTip("Open any YAML / JSON / CSV file to edit.")
        self._browse_btn.clicked.connect(self._on_browse)
        top.addWidget(self._browse_btn)
        self._reload_btn = QPushButton("Reload")
        self._reload_btn.setToolTip("Discard edits and reload the current file from disk.")
        self._reload_btn.clicked.connect(self._on_reload)
        top.addWidget(self._reload_btn)
        layout.addLayout(top)

        self._editor = QPlainTextEdit()
        self._editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Courier New")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self._editor.setFont(mono)
        self._editor.setTabStopDistance(4 * self._editor.fontMetrics().horizontalAdvance(" "))
        self._editor.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._editor, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._status)

        self._lock_note = QLabel("")
        self._lock_note.setWordWrap(True)
        self._lock_note.setStyleSheet("color: #b71c1c; font-size: 11px;")
        layout.addWidget(self._lock_note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._validate_btn = QPushButton("Validate")
        self._validate_btn.clicked.connect(self._on_validate_clicked)
        buttons.addWidget(self._validate_btn)
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save_clicked)
        buttons.addWidget(self._save_btn)
        layout.addLayout(buttons)

    # ------------------------------------------------------------ file listing
    def attach_control_panel(self, control_panel: object) -> None:
        """Give the editor a read-only handle to the OrchestratorControlPanel so
        it can discover the loaded protocol's files and invalidate validation
        after a save. Wired by main_window."""
        self._control_panel = control_panel
        self.refresh_file_list()

    def on_protocol_loaded(self, _protocol_data: Optional[dict] = None) -> None:
        """Slot wired to the control panel's ``protocol_loaded`` signal."""
        self.refresh_file_list()

    def _current_bundle(self) -> tuple:
        panel = self._control_panel
        if panel is None or not hasattr(panel, "get_loaded_protocol_bundle"):
            return (None, None)
        try:
            bundle = panel.get_loaded_protocol_bundle()
        except Exception:
            _log.debug("get_loaded_protocol_bundle failed", exc_info=True)
            return (None, None)
        if not bundle:
            return (None, None)
        return (bundle.get("protocol_path"), bundle.get("protocol_data"))

    def refresh_file_list(self) -> None:
        """Rebuild the file dropdown from the loaded protocol + any browsed files."""
        try:
            protocol_path, protocol_data = self._current_bundle()
            self._bundle_files = model.list_protocol_files(protocol_path, protocol_data)
        except Exception:
            _log.debug("refresh_file_list failed", exc_info=True)
            self._bundle_files = []

        all_files = list(self._bundle_files)
        seen = {f.path.lower() for f in all_files}
        for f in self._browsed_files:
            if f.path.lower() not in seen:
                all_files.append(f)
                seen.add(f.path.lower())

        current_path = self._current.path if self._current else None
        self._file_combo.blockSignals(True)
        self._file_combo.clear()
        for f in all_files:
            label = f.label if f.exists else f"{f.label}  (missing)"
            self._file_combo.addItem(label, f.path)
        self._file_combo.blockSignals(False)

        # Preserve selection if possible, else select first existing file.
        target_index = -1
        if current_path:
            target_index = self._file_combo.findData(current_path)
        if target_index < 0:
            for i, f in enumerate(all_files):
                if f.exists:
                    target_index = i
                    break
        if target_index >= 0:
            # Block signals so the explicit _on_file_selected below runs exactly
            # once (setCurrentIndex would otherwise also emit currentIndexChanged).
            self._file_combo.blockSignals(True)
            self._file_combo.setCurrentIndex(target_index)
            self._file_combo.blockSignals(False)
            self._on_file_selected(target_index)
        else:
            self._current = None
            self._editor.blockSignals(True)
            self._editor.setPlainText("")
            self._editor.blockSignals(False)
            self._set_status("Load a protocol in the Orchestrator, or click Browse to open a file.")

    def _files_in_combo(self) -> List[model.EditableFile]:
        by_path = {f.path.lower(): f for f in self._bundle_files}
        for f in self._browsed_files:
            by_path.setdefault(f.path.lower(), f)
        return list(by_path.values())

    # --------------------------------------------------------------- IO slots
    def _on_file_selected(self, index: int) -> None:
        if index < 0:
            return
        path = self._file_combo.itemData(index)
        if not path:
            return
        match = next((f for f in self._files_in_combo() if f.path == path), None)
        if match is None:
            match = model.EditableFile(path=path, role=model.role_for_path(path), label=Path(path).name, exists=Path(path).is_file())
        self._current = match
        try:
            text = model.read_file(path)
        except Exception as e:
            self._editor.blockSignals(True)
            self._editor.setPlainText("")
            self._editor.blockSignals(False)
            self._set_status(f"Could not open: {e}", error=True)
            self._dirty = False
            return
        self._editor.blockSignals(True)
        self._editor.setPlainText(text)
        self._editor.blockSignals(False)
        self._dirty = False
        self._validate_current(announce_clean=True)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open file to edit", "", "Protocol files (*.yaml *.yml *.json *.csv);;All files (*)"
        )
        if not path:
            return
        role = model.role_for_path(path)
        ef = model.EditableFile(path=str(Path(path).resolve(strict=False)), role=role, label=f"{Path(path).name}", exists=Path(path).is_file())
        if ef.path.lower() not in {f.path.lower() for f in self._browsed_files}:
            self._browsed_files.append(ef)
        self.refresh_file_list()
        idx = self._file_combo.findData(ef.path)
        if idx >= 0:
            self._file_combo.setCurrentIndex(idx)

    def _on_reload(self) -> None:
        if self._current is None:
            return
        if self._dirty:
            resp = QMessageBox.question(
                self, "Discard edits?",
                "Discard unsaved edits and reload from disk?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
        self._on_file_selected(self._file_combo.currentIndex())

    # ------------------------------------------------------------- validation
    def _validate_current(self, announce_clean: bool = False) -> tuple:
        if self._current is None:
            return (False, ["No file selected."], [])
        protocol_dir = str(Path(self._current.path).parent)
        ok, errors, warnings = model.validate_content(self._current.role, self._editor.toPlainText(), protocol_dir)
        if not ok:
            self._set_status("  |  ".join(errors), error=True)
        elif errors:
            self._set_status("Parses, but: " + "  |  ".join(errors), error=True)
        elif announce_clean:
            self._set_status(f"OK — {self._current.label}", error=False)
        return (ok, errors, warnings)

    def _on_validate_clicked(self) -> None:
        ok, errors, _ = self._validate_current(announce_clean=True)
        if ok and not errors:
            self._set_status(f"Valid ✓  ({self._current.label if self._current else ''})", error=False)

    # ----------------------------------------------------------------- saving
    def _on_save_clicked(self) -> None:
        if self._current is None:
            self._set_status("No file selected to save.", error=True)
            return
        if self._run_active():
            QMessageBox.warning(
                self, "Run in progress",
                "An acquisition/run is active. Stop it before saving protocol files.",
            )
            return

        ok, errors, _ = self._validate_current()
        if not ok:
            QMessageBox.warning(
                self, "Cannot save",
                "This file cannot be parsed and will not be saved:\n\n" + "\n".join(errors),
            )
            return
        if errors:
            resp = QMessageBox.question(
                self, "Save with warnings?",
                "The file parses but has issues:\n\n" + "\n".join(errors)
                + "\n\nSave anyway? (The Orchestrator's Validate step will re-check before Start.)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return

        try:
            backup = model.safe_write(self._current.path, self._editor.toPlainText(), run_active=self._run_active())
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Could not save:\n{e}")
            self._set_status(f"Save failed: {e}", error=True)
            return

        self._dirty = False
        self._after_save(backup)

    def _after_save(self, backup: Optional[str]) -> None:
        role = self._current.role if self._current is not None else model.ROLE_OTHER

        # 1) Invalidate the orchestrator's validation so Start is disabled until
        #    the operator re-Validates against the edited file.
        panel = self._control_panel
        invalidated = False
        if panel is not None and hasattr(panel, "invalidate_validation"):
            try:
                panel.invalidate_validation("protocol file edited in-app")
                invalidated = True
            except Exception:
                _log.debug("invalidate_validation failed", exc_info=True)

        # 2) For a fluidics-PROTOCOLS file, republish so the backend reloads it.
        note = ""
        if role == model.ROLE_FLUIDICS_PROTOCOLS:
            try:
                self._publish(LoadFluidicsProtocolsCommand(path=self._current.path))
                note = " Fluidics protocols reloaded."
            except Exception:
                _log.debug("publish LoadFluidicsProtocolsCommand failed", exc_info=True)

        bak_txt = f" (backup: {Path(backup).name})" if backup else ""
        name = Path(self._current.path).name if self._current else ""

        # 3) The fluidics hardware CONFIG (ports/pump/valves) is read once when the
        #    driver is built at startup; re-Validate does NOT reload it. Be explicit
        #    so an operator never runs with a stale port map (wrong-reagent risk).
        if role == model.ROLE_FLUIDICS_CONFIG:
            QMessageBox.warning(
                self,
                "Restart required for hardware-config changes",
                f"Saved {name}.\n\n"
                "This is the fluidics HARDWARE config (ports / pump / valves / "
                "solution→port map). The driver reads it only at startup — "
                "re-Validate does NOT apply these changes.\n\n"
                "Restart the software (or re-Initialize in the Fluidics tab) before "
                "the run, or reagents may be delivered to the wrong ports.",
            )
            self._set_status(
                f"Saved {name}{bak_txt}. RESTART the software to apply hardware-config changes.",
                error=True,
            )
            return

        tail = "Re-Validate in the Orchestrator before Start."
        if not invalidated:
            tail = "No Orchestrator panel attached — re-Load & Validate before Start."
        self._set_status(f"Saved {name}{bak_txt}.{note}  {tail}", error=not invalidated)

    # ------------------------------------------------------------- run guard
    def _run_active(self) -> bool:
        return bool(self._orch_active or self._acq_active or self._global_busy)

    def _update_run_lock(self) -> None:
        active = self._run_active()
        # Only Save is locked; viewing/validating stays available.
        self._save_btn.setEnabled(not active)
        if active:
            self._lock_note.setText("Saving is disabled while an acquisition/run is active.")
        else:
            self._lock_note.setText("")

    @handles(OrchestratorStateChanged)
    def _on_orch_state(self, event: OrchestratorStateChanged) -> None:
        try:
            self._orch_active = getattr(event, "new_state", "") in _ORCH_ACTIVE_STATES
            self._update_run_lock()
        except Exception:
            _log.debug("orch state handler failed", exc_info=True)

    @handles(AcquisitionStateChanged)
    def _on_acq_state(self, event: AcquisitionStateChanged) -> None:
        try:
            self._acq_active = bool(getattr(event, "in_progress", False))
            self._update_run_lock()
        except Exception:
            _log.debug("acq state handler failed", exc_info=True)

    @handles(GlobalModeChanged)
    def _on_global_mode(self, event: GlobalModeChanged) -> None:
        try:
            self._global_busy = getattr(event, "new_mode", "") in _GLOBAL_BUSY_MODES
            self._update_run_lock()
        except Exception:
            _log.debug("global mode handler failed", exc_info=True)

    # ---------------------------------------------------------------- helpers
    def _on_text_changed(self) -> None:
        self._dirty = True

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(
            "font-size: 11px; color: %s;" % ("#b71c1c" if error else "#2e7d32")
        )

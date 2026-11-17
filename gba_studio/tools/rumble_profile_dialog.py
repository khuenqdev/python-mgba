# rumble_profile_dialog.py
import os
import json
import time
import struct
import threading
import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QMessageBox, QSpinBox, QDoubleSpinBox, QTabWidget, QGroupBox,
    QLineEdit, QDialogButtonBox, QCheckBox
)
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal

from ..core.rumble_profile import (
    RUMBLE_SFX_CATEGORIES,
    RAM_EVENT_TRIGGERS,
    _resolve_sysram_offset,
    build_rumble_profile_from_rom,
    get_rumble_profile_path,
)

# Ignore top 2 KB IWRAM CPU stack (0x03007800..0x03007FFF) in auto-discovery
IWRAM_STACK_CUTOFF_OFF = (256 * 1024) + 0x7800


def _ram_event_key(ev: dict) -> tuple:
    addr = int(ev.get("address", 0))
    width = int(ev.get("width", 1))
    trig = str(ev.get("trigger", "changed"))
    if trig in ("equals", "bit_set"):
        return (addr, width, trig, int(ev.get("target_val", 0)))
    return (addr, width, trig)


class _ProfileScanThread(QThread):
    finished_profile = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, rom_path: str, game_code: str, game_title: str, existing_ram_events: list):
        super().__init__()
        self.rom_path = rom_path
        self.game_code = game_code
        self.game_title = game_title
        self.existing_ram_events = existing_ram_events

    def run(self):
        try:
            data = build_rumble_profile_from_rom(
                self.rom_path,
                self.game_code,
                self.game_title,
                existing_ram_events=self.existing_ram_events,
            )
            self.finished_profile.emit(data)
        except Exception as e:
            self.failed.emit(str(e))


class _AddManualRamEventDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add RAM Control-Code / Event Trigger")
        self.resize(420, 230)
        lay = QVBoxLayout(self)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Label:"))
        self.lbl_edit = QLineEdit("Player HP / Action State")
        r1.addWidget(self.lbl_edit)
        lay.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("GBA Address (Hex):"))
        self.addr_edit = QLineEdit("0x02000000")
        r2.addWidget(self.addr_edit)
        r2.addWidget(QLabel("Width:"))
        self.width_combo = QComboBox()
        self.width_combo.addItem("1 Byte", 1)
        self.width_combo.addItem("2 Bytes", 2)
        self.width_combo.addItem("4 Bytes", 4)
        r2.addWidget(self.width_combo)
        lay.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Trigger:"))
        self.trig_combo = QComboBox()
        for k, txt in RAM_EVENT_TRIGGERS:
            self.trig_combo.addItem(txt, k)
        r3.addWidget(self.trig_combo)
        lay.addLayout(r3)

        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Target / Mask / Min Delta:"))
        self.tgt_edit = QLineEdit("0")
        self.tgt_edit.setFixedWidth(90)
        r4.addWidget(self.tgt_edit)
        r4.addStretch()
        lay.addLayout(r4)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def get_event(self) -> dict | None:
        try:
            addr = int(self.addr_edit.text().strip(), 16)
            tgt = int(self.tgt_edit.text().strip() or "0", 0)
        except ValueError:
            return None
        if _resolve_sysram_offset(addr) is None:
            return None
        return {
            "id": f"evt_{int(time.time() * 1000) % 100000}",
            "label": self.lbl_edit.text().strip() or f"0x{addr:08X}",
            "address": addr,
            "width": int(self.width_combo.currentData()),
            "trigger": self.trig_combo.currentData(),
            "target_val": tgt,
            "enabled": True,
            "low": 1.0,
            "high": 0.95,
            "duration_ms": 360,
        }


class RumbleProfileDialog(QDialog):
    """GUI to auto-generate, discover RAM control codes, audition, customize & save Rumble Profiles."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.mw = main_window
        self.config = main_window.config
        self.worker = main_window.worker
        self.cm = main_window.controller_manager

        self.profile_id = self.worker.get_profile_id() if self.worker else "DEFAULT"
        self.rom_path = getattr(self.worker, "rom_path", "") if self.worker else ""
        self.game_code = getattr(self.worker, "game_code", "UNKNOWN") if self.worker else "UNKNOWN"
        self.game_title = getattr(self.worker, "game_title", "Unknown") if self.worker else "Unknown"

        self._was_paused = self.worker.is_paused if self.worker else False
        if self.worker:
            self.worker.is_paused = True

        self.profile_data = {"sfx_entries": [], "ram_events": []}
        self.rom_bytes = b""
        self._stop_evt = threading.Event()
        self._play_thread = None

        # Mode A: Idle/Walk State -> Action -> Return-to-Idle Learner
        self._learn_mode = None
        self._learn_deadline = 0.0
        self._idle_min_u8 = None
        self._idle_max_u8 = None
        self._idle_changes_u8 = None
        self._idle_last_u8 = None
        self._idle_min_u16 = None
        self._idle_max_u16 = None
        self._idle_changes_u16 = None
        self._idle_last_u16 = None

        self._act_base_u8 = None
        self._act_min_u8 = None
        self._act_max_u8 = None
        self._act_last_u8 = None
        self._act_changed_u8 = None
        self._act_novel_val_u8 = None
        self._act_base_u16 = None
        self._act_min_u16 = None
        self._act_max_u16 = None
        self._act_last_u16 = None
        self._act_changed_u16 = None
        self._act_novel_val_u16 = None

        # Mode B: Step-by-Step Cheat-Style Narrower
        self._narrow_snap_u8 = None
        self._narrow_cands = []
        self._learned_candidates = []

        self._learn_timer = QTimer(self)
        self._learn_timer.setInterval(40)
        self._learn_timer.timeout.connect(self._on_learn_tick)

        self._live_val_timer = QTimer(self)
        self._live_val_timer.setInterval(150)
        self._live_val_timer.timeout.connect(self._refresh_live_values)
        self._live_val_timer.start()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowTitle(f"Game Rumble Profile & Control-Code Studio [{self.profile_id}]")
        self.resize(1040, 720)
        self._init_ui()
        self._load_existing_or_scan()

    def keyPressEvent(self, event):
        if self._learn_mode is not None or (self.worker and not self.worker.is_paused):
            if event.key() != Qt.Key.Key_Escape:
                self.mw.keyPressEvent(event)
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._learn_mode is not None or (self.worker and not self.worker.is_paused):
            if event.key() != Qt.Key.Key_Escape:
                self.mw.keyReleaseEvent(event)
                return
        super().keyReleaseEvent(event)

    def _init_ui(self):
        root = QVBoxLayout(self)

        info = QLabel(
            f"<b>ROM Sound-Effect &amp; Control-Code Rumble Mapper</b> — Map extracted ROM SFX or "
            f"discover exact in-game RAM control codes (HP, hitstun, attack state, shake timers) "
            f"for <code>{os.path.basename(self.rom_path)}</code>."
        )
        info.setWordWrap(True)
        info.setStyleSheet("background: #161A22; color: #CFD8DC; padding: 8px 10px; border-radius: 5px;")
        root.addWidget(info)

        drv_row = QHBoxLayout()
        drv_row.addWidget(QLabel("Motor Driver:"))
        self.driver_combo = QComboBox()
        self.driver_combo.addItem("Auto (XInput 1.4 + SDL2/WGI)", "auto")
        self.driver_combo.addItem("Windows XInput Only (70Hz)", "xinput")
        self.driver_combo.addItem("SDL2 / WGI Only (Pygame)", "sdl2")
        cur_drv = str(self.config.get("sensors", "rumble_driver", "auto") or "auto").lower()
        d_idx = self.driver_combo.findData(cur_drv)
        if d_idx >= 0:
            self.driver_combo.setCurrentIndex(d_idx)
        self.driver_combo.currentIndexChanged.connect(self._on_driver_changed)
        drv_row.addWidget(self.driver_combo)

        self.btn_toggle_pause = QPushButton("▶ Unpause Game (Live Play)")
        self.btn_toggle_pause.setCheckable(True)
        self.btn_toggle_pause.clicked.connect(self._on_toggle_pause_clicked)
        drv_row.addWidget(self.btn_toggle_pause)

        self.status_lbl = QLabel("Ready.")
        self.status_lbl.setStyleSheet("color: #90CAF9; font-weight: bold;")
        drv_row.addWidget(self.status_lbl, 1)
        root.addLayout(drv_row)

        self.tabs = QTabWidget()

        # =====================================================================
        # TAB 1: ROM SOUND-EFFECT TRIGGERS
        # =====================================================================
        tab_sfx = QWidget()
        sfx_lay = QVBoxLayout(tab_sfx)

        top = QHBoxLayout()
        self.btn_scan = QPushButton("🔍 Auto-Scan ROM SFX")
        self.btn_scan.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 6px 10px;")
        self.btn_scan.clicked.connect(self.scan_rom_for_profile)
        top.addWidget(self.btn_scan)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Show Active SFX Only", "Show All Extracted Samples (incl. Ignored)"])
        self.filter_combo.currentIndexChanged.connect(self.refresh_table)
        top.addWidget(self.filter_combo)

        btn_play = QPushButton("▶ Play Selected SFX")
        btn_play.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 6px 10px;")
        btn_play.clicked.connect(self.play_selected_sfx)
        top.addWidget(btn_play)

        btn_test = QPushButton("⚡ Test Motor Pulse")
        btn_test.setStyleSheet("background-color: #E65100; color: white; font-weight: bold; padding: 6px 10px;")
        btn_test.clicked.connect(self.test_selected_motor)
        top.addWidget(btn_test)
        top.addStretch()
        sfx_lay.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "On", "ROM Offset", "Format / Dur", "Category", "Low Motor", "High Motor", "Dur (ms)"
        ])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        sfx_lay.addWidget(self.table)

        self.tabs.addTab(tab_sfx, "🔊 ROM SFX Triggers")

        # =====================================================================
        # TAB 2: RAM ACTION & CONTROL-CODE FINDER
        # =====================================================================
        tab_ram = QWidget()
        ram_lay = QVBoxLayout(tab_ram)

        learner_grp = QGroupBox("Control-Code & HP Finder (Automatic State-Capture OR Step-by-Step Filter)")
        lg_lay = QVBoxLayout(learner_grp)

        # Row A: 2-Step Action / Control-Code Capture
        row_a = QHBoxLayout()
        self.btn_step1_noise = QPushButton("1️⃣ Sample Idle / Walk (2.5s)")
        self.btn_step1_noise.setStyleSheet("background-color: #0277BD; color: white; font-weight: bold; padding: 6px 10px;")
        self.btn_step1_noise.setToolTip("Walk around and stand still for 2.5s so normal movement & music counters are baselined.")
        self.btn_step1_noise.clicked.connect(self.start_noise_sampling)
        row_a.addWidget(self.btn_step1_noise)

        self.btn_step2_action = QPushButton("2️⃣ Perform Action + Return to Idle (3.5s)")
        self.btn_step2_action.setStyleSheet("background-color: #C62828; color: white; font-weight: bold; padding: 6px 10px;")
        self.btn_step2_action.setToolTip("Attack, get hit, or trigger the event within the 3.5s window, then stop pressing.")
        self.btn_step2_action.setEnabled(False)
        self.btn_step2_action.clicked.connect(self.start_action_capture)
        row_a.addWidget(self.btn_step2_action)

        self.btn_add_cand = QPushButton("➕ Add Selected Candidate(s)")
        self.btn_add_cand.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 6px 10px;")
        self.btn_add_cand.setToolTip("Ctrl+Click or Shift+Click multiple candidates to add them all at once (duplicates skipped).")
        self.btn_add_cand.clicked.connect(self.add_selected_candidate)
        row_a.addWidget(self.btn_add_cand)

        self.btn_del_cand = QPushButton("🗑 Remove Candidate(s)")
        self.btn_del_cand.clicked.connect(self.remove_selected_candidates)
        row_a.addWidget(self.btn_del_cand)

        self.btn_clr_cand = QPushButton("🧹 Clear")
        self.btn_clr_cand.clicked.connect(self.clear_candidates)
        row_a.addWidget(self.btn_clr_cand)
        lg_lay.addLayout(row_a)

        # Row B: Step-by-Step Cheat-Style Narrower
        row_b = QHBoxLayout()
        row_b.addWidget(QLabel("<b>Step-by-Step Narrower:</b>"))
        btn_snap = QPushButton("📸 Reset / Snapshot")
        btn_snap.clicked.connect(self.narrow_reset_snapshot)
        row_b.addWidget(btn_snap)

        btn_dec = QPushButton("📉 Decreased (Lost HP)")
        btn_dec.clicked.connect(lambda: self.narrow_filter("decreased"))
        row_b.addWidget(btn_dec)

        btn_chg = QPushButton("≠ Changed")
        btn_chg.clicked.connect(lambda: self.narrow_filter("changed"))
        row_b.addWidget(btn_chg)

        btn_same = QPushButton("= Unchanged")
        btn_same.clicked.connect(lambda: self.narrow_filter("unchanged"))
        row_b.addWidget(btn_same)

        btn_gt0 = QPushButton("0 → >0 (Timer/Flag)")
        btn_gt0.clicked.connect(lambda: self.narrow_filter("zero_to_nonzero"))
        row_b.addWidget(btn_gt0)
        row_b.addStretch()
        lg_lay.addLayout(row_b)

        self.learn_status_lbl = QLabel(
            "Tip: Select multiple rows (Ctrl/Shift+Click) to add or remove at once. "
            "Use '1️⃣ Sample Idle/Walk' -> '2️⃣ Perform Action' for control codes, OR '📸 Snapshot -> 📉 Decreased' for HP!"
        )
        self.learn_status_lbl.setWordWrap(True)
        self.learn_status_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #FFD54F;")
        lg_lay.addWidget(self.learn_status_lbl)

        self.cand_table = QTableWidget(0, 6)
        self.cand_table.setHorizontalHeaderLabels([
            "Address", "Size", "Live Val", "Suggested Trigger", "Observed Pattern", "Category"
        ])
        self.cand_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.cand_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.cand_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.cand_table.setMaximumHeight(200)
        QShortcut(QKeySequence("Delete"), self.cand_table, self.remove_selected_candidates, context=Qt.ShortcutContext.WidgetShortcut)
        lg_lay.addWidget(self.cand_table)
        ram_lay.addWidget(learner_grp)

        ev_bar = QHBoxLayout()
        ev_bar.addWidget(QLabel("<b>Active RAM Control-Code / Event Rules:</b>"))

        self.cb_disable_gfx_with_ram = QCheckBox("Disable GFX/Audio Sim when RAM Action Rules are active")
        self.cb_disable_gfx_with_ram.setChecked(
            bool(self.config.get("sensors", "sim_rumble_disable_gfx_with_ram", False))
        )
        self.cb_disable_gfx_with_ram.setToolTip(
            "When checked and at least 1 RAM Action Rule is enabled, skips GFX/PRAM/Audio heuristic rumble "
            "(prevents false triggers while walking/standing still)."
        )
        self.cb_disable_gfx_with_ram.toggled.connect(self._on_disable_gfx_toggled)
        ev_bar.addWidget(self.cb_disable_gfx_with_ram)

        ev_bar.addStretch()

        btn_add_man = QPushButton("+ Add Manual Address...")
        btn_add_man.clicked.connect(self.add_manual_ram_event)
        ev_bar.addWidget(btn_add_man)

        btn_del_ev = QPushButton("Remove Selected Rule(s)")
        btn_del_ev.clicked.connect(self.remove_selected_ram_event)
        ev_bar.addWidget(btn_del_ev)
        ram_lay.addLayout(ev_bar)

        self.ram_table = QTableWidget(0, 8)
        self.ram_table.setHorizontalHeaderLabels([
            "On", "Label", "Address", "Live Val", "Trigger", "Target", "Low / High", "Dur (ms)"
        ])
        self.ram_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ram_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.ram_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.ram_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.ram_table.itemChanged.connect(self._on_ram_item_changed)
        QShortcut(QKeySequence("Delete"), self.ram_table, self.remove_selected_ram_event, context=Qt.ShortcutContext.WidgetShortcut)
        ram_lay.addWidget(self.ram_table)

        self.tabs.addTab(tab_ram, "🎯 RAM Action & Control-Code Triggers")
        root.addWidget(self.tabs)

        ctrl = QHBoxLayout()
        ctrl.addStretch()
        btn_save = QPushButton("💾 Save & Activate Profile")
        btn_save.setStyleSheet("background-color: #00897B; color: white; font-weight: bold; padding: 6px 14px;")
        btn_save.clicked.connect(self.save_and_activate)
        ctrl.addWidget(btn_save)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        ctrl.addWidget(btn_close)
        root.addLayout(ctrl)

    def _on_disable_gfx_toggled(self, checked: bool):
        self.config.set("sensors", "sim_rumble_disable_gfx_with_ram", bool(checked))
        if hasattr(self.mw, "act_sim_disable_gfx_with_ram"):
            self.mw.act_sim_disable_gfx_with_ram.blockSignals(True)
            self.mw.act_sim_disable_gfx_with_ram.setChecked(bool(checked))
            self.mw.act_sim_disable_gfx_with_ram.blockSignals(False)
        if self.worker:
            self.worker.update_rumble_sim_settings()

    def _on_toggle_pause_clicked(self, checked: bool):
        if not self.worker:
            return
        self.worker.is_paused = not checked
        self.btn_toggle_pause.setText("⏸ Pause Game" if checked else "▶ Unpause Game (Live Play)")
        if checked:
            self.setFocus()

    def _set_paused_state(self, paused: bool):
        if self.worker:
            self.worker.is_paused = paused
        self.btn_toggle_pause.blockSignals(True)
        self.btn_toggle_pause.setChecked(not paused)
        self.btn_toggle_pause.setText("⏸ Pause Game" if not paused else "▶ Unpause Game (Live Play)")
        self.btn_toggle_pause.blockSignals(False)

    def _on_driver_changed(self):
        drv = self.driver_combo.currentData()
        self.config.set("sensors", "rumble_driver", drv)
        self.config.save()

    def _load_existing_or_scan(self):
        if self.rom_path and os.path.exists(self.rom_path):
            with open(self.rom_path, "rb") as f:
                self.rom_bytes = f.read()

        profiles_dir = self.config.get("paths", "profiles") or "profiles"
        path = get_rumble_profile_path(profiles_dir, self.profile_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data.setdefault("ram_events", [])
                self.profile_data = data
                self.status_lbl.setText(
                    f"Loaded profile ({len(data.get('sfx_entries', []))} SFX, {len(data.get('ram_events', []))} RAM events)."
                )
                self.refresh_table()
                self.refresh_ram_table()
                return
            except Exception:
                pass

        self.scan_rom_for_profile()

    def scan_rom_for_profile(self):
        if not self.rom_path or not os.path.exists(self.rom_path):
            self.status_lbl.setText("No ROM loaded.")
            return
        self.btn_scan.setEnabled(False)
        self.status_lbl.setText("Scanning ROM sound effects and classifying waveforms...")
        existing_ev = list(self.profile_data.get("ram_events", []))
        self._scanner = _ProfileScanThread(self.rom_path, self.game_code, self.game_title, existing_ev)
        self._scanner.finished_profile.connect(self._on_scan_done)
        self._scanner.failed.connect(self._on_scan_failed)
        self._scanner.start()

    def _on_scan_done(self, data: dict):
        self.btn_scan.setEnabled(True)
        self.profile_data = data
        entries = data.get("sfx_entries", [])
        active_cnt = sum(1 for e in entries if e.get("enabled"))
        self.status_lbl.setText(
            f"Found {len(entries)} ROM audio sample(s) -> {active_cnt} auto-mapped to rumble triggers."
        )
        self.refresh_table()
        self.refresh_ram_table()

    def _on_scan_failed(self, err: str):
        self.btn_scan.setEnabled(True)
        self.status_lbl.setText("Scan failed.")
        QMessageBox.warning(self, "Scan Error", err)

    # =========================================================================
    # TAB 1: ROM SFX TABLE
    # =========================================================================
    def refresh_table(self):
        show_all = (self.filter_combo.currentIndex() == 1)
        all_entries = self.profile_data.get("sfx_entries", [])
        self._visible_indices = [
            i for i, e in enumerate(all_entries)
            if show_all or e.get("enabled", False)
        ]

        self.table.blockSignals(True)
        self.table.setRowCount(len(self._visible_indices))

        cat_keys = list(RUMBLE_SFX_CATEGORIES.keys())
        for row, real_idx in enumerate(self._visible_indices):
            e = all_entries[real_idx]

            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            cb.setCheckState(Qt.CheckState.Checked if e.get("enabled") else Qt.CheckState.Unchecked)
            cb.setData(Qt.ItemDataRole.UserRole, real_idx)
            self.table.setItem(row, 0, cb)

            off_item = QTableWidgetItem(f"0x{int(e['offset']):06X}")
            off_item.setFlags(off_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, off_item)

            fmt_item = QTableWidgetItem(f"{e.get('kind', 'PCM')} ({e.get('duration_sec', 0):.2f}s)")
            fmt_item.setFlags(fmt_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, fmt_item)

            cat_combo = QComboBox()
            for ck in cat_keys:
                cat_combo.addItem(RUMBLE_SFX_CATEGORIES[ck]["label"], ck)
            c_idx = cat_combo.findData(e.get("category", "ignore"))
            if c_idx >= 0:
                cat_combo.setCurrentIndex(c_idx)
            cat_combo.currentIndexChanged.connect(lambda _, r=row, ri=real_idx, c=cat_combo: self._on_cat_changed(r, ri, c))
            self.table.setCellWidget(row, 3, cat_combo)

            sp_low = QDoubleSpinBox()
            sp_low.setRange(0.0, 1.0)
            sp_low.setSingleStep(0.05)
            sp_low.setValue(float(e.get("low", 0.0)))
            sp_low.valueChanged.connect(lambda v, ri=real_idx: self._set_field(ri, "low", round(v, 2)))
            self.table.setCellWidget(row, 4, sp_low)

            sp_high = QDoubleSpinBox()
            sp_high.setRange(0.0, 1.0)
            sp_high.setSingleStep(0.05)
            sp_high.setValue(float(e.get("high", 0.0)))
            sp_high.valueChanged.connect(lambda v, ri=real_idx: self._set_field(ri, "high", round(v, 2)))
            self.table.setCellWidget(row, 5, sp_high)

            sp_dur = QSpinBox()
            sp_dur.setRange(0, 1200)
            sp_dur.setSingleStep(20)
            sp_dur.setValue(int(e.get("duration_ms", 210)))
            sp_dur.valueChanged.connect(lambda v, ri=real_idx: self._set_field(ri, "duration_ms", int(v)))
            self.table.setCellWidget(row, 6, sp_dur)

        self.table.blockSignals(False)

    def _set_field(self, real_idx: int, key: str, val):
        self.profile_data["sfx_entries"][real_idx][key] = val

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() != 0:
            return
        real_idx = item.data(Qt.ItemDataRole.UserRole)
        if real_idx is not None:
            self.profile_data["sfx_entries"][real_idx]["enabled"] = (item.checkState() == Qt.CheckState.Checked)

    def _on_cat_changed(self, row: int, real_idx: int, combo: QComboBox):
        cat = combo.currentData()
        preset = RUMBLE_SFX_CATEGORIES[cat]
        e = self.profile_data["sfx_entries"][real_idx]
        e["category"] = cat
        e["enabled"] = (cat != "ignore")
        e["low"] = preset["low"]
        e["high"] = preset["high"]
        e["duration_ms"] = preset["duration_ms"]

        self.table.blockSignals(True)
        self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if e["enabled"] else Qt.CheckState.Unchecked)
        self.table.cellWidget(row, 4).setValue(e["low"])
        self.table.cellWidget(row, 5).setValue(e["high"])
        self.table.cellWidget(row, 6).setValue(e["duration_ms"])
        self.table.blockSignals(False)

    def _get_selected_entry(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Notice", "Select an SFX row first.")
            return None
        real_idx = self._visible_indices[rows[0].row()]
        return self.profile_data["sfx_entries"][real_idx]

    def play_selected_sfx(self):
        e = self._get_selected_entry()
        if not e or not self.rom_bytes:
            return
        start = int(e["data_offset"])
        length = int(e["length"])
        sr = max(4000, min(48000, int(e.get("sample_rate", 13379))))
        pcm_i8 = np.frombuffer(self.rom_bytes, dtype=np.int8, count=length, offset=start)
        i16 = pcm_i8.astype(np.int16) * 256
        stereo = np.column_stack((i16, i16))

        self._stop_evt.set()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=0.3)
        self._stop_evt.clear()

        def _play():
            try:
                import sounddevice as sd
                with sd.OutputStream(samplerate=sr, channels=2, dtype="int16", latency="low") as stream:
                    for i in range(0, len(stereo), 1024):
                        if self._stop_evt.is_set():
                            break
                        stream.write(stereo[i:i + 1024])
            except Exception:
                pass

        self._play_thread = threading.Thread(target=_play, daemon=True)
        self._play_thread.start()

    def test_selected_motor(self):
        e = self._get_selected_entry()
        if not e:
            return
        if not self.config.get("sensors", "rumble_enabled"):
            QMessageBox.warning(self, "Rumble Disabled", "Enable Sensors & Rumble → Controller Motor Rumble first.")
            return
        low = float(e.get("low", 0.0))
        high = float(e.get("high", 0.0))
        if low <= 0.0 and high <= 0.0:
            QMessageBox.information(self, "Ignored SFX", "This SFX row is set to Ignore (0.00 motor strength).")
            return
        dur = max(140, int(e.get("duration_ms", 210)))
        drv = self.driver_combo.currentData()
        print(f"[PROFILE TEST] {e.get('label')} -> low={low:.2f}, high={high:.2f}, dur={dur}ms, driver={drv}")
        if self.cm:
            self.cm.trigger_rumble(low, high, dur, driver_override=drv, force_log=True)
        if hasattr(self.mw, "sensor_hud"):
            self.mw.sensor_hud.on_rumble_triggered(low, high, dur)

    # =========================================================================
    # TAB 2: RAM ACTION & CONTROL-CODE FINDER (STATE-RETURN + STEP NARROWER)
    # =========================================================================
    def _read_combined_sysram_u8(self) -> np.ndarray | None:
        if not self.worker:
            return None
        ewram = self.worker.get_memory_region("EWRAM")
        iwram = self.worker.get_memory_region("IWRAM")
        if not ewram:
            return None
        e_arr = np.frombuffer(ewram[:256 * 1024], dtype=np.uint8)
        if len(e_arr) < 256 * 1024:
            pad = np.zeros(256 * 1024, dtype=np.uint8)
            pad[:len(e_arr)] = e_arr
            e_arr = pad
        i_arr = np.frombuffer(iwram[:32 * 1024], dtype=np.uint8) if iwram else np.zeros(32 * 1024, dtype=np.uint8)
        if len(i_arr) < 32 * 1024:
            pad = np.zeros(32 * 1024, dtype=np.uint8)
            pad[:len(i_arr)] = i_arr
            i_arr = pad
        return np.concatenate((e_arr, i_arr))

    @staticmethod
    def _offset_to_gba_addr(off: int) -> int:
        if off < 256 * 1024:
            return 0x02000000 + off
        return 0x03000000 + (off - 256 * 1024)

    def start_noise_sampling(self):
        if not self.worker:
            return
        snap = self._read_combined_sysram_u8()
        if snap is None:
            QMessageBox.warning(self, "Error", "Could not read EWRAM/IWRAM.")
            return

        self._idle_min_u8 = snap.copy()
        self._idle_max_u8 = snap.copy()
        self._idle_last_u8 = snap.copy()
        self._idle_changes_u8 = np.zeros(len(snap), dtype=np.uint16)

        u16 = snap.view(np.uint16)
        self._idle_min_u16 = u16.copy()
        self._idle_max_u16 = u16.copy()
        self._idle_last_u16 = u16.copy()
        self._idle_changes_u16 = np.zeros(len(u16), dtype=np.uint16)

        self._learn_mode = "noise"
        self._learn_deadline = time.monotonic() + 2.5

        self.btn_step1_noise.setEnabled(False)
        self.btn_step2_action.setEnabled(False)
        self._set_paused_state(False)
        self.setFocus()
        self._learn_timer.start()

    def start_action_capture(self):
        if not self.worker or self._idle_changes_u8 is None:
            return
        snap = self._read_combined_sysram_u8()
        if snap is None:
            return

        self._act_base_u8 = snap.copy()
        self._act_min_u8 = snap.copy()
        self._act_max_u8 = snap.copy()
        self._act_last_u8 = snap.copy()
        self._act_changed_u8 = np.zeros(len(snap), dtype=bool)
        self._act_novel_val_u8 = np.full(len(snap), -1, dtype=np.int32)

        u16 = snap.view(np.uint16)
        self._act_base_u16 = u16.copy()
        self._act_min_u16 = u16.copy()
        self._act_max_u16 = u16.copy()
        self._act_last_u16 = u16.copy()
        self._act_changed_u16 = np.zeros(len(u16), dtype=bool)
        self._act_novel_val_u16 = np.full(len(u16), -1, dtype=np.int32)

        self._learn_mode = "action"
        self._learn_deadline = time.monotonic() + 3.5

        self.btn_step1_noise.setEnabled(False)
        self.btn_step2_action.setEnabled(False)
        self._set_paused_state(False)
        self.setFocus()
        self._learn_timer.start()

    def _on_learn_tick(self):
        snap = self._read_combined_sysram_u8()
        if snap is None:
            return

        rem = max(0.0, self._learn_deadline - time.monotonic())

        if self._learn_mode == "noise":
            diff_u8 = snap != self._idle_last_u8
            self._idle_changes_u8 += diff_u8.astype(np.uint16)
            np.minimum(self._idle_min_u8, snap, out=self._idle_min_u8)
            np.maximum(self._idle_max_u8, snap, out=self._idle_max_u8)
            self._idle_last_u8 = snap

            u16 = snap.view(np.uint16)
            diff_u16 = u16 != self._idle_last_u16
            self._idle_changes_u16 += diff_u16.astype(np.uint16)
            np.minimum(self._idle_min_u16, u16, out=self._idle_min_u16)
            np.maximum(self._idle_max_u16, u16, out=self._idle_max_u16)
            self._idle_last_u16 = u16

            self.learn_status_lbl.setText(
                f"Step 1: Walk around & stand still... {rem:.1f}s left (Keyboard/Gamepad active!)"
            )
            if rem <= 0.0:
                self._learn_timer.stop()
                self._set_paused_state(True)
                self._learn_mode = None
                self.btn_step1_noise.setEnabled(True)
                self.btn_step2_action.setEnabled(True)
                static_cnt = int(np.count_nonzero(self._idle_changes_u8[:IWRAM_STACK_CUTOFF_OFF] == 0))
                small_enum_cnt = int(np.count_nonzero(
                    (self._idle_changes_u8[:IWRAM_STACK_CUTOFF_OFF] > 0)
                    & ((self._idle_max_u8[:IWRAM_STACK_CUTOFF_OFF] - self._idle_min_u8[:IWRAM_STACK_CUTOFF_OFF]) <= 6)
                ))
                self.learn_status_lbl.setText(
                    f"✅ Step 1 Done ({static_cnt:,} static + {small_enum_cnt:,} walk-state bytes tracked). "
                    f"Click Step 2, perform your attack/hit, then release!"
                )

        elif self._learn_mode == "action":
            self._act_changed_u8 |= (snap != self._act_last_u8)
            np.minimum(self._act_min_u8, snap, out=self._act_min_u8)
            np.maximum(self._act_max_u8, snap, out=self._act_max_u8)
            outside_u8 = (self._act_novel_val_u8 < 0) & ((snap < self._idle_min_u8) | (snap > self._idle_max_u8))
            if np.any(outside_u8):
                self._act_novel_val_u8[outside_u8] = snap[outside_u8].astype(np.int32)
            self._act_last_u8 = snap

            u16 = snap.view(np.uint16)
            self._act_changed_u16 |= (u16 != self._act_last_u16)
            np.minimum(self._act_min_u16, u16, out=self._act_min_u16)
            np.maximum(self._act_max_u16, u16, out=self._act_max_u16)
            outside_u16 = (self._act_novel_val_u16 < 0) & ((u16 < self._idle_min_u16) | (u16 > self._idle_max_u16))
            if np.any(outside_u16):
                self._act_novel_val_u16[outside_u16] = u16[outside_u16].astype(np.int32)
            self._act_last_u16 = u16

            self.learn_status_lbl.setText(
                f"🔴 CAPTURING ACTION ({rem:.1f}s left) — Attack / Get Hit now, then release to Idle!"
            )
            if rem <= 0.0:
                self._learn_timer.stop()
                self._set_paused_state(True)
                self._learn_mode = None
                self.btn_step1_noise.setEnabled(True)
                self.btn_step2_action.setEnabled(True)
                self._finalize_action_candidates()

    def _finalize_action_candidates(self):
        limit = IWRAM_STACK_CUTOFF_OFF
        idle_chg = self._idle_changes_u8[:limit]
        idle_span = (self._idle_max_u8[:limit].astype(np.int16) - self._idle_min_u8[:limit].astype(np.int16))

        clean_idle = (idle_chg == 0) | ((idle_span <= 6) & (idle_chg <= 16))
        novel_hits = clean_idle & (self._act_novel_val_u8[:limit] >= 0)

        buckets = {"equals": [], "zero_to_nonzero": [], "decreased": [], "other": []}

        for b_off_np in np.where(novel_hits)[0]:
            b_off = int(b_off_np)
            idle_lo = int(self._idle_min_u8[b_off])
            idle_hi = int(self._idle_max_u8[b_off])
            novel_v = int(self._act_novel_val_u8[b_off])
            end_v = int(self._act_last_u8[b_off])
            mn = int(self._act_min_u8[b_off])
            mx = int(self._act_max_u8[b_off])
            returned_to_idle = (idle_lo <= end_v <= idle_hi)

            if idle_lo == 0 and idle_hi == 0 and mx > 0 and returned_to_idle:
                buckets["zero_to_nonzero"].append({
                    "address": self._offset_to_gba_addr(b_off),
                    "width": 1,
                    "trigger": "zero_to_nonzero",
                    "target_val": 0,
                    "desc": f"0 -> {mx} -> 0 (Action/Hit Timer)",
                    "cat": "Hitstun / Shake / Flash Timer",
                })
            elif returned_to_idle and novel_v <= 64:
                buckets["equals"].append({
                    "address": self._offset_to_gba_addr(b_off),
                    "width": 1,
                    "trigger": "equals",
                    "target_val": novel_v,
                    "desc": f"Idle[{idle_lo}..{idle_hi}] -> {novel_v} (0x{novel_v:02X}) -> {end_v}",
                    "cat": f"Action Control Code == {novel_v}",
                })
            elif idle_lo == 0 and idle_hi == 0 and mx > 0:
                buckets["zero_to_nonzero"].append({
                    "address": self._offset_to_gba_addr(b_off),
                    "width": 1,
                    "trigger": "zero_to_nonzero",
                    "target_val": 0,
                    "desc": f"0 -> peak {mx} (end {end_v})",
                    "cat": "Timer / Active Flag",
                })
            elif idle_lo == idle_hi and mn < idle_lo and end_v < idle_lo:
                buckets["decreased"].append({
                    "address": self._offset_to_gba_addr(b_off),
                    "width": 1,
                    "trigger": "decreased",
                    "target_val": 1,
                    "desc": f"{idle_lo} -> {end_v} (min {mn})",
                    "cat": "HP / Shield / Counter",
                })
            else:
                buckets["other"].append({
                    "address": self._offset_to_gba_addr(b_off),
                    "width": 1,
                    "trigger": "changed",
                    "target_val": 0,
                    "desc": f"Idle[{idle_lo}..{idle_hi}] -> {novel_v} (end {end_v})",
                    "cat": "State / Variable",
                })

        limit16 = limit // 2
        clean16_static = (self._idle_changes_u16[:limit16] == 0) & self._act_changed_u16[:limit16]
        for w_idx_np in np.where(clean16_static)[0]:
            w_idx = int(w_idx_np)
            b0 = int(self._act_base_u16[w_idx])
            mn = int(self._act_min_u16[w_idx])
            mx = int(self._act_max_u16[w_idx])
            end_v = int(self._act_last_u16[w_idx])
            if mx == b0 and mn < b0 and b0 >= 256:
                buckets["decreased"].append({
                    "address": self._offset_to_gba_addr(w_idx * 2),
                    "width": 2,
                    "trigger": "decreased",
                    "target_val": 1,
                    "desc": f"{b0} -> {end_v} (min {mn})",
                    "cat": "16-bit HP / Shield",
                })

        merged = (
            buckets["equals"][:35]
            + buckets["zero_to_nonzero"][:35]
            + buckets["decreased"][:35]
            + buckets["other"][:20]
        )
        self._populate_candidates_table(merged)
        self.learn_status_lbl.setText(
            f"✅ Found {len(buckets['equals'])} Action Code(s), {len(buckets['zero_to_nonzero'])} Timer(s), "
            f"{len(buckets['decreased'])} Decreased(HP), {len(buckets['other'])} Other (showing {len(merged)})."
        )

    # =========================================================================
    # STEP-BY-STEP CHEAT-STYLE NARROWER (100% RELIABLE FOR HP / TIMERS)
    # =========================================================================
    def narrow_reset_snapshot(self):
        snap = self._read_combined_sysram_u8()
        if snap is None:
            return
        self._narrow_snap_u8 = snap[:IWRAM_STACK_CUTOFF_OFF].copy()
        self._narrow_cands = []
        self._learned_candidates = []
        self.cand_table.setRowCount(0)
        self.learn_status_lbl.setText(
            "📸 Snapshot captured! Unpause, perform the action / take a hit, then click '📉 Decreased' or '≠ Changed'."
        )

    def narrow_filter(self, mode: str):
        snap = self._read_combined_sysram_u8()
        if snap is None or self._narrow_snap_u8 is None:
            QMessageBox.information(self, "Notice", "Click '📸 Reset / Snapshot' first.")
            return

        cur = snap[:IWRAM_STACK_CUTOFF_OFF]
        prev = self._narrow_snap_u8

        if not self._narrow_cands:
            if mode == "decreased":
                mask = cur < prev
            elif mode == "changed":
                mask = cur != prev
            elif mode == "unchanged":
                mask = cur == prev
            elif mode == "zero_to_nonzero":
                mask = (prev == 0) & (cur > 0)
            else:
                return
            self._narrow_cands = [(int(off), 1) for off in np.where(mask)[0]]
        else:
            kept = []
            for off, width in self._narrow_cands:
                pv = int(prev[off])
                cv = int(cur[off])
                if mode == "decreased" and cv < pv:
                    kept.append((off, width))
                elif mode == "changed" and cv != pv:
                    kept.append((off, width))
                elif mode == "unchanged" and cv == pv:
                    kept.append((off, width))
                elif mode == "zero_to_nonzero" and pv == 0 and cv > 0:
                    kept.append((off, width))
            self._narrow_cands = kept

        trig_map = {
            "decreased": ("decreased", 1, "HP / Shield / Counter"),
            "zero_to_nonzero": ("zero_to_nonzero", 0, "Hitstun / Shake Timer"),
            "changed": ("changed", 0, "State / Control Code"),
            "unchanged": ("changed", 0, "Filtered Candidate"),
        }
        trig, tgt, cat = trig_map[mode]
        rows = []
        for off, width in self._narrow_cands[:120]:
            pv = int(prev[off])
            cv = int(cur[off])
            rows.append({
                "address": self._offset_to_gba_addr(off),
                "width": width,
                "trigger": trig,
                "target_val": tgt,
                "desc": f"{pv} -> {cv}",
                "cat": cat,
            })

        self._narrow_snap_u8 = cur.copy()
        self._populate_candidates_table(rows)
        self.learn_status_lbl.setText(
            f"Narrowed to {len(self._narrow_cands):,} byte(s) (showing top {len(rows)}). Repeat filter to narrow further!"
        )

    def _populate_candidates_table(self, candidates: list):
        self._learned_candidates = list(candidates)
        self.cand_table.setRowCount(len(self._learned_candidates))
        for r, c in enumerate(self._learned_candidates):
            self.cand_table.setItem(r, 0, QTableWidgetItem(f"0x{c['address']:08X}"))
            self.cand_table.setItem(r, 1, QTableWidgetItem(f"{c['width']}B"))
            self.cand_table.setItem(r, 2, QTableWidgetItem("--"))
            self.cand_table.setItem(r, 3, QTableWidgetItem(c["trigger"]))
            self.cand_table.setItem(r, 4, QTableWidgetItem(c["desc"]))
            self.cand_table.setItem(r, 5, QTableWidgetItem(c["cat"]))
        self._refresh_live_values()

    def _read_addr_live_val(self, addr: int, width: int) -> str:
        if not self.worker:
            return "--"
        off = _resolve_sysram_offset(addr)
        if off is None:
            return "--"
        if off < 256 * 1024:
            raw = self.worker.get_memory_region("EWRAM")
            local_off = off
        else:
            raw = self.worker.get_memory_region("IWRAM")
            local_off = off - (256 * 1024)
        if not raw or local_off + width > len(raw):
            return "--"
        if width == 1:
            v = raw[local_off]
            return f"{v} (0x{v:02X})"
        if width == 2:
            v = struct.unpack_from("<H", raw, local_off)[0]
            return f"{v} (0x{v:04X})"
        v = struct.unpack_from("<I", raw, local_off)[0]
        return f"{v} (0x{v:08X})"

    def _refresh_live_values(self):
        if not self.worker or self.tabs.currentIndex() != 1:
            return
        for r, c in enumerate(self._learned_candidates):
            item = self.cand_table.item(r, 2)
            if item:
                txt = self._read_addr_live_val(c["address"], c["width"])
                if item.text() != txt:
                    item.setText(txt)

        ev_list = self.profile_data.get("ram_events", [])
        for r, ev in enumerate(ev_list):
            item = self.ram_table.item(r, 3)
            if item:
                txt = self._read_addr_live_val(int(ev.get("address", 0)), int(ev.get("width", 1)))
                if item.text() != txt:
                    item.setText(txt)

    def remove_selected_candidates(self):
        rows = sorted({idx.row() for idx in self.cand_table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            return

        removed_keys = set()
        for r in rows:
            if 0 <= r < len(self._learned_candidates):
                cand = self._learned_candidates.pop(r)
                off = _resolve_sysram_offset(cand["address"])
                if off is not None:
                    removed_keys.add((off, cand["width"]))

        if self._narrow_cands and removed_keys:
            self._narrow_cands = [pair for pair in self._narrow_cands if pair not in removed_keys]

        self._populate_candidates_table(self._learned_candidates)
        self.learn_status_lbl.setText(
            f"🗑 Removed {len(rows)} candidate(s) ({len(self._learned_candidates)} remaining)."
        )

    def clear_candidates(self):
        self._learned_candidates = []
        self._narrow_cands = []
        self.cand_table.setRowCount(0)
        self.learn_status_lbl.setText("Cleared candidate list.")

    def add_selected_candidate(self):
        selected = sorted({idx.row() for idx in self.cand_table.selectionModel().selectedRows()})
        if not selected:
            QMessageBox.information(self, "Notice", "Select one or more candidate rows first (Ctrl/Shift+Click).")
            return

        ev_list = self.profile_data.setdefault("ram_events", [])
        existing_keys = {_ram_event_key(e) for e in ev_list}

        added = 0
        skipped_dup = 0
        base_ts = int(time.time() * 1000) % 100000

        for idx_pos, r in enumerate(selected):
            if not (0 <= r < len(self._learned_candidates)):
                continue
            cand = self._learned_candidates[r]
            ev = {
                "id": f"evt_{(base_ts + idx_pos) % 100000}",
                "label": cand["cat"],
                "address": cand["address"],
                "width": cand["width"],
                "trigger": cand["trigger"],
                "target_val": int(cand.get("target_val", 0)),
                "enabled": True,
                "low": 1.0 if cand["trigger"] == "decreased" else 0.90,
                "high": 0.95 if cand["trigger"] == "decreased" else 1.0,
                "duration_ms": 380 if cand["trigger"] == "decreased" else 280,
            }
            key = _ram_event_key(ev)
            if key in existing_keys:
                skipped_dup += 1
                continue
            existing_keys.add(key)
            ev_list.append(ev)
            added += 1

        if added > 0:
            self.refresh_ram_table()

        if added > 0 and skipped_dup > 0:
            self.learn_status_lbl.setText(f"✅ Added {added} rule(s), skipped {skipped_dup} duplicate(s).")
        elif added > 0:
            self.learn_status_lbl.setText(f"✅ Added {added} RAM rule(s) to profile.")
        else:
            self.learn_status_lbl.setText("ℹ All selected candidate(s) are already in the profile (duplicates skipped).")

    def add_manual_ram_event(self):
        dlg = _AddManualRamEventDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ev = dlg.get_event()
            if not ev:
                QMessageBox.warning(self, "Invalid Address", "Address must be in EWRAM (0x02xxxxxx) or IWRAM (0x03xxxxxx).")
                return
            ev_list = self.profile_data.setdefault("ram_events", [])
            if _ram_event_key(ev) in {_ram_event_key(e) for e in ev_list}:
                QMessageBox.information(self, "Duplicate Rule", "An identical RAM rule already exists in this profile.")
                return
            ev_list.append(ev)
            self.refresh_ram_table()

    def remove_selected_ram_event(self):
        rows = sorted({idx.row() for idx in self.ram_table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            return
        ev_list = self.profile_data.get("ram_events", [])
        for r in rows:
            if 0 <= r < len(ev_list):
                del ev_list[r]
        self.refresh_ram_table()

    def refresh_ram_table(self):
        ev_list = self.profile_data.get("ram_events", [])
        self.ram_table.blockSignals(True)
        self.ram_table.setRowCount(len(ev_list))

        for r, ev in enumerate(ev_list):
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            cb.setCheckState(Qt.CheckState.Checked if ev.get("enabled", True) else Qt.CheckState.Unchecked)
            self.ram_table.setItem(r, 0, cb)

            self.ram_table.setItem(r, 1, QTableWidgetItem(str(ev.get("label", ""))))

            addr_item = QTableWidgetItem(f"0x{int(ev.get('address', 0)):08X}")
            addr_item.setFlags(addr_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.ram_table.setItem(r, 2, addr_item)

            live_item = QTableWidgetItem("--")
            live_item.setFlags(live_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.ram_table.setItem(r, 3, live_item)

            trig_combo = QComboBox()
            for k, lbl in RAM_EVENT_TRIGGERS:
                trig_combo.addItem(lbl, k)
            t_idx = trig_combo.findData(ev.get("trigger", "changed"))
            if t_idx >= 0:
                trig_combo.setCurrentIndex(t_idx)
            trig_combo.currentIndexChanged.connect(lambda _, row=r, c=trig_combo: self._set_ram_field(row, "trigger", c.currentData()))
            self.ram_table.setCellWidget(r, 4, trig_combo)

            sp_tgt = QSpinBox()
            sp_tgt.setRange(0, 65535)
            sp_tgt.setValue(int(ev.get("target_val", 0)))
            sp_tgt.valueChanged.connect(lambda v, row=r: self._set_ram_field(row, "target_val", int(v)))
            self.ram_table.setCellWidget(r, 5, sp_tgt)

            lh_item = QTableWidgetItem(f"{float(ev.get('low', 1.0)):.2f} / {float(ev.get('high', 0.95)):.2f}")
            lh_item.setFlags(lh_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.ram_table.setItem(r, 6, lh_item)

            sp_dur = QSpinBox()
            sp_dur.setRange(60, 1200)
            sp_dur.setSingleStep(20)
            sp_dur.setValue(int(ev.get("duration_ms", 340)))
            sp_dur.valueChanged.connect(lambda v, row=r: self._set_ram_field(row, "duration_ms", int(v)))
            self.ram_table.setCellWidget(r, 7, sp_dur)

        self.ram_table.blockSignals(False)
        self._refresh_live_values()

    def _set_ram_field(self, row: int, key: str, val):
        ev_list = self.profile_data.get("ram_events", [])
        if 0 <= row < len(ev_list):
            ev_list[row][key] = val

    def _on_ram_item_changed(self, item: QTableWidgetItem):
        row, col = item.row(), item.column()
        ev_list = self.profile_data.get("ram_events", [])
        if not (0 <= row < len(ev_list)):
            return
        if col == 0:
            ev_list[row]["enabled"] = (item.checkState() == Qt.CheckState.Checked)
        elif col == 1:
            ev_list[row]["label"] = item.text().strip()

    # =========================================================================
    # SAVE & LIFECYCLE
    # =========================================================================
    def save_and_activate(self):
        if not self.worker:
            return
        self.config.set("sensors", "sim_rumble_disable_gfx_with_ram", self.cb_disable_gfx_with_ram.isChecked())
        self.worker.rumble_sim.profile_store.save_profile(self.profile_data, self.profile_id)
        self.worker.reload_rumble_profile()
        self.worker.update_rumble_sim_settings()
        sfx_cnt = len(self.worker.rumble_sim.profile_store._active_entries)
        evt_cnt = len(self.worker.rumble_sim.profile_store._active_ram_events)
        self.mw.video_widget.show_osd(f"📳 Saved Profile ({sfx_cnt} SFX, {evt_cnt} RAM Events)", 3000)
        QMessageBox.information(
            self,
            "Profile Saved",
            f"Saved {sfx_cnt} SFX trigger(s) and {evt_cnt} RAM event rule(s) to:\n"
            f"{self.worker.rumble_sim.profile_store.profile_path}"
        )

    def _restore_worker(self):
        if self._learn_timer.isActive():
            self._learn_timer.stop()
        if self._live_val_timer.isActive():
            self._live_val_timer.stop()
        if self.worker:
            self.worker.is_paused = self._was_paused

    def accept(self):
        self._restore_worker()
        super().accept()

    def reject(self):
        self._restore_worker()
        super().reject()

    def closeEvent(self, event):
        self._stop_evt.set()
        self._restore_worker()
        event.accept()

### Part 2 of 2: `rumble_profile_dialog.py`, `menu_actions.py`, & `settings_actions.py`

#### 4. `gba_studio/tools/rumble_profile_dialog.py`
```python
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
```

---

#### 5. `gba_studio/ui/menu_actions.py`
```python
# menu_actions.py
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence

from ..tools.dev_tools import (
    PaletteViewerDialog, SpriteViewerDialog, TileViewerDialog,
    MapViewerDialog, FrameInspectorDialog, MemoryViewerDialog,
    MemorySearchDialog, IORegisterViewerDialog
)


class MenuBuilderMixin:
    def _create_menus(self):
        menubar = self.menuBar()

        # 1. FILE MENU
        file_menu = menubar.addMenu("File")

        load_action = QAction("Load ROM...", self)
        load_action.setShortcut(QKeySequence("Ctrl+O"))
        load_action.triggered.connect(self.load_rom)
        file_menu.addAction(load_action)

        self.recent_menu = file_menu.addMenu("Open Recent")

        apply_patch_act = QAction("Apply Soft-Patch (.ips / .bps / .ups)...", self)
        apply_patch_act.triggered.connect(self.apply_patch_dialog)
        file_menu.addAction(apply_patch_act)

        file_menu.addSeparator()

        save_file_act = QAction("Save State to File...", self)
        save_file_act.triggered.connect(self.save_state_to_file)
        file_menu.addAction(save_file_act)

        load_file_act = QAction("Load State from File...", self)
        load_file_act.triggered.connect(self.load_state_from_file)
        file_menu.addAction(load_file_act)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 2. EMULATION MENU
        emu_menu = menubar.addMenu("Emulation")

        self.pause_action = QAction("Pause", self, checkable=True)
        self.pause_action.setShortcut(QKeySequence("Ctrl+P"))
        self.pause_action.triggered.connect(self.toggle_pause)
        emu_menu.addAction(self.pause_action)

        self.pause_unfocus_action = QAction("Pause When Unfocused / Inactive", self, checkable=True)
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))
        self.pause_unfocus_action.triggered.connect(self.toggle_pause_on_unfocus)
        emu_menu.addAction(self.pause_unfocus_action)

        step_act = QAction("Step Frame", self)
        step_act.setShortcut(QKeySequence("Ctrl+N"))
        step_act.triggered.connect(self.step_frame)
        emu_menu.addAction(step_act)

        reset_action = QAction("Reset", self)
        reset_action.setShortcut(QKeySequence("Ctrl+R"))
        reset_action.triggered.connect(self.reset_emulation)
        emu_menu.addAction(reset_action)

        emu_menu.addSeparator()

        self.rewind_enabled_action = QAction("Enable Rewind Buffer (Hold: Space or `)", self, checkable=True)
        self.rewind_enabled_action.setChecked(bool(self.config.get("emulation", "rewind_enabled", True)))
        self.rewind_enabled_action.triggered.connect(self.toggle_rewind_enabled)
        emu_menu.addAction(self.rewind_enabled_action)

        self.auto_patch_action = QAction("Auto Soft-Patch (.ips / .bps / .ups)", self, checkable=True)
        self.auto_patch_action.setChecked(bool(self.config.get("emulation", "auto_soft_patch", True)))
        self.auto_patch_action.triggered.connect(self.toggle_auto_soft_patch)
        emu_menu.addAction(self.auto_patch_action)

        emu_menu.addSeparator()

        self.skip_bios_action = QAction("Skip BIOS Intro", self, checkable=True)
        self.skip_bios_action.setChecked(bool(self.config.get("emulation", "skip_bios")))
        self.skip_bios_action.triggered.connect(self.toggle_skip_bios)
        emu_menu.addAction(self.skip_bios_action)

        emu_menu.addSeparator()

        quick_save_act = QAction("Quick Save State", self)
        quick_save_act.setShortcut(QKeySequence("Shift+F10"))
        quick_save_act.triggered.connect(lambda: self.quick_save_state(self.current_slot))
        emu_menu.addAction(quick_save_act)

        quick_load_act = QAction("Quick Load State", self)
        quick_load_act.setShortcut(QKeySequence("F10"))
        quick_load_act.triggered.connect(lambda: self.quick_load_state(self.current_slot))
        emu_menu.addAction(quick_load_act)

        state_browser_act = QAction("Save State Slot Browser...", self)
        state_browser_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        state_browser_act.triggered.connect(self.open_state_browser)
        emu_menu.addAction(state_browser_act)

        slot_menu = emu_menu.addMenu("Select State Slot")
        self.slot_group = QActionGroup(self)
        self.slot_group.setExclusive(True)
        self.slot_actions = {}

        for slot_num in range(1, 10):
            slot_act = QAction(f"Slot {slot_num}", self, checkable=True)
            slot_act.triggered.connect(lambda checked, s=slot_num: self.set_active_slot(s))
            self.slot_group.addAction(slot_act)
            slot_menu.addAction(slot_act)
            self.slot_actions[slot_num] = slot_act

        emu_menu.addSeparator()

        self.ff_action = QAction("Fast Forward (Hotkey: = / -)", self, checkable=True)
        self.ff_action.triggered.connect(self.toggle_ff)
        emu_menu.addAction(self.ff_action)

        ff_speed_menu = emu_menu.addMenu("Fast Forward Speed")
        self.ff_group = QActionGroup(self)
        self.ff_group.setExclusive(True)
        self.ff_actions = {}

        speeds = [1.5, 2.0, 3.0, 4.0, 8.0, 10.0]
        for spd in speeds:
            spd_act = QAction(f"{spd}x", self, checkable=True)
            spd_act.triggered.connect(lambda checked, s=spd: self.set_ff_speed(s))
            self.ff_group.addAction(spd_act)
            ff_speed_menu.addAction(spd_act)
            self.ff_actions[spd] = spd_act

        act_ff_up = QAction("Faster Fast-Forward Speed (+)", self)
        act_ff_up.setShortcut(QKeySequence("="))
        act_ff_up.triggered.connect(lambda: self.cycle_ff_speed(forward=True))
        ff_speed_menu.addAction(act_ff_up)

        act_ff_down = QAction("Slower Fast-Forward Speed (-)", self)
        act_ff_down.setShortcut(QKeySequence("-"))
        act_ff_down.triggered.connect(lambda: self.cycle_ff_speed(forward=False))
        ff_speed_menu.addAction(act_ff_down)

        emu_menu.addSeparator()
        replay_menu = emu_menu.addMenu("Gameplay Replay & TAS")

        self.act_record_session = QAction("⏺ Start Recording Session...", self)
        self.act_record_session.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.act_record_session.triggered.connect(self.toggle_session_recording)
        replay_menu.addAction(self.act_record_session)

        self.act_play_session = QAction("▶ Load & Play Replay (.json)...", self)
        self.act_play_session.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.act_play_session.triggered.connect(self.load_replay_session)
        replay_menu.addAction(self.act_play_session)

        self.act_stop_replay = QAction("⏹ Stop Current Replay", self)
        self.act_stop_replay.triggered.connect(self.stop_replay_session)
        replay_menu.addAction(self.act_stop_replay)

        replay_menu.addSeparator()

        self.act_tas_editor = QAction("🎹 Piano-Roll TAS Timeline Editor...", self)
        self.act_tas_editor.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.act_tas_editor.triggered.connect(self.open_tas_editor)
        replay_menu.addAction(self.act_tas_editor)

        # 3. SENSORS & PERIPHERALS MENU
        sensors_menu = menubar.addMenu("Sensors & Rumble")

        self.act_sensor_hud = QAction("Show Sensor / Rumble Status Panel", self, checkable=True)
        self.act_sensor_hud.setChecked(bool(self.config.get("sensors", "show_hud", True)))
        self.act_sensor_hud.triggered.connect(self.toggle_sensor_hud)
        sensors_menu.addAction(self.act_sensor_hud)
        sensors_menu.addSeparator()

        solar_menu = sensors_menu.addMenu("☀️ Solar Sensor (Boktai)")
        self.solar_group = QActionGroup(self)
        self.solar_group.setExclusive(True)
        self.solar_actions = {}

        for lvl in range(11):
            act = QAction(
                f"Sun Level {lvl}" + (" (Maximum)" if lvl == 10 else (" (Dark)" if lvl == 0 else "")),
                self, checkable=True
            )
            act.triggered.connect(lambda checked, l=lvl: self.set_solar_level(l))
            self.solar_group.addAction(act)
            solar_menu.addAction(act)
            self.solar_actions[lvl] = act

        act_brighten = QAction("Brighten Sunlight (+1)", self)
        act_brighten.setShortcut(QKeySequence("]"))
        act_brighten.triggered.connect(lambda: self.adjust_solar_sensor(1))
        solar_menu.addAction(act_brighten)

        act_darken = QAction("Darken Sunlight (-1)", self)
        act_darken.setShortcut(QKeySequence("["))
        act_darken.triggered.connect(lambda: self.adjust_solar_sensor(-1))
        solar_menu.addAction(act_darken)

        sensors_menu.addSeparator()

        self.act_rumble = QAction("🎮 Controller Motor Rumble", self, checkable=True)
        self.act_rumble.setChecked(bool(self.config.get("sensors", "rumble_enabled")))
        self.act_rumble.triggered.connect(self.toggle_rumble)
        sensors_menu.addAction(self.act_rumble)

        sim_rumble_menu = sensors_menu.addMenu("📳 Simulated Rumble (Non-Rumble Games)")

        act_gen_profile = QAction("🛠 Generate / Edit Game SFX & RAM Rumble Profile...", self)
        act_gen_profile.triggered.connect(self.open_rumble_profile_dialog)
        sim_rumble_menu.addAction(act_gen_profile)

        self.act_sim_use_profile = QAction("Use ROM SFX / RAM Profile (When Available)", self, checkable=True)
        self.act_sim_use_profile.setChecked(bool(self.config.get("sensors", "sim_rumble_use_profile", True)))
        self.act_sim_use_profile.triggered.connect(self.toggle_sim_use_profile)
        sim_rumble_menu.addAction(self.act_sim_use_profile)

        self.act_sim_disable_gfx_with_ram = QAction(
            "Disable GFX/Audio Sim When RAM Profile Active", self, checkable=True
        )
        self.act_sim_disable_gfx_with_ram.setChecked(
            bool(self.config.get("sensors", "sim_rumble_disable_gfx_with_ram", False))
        )
        self.act_sim_disable_gfx_with_ram.triggered.connect(self.toggle_sim_disable_gfx_with_ram)
        sim_rumble_menu.addAction(self.act_sim_disable_gfx_with_ram)

        sim_rumble_menu.addSeparator()

        sim_mode_menu = sim_rumble_menu.addMenu("Activation Mode")
        self.sim_rumble_mode_group = QActionGroup(self)
        self.sim_rumble_mode_group.setExclusive(True)
        self.sim_rumble_mode_actions = {}
        for mode_key, lbl in [
            ("auto", "Auto (Games Without Native Rumble)"),
            ("always", "Always On (All Games)"),
            ("off", "Off (Native Rumble Only)"),
        ]:
            act = QAction(lbl, self, checkable=True)
            act.triggered.connect(lambda checked, m=mode_key: self.set_sim_rumble_mode(m))
            self.sim_rumble_mode_group.addAction(act)
            sim_mode_menu.addAction(act)
            self.sim_rumble_mode_actions[mode_key] = act

        sim_prof_menu = sim_rumble_menu.addMenu("Detection Profile")
        self.sim_rumble_profile_group = QActionGroup(self)
        self.sim_rumble_profile_group.setExclusive(True)
        self.sim_rumble_profile_actions = {}
        for prof_key, lbl in [
            ("balanced", "Balanced (Hits, Shakes, Magic & Items)"),
            ("impacts_only", "Heavy Hits & Screen Shake Only"),
            ("rpg_magic", "Sensitive RPG / Action (Extra Magic & Hits)"),
        ]:
            act = QAction(lbl, self, checkable=True)
            act.triggered.connect(lambda checked, p=prof_key: self.set_sim_rumble_profile(p))
            self.sim_rumble_profile_group.addAction(act)
            sim_prof_menu.addAction(act)
            self.sim_rumble_profile_actions[prof_key] = act

        sim_sens_menu = sim_rumble_menu.addMenu("Intensity / Sensitivity")
        self.sim_rumble_sens_group = QActionGroup(self)
        self.sim_rumble_sens_group.setExclusive(True)
        self.sim_rumble_sens_actions = {}
        for sens_val, lbl in [
            (0.6, "Low (60%)"),
            (1.0, "Normal (100%)"),
            (1.4, "High (140%)"),
            (1.8, "Max (180%)"),
        ]:
            act = QAction(lbl, self, checkable=True)
            act.triggered.connect(lambda checked, s=sens_val: self.set_sim_rumble_sensitivity(s))
            self.sim_rumble_sens_group.addAction(act)
            sim_sens_menu.addAction(act)
            self.sim_rumble_sens_actions[sens_val] = act

        self.act_mouse_gyro = QAction("🖱️ Mouse Twist to Gyro (Right-Click Drag)", self, checkable=True)
        self.act_mouse_gyro.setChecked(bool(self.config.get("sensors", "mouse_gyro")))
        self.act_mouse_gyro.triggered.connect(self.toggle_mouse_gyro)
        sensors_menu.addAction(self.act_mouse_gyro)

        act_reset_tilt = QAction("🎯 Center Gyro & Tilt Sensor", self)
        act_reset_tilt.setShortcut(QKeySequence("Backspace"))
        act_reset_tilt.triggered.connect(self.reset_sensors)
        sensors_menu.addAction(act_reset_tilt)

        # 4. AUDIO MENU
        audio_menu = menubar.addMenu("Audio")
        self.mute_action = QAction("Mute", self, checkable=True)
        self.mute_action.setChecked(self.config.get("audio", "mute"))
        self.mute_action.triggered.connect(self.toggle_mute)
        audio_menu.addAction(self.mute_action)

        self.ff_pitch_action = QAction("Pitch-Corrected Fast-Forward Audio (WSOLA)", self, checkable=True)
        self.ff_pitch_action.setChecked(bool(self.config.get("audio", "fast_forward_pitch_corrected", True)))
        self.ff_pitch_action.triggered.connect(self.toggle_ff_pitch_corrected)
        audio_menu.addAction(self.ff_pitch_action)

        audio_menu.addSeparator()

        lpf_menu = audio_menu.addMenu("Low-Pass Filter")
        self.lpf_group = QActionGroup(self)
        self.lpf_group.setExclusive(True)
        self.lpf_actions = {}

        lpf_modes = [
            ("off", "Off (Raw DirectSound)"),
            ("light", "Light (~12 kHz - Mild Hiss Reduction)"),
            ("medium", "Medium (~8 kHz - Warm Speaker Sound)"),
            ("heavy", "Heavy (~5 kHz - Smooth Vintage Sound)")
        ]
        for mode_key, label in lpf_modes:
            act = QAction(label, self, checkable=True)
            act.triggered.connect(lambda checked, m=mode_key: self.set_lpf_mode(m))
            self.lpf_group.addAction(act)
            lpf_menu.addAction(act)
            self.lpf_actions[mode_key] = act

        enh_menu = audio_menu.addMenu("Enhancements")
        self.widener_action = QAction("Stereo Widener (Surround Sound)", self, checkable=True)
        self.widener_action.triggered.connect(self.toggle_stereo_widener)
        enh_menu.addAction(self.widener_action)

        self.bass_action = QAction("Bass Boost (Low-End Warmth)", self, checkable=True)
        self.bass_action.triggered.connect(self.toggle_bass_boost)
        enh_menu.addAction(self.bass_action)

        self.limiter_action = QAction("Soft Limiter (Anti-Clipping)", self, checkable=True)
        self.limiter_action.triggered.connect(self.toggle_soft_limiter)
        enh_menu.addAction(self.limiter_action)

        vol_menu = audio_menu.addMenu("Volume Gain")
        self.vol_group = QActionGroup(self)
        self.vol_group.setExclusive(True)
        self.vol_actions = {}

        vol_levels = [0.25, 0.5, 0.75, 1.0, 1.25]
        for vol in vol_levels:
            lbl = f"{int(vol * 100)}%" + (" (Default)" if abs(vol - 0.5) < 0.01 else "")
            act = QAction(lbl, self, checkable=True)
            act.triggered.connect(lambda checked, v=vol: self.set_volume_gain(v))
            self.vol_group.addAction(act)
            vol_menu.addAction(act)
            self.vol_actions[vol] = act

        # 5. VIDEO MENU
        video_menu = menubar.addMenu("Video")

        self.fullscreen_action = QAction("Fullscreen (Alt+Enter / F11)", self, checkable=True)
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        video_menu.addAction(self.fullscreen_action)

        self.integer_scale_action = QAction("Integer Scaling Only", self, checkable=True)
        self.integer_scale_action.setChecked(bool(self.config.get("video", "integer_scaling", False)))
        self.integer_scale_action.triggered.connect(self.toggle_integer_scaling)
        video_menu.addAction(self.integer_scale_action)

        self.input_overlay_action = QAction("Always Show Live Input & Frame HUD", self, checkable=True)
        self.input_overlay_action.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self.input_overlay_action.setChecked(bool(self.config.get("video", "show_input_overlay", False)))
        self.input_overlay_action.triggered.connect(self.toggle_input_overlay)
        video_menu.addAction(self.input_overlay_action)

        video_menu.addSeparator()

        shader_menu = video_menu.addMenu("RetroArch Shaders")

        self.act_shader_toggle = QAction("Enable Shaders", self, checkable=True)
        self.act_shader_toggle.setChecked(self.shader_manager.enabled)
        self.act_shader_toggle.triggered.connect(self.toggle_shaders)
        shader_menu.addAction(self.act_shader_toggle)

        act_shader_cfg = QAction("Configure Shaders & Parameters...", self)
        act_shader_cfg.setShortcut(QKeySequence("F8"))
        act_shader_cfg.triggered.connect(self.open_shader_dialog)
        shader_menu.addAction(act_shader_cfg)
        shader_menu.addSeparator()

        sub_presets = shader_menu.addMenu("Quick Presets")
        presets = [
            ("GBA Color & LCD Grid", "gba_color"),
            ("CRT-Easymode", "crt"),
            ("Scanlines Classic", "scanlines"),
            ("Sharp Bilinear", "sharp"),
            ("Stock (Passthrough)", "stock")
        ]
        for label, p_id in presets:
            act = sub_presets.addAction(label)
            act.triggered.connect(lambda _, pid=p_id: self.apply_quick_shader_preset(pid))

        video_menu.addSeparator()

        self.blend_action = QAction("Interframe Blending", self, checkable=True)
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.blend_action.triggered.connect(self.toggle_interframe_blending)
        video_menu.addAction(self.blend_action)

        frameskip_menu = video_menu.addMenu("Frameskip")
        self.fs_group = QActionGroup(self)
        self.fs_group.setExclusive(True)
        self.fs_actions = {}

        for fs in range(0, 6):
            label = "0 (Disabled)" if fs == 0 else f"{fs} Frames"
            fs_act = QAction(label, self, checkable=True)
            fs_act.triggered.connect(lambda checked, count=fs: self.set_frameskip(count))
            self.fs_group.addAction(fs_act)
            frameskip_menu.addAction(fs_act)
            self.fs_actions[fs] = fs_act

        video_menu.addSeparator()

        screenshot_action = QAction("Screenshot", self)
        screenshot_action.setShortcut(QKeySequence("F12"))
        screenshot_action.triggered.connect(self.take_screenshot)
        video_menu.addAction(screenshot_action)

        clip_menu = video_menu.addMenu("Clip Recorder (GIF / MP4)")

        self.act_record_gif = QAction("🎬 Record Animated GIF (30 FPS)", self, checkable=True)
        self.act_record_gif.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self.act_record_gif.triggered.connect(self.toggle_clip_gif)
        clip_menu.addAction(self.act_record_gif)

        self.act_record_mp4 = QAction("🎥 Record MP4 Video (60 FPS)", self, checkable=True)
        self.act_record_mp4.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.act_record_mp4.triggered.connect(self.toggle_clip_mp4)
        clip_menu.addAction(self.act_record_mp4)

        self.act_stop_clip = QAction("⏹ Stop Clip Recording", self)
        self.act_stop_clip.setEnabled(False)
        self.act_stop_clip.triggered.connect(self.stop_clip_recording)
        clip_menu.addAction(self.act_stop_clip)

        clip_menu.addSeparator()
        clip_scale_menu = clip_menu.addMenu("Clip Export Scale")
        self.clip_scale_group = QActionGroup(self)
        self.clip_scale_group.setExclusive(True)
        self.clip_scale_actions = {}

        for sc in range(1, 5):
            sc_act = QAction(f"{sc}x ({240*sc}×{160*sc})", self, checkable=True)
            sc_act.triggered.connect(lambda checked, s=sc: self.set_clip_scale(s))
            self.clip_scale_group.addAction(sc_act)
            clip_scale_menu.addAction(sc_act)
            self.clip_scale_actions[sc] = sc_act

        video_menu.addSeparator()

        scale_menu = video_menu.addMenu("Window Scale")
        self.scale_group = QActionGroup(self)
        self.scale_group.setExclusive(True)
        self.scale_actions = {}

        for s in range(1, 7):
            scale_act = QAction(f"{s}x Scale ({240*s}×{160*s})", self, checkable=True)
            scale_act.triggered.connect(lambda checked, sc=s: self.set_scale(sc))
            self.scale_group.addAction(scale_act)
            scale_menu.addAction(scale_act)
            self.scale_actions[s] = scale_act

        # 6. GAME STATE VIEWS MENU
        views_menu = menubar.addMenu("Game state views")

        dossier_act = QAction("🚀 Instant AI Scene Dump...", self)
        dossier_act.setShortcut(QKeySequence("Ctrl+Shift+D"))
        dossier_act.triggered.connect(self.trigger_instant_scene_dump)
        views_menu.addAction(dossier_act)
        views_menu.addSeparator()

        views_menu.addAction("View palettes...", lambda: self._open_dev_tool(PaletteViewerDialog))
        views_menu.addAction("View sprites...", lambda: self._open_dev_tool(SpriteViewerDialog))
        views_menu.addAction("View tiles...", lambda: self._open_dev_tool(TileViewerDialog))
        views_menu.addAction("View & edit map (SBB)...", lambda: self._open_dev_tool(MapViewerDialog))
        views_menu.addSeparator()
        views_menu.addAction("Frame inspector...", lambda: self._open_dev_tool(FrameInspectorDialog))

        hex_act = QAction("View memory (Hex Editor & Watch Table)...", self)
        hex_act.setShortcut(QKeySequence("Ctrl+H"))
        hex_act.triggered.connect(lambda: self._open_dev_tool(MemoryViewerDialog))
        views_menu.addAction(hex_act)

        views_menu.addAction("Search memory...", lambda: self._open_dev_tool(MemorySearchDialog))
        views_menu.addAction("View I/O registers...", lambda: self._open_dev_tool(IORegisterViewerDialog))

        # 7. TRANSLATION MENU
        translation_menu = menubar.addMenu("Translation")

        ai_trans_act = QAction("⚡ Translate Screen with AI (Hotkey: T)", self)
        ai_trans_act.setShortcut(QKeySequence("T"))
        ai_trans_act.triggered.connect(lambda: self.trigger_ai_translation(False))
        translation_menu.addAction(ai_trans_act)

        self.act_auto_translate = QAction("🤖 Auto-Translate on Dialogue Change", self, checkable=True)
        self.act_auto_translate.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.act_auto_translate.setChecked(bool(self.config.get("ai_translation", "auto_translate", False)))
        self.act_auto_translate.triggered.connect(self.toggle_auto_translate)
        translation_menu.addAction(self.act_auto_translate)

        self.act_trans_inpaint = QAction("🎨 In-Place Textbox Inpainting", self, checkable=True)
        self.act_trans_inpaint.setShortcut(QKeySequence("Ctrl+Shift+V"))
        self.act_trans_inpaint.setChecked(bool(self.config.get("ai_translation", "inpaint_enabled", True)))
        self.act_trans_inpaint.triggered.connect(self.toggle_translation_inpaint)
        translation_menu.addAction(self.act_trans_inpaint)

        font_size_menu = translation_menu.addMenu("Inpainted Text Size")
        act_size_inc = QAction("Increase Text Size (+10%)", self)
        act_size_inc.setShortcut(QKeySequence("Ctrl+Shift+="))
        act_size_inc.triggered.connect(lambda: self.adjust_inpaint_font_scale(0.10))
        font_size_menu.addAction(act_size_inc)

        act_size_dec = QAction("Decrease Text Size (-10%)", self)
        act_size_dec.setShortcut(QKeySequence("Ctrl+Shift+-"))
        act_size_dec.triggered.connect(lambda: self.adjust_inpaint_font_scale(-0.10))
        font_size_menu.addAction(act_size_dec)

        act_size_reset = QAction("Reset Text Size (100%)", self)
        act_size_reset.triggered.connect(lambda: self.set_inpaint_font_scale(1.0))
        font_size_menu.addAction(act_size_reset)
        font_size_menu.addSeparator()

        for pct in (80, 100, 120, 140):
            act_preset = font_size_menu.addAction(f"{pct}%" + (" (Default)" if pct == 100 else ""))
            act_preset.triggered.connect(lambda _, p=pct: self.set_inpaint_font_scale(p / 100.0))

        self.act_trans_overlay = QAction("Fullscreen Floating Subtitle Overlay", self, checkable=True)
        self.act_trans_overlay.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.act_trans_overlay.setChecked(bool(self.config.get("ai_translation", "overlay_enabled", True)))
        self.act_trans_overlay.triggered.connect(self.toggle_translation_overlay)
        translation_menu.addAction(self.act_trans_overlay)

        clear_panel_act = QAction("Clear Subtitle & Inpaint Overlays", self)
        clear_panel_act.triggered.connect(self.clear_translation_subtitles)
        translation_menu.addAction(clear_panel_act)

        glossary_act = QAction("Per-Game Glossary & Translation Cache...", self)
        glossary_act.setShortcut(QKeySequence("Ctrl+Shift+T"))
        glossary_act.triggered.connect(self.open_translation_glossary)
        translation_menu.addAction(glossary_act)

        ai_cfg_act = QAction("Configure AI Translation...", self)
        ai_cfg_act.triggered.connect(self.open_ai_translation_config)
        translation_menu.addAction(ai_cfg_act)

        translation_menu.addSeparator()

        text_inspector_act = QAction("Text Inspector & Script Auto-Repointer...", self)
        text_inspector_act.triggered.connect(self.open_translation_tools)
        translation_menu.addAction(text_inspector_act)

        font_tools_act = QAction("Font Studio & TTF Generator...", self)
        font_tools_act.triggered.connect(self.open_font_tools)
        translation_menu.addAction(font_tools_act)

        translation_menu.addSeparator()

        self.ruler_action = QAction("Screen Pixel Ruler Overlay", self, checkable=True)
        self.ruler_action.triggered.connect(self.toggle_pixel_ruler)
        translation_menu.addAction(self.ruler_action)

        # 8. TOOLS MENU
        tools_menu = menubar.addMenu("Tools")

        editor_act = QAction("In-Game Graphic Editor (YY-CHR)...", self)
        editor_act.setShortcut(QKeySequence("Ctrl+G"))
        editor_act.triggered.connect(self.open_graphic_editor)
        tools_menu.addAction(editor_act)

        hex_tool_act = QAction("Interactive Hex Editor & Watch Table...", self)
        hex_tool_act.triggered.connect(lambda: self._open_dev_tool(MemoryViewerDialog))
        tools_menu.addAction(hex_tool_act)

        sappy_act = QAction("🎵 Sappy (M4A) Music & Soundbank Extractor...", self)
        sappy_act.triggered.connect(self.open_sappy_extractor)
        tools_menu.addAction(sappy_act)

        tools_menu.addSeparator()
        tools_menu.addAction("Cheats Manager...", self.open_cheats_manager)

        self.act_netplay = QAction("🌐 Netplay & Link Cable...", self)
        self.act_netplay.triggered.connect(self.open_netplay_dialog)
        tools_menu.addAction(self.act_netplay)

        tools_menu.addSeparator()
        tools_menu.addAction("Configure Controls & Motion Feedback...", self.open_controller_config)
        tools_menu.addAction("🔑 API Keys & Credentials...", self.open_ra_settings_dialog)

        profile_menu = tools_menu.addMenu("Per-Game Profile")

        self.use_profile_action = QAction("Use Per-Game Profile (No ROM)", self, checkable=True)
        self.use_profile_action.setEnabled(False)
        self.use_profile_action.triggered.connect(self.toggle_game_profile)
        profile_menu.addAction(self.use_profile_action)

        self.seed_profile_action = QAction("Copy Current Settings to Game Profile", self)
        self.seed_profile_action.setEnabled(False)
        self.seed_profile_action.triggered.connect(self.copy_global_to_game_profile)
        profile_menu.addAction(self.seed_profile_action)

        self.delete_profile_action = QAction("Delete Current Game Profile...", self)
        self.delete_profile_action.setEnabled(False)
        self.delete_profile_action.triggered.connect(self.delete_game_profile)
        profile_menu.addAction(self.delete_profile_action)

        stats_act = QAction("📊 Playtime & Game Statistics...", self)
        stats_act.triggered.connect(self.open_game_stats_dialog)
        tools_menu.addAction(stats_act)

        tools_menu.addSeparator()

        reset_settings_act = QAction("Reset Settings to Default...", self)
        reset_settings_act.triggered.connect(self.reset_settings_to_default)
        tools_menu.addAction(reset_settings_act)

        # 9. RETROACHIEVEMENTS MENU
        ra_menu = menubar.addMenu("RetroAchievements")

        self.act_ra_enabled = QAction("🏆 Enable RetroAchievements", self, checkable=True)
        self.act_ra_enabled.setChecked(bool(self.config.get("retroachievements", "enabled", False)))
        self.act_ra_enabled.triggered.connect(self.toggle_ra_enabled)
        ra_menu.addAction(self.act_ra_enabled)

        self.act_ra_hardcore = QAction("🔥 Hardcore Mode", self, checkable=True)
        self.act_ra_hardcore.setChecked(bool(self.config.get("retroachievements", "hardcore", False)))
        self.act_ra_hardcore.triggered.connect(self.toggle_ra_hardcore)
        ra_menu.addAction(self.act_ra_hardcore)

        self.act_ra_panel = QAction("📋 Show Achievements Right Panel", self, checkable=True)
        self.act_ra_panel.setChecked(bool(self.config.get("retroachievements", "show_panel", True)))
        self.act_ra_panel.triggered.connect(self.toggle_ra_panel)
        ra_menu.addAction(self.act_ra_panel)

        self.act_ra_popup = QAction("🔔 Fullscreen Achievement Popups", self, checkable=True)
        self.act_ra_popup.setChecked(bool(self.config.get("retroachievements", "popup_notifications", True)))
        self.act_ra_popup.triggered.connect(self.toggle_ra_popup)
        ra_menu.addAction(self.act_ra_popup)

        ra_menu.addSeparator()
        ra_menu.addAction("⚙ Account & Credentials Settings...", self.open_ra_settings_dialog)
```

---

#### 6. `gba_studio/ui/settings_actions.py`
```python
# settings_actions.py
from PyQt6.QtWidgets import QMessageBox

from .controller import ControllerConfigDialog
from ..tools.dev_tools import MemoryViewerDialog


class SettingsActionsMixin:
    def _sync_ui_with_config(self):
        self.mute_action.setChecked(bool(self.config.get("audio", "mute")))
        if hasattr(self, "ff_pitch_action"):
            self.ff_pitch_action.setChecked(
                bool(self.config.get("audio", "fast_forward_pitch_corrected", True))
            )

        lpf = str(self.config.get("audio", "low_pass_filter") or "medium").lower()
        if lpf in self.lpf_actions:
            self.lpf_actions[lpf].setChecked(True)
        self.widener_action.setChecked(bool(self.config.get("audio", "stereo_widener")))
        self.bass_action.setChecked(bool(self.config.get("audio", "bass_boost")))
        self.limiter_action.setChecked(bool(self.config.get("audio", "soft_limiter")))

        raw_vol = self.config.get("audio", "volume")
        current_vol = float(raw_vol) if raw_vol is not None else 0.5
        if current_vol > 2.0:
            current_vol = 0.5
            self.config.set("audio", "volume", 0.5)

        closest_vol = min(self.vol_actions.keys(), key=lambda v: abs(v - current_vol))
        self.vol_actions[closest_vol].setChecked(True)

        self.skip_bios_action.setChecked(bool(self.config.get("emulation", "skip_bios")))
        self.rewind_enabled_action.setChecked(bool(self.config.get("emulation", "rewind_enabled", True)))
        self.auto_patch_action.setChecked(bool(self.config.get("emulation", "auto_soft_patch", True)))
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.integer_scale_action.setChecked(bool(self.config.get("video", "integer_scaling", False)))
        self.input_overlay_action.setChecked(bool(self.config.get("video", "show_input_overlay", False)))
        self.act_trans_overlay.setChecked(bool(self.config.get("ai_translation", "overlay_enabled", True)))
        if hasattr(self, "act_trans_inpaint"):
            self.act_trans_inpaint.setChecked(bool(self.config.get("ai_translation", "inpaint_enabled", True)))
        if hasattr(self, "act_auto_translate"):
            self.act_auto_translate.setChecked(bool(self.config.get("ai_translation", "auto_translate", False)))
        if hasattr(self, "_sync_auto_translate_timer"):
            self._sync_auto_translate_timer()
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))
        self.act_shader_toggle.setChecked(bool(self.config.get("shaders", "enabled", False)))

        if hasattr(self, "act_ra_enabled"):
            self.act_ra_enabled.setChecked(bool(self.config.get("retroachievements", "enabled", False)))
            self.act_ra_hardcore.setChecked(bool(self.config.get("retroachievements", "hardcore", False)))
            self.act_ra_panel.setChecked(bool(self.config.get("retroachievements", "show_panel", True)))
            self.act_ra_popup.setChecked(bool(self.config.get("retroachievements", "popup_notifications", True)))

        clip_scale = int(self.config.get("video", "clip_scale", 2) or 2)
        if clip_scale in self.clip_scale_actions:
            self.clip_scale_actions[clip_scale].setChecked(True)

        self.current_slot = int(self.config.get("emulation", "state_slot") or 1)
        if self.current_slot in self.slot_actions:
            self.slot_actions[self.current_slot].setChecked(True)

        current_ff = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
        for spd, act in self.ff_actions.items():
            if abs(spd - current_ff) < 0.01:
                act.setChecked(True)
                break

        current_fs = int(self.config.get("video", "frameskip") or 0)
        if current_fs in self.fs_actions:
            self.fs_actions[current_fs].setChecked(True)

        if self.scale_factor in self.scale_actions:
            self.scale_actions[self.scale_factor].setChecked(True)

        solar_lvl = int(self.config.get("sensors", "solar_level") if self.config.get("sensors") else 5)
        if solar_lvl in self.solar_actions:
            self.solar_actions[solar_lvl].setChecked(True)
        self.sensor_hud.on_solar_updated(solar_lvl)

        if hasattr(self, "act_rumble"):
            self.act_rumble.setChecked(bool(self.config.get("sensors", "rumble_enabled", True)))
        if hasattr(self, "act_sim_use_profile"):
            self.act_sim_use_profile.setChecked(bool(self.config.get("sensors", "sim_rumble_use_profile", True)))
        if hasattr(self, "act_sim_disable_gfx_with_ram"):
            self.act_sim_disable_gfx_with_ram.setChecked(
                bool(self.config.get("sensors", "sim_rumble_disable_gfx_with_ram", False))
            )
        if hasattr(self, "sim_rumble_mode_actions"):
            sim_mode = str(self.config.get("sensors", "sim_rumble_mode", "auto") or "auto").lower()
            if sim_mode in self.sim_rumble_mode_actions:
                self.sim_rumble_mode_actions[sim_mode].setChecked(True)
        if hasattr(self, "sim_rumble_profile_actions"):
            sim_prof = str(self.config.get("sensors", "sim_rumble_profile", "balanced") or "balanced").lower()
            if sim_prof in self.sim_rumble_profile_actions:
                self.sim_rumble_profile_actions[sim_prof].setChecked(True)
        if hasattr(self, "sim_rumble_sens_actions"):
            cur_sens = float(self.config.get("sensors", "sim_rumble_sensitivity", 1.0) or 1.0)
            closest_sens = min(self.sim_rumble_sens_actions.keys(), key=lambda s: abs(s - cur_sens))
            self.sim_rumble_sens_actions[closest_sens].setChecked(True)

        if hasattr(self, "use_profile_action"):
            has_rom = bool(getattr(self, "worker", None) and getattr(self, "current_rom", None))
            active_pid = getattr(self.config, "active_profile_id", None)
            self.use_profile_action.blockSignals(True)
            self.use_profile_action.setEnabled(has_rom)
            self.use_profile_action.setChecked(bool(has_rom and active_pid))
            if has_rom:
                pid = self.worker.get_profile_id()
                self.use_profile_action.setText(f"Use Per-Game Profile [{pid}]")
                self.seed_profile_action.setEnabled(True)
                self.delete_profile_action.setEnabled(self.config.has_profile(pid))
            else:
                self.use_profile_action.setText("Use Per-Game Profile (No ROM)")
                self.seed_profile_action.setEnabled(False)
                self.delete_profile_action.setEnabled(False)
            self.use_profile_action.blockSignals(False)

        self._build_keymap()

    def open_rumble_profile_dialog(self):
        if not self.worker or not self.current_rom:
            QMessageBox.warning(self, "Notice", "Load a ROM first to generate or edit its SFX Rumble Profile.")
            return
        from ..tools.rumble_profile_dialog import RumbleProfileDialog
        self.active_tool_window = RumbleProfileDialog(self, self)
        self.active_tool_window.show()

    def toggle_sim_use_profile(self):
        val = self.act_sim_use_profile.isChecked()
        self.config.set("sensors", "sim_rumble_use_profile", val)
        if self.worker:
            self.worker.update_rumble_sim_settings()
        self.video_widget.show_osd(
            f"📳 ROM SFX / RAM Profile: {'ON' if val else 'OFF (Audio+Video Sim Only)'}"
        )

    def toggle_sim_disable_gfx_with_ram(self):
        val = self.act_sim_disable_gfx_with_ram.isChecked()
        self.config.set("sensors", "sim_rumble_disable_gfx_with_ram", val)
        if self.worker:
            self.worker.update_rumble_sim_settings()
        self.video_widget.show_osd(
            f"📳 GFX/Audio Sim w/ RAM Profile: {'DISABLED (RAM-Only)' if val else 'ENABLED (Hybrid)'}"
        )

    def set_sim_rumble_mode(self, mode: str):
        self.config.set("sensors", "sim_rumble_mode", mode)
        if self.worker:
            self.worker.update_rumble_sim_settings()
        labels = {"auto": "Auto (Non-Rumble Games)", "always": "Always On", "off": "OFF"}
        self.video_widget.show_osd(f"📳 Simulated Rumble: {labels.get(mode, mode)}")

    def set_sim_rumble_profile(self, profile: str):
        self.config.set("sensors", "sim_rumble_profile", profile)
        if self.worker:
            self.worker.update_rumble_sim_settings()
        labels = {"balanced": "Balanced", "impacts_only": "Heavy Hits & Shake", "rpg_magic": "Sensitive RPG/Magic"}
        self.video_widget.show_osd(f"📳 Sim Rumble Profile: {labels.get(profile, profile)}")

    def set_sim_rumble_sensitivity(self, sens: float):
        self.config.set("sensors", "sim_rumble_sensitivity", float(sens))
        if self.worker:
            self.worker.update_rumble_sim_settings()
        self.video_widget.show_osd(f"📳 Sim Rumble Sensitivity: {int(round(sens * 100))}%")

    def toggle_ra_enabled(self):
        val = self.act_ra_enabled.isChecked()
        self.config.set("retroachievements", "enabled", val)
        if val and self.current_rom and self.worker:
            self.ra_manager.start_game_session(self.current_rom, fallback_title=self.worker.game_title)
        else:
            self.ra_manager.stop_session()
        self.video_widget.show_osd(f"🏆 RetroAchievements: {'ON' if val else 'OFF'}")

    def toggle_ra_hardcore(self):
        val = self.act_ra_hardcore.isChecked()
        self.config.set("retroachievements", "hardcore", val)
        if self.current_rom and self.worker and bool(self.config.get("retroachievements", "enabled", False)):
            self.ra_manager.start_game_session(self.current_rom, fallback_title=self.worker.game_title)
        self.video_widget.show_osd(f"🔥 RA Hardcore Mode: {'ON' if val else 'OFF'}")

    def toggle_ra_panel(self):
        val = self.act_ra_panel.isChecked()
        self.config.set("retroachievements", "show_panel", val)
        if self.isFullScreen():
            self._pre_fs_ra_panel_visible = val
            return
        if hasattr(self, "_apply_ra_panel_visibility"):
            self._apply_ra_panel_visibility(val)
        self.video_widget.show_osd(f"Achievements Panel: {'Shown' if val else 'Hidden'}")

    def toggle_ra_popup(self):
        val = self.act_ra_popup.isChecked()
        self.config.set("retroachievements", "popup_notifications", val)
        self.video_widget.show_osd(f"Achievement Popups: {'ON' if val else 'OFF'}")

    def open_ra_settings_dialog(self):
        from .ra_dialog import RetroAchievementsSettingsDialog
        dlg = RetroAchievementsSettingsDialog(self, self)
        if dlg.exec():
            self._sync_ui_with_config()
            if not self.isFullScreen() and hasattr(self, "_apply_ra_panel_visibility"):
                self._apply_ra_panel_visibility(bool(self.config.get("retroachievements", "show_panel", True)))
            if self.current_rom and self.worker:
                if bool(self.config.get("retroachievements", "enabled", False)):
                    self.ra_manager.start_game_session(self.current_rom, fallback_title=self.worker.game_title)
                else:
                    self.ra_manager.stop_session()

    def toggle_ff_pitch_corrected(self):
        val = self.ff_pitch_action.isChecked()
        self.config.set("audio", "fast_forward_pitch_corrected", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Fast-Forward Pitch Correction: {'ON (WSOLA)' if val else 'OFF'}")

    def set_lpf_mode(self, mode: str):
        self.config.set("audio", "low_pass_filter", mode)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Low-Pass Filter: {mode.capitalize()}")

    def toggle_stereo_widener(self):
        val = self.widener_action.isChecked()
        self.config.set("audio", "stereo_widener", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Stereo Widener: {'ON' if val else 'OFF'}")

    def toggle_bass_boost(self):
        val = self.bass_action.isChecked()
        self.config.set("audio", "bass_boost", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Bass Boost: {'ON' if val else 'OFF'}")

    def toggle_soft_limiter(self):
        val = self.limiter_action.isChecked()
        self.config.set("audio", "soft_limiter", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Soft Limiter: {'ON' if val else 'OFF'}")

    def set_volume_gain(self, vol: float):
        self.config.set("audio", "volume", vol)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Volume: {int(vol * 100)}%")

    def toggle_mute(self):
        self.config.set("audio", "mute", self.mute_action.isChecked())
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd("Audio Muted" if self.mute_action.isChecked() else "Audio Unmuted")

    def reset_settings_to_default(self):
        confirm = QMessageBox.question(
            self, "Reset Settings",
            "Are you sure you want to reset all global settings, controls, sensors, shaders, and audio options back to default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.config.reset_to_defaults()
            self._apply_effective_config_to_runtime()
            self.update_recent_menu()
            if self.worker:
                self.on_game_identified(self.worker.game_title, self.worker.game_code, self.worker.rumble_type)
            self.video_widget.show_osd("Settings Reset to Default")
            QMessageBox.information(self, "Success", "All settings have been restored to factory defaults.")

    def _open_dev_tool(self, dialog_cls):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to inspect game state.")
            return
        self.active_tool_window = dialog_cls(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_tas_editor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to use the Piano-Roll TAS Editor.")
            return
        from ..tools.tas_editor import TASTimelineDialog
        self.active_tool_window = TASTimelineDialog(self, self)
        self.active_tool_window.show()

    def open_sappy_extractor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Load a ROM first to scan for Sappy / M4A music.")
            return
        from ..tools.sappy_extractor import SappyExtractorDialog
        self.active_tool_window = SappyExtractorDialog(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_netplay_dialog(self):
        from .netplay_dialog import NetplayLinkDialog
        self.active_tool_window = NetplayLinkDialog(self, self)
        self.active_tool_window.show()

    def open_game_stats_dialog(self):
        from .stats_dialog import GameStatsDialog
        self.active_tool_window = GameStatsDialog(self, self)
        self.active_tool_window.show()

    def open_graphic_editor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to edit graphics.")
            return
        from ..tools.graphics.editor import GraphicEditorDialog
        self.active_tool_window = GraphicEditorDialog(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_translation_tools(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to inspect memory.")
            return
        try:
            from ..tools.translation_dialog import TranslationToolsDialog
            self.active_tool_window = TranslationToolsDialog(self.worker, self.config, self)
            self.active_tool_window.show()
        except ImportError as e:
            QMessageBox.critical(self, "Error", f"Failed to load translation tools: {e}")

    def open_font_tools(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to scan fonts.")
            return
        try:
            from ..tools.font_tools import FontToolsDialog
            self.active_tool_window = FontToolsDialog(self.worker, self.config, self)
            self.active_tool_window.show()
        except ImportError as e:
            QMessageBox.critical(self, "Error", f"Failed to load font tools: {e}")

    def open_cheats_manager(self):
        if not self.worker:
            QMessageBox.warning(self, "Error", "Emulation must be running to manage cheats.")
            return
        if self.worker.is_ra_hardcore_active():
            QMessageBox.warning(self, "Hardcore Mode", "Cheats are disabled while RetroAchievements Hardcore Mode is active.")
            return
        from ..core.cheats import CheatManagerWindow
        self.active_tool_window = CheatManagerWindow(self.worker.cheat_engine, self.config, self)
        self.active_tool_window.show()

    def open_controller_config(self):
        dlg = ControllerConfigDialog(self.config, self.controller_manager, self)
        if dlg.exec():
            self._build_keymap()

    def toggle_pixel_ruler(self):
        self.video_widget.ruler_enabled = self.ruler_action.isChecked()
        self.video_widget.show_osd("Pixel Ruler: " + ("ON" if self.ruler_action.isChecked() else "OFF"))
        self.video_widget.update()
```
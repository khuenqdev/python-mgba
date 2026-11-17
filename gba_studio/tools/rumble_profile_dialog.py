# rumble_profile_dialog.py
import os
import json
import threading
import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QMessageBox, QSpinBox, QDoubleSpinBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from ..core.rumble_profile import (
    RUMBLE_SFX_CATEGORIES,
    build_rumble_profile_from_rom,
    get_rumble_profile_path,
)


class _ProfileScanThread(QThread):
    finished_profile = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, rom_path: str, game_code: str, game_title: str):
        super().__init__()
        self.rom_path = rom_path
        self.game_code = game_code
        self.game_title = game_title

    def run(self):
        try:
            data = build_rumble_profile_from_rom(self.rom_path, self.game_code, self.game_title)
            self.finished_profile.emit(data)
        except Exception as e:
            self.failed.emit(str(e))


class RumbleProfileDialog(QDialog):
    """GUI to auto-generate, audition, customize, and save ROM SFX Rumble Profiles."""

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

        # Pause background game simulation while testing SFX pulses
        self._was_paused = self.worker.is_paused if self.worker else False
        if self.worker:
            self.worker.is_paused = True

        self.profile_data = {"sfx_entries": []}
        self.rom_bytes = b""
        self._stop_evt = threading.Event()
        self._play_thread = None

        self.setWindowTitle(f"Game SFX Rumble Profile Studio [{self.profile_id}]")
        self.resize(940, 620)
        self._init_ui()
        self._load_existing_or_scan()

    def _init_ui(self):
        root = QVBoxLayout(self)

        info = QLabel(
            f"<b>ROM Sound-Effect Rumble Mapper</b> — Extracts sound effects from "
            f"<code>{os.path.basename(self.rom_path)}</code> and links them directly to "
            f"high-precision haptic pulses (emulation paused while editor is open)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("background: #161A22; color: #CFD8DC; padding: 8px 10px; border-radius: 5px;")
        root.addWidget(info)

        top = QHBoxLayout()
        self.btn_scan = QPushButton("🔍 Auto-Scan ROM SFX")
        self.btn_scan.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 6px 10px;")
        self.btn_scan.clicked.connect(self.scan_rom_for_profile)
        top.addWidget(self.btn_scan)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Show Active SFX Only", "Show All Extracted Samples (incl. Ignored)"])
        self.filter_combo.currentIndexChanged.connect(self.refresh_table)
        top.addWidget(self.filter_combo)

        top.addWidget(QLabel("Motor Driver:"))
        self.driver_combo = QComboBox()
        self.driver_combo.addItem("Windows XInput (60Hz)", "xinput")
        self.driver_combo.addItem("SDL2 / WGI (Pygame)", "sdl2")
        self.driver_combo.addItem("Auto", "auto")
        cur_drv = str(self.config.get("sensors", "rumble_driver", "xinput") or "xinput").lower()
        d_idx = self.driver_combo.findData(cur_drv)
        if d_idx >= 0:
            self.driver_combo.setCurrentIndex(d_idx)
        self.driver_combo.currentIndexChanged.connect(self._on_driver_changed)
        top.addWidget(self.driver_combo)

        self.status_lbl = QLabel("Ready.")
        self.status_lbl.setStyleSheet("color: #90CAF9; font-weight: bold;")
        top.addWidget(self.status_lbl, 1)
        root.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "On", "ROM Offset", "Format / Dur", "Category", "Low Motor", "High Motor", "Dur (ms)"
        ])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table)

        ctrl = QHBoxLayout()
        btn_play = QPushButton("▶ Play Selected SFX")
        btn_play.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 6px 10px;")
        btn_play.clicked.connect(self.play_selected_sfx)
        ctrl.addWidget(btn_play)

        btn_test = QPushButton("⚡ Test Motor Pulse")
        btn_test.setStyleSheet("background-color: #E65100; color: white; font-weight: bold; padding: 6px 10px;")
        btn_test.clicked.connect(self.test_selected_motor)
        ctrl.addWidget(btn_test)

        ctrl.addStretch()

        btn_save = QPushButton("💾 Save & Activate Profile")
        btn_save.setStyleSheet("background-color: #00897B; color: white; font-weight: bold; padding: 6px 14px;")
        btn_save.clicked.connect(self.save_and_activate)
        ctrl.addWidget(btn_save)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        ctrl.addWidget(btn_close)
        root.addLayout(ctrl)

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
                    self.profile_data = json.load(f)
                self.status_lbl.setText(f"Loaded saved profile ({len(self.profile_data.get('sfx_entries', []))} samples).")
                self.refresh_table()
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
        self._scanner = _ProfileScanThread(self.rom_path, self.game_code, self.game_title)
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

    def _on_scan_failed(self, err: str):
        self.btn_scan.setEnabled(True)
        self.status_lbl.setText("Scan failed.")
        QMessageBox.warning(self, "Scan Error", err)

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
        dur = max(120, int(e.get("duration_ms", 210)))
        drv = self.driver_combo.currentData()
        print(f"[PROFILE TEST] {e.get('label')} -> low={low:.2f}, high={high:.2f}, dur={dur}ms, driver={drv}")
        if self.cm:
            self.cm.trigger_rumble(low, high, dur, driver_override=drv, force_log=True)
        if hasattr(self.mw, "sensor_hud"):
            self.mw.sensor_hud.on_rumble_triggered(low, high, dur)

    def save_and_activate(self):
        if not self.worker:
            return
        self.worker.rumble_sim.profile_store.save_profile(self.profile_data, self.profile_id)
        self.worker.reload_rumble_profile()
        active_cnt = len(self.worker.rumble_sim.profile_store._active_entries)
        self.mw.video_widget.show_osd(f"📳 Saved Rumble Profile ({active_cnt} SFX triggers)", 3000)
        QMessageBox.information(
            self,
            "Profile Saved",
            f"Saved {active_cnt} active SFX trigger(s) to:\n{self.worker.rumble_sim.profile_store.profile_path}"
        )

    def _restore_worker(self):
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
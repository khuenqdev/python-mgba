# watch_table.py
import struct

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QLineEdit,
    QTabWidget, QTextEdit, QMessageBox, QSpinBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QTimer

from ..core.watchpoints import (
    WATCH_TYPES,
    resolve_gba_address,
    dtype_byte_width,
    decode_watch_bytes,
    encode_watch_value,
    find_calling_instructions,
)


class AddWatchDialog(QDialog):
    def __init__(self, default_addr: int = 0x02000000, default_type: str = "uint16", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pin Address to Watch Table")
        self.resize(360, 180)
        layout = QVBoxLayout(self)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Label:"))
        self.label_edit = QLineEdit(f"Var 0x{default_addr:08X}")
        row1.addWidget(self.label_edit)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Address (Hex):"))
        self.addr_edit = QLineEdit(f"0x{default_addr:08X}")
        row2.addWidget(self.addr_edit)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Display Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(WATCH_TYPES)
        idx = self.type_combo.findText(default_type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        row3.addWidget(self.type_combo)

        row3.addWidget(QLabel("Len:"))
        self.len_spin = QSpinBox()
        self.len_spin.setRange(1, 32)
        self.len_spin.setValue(8)
        row3.addWidget(self.len_spin)
        layout.addLayout(row3)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_entry(self) -> dict | None:
        try:
            addr = int(self.addr_edit.text().strip(), 16)
        except ValueError:
            return None
        reg, _, _ = resolve_gba_address(addr)
        if reg is None:
            return None
        dtype = self.type_combo.currentText()
        return {
            "label": self.label_edit.text().strip() or f"0x{addr:08X}",
            "address": addr,
            "dtype": dtype,
            "length": self.len_spin.value() if dtype in ("hex", "string") else dtype_byte_width(dtype),
            "freeze": False,
            "freeze_hex": "",
        }


class AddWatchpointDialog(QDialog):
    def __init__(self, default_addr: int = 0x02000000, default_width: int = 2, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Memory Write Watchpoint")
        self.resize(380, 210)
        layout = QVBoxLayout(self)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Label:"))
        self.label_edit = QLineEdit(f"Watch 0x{default_addr:08X}")
        r1.addWidget(self.label_edit)
        layout.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Address (Hex):"))
        self.addr_edit = QLineEdit(f"0x{default_addr:08X}")
        r2.addWidget(self.addr_edit)

        r2.addWidget(QLabel("Size:"))
        self.size_combo = QComboBox()
        self.size_combo.addItem("1 Byte", 1)
        self.size_combo.addItem("2 Bytes", 2)
        self.size_combo.addItem("4 Bytes", 4)
        idx = self.size_combo.findData(default_width)
        if idx >= 0:
            self.size_combo.setCurrentIndex(idx)
        r2.addWidget(self.size_combo)
        layout.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Trigger On:"))
        self.cond_combo = QComboBox()
        self.cond_combo.addItem("Any Value Change (Write)", "any_write")
        self.cond_combo.addItem("Written Value == Target", "equals")
        self.cond_combo.addItem("Written Value < Target", "less")
        self.cond_combo.addItem("Written Value > Target", "greater")
        r3.addWidget(self.cond_combo)

        self.target_edit = QLineEdit("0")
        self.target_edit.setFixedWidth(80)
        r3.addWidget(self.target_edit)
        layout.addLayout(r3)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_watchpoint(self) -> dict | None:
        try:
            addr = int(self.addr_edit.text().strip(), 16)
            tgt = int(self.target_edit.text().strip() or "0", 0)
        except ValueError:
            return None
        reg, _, _ = resolve_gba_address(addr)
        if reg is None:
            return None
        return {
            "label": self.label_edit.text().strip() or f"WP 0x{addr:08X}",
            "address": addr,
            "width": int(self.size_combo.currentData()),
            "condition": self.cond_combo.currentData(),
            "target_val": tgt,
            "enabled": True,
            "last_val": None,
        }


class WatchpointHitDialog(QDialog):
    """Auto-opens when a Write Watchpoint triggers, displaying Capstone ROM XRef disassembly."""

    def __init__(self, hit_info: dict, worker, parent=None):
        super().__init__(parent)
        self.hit = hit_info
        self.worker = worker
        self.setWindowTitle(f"Write Watchpoint Hit! [0x{hit_info['address']:08X}]")
        self.resize(700, 480)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        addr = self.hit["address"]
        old_v = self.hit["old_val"]
        new_v = self.hit["new_val"]
        frame_n = self.hit["frame_num"]
        label = self.hit.get("label", "")

        banner = QLabel(
            f"<b style='color:#FF5252; font-size:14px;'>⏸ Write Watchpoint Triggered: {label}</b><br>"
            f"<b>Address:</b> <code>0x{addr:08X}</code> ({self.hit['width']}B) &nbsp;|&nbsp; "
            f"<b>Frame:</b> {frame_n}<br>"
            f"<b>Change:</b> <code>{old_v} (0x{old_v:X})</code> → <code>{new_v} (0x{new_v:X})</code>"
        )
        banner.setStyleSheet("background:#1A1D24; color:#ECEFF1; padding:10px; border-radius:6px; border:1px solid #B71C1C;")
        layout.addWidget(banner)

        layout.addWidget(QLabel("<b>Capstone ARM/Thumb ROM Callers & Literal-Pool References:</b>"))
        self.asm_view = QTextEdit()
        self.asm_view.setReadOnly(True)
        self.asm_view.setStyleSheet("font-family: monospace; font-size: 12px; background:#0F1218; color:#80CBC4;")
        rom_path = getattr(self.worker, "rom_path", "") if self.worker else ""
        self.asm_view.setPlainText(find_calling_instructions(rom_path, addr))
        layout.addWidget(self.asm_view)

        btn_row = QHBoxLayout()
        btn_resume = QPushButton("▶ Resume Emulation")
        btn_resume.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 6px 12px;")
        btn_resume.clicked.connect(self._resume)
        btn_row.addWidget(btn_resume)

        btn_step = QPushButton("⏭ Step 1 Frame")
        btn_step.clicked.connect(self._step_one)
        btn_row.addWidget(btn_step)

        btn_disable = QPushButton("🔇 Disable Watchpoint & Resume")
        btn_disable.clicked.connect(self._disable_and_resume)
        btn_row.addWidget(btn_disable)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _resume(self):
        if self.worker:
            self.worker.is_paused = False
        parent_win = self.parent()
        if parent_win and hasattr(parent_win, "pause_action"):
            parent_win.pause_action.setChecked(False)
        self.accept()

    def _step_one(self):
        if self.worker:
            self.worker.step_one_frame()
        self.accept()

    def _disable_and_resume(self):
        if self.worker and hasattr(self.worker, "watch_engine"):
            addr = self.hit["address"]
            for wp in self.worker.watch_engine.watchpoints:
                if int(wp["address"]) == addr:
                    wp["enabled"] = False
        self._resume()


class WatchTablePanel(QWidget):
    """Persistent Cheat-Engine Watch Table & Write Watchpoints Dock Panel."""

    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self._init_ui()
        self.refresh_tables()

        self.live_timer = QTimer(self)
        self.live_timer.setInterval(100)
        self.live_timer.timeout.connect(self.refresh_live_values)
        self.live_timer.start()

    @property
    def engine(self):
        return getattr(self.worker, "watch_engine", None) if self.worker else None

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)

        self.tabs = QTabWidget()

        # Tab 1: Watch Table (Address Table)
        tab_watch = QWidget()
        w_lay = QVBoxLayout(tab_watch)

        w_bar = QHBoxLayout()
        btn_add_w = QPushButton("+ Add Address...")
        btn_add_w.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold;")
        btn_add_w.clicked.connect(self.prompt_add_watch)
        w_bar.addWidget(btn_add_w)

        btn_del_w = QPushButton("Remove Selected")
        btn_del_w.clicked.connect(self.remove_selected_watch)
        w_bar.addWidget(btn_del_w)

        w_bar.addStretch()
        w_lay.addLayout(w_bar)

        self.watch_table = QTableWidget(0, 5)
        self.watch_table.setHorizontalHeaderLabels(["Freeze", "Label", "Address", "Type", "Live Value"])
        self.watch_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.watch_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.watch_table.itemChanged.connect(self.on_watch_item_changed)
        w_lay.addWidget(self.watch_table)
        self.tabs.addTab(tab_watch, "📌 Watch Table (Freeze & Monitor)")

        # Tab 2: Write Watchpoints
        tab_wp = QWidget()
        wp_lay = QVBoxLayout(tab_wp)

        wp_bar = QHBoxLayout()
        btn_add_wp = QPushButton("+ Add Write Watchpoint...")
        btn_add_wp.setStyleSheet("background-color: #C62828; color: white; font-weight: bold;")
        btn_add_wp.clicked.connect(self.prompt_add_watchpoint)
        wp_bar.addWidget(btn_add_wp)

        btn_del_wp = QPushButton("Remove Selected")
        btn_del_wp.clicked.connect(self.remove_selected_wp)
        wp_bar.addWidget(btn_del_wp)

        btn_xref = QPushButton("🔍 Inspect Calling ASM (Capstone)")
        btn_xref.clicked.connect(self.inspect_selected_wp_asm)
        wp_bar.addWidget(btn_xref)

        wp_bar.addStretch()
        wp_lay.addLayout(wp_bar)

        self.wp_table = QTableWidget(0, 6)
        self.wp_table.setHorizontalHeaderLabels(["Enabled", "Label", "Address", "Size", "Condition", "Last Value"])
        self.wp_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.wp_table.itemChanged.connect(self.on_wp_item_changed)
        wp_lay.addWidget(self.wp_table)
        self.tabs.addTab(tab_wp, "🎯 Write Watchpoints (Breakpoints)")

        root.addWidget(self.tabs)

    def _read_addr_bytes(self, addr: int, width: int) -> bytes | None:
        if not self.worker:
            return None
        reg_name, _, off = resolve_gba_address(addr)
        if not reg_name:
            return None
        if reg_name == "IWRAM":
            # resolve_gba_address returns (256KB + local_off) for raw libretro region 2,
            # while get_memory_region("IWRAM") already slices [256KB:].
            off -= 256 * 1024
        raw = self.worker.get_memory_region(reg_name)
        if not raw or off < 0 or off + width > len(raw):
            return None
        return raw[off:off + width]

    def _write_addr_bytes(self, addr: int, payload: bytes):
        if not self.worker or not payload:
            return
        reg_name, _, off = resolve_gba_address(addr)
        if not reg_name:
            return
        if reg_name == "IWRAM":
            off -= 256 * 1024
        self.worker.write_memory_region(reg_name, off, payload)

    def add_watch_entry(self, addr: int, label: str = "", dtype: str = "uint16"):
        if not self.engine:
            return
        self.engine.watches.append({
            "label": label or f"Var 0x{addr:08X}",
            "address": int(addr),
            "dtype": dtype,
            "length": dtype_byte_width(dtype),
            "freeze": False,
            "freeze_hex": "",
        })
        self.engine.save_default_watches()
        self.refresh_tables()
        self.tabs.setCurrentIndex(0)

    def add_watchpoint_entry(self, addr: int, width: int = 2, label: str = ""):
        if not self.engine:
            return
        raw = self._read_addr_bytes(addr, width)
        last_val = int.from_bytes(raw, "little") if raw else None
        self.engine.watchpoints.append({
            "label": label or f"WP 0x{addr:08X}",
            "address": int(addr),
            "width": int(width),
            "condition": "any_write",
            "target_val": 0,
            "enabled": True,
            "last_val": last_val,
        })
        self.refresh_tables()
        self.tabs.setCurrentIndex(1)

    def prompt_add_watch(self):
        dlg = AddWatchDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            entry = dlg.get_entry()
            if not entry:
                QMessageBox.warning(self, "Invalid Address", "Address must be in EWRAM, IWRAM, VRAM, PRAM, OAM, or SRAM.")
                return
            if self.engine:
                self.engine.watches.append(entry)
                self.engine.save_default_watches()
                self.refresh_tables()

    def prompt_add_watchpoint(self):
        dlg = AddWatchpointDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            wp = dlg.get_watchpoint()
            if not wp:
                QMessageBox.warning(self, "Invalid Address", "Address must be in valid GBA RAM.")
                return
            if self.engine:
                raw = self._read_addr_bytes(wp["address"], wp["width"])
                wp["last_val"] = int.from_bytes(raw, "little") if raw else None
                self.engine.watchpoints.append(wp)
                self.refresh_tables()

    def remove_selected_watch(self):
        row = self.watch_table.currentRow()
        if row >= 0 and self.engine and row < len(self.engine.watches):
            del self.engine.watches[row]
            self.engine.save_default_watches()
            self.refresh_tables()

    def remove_selected_wp(self):
        row = self.wp_table.currentRow()
        if row >= 0 and self.engine and row < len(self.engine.watchpoints):
            del self.engine.watchpoints[row]
            self.refresh_tables()

    def inspect_selected_wp_asm(self):
        row = self.wp_table.currentRow()
        if row < 0 or not self.engine or row >= len(self.engine.watchpoints):
            return
        wp = self.engine.watchpoints[row]
        rom_path = getattr(self.worker, "rom_path", "")
        asm_text = find_calling_instructions(rom_path, int(wp["address"]))
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Capstone ROM XRef - 0x{wp['address']:08X}")
        dlg.resize(660, 420)
        lay = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setStyleSheet("font-family: monospace; font-size: 12px; background:#0F1218; color:#80CBC4;")
        txt.setPlainText(asm_text)
        lay.addWidget(txt)
        dlg.exec()

    def refresh_tables(self):
        if not self.engine:
            return

        # 1. Watch Table
        self.watch_table.blockSignals(True)
        self.watch_table.setRowCount(len(self.engine.watches))
        for r, w in enumerate(self.engine.watches):
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            cb.setCheckState(Qt.CheckState.Checked if w.get("freeze") else Qt.CheckState.Unchecked)
            self.watch_table.setItem(r, 0, cb)

            self.watch_table.setItem(r, 1, QTableWidgetItem(w.get("label", "")))

            addr_item = QTableWidgetItem(f"0x{int(w['address']):08X}")
            addr_item.setFlags(addr_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.watch_table.setItem(r, 2, addr_item)

            combo = QComboBox()
            combo.addItems(WATCH_TYPES)
            combo.setCurrentText(w.get("dtype", "uint16"))
            combo.currentTextChanged.connect(lambda t, row=r: self._on_watch_type_changed(row, t))
            self.watch_table.setCellWidget(r, 3, combo)

            self.watch_table.setItem(r, 4, QTableWidgetItem("..."))
        self.watch_table.blockSignals(False)

        # 2. Watchpoints Table
        self.wp_table.blockSignals(True)
        self.wp_table.setRowCount(len(self.engine.watchpoints))
        for r, wp in enumerate(self.engine.watchpoints):
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            cb.setCheckState(Qt.CheckState.Checked if wp.get("enabled", True) else Qt.CheckState.Unchecked)
            self.wp_table.setItem(r, 0, cb)

            self.wp_table.setItem(r, 1, QTableWidgetItem(wp.get("label", "")))
            self.wp_table.setItem(r, 2, QTableWidgetItem(f"0x{int(wp['address']):08X}"))
            self.wp_table.setItem(r, 3, QTableWidgetItem(f"{wp.get('width', 2)}B"))
            cond_txt = wp.get("condition", "any_write")
            if cond_txt != "any_write":
                cond_txt = f"{cond_txt} {wp.get('target_val', 0)}"
            self.wp_table.setItem(r, 4, QTableWidgetItem(cond_txt))
            lv = wp.get("last_val")
            self.wp_table.setItem(r, 5, QTableWidgetItem(f"{lv} (0x{lv:X})" if lv is not None else "--"))
        self.wp_table.blockSignals(False)

        self.refresh_live_values()

    def _on_watch_type_changed(self, row: int, new_type: str):
        if not self.engine or row >= len(self.engine.watches):
            return
        w = self.engine.watches[row]
        w["dtype"] = new_type
        w["length"] = dtype_byte_width(new_type, w.get("length", 8))
        if w.get("freeze"):
            raw = self._read_addr_bytes(int(w["address"]), w["length"])
            if raw:
                w["freeze_hex"] = raw.hex()
        self.engine.save_default_watches()
        self.refresh_live_values()

    def on_watch_item_changed(self, item: QTableWidgetItem):
        row, col = item.row(), item.column()
        if not self.engine or row >= len(self.engine.watches):
            return
        w = self.engine.watches[row]

        if col == 0:
            freeze = (item.checkState() == Qt.CheckState.Checked)
            w["freeze"] = freeze
            if freeze and not w.get("freeze_hex"):
                width = dtype_byte_width(w["dtype"], w.get("length", 8))
                raw = self._read_addr_bytes(int(w["address"]), width)
                if raw:
                    w["freeze_hex"] = raw.hex()
            self.engine.save_default_watches()
        elif col == 1:
            w["label"] = item.text().strip()
            self.engine.save_default_watches()
        elif col == 4:
            width = dtype_byte_width(w["dtype"], w.get("length", 8))
            payload = encode_watch_value(item.text(), w["dtype"], width=width)
            if payload:
                self._write_addr_bytes(int(w["address"]), payload)
                w["freeze_hex"] = payload.hex()
                self.engine.save_default_watches()

    def on_wp_item_changed(self, item: QTableWidgetItem):
        row, col = item.row(), item.column()
        if not self.engine or row >= len(self.engine.watchpoints):
            return
        wp = self.engine.watchpoints[row]
        if col == 0:
            wp["enabled"] = (item.checkState() == Qt.CheckState.Checked)
        elif col == 1:
            wp["label"] = item.text().strip()

    def refresh_live_values(self):
        if not self.engine or not self.worker:
            return
        # Don't overwrite the value cell currently being edited by the user
        if self.watch_table.state() == QTableWidget.State.EditingState:
            return

        self.watch_table.blockSignals(True)
        for r, w in enumerate(self.engine.watches):
            width = dtype_byte_width(w.get("dtype", "uint16"), w.get("length", 8))
            raw = self._read_addr_bytes(int(w["address"]), width)
            txt = decode_watch_bytes(raw, w.get("dtype", "uint16")) if raw else "?"
            cell = self.watch_table.item(r, 4)
            if cell and cell.text() != txt:
                cell.setText(txt)
        self.watch_table.blockSignals(False)

        self.wp_table.blockSignals(True)
        for r, wp in enumerate(self.engine.watchpoints):
            lv = wp.get("last_val")
            txt = f"{lv} (0x{lv:X})" if lv is not None else "--"
            cell = self.wp_table.item(r, 5)
            if cell and cell.text() != txt:
                cell.setText(txt)
        self.wp_table.blockSignals(False)

    def stop_timers(self):
        if hasattr(self, "live_timer"):
            self.live_timer.stop()

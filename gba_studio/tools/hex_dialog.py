# hex_editor.py
import os
import struct
import numpy as np

from PyQt6.QtWidgets import (QDialog, QWidget, QAbstractScrollArea, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit, 
                             QSplitter, QCheckBox, QSpinBox, QMenu, QMessageBox, 
                             QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView, 
                             QGroupBox, QFrame)
from PyQt6.QtGui import (QPainter, QColor, QFont, QFontMetrics, QPen, QBrush, 
                         QKeySequence, QAction, QClipboard)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRect

# GBA Memory Region Definitions
GBA_REGIONS = {
    "EWRAM (256 KB) - 0x02000000": ("EWRAM", 0x02000000, 256 * 1024),
    "IWRAM (32 KB)  - 0x03000000": ("IWRAM", 0x03000000, 32 * 1024),
    "VRAM (96 KB)   - 0x06000000": ("VRAM", 0x06000000, 96 * 1024),
    "PRAM (1 KB)    - 0x05000000": ("PRAM", 0x05000000, 1024),
    "OAM (1 KB)     - 0x07000000": ("OAM", 0x07000000, 1024),
    "ROM (Cartridge)- 0x08000000": ("ROM", 0x08000000, 32 * 1024 * 1024),
    "SRAM (32 KB)   - 0x0E000000": ("SRAM", 0x0E000000, 32 * 1024)
}


from .hex_canvas import VirtualHexCanvas
from .data_inspector import DataInspectorWidget

class HexEditorDialog(QDialog):
    """Full-featured Hex Editor & Forensic Analyzer (ImHex / HxD Replica)."""

    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Interactive Hex Editor & Memory Studio")
        self.resize(1150, 720)

        self.current_region_name = "EWRAM"
        self.current_base_addr = 0x02000000

        self._init_ui()
        self._load_active_region()

        # Live RAM Watch Timer
        self.live_timer = QTimer(self)
        self.live_timer.timeout.connect(self.poll_live_memory)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # 1. Top Ribbon Toolbar
        top_bar = QHBoxLayout()

        top_bar.addWidget(QLabel("<b>Region:</b>"))
        self.region_combo = QComboBox()
        self.region_combo.addItems(list(GBA_REGIONS.keys()))
        self.region_combo.currentIndexChanged.connect(self.on_region_changed)
        top_bar.addWidget(self.region_combo)

        top_bar.addWidget(QLabel("Goto Offset:"))
        self.jump_edit = QLineEdit("0x02000000")
        self.jump_edit.setFixedWidth(100)
        top_bar.addWidget(self.jump_edit)

        btn_go = QPushButton("Go")
        btn_go.clicked.connect(self.on_jump_click)
        top_bar.addWidget(btn_go)

        top_bar.addSpacing(10)
        self.cb_live = QCheckBox("⚡ Live Watch")
        self.cb_live.toggled.connect(self.toggle_live_watch)
        top_bar.addWidget(self.cb_live)

        self.spin_poll = QSpinBox()
        self.spin_poll.setRange(50, 2000)
        self.spin_poll.setValue(100)
        self.spin_poll.setSuffix(" ms")
        top_bar.addWidget(self.spin_poll)

        top_bar.addStretch()

        self.btn_write = QPushButton("💾 Write Changes to RAM")
        self.btn_write.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 5px 10px;")
        self.btn_write.clicked.connect(self.commit_changes)
        top_bar.addWidget(self.btn_write)

        btn_save_bin = QPushButton("Export Bin...")
        btn_save_bin.clicked.connect(self.export_binary_file)
        top_bar.addWidget(btn_save_bin)

        main_layout.addLayout(top_bar)

        # 2. Main Body Splitter: Virtual Hex Canvas on Left + Data Inspector on Right
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.hex_canvas = VirtualHexCanvas(self)
        self.hex_canvas.cursor_changed.connect(self.on_cursor_moved)
        self.hex_canvas.data_modified.connect(self.update_status_bar)
        splitter.addWidget(self.hex_canvas)

        self.inspector = DataInspectorWidget(self)
        self.inspector.jump_requested.connect(self.jump_to_address)
        splitter.addWidget(self.inspector)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)

        # 3. Find & Replace Bottom Dock (Expandable)
        self.search_bar = QFrame()
        self.search_bar.setFrameShape(QFrame.Shape.StyledPanel)
        sb_layout = QHBoxLayout(self.search_bar)
        sb_layout.setContentsMargins(4, 4, 4, 4)

        sb_layout.addWidget(QLabel("<b>Find:</b>"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Hex bytes (e.g. 1A 2B 3C) or text string...")
        sb_layout.addWidget(self.search_edit)

        self.cb_search_text = QCheckBox("Match Text (ASCII)")
        sb_layout.addWidget(self.cb_search_text)

        btn_find_next = QPushButton("Find Next")
        btn_find_next.clicked.connect(self.find_next)
        sb_layout.addWidget(btn_find_next)

        btn_find_prev = QPushButton("Find Prev")
        btn_find_prev.clicked.connect(self.find_prev)
        sb_layout.addWidget(btn_find_prev)

        main_layout.addWidget(self.search_bar)

        # 4. Bottom Status Bar
        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet("font-family: monospace; font-size: 11px; color: #90A4AE; padding: 2px;")
        main_layout.addWidget(self.status_label)

    # =========================================================================
    # Region Switching & Live Watching
    # =========================================================================
    def on_region_changed(self):
        reg_str = self.region_combo.currentText()
        reg_name, base_addr, _ = GBA_REGIONS[reg_str]
        self.current_region_name = reg_name
        self.current_base_addr = base_addr
        self.jump_edit.setText(f"0x{base_addr:08X}")
        self._load_active_region()

    def _load_active_region(self):
        if not self.worker: return
        raw_mem = self.worker.get_memory_region(self.current_region_name)
        if raw_mem is not None:
            self.hex_canvas.set_buffer(raw_mem, self.current_base_addr)
            self.status_label.setText(f"Loaded {self.current_region_name}: {len(raw_mem):,} bytes.")
        else:
            self.hex_canvas.set_buffer(b"", self.current_base_addr)
            self.status_label.setText(f"{self.current_region_name} not available in active core.")
        self.update_status_bar()

    def toggle_live_watch(self, enabled: bool):
        if enabled:
            interval = self.spin_poll.value()
            self.live_timer.start(interval)
            self.status_label.setText(f"⚡ Live Watch Active ({interval} ms). Changed bytes are highlighted.")
        else:
            self.live_timer.stop()
            self.hex_canvas.live_diff_offsets.clear()
            self.hex_canvas.viewport().update()
            self.status_label.setText("Live watch stopped.")

    def poll_live_memory(self):
        if not self.worker: return
        raw = self.worker.get_memory_region(self.current_region_name)
        if raw:
            self.hex_canvas.update_live_buffer(raw)

    # =========================================================================
    # Navigation & Actions
    # =========================================================================
    def on_jump_click(self):
        try:
            val = int(self.jump_edit.text(), 16)
            self.jump_to_address(val)
        except ValueError:
            pass

    def jump_to_address(self, abs_addr: int):
        for reg_key, (reg_name, base, size) in GBA_REGIONS.items():
            if base <= abs_addr < base + size:
                if self.current_region_name != reg_name:
                    idx = self.region_combo.findText(reg_key)
                    if idx >= 0:
                        self.region_combo.setCurrentIndex(idx)
                offset = abs_addr - base
                self.hex_canvas.set_cursor(offset)
                return

        offset = abs_addr - self.current_base_addr
        self.hex_canvas.set_cursor(offset)

    def on_cursor_moved(self, offset: int):
        self.inspector.update_values(self.hex_canvas.data, offset, self.current_base_addr)
        self.update_status_bar()

    def update_status_bar(self):
        off = self.hex_canvas.cursor_pos
        abs_addr = self.current_base_addr + off
        mod_count = len(self.hex_canvas.modified_map)
        s, e = self.hex_canvas.get_selection_range()
        sel_size = (e - s + 1) if (self.hex_canvas.selection_start is not None and self.hex_canvas.selection_start != self.hex_canvas.selection_end) else 1

        self.status_label.setText(
            f"Address: 0x{abs_addr:08X} (Offset: 0x{off:06X}) | "
            f"Selected: {sel_size} byte(s) | "
            f"Modified: {mod_count} byte(s) | "
            f"Total Size: {len(self.hex_canvas.data):,} bytes"
        )

    # =========================================================================
    # Memory Writing & Exporting
    # =========================================================================
    def commit_changes(self):
        """Flushes all modified bytes directly into active emulator RAM or ROM."""
        if not self.worker or not self.hex_canvas.modified_map:
            QMessageBox.information(self, "Notice", "No modifications to commit.")
            return

        if self.current_region_name == "ROM":
            reply = QMessageBox.question(
                self, "Save Modified ROM", 
                "Save changes directly to a new .gba file on disk?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.export_binary_file()
            return

        success_count = 0
        for off, _ in self.hex_canvas.modified_map.items():
            b = bytes([self.hex_canvas.data[off]])
            if self.worker.write_memory_region(self.current_region_name, off, b):
                success_count += 1

        self.hex_canvas.modified_map.clear()
        self.hex_canvas.viewport().update()
        self.update_status_bar()
        QMessageBox.information(self, "Success", f"Committed {success_count} modified byte(s) to {self.current_region_name}!")

    def export_binary_file(self):
        filepath, _ = QFileDialog.getSaveFileName(self, "Export Region to Binary", f"{self.current_region_name.lower()}_dump.bin", "Binary Files (*.bin *.gba)")
        if filepath:
            with open(filepath, "wb") as f:
                f.write(self.hex_canvas.data)
            QMessageBox.information(self, "Exported", f"Saved binary file to:\n{filepath}")

    # =========================================================================
    # Search Engine
    # =========================================================================
    def _get_search_pattern(self) -> bytes:
        query = self.search_edit.text().strip()
        if not query: return None

        if self.cb_search_text.isChecked():
            return query.encode('latin-1', errors='ignore')
        else:
            clean = query.replace(" ", "").replace(",", "").replace("0x", "")
            try:
                return bytes.fromhex(clean)
            except ValueError:
                return None

    def find_next(self):
        pat = self._get_search_pattern()
        if not pat: return

        data = bytes(self.hex_canvas.data)
        start_pos = self.hex_canvas.cursor_pos + 1
        pos = data.find(pat, start_pos)
        if pos == -1:
            pos = data.find(pat, 0)  # Wrap around

        if pos != -1:
            self.hex_canvas.set_cursor(pos)
            self.hex_canvas.selection_start = pos
            self.hex_canvas.selection_end = pos + len(pat) - 1
            self.hex_canvas.viewport().update()
        else:
            QMessageBox.information(self, "Not Found", "Pattern not found.")

    def find_prev(self):
        pat = self._get_search_pattern()
        if not pat: return

        data = bytes(self.hex_canvas.data)
        pos = data.rfind(pat, 0, max(0, self.hex_canvas.cursor_pos))
        if pos == -1:
            pos = data.rfind(pat)  # Wrap around

        if pos != -1:
            self.hex_canvas.set_cursor(pos)
            self.hex_canvas.selection_start = pos
            self.hex_canvas.selection_end = pos + len(pat) - 1
            self.hex_canvas.viewport().update()
        else:
            QMessageBox.information(self, "Not Found", "Pattern not found.")

    def closeEvent(self, event):
        if hasattr(self, 'live_timer'):
            self.live_timer.stop()
        event.accept()

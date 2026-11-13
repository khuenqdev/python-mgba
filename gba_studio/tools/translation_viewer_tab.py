# translation_tools.py
import os
import struct
import csv
import re
import numpy as np
from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QComboBox, QLineEdit, 
                             QTableWidget, QTableWidgetItem, QHeaderView, 
                             QFileDialog, QMessageBox, QTabWidget, QGroupBox,
                             QTextEdit, QSpinBox, QCheckBox)
from PyQt6.QtCore import Qt, QTimer

from .encoding import EncodingManager

class ViewerTabMixin:
    def _init_viewer_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        nav_bar = QHBoxLayout()
        nav_bar.addWidget(QLabel("Region:"))
        self.region_combo = QComboBox()
        self.region_combo.addItems(["ROM (Cartridge)", "EWRAM (256 KB)", "IWRAM (32 KB)"])
        self.region_combo.currentIndexChanged.connect(self.refresh_memory)
        nav_bar.addWidget(self.region_combo)

        nav_bar.addWidget(QLabel("Offset (Hex):"))
        self.jump_edit = QLineEdit("0x00000000")
        self.jump_edit.setFixedWidth(100)
        nav_bar.addWidget(self.jump_edit)
        
        jump_btn = QPushButton("Jump")
        jump_btn.clicked.connect(self.on_jump)
        nav_bar.addWidget(jump_btn)
        nav_bar.addStretch()
        layout.addLayout(nav_bar)

        self.hex_display = QLabel()
        self.hex_display.setStyleSheet("font-family: monospace; font-size: 13px; background: #111; color: #00FF66; padding: 10px;")
        self.hex_display.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.hex_display)

        btn_bar = QHBoxLayout()
        prev_btn = QPushButton("◀ Prev Page")
        prev_btn.clicked.connect(lambda: self.navigate_page(-1))
        next_btn = QPushButton("Next Page ▶")
        next_btn.clicked.connect(lambda: self.navigate_page(1))
        btn_bar.addWidget(prev_btn)
        btn_bar.addWidget(next_btn)
        layout.addLayout(btn_bar)
        
        self.current_offset = 0
        self.bytes_per_page = 16 * 23

    def get_region_data(self):
        sel = self.region_combo.currentText()
        if sel.startswith("ROM") and self.worker.rom_path:
            with open(self.worker.rom_path, 'rb') as f:
                return f.read(), 0x08000000
        elif sel.startswith("EWRAM"):
            return self.worker.get_memory_region("EWRAM"), 0x02000000
        elif sel.startswith("IWRAM"):
            mem = self.worker.get_memory_region("SYSTEM_RAM")
            if mem and len(mem) > 256 * 1024:
                return mem[256 * 1024:], 0x03000000
        return None, 0

    def on_jump(self):
        try:
            val = int(self.jump_edit.text(), 16)
            _, base_addr = self.get_region_data()
            if val >= base_addr: val -= base_addr
            self.current_offset = val
            self.refresh_memory()
        except ValueError: pass

    def navigate_page(self, direction):
        self.current_offset = max(0, self.current_offset + (direction * self.bytes_per_page))
        self.refresh_memory()

    def refresh_memory(self):
        raw_mem, base_addr = self.get_region_data()
        if not raw_mem:
            self.hex_display.setText("Region data not available.")
            return

        self.current_offset = min(max(0, self.current_offset), max(0, len(raw_mem) - self.bytes_per_page))
        chunk = raw_mem[self.current_offset:self.current_offset + self.bytes_per_page]

        lines = [f"Offset      00 01 02 03 04 05 06 07  08 09 0A 0B 0C 0D 0E 0F  [{self.encoding.mode}]"]
        lines.append("-" * 75)

        for i in range(0, len(chunk), 16):
            sub = chunk[i:i + 16]
            hex_part1 = " ".join(f"{b:02X}" for b in sub[:8])
            hex_part2 = " ".join(f"{b:02X}" for b in sub[8:])
            
            text_part = self.encoding.decode(bytes(sub))
            text_part = text_part.ljust(16)[:16] 
            
            line_addr = base_addr + self.current_offset + i
            lines.append(f"{line_addr:08X}    {hex_part1:<23}  {hex_part2:<23}  |{text_part}|")

        self.hex_display.setText("\n".join(lines))

    # =========================================================================
    # TAB 2: SCRIPT EXTRACTOR & AUTO-REPOINTER
    # =========================================================================

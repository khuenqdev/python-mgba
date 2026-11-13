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

class SearchTabMixin:
    def _init_search_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        desc = QLabel("<b>Relative Search:</b> Find text with unknown custom encodings by searching for mathematical character distances.")
        layout.addWidget(desc)
        
        input_bar = QHBoxLayout()
        input_bar.addWidget(QLabel("Search String:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("e.g. SWORD")
        input_bar.addWidget(self.search_edit)
        
        search_btn = QPushButton("Search ROM")
        search_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        search_btn.clicked.connect(self.run_relative_search)
        input_bar.addWidget(search_btn)
        layout.addLayout(input_bar)

        self.search_table = QTableWidget(0, 3)
        self.search_table.setHorizontalHeaderLabels(["ROM Address", "Guessed Mapping Offset", "Generate Custom .tbl"])
        self.search_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.search_table)

    def on_encoding_changed(self):
        self.encoding.mode = self.enc_combo.currentText()
        self.refresh_memory()

    def on_load_tbl(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Load Table File", "", "Table Files (*.tbl);;All Files (*.*)")
        if filepath and self.encoding.load_tbl(filepath):
            self.tbl_status.setText(f"Loaded: {os.path.basename(filepath)}")
            idx = self.enc_combo.findText("Custom TBL")
            if idx >= 0: self.enc_combo.setCurrentIndex(idx)
            self.refresh_memory()

    def run_relative_search(self):
        query = self.search_edit.text().strip()
        if len(query) < 3:
            QMessageBox.warning(self, "Notice", "Please enter at least 3 characters for relative search.")
            return
            
        rom_data, base_addr = self.get_region_data()
        if not rom_data: return

        target_diffs = [(ord(query[i+1]) - ord(query[i])) % 256 for i in range(len(query)-1)]
        
        data_np = np.frombuffer(rom_data, dtype=np.uint8).astype(np.int16)
        diffs_np = (data_np[1:] - data_np[:-1]) % 256
        
        L = len(diffs_np) - len(target_diffs) + 1
        mask = (diffs_np[:L] == target_diffs[0])
        for j in range(1, len(target_diffs)):
            mask &= (diffs_np[j : L+j] == target_diffs[j])
            
        match_indices = np.where(mask)[0]
        
        self.search_table.setRowCount(len(match_indices))
        for row, idx in enumerate(match_indices):
            addr = base_addr + idx
            
            base_char_val = ord(query[0])
            matched_byte_val = rom_data[idx]
            mapping_offset = (matched_byte_val - base_char_val)
            
            self.search_table.setItem(row, 0, QTableWidgetItem(f"0x{addr:08X}"))
            self.search_table.setItem(row, 1, QTableWidgetItem(f"Offset: {mapping_offset:+}"))
            
            gen_btn = QPushButton("Generate .tbl & View")
            gen_btn.clicked.connect(lambda _, a=addr, off=mapping_offset: self.generate_tbl_and_jump(a, off))
            self.search_table.setCellWidget(row, 2, gen_btn)
            
        QMessageBox.information(self, "Search Complete", f"Found {len(match_indices)} potential matches in ROM.")

    def generate_tbl_and_jump(self, address, offset):
        tbl_content = ""
        chars_to_map = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?.,'- "
        for char in chars_to_map:
            hex_val = (ord(char) + offset) % 256
            tbl_content += f"{hex_val:02X}={char}\n"
            
        export_dir = self.config.get("paths", "exports") or "exports"
        tbl_path = os.path.join(export_dir, "auto_generated.tbl")
        os.makedirs(os.path.dirname(tbl_path), exist_ok=True)
        with open(tbl_path, 'w', encoding='utf-8') as f:
            f.write(tbl_content)
            
        self.encoding.load_tbl(tbl_path)
        self.tbl_status.setText("Loaded: auto_generated.tbl")
        
        idx = self.enc_combo.findText("Custom TBL")
        if idx >= 0: self.enc_combo.setCurrentIndex(idx)
        
        self.region_combo.setCurrentIndex(0) 
        self.current_offset = (address - 0x08000000) & ~0xF
        self.refresh_memory()
        
        self.tabs.setCurrentIndex(0)

    # =========================================================================
    # TAB 4: WYSIWYG LIVE RAM TEXT INJECTOR (WITH AUTO-DETECT)
    # =========================================================================

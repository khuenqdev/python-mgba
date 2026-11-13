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

class ScriptTabMixin:
    def _init_script_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        scan_grp = QGroupBox("1. Extract Scripts from Pointer Table")
        scan_layout = QHBoxLayout(scan_grp)
        
        scan_layout.addWidget(QLabel("Table Start:"))
        self.ptr_start_edit = QLineEdit("0x08000000")
        scan_layout.addWidget(self.ptr_start_edit)
        
        scan_layout.addWidget(QLabel("Table End:"))
        self.ptr_end_edit = QLineEdit("0x08000100")
        scan_layout.addWidget(self.ptr_end_edit)
        
        scan_layout.addWidget(QLabel("Terminator (Hex):"))
        self.ptr_term_edit = QLineEdit("00")
        self.ptr_term_edit.setFixedWidth(50)
        scan_layout.addWidget(self.ptr_term_edit)
        
        scan_btn = QPushButton("Scan & Dump Scripts")
        scan_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        scan_btn.clicked.connect(self.scan_pointers_for_scripts)
        scan_layout.addWidget(scan_btn)
        layout.addWidget(scan_grp)

        self.script_table = QTableWidget(0, 4)
        self.script_table.setHorizontalHeaderLabels(["Pointer Addr", "Target Addr", "Original Text", "Translated Text"])
        self.script_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.script_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.script_table)
        
        csv_bar = QHBoxLayout()
        export_csv_btn = QPushButton("⬇ Export to CSV")
        export_csv_btn.clicked.connect(self.export_scripts_csv)
        import_csv_btn = QPushButton("⬆ Import from CSV")
        import_csv_btn.clicked.connect(self.import_scripts_csv)
        csv_bar.addWidget(export_csv_btn)
        csv_bar.addWidget(import_csv_btn)
        csv_bar.addStretch()
        layout.addLayout(csv_bar)

        patch_grp = QGroupBox("2. Repoint and Patch ROM")
        patch_layout = QHBoxLayout(patch_grp)
        
        patch_layout.addWidget(QLabel("Free Space Offset (Target for injected text):"))
        self.free_space_edit = QLineEdit("0x08800000")
        patch_layout.addWidget(self.free_space_edit)
        
        patch_btn = QPushButton("Translate & Generate Patched ROM...")
        patch_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 5px;")
        patch_btn.clicked.connect(self.apply_translations_and_repoint)
        patch_layout.addWidget(patch_btn)
        layout.addWidget(patch_grp)

    def scan_pointers_for_scripts(self):
        if not self.worker.rom_path or not os.path.exists(self.worker.rom_path):
            QMessageBox.warning(self, "Error", "No ROM file available to scan.")
            return
            
        try:
            start_addr = int(self.ptr_start_edit.text(), 16)
            end_addr = int(self.ptr_end_edit.text(), 16)
            terminator = int(self.ptr_term_edit.text(), 16)
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Please ensure addresses are valid Hex.")
            return
            
        with open(self.worker.rom_path, 'rb') as f:
            rom_data = f.read()
            
        rom_size = len(rom_data)
        if start_addr >= 0x08000000: start_addr -= 0x08000000
        if end_addr >= 0x08000000: end_addr -= 0x08000000
        
        self.script_table.setRowCount(0)
        
        row = 0
        for offset in range(start_addr, end_addr, 4):
            if offset + 4 > rom_size: break
            ptr_val = struct.unpack("<I", rom_data[offset:offset+4])[0]
            
            if 0x08000000 <= ptr_val < (0x08000000 + rom_size):
                str_offset = ptr_val - 0x08000000
                str_bytes = bytearray()
                curr = str_offset
                while curr < rom_size:
                    b = rom_data[curr]
                    if b == terminator: break
                    str_bytes.append(b)
                    curr += 1
                
                decoded_text = self.encoding.decode(bytes(str_bytes))
                
                self.script_table.insertRow(row)
                self.script_table.setItem(row, 0, QTableWidgetItem(f"0x{0x08000000 + offset:08X}"))
                self.script_table.setItem(row, 1, QTableWidgetItem(f"0x{ptr_val:08X}"))
                
                orig_item = QTableWidgetItem(decoded_text)
                orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.script_table.setItem(row, 2, orig_item)
                self.script_table.setItem(row, 3, QTableWidgetItem(""))
                row += 1
                
        QMessageBox.information(self, "Scan Complete", f"Successfully extracted {row} strings from pointers.")

    def export_scripts_csv(self):
        if self.script_table.rowCount() == 0: return
        filepath, _ = QFileDialog.getSaveFileName(self, "Export Scripts", "", "CSV Files (*.csv)")
        if filepath:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Pointer Addr", "Target Addr", "Original Text", "Translated Text"])
                for r in range(self.script_table.rowCount()):
                    writer.writerow([
                        self.script_table.item(r, 0).text(),
                        self.script_table.item(r, 1).text(),
                        self.script_table.item(r, 2).text(),
                        self.script_table.item(r, 3).text() if self.script_table.item(r, 3) else ""
                    ])
            QMessageBox.information(self, "Exported", f"Scripts saved to {os.path.basename(filepath)}")

    def import_scripts_csv(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Import Scripts", "", "CSV Files (*.csv)")
        if filepath:
            self.script_table.setRowCount(0)
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)
                for row, data in enumerate(reader):
                    if len(data) < 4: continue
                    self.script_table.insertRow(row)
                    self.script_table.setItem(row, 0, QTableWidgetItem(data[0]))
                    self.script_table.setItem(row, 1, QTableWidgetItem(data[1]))
                    
                    orig_item = QTableWidgetItem(data[2])
                    orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.script_table.setItem(row, 2, orig_item)
                    self.script_table.setItem(row, 3, QTableWidgetItem(data[3]))
            QMessageBox.information(self, "Imported", "Scripts loaded successfully.")

    def apply_translations_and_repoint(self):
        if self.script_table.rowCount() == 0: return
        
        try:
            free_space_addr = int(self.free_space_edit.text(), 16)
            terminator = int(self.ptr_term_edit.text(), 16)
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Please ensure Free Space offset and Terminator are valid Hex.")
            return
            
        with open(self.worker.rom_path, 'rb') as f:
            rom_data = bytearray(f.read())
            
        if free_space_addr >= 0x08000000:
            free_space_offset = free_space_addr - 0x08000000
        else:
            free_space_offset = free_space_addr
            
        lines_patched = 0
        
        for r in range(self.script_table.rowCount()):
            trans_item = self.script_table.item(r, 3)
            if not trans_item or not trans_item.text().strip(): continue
                
            translated_text = trans_item.text()
            ptr_addr = int(self.script_table.item(r, 0).text(), 16) - 0x08000000
            
            encoded_bytes = self.encoding.encode(translated_text) + bytes([terminator])
            
            if free_space_offset + len(encoded_bytes) > len(rom_data):
                padding_needed = (free_space_offset + len(encoded_bytes)) - len(rom_data)
                rom_data.extend(b'\xFF' * padding_needed)
                
            rom_data[free_space_offset : free_space_offset + len(encoded_bytes)] = encoded_bytes
            new_ptr_val = free_space_offset + 0x08000000
            rom_data[ptr_addr : ptr_addr + 4] = struct.pack("<I", new_ptr_val)
            
            free_space_offset += len(encoded_bytes)
            lines_patched += 1

        default_name = f"translated_{os.path.basename(self.worker.rom_path)}"
        save_path, _ = QFileDialog.getSaveFileName(self, "Save Patched ROM", default_name, "GBA ROMs (*.gba)")
        if save_path:
            with open(save_path, 'wb') as f: f.write(rom_data)
            self.free_space_edit.setText(f"0x{free_space_offset + 0x08000000:08X}")
            QMessageBox.information(self, "Success", f"Repointed and injected {lines_patched} translated lines into new ROM!")

    # =========================================================================
    # TAB 3: RELATIVE VALUE SEARCH
    # =========================================================================

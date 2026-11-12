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

class EncodingManager:
    """Handles Shift-JIS, ASCII, and Custom Hex .tbl decoding and encoding."""
    def __init__(self):
        self.mode = "ASCII"
        self.custom_tbl = {}
        self.rev_tbl = {}
        self.rev_keys = []
        self.max_key_len = 1

    def load_tbl(self, filepath: str) -> bool:
        self.custom_tbl.clear()
        self.rev_tbl.clear()
        self.rev_keys.clear()
        self.max_key_len = 1
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or '=' not in line: continue
                    hex_part, text_part = line.split('=', 1)
                    
                    try:
                        key_bytes = bytes.fromhex(hex_part)
                        self.custom_tbl[key_bytes] = text_part
                        if len(key_bytes) > self.max_key_len:
                            self.max_key_len = len(key_bytes)
                    except ValueError:
                        continue
            
            # Build reverse lookup table for encoding
            self.rev_tbl = {v: k for k, v in self.custom_tbl.items()}
            self.rev_keys = sorted(self.rev_tbl.keys(), key=len, reverse=True)
            self.mode = "Custom TBL"
            return True
        except Exception as e:
            print(f"TBL Load Error: {e}")
            return False

    def decode(self, data: bytes) -> str:
        if not data: return ""
        
        if self.mode == "Shift-JIS":
            return data.decode('shift_jis', errors='replace').replace('\n', ' ')
        elif self.mode == "ASCII":
            return "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
        elif self.mode == "Custom TBL" and self.custom_tbl:
            decoded = ""
            i = 0
            while i < len(data):
                matched = False
                for length in range(self.max_key_len, 0, -1):
                    if i + length > len(data): continue
                    chunk = data[i:i+length]
                    if chunk in self.custom_tbl:
                        decoded += self.custom_tbl[chunk]
                        i += length
                        matched = True
                        break
                if not matched:
                    decoded += "."
                    i += 1
            return decoded
        return "".join(chr(b) if 32 <= b <= 126 else "." for b in data)

    def encode(self, text: str) -> bytes:
        if not text: return b""
        out = bytearray()
        
        parts = re.split(r'\[([0-9A-Fa-f]{2})\]', text)
        
        for i, part in enumerate(parts):
            if i % 2 == 1:
                out.append(int(part, 16))
            else:
                if not part: continue
                if self.mode == "Shift-JIS":
                    out.extend(part.encode('shift_jis', errors='ignore'))
                elif self.mode == "ASCII":
                    out.extend(part.encode('ascii', errors='ignore'))
                elif self.mode == "Custom TBL" and self.rev_tbl:
                    pi = 0
                    while pi < len(part):
                        matched = False
                        for k in self.rev_keys:
                            if part.startswith(k, pi):
                                out.extend(self.rev_tbl[k])
                                pi += len(k)
                                matched = True
                                break
                        if not matched:
                            out.extend(part[pi].encode('ascii', errors='ignore'))
                            pi += 1
        return bytes(out)


class TranslationToolsDialog(QDialog):
    """Integrated Translation Suite for ROM Hackers."""
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.encoding = EncodingManager()
        
        self.setWindowTitle("Translation Tools - Text Inspector & Script Patcher")
        self.resize(1000, 700)
        
        self._init_ui()
        self.refresh_memory()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # --- Top Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<b>Global Encoding Mode:</b>"))
        
        self.enc_combo = QComboBox()
        self.enc_combo.addItems(["ASCII", "Shift-JIS", "Custom TBL"])
        self.enc_combo.currentIndexChanged.connect(self.on_encoding_changed)
        toolbar.addWidget(self.enc_combo)
        
        self.load_tbl_btn = QPushButton("Load .tbl File...")
        self.load_tbl_btn.clicked.connect(self.on_load_tbl)
        toolbar.addWidget(self.load_tbl_btn)
        
        self.tbl_status = QLabel("No table loaded.")
        self.tbl_status.setStyleSheet("color: #888; font-style: italic;")
        toolbar.addWidget(self.tbl_status)
        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # --- Tabs ---
        self.tabs = QTabWidget()
        
        self.tab_viewer = QWidget()
        self._init_viewer_tab(self.tab_viewer)
        self.tabs.addTab(self.tab_viewer, "Hex / Text Viewer")
        
        self.tab_script = QWidget()
        self._init_script_tab(self.tab_script)
        self.tabs.addTab(self.tab_script, "Script Extractor & Auto-Repointer")

        self.tab_search = QWidget()
        self._init_search_tab(self.tab_search)
        self.tabs.addTab(self.tab_search, "Relative Value Search")

        self.tab_injector = QWidget()
        self._init_injector_tab(self.tab_injector)
        self.tabs.addTab(self.tab_injector, "WYSIWYG Live RAM Text Injector")
        
        main_layout.addWidget(self.tabs)

    # =========================================================================
    # TAB 1: HEX / TEXT VIEWER
    # =========================================================================
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
    def _init_injector_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        desc = QLabel(
            "<b>WYSIWYG Live Text Injector</b><br>"
            "Type translated text to inject directly into the game's live RAM (EWRAM/IWRAM) buffer.<br>"
            "<i>Note: Use [XX] for raw hex control codes (e.g., [FE] for Newline, [FF] for String Terminator).</i><br>"
            "Draw a box around the on-screen dialogue using the <b>Screen Pixel Ruler (Translation Menu)</b>, then click Auto-Detect!"
        )
        desc.setStyleSheet("background-color: #1a1a1a; padding: 10px; border-radius: 5px;")
        layout.addWidget(desc)

        cfg_row = QHBoxLayout()
        cfg_row.addWidget(QLabel("Target RAM Address:"))
        self.inj_addr_edit = QLineEdit("0x02000000")
        self.inj_addr_edit.setFixedWidth(120)
        cfg_row.addWidget(self.inj_addr_edit)

        # 🎯 The Auto-Detect Button
        btn_detect = QPushButton("🎯 Auto-Detect from Ruler Box")
        btn_detect.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; padding: 5px;")
        btn_detect.clicked.connect(self.auto_detect_from_ruler)
        cfg_row.addWidget(btn_detect)

        cfg_row.addWidget(QLabel("Max Bytes:"))
        self.inj_max_bytes = QSpinBox()
        self.inj_max_bytes.setRange(1, 1024)
        self.inj_max_bytes.setValue(256)
        cfg_row.addWidget(self.inj_max_bytes)
        
        self.inj_auto_freeze = QCheckBox("Lock in RAM (15Hz)")
        self.inj_auto_freeze.toggled.connect(self.toggle_auto_inject)
        cfg_row.addWidget(self.inj_auto_freeze)
        cfg_row.addStretch()
        layout.addLayout(cfg_row)

        self.inj_text_edit = QTextEdit()
        self.inj_text_edit.setPlaceholderText("Enter translated dialogue here...\nExample: Hello World![FE]This is a test.[FF]")
        layout.addWidget(self.inj_text_edit)

        btn_row = QHBoxLayout()
        inj_btn = QPushButton("⚡ Inject Text to RAM")
        inj_btn.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold; font-size: 14px; padding: 10px;")
        inj_btn.clicked.connect(self.inject_text_to_ram)
        btn_row.addWidget(inj_btn)
        
        self.inj_status = QLabel("Ready.")
        self.inj_status.setStyleSheet("color: #00FF66; font-weight: bold;")
        btn_row.addWidget(self.inj_status)
        layout.addLayout(btn_row)

        self.auto_inject_timer = QTimer(self)
        self.auto_inject_timer.setInterval(66)
        self.auto_inject_timer.timeout.connect(self.inject_text_to_ram)

    def auto_detect_from_ruler(self):
        """Uses the drawn Screen Ruler box to auto-detect the RAM address of the text inside."""
        if not self.parent() or not hasattr(self.parent(), 'video_widget'):
            return

        video_widget = self.parent().video_widget
        if not video_widget.ruler_start or not video_widget.ruler_end:
            QMessageBox.warning(self, "Error", "Please draw a box over the text on the screen using the Pixel Ruler (Translation Menu) first.")
            return

        x1, y1 = video_widget.ruler_start
        x2, y2 = video_widget.ruler_end
        rx, ry = min(x1, x2), min(y1, y2)
        rw, rh = abs(x2 - x1), abs(y2 - y1)

        # Convert screen pixels to tilemap coordinates (8x8)
        tx1, ty1 = rx // 8, ry // 8
        tx2, ty2 = (rx + rw) // 8, (ry + rh) // 8

        vram = self.worker.get_memory_region("VRAM")
        if not vram: return

        # Find active screen base blocks
        active_sbbs = []
        for sbb in range(32):
            if any(vram[sbb*0x800 : (sbb+1)*0x800]):
                active_sbbs.append(sbb)

        best_match = None
        for sbb in active_sbbs:
            sbb_offset = sbb * 0x800
            map_entries = struct.unpack("<1024H", vram[sbb_offset:sbb_offset+0x800])
            
            # Extract longest contiguous horizontal sequence of non-zero tiles
            longest_seq = []
            for ty in range(ty1, ty2 + 1):
                current_seq = []
                for tx in range(tx1, tx2 + 1):
                    if ty < 32 and tx < 32:
                        t_idx = map_entries[ty * 32 + tx] & 0x3FF
                        if t_idx != 0:
                            current_seq.append(t_idx)
                        else:
                            if len(current_seq) > len(longest_seq):
                                longest_seq = current_seq
                            current_seq = []
                if len(current_seq) > len(longest_seq):
                    longest_seq = current_seq

            if len(longest_seq) >= 3:
                # Calculate relative distances of the tiles
                target_diffs = [(longest_seq[i+1] - longest_seq[i]) % 256 for i in range(len(longest_seq)-1)]
                
                # Fast relative search through EWRAM
                ewram = self.worker.get_memory_region("EWRAM")
                match = self._numpy_relative_search(ewram, target_diffs)
                if match is not None:
                    best_match = 0x02000000 + match
                    break
                
                # Fallback to IWRAM
                iwram = self.worker.get_memory_region("SYSTEM_RAM")
                if iwram and len(iwram) > 256 * 1024:
                    match = self._numpy_relative_search(iwram[256 * 1024:], target_diffs)
                    if match is not None:
                        best_match = 0x03000000 + match
                        break

        if best_match:
            self.inj_addr_edit.setText(f"0x{best_match:08X}")
            self.inj_status.setText(f"Auto-Detected target at 0x{best_match:08X}!")
        else:
            self.inj_status.setText("Could not find matching text sequence in RAM.")
            QMessageBox.information(self, "Not Found", "Could not correlate the screen tiles to an active string in RAM.\nThe game may be using VWF (Variable Width Fonts) or a 2-byte encoding.")

    def _numpy_relative_search(self, mem, target_diffs):
        if not mem or len(mem) < len(target_diffs): return None
        data_np = np.frombuffer(mem, dtype=np.uint8).astype(np.int16)
        diffs_np = (data_np[1:] - data_np[:-1]) % 256
        L = len(diffs_np) - len(target_diffs) + 1
        if L <= 0: return None
        
        mask = (diffs_np[:L] == target_diffs[0])
        for j in range(1, len(target_diffs)):
            mask &= (diffs_np[j : L+j] == target_diffs[j])
            
        indices = np.where(mask)[0]
        if len(indices) > 0:
            return indices[0]
        return None

    def toggle_auto_inject(self, checked):
        if checked:
            self.auto_inject_timer.start()
            self.inj_status.setText("Status: AUTO-INJECTING (Locked in RAM)")
        else:
            self.auto_inject_timer.stop()
            self.inj_status.setText("Status: Auto-Inject Stopped.")

    def inject_text_to_ram(self):
        if not self.worker or not self.worker.emu:
            self.inj_status.setText("Error: Emulator not running.")
            return

        try:
            target_addr = int(self.inj_addr_edit.text(), 16)
        except ValueError:
            self.inj_status.setText("Error: Invalid Target Address.")
            return

        text = self.inj_text_edit.toPlainText()
        if not text:
            return

        payload = self.encoding.encode(text)
        max_bytes = self.inj_max_bytes.value()

        if len(payload) > max_bytes:
            payload = payload[:max_bytes]

        if 0x02000000 <= target_addr <= 0x0203FFFF:
            offset = target_addr - 0x02000000
            self.worker.write_memory_region("EWRAM", offset, payload)
            if not self.inj_auto_freeze.isChecked():
                self.inj_status.setText(f"Success: Injected {len(payload)} bytes to EWRAM.")
        elif 0x03000000 <= target_addr <= 0x03007FFF:
            offset = target_addr - 0x03000000
            mem = self.worker.get_memory_region("SYSTEM_RAM")
            if mem and len(mem) > 256 * 1024:
                # Assuming IWRAM comes after EWRAM in the SYSTEM_RAM block
                self.worker.write_memory_region("SYSTEM_RAM", (256 * 1024) + offset, payload)
                if not self.inj_auto_freeze.isChecked():
                    self.inj_status.setText(f"Success: Injected {len(payload)} bytes to IWRAM.")
            else:
                self.inj_status.setText("Error: IWRAM region not accessible on this core.")
        else:
            self.inj_status.setText("Error: Address must be in EWRAM (0x02..) or IWRAM (0x03..).")

    def closeEvent(self, event):
        if hasattr(self, 'auto_inject_timer'):
            self.auto_inject_timer.stop()
        event.accept()
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

class InjectorTabMixin:
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


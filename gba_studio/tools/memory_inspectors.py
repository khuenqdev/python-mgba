# dev_tools.py
import os
import struct
import datetime
import numpy as np
from PIL import Image

from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QComboBox, QSpinBox, 
                             QTableWidget, QTableWidgetItem, QHeaderView, 
                             QScrollArea, QFileDialog, QMessageBox, QLineEdit, 
                             QSplitter, QCheckBox)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt

from .dev_common import *

class FrameInspectorDialog(QDialog):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Frame Inspector")
        self.resize(540, 440)

        self._init_ui()
        self.update_frame_display()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
        self.step_btn = QPushButton("▶ Step 1 Frame")
        self.step_btn.clicked.connect(self.step_frame)
        self.resume_btn = QPushButton("Resume Game")
        self.resume_btn.clicked.connect(self.resume_game)
        self.export_btn = QPushButton("Export Current Frame to PNG")
        self.export_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.export_btn.clicked.connect(self.export_png)

        top_bar.addWidget(self.step_btn)
        top_bar.addWidget(self.resume_btn)
        top_bar.addWidget(self.export_btn)
        layout.addLayout(top_bar)

        self.frame_label = QLabel("No frame available yet.")
        self.frame_label.setStyleSheet("background: black; color: white;")
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.frame_label)

    def step_frame(self):
        if self.worker:
            self.worker.step_one_frame()
            self.update_frame_display()

    def resume_game(self):
        if self.worker:
            self.worker.is_paused = False
            self.accept()

    def update_frame_display(self):
        frame = getattr(self.worker, 'latest_video_frame', None)
        if frame is not None:
            h, w, c = frame.shape
            qimg = QImage(frame.data, w, h, w * c, QImage.Format.Format_RGB888)
            self.frame_label.setPixmap(QPixmap.fromImage(qimg).scaled(480, 320, Qt.AspectRatioMode.KeepAspectRatio))
        else:
            self.frame_label.setText("No frame available yet. Step 1 frame or unpause.")

    def export_png(self):
        frame = getattr(self.worker, 'latest_video_frame', None)
        if frame is not None:
            img = Image.fromarray(frame, 'RGB')
            export_pil_to_png(self, img, f"frame_inspect_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png", self.config)


# ==============================================================================
# 6. SEARCH MEMORY (CHEAT SEARCHER)
# ==============================================================================
class MemorySearchDialog(QDialog):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Search Memory")
        self.resize(650, 480)

        self.candidates = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Data Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["1 Byte (uint8)", "2 Bytes (uint16)", "4 Bytes (uint32)"])
        top.addWidget(self.type_combo)

        top.addWidget(QLabel("Compare:"))
        self.cmp_combo = QComboBox()
        self.cmp_combo.addItems(["Exact Value", "Greater Than", "Less Than", "Changed", "Unchanged"])
        top.addWidget(self.cmp_combo)

        top.addWidget(QLabel("Value:"))
        self.val_edit = QLineEdit()
        self.val_edit.setPlaceholderText("Dec or 0xHex")
        top.addWidget(self.val_edit)
        layout.addLayout(top)

        btn_row = QHBoxLayout()
        self.search_btn = QPushButton("New Search")
        self.search_btn.clicked.connect(self.new_search)
        self.filter_btn = QPushButton("Filter Next")
        self.filter_btn.clicked.connect(self.filter_search)
        self.add_cheat_btn = QPushButton("Add to Cheat Engine")
        self.add_cheat_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        self.add_cheat_btn.clicked.connect(self.add_selected_as_cheat)

        btn_row.addWidget(self.search_btn)
        btn_row.addWidget(self.filter_btn)
        btn_row.addWidget(self.add_cheat_btn)
        layout.addLayout(btn_row)

        self.status_label = QLabel("Enter a value and click 'New Search'.")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Address (EWRAM)", "Old Value", "Current Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

    def get_data_size(self):
        return [1, 2, 4][self.type_combo.currentIndex()]

    def parse_input_val(self):
        text = self.val_edit.text().strip()
        if not text: return 0
        return int(text, 16) if text.startswith("0x") else int(text)

    def new_search(self):
        raw = self.worker.get_memory_region("EWRAM")
        if not raw:
            self.status_label.setText("EWRAM memory not accessible.")
            return

        size = self.get_data_size()
        cmp_type = self.cmp_combo.currentText()
        target_val = self.parse_input_val()
        self.candidates.clear()

        limit = len(raw) - size
        for off in range(0, limit, size):
            val = int.from_bytes(raw[off:off+size], "little")
            matched = False
            if cmp_type == "Exact Value" and val == target_val: matched = True
            elif cmp_type == "Greater Than" and val > target_val: matched = True
            elif cmp_type == "Less Than" and val < target_val: matched = True
            
            if matched:
                self.candidates[off] = val
                if len(self.candidates) > 5000: break

        self.update_results_table(raw)

    def filter_search(self):
        raw = self.worker.get_memory_region("EWRAM")
        if not raw: return

        size = self.get_data_size()
        cmp_type = self.cmp_combo.currentText()
        target_val = self.parse_input_val()
        new_candidates = {}

        for off, old_val in self.candidates.items():
            val = int.from_bytes(raw[off:off+size], "little")
            matched = False
            if cmp_type == "Exact Value" and val == target_val: matched = True
            elif cmp_type == "Greater Than" and val > target_val: matched = True
            elif cmp_type == "Less Than" and val < target_val: matched = True
            elif cmp_type == "Changed" and val != old_val: matched = True
            elif cmp_type == "Unchanged" and val == old_val: matched = True

            if matched: new_candidates[off] = val

        self.candidates = new_candidates
        self.update_results_table(raw)

    def update_results_table(self, raw_mem):
        size = self.get_data_size()
        self.table.setRowCount(min(len(self.candidates), 200))
        self.status_label.setText(f"Found {len(self.candidates)} matching addresses.")

        for row, (off, old_val) in enumerate(list(self.candidates.items())[:200]):
            curr_val = int.from_bytes(raw_mem[off:off+size], "little")
            addr_str = f"0x{0x02000000 + off:08X}"
            self.table.setItem(row, 0, QTableWidgetItem(addr_str))
            self.table.setItem(row, 1, QTableWidgetItem(str(old_val)))
            self.table.setItem(row, 2, QTableWidgetItem(str(curr_val)))

    def add_selected_as_cheat(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows: return
        addr_str = self.table.item(rows[0].row(), 0).text()
        curr_val = self.table.item(rows[0].row(), 2).text()
        code_str = f"{int(addr_str, 16):08X} {int(curr_val):08X}"

        self.worker.cheat_engine.cheats.append({
            "desc": f"Freeze {addr_str}",
            "code": code_str,
            "type": "Raw",
            "enabled": True
        })
        QMessageBox.information(self, "Cheat Created", f"Added cheat lock for:\n{code_str}")


# ==============================================================================
# 7. VIEW I/O REGISTERS
# ==============================================================================
class IORegisterViewerDialog(QDialog):
    REGISTERS = [
        (0x000, "DISPCNT", "LCD Control & BG Modes 0-5"),
        (0x004, "DISPSTAT", "General LCD Status (V-Blank, H-Blank, V-Counter)"),
        (0x006, "VCOUNT", "Current Scanline Counter (0-227)"),
        (0x008, "BG0CNT", "BG0 Control (Priority, Charbase, Screenbase, Size)"),
        (0x00A, "BG1CNT", "BG1 Control"),
        (0x00C, "BG2CNT", "BG2 Control"),
        (0x00E, "BG3CNT", "BG3 Control"),
        (0x010, "BG0HOFS", "BG0 Horizontal Scroll Offset"),
        (0x012, "BG0VOFS", "BG0 Vertical Scroll Offset"),
        (0x040, "WIN0H", "Window 0 Horizontal Dimensions"),
        (0x044, "WIN0V", "Window 0 Vertical Dimensions"),
        (0x048, "WININ", "Inside Window 0/1 Control"),
        (0x04A, "WINOUT", "Outside Window Control"),
        (0x080, "SOUND1CNT_L", "Sound 1 Sweep Register"),
        (0x084, "SOUND1CNT_X", "Sound 1 Frequency & Control"),
        (0x0B0, "DMA0CNT_H", "DMA 0 Control"),
        (0x0BA, "DMA1CNT_H", "DMA 1 Control"),
        (0x0C6, "DMA2CNT_H", "DMA 2 Control"),
        (0x0D2, "DMA3CNT_H", "DMA 3 Control"),
        (0x100, "TM0CNT_L", "Timer 0 Counter Data"),
        (0x102, "TM0CNT_H", "Timer 0 Control"),
        (0x200, "IE", "Interrupt Enable Register"),
        (0x202, "IF", "Interrupt Request Flags"),
        (0x208, "IME", "Interrupt Master Enable")
    ]

    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - I/O Registers")
        self.resize(700, 480)

        self._init_ui()
        self.refresh_registers()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh Registers")
        self.refresh_btn.clicked.connect(self.refresh_registers)
        top.addWidget(self.refresh_btn)
        top.addStretch()
        layout.addLayout(top)

        self.table = QTableWidget(len(self.REGISTERS), 4)
        self.table.setHorizontalHeaderLabels(["Address", "Name", "Value (Hex)", "Hardware Description"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

    def refresh_registers(self):
        for row, (offset, name, desc) in enumerate(self.REGISTERS):
            addr_str = f"0x0400{offset:04X}"
            val_str = "0x0080" if name == "DISPCNT" else "0x0000"
            self.table.setItem(row, 0, QTableWidgetItem(addr_str))
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(val_str))
            self.table.setItem(row, 3, QTableWidgetItem(desc))

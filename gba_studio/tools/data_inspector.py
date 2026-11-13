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

class DataInspectorWidget(QWidget):
    """ImHex-style side panel for live parsing of data structures at cursor position."""
    jump_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(270)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        group = QGroupBox("Data Inspector")
        group.setStyleSheet("QGroupBox { font-weight: bold; color: #64B5F6; }")
        g_layout = QVBoxLayout(group)

        self.lbl_offset = QLabel("Offset: 0x00000000")
        self.lbl_offset.setStyleSheet("font-family: monospace; font-size: 12px; color: #81C784;")
        g_layout.addWidget(self.lbl_offset)

        self.lbl_binary = QLabel("Binary: 0000 0000")
        self.lbl_binary.setStyleSheet("font-family: monospace; font-size: 11px;")
        g_layout.addWidget(self.lbl_binary)

        self.lbl_int8 = QLabel("int8: 0  |  uint8: 0 (0x00)")
        self.lbl_int8.setStyleSheet("font-family: monospace; font-size: 11px;")
        g_layout.addWidget(self.lbl_int8)

        self.lbl_int16 = QLabel("int16: 0  |  uint16: 0")
        self.lbl_int16.setStyleSheet("font-family: monospace; font-size: 11px;")
        g_layout.addWidget(self.lbl_int16)

        self.lbl_int32 = QLabel("int32: 0\nuint32: 0 (0x00000000)")
        self.lbl_int32.setStyleSheet("font-family: monospace; font-size: 11px;")
        g_layout.addWidget(self.lbl_int32)

        self.lbl_float = QLabel("float32: 0.0")
        self.lbl_float.setStyleSheet("font-family: monospace; font-size: 11px;")
        g_layout.addWidget(self.lbl_float)

        # GBA BGR555 Color Swatch
        color_box = QHBoxLayout()
        color_box.addWidget(QLabel("GBA Color:"))
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(24, 24)
        self.color_swatch.setStyleSheet("background: #000; border: 1px solid #555;")
        color_box.addWidget(self.color_swatch)
        self.lbl_color_val = QLabel("RGB: (0, 0, 0)")
        self.lbl_color_val.setStyleSheet("font-size: 11px;")
        color_box.addWidget(self.lbl_color_val)
        color_box.addStretch()
        g_layout.addLayout(color_box)

        # GBA Pointer Target Detector
        self.pointer_box = QGroupBox("GBA Pointer")
        p_layout = QVBoxLayout(self.pointer_box)
        self.lbl_pointer = QLabel("Target: None")
        self.lbl_pointer.setStyleSheet("font-family: monospace; font-size: 11px; color: #FFD54F;")
        p_layout.addWidget(self.lbl_pointer)
        self.btn_jump_ptr = QPushButton("Follow Pointer")
        self.btn_jump_ptr.setStyleSheet("background: #1976D2; color: white; font-weight: bold; padding: 4px;")
        self.btn_jump_ptr.clicked.connect(self.on_jump_pointer)
        self.btn_jump_ptr.setEnabled(False)
        p_layout.addWidget(self.btn_jump_ptr)
        g_layout.addWidget(self.pointer_box)

        g_layout.addStretch()
        layout.addWidget(group)
        self.target_pointer_addr = None

    def update_values(self, data: bytearray, offset: int, base_addr: int):
        if not data or offset >= len(data): return
        abs_addr = base_addr + offset
        self.lbl_offset.setText(f"Offset: 0x{abs_addr:08X} ({abs_addr})")

        # 8-Bit
        b8 = data[offset]
        b_bin = f"{b8:08b}"
        self.lbl_binary.setText(f"Binary: {b_bin[:4]} {b_bin[4:]}")
        i8 = struct.unpack("b", bytes([b8]))[0]
        self.lbl_int8.setText(f"int8: {i8}  |  uint8: {b8} (0x{b8:02X})")

        # 16-Bit
        if offset + 2 <= len(data):
            chunk16 = bytes(data[offset:offset+2])
            u16 = struct.unpack("<H", chunk16)[0]
            i16 = struct.unpack("<h", chunk16)[0]
            self.lbl_int16.setText(f"int16: {i16}  |  uint16: {u16}")

            # GBA BGR555 Decoding
            r = (u16 & 0x1F) * 255 // 31
            g = ((u16 >> 5) & 0x1F) * 255 // 31
            b = ((u16 >> 10) & 0x1F) * 255 // 31
            self.color_swatch.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #FFF;")
            self.lbl_color_val.setText(f"RGB: ({r}, {g}, {b})")
        else:
            self.lbl_int16.setText("int16: N/A")

        # 32-Bit & Float
        if offset + 4 <= len(data):
            chunk32 = bytes(data[offset:offset+4])
            u32 = struct.unpack("<I", chunk32)[0]
            i32 = struct.unpack("<i", chunk32)[0]
            flt = struct.unpack("<f", chunk32)[0]
            self.lbl_int32.setText(f"int32: {i32}\nuint32: {u32} (0x{u32:08X})")
            self.lbl_float.setText(f"float32: {flt:.6f}")

            # GBA Pointer Classification
            self.target_pointer_addr = None
            if 0x08000000 <= u32 <= 0x09FFFFFF:
                self.lbl_pointer.setText(f"ROM: 0x{u32:08X}")
                self.target_pointer_addr = u32
                self.btn_jump_ptr.setEnabled(True)
            elif 0x02000000 <= u32 <= 0x0203FFFF:
                self.lbl_pointer.setText(f"EWRAM: 0x{u32:08X}")
                self.target_pointer_addr = u32
                self.btn_jump_ptr.setEnabled(True)
            elif 0x03000000 <= u32 <= 0x03007FFF:
                self.lbl_pointer.setText(f"IWRAM: 0x{u32:08X}")
                self.target_pointer_addr = u32
                self.btn_jump_ptr.setEnabled(True)
            elif 0x06000000 <= u32 <= 0x06017FFF:
                self.lbl_pointer.setText(f"VRAM: 0x{u32:08X}")
                self.target_pointer_addr = u32
                self.btn_jump_ptr.setEnabled(True)
            else:
                self.lbl_pointer.setText("Target: None")
                self.btn_jump_ptr.setEnabled(False)
        else:
            self.lbl_int32.setText("int32: N/A")
            self.lbl_float.setText("float32: N/A")
            self.btn_jump_ptr.setEnabled(False)

    def on_jump_pointer(self):
        if self.target_pointer_addr is not None:
            self.jump_requested.emit(self.target_pointer_addr)



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


class VirtualHexCanvas(QAbstractScrollArea):
    """Virtual scrollable hex editor canvas supporting large memory spaces at 60 FPS."""
    cursor_changed = pyqtSignal(int)
    data_modified = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = bytearray()
        self.original_data = bytes()
        self.base_address = 0x02000000
        self.bytes_per_row = 16

        # Editor State
        self.cursor_pos = 0
        self.cursor_nibble = 0  # 0: high nibble, 1: low nibble
        self.cursor_in_text = False
        self.selection_start = None
        self.selection_end = None
        self.is_dragging = False

        # Undo / Redo / Modifications
        self.modified_map = {}  # offset -> original_byte
        self.undo_stack = []
        self.redo_stack = []

        # Highlighting & Live Watch
        self.live_diff_offsets = set()
        self.search_matches = []
        self.bookmarks = set()

        # Appearance & Typography
        self.font = QFont("Cascadia Code, Consolas, Courier New, monospace", 10)
        self.font.setStyleHint(QFont.StyleHint.Monospace)
        self.font_metrics = QFontMetrics(self.font)
        self.char_w = self.font_metrics.horizontalAdvance('0')
        self.line_h = self.font_metrics.lineSpacing() + 3

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setStyleSheet("background-color: #12151B;")
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())

    def set_buffer(self, raw_bytes: bytes, base_address: int):
        self.original_data = bytes(raw_bytes) if raw_bytes else bytes()
        self.data = bytearray(self.original_data)
        self.base_address = base_address
        self.cursor_pos = 0
        self.cursor_nibble = 0
        self.selection_start = None
        self.selection_end = None
        self.modified_map.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.live_diff_offsets.clear()
        self.search_matches.clear()
        self.update_scroll_limits()
        self.viewport().update()
        self.cursor_changed.emit(self.cursor_pos)

    def update_live_buffer(self, new_bytes: bytes):
        """Refreshes buffer from live emulator and highlights deltas (ImHex/CheatEngine style)."""
        if not new_bytes: return
        min_len = min(len(self.data), len(new_bytes))

        changed = set()
        for i in range(min_len):
            if self.data[i] != new_bytes[i] and i not in self.modified_map:
                changed.add(i)
                self.data[i] = new_bytes[i]

        self.live_diff_offsets = changed
        self.viewport().update()
        self.cursor_changed.emit(self.cursor_pos)

    def update_scroll_limits(self):
        total_rows = (len(self.data) + self.bytes_per_row - 1) // self.bytes_per_row
        visible_rows = self.viewport().height() // self.line_h
        self.verticalScrollBar().setRange(0, max(0, total_rows - visible_rows))
        self.verticalScrollBar().setSingleStep(1)
        self.verticalScrollBar().setPageStep(max(1, visible_rows))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scroll_limits()

    # =========================================================================
    # Geometry & Layout Helpers
    # =========================================================================
    def get_column_offsets(self):
        margin = 12
        addr_width = 9 * self.char_w
        hex_start = margin + addr_width + 15
        byte_w = 3 * self.char_w
        gap_w = self.char_w
        hex_width = (16 * byte_w) + gap_w
        text_start = hex_start + hex_width + 15
        return margin, hex_start, text_start

    def offset_to_coords(self, offset: int):
        first_row = self.verticalScrollBar().value()
        row = (offset // self.bytes_per_row) - first_row
        col = offset % self.bytes_per_row
        _, hex_start, text_start = self.get_column_offsets()

        extra_gap = self.char_w if col >= 8 else 0
        hex_x = hex_start + (col * 3 * self.char_w) + extra_gap
        text_x = text_start + (col * self.char_w)
        y = row * self.line_h + 3
        return hex_x, text_x, y

    def pos_to_offset(self, pos):
        first_row = self.verticalScrollBar().value()
        row = (pos.y() - 3) // self.line_h
        target_row = first_row + row
        if target_row < 0: return None, False

        _, hex_start, text_start = self.get_column_offsets()
        x = pos.x()

        # Check Decoded Text Column
        if x >= text_start:
            col = (x - text_start) // self.char_w
            col = max(0, min(15, col))
            offset = (target_row * self.bytes_per_row) + col
            return min(len(self.data) - 1, max(0, offset)), True

        # Check Hex Column
        if x >= hex_start:
            rel_x = x - hex_start
            col = rel_x // (3 * self.char_w)
            if rel_x >= (8 * 3 * self.char_w):
                col = (rel_x - self.char_w) // (3 * self.char_w)
            col = max(0, min(15, col))
            offset = (target_row * self.bytes_per_row) + col
            return min(len(self.data) - 1, max(0, offset)), False

        return None, False

    # =========================================================================
    # Navigation & Selection
    # =========================================================================
    def set_cursor(self, offset: int, in_text=False):
        if not self.data: return
        self.cursor_pos = max(0, min(len(self.data) - 1, offset))
        self.cursor_nibble = 0
        self.cursor_in_text = in_text

        # Keep cursor in view
        first_row = self.verticalScrollBar().value()
        row = self.cursor_pos // self.bytes_per_row
        visible_rows = self.viewport().height() // self.line_h

        if row < first_row:
            self.verticalScrollBar().setValue(row)
        elif row >= first_row + visible_rows:
            self.verticalScrollBar().setValue(row - visible_rows + 1)

        self.viewport().update()
        self.cursor_changed.emit(self.cursor_pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            off, in_text = self.pos_to_offset(event.pos())
            if off is not None:
                self.set_cursor(off, in_text)
                self.selection_start = off
                self.selection_end = off
                self.is_dragging = True
                self.viewport().update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            off, _ = self.pos_to_offset(event.pos())
            if off is not None:
                self.selection_end = off
                self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False

    def get_selection_range(self):
        if self.selection_start is None or self.selection_end is None:
            return self.cursor_pos, self.cursor_pos
        s = min(self.selection_start, self.selection_end)
        e = max(self.selection_start, self.selection_end)
        return s, e

    # =========================================================================
    # Keyboard & Inline Editing (HxD-Style)
    # =========================================================================
    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        # Shortcuts
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_C: self.copy_hex_string(); return
            elif key == Qt.Key.Key_V: self.paste_bytes(); return
            elif key == Qt.Key.Key_Z: self.undo(); return
            elif key == Qt.Key.Key_Y: self.redo(); return
            elif key == Qt.Key.Key_A:
                self.selection_start = 0
                self.selection_end = len(self.data) - 1
                self.viewport().update()
                return

        # Navigation
        if key == Qt.Key.Key_Left:
            if not self.cursor_in_text and self.cursor_nibble == 1:
                self.cursor_nibble = 0
                self.viewport().update()
            else:
                self.set_cursor(self.cursor_pos - 1, self.cursor_in_text)
            return
        elif key == Qt.Key.Key_Right:
            if not self.cursor_in_text and self.cursor_nibble == 0:
                self.cursor_nibble = 1
                self.viewport().update()
            else:
                self.set_cursor(self.cursor_pos + 1, self.cursor_in_text)
            return
        elif key == Qt.Key.Key_Up:
            self.set_cursor(self.cursor_pos - self.bytes_per_row, self.cursor_in_text)
            return
        elif key == Qt.Key.Key_Down:
            self.set_cursor(self.cursor_pos + self.bytes_per_row, self.cursor_in_text)
            return
        elif key == Qt.Key.Key_PageUp:
            page = (self.viewport().height() // self.line_h) * self.bytes_per_row
            self.set_cursor(self.cursor_pos - page, self.cursor_in_text)
            return
        elif key == Qt.Key.Key_PageDown:
            page = (self.viewport().height() // self.line_h) * self.bytes_per_row
            self.set_cursor(self.cursor_pos + page, self.cursor_in_text)
            return
        elif key == Qt.Key.Key_Tab:
            self.cursor_in_text = not self.cursor_in_text
            self.cursor_nibble = 0
            self.viewport().update()
            return

        # Inline Editing in Hex Field
        if not self.cursor_in_text:
            text = event.text()
            if text and text in "0123456789abcdefABCDEF":
                val = int(text, 16)
                old_val = self.data[self.cursor_pos]

                if self.cursor_pos not in self.modified_map:
                    self.modified_map[self.cursor_pos] = old_val

                if self.cursor_nibble == 0:
                    new_val = (val << 4) | (old_val & 0x0F)
                    self._apply_edit(self.cursor_pos, new_val)
                    self.cursor_nibble = 1
                else:
                    new_val = (old_val & 0xF0) | val
                    self._apply_edit(self.cursor_pos, new_val)
                    self.cursor_nibble = 0
                    self.set_cursor(self.cursor_pos + 1, in_text=False)

                self.viewport().update()
                self.data_modified.emit()
                return

        # Inline Editing in Decoded Text Field
        else:
            text = event.text()
            if text and len(text) == 1 and 32 <= ord(text) <= 126:
                if self.cursor_pos not in self.modified_map:
                    self.modified_map[self.cursor_pos] = self.data[self.cursor_pos]

                self._apply_edit(self.cursor_pos, ord(text))
                self.set_cursor(self.cursor_pos + 1, in_text=True)
                self.viewport().update()
                self.data_modified.emit()
                return

        super().keyPressEvent(event)

    def _apply_edit(self, offset: int, new_val: int):
        old_val = self.data[offset]
        self.data[offset] = new_val
        self.undo_stack.append((offset, old_val, new_val))
        self.redo_stack.clear()
        self.cursor_changed.emit(self.cursor_pos)

    def undo(self):
        if not self.undo_stack: return
        offset, old_val, new_val = self.undo_stack.pop()
        self.data[offset] = old_val
        self.redo_stack.append((offset, old_val, new_val))
        if offset in self.modified_map and self.data[offset] == self.original_data[offset]:
            del self.modified_map[offset]
        self.set_cursor(offset, self.cursor_in_text)
        self.viewport().update()
        self.data_modified.emit()

    def redo(self):
        if not self.redo_stack: return
        offset, old_val, new_val = self.redo_stack.pop()
        self.data[offset] = new_val
        self.undo_stack.append((offset, old_val, new_val))
        self.modified_map[offset] = self.original_data[offset]
        self.set_cursor(offset, self.cursor_in_text)
        self.viewport().update()
        self.data_modified.emit()

    # =========================================================================
    # Rendering Pipeline
    # =========================================================================
    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setFont(self.font)

        if not self.data:
            painter.setPen(QColor(100, 110, 125))
            painter.drawText(20, 30, "No memory data loaded for this region.")
            return

        first_row = self.verticalScrollBar().value()
        visible_rows = (self.viewport().height() // self.line_h) + 2
        total_rows = (len(self.data) + self.bytes_per_row - 1) // self.bytes_per_row
        last_row = min(total_rows, first_row + visible_rows)

        margin, hex_start, text_start = self.get_column_offsets()
        sel_s, sel_e = self.get_selection_range()

        # Separator Lines
        painter.setPen(QPen(QColor(35, 42, 54), 1))
        painter.drawLine(hex_start - 8, 0, hex_start - 8, self.viewport().height())
        painter.drawLine(text_start - 8, 0, text_start - 8, self.viewport().height())

        for row_idx in range(first_row, last_row):
            y = (row_idx - first_row) * self.line_h + self.line_h
            row_offset = row_idx * self.bytes_per_row
            row_addr = self.base_address + row_offset

            # Address Column
            painter.setPen(QColor(85, 135, 175))
            painter.drawText(margin, y, f"{row_addr:08X}")

            # Bytes in Row
            row_bytes = self.data[row_offset:row_offset + self.bytes_per_row]
            for col_idx, b in enumerate(row_bytes):
                curr_offset = row_offset + col_idx
                extra_gap = self.char_w if col_idx >= 8 else 0
                hx = hex_start + (col_idx * 3 * self.char_w) + extra_gap
                tx = text_start + (col_idx * self.char_w)

                # Background Highlights
                is_selected = (sel_s <= curr_offset <= sel_e) and (self.selection_start is not None and self.selection_start != self.selection_end)
                is_cursor = (curr_offset == self.cursor_pos)
                is_modified = curr_offset in self.modified_map
                is_live_diff = curr_offset in self.live_diff_offsets
                is_bookmarked = curr_offset in self.bookmarks
                is_matched = curr_offset in self.search_matches

                bg_rect_hex = QRect(hx - 2, y - self.line_h + 4, int(2.5 * self.char_w), self.line_h)
                bg_rect_text = QRect(tx, y - self.line_h + 4, self.char_w, self.line_h)

                if is_selected:
                    painter.fillRect(bg_rect_hex, QColor(38, 79, 120))
                    painter.fillRect(bg_rect_text, QColor(38, 79, 120))
                elif is_matched:
                    painter.fillRect(bg_rect_hex, QColor(90, 85, 20))
                    painter.fillRect(bg_rect_text, QColor(90, 85, 20))
                elif is_bookmarked:
                    painter.fillRect(bg_rect_hex, QColor(80, 40, 110))
                    painter.fillRect(bg_rect_text, QColor(80, 40, 110))
                elif is_live_diff:
                    painter.fillRect(bg_rect_hex, QColor(25, 95, 55))
                    painter.fillRect(bg_rect_text, QColor(25, 95, 55))

                # Text Colors
                if is_modified:
                    text_color = QColor(255, 140, 0)  # Orange for modified bytes
                elif b == 0:
                    text_color = QColor(70, 78, 92)    # Dim zero bytes
                else:
                    text_color = QColor(215, 225, 235)

                painter.setPen(text_color)
                painter.drawText(hx, y, f"{b:02X}")

                # Decoded Text Character
                ascii_char = chr(b) if 32 <= b <= 126 else "·"
                painter.drawText(tx, y, ascii_char)

                # Cursor Rendering
                if is_cursor:
                    pen_cursor = QPen(QColor(0, 255, 180), 2)
                    painter.setPen(pen_cursor)
                    if not self.cursor_in_text:
                        painter.drawRect(bg_rect_hex)
                    else:
                        painter.drawRect(bg_rect_text)

    # =========================================================================
    # Context Menu & Copy/Paste Utilities
    # =========================================================================
    def show_context_menu(self, global_pos):
        menu = QMenu(self)
        menu.setStyleSheet("background-color: #1A1D24; color: #EEE; border: 1px solid #333;")

        act_copy_hex = menu.addAction("📋 Copy Hex String")
        act_copy_c = menu.addAction("📄 Copy as C Array")
        act_copy_txt = menu.addAction("🔤 Copy as Text")
        act_copy_addr = menu.addAction("📍 Copy Address")
        menu.addSeparator()

        act_paste = menu.addAction("📥 Paste (Overwrite)")
        act_fill_00 = menu.addAction("🧹 Fill Selection with 0x00")
        act_fill_ff = menu.addAction("⚡ Fill Selection with 0xFF")
        menu.addSeparator()

        act_bm = menu.addAction("🔖 Toggle Bookmark at Cursor")

        chosen = menu.exec(global_pos)
        if chosen == act_copy_hex: self.copy_hex_string()
        elif chosen == act_copy_c: self.copy_c_array()
        elif chosen == act_copy_txt: self.copy_text()
        elif chosen == act_copy_addr: self.copy_address()
        elif chosen == act_paste: self.paste_bytes()
        elif chosen == act_fill_00: self.fill_selection(0x00)
        elif chosen == act_fill_ff: self.fill_selection(0xFF)
        elif chosen == act_bm: self.toggle_bookmark(self.cursor_pos)

    def copy_hex_string(self):
        s, e = self.get_selection_range()
        sub = self.data[s:e + 1]
        hex_str = " ".join(f"{b:02X}" for b in sub)
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(hex_str)

    def copy_c_array(self):
        s, e = self.get_selection_range()
        sub = self.data[s:e + 1]
        c_str = ", ".join(f"0x{b:02X}" for b in sub)
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(f"const uint8_t data[{len(sub)}] = {{ {c_str} }};")

    def copy_text(self):
        s, e = self.get_selection_range()
        sub = self.data[s:e + 1]
        txt = "".join(chr(b) if 32 <= b <= 126 else "." for b in sub)
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(txt)

    def copy_address(self):
        addr_str = f"0x{self.base_address + self.cursor_pos:08X}"
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(addr_str)

    def paste_bytes(self):
        from PyQt6.QtWidgets import QApplication
        clip = QApplication.clipboard().text().strip()
        if not clip: return

        clean_hex = clip.replace(" ", "").replace(",", "").replace("0x", "")
        if all(c in "0123456789abcdefABCDEF" for c in clean_hex) and len(clean_hex) % 2 == 0:
            byte_vals = bytes.fromhex(clean_hex)
        else:
            byte_vals = clip.encode('latin-1', errors='replace')

        for i, val in enumerate(byte_vals):
            target = self.cursor_pos + i
            if target >= len(self.data): break
            if target not in self.modified_map:
                self.modified_map[target] = self.data[target]
            self._apply_edit(target, val)

        self.viewport().update()
        self.data_modified.emit()

    def fill_selection(self, fill_byte: int):
        s, e = self.get_selection_range()
        for off in range(s, e + 1):
            if off not in self.modified_map:
                self.modified_map[off] = self.data[off]
            self._apply_edit(off, fill_byte)
        self.viewport().update()
        self.data_modified.emit()

    def toggle_bookmark(self, offset: int):
        if offset in self.bookmarks:
            self.bookmarks.remove(offset)
        else:
            self.bookmarks.add(offset)
        self.viewport().update()


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
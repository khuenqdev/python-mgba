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



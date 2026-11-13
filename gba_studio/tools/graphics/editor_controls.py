# graphic_editor.py
import os
import struct
import datetime
from collections import deque
import numpy as np
from PIL import Image

from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QComboBox, QCheckBox, 
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QScrollArea, QFileDialog, QMessageBox, QSpinBox, 
                             QGroupBox, QColorDialog, QTabWidget)
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QPen
from PyQt6.QtCore import Qt

from .patches import *
from .widgets import ZoomedCanvas, TilesetGridWidget

class EditorControlsMixin:
    def on_bpp_toggled(self, checked: bool):
        self.is_8bpp = checked
        self.tileset_grid.update_dimensions()
        self.tileset_grid.update()
        self.zoom_canvas.update()

    def get_current_total_tiles(self) -> int:
        bpp_mult = 64 if self.is_8bpp else 32
        if self.selected_cbb == 6: # Entire 96 KB VRAM
            return len(self.vram_copy) // bpp_mult
        return 0x4000 // bpp_mult

    def on_tab_changed(self, idx: int):
        self.mode = "tiles" if idx == 0 else "sprites"
        if self.mode == "sprites":
            self.on_sprite_table_selected()
        else:
            self.select_tile(self.selected_tile_idx)

    def set_tool(self, tool_name: str):
        self.current_tool = tool_name
        self.btn_pencil.setChecked(tool_name == "pencil")
        self.btn_eraser.setChecked(tool_name == "eraser")
        self.btn_line.setChecked(tool_name == "line")
        self.btn_picker.setChecked(tool_name == "eyedropper")
        self.btn_bucket.setChecked(tool_name == "bucket")
        self.btn_rect.setChecked(tool_name == "rect_stroke")
        self.btn_fill.setChecked(tool_name == "rect_fill")
        self.btn_ellipse.setChecked(tool_name == "ellipse_stroke")
        self.btn_ellipse_fill.setChecked(tool_name == "ellipse_fill")

    def on_brush_size_changed(self, size: int):
        self.brush_size = size

    def toggle_tile_grid(self, checked):
        self.show_tile_grid = checked
        self.zoom_canvas.update()

    def toggle_pixel_grid(self, checked):
        self.show_pixel_grid = checked
        self.zoom_canvas.update()

    def on_cbb_changed(self, idx: int):
        self.selected_cbb = idx
        self.tileset_grid.update_dimensions()
        self.select_tile(self.selected_tile_idx)
        self.tileset_grid.update()

    def select_tile(self, tile_idx: int):
        max_t = self.get_current_total_tiles()
        self.selected_tile_idx = max(0, min(max_t - 1, tile_idx))
        
        bpp_mult = 64 if self.is_8bpp else 32
        base_offset = 0 if self.selected_cbb == 6 else (self.selected_cbb * 0x4000)
        addr = 0x06000000 + base_offset + (self.selected_tile_idx * bpp_mult)
        
        if hasattr(self, 'info_label') and self.info_label is not None:
            self.info_label.setText(
                f"Mode: {'8bpp' if self.is_8bpp else '4bpp'} Tileset\n"
                f"Tile: 0x{self.selected_tile_idx:03X} ({self.selected_tile_idx})\n"
                f"Addr: 0x{addr:08X}\n"
                f"Bank: {self.selected_pal_bank}"
            )
        if hasattr(self, 'zoom_canvas') and self.zoom_canvas is not None:
            self.zoom_canvas.sizeHint()
            self.zoom_canvas.update()

    def populate_sprite_table(self):
        words = struct.unpack("<512H", self.oam_copy[:1024]) if len(self.oam_copy) >= 1024 else [0]*512
        for i in range(128):
            a0, a1, a2 = words[i*4], words[i*4+1], words[i*4+2]
            y = a0 & 0xFF
            shape = (a0 >> 14) & 0x3
            x = a1 & 0x1FF
            if x >= 240: x -= 512
            size_idx = (a1 >> 14) & 0x3
            dim = self.SPRITE_SIZES.get(shape, [(8, 8)]*4)[size_idx]
            tile = a2 & 0x3FF
            pal = (a2 >> 12) & 0xF
            h_flip = bool(a1 & (1 << 12))

            self.sprite_table.setItem(i, 0, QTableWidgetItem(f"{i:03d}"))
            self.sprite_table.setItem(i, 1, QTableWidgetItem(str(x)))
            self.sprite_table.setItem(i, 2, QTableWidgetItem(str(y)))
            self.sprite_table.setItem(i, 3, QTableWidgetItem(f"{dim[0]}x{dim[1]}"))
            self.sprite_table.setItem(i, 4, QTableWidgetItem(f"0x{tile:03X}"))
            self.sprite_table.setItem(i, 5, QTableWidgetItem(str(pal)))
            self.sprite_table.setItem(i, 6, QTableWidgetItem(f"{'H' if h_flip else '-'}"))

    def on_sprite_table_selected(self):
        rows = self.sprite_table.selectionModel().selectedRows()
        if not rows: return
        self.selected_sprite_idx = rows[0].row()
        
        words = struct.unpack("<512H", self.oam_copy[:1024])
        a0, a1, a2 = words[self.selected_sprite_idx*4], words[self.selected_sprite_idx*4+1], words[self.selected_sprite_idx*4+2]
        shape = (a0 >> 14) & 0x3
        size_idx = (a1 >> 14) & 0x3
        w, h = self.SPRITE_SIZES.get(shape, [(8, 8)]*4)[size_idx]
        start_tile = a2 & 0x3FF
        pal = (a2 >> 12) & 0xF

        self.selected_cbb = 4
        self.selected_tile_idx = start_tile
        self.selected_pal_bank = pal
        self.bank_spin.blockSignals(True)
        self.bank_spin.setValue(pal)
        self.bank_spin.blockSignals(False)
        self._build_swatches()

        addr = 0x06010000 + (start_tile * 32)
        if hasattr(self, 'info_label') and self.info_label is not None:
            self.info_label.setText(
                f"Mode: Sprite #{self.selected_sprite_idx:03d}\n"
                f"Size: {w}x{h}\n"
                f"Base Tile: 0x{start_tile:03X}\n"
                f"Addr: 0x{addr:08X}\n"
                f"Bank: {pal}"
            )
        if hasattr(self, 'zoom_canvas') and self.zoom_canvas is not None:
            self.zoom_canvas.sizeHint()
            self.zoom_canvas.update()

    def get_active_dimensions(self) -> tuple:
        if self.mode == "sprites":
            words = struct.unpack("<512H", self.oam_copy[:1024])
            a0, a1 = words[self.selected_sprite_idx*4], words[self.selected_sprite_idx*4+1]
            shape = (a0 >> 14) & 0x3
            size_idx = (a1 >> 14) & 0x3
            return self.SPRITE_SIZES.get(shape, [(8, 8)]*4)[size_idx]
        return (8, 8)

    def get_pixel_index(self, px: int, py: int) -> int:
        bpp_mult = 64 if self.is_8bpp else 32
        base_offset = 0 if self.selected_cbb == 6 else (self.selected_cbb * 0x4000)

        if self.mode == "sprites":
            tx = px // 8
            ty = py // 8
            lx = px % 8
            ly = py % 8
            tile_offset_idx = self.selected_tile_idx + (ty * 32) + tx
            offset = 0x10000 + (tile_offset_idx * 32) + (ly * 4) + (lx // 2)
            if offset < len(self.vram_copy):
                b = self.vram_copy[offset]
                return (b & 0xF) if (lx % 2 == 0) else ((b >> 4) & 0xF)
        else:
            if not self.is_8bpp:
                offset = base_offset + (self.selected_tile_idx * 32) + (py * 4) + (px // 2)
                if offset < len(self.vram_copy):
                    b = self.vram_copy[offset]
                    return (b & 0xF) if (px % 2 == 0) else ((b >> 4) & 0xF)
            else:
                offset = base_offset + (self.selected_tile_idx * 64) + (py * 8) + px
                if offset < len(self.vram_copy):
                    return self.vram_copy[offset]
        return 0

    def set_pixel_index(self, px: int, py: int, c_idx: int):
        bpp_mult = 64 if self.is_8bpp else 32
        base_offset = 0 if self.selected_cbb == 6 else (self.selected_cbb * 0x4000)

        if self.mode == "sprites":
            tx = px // 8
            ty = py // 8
            lx = px % 8
            ly = py % 8
            tile_offset_idx = self.selected_tile_idx + (ty * 32) + tx
            offset = 0x10000 + (tile_offset_idx * 32) + (ly * 4) + (lx // 2)
            if offset < len(self.vram_copy):
                old = self.vram_copy[offset]
                new = (old & 0xF0) | (c_idx & 0x0F) if (lx % 2 == 0) else (old & 0x0F) | ((c_idx & 0x0F) << 4)
                self.vram_copy[offset] = new
                self.modified_vram_offsets.add(offset)
                if self.worker: self.worker.write_memory_region("VRAM", offset, bytes([new]))
        else:
            if not self.is_8bpp:
                offset = base_offset + (self.selected_tile_idx * 32) + (py * 4) + (px // 2)
                if offset < len(self.vram_copy):
                    old = self.vram_copy[offset]
                    new = (old & 0xF0) | (c_idx & 0x0F) if (px % 2 == 0) else (old & 0x0F) | ((c_idx & 0x0F) << 4)
                    self.vram_copy[offset] = new
                    self.modified_vram_offsets.add(offset)
                    if self.worker: self.worker.write_memory_region("VRAM", offset, bytes([new]))
            else:
                offset = base_offset + (self.selected_tile_idx * 64) + (py * 8) + px
                if offset < len(self.vram_copy):
                    self.vram_copy[offset] = c_idx & 0xFF
                    self.modified_vram_offsets.add(offset)
                    if self.worker: self.worker.write_memory_region("VRAM", offset, bytes([c_idx & 0xFF]))

    def get_color_for_index(self, c_idx: int) -> tuple:
        if self.is_8bpp:
            return self.palette_colors[c_idx % len(self.palette_colors)]
        is_obj = (self.selected_cbb in [4, 5] or self.mode == "sprites")
        flat_idx = (self.selected_pal_bank * 16 + c_idx)
        if is_obj: flat_idx += 256
        return self.palette_colors[flat_idx % len(self.palette_colors)]

    def on_pal_bank_changed(self, bank: int):
        self.selected_pal_bank = bank
        self._build_swatches()
        if hasattr(self, 'zoom_canvas') and self.zoom_canvas is not None: self.zoom_canvas.update()
        if hasattr(self, 'tileset_grid') and self.tileset_grid is not None: self.tileset_grid.update()

    def _build_swatches(self):
        for i in reversed(range(self.swatch_layout.count())):
            w = self.swatch_layout.itemAt(i).widget()
            if w: w.setParent(None)

        for c_idx in range(16):
            rgb = self.get_color_for_index(c_idx)
            btn = QPushButton()
            btn.setFixedSize(14, 22)
            border = "2px solid #FFF" if c_idx == self.selected_color_idx else "1px solid #333"
            btn.setStyleSheet(f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); border: {border};")
            btn.clicked.connect(lambda _, ci=c_idx: self.select_color_index(ci))
            self.swatch_layout.addWidget(btn)

    def select_color_index(self, idx: int):
        self.selected_color_idx = idx
        self._build_swatches()

    def open_rgb_color_picker(self):
        current_rgb = self.get_color_for_index(self.selected_color_idx)
        chosen = QColorDialog.getColor(QColor(*current_rgb), self, "Edit Active Palette Color")
        if chosen.isValid():
            new_rgb = (int(chosen.red()), int(chosen.green()), int(chosen.blue()))
            is_obj = (self.selected_cbb in [4, 5] or self.mode == "sprites")
            flat_idx = (self.selected_pal_bank * 16 + self.selected_color_idx)
            if is_obj: flat_idx += 256
            self.palette_colors[flat_idx % len(self.palette_colors)] = new_rgb
            
            bgr555 = rgb_to_bgr555(*new_rgb)
            pram_offset = flat_idx * 2

            if self.pram_copy and pram_offset + 2 <= len(self.pram_copy):
                self.pram_copy[pram_offset:pram_offset+2] = bgr555.to_bytes(2, "little")

            if self.worker:
                self.worker.write_memory_region("PRAM", pram_offset, bgr555.to_bytes(2, "little"))

            self._build_swatches()
            self.zoom_canvas.update()
            self.tileset_grid.update()


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

def bgr555_to_rgb(val: int):
    val = int(val)
    r = (val & 0x1F) * 255 // 31
    g = ((val >> 5) & 0x1F) * 255 // 31
    b = ((val >> 10) & 0x1F) * 255 // 31
    return (int(r), int(g), int(b))

def rgb_to_bgr555(r: int, g: int, b: int) -> int:
    r, g, b = int(r), int(g), int(b)
    r5 = (r * 31 // 255) & 0x1F
    g5 = (g * 31 // 255) & 0x1F
    b5 = (b * 31 // 255) & 0x1F
    return int(r5 | (g5 << 5) | (b5 << 10))

def create_ips_patch(original: bytes, modified: bytes) -> bytes:
    patch = bytearray(b"PATCH")
    i = 0
    length = min(len(original), len(modified))

    while i < length:
        if original[i] != modified[i]:
            start = i
            while i < length and (original[i] != modified[i] or (i - start < 65535 and i + 1 < length and original[i+1] != modified[i+1])):
                i += 1
                if i - start >= 65535: break

            chunk_len = i - start
            offset = start
            if offset == 0x454F46: # Offset cannot be 'EOF' in ASCII
                offset -= 1
                chunk_len += 1
                start -= 1

            patch.extend(offset.to_bytes(3, byteorder='big'))
            patch.extend(chunk_len.to_bytes(2, byteorder='big'))
            patch.extend(modified[start:start + chunk_len])
        else:
            i += 1

    patch.extend(b"EOF")
    return bytes(patch)

# ==============================================================================
# GBA BIOS LZ77 (Type 0x10) Decompressor & Compressor
# ==============================================================================
def lz77_decompress(data: bytes, offset: int = 0) -> tuple:
    if offset + 4 > len(data): return None, 0
    if data[offset] != 0x10: return None, 0
    decomp_size = int(data[offset+1]) | (int(data[offset+2]) << 8) | (int(data[offset+3]) << 16)
    if decomp_size == 0 or decomp_size > 0x80000: return None, 0

    out = bytearray()
    pos = offset + 4

    while len(out) < decomp_size and pos < len(data):
        flags = data[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(out) >= decomp_size: break
            if (flags >> bit) & 1:
                if pos + 2 > len(data): break
                b1, b2 = data[pos], data[pos+1]
                pos += 2
                length = (b1 >> 4) + 3
                disp = (((b1 & 0xF) << 8) | b2) + 1
                if disp > len(out): break
                for _ in range(length):
                    if len(out) >= decomp_size: break
                    out.append(out[-disp])
            else:
                if pos >= len(data): break
                out.append(data[pos])
                pos += 1

    if len(out) == decomp_size:
        return bytes(out), pos - offset
    return None, 0

def lz77_compress(data: bytes) -> bytes:
    decomp_size = len(data)
    out = bytearray([0x10, decomp_size & 0xFF, (decomp_size >> 8) & 0xFF, (decomp_size >> 16) & 0xFF])
    pos = 0

    while pos < decomp_size:
        flag_pos = len(out)
        out.append(0)
        flags = 0

        for bit in range(7, -1, -1):
            if pos >= decomp_size: break
            best_len, best_disp = 0, 0
            max_look = min(18, decomp_size - pos)
            window_start = max(0, pos - 4096)

            if max_look >= 3:
                sub = data[pos:pos+3]
                w_pos = data.find(sub, window_start, pos)
                while w_pos != -1:
                    match_len = 3
                    while match_len < max_look and data[w_pos + match_len] == data[pos + match_len]:
                        match_len += 1
                    if match_len > best_len:
                        best_len = match_len
                        best_disp = pos - w_pos
                        if best_len == 18: break
                    w_pos = data.find(sub, w_pos + 1, pos)

            if best_len >= 3:
                flags |= (1 << bit)
                b1 = ((best_len - 3) << 4) | (((best_disp - 1) >> 8) & 0xF)
                b2 = (best_disp - 1) & 0xFF
                out.extend([b1, b2])
                pos += best_len
            else:
                out.append(data[pos])
                pos += 1

        out[flag_pos] = flags

    return bytes(out)


class ZoomedCanvas(QWidget):
    """Enlarged pixel editor for the currently active tile or sprite."""
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setMouseTracking(True)
        self.drag_start_pos = None
        self.temp_draw_pos = None

    def get_zoom(self):
        w, _ = self.editor.get_active_dimensions()
        return max(4, 256 // max(1, w))

    def sizeHint(self):
        w, h = self.editor.get_active_dimensions()
        z = self.get_zoom()
        self.setFixedSize(w * z, h * z)
        return super().sizeHint()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            z = self.get_zoom()
            x = int(event.position().x() // z)
            y = int(event.position().y() // z)
            w, h = self.editor.get_active_dimensions()

            if 0 <= x < w and 0 <= y < h:
                self.drag_start_pos = (x, y)
                t = self.editor.current_tool
                if t in ["pencil", "eraser"]:
                    self.editor.apply_brush(x, y, erase=(t == "eraser"))
                elif t == "eyedropper":
                    self.editor.pick_color(x, y)
                elif t == "bucket":
                    self.editor.apply_flood_fill(x, y)
                self.update()

    def mouseMoveEvent(self, event):
        z = self.get_zoom()
        x = int(event.position().x() // z)
        y = int(event.position().y() // z)
        w, h = self.editor.get_active_dimensions()

        if 0 <= x < w and 0 <= y < h:
            if event.buttons() & Qt.MouseButton.LeftButton:
                t = self.editor.current_tool
                if t in ["pencil", "eraser"]:
                    self.editor.apply_brush(x, y, erase=(t == "eraser"))
                elif t in ["line", "rect_stroke", "rect_fill", "ellipse_stroke", "ellipse_fill"]:
                    self.temp_draw_pos = (x, y)
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_start_pos:
            z = self.get_zoom()
            w, h = self.editor.get_active_dimensions()
            x = max(0, min(w - 1, int(event.position().x() // z)))
            y = max(0, min(h - 1, int(event.position().y() // z)))

            t = self.editor.current_tool
            if t == "line": self.editor.apply_line(self.drag_start_pos[0], self.drag_start_pos[1], x, y)
            elif t == "rect_stroke": self.editor.apply_rect(self.drag_start_pos[0], self.drag_start_pos[1], x, y, fill=False)
            elif t == "rect_fill": self.editor.apply_rect(self.drag_start_pos[0], self.drag_start_pos[1], x, y, fill=True)
            elif t == "ellipse_stroke": self.editor.apply_ellipse(self.drag_start_pos[0], self.drag_start_pos[1], x, y, fill=False)
            elif t == "ellipse_fill": self.editor.apply_ellipse(self.drag_start_pos[0], self.drag_start_pos[1], x, y, fill=True)

            self.drag_start_pos = None
            self.temp_draw_pos = None
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.editor.get_active_dimensions()
        z = self.get_zoom()

        # 1. Checkerboard pattern
        for y in range(0, self.height(), z):
            for x in range(0, self.width(), z):
                col = QColor(30, 30, 30) if ((x // z) + (y // z)) % 2 == 0 else QColor(45, 45, 45)
                painter.fillRect(x, y, z, z, col)

        # 2. Render Pixels
        for py in range(h):
            for px in range(w):
                c_idx = self.editor.get_pixel_index(px, py)
                rgb = self.editor.get_color_for_index(c_idx)
                if c_idx != 0:
                    painter.fillRect(px * z, py * z, z, z, QColor(*rgb))

        # 3. Shape Previews
        if self.drag_start_pos and self.temp_draw_pos:
            painter.setPen(QPen(QColor(255, 255, 0, 220), 2))
            x1, y1 = self.drag_start_pos[0] * z, self.drag_start_pos[1] * z
            x2, y2 = self.temp_draw_pos[0] * z, self.temp_draw_pos[1] * z
            t = self.editor.current_tool

            if t == "line": painter.drawLine(x1, y1, x2, y2)
            elif t in ["rect_stroke", "rect_fill"]:
                rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x2 - x1) + z, abs(y2 - y1) + z
                if t == "rect_fill": painter.fillRect(rx, ry, rw, rh, QColor(255, 255, 0, 100))
                painter.drawRect(rx, ry, rw, rh)
            elif t in ["ellipse_stroke", "ellipse_fill"]:
                rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x2 - x1) + z, abs(y2 - y1) + z
                if t == "ellipse_fill": painter.setBrush(QColor(255, 255, 0, 100))
                painter.drawEllipse(rx, ry, rw, rh)

        # 4. Grids
        if self.editor.show_pixel_grid and z >= 6:
            painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
            for x in range(0, self.width(), z): painter.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), z): painter.drawLine(0, y, self.width(), y)

        if self.editor.show_tile_grid:
            painter.setPen(QPen(QColor(255, 255, 0, 90), 1))
            tz = 8 * z
            for x in range(0, self.width(), tz): painter.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), tz): painter.drawLine(0, y, self.width(), y)

        painter.end()


class TilesetGridWidget(QWidget):
    """YY-CHR Style Pattern Table with full 32-tile width to fit all VRAM glyphs."""
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.tiles_per_row = 32 # 32 tiles per row = 256 pixels wide
        self.zoom = 2
        self.update_dimensions()

    def update_dimensions(self):
        total_tiles = self.editor.get_current_total_tiles()
        rows = max(32, (total_tiles + self.tiles_per_row - 1) // self.tiles_per_row)
        self.setFixedSize(self.tiles_per_row * 8 * self.zoom, rows * 8 * self.zoom)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            tx = int(event.position().x() // (8 * self.zoom))
            ty = int(event.position().y() // (8 * self.zoom))
            if 0 <= tx < self.tiles_per_row:
                tile_idx = ty * self.tiles_per_row + tx
                self.editor.select_tile(tile_idx)
                self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 20))
        img = self.editor.render_tileset_image()
        if img:
            qimg = QImage(img.tobytes(), img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
            scaled = qimg.scaled(self.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
            painter.drawImage(0, 0, scaled)

        st = self.editor.selected_tile_idx
        sx = (st % self.tiles_per_row) * 8 * self.zoom
        sy = (st // self.tiles_per_row) * 8 * self.zoom
        painter.setPen(QPen(QColor(255, 255, 0), 2))
        painter.drawRect(sx, sy, 8 * self.zoom, 8 * self.zoom)
        painter.end()


class GraphicEditorDialog(QDialog):
    SPRITE_SIZES = {
        0: [(8, 8), (16, 16), (32, 32), (64, 64)],
        1: [(16, 8), (32, 8), (32, 16), (64, 32)],
        2: [(8, 16), (8, 32), (16, 32), (32, 64)]
    }

    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("In-Game Graphic Editor (YY-CHR)")
        self.resize(1260, 800)

        if self.worker:
            self.was_paused = self.worker.is_paused
            self.worker.is_paused = True

        # Editor State
        self.mode = "tiles"
        self.current_tool = "pencil"
        self.brush_size = 1
        self.selected_color_idx = 1
        self.selected_pal_bank = 0
        self.selected_cbb = 0 # 0-5, or 6 for entire VRAM
        self.is_8bpp = False
        self.selected_tile_idx = 0
        self.selected_sprite_idx = 0

        self.show_tile_grid = True
        self.show_pixel_grid = True

        self.vram_copy = None
        self.original_vram = None
        self.pram_copy = None
        self.oam_copy = None
        self.palette_colors = []
        self.modified_vram_offsets = set()

        self._load_memory()
        self._init_ui()
        self.select_tile(0)

    def _load_memory(self):
        vram = self.worker.get_memory_region("VRAM") if self.worker else None
        self.vram_copy = bytearray(vram) if vram else bytearray(96 * 1024)
        self.original_vram = bytes(self.vram_copy)

        pram = self.worker.get_memory_region("PRAM") if self.worker else None
        self.pram_copy = bytearray(pram) if pram else bytearray(1024)

        oam = self.worker.get_memory_region("OAM") if self.worker else None
        self.oam_copy = bytearray(oam) if oam else bytearray(1024)

        if not all(b == 0 for b in self.pram_copy[:1024]):
            words = struct.unpack("<512H", self.pram_copy[:1024])
            self.palette_colors = [bgr555_to_rgb(w) for w in words]
        else:
            frame = getattr(self.worker, 'latest_video_frame', None)
            if frame is not None:
                flat = frame.reshape(-1, 3)
                unique = np.unique(flat, axis=0)
                self.palette_colors = [(int(c[0]), int(c[1]), int(c[2])) for c in unique[:512]]
            while len(self.palette_colors) < 512: self.palette_colors.append((0, 0, 0))

    def refresh_from_emulator(self):
        """Pulls the latest live VRAM from the emulator while keeping existing window open."""
        self._load_memory()
        self.tileset_grid.update_dimensions()
        self.tileset_grid.update()
        self.zoom_canvas.update()
        self.populate_sprite_table()
        QMessageBox.information(self, "Refreshed", "VRAM, PRAM, and OAM refreshed from live game state.")

    def _init_ui(self):
        main_layout = QHBoxLayout(self)

        # ======================================================================
        # 1. Left Panel (Drawing Tools & Palette)
        # ======================================================================
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(290)

        tool_box = QGroupBox("Drawing Tools")
        tb_layout = QVBoxLayout(tool_box)

        r1 = QHBoxLayout()
        self.btn_pencil = QPushButton("✏️ Pencil")
        self.btn_pencil.setCheckable(True)
        self.btn_pencil.setChecked(True)
        r1.addWidget(self.btn_pencil)

        self.btn_eraser = QPushButton("🧹 Eraser")
        self.btn_eraser.setCheckable(True)
        r1.addWidget(self.btn_eraser)
        tb_layout.addLayout(r1)

        r2 = QHBoxLayout()
        self.btn_line = QPushButton("📏 Line")
        self.btn_line.setCheckable(True)
        r2.addWidget(self.btn_line)

        self.btn_picker = QPushButton("💧 Picker")
        self.btn_picker.setCheckable(True)
        r2.addWidget(self.btn_picker)
        tb_layout.addLayout(r2)

        r3 = QHBoxLayout()
        self.btn_bucket = QPushButton("🪣 Bucket Fill")
        self.btn_bucket.setCheckable(True)
        r3.addWidget(self.btn_bucket)
        tb_layout.addLayout(r3)

        r4 = QHBoxLayout()
        self.btn_rect = QPushButton("⬜ Rect (Stroke)")
        self.btn_rect.setCheckable(True)
        r4.addWidget(self.btn_rect)

        self.btn_fill = QPushButton("⬛ Rect (Fill)")
        self.btn_fill.setCheckable(True)
        r4.addWidget(self.btn_fill)
        tb_layout.addLayout(r4)

        r5 = QHBoxLayout()
        self.btn_ellipse = QPushButton("⭕ Circle (Stroke)")
        self.btn_ellipse.setCheckable(True)
        r5.addWidget(self.btn_ellipse)

        self.btn_ellipse_fill = QPushButton("🔴 Circle (Fill)")
        self.btn_ellipse_fill.setCheckable(True)
        r5.addWidget(self.btn_ellipse_fill)
        tb_layout.addLayout(r5)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Brush Size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, 16)
        self.size_spin.setValue(1)
        size_row.addWidget(self.size_spin)
        size_row.addWidget(QLabel("px"))
        tb_layout.addLayout(size_row)
        left_layout.addWidget(tool_box)

        # Palette Picker
        pal_box = QGroupBox("Palette & Color Picker")
        pb_layout = QVBoxLayout(pal_box)
        
        bank_row = QHBoxLayout()
        bank_row.addWidget(QLabel("Bank (0-15):"))
        self.bank_spin = QSpinBox()
        self.bank_spin.setRange(0, 15)
        bank_row.addWidget(self.bank_spin)
        pb_layout.addLayout(bank_row)

        self.swatch_grid = QWidget()
        self.swatch_layout = QHBoxLayout(self.swatch_grid)
        self.swatch_layout.setContentsMargins(0, 4, 0, 4)
        pb_layout.addWidget(self.swatch_grid)

        self.btn_color_picker = QPushButton("🎨 Edit Palette Color (Full RGB)...")
        self.btn_color_picker.setStyleSheet("font-weight: bold; background-color: #9C27B0; color: white; padding: 5px;")
        pb_layout.addWidget(self.btn_color_picker)
        left_layout.addWidget(pal_box)

        # Grid Options
        grid_box = QGroupBox("Grid Display")
        gb_layout = QVBoxLayout(grid_box)
        self.cb_tile_grid = QCheckBox("Show 8x8 Tile Grid")
        self.cb_tile_grid.setChecked(True)
        gb_layout.addWidget(self.cb_tile_grid)

        self.cb_pixel_grid = QCheckBox("Show 1x1 Pixel Grid")
        self.cb_pixel_grid.setChecked(True)
        gb_layout.addWidget(self.cb_pixel_grid)
        left_layout.addWidget(grid_box)

        # Save Actions
        export_box = QGroupBox("Save & Patching")
        eb_layout = QVBoxLayout(export_box)
        
        btn_refresh = QPushButton("🔄 Refresh Live VRAM")
        btn_refresh.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; padding: 5px;")
        btn_refresh.clicked.connect(self.refresh_from_emulator)
        eb_layout.addWidget(btn_refresh)

        self.btn_save_rom = QPushButton("Save Modified ROM (.gba)...")
        self.btn_save_rom.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 5px;")
        eb_layout.addWidget(self.btn_save_rom)

        self.btn_save_ips = QPushButton("Export as IPS Patch (.ips)...")
        self.btn_save_ips.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 5px;")
        eb_layout.addWidget(self.btn_save_ips)

        self.btn_vram_dump = QPushButton("Save Raw VRAM Dump (.bin)...")
        eb_layout.addWidget(self.btn_vram_dump)

        self.btn_png_export = QPushButton("Export Current Graphic to PNG...")
        eb_layout.addWidget(self.btn_png_export)
        left_layout.addWidget(export_box)

        left_layout.addStretch()
        main_layout.addWidget(left_panel)

        # ======================================================================
        # 2. Center Panel (Tileset Pattern Table vs Sprites Inspector)
        # ======================================================================
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)

        self.tab_widget = QTabWidget()

        # Tab 1: Tileset Pattern Table
        tab_tiles = QWidget()
        tab_tiles_layout = QVBoxLayout(tab_tiles)
        
        cbb_row = QHBoxLayout()
        cbb_row.addWidget(QLabel("Select Region:"))
        self.cbb_combo = QComboBox()
        self.cbb_combo.addItems([
            "Charblock 0 (0x06000000 - BG)", "Charblock 1 (0x06004000 - BG)",
            "Charblock 2 (0x06008000 - BG)", "Charblock 3 (0x0600C000 - BG)",
            "Charblock 4 (0x06010000 - OBJ)", "Charblock 5 (0x06014000 - OBJ)",
            "Entire VRAM (0x06000000 - 96 KB Continuous)"
        ])
        cbb_row.addWidget(self.cbb_combo)

        self.bpp_checkbox = QCheckBox("8bpp (256 Colors)")
        self.bpp_checkbox.toggled.connect(self.on_bpp_toggled)
        cbb_row.addWidget(self.bpp_checkbox)
        tab_tiles_layout.addLayout(cbb_row)

        scroll_tileset = QScrollArea()
        scroll_tileset.setWidgetResizable(True)
        scroll_tileset.setStyleSheet("background-color: #111;")
        self.tileset_grid = TilesetGridWidget(self)
        scroll_tileset.setWidget(self.tileset_grid)
        tab_tiles_layout.addWidget(scroll_tileset)
        self.tab_widget.addTab(tab_tiles, "🧩 Background & Object Tiles (VRAM)")

        # Tab 2: Sprites / OAM Table
        tab_sprites = QWidget()
        tab_sprites_layout = QVBoxLayout(tab_sprites)
        
        self.sprite_table = QTableWidget(128, 7)
        self.sprite_table.setHorizontalHeaderLabels(["ID", "X", "Y", "Size", "Tile", "Pal", "Flip"])
        self.sprite_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.sprite_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tab_sprites_layout.addWidget(self.sprite_table)
        self.tab_widget.addTab(tab_sprites, "👾 Sprites (OAM Inspector)")

        center_layout.addWidget(self.tab_widget)
        main_layout.addWidget(center_panel)

        # ======================================================================
        # 3. Right Column: Zoomed Pixel Editor
        # ======================================================================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_panel.setFixedWidth(290)

        right_layout.addWidget(QLabel("<b>Zoomed Pixel Editor</b>"))

        scroll_zoom = QScrollArea()
        scroll_zoom.setWidgetResizable(True)
        scroll_zoom.setStyleSheet("background-color: #111;")
        self.zoom_canvas = ZoomedCanvas(self)
        scroll_zoom.setWidget(self.zoom_canvas)
        right_layout.addWidget(scroll_zoom)

        self.info_label = QLabel("Tile: 0x000 (0)\nAddr: 0x06000000")
        self.info_label.setStyleSheet("font-family: monospace; font-size: 13px; background: #222; color: #00FF66; padding: 6px;")
        right_layout.addWidget(self.info_label)

        right_layout.addStretch()
        main_layout.addWidget(right_panel)

        # ======================================================================
        # 4. Populate Data & Connect Signals safely
        # ======================================================================
        self._build_swatches()
        self.populate_sprite_table()

        self.btn_pencil.clicked.connect(lambda: self.set_tool("pencil"))
        self.btn_eraser.clicked.connect(lambda: self.set_tool("eraser"))
        self.btn_line.clicked.connect(lambda: self.set_tool("line"))
        self.btn_picker.clicked.connect(lambda: self.set_tool("eyedropper"))
        self.btn_bucket.clicked.connect(lambda: self.set_tool("bucket"))
        self.btn_rect.clicked.connect(lambda: self.set_tool("rect_stroke"))
        self.btn_fill.clicked.connect(lambda: self.set_tool("rect_fill"))
        self.btn_ellipse.clicked.connect(lambda: self.set_tool("ellipse_stroke"))
        self.btn_ellipse_fill.clicked.connect(lambda: self.set_tool("ellipse_fill"))
        self.size_spin.valueChanged.connect(self.on_brush_size_changed)

        self.bank_spin.valueChanged.connect(self.on_pal_bank_changed)
        self.btn_color_picker.clicked.connect(self.open_rgb_color_picker)
        self.cb_tile_grid.toggled.connect(self.toggle_tile_grid)
        self.cb_pixel_grid.toggled.connect(self.toggle_pixel_grid)

        self.btn_save_rom.clicked.connect(self.save_modified_rom)
        self.btn_save_ips.clicked.connect(self.export_ips_patch)
        self.btn_vram_dump.clicked.connect(self.export_raw_vram_dump)
        self.btn_png_export.clicked.connect(self.export_current_to_png)

        self.cbb_combo.currentIndexChanged.connect(self.on_cbb_changed)
        self.sprite_table.itemSelectionChanged.connect(self.on_sprite_table_selected)
        self.tab_widget.currentChanged.connect(self.on_tab_changed)

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

    def render_tileset_image(self) -> Image.Image:
        bpp_mult = 64 if self.is_8bpp else 32
        base_offset = 0 if self.selected_cbb == 6 else (self.selected_cbb * 0x4000)
        total_tiles = self.get_current_total_tiles()
        
        tiles_per_row = 32
        rows = max(32, (total_tiles + tiles_per_row - 1) // tiles_per_row)

        img = Image.new("RGBA", (tiles_per_row * 8, rows * 8), (20, 20, 20, 255))
        for t in range(total_tiles):
            tx = (t % tiles_per_row) * 8
            ty = (t // tiles_per_row) * 8
            t_offset = base_offset + (t * bpp_mult)
            if t_offset + bpp_mult > len(self.vram_copy): break
            t_data = self.vram_copy[t_offset:t_offset + bpp_mult]

            for py in range(8):
                for px in range(8):
                    if not self.is_8bpp:
                        b = t_data[py * 4 + px // 2]
                        c_idx = (b & 0xF) if (px % 2 == 0) else ((b >> 4) & 0xF)
                    else:
                        c_idx = t_data[py * 8 + px]
                    rgb = self.get_color_for_index(c_idx)
                    img.putpixel((tx + px, ty + py), (*rgb, 255))
        return img

    # ==========================================================================
    # Drawing & Pixel Editing Operations
    # ==========================================================================
    def apply_brush(self, center_x: int, center_y: int, erase: bool = False):
        color_val = 0 if erase else self.selected_color_idx
        r = self.brush_size // 2
        w, h = self.get_active_dimensions()

        for dy in range(-r, self.brush_size - r):
            for dx in range(-r, self.brush_size - r):
                nx, ny = center_x + dx, center_y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    self.set_pixel_index(nx, ny, color_val)

        self.zoom_canvas.update()
        self.tileset_grid.update()

    def pick_color(self, x: int, y: int):
        c_idx = self.get_pixel_index(x, y)
        self.select_color_index(c_idx)
        self.set_tool("pencil")

    def apply_flood_fill(self, start_x: int, start_y: int):
        target_idx = self.get_pixel_index(start_x, start_y)
        rep_idx = self.selected_color_idx
        if target_idx == rep_idx: return

        w, h = self.get_active_dimensions()
        queue = deque([(start_x, start_y)])
        visited = set([(start_x, start_y)])

        while queue:
            cx, cy = queue.popleft()
            if self.get_pixel_index(cx, cy) == target_idx:
                self.set_pixel_index(cx, cy, rep_idx)
                for nx, ny in [(cx+1, cy), (cx-1, cy), (cx, cy+1), (cx, cy-1)]:
                    if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                        visited.add((nx, ny))
                        queue.append((nx, ny))

        self.zoom_canvas.update()
        self.tileset_grid.update()

    def apply_line(self, x0: int, y0: int, x1: int, y1: int):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.apply_brush(x0, y0)
            if x0 == x1 and y0 == y1: break
            e2 = 2 * err
            if e2 > -dy: err -= dy; x0 += sx
            if e2 < dx: err += dx; y0 += sy

    def apply_rect(self, x0: int, y0: int, x1: int, y1: int, fill: bool = False):
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        if fill:
            for y in range(ry, ry + rh + 1):
                for x in range(rx, rx + rw + 1): self.apply_brush(x, y)
        else:
            for x in range(rx, rx + rw + 1):
                self.apply_brush(x, ry)
                self.apply_brush(x, ry + rh)
            for y in range(ry, ry + rh + 1):
                self.apply_brush(rx, y)
                self.apply_brush(rx + rw, y)

    def apply_ellipse(self, x0: int, y0: int, x1: int, y1: int, fill: bool = False):
        rx, ry = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w == 0 or h == 0: return

        cx, cy = rx + w / 2, ry + h / 2
        rad_x, rad_y = w / 2, h / 2

        for y in range(ry, ry + h + 1):
            for x in range(rx, rx + w + 1):
                norm = ((x - cx) / rad_x)**2 + ((y - cy) / rad_y)**2
                if fill and norm <= 1.0:
                    self.apply_brush(x, y)
                elif not fill and abs(norm - 1.0) <= 0.15:
                    self.apply_brush(x, y)

    # ==========================================================================
    # ROM Saving & Advanced Patch Generation
    # ==========================================================================
    def get_patched_rom_bytes(self) -> tuple:
        if not self.worker or not self.worker.rom_path or not os.path.exists(self.worker.rom_path):
            return None, None, 0

        with open(self.worker.rom_path, "rb") as f:
            rom_data = bytearray(f.read())

        orig_rom = bytes(rom_data)
        matches_found = 0
        bpp_mult = 64 if self.is_8bpp else 32
        modified_tile_addresses = {off - (off % bpp_mult) for off in self.modified_vram_offsets}

        for tile_addr in modified_tile_addresses:
            orig_tile = self.original_vram[tile_addr:tile_addr + bpp_mult]
            new_tile = bytes(self.vram_copy[tile_addr:tile_addr + bpp_mult])
            if orig_tile == new_tile: continue

            pos = 0
            while True:
                found = rom_data.find(orig_tile, pos)
                if found == -1: break
                rom_data[found:found + bpp_mult] = new_tile
                matches_found += 1
                pos = found + bpp_mult

        if matches_found == 0 and modified_tile_addresses:
            for offset in range(0, len(rom_data) - 4, 4):
                if rom_data[offset] == 0x10:
                    decomp, comp_len = lz77_decompress(rom_data, offset)
                    if decomp and comp_len > 4:
                        decomp_arr = bytearray(decomp)
                        stream_modified = False

                        for tile_addr in modified_tile_addresses:
                            orig_tile = self.original_vram[tile_addr:tile_addr + bpp_mult]
                            new_tile = bytes(self.vram_copy[tile_addr:tile_addr + bpp_mult])
                            
                            t_pos = decomp_arr.find(orig_tile)
                            if t_pos != -1:
                                decomp_arr[t_pos:t_pos + bpp_mult] = new_tile
                                stream_modified = True
                                matches_found += 1

                        if stream_modified:
                            recomp = lz77_compress(bytes(decomp_arr))
                            if len(recomp) <= comp_len:
                                rom_data[offset:offset + len(recomp)] = recomp
                                rom_data[offset + len(recomp):offset + comp_len] = bytes(comp_len - len(recomp))

        return bytes(rom_data), orig_rom, matches_found

    def save_modified_rom(self):
        result = self.get_patched_rom_bytes()
        if not result or not result[0]:
            QMessageBox.warning(self, "ROM Error", "Could not locate original game ROM on disk.")
            return

        patched_rom, _, matches = result
        if matches == 0:
            QMessageBox.warning(
                self, "Notice", 
                "No matching tile patterns were found in the ROM file.\n"
                "(The graphics may be decompressed dynamically into RAM).\n\n"
                "Tip: Use 'Save Raw VRAM Dump (.bin)' to export your modified graphics directly!"
            )
            return

        default_name = f"patched_{os.path.basename(self.worker.rom_path)}"
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Modified ROM", default_name, "GBA ROMs (*.gba);;All Files (*.*)")
        if filepath:
            with open(filepath, "wb") as f: f.write(patched_rom)
            QMessageBox.information(self, "Success", f"Patched {matches} tile instance(s) into:\n{filepath}")

    def export_ips_patch(self):
        result = self.get_patched_rom_bytes()
        if not result or not result[0]:
            QMessageBox.warning(self, "ROM Error", "Could not locate original ROM file.")
            return

        patched_rom, orig_rom, matches = result
        if matches == 0:
            QMessageBox.warning(self, "Notice", "No matched tile changes to generate an IPS patch from.")
            return

        ips_data = create_ips_patch(orig_rom, patched_rom)
        default_name = f"{os.path.splitext(os.path.basename(self.worker.rom_path))[0]}.ips"
        filepath, _ = QFileDialog.getSaveFileName(self, "Export IPS Patch", default_name, "IPS Patches (*.ips)")
        if filepath:
            with open(filepath, "wb") as f: f.write(ips_data)
            QMessageBox.information(self, "Patch Created", f"Successfully created IPS Patch ({len(ips_data)} bytes) at:\n{filepath}")

    def export_raw_vram_dump(self):
        default_name = f"vram_dump_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.bin"
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Raw VRAM Dump", default_name, "Binary Files (*.bin)")
        if filepath:
            with open(filepath, "wb") as f: f.write(self.vram_copy)
            QMessageBox.information(self, "Saved", f"Exported 96 KB VRAM buffer to:\n{filepath}")

    def export_current_to_png(self):
        w, h = self.get_active_dimensions()
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for py in range(h):
            for px in range(w):
                c_idx = self.get_pixel_index(px, py)
                if c_idx != 0:
                    rgb = self.get_color_for_index(c_idx)
                    img.putpixel((px, py), (*rgb, 255))
        
        default_name = f"export_{self.mode}_{self.selected_tile_idx}.png"
        filepath, _ = QFileDialog.getSaveFileName(self, "Export Graphic to PNG", default_name, "PNG Images (*.png)")
        if filepath:
            img.save(filepath)
            QMessageBox.information(self, "Exported", f"Saved to:\n{filepath}")

    def closeEvent(self, event):
        """Flushes the full modified VRAM buffer to the live core and unpauses."""
        if self.worker and self.vram_copy:
            self.worker.write_memory_region("VRAM", 0, bytes(self.vram_copy))
        if self.worker and hasattr(self, 'was_paused'):
            self.worker.is_paused = self.was_paused
        event.accept()
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

class PaletteViewerDialog(QDialog):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Palettes")
        self.resize(600, 480)
        
        self.colors_bg = []
        self.colors_obj = []
        
        self._init_ui()
        self.refresh_palettes()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_palettes)
        self.export_btn = QPushButton("Export Palettes to PNG")
        self.export_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.export_btn.clicked.connect(self.export_png)
        top_bar.addWidget(self.refresh_btn)
        top_bar.addWidget(self.export_btn)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        self.info_label = QLabel("Click a color swatch for details.")
        self.info_label.setStyleSheet("font-family: monospace; font-size: 13px; padding: 4px; background: #222; color: #EEE;")
        layout.addWidget(self.info_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.palette_container = QWidget()
        self.palette_layout = QVBoxLayout(self.palette_container)
        self.scroll.setWidget(self.palette_container)
        layout.addWidget(self.scroll)

    def refresh_palettes(self):
        pram = self.worker.get_memory_region("PRAM")
        if pram and len(pram) >= 1024:
            words = struct.unpack("<512H", pram[:1024])
            self.colors_bg = [bgr555_to_rgb(w) for w in words[:256]]
            self.colors_obj = [bgr555_to_rgb(w) for w in words[256:]]
        else:
            frame = getattr(self.worker, 'latest_video_frame', None)
            if frame is not None:
                flat = frame.reshape(-1, 3)
                unique_colors = np.unique(flat, axis=0)
                sampled = [tuple(c) for c in unique_colors[:512]]
                while len(sampled) < 512: sampled.append((0, 0, 0))
                self.colors_bg = sampled[:256]
                self.colors_obj = sampled[256:]
            else:
                self.colors_bg = get_default_palette()
                self.colors_obj = get_default_palette()

        for i in reversed(range(self.palette_layout.count())):
            w = self.palette_layout.itemAt(i).widget()
            if w: w.setParent(None)

        self.palette_layout.addWidget(QLabel("<b>Background Palettes (16 Banks × 16 Colors)</b>"))
        self._build_grid(self.colors_bg, "BG")

        self.palette_layout.addWidget(QLabel("<br><b>Sprite (OBJ) Palettes (16 Banks × 16 Colors)</b>"))
        self._build_grid(self.colors_obj, "OBJ")

    def _build_grid(self, color_list, prefix):
        for bank in range(16):
            row_layout = QHBoxLayout()
            lbl = QLabel(f"{prefix} Bank {bank:02d}:")
            lbl.setFixedWidth(90)
            row_layout.addWidget(lbl)
            
            for col in range(16):
                idx = bank * 16 + col
                rgb = color_list[idx]
                btn = QPushButton()
                btn.setFixedSize(22, 22)
                btn.setStyleSheet(f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); border: 1px solid #444;")
                btn.clicked.connect(lambda _, p=prefix, b=bank, c=col, r=rgb: 
                                    self.info_label.setText(f"{p} Bank {b:02d}, Slot {c:02d} | RGB: ({r[0]:3d}, {r[1]:3d}, {r[2]:3d}) | Hex: #{r[0]:02X}{r[1]:02X}{r[2]:02X}"))
                row_layout.addWidget(btn)
            row_layout.addStretch()
            self.palette_layout.addLayout(row_layout)

    def export_png(self):
        if not self.colors_bg: return
        img = Image.new("RGB", (16, 32))
        for y in range(16):
            for x in range(16): img.putpixel((x, y), self.colors_bg[y * 16 + x])
        for y in range(16):
            for x in range(16): img.putpixel((x, 16 + y), self.colors_obj[y * 16 + x])
        scaled = img.resize((256, 512), Image.Resampling.NEAREST)
        export_pil_to_png(self, scaled, f"palettes_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png", self.config)


# ==============================================================================
# 2. VIEW SPRITES (OAM)
# ==============================================================================
class SpriteViewerDialog(QDialog):
    SPRITE_SIZES = {
        0: [(8, 8), (16, 16), (32, 32), (64, 64)], 
        1: [(16, 8), (32, 8), (32, 16), (64, 32)],
        2: [(8, 16), (8, 32), (16, 32), (32, 64)] 
    }

    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Sprites (OAM)")
        self.resize(850, 520)

        self.selected_sprite_index = 0
        self._init_ui()
        self.refresh_sprites()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        
        btn_box = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh OAM")
        self.refresh_btn.clicked.connect(self.refresh_sprites)
        btn_box.addWidget(self.refresh_btn)
        
        self.export_all_btn = QPushButton("Export All to Sheet PNG")
        self.export_all_btn.clicked.connect(self.export_all_sprites_png)
        btn_box.addWidget(self.export_all_btn)
        left_layout.addLayout(btn_box)

        self.table = QTableWidget(128, 8)
        self.table.setHorizontalHeaderLabels(["ID", "X", "Y", "Size", "Tile", "Pal", "Flip", "Pri"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.on_sprite_selected)
        left_layout.addWidget(self.table)
        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        
        right_layout.addWidget(QLabel("<b>Selected Sprite Preview</b>"))
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(256, 256)
        self.preview_label.setStyleSheet("background-color: #1A1A1A; border: 1px solid #444;")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self.preview_label)

        self.details_label = QLabel("Select a sprite to inspect attributes.")
        self.details_label.setStyleSheet("font-family: monospace; font-size: 12px;")
        right_layout.addWidget(self.details_label)

        self.export_btn = QPushButton("Export Selected Sprite to PNG")
        self.export_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.export_btn.clicked.connect(self.export_selected_png)
        right_layout.addWidget(self.export_btn)
        right_layout.addStretch()
        splitter.addWidget(right_widget)

    def refresh_sprites(self):
        oam = self.worker.get_memory_region("OAM")
        words = struct.unpack("<512H", oam[:1024]) if (oam and len(oam) >= 1024) else [0]*512

        for i in range(128):
            a0, a1, a2 = words[i*4], words[i*4+1], words[i*4+2]
            y = a0 & 0xFF
            shape = (a0 >> 14) & 0x3
            x = a1 & 0x1FF
            if x >= 240: x -= 512
            size_idx = (a1 >> 14) & 0x3
            dim = self.SPRITE_SIZES.get(shape, [(8, 8)]*4)[size_idx]
            tile = a2 & 0x3FF if oam else i * 4
            pal = (a2 >> 12) & 0xF
            h_flip = bool(a1 & (1 << 12))
            v_flip = bool(a1 & (1 << 13))
            pri = (a2 >> 10) & 0x3

            self.table.setItem(i, 0, QTableWidgetItem(f"{i:03d}"))
            self.table.setItem(i, 1, QTableWidgetItem(str(x)))
            self.table.setItem(i, 2, QTableWidgetItem(str(y)))
            self.table.setItem(i, 3, QTableWidgetItem(f"{dim[0]}x{dim[1]}"))
            self.table.setItem(i, 4, QTableWidgetItem(f"0x{tile:03X}"))
            self.table.setItem(i, 5, QTableWidgetItem(str(pal)))
            self.table.setItem(i, 6, QTableWidgetItem(f"{'H' if h_flip else '-'}{'V' if v_flip else '-'}"))
            self.table.setItem(i, 7, QTableWidgetItem(str(pri)))

        self.table.selectRow(0)

    def render_sprite_image(self, sprite_idx: int) -> Image.Image:
        vram = self.worker.get_memory_region("VRAM")
        if not vram: return None

        oam = self.worker.get_memory_region("OAM")
        words = struct.unpack("<512H", oam[:1024]) if (oam and len(oam) >= 1024) else None
        
        if words:
            a0, a1, a2 = words[sprite_idx*4], words[sprite_idx*4+1], words[sprite_idx*4+2]
            shape = (a0 >> 14) & 0x3
            size_idx = (a1 >> 14) & 0x3
            w, h = self.SPRITE_SIZES.get(shape, [(8, 8)]*4)[size_idx]
            start_tile = a2 & 0x3FF
            h_flip, v_flip = bool(a1 & (1 << 12)), bool(a1 & (1 << 13))
        else:
            w, h = (16, 16)
            start_tile = sprite_idx * 4
            h_flip, v_flip = False, False

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        tiles_x, tiles_y = w // 8, h // 8
        obj_vram_start = 0x10000

        for ty in range(tiles_y):
            for tx in range(tiles_x):
                t_idx = start_tile + (ty * 32) + tx
                offset = obj_vram_start + (t_idx * 32)
                if offset + 32 > len(vram): continue
                tile_bytes = vram[offset:offset + 32]

                for py in range(8):
                    for px in range(8):
                        b = tile_bytes[py * 4 + px // 2]
                        c_idx = (b & 0xF) if (px % 2 == 0) else ((b >> 4) & 0xF)
                        if c_idx != 0:
                            lum = c_idx * 17
                            img.putpixel((tx * 8 + px, ty * 8 + py), (lum, lum, lum, 255))

        if h_flip: img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if v_flip: img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return img

    def on_sprite_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows: return
        self.selected_sprite_index = rows[0].row()

        img = self.render_sprite_image(self.selected_sprite_index)
        if img:
            qimg = QImage(img.tobytes(), img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
            pix = QPixmap.fromImage(qimg).scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
            self.preview_label.setPixmap(pix)
            self.details_label.setText(f"Sprite #{self.selected_sprite_index:03d}\nTile: 0x{self.selected_sprite_index*4:03X}")

    def export_selected_png(self):
        img = self.render_sprite_image(self.selected_sprite_index)
        if img: export_pil_to_png(self, img, f"sprite_{self.selected_sprite_index:03d}.png", self.config)

    def export_all_sprites_png(self):
        sheet = Image.new("RGBA", (16 * 64, 8 * 64), (0, 0, 0, 0))
        for i in range(128):
            sp = self.render_sprite_image(i)
            if sp: sheet.paste(sp, ((i % 16) * 64, (i // 16) * 64))
        export_pil_to_png(self, sheet, f"sprite_sheet_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png", self.config)


# ==============================================================================
# 3. VIEW TILES
# ==============================================================================

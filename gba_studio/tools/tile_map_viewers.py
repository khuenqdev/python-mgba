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

class TileViewerDialog(QDialog):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Tiles")
        self.resize(580, 640)

        self.current_img = None
        self._init_ui()
        self.render_tiles()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Charblock:"))
        self.block_combo = QComboBox()
        self.block_combo.addItems([
            "0 (0x06000000 - BG)", "1 (0x06004000 - BG)", 
            "2 (0x06008000 - BG)", "3 (0x0600C000 - BG)",
            "4 (0x06010000 - OBJ)", "5 (0x06014000 - OBJ)"
        ])
        self.block_combo.currentIndexChanged.connect(self.render_tiles)
        controls.addWidget(self.block_combo)

        controls.addWidget(QLabel("Palette Bank:"))
        self.pal_spin = QSpinBox()
        self.pal_spin.setRange(0, 15)
        self.pal_spin.valueChanged.connect(self.render_tiles)
        controls.addWidget(self.pal_spin)

        self.bpp_check = QCheckBox("8bpp")
        self.bpp_check.toggled.connect(self.render_tiles)
        controls.addWidget(self.bpp_check)
        layout.addLayout(controls)

        btn_bar = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.render_tiles)
        self.export_btn = QPushButton("Export Tileset to PNG")
        self.export_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.export_btn.clicked.connect(self.export_png)
        btn_bar.addWidget(self.refresh_btn)
        btn_bar.addWidget(self.export_btn)
        layout.addLayout(btn_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.img_label)
        layout.addWidget(self.scroll)

    def render_tiles(self):
        vram = self.worker.get_memory_region("VRAM")
        if not vram:
            self.img_label.setText("VRAM not available. Please ensure game is loaded.")
            return

        block_idx = self.block_combo.currentIndex()
        pal_bank = self.pal_spin.value()
        is_8bpp = self.bpp_check.isChecked()

        colors = get_default_palette()
        block_offset = block_idx * 0x4000
        block_bytes = vram[block_offset:block_offset + 0x4000]

        tile_size = 64 if is_8bpp else 32
        total_tiles = len(block_bytes) // tile_size
        tiles_per_row = 16
        rows = (total_tiles + tiles_per_row - 1) // tiles_per_row

        img = Image.new("RGBA", (tiles_per_row * 8, rows * 8), (0, 0, 0, 255))
        for t in range(total_tiles):
            tx = (t % tiles_per_row) * 8
            ty = (t // tiles_per_row) * 8
            t_data = block_bytes[t * tile_size:(t + 1) * tile_size]

            for py in range(8):
                for px in range(8):
                    if not is_8bpp:
                        b = t_data[py * 4 + px // 2]
                        c_idx = (b & 0xF) if (px % 2 == 0) else ((b >> 4) & 0xF)
                        rgb = colors[pal_bank * 16 + c_idx]
                    else:
                        c_idx = t_data[py * 8 + px]
                        rgb = colors[c_idx]
                    img.putpixel((tx + px, ty + py), (rgb[0], rgb[1], rgb[2], 255))

        self.current_img = img
        scaled = img.resize((img.width * 2, img.height * 2), Image.Resampling.NEAREST)
        qimg = QImage(scaled.tobytes(), scaled.width, scaled.height, scaled.width * 4, QImage.Format.Format_RGBA8888)
        self.img_label.setPixmap(QPixmap.fromImage(qimg))

    def export_png(self):
        if self.current_img:
            export_pil_to_png(self, self.current_img, f"tileset_block{self.block_combo.currentIndex()}.png", self.config)


# ==============================================================================
# 4. VIEW MAP (TILEMAP)
# ==============================================================================
class MapViewerDialog(QDialog):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Map (Tilemap)")
        self.resize(600, 640)

        self.current_map_img = None
        self._init_ui()
        self.render_map()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Screen Base Block (0-31):"))
        self.sbb_spin = QSpinBox()
        self.sbb_spin.setRange(0, 31)
        self.sbb_spin.setValue(0)
        self.sbb_spin.valueChanged.connect(self.render_map)
        top_bar.addWidget(self.sbb_spin)

        top_bar.addWidget(QLabel("Charblock (0-3):"))
        self.cbb_spin = QSpinBox()
        self.cbb_spin.setRange(0, 3)
        self.cbb_spin.setValue(0)
        self.cbb_spin.valueChanged.connect(self.render_map)
        top_bar.addWidget(self.cbb_spin)
        layout.addLayout(top_bar)

        btn_bar = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh Map")
        self.refresh_btn.clicked.connect(self.render_map)
        self.export_btn = QPushButton("Export Map to PNG")
        self.export_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.export_btn.clicked.connect(self.export_png)
        btn_bar.addWidget(self.refresh_btn)
        btn_bar.addWidget(self.export_btn)
        layout.addLayout(btn_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.map_label = QLabel()
        self.map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.map_label)
        layout.addWidget(self.scroll)

    def render_map(self):
        vram = self.worker.get_memory_region("VRAM")
        if not vram:
            self.map_label.setText("VRAM not available. Ensure game is running.")
            return

        sbb = self.sbb_spin.value()
        cbb = self.cbb_spin.value()
        bg_colors = get_default_palette()

        map_offset = sbb * 0x800
        tile_offset_base = cbb * 0x4000

        map_entries = struct.unpack("<1024H", vram[map_offset:map_offset + 0x800])
        img = Image.new("RGB", (256, 256), (0, 0, 0))

        for ty in range(32):
            for tx in range(32):
                entry = map_entries[ty * 32 + tx]
                tile_idx = entry & 0x3FF
                h_flip = bool(entry & (1 << 10))
                v_flip = bool(entry & (1 << 11))
                pal_bank = (entry >> 12) & 0xF

                t_addr = tile_offset_base + (tile_idx * 32)
                if t_addr + 32 > len(vram): continue
                tile_bytes = vram[t_addr:t_addr + 32]

                for py in range(8):
                    for px in range(8):
                        actual_px = (7 - px) if h_flip else px
                        actual_py = (7 - py) if v_flip else py
                        
                        b = tile_bytes[actual_py * 4 + actual_px // 2]
                        c_idx = (b & 0xF) if (actual_px % 2 == 0) else ((b >> 4) & 0xF)
                        rgb = bg_colors[pal_bank * 16 + c_idx]
                        img.putpixel((tx * 8 + px, ty * 8 + py), rgb)

        self.current_map_img = img
        qimg = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
        self.map_label.setPixmap(QPixmap.fromImage(qimg).scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio))

    def export_png(self):
        if self.current_map_img:
            export_pil_to_png(self, self.current_map_img, f"map_sbb{self.sbb_spin.value()}.png", self.config)


# ==============================================================================
# 5. FRAME INSPECTOR
# ==============================================================================

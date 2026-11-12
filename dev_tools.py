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

from hex_editor import HexEditorDialog

# Alias to ensure backwards-compatibility with all existing views & tools
MemoryViewerDialog = HexEditorDialog


def bgr555_to_rgb(val: int):
    val = int(val)
    r = (val & 0x1F) * 255 // 31
    g = ((val >> 5) & 0x1F) * 255 // 31
    b = ((val >> 10) & 0x1F) * 255 // 31
    return (int(r), int(g), int(b))


def export_pil_to_png(parent, pil_img: Image.Image, default_filename: str, config):
    export_dir = config.get("paths", "exports") or "exports"
    os.makedirs(export_dir, exist_ok=True)
    initial_path = os.path.join(export_dir, default_filename)
    
    filepath, _ = QFileDialog.getSaveFileName(
        parent, "Export to PNG", initial_path, "PNG Images (*.png)"
    )
    if filepath:
        pil_img.save(filepath, "PNG")
        QMessageBox.information(parent, "Export Successful", f"Saved to:\n{filepath}")


def get_default_palette():
    """Generates a standard 256-color GBA palette fallback if PRAM is unmapped."""
    colors = []
    for i in range(256):
        r = (i * 7) % 256
        g = (i * 13) % 256
        b = (i * 19) % 256
        colors.append((r, g, b))
    colors[0] = (0, 0, 0)
    return colors


# ==============================================================================
# 1. VIEW PALETTES
# ==============================================================================
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
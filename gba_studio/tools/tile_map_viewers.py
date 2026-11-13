# tile_map_viewers.py
import struct
import numpy as np
from PIL import Image

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QScrollArea, QCheckBox
)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt, QTimer

from .dev_common import bgr555_to_rgb, export_pil_to_png, get_default_palette


def _extract_pram_palettes(worker):
    """Returns (bg_colors[256], obj_colors[256], is_live_pram)."""
    pram = worker.get_memory_region("PRAM")
    if pram and len(pram) >= 1024 and not all(b == 0 for b in pram[:1024]):
        words = struct.unpack("<512H", pram[:1024])
        bg_colors = [bgr555_to_rgb(w) for w in words[:256]]
        obj_colors = [bgr555_to_rgb(w) for w in words[256:]]
        return bg_colors, obj_colors, True
    fallback = get_default_palette()
    return fallback, fallback, False


def _decode_tiles_4bpp(raw_bytes: bytes, max_tiles: int) -> np.ndarray:
    count = min(max_tiles, len(raw_bytes) // 32)
    if count <= 0:
        return np.zeros((0, 8, 8), dtype=np.uint8)
    arr = np.frombuffer(raw_bytes[:count * 32], dtype=np.uint8).reshape(count, 8, 4)
    tiles = np.empty((count, 8, 8), dtype=np.uint8)
    tiles[:, :, 0::2] = arr & 0x0F
    tiles[:, :, 1::2] = (arr >> 4) & 0x0F
    return tiles


def _decode_tiles_8bpp(raw_bytes: bytes, max_tiles: int) -> np.ndarray:
    count = min(max_tiles, len(raw_bytes) // 64)
    if count <= 0:
        return np.zeros((0, 8, 8), dtype=np.uint8)
    return np.frombuffer(raw_bytes[:count * 64], dtype=np.uint8).reshape(count, 8, 8)


# ==============================================================================
# 3. VIEW TILES
# ==============================================================================
class TileViewerDialog(QDialog):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Tiles")
        self.resize(620, 680)

        self.current_img = None
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(100)  # 10 Hz
        self.refresh_timer.timeout.connect(self.render_tiles)

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
        btn_bar.addWidget(self.refresh_btn)

        self.auto_refresh_cb = QCheckBox("⚡ Auto-Refresh (10 Hz)")
        self.auto_refresh_cb.toggled.connect(self.toggle_auto_refresh)
        btn_bar.addWidget(self.auto_refresh_cb)

        self.pal_status_lbl = QLabel("")
        self.pal_status_lbl.setStyleSheet("color: #81C784; font-size: 11px;")
        btn_bar.addWidget(self.pal_status_lbl)

        btn_bar.addStretch()

        self.export_btn = QPushButton("Export Tileset to PNG")
        self.export_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.export_btn.clicked.connect(self.export_png)
        btn_bar.addWidget(self.export_btn)
        layout.addLayout(btn_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.img_label)
        layout.addWidget(self.scroll)

    def toggle_auto_refresh(self, enabled: bool):
        if enabled:
            self.refresh_timer.start()
        else:
            self.refresh_timer.stop()

    def render_tiles(self):
        vram = self.worker.get_memory_region("VRAM")
        if not vram:
            self.img_label.setText("VRAM not available. Please ensure game is loaded.")
            return

        block_idx = self.block_combo.currentIndex()
        pal_bank = self.pal_spin.value()
        is_8bpp = self.bpp_check.isChecked()
        is_obj = (block_idx >= 4)

        bg_colors, obj_colors, is_live_pram = _extract_pram_palettes(self.worker)
        pal_source = obj_colors if is_obj else bg_colors

        if is_live_pram:
            self.pal_status_lbl.setText(f"Palette: Live PRAM ({'OBJ' if is_obj else 'BG'})")
            self.pal_status_lbl.setStyleSheet("color: #81C784; font-size: 11px;")
        else:
            self.pal_status_lbl.setText("Palette: Fallback")
            self.pal_status_lbl.setStyleSheet("color: #FFB74D; font-size: 11px;")

        block_offset = block_idx * 0x4000
        block_bytes = vram[block_offset:block_offset + 0x4000]

        tile_size = 64 if is_8bpp else 32
        total_tiles = len(block_bytes) // tile_size
        if total_tiles <= 0:
            return

        tiles_per_row = 16
        rows = (total_tiles + tiles_per_row - 1) // tiles_per_row

        if not is_8bpp:
            tiles = _decode_tiles_4bpp(block_bytes, total_tiles)
            bank_colors = list(pal_source[pal_bank * 16:pal_bank * 16 + 16])
            if is_live_pram and not is_obj:
                bank_colors[0] = bg_colors[0]
            pal_arr = np.array(bank_colors, dtype=np.uint8)
        else:
            tiles = _decode_tiles_8bpp(block_bytes, total_tiles)
            pal_arr = np.array(pal_source[:256], dtype=np.uint8)

        grid_tiles = np.zeros((rows * tiles_per_row, 8, 8), dtype=np.uint8)
        grid_tiles[:len(tiles)] = tiles
        idx_grid = grid_tiles.reshape(rows, tiles_per_row, 8, 8).transpose(0, 2, 1, 3).reshape(rows * 8, tiles_per_row * 8)

        rgb = pal_arr[idx_grid]
        rgba = np.empty((rows * 8, tiles_per_row * 8, 4), dtype=np.uint8)
        rgba[..., :3] = rgb
        rgba[..., 3] = 255

        self.current_img = Image.fromarray(rgba, "RGBA")
        qimg = QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.shape[1] * 4, QImage.Format.Format_RGBA8888)
        pix = QPixmap.fromImage(qimg).scaled(
            rgba.shape[1] * 2, rgba.shape[0] * 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation
        )
        self.img_label.setPixmap(pix)

    def export_png(self):
        if self.current_img:
            export_pil_to_png(self, self.current_img, f"tileset_block{self.block_combo.currentIndex()}.png", self.config)

    def closeEvent(self, event):
        self.refresh_timer.stop()
        event.accept()


# ==============================================================================
# 4. VIEW MAP (TILEMAP)
# ==============================================================================
class MapViewerDialog(QDialog):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Map (Tilemap)")
        self.resize(620, 660)

        self.current_map_img = None
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(100)  # 10 Hz
        self.refresh_timer.timeout.connect(self.render_map)

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

        self.bpp_check = QCheckBox("8bpp")
        self.bpp_check.toggled.connect(self.render_map)
        top_bar.addWidget(self.bpp_check)
        layout.addLayout(top_bar)

        btn_bar = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh Map")
        self.refresh_btn.clicked.connect(self.render_map)
        btn_bar.addWidget(self.refresh_btn)

        self.auto_refresh_cb = QCheckBox("⚡ Auto-Refresh (10 Hz)")
        self.auto_refresh_cb.toggled.connect(self.toggle_auto_refresh)
        btn_bar.addWidget(self.auto_refresh_cb)

        self.pal_status_lbl = QLabel("")
        self.pal_status_lbl.setStyleSheet("color: #81C784; font-size: 11px;")
        btn_bar.addWidget(self.pal_status_lbl)

        btn_bar.addStretch()

        self.export_btn = QPushButton("Export Map to PNG")
        self.export_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.export_btn.clicked.connect(self.export_png)
        btn_bar.addWidget(self.export_btn)
        layout.addLayout(btn_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.map_label = QLabel()
        self.map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.map_label)
        layout.addWidget(self.scroll)

    def toggle_auto_refresh(self, enabled: bool):
        if enabled:
            self.refresh_timer.start()
        else:
            self.refresh_timer.stop()

    def render_map(self):
        vram = self.worker.get_memory_region("VRAM")
        if not vram:
            self.map_label.setText("VRAM not available. Ensure game is running.")
            return

        sbb = self.sbb_spin.value()
        cbb = self.cbb_spin.value()
        is_8bpp = self.bpp_check.isChecked()

        bg_colors, _, is_live_pram = _extract_pram_palettes(self.worker)
        if is_live_pram:
            self.pal_status_lbl.setText("Palette: Live PRAM")
            self.pal_status_lbl.setStyleSheet("color: #81C784; font-size: 11px;")
        else:
            self.pal_status_lbl.setText("Palette: Fallback")
            self.pal_status_lbl.setStyleSheet("color: #FFB74D; font-size: 11px;")

        map_offset = sbb * 0x800
        tile_offset_base = cbb * 0x4000
        if map_offset + 0x800 > len(vram) or tile_offset_base >= len(vram):
            return

        map_entries = struct.unpack("<1024H", vram[map_offset:map_offset + 0x800])
        cbb_bytes = vram[tile_offset_base:]

        if not is_8bpp:
            all_tiles = _decode_tiles_4bpp(cbb_bytes, 1024)
            pal_arr = np.array(bg_colors[:256], dtype=np.uint8)
            if is_live_pram:
                pal_arr[0::16] = pal_arr[0]  # Color 0 of every BG bank shows backdrop PRAM[0]
        else:
            all_tiles = _decode_tiles_8bpp(cbb_bytes, 1024)
            pal_arr = np.array(bg_colors[:256], dtype=np.uint8)

        num_tiles = len(all_tiles)
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        rgba[..., :3] = pal_arr[0]
        rgba[..., 3] = 255

        for ty in range(32):
            for tx in range(32):
                entry = map_entries[ty * 32 + tx]
                tile_idx = entry & 0x3FF
                if tile_idx >= num_tiles:
                    continue

                tile = all_tiles[tile_idx]
                if entry & (1 << 10):
                    tile = tile[:, ::-1]
                if entry & (1 << 11):
                    tile = tile[::-1, :]

                if not is_8bpp:
                    pal_bank = (entry >> 12) & 0xF
                    rgba[ty * 8:(ty + 1) * 8, tx * 8:(tx + 1) * 8, :3] = pal_arr[pal_bank * 16 + tile]
                else:
                    rgba[ty * 8:(ty + 1) * 8, tx * 8:(tx + 1) * 8, :3] = pal_arr[tile]

        self.current_map_img = Image.fromarray(rgba, "RGBA")
        qimg = QImage(rgba.data, 256, 256, 256 * 4, QImage.Format.Format_RGBA8888)
        self.map_label.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                512, 512,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation
            )
        )

    def export_png(self):
        if self.current_map_img:
            export_pil_to_png(self, self.current_map_img, f"map_sbb{self.sbb_spin.value()}.png", self.config)

    def closeEvent(self, event):
        self.refresh_timer.stop()
        event.accept()
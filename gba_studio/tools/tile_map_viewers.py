# tile_map_viewers.py
import os
import struct
from collections import deque
import numpy as np
from PIL import Image

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QScrollArea, QCheckBox, QSplitter, QGroupBox,
    QFileDialog, QMessageBox
)
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QKeySequence, QShortcut
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from .dev_common import bgr555_to_rgb, export_pil_to_png, get_default_palette
from .graphics.patches import create_ips_patch, lz77_decompress, lz77_compress


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
# 4. INTERACTIVE GBA TILEMAP (SBB) EDITOR (AdvanceMap Lite)
# ==============================================================================
class TileBrushPaletteWidget(QWidget):
    """Clickable CBB pattern table for selecting the tile brush in MapViewerDialog."""

    tile_selected = pyqtSignal(int)

    def __init__(self, dialog, parent=None):
        super().__init__(parent)
        self.dlg = dialog
        self.tiles_per_row = 16
        self.zoom = 2
        self._qimg = None
        self.setFixedSize(self.tiles_per_row * 8 * self.zoom, 32 * 8 * self.zoom)

    def rebuild_image(self, all_tiles: np.ndarray, pal_arr: np.ndarray, is_8bpp: bool, pal_bank: int):
        total = max(1, len(all_tiles))
        rows = max(16, (total + self.tiles_per_row - 1) // self.tiles_per_row)
        grid = np.zeros((rows * self.tiles_per_row, 8, 8), dtype=np.uint8)
        grid[:len(all_tiles)] = all_tiles
        idx_grid = grid.reshape(rows, self.tiles_per_row, 8, 8).transpose(0, 2, 1, 3).reshape(rows * 8, self.tiles_per_row * 8)

        if not is_8bpp:
            bank = pal_arr[pal_bank * 16:pal_bank * 16 + 16]
            rgb = bank[idx_grid]
        else:
            rgb = pal_arr[idx_grid]

        rgba = np.empty((rows * 8, self.tiles_per_row * 8, 4), dtype=np.uint8)
        rgba[..., :3] = rgb
        rgba[..., 3] = 255
        self._rgba_buf = rgba
        self._qimg = QImage(
            self._rgba_buf.data, rgba.shape[1], rgba.shape[0],
            rgba.shape[1] * 4, QImage.Format.Format_RGBA8888
        )
        self.setFixedSize(self.tiles_per_row * 8 * self.zoom, rows * 8 * self.zoom)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            tx = int(event.position().x() // (8 * self.zoom))
            ty = int(event.position().y() // (8 * self.zoom))
            if 0 <= tx < self.tiles_per_row:
                tile_idx = min(1023, ty * self.tiles_per_row + tx)
                self.tile_selected.emit(tile_idx)
                self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(18, 20, 26))
        if self._qimg is not None:
            scaled = self._qimg.scaled(
                self.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation
            )
            painter.drawImage(0, 0, scaled)

        st = self.dlg.brush_tile_idx
        sx = (st % self.tiles_per_row) * 8 * self.zoom
        sy = (st // self.tiles_per_row) * 8 * self.zoom
        painter.setPen(QPen(QColor(255, 235, 59), 2))
        painter.drawRect(sx, sy, 8 * self.zoom, 8 * self.zoom)
        painter.end()


class InteractiveMapCanvas(QWidget):
    """32x32 Screen Base Block interactive tilemap canvas supporting Stamp, Eyedropper & Fill."""

    hovered_cell = pyqtSignal(int, int)

    def __init__(self, dialog, parent=None):
        super().__init__(parent)
        self.dlg = dialog
        self.zoom = 2
        self._qimg = None
        self._rgba_buf = None
        self._hover_cell = None
        self._painting = False
        self.setMouseTracking(True)
        self.setFixedSize(256 * self.zoom, 256 * self.zoom)

    def update_bitmap(self, rgba: np.ndarray):
        self._rgba_buf = np.ascontiguousarray(rgba, dtype=np.uint8)
        self._qimg = QImage(
            self._rgba_buf.data, 256, 256, 256 * 4, QImage.Format.Format_RGBA8888
        )
        self.update()

    def _cell_from_pos(self, pos) -> tuple[int, int] | None:
        tx = int(pos.x() // (8 * self.zoom))
        ty = int(pos.y() // (8 * self.zoom))
        if 0 <= tx < 32 and 0 <= ty < 32:
            return tx, ty
        return None

    def mousePressEvent(self, event):
        cell = self._cell_from_pos(event.position())
        if cell is None:
            return
        tx, ty = cell

        if event.button() == Qt.MouseButton.RightButton:
            self.dlg.pick_tile_at(tx, ty)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            tool = self.dlg.current_tool
            if tool == "eyedropper":
                self.dlg.pick_tile_at(tx, ty)
            elif tool == "fill":
                self.dlg.flood_fill_tile(tx, ty)
            else:
                self.dlg.begin_edit()
                self._painting = True
                self.dlg.stamp_tile_at(tx, ty)

    def mouseMoveEvent(self, event):
        cell = self._cell_from_pos(event.position())
        self._hover_cell = cell
        if cell is not None:
            self.hovered_cell.emit(cell[0], cell[1])
            if self._painting and (event.buttons() & Qt.MouseButton.LeftButton):
                self.dlg.stamp_tile_at(cell[0], cell[1])
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._painting:
            self._painting = False
            self.dlg.commit_edit()

    def leaveEvent(self, event):
        self._hover_cell = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(12, 14, 18))
        if self._qimg is not None:
            scaled = self._qimg.scaled(
                self.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation
            )
            painter.drawImage(0, 0, scaled)

        step = 8 * self.zoom
        if self.dlg.show_grid_cb.isChecked():
            painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
            for i in range(1, 32):
                painter.drawLine(i * step, 0, i * step, self.height())
                painter.drawLine(0, i * step, self.width(), i * step)

            # Highlight standard 240x160 GBA viewport boundary (30x20 tiles)
            painter.setPen(QPen(QColor(0, 230, 118, 120), 1, Qt.PenStyle.DashLine))
            painter.drawRect(0, 0, 30 * step, 20 * step)

        if self._hover_cell is not None:
            hx, hy = self._hover_cell
            painter.setPen(QPen(QColor(255, 235, 59), 2))
            painter.drawRect(hx * step, hy * step, step, step)

        painter.end()


class MapViewerDialog(QDialog):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Game State Views - Interactive Tilemap (SBB) Editor")
        self.resize(1020, 680)

        self.current_map_img = None
        self.current_tool = "stamp"
        self.brush_tile_idx = 0
        self.brush_pal_bank = 0
        self.brush_h_flip = False
        self.brush_v_flip = False

        # Local SBB snapshot & undo history for editing + ROM/IPS export
        self.original_sbb_bytes = None
        self.sbb_entries = [0] * 1024
        self._last_loaded_sbb = -1
        self.undo_stack = []
        self._active_stroke = {}
        self._stroke_depth = 0

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(100)  # 10 Hz
        self.refresh_timer.timeout.connect(self.render_map)

        self._init_ui()
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        self.reload_sbb_from_vram()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Row 1: Memory Region & Refresh Controls
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Screen Base Block (0-31):"))
        self.sbb_spin = QSpinBox()
        self.sbb_spin.setRange(0, 31)
        self.sbb_spin.setValue(0)
        self.sbb_spin.valueChanged.connect(self.reload_sbb_from_vram)
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

        self.show_grid_cb = QCheckBox("Tile Grid")
        self.show_grid_cb.setChecked(True)
        self.show_grid_cb.toggled.connect(lambda _: self.map_canvas.update())
        top_bar.addWidget(self.show_grid_cb)

        self.refresh_btn = QPushButton("🔄 Pull Live VRAM")
        self.refresh_btn.clicked.connect(self.reload_sbb_from_vram)
        top_bar.addWidget(self.refresh_btn)

        self.auto_refresh_cb = QCheckBox("⚡ Auto-Refresh (10 Hz)")
        self.auto_refresh_cb.toggled.connect(self.toggle_auto_refresh)
        top_bar.addWidget(self.auto_refresh_cb)

        self.pal_status_lbl = QLabel("")
        self.pal_status_lbl.setStyleSheet("color: #81C784; font-size: 11px;")
        top_bar.addWidget(self.pal_status_lbl)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # Row 2: Brush & Export Toolbar
        tool_bar = QHBoxLayout()

        self.btn_stamp = QPushButton("🖌️ Stamp")
        self.btn_stamp.setCheckable(True)
        self.btn_stamp.setChecked(True)
        self.btn_stamp.clicked.connect(lambda: self.set_tool("stamp"))
        tool_bar.addWidget(self.btn_stamp)

        self.btn_pick = QPushButton("💧 Pick")
        self.btn_pick.setCheckable(True)
        self.btn_pick.clicked.connect(lambda: self.set_tool("eyedropper"))
        tool_bar.addWidget(self.btn_pick)

        self.btn_fill = QPushButton("🪣 Fill")
        self.btn_fill.setCheckable(True)
        self.btn_fill.clicked.connect(lambda: self.set_tool("fill"))
        tool_bar.addWidget(self.btn_fill)

        tool_bar.addWidget(QLabel("Pal Bank:"))
        self.brush_pal_spin = QSpinBox()
        self.brush_pal_spin.setRange(0, 15)
        self.brush_pal_spin.valueChanged.connect(self.on_brush_pal_changed)
        tool_bar.addWidget(self.brush_pal_spin)

        self.cb_hflip = QCheckBox("H-Flip")
        self.cb_hflip.toggled.connect(lambda c: setattr(self, "brush_h_flip", c))
        tool_bar.addWidget(self.cb_hflip)

        self.cb_vflip = QCheckBox("V-Flip")
        self.cb_vflip.toggled.connect(lambda c: setattr(self, "brush_v_flip", c))
        tool_bar.addWidget(self.cb_vflip)

        self.btn_undo = QPushButton("↩ Undo")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.undo)
        tool_bar.addWidget(self.btn_undo)

        tool_bar.addStretch()

        self.export_btn = QPushButton("PNG...")
        self.export_btn.clicked.connect(self.export_png)
        tool_bar.addWidget(self.export_btn)

        self.btn_save_sbb = QPushButton("SBB .bin...")
        self.btn_save_sbb.clicked.connect(self.export_sbb_bin)
        tool_bar.addWidget(self.btn_save_sbb)

        self.btn_save_ips = QPushButton("Export IPS...")
        self.btn_save_ips.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.btn_save_ips.clicked.connect(self.export_ips_patch)
        tool_bar.addWidget(self.btn_save_ips)

        self.btn_save_rom = QPushButton("Save ROM (.gba)...")
        self.btn_save_rom.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        self.btn_save_rom.clicked.connect(self.save_modified_rom)
        tool_bar.addWidget(self.btn_save_rom)

        layout.addLayout(tool_bar)

        # Main Splitter: Left = Tile Brush Palette, Right = 32x32 SBB Map Canvas
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_grp = QGroupBox("Tile Brush Selector (Click Tile)")
        left_lay = QVBoxLayout(left_grp)
        self.brush_lbl = QLabel("Brush: Tile 0x000 | Pal 0")
        self.brush_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #FFD54F;")
        left_lay.addWidget(self.brush_lbl)

        brush_scroll = QScrollArea()
        brush_scroll.setWidgetResizable(True)
        self.tile_palette = TileBrushPaletteWidget(self)
        self.tile_palette.tile_selected.connect(self.on_brush_tile_selected)
        brush_scroll.setWidget(self.tile_palette)
        left_lay.addWidget(brush_scroll)
        splitter.addWidget(left_grp)

        right_grp = QGroupBox("32×32 Screen Base Block Canvas (Left-Click/Drag Stamp, Right-Click Pick)")
        right_lay = QVBoxLayout(right_grp)
        map_scroll = QScrollArea()
        map_scroll.setWidgetResizable(True)
        self.map_canvas = InteractiveMapCanvas(self)
        self.map_canvas.hovered_cell.connect(self.on_hover_cell)
        map_scroll.setWidget(self.map_canvas)
        right_lay.addWidget(map_scroll)
        splitter.addWidget(right_grp)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        layout.addWidget(splitter)

        self.hover_lbl = QLabel("Hover over tilemap to inspect cell...")
        self.hover_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #90CAF9;")
        layout.addWidget(self.hover_lbl)

    def set_tool(self, name: str):
        self.current_tool = name
        self.btn_stamp.setChecked(name == "stamp")
        self.btn_pick.setChecked(name == "eyedropper")
        self.btn_fill.setChecked(name == "fill")

    def on_brush_tile_selected(self, tile_idx: int):
        self.brush_tile_idx = tile_idx
        self.brush_lbl.setText(f"Brush: Tile 0x{tile_idx:03X} ({tile_idx}) | Pal {self.brush_pal_bank}")

    def on_brush_pal_changed(self, pal: int):
        self.brush_pal_bank = pal
        self.brush_lbl.setText(f"Brush: Tile 0x{self.brush_tile_idx:03X} ({self.brush_tile_idx}) | Pal {pal}")
        self.render_map()

    def toggle_auto_refresh(self, enabled: bool):
        if enabled:
            self.refresh_timer.start()
        else:
            self.refresh_timer.stop()

    def reload_sbb_from_vram(self):
        vram = self.worker.get_memory_region("VRAM") if self.worker else None
        sbb = self.sbb_spin.value()
        if vram and (sbb * 0x800 + 0x800) <= len(vram):
            raw = vram[sbb * 0x800:(sbb + 1) * 0x800]
            self.original_sbb_bytes = bytes(raw)
            self.sbb_entries = list(struct.unpack("<1024H", raw))
            self._last_loaded_sbb = sbb
            self.undo_stack.clear()
            self.btn_undo.setEnabled(False)
        self.render_map()

    def _compose_brush_entry(self) -> int:
        entry = self.brush_tile_idx & 0x3FF
        if self.brush_h_flip:
            entry |= (1 << 10)
        if self.brush_v_flip:
            entry |= (1 << 11)
        entry |= (self.brush_pal_bank & 0xF) << 12
        return entry

    def begin_edit(self):
        if self._stroke_depth == 0:
            self._active_stroke = {}
        self._stroke_depth += 1

    def commit_edit(self):
        if self._stroke_depth <= 0:
            return
        self._stroke_depth -= 1
        if self._stroke_depth == 0 and self._active_stroke:
            batch = [(idx, old_v, new_v) for idx, (old_v, new_v) in self._active_stroke.items() if old_v != new_v]
            self._active_stroke.clear()
            if batch:
                self.undo_stack.append(batch)
                self.btn_undo.setEnabled(True)

    def _write_sbb_cell(self, idx: int, new_val: int):
        old_val = self.sbb_entries[idx]
        if old_val == new_val:
            return
        self.sbb_entries[idx] = new_val
        if self._stroke_depth > 0:
            if idx not in self._active_stroke:
                self._active_stroke[idx] = (old_val, new_val)
            else:
                self._active_stroke[idx] = (self._active_stroke[idx][0], new_val)
        else:
            self.undo_stack.append([(idx, old_val, new_val)])
            self.btn_undo.setEnabled(True)

        if self.worker:
            sbb_off = self.sbb_spin.value() * 0x800
            self.worker.write_memory_region("VRAM", sbb_off + (idx * 2), struct.pack("<H", new_val))

    def undo(self):
        if not self.undo_stack:
            return
        batch = self.undo_stack.pop()
        sbb_off = self.sbb_spin.value() * 0x800
        for idx, old_val, _ in batch:
            self.sbb_entries[idx] = old_val
            if self.worker:
                self.worker.write_memory_region("VRAM", sbb_off + (idx * 2), struct.pack("<H", old_val))
        self.btn_undo.setEnabled(len(self.undo_stack) > 0)
        self.render_map()

    def stamp_tile_at(self, tx: int, ty: int):
        idx = ty * 32 + tx
        self._write_sbb_cell(idx, self._compose_brush_entry())
        self.render_map()

    def pick_tile_at(self, tx: int, ty: int):
        entry = self.sbb_entries[ty * 32 + tx]
        self.brush_tile_idx = entry & 0x3FF
        self.brush_h_flip = bool(entry & (1 << 10))
        self.brush_v_flip = bool(entry & (1 << 11))
        self.brush_pal_bank = (entry >> 12) & 0xF

        self.cb_hflip.setChecked(self.brush_h_flip)
        self.cb_vflip.setChecked(self.brush_v_flip)
        self.brush_pal_spin.blockSignals(True)
        self.brush_pal_spin.setValue(self.brush_pal_bank)
        self.brush_pal_spin.blockSignals(False)
        self.on_brush_tile_selected(self.brush_tile_idx)
        self.tile_palette.update()
        self.set_tool("stamp")

    def flood_fill_tile(self, start_tx: int, start_ty: int):
        target = self.sbb_entries[start_ty * 32 + start_tx]
        replacement = self._compose_brush_entry()
        if target == replacement:
            return

        q = deque([(start_tx, start_ty)])
        seen = {(start_tx, start_ty)}
        self.begin_edit()
        try:
            while q:
                x, y = q.popleft()
                idx = y * 32 + x
                if self.sbb_entries[idx] == target:
                    self._write_sbb_cell(idx, replacement)
                    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                        if 0 <= nx < 32 and 0 <= ny < 32 and (nx, ny) not in seen:
                            seen.add((nx, ny))
                            q.append((nx, ny))
        finally:
            self.commit_edit()
        self.render_map()

    def on_hover_cell(self, tx: int, ty: int):
        idx = ty * 32 + tx
        entry = self.sbb_entries[idx]
        t_id = entry & 0x3FF
        hf = "H" if (entry & (1 << 10)) else "-"
        vf = "V" if (entry & (1 << 11)) else "-"
        pal = (entry >> 12) & 0xF
        vram_addr = 0x06000000 + (self.sbb_spin.value() * 0x800) + (idx * 2)
        self.hover_lbl.setText(
            f"Cell ({tx:02d}, {ty:02d}) | VRAM: 0x{vram_addr:08X} | "
            f"Raw: 0x{entry:04X} | Tile: 0x{t_id:03X} | Pal: {pal} | Flip: {hf}{vf}"
        )

    def render_map(self):
        vram = self.worker.get_memory_region("VRAM") if self.worker else None
        if not vram:
            return

        sbb = self.sbb_spin.value()
        cbb = self.cbb_spin.value()
        is_8bpp = self.bpp_check.isChecked()

        # If auto-refreshing live, keep local buffer synced to VRAM
        if self.auto_refresh_cb.isChecked() or self._last_loaded_sbb != sbb:
            map_offset = sbb * 0x800
            if map_offset + 0x800 <= len(vram):
                raw = vram[map_offset:map_offset + 0x800]
                if self.original_sbb_bytes is None or self._last_loaded_sbb != sbb:
                    self.original_sbb_bytes = bytes(raw)
                self.sbb_entries = list(struct.unpack("<1024H", raw))
                self._last_loaded_sbb = sbb

        bg_colors, _, is_live_pram = _extract_pram_palettes(self.worker)
        if is_live_pram:
            self.pal_status_lbl.setText("Palette: Live PRAM")
            self.pal_status_lbl.setStyleSheet("color: #81C784; font-size: 11px;")
        else:
            self.pal_status_lbl.setText("Palette: Fallback")
            self.pal_status_lbl.setStyleSheet("color: #FFB74D; font-size: 11px;")

        tile_offset_base = cbb * 0x4000
        if tile_offset_base >= len(vram):
            return

        cbb_bytes = vram[tile_offset_base:]
        if not is_8bpp:
            all_tiles = _decode_tiles_4bpp(cbb_bytes, 1024)
            pal_arr = np.array(bg_colors[:256], dtype=np.uint8)
            if is_live_pram:
                pal_arr[0::16] = pal_arr[0]
        else:
            all_tiles = _decode_tiles_8bpp(cbb_bytes, 1024)
            pal_arr = np.array(bg_colors[:256], dtype=np.uint8)

        self.tile_palette.rebuild_image(all_tiles, pal_arr, is_8bpp, self.brush_pal_bank)

        num_tiles = len(all_tiles)
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        rgba[..., :3] = pal_arr[0]
        rgba[..., 3] = 255

        for ty in range(32):
            for tx in range(32):
                entry = self.sbb_entries[ty * 32 + tx]
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
        self.map_canvas.update_bitmap(rgba)

    # ==========================================================================
    # EXPORT & ROM / IPS PATCHING
    # ==========================================================================
    def _build_patched_rom(self) -> tuple:
        if not self.worker or not self.worker.rom_path or not os.path.exists(self.worker.rom_path):
            return None, None, 0
        if not self.original_sbb_bytes:
            return None, None, 0

        new_sbb = struct.pack("<1024H", *self.sbb_entries)
        if new_sbb == self.original_sbb_bytes:
            return None, None, 0

        with open(self.worker.rom_path, "rb") as f:
            rom_data = bytearray(f.read())
        orig_rom = bytes(rom_data)
        matches = 0

        # 1. Exact full 2048-byte or visible 1280-byte SBB match
        for span in (2048, 1280):
            orig_slice = self.original_sbb_bytes[:span]
            new_slice = new_sbb[:span]
            if orig_slice == new_slice:
                continue
            pos = 0
            while True:
                found = rom_data.find(orig_slice, pos)
                if found == -1:
                    break
                rom_data[found:found + span] = new_slice
                matches += 1
                pos = found + span
            if matches > 0:
                break

        # 2. Modified 64-byte row matches (handles partial screen maps)
        if matches == 0:
            for row in range(32):
                r_off = row * 64
                orig_row = self.original_sbb_bytes[r_off:r_off + 64]
                new_row = new_sbb[r_off:r_off + 64]
                if orig_row == new_row:
                    continue
                pos = 0
                while True:
                    found = rom_data.find(orig_row, pos)
                    if found == -1:
                        break
                    rom_data[found:found + 64] = new_row
                    matches += 1
                    pos = found + 64

        # 3. Fallback: scan BIOS LZ77 (0x10) compressed tilemaps
        if matches == 0:
            modified_rows = [
                (r * 64, self.original_sbb_bytes[r * 64:(r + 1) * 64], new_sbb[r * 64:(r + 1) * 64])
                for r in range(32)
                if self.original_sbb_bytes[r * 64:(r + 1) * 64] != new_sbb[r * 64:(r + 1) * 64]
            ]
            for offset in range(0, len(rom_data) - 4, 4):
                if rom_data[offset] != 0x10:
                    continue
                decomp, comp_len = lz77_decompress(rom_data, offset)
                if not decomp or comp_len <= 4:
                    continue
                decomp_arr = bytearray(decomp)
                stream_hit = False
                for _, orig_row, new_row in modified_rows:
                    r_pos = decomp_arr.find(orig_row)
                    if r_pos != -1:
                        decomp_arr[r_pos:r_pos + 64] = new_row
                        stream_hit = True
                        matches += 1
                if stream_hit:
                    recomp = lz77_compress(bytes(decomp_arr))
                    if len(recomp) <= comp_len:
                        rom_data[offset:offset + len(recomp)] = recomp
                        rom_data[offset + len(recomp):offset + comp_len] = bytes(comp_len - len(recomp))

        return bytes(rom_data), orig_rom, matches

    def save_modified_rom(self):
        patched, _, matches = self._build_patched_rom()
        if not patched or matches == 0:
            QMessageBox.warning(
                self, "ROM Patch Notice",
                "Could not locate matching uncompressed or LZ77 tilemap data in ROM.\n"
                "(Many GBA games stream tilemaps dynamically from WRAM or custom compression.)\n\n"
                "Tip: Use 'SBB .bin...' to export your edited 2 KB Screen Base Block directly!"
            )
            return

        default_name = f"patched_map_{os.path.basename(self.worker.rom_path)}"
        path, _ = QFileDialog.getSaveFileName(self, "Save Modified ROM", default_name, "GBA ROMs (*.gba)")
        if path:
            with open(path, "wb") as f:
                f.write(patched)
            QMessageBox.information(self, "Success", f"Patched {matches} tilemap block/row match(es) into:\n{path}")

    def export_ips_patch(self):
        patched, orig_rom, matches = self._build_patched_rom()
        if not patched or matches == 0:
            QMessageBox.warning(self, "IPS Notice", "No matched ROM tilemap changes found to generate an IPS patch.")
            return

        ips_data = create_ips_patch(orig_rom, patched)
        default_name = f"{os.path.splitext(os.path.basename(self.worker.rom_path))[0]}_map.ips"
        path, _ = QFileDialog.getSaveFileName(self, "Export IPS Patch", default_name, "IPS Patches (*.ips)")
        if path:
            with open(path, "wb") as f:
                f.write(ips_data)
            QMessageBox.information(self, "IPS Created", f"Exported IPS patch ({len(ips_data)} bytes) to:\n{path}")

    def export_sbb_bin(self):
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        default_path = os.path.join(export_dir, f"map_sbb{self.sbb_spin.value()}.bin")
        path, _ = QFileDialog.getSaveFileName(self, "Export SBB Binary", default_path, "Binary Files (*.bin)")
        if path:
            with open(path, "wb") as f:
                f.write(struct.pack("<1024H", *self.sbb_entries))
            QMessageBox.information(self, "Exported", f"Saved 2 KB SBB dump to:\n{path}")

    def export_png(self):
        if self.current_map_img:
            export_pil_to_png(self, self.current_map_img, f"map_sbb{self.sbb_spin.value()}.png", self.config)

    def closeEvent(self, event):
        self.refresh_timer.stop()
        event.accept()
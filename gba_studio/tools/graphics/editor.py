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

from .editor_controls import EditorControlsMixin
from .drawing_tools import DrawingToolsMixin
from .rom_export import RomExportMixin

class GraphicEditorDialog(QDialog, EditorControlsMixin, DrawingToolsMixin, RomExportMixin):
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

    def closeEvent(self, event):
        """Flushes the full modified VRAM buffer to the live core and unpauses."""
        if self.worker and self.vram_copy:
            self.worker.write_memory_region("VRAM", 0, bytes(self.vram_copy))
        if self.worker and hasattr(self, 'was_paused'):
            self.worker.is_paused = self.was_paused
        event.accept()

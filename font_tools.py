# font_tools.py
import os
import json
import struct
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QComboBox, QLineEdit, 
                             QFileDialog, QMessageBox, QTabWidget, QSpinBox, 
                             QGroupBox, QTextEdit, QCheckBox, QScrollArea)
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor
from PyQt6.QtCore import Qt

class FontToolsDialog(QDialog):
    """GBA Font Ripper, Editor, and TTF Generator Suite."""
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Translation Tools - Font Studio & TTF Generator")
        self.resize(800, 650)
        
        self.current_font_img = None
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        
        # Tab 1: 1bpp / 2bpp ROM Font Ripper
        self.tab_ripper = QWidget()
        self._init_ripper_tab(self.tab_ripper)
        self.tabs.addTab(self.tab_ripper, "ROM Font Ripper (1bpp / 2bpp)")
        
        # Tab 2: TTF to GBA Font Generator
        self.tab_generator = QWidget()
        self._init_generator_tab(self.tab_generator)
        self.tabs.addTab(self.tab_generator, "TTF to GBA Tile Generator")

        main_layout.addWidget(self.tabs)

    # =========================================================================
    # TAB 1: ROM FONT RIPPER (1bpp, 2bpp, 4bpp)
    # =========================================================================
    def _init_ripper_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        nav_bar = QHBoxLayout()
        nav_bar.addWidget(QLabel("ROM Offset (Hex):"))
        self.offset_edit = QLineEdit("0x08000000")
        self.offset_edit.setFixedWidth(100)
        nav_bar.addWidget(self.offset_edit)
        
        nav_bar.addWidget(QLabel("Bit Depth:"))
        self.bpp_combo = QComboBox()
        self.bpp_combo.addItems(["1bpp (Monochrome)", "2bpp (4 Colors)", "4bpp (16 Colors)"])
        nav_bar.addWidget(self.bpp_combo)

        self.endian_check = QCheckBox("LSB First (Standard GBA)")
        self.endian_check.setChecked(True)
        nav_bar.addWidget(self.endian_check)

        jump_btn = QPushButton("Scan & Render")
        jump_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        jump_btn.clicked.connect(self.render_rom_font)
        nav_bar.addWidget(jump_btn)
        nav_bar.addStretch()
        layout.addLayout(nav_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.font_preview_label = QLabel("Enter ROM offset and click Scan.")
        self.font_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.font_preview_label.setStyleSheet("background-color: #111; color: #00FF66;")
        self.scroll.setWidget(self.font_preview_label)
        layout.addWidget(self.scroll)

        btn_bar = QHBoxLayout()
        export_btn = QPushButton("Export Decoded Sheet to PNG...")
        export_btn.clicked.connect(self.export_ripped_png)
        btn_bar.addWidget(export_btn)
        layout.addLayout(btn_bar)

    def render_rom_font(self):
        if not self.worker.rom_path or not os.path.exists(self.worker.rom_path):
            QMessageBox.warning(self, "Error", "No ROM loaded.")
            return

        try:
            offset = int(self.offset_edit.text(), 16)
            if offset >= 0x08000000:
                offset -= 0x08000000
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Invalid Hex Offset.")
            return

        with open(self.worker.rom_path, 'rb') as f:
            f.seek(offset)
            # Read enough data for 256 tiles
            raw_data = f.read(256 * 32) 

        bpp_mode = self.bpp_combo.currentIndex() # 0 = 1bpp, 1 = 2bpp, 2 = 4bpp
        lsb_first = self.endian_check.isChecked()
        
        tiles_per_row = 16
        rows = 16
        img = Image.new("RGBA", (tiles_per_row * 8, rows * 8), (0, 0, 0, 255))
        
        # Color mapping (Grayscale scaling)
        colors_1bpp = [(0,0,0,255), (255,255,255,255)]
        colors_2bpp = [(0,0,0,255), (85,85,85,255), (170,170,170,255), (255,255,255,255)]

        data_idx = 0
        for ty in range(rows):
            for tx in range(tiles_per_row):
                if data_idx >= len(raw_data): break

                if bpp_mode == 0: # 1bpp (8 bytes per tile)
                    if data_idx + 8 > len(raw_data): break
                    tile_bytes = raw_data[data_idx:data_idx+8]
                    for py in range(8):
                        b = tile_bytes[py]
                        for px in range(8):
                            shift = px if lsb_first else (7 - px)
                            c_idx = (b >> shift) & 1
                            img.putpixel((tx*8 + px, ty*8 + py), colors_1bpp[c_idx])
                    data_idx += 8

                elif bpp_mode == 1: # 2bpp (16 bytes per tile)
                    if data_idx + 16 > len(raw_data): break
                    tile_bytes = raw_data[data_idx:data_idx+16]
                    for py in range(8):
                        b0, b1 = tile_bytes[py*2], tile_bytes[py*2+1]
                        for px in range(8):
                            shift = px if lsb_first else (7 - px)
                            bit0 = (b0 >> shift) & 1
                            bit1 = (b1 >> shift) & 1
                            c_idx = bit0 | (bit1 << 1)
                            img.putpixel((tx*8 + px, ty*8 + py), colors_2bpp[c_idx])
                    data_idx += 16

                elif bpp_mode == 2: # 4bpp (32 bytes per tile)
                    if data_idx + 32 > len(raw_data): break
                    tile_bytes = raw_data[data_idx:data_idx+32]
                    for py in range(8):
                        for px in range(8):
                            b = tile_bytes[py * 4 + px // 2]
                            c_idx = (b & 0xF) if (px % 2 == 0) else ((b >> 4) & 0xF)
                            lum = c_idx * 17
                            img.putpixel((tx*8 + px, ty*8 + py), (lum, lum, lum, 255))
                    data_idx += 32

        self.current_font_img = img
        scaled = img.resize((img.width * 4, img.height * 4), Image.Resampling.NEAREST)
        qimg = QImage(scaled.tobytes(), scaled.width, scaled.height, scaled.width * 4, QImage.Format.Format_RGBA8888)
        self.font_preview_label.setPixmap(QPixmap.fromImage(qimg))

    def export_ripped_png(self):
        if not self.current_font_img: return
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        filepath, _ = QFileDialog.getSaveFileName(self, "Export Font", os.path.join(export_dir, "ripped_font.png"), "PNG (*.png)")
        if filepath:
            self.current_font_img.save(filepath)
            QMessageBox.information(self, "Exported", f"Saved to {filepath}")

    # =========================================================================
    # TAB 2: TTF TO GBA GENERATOR
    # =========================================================================
    def _init_generator_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        # Setup
        setup_box = QGroupBox("1. Font Configuration")
        setup_layout = QVBoxLayout(setup_box)
        
        file_row = QHBoxLayout()
        self.ttf_path_edit = QLineEdit()
        self.ttf_path_edit.setPlaceholderText("Select a .ttf or .otf file...")
        btn_browse_ttf = QPushButton("Browse...")
        btn_browse_ttf.clicked.connect(self.browse_ttf)
        file_row.addWidget(self.ttf_path_edit)
        file_row.addWidget(btn_browse_ttf)
        setup_layout.addLayout(file_row)
        
        param_row = QHBoxLayout()
        param_row.addWidget(QLabel("Font Size (px):"))
        self.ttf_size = QSpinBox()
        self.ttf_size.setRange(4, 32)
        self.ttf_size.setValue(10)
        param_row.addWidget(self.ttf_size)

        param_row.addWidget(QLabel("Format:"))
        self.gen_bpp_combo = QComboBox()
        self.gen_bpp_combo.addItems(["1bpp (Monochrome)", "2bpp (4 Color)", "4bpp (16 Color)"])
        param_row.addWidget(self.gen_bpp_combo)
        setup_layout.addLayout(param_row)
        layout.addWidget(setup_box)

        # Characters
        char_box = QGroupBox("2. Character Set (VWF)")
        char_layout = QVBoxLayout(char_box)
        char_layout.addWidget(QLabel("Type the exact characters you want to generate in order:"))
        self.chars_edit = QTextEdit()
        self.chars_edit.setPlainText("ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n0123456789!?.,'- ")
        self.chars_edit.setFixedHeight(80)
        char_layout.addWidget(self.chars_edit)
        layout.addWidget(char_box)

        # Actions
        btn_gen = QPushButton("Generate GBA Binary & Width Table (VWF)...")
        btn_gen.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        btn_gen.clicked.connect(self.generate_gba_font)
        layout.addWidget(btn_gen)

        self.gen_status = QLabel("Ready.")
        layout.addWidget(self.gen_status)
        layout.addStretch()

    def browse_ttf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select TrueType Font", "", "Fonts (*.ttf *.otf)")
        if path: self.ttf_path_edit.setText(path)

    def generate_gba_font(self):
        font_path = self.ttf_path_edit.text()
        if not os.path.exists(font_path):
            QMessageBox.warning(self, "Error", "Invalid font file path.")
            return

        try:
            font = ImageFont.truetype(font_path, self.ttf_size.value())
        except Exception as e:
            QMessageBox.warning(self, "Font Error", f"Could not load TTF: {e}")
            return

        chars = self.chars_edit.toPlainText().replace('\n', '')
        if not chars: return

        bpp_mode = self.gen_bpp_combo.currentIndex() # 0=1bpp, 1=2bpp, 2=4bpp
        
        binary_payload = bytearray()
        width_table = []
        width_dict = {}

        # GBA Tiles are always 8x8. 
        for i, char in enumerate(chars):
            # Render character onto an 8x8 image
            img = Image.new("L", (8, 8), 0) # Black background
            draw = ImageDraw.Draw(img)
            draw.text((0, -2), char, fill=255, font=font) # -2 offsets typical TTF ascenders
            
            # Calculate variable width (VWF)
            bbox = font.getbbox(char)
            width = bbox[2] - bbox[0] if bbox else 4
            width_table.append(width)
            width_dict[char] = width

            # Quantize and pack into GBA binary formats
            pixels = img.load()
            
            if bpp_mode == 0: # 1bpp
                for y in range(8):
                    byte_val = 0
                    for x in range(8):
                        if pixels[x, y] > 127: # Threshold
                            byte_val |= (1 << x) # LSB first unpacking standard
                    binary_payload.append(byte_val)
                    
            elif bpp_mode == 2: # 4bpp standard GBA
                for y in range(8):
                    for x in range(0, 8, 2):
                        p1 = 15 if pixels[x, y] > 127 else 0
                        p2 = 15 if pixels[x+1, y] > 127 else 0
                        byte_val = (p1 & 0xF) | ((p2 & 0xF) << 4)
                        binary_payload.append(byte_val)

        # Export Files
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(font_path))[0]
        timestamp = datetime.datetime.now().strftime("%Y%m%d")
        
        bin_path = os.path.join(export_dir, f"{base_name}_{timestamp}_{len(chars)}chars.bin")
        vwf_path = os.path.join(export_dir, f"{base_name}_{timestamp}_vwf.bin")
        json_path = os.path.join(export_dir, f"{base_name}_{timestamp}_vwf.json")

        with open(bin_path, 'wb') as f: f.write(binary_payload)
        with open(vwf_path, 'wb') as f: f.write(bytes(width_table))
        with open(json_path, 'w', encoding='utf-8') as f: json.dump(width_dict, f, indent=4, ensure_ascii=False)

        self.gen_status.setText(f"Success! Generated {len(chars)} tiles ({len(binary_payload)} bytes).")
        QMessageBox.information(self, "Export Complete", 
                                f"Font packed successfully!\n\n"
                                f"Graphics: {os.path.basename(bin_path)}\n"
                                f"Width Table: {os.path.basename(vwf_path)}")
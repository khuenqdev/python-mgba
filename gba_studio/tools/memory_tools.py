# memory_tools.py
import os
import json
import datetime
import numpy as np
from PIL import Image

from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QPushButton, 
                             QScrollArea, QLabel, QFileDialog, QMessageBox)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt

# Attempt to load the Enum for Video RAM, safely fallback to hardcoded Libretro IDs
try:
    from pdretro import MemoryRegion
    VRAM_ID = getattr(MemoryRegion, 'VIDEO_RAM', 3)
except ImportError:
    VRAM_ID = 3
    
# Hardcode Palette RAM to 4 (specific to mGBA libretro core mapping)
PRAM_ID = 4 

class MemoryToolsWindow(QMainWindow):
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        
        self.setWindowTitle("VRAM Tileset & Sprite Viewer")
        self.resize(560, 800)
        
        self._init_ui()
        self._load_vram_preview()

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Export Button (Bright Green for visibility)
        self.export_btn = QPushButton("Export JSON Map & Color/Raw PNGs")
        self.export_btn.setStyleSheet("font-weight: bold; font-size: 14px; background-color: #4CAF50; color: white; padding: 10px;")
        self.export_btn.clicked.connect(self.export_vram_data)
        layout.addWidget(self.export_btn)

        # Scrollable Area for the massive tileset image
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background-color: black;")
        layout.addWidget(self.scroll_area)

        # Image Label that goes inside the scroll area
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll_area.setWidget(self.image_label)

    def _load_vram_preview(self):
        """Fetches the VRAM from the active emulation core and renders a 2x scaled preview."""
        vram_bytes = self.worker.get_memory_region(VRAM_ID)
        if not vram_bytes:
            QMessageBox.warning(self, "Error", "Could not read VRAM. Ensure game is actively running.")
            return

        img_data = np.zeros((768, 256), dtype=np.uint8) 
        max_tiles = min(len(vram_bytes) // 32, 3072) 
        
        if max_tiles > 0:
            arr = np.frombuffer(vram_bytes[:max_tiles*32], dtype=np.uint8).reshape((max_tiles, 8, 4))
            p1 = (arr & 0x0F) * 17        
            p2 = ((arr >> 4) & 0x0F) * 17 
            pixels = np.empty((max_tiles, 8, 8), dtype=np.uint8)
            pixels[:, :, 0::2] = p1
            pixels[:, :, 1::2] = p2
            for t in range(max_tiles):
                tile_x = (t % 32) * 8
                tile_y = (t // 32) * 8
                img_data[tile_y:tile_y+8, tile_x:tile_x+8] = pixels[t]

        # Convert numpy array to PyQt6 QImage
        height, width = img_data.shape
        bytes_per_line = width
        qimg = QImage(img_data.data, width, height, bytes_per_line, QImage.Format.Format_Grayscale8)
        
        # Scale 2x for better visibility in the tool
        pixmap = QPixmap.fromImage(qimg).scaled(width * 2, height * 2, 
                                                Qt.AspectRatioMode.KeepAspectRatio, 
                                                Qt.TransformationMode.FastTransformation)
        self.image_label.setPixmap(pixmap)

    def export_vram_data(self):
        """Extracts VRAM, parses Palettes, and writes Colored/Raw PNGs + JSON Map to disk."""
        screenshots_dir = self.config.get("paths", "screenshots") or "screenshots"
        os.makedirs(screenshots_dir, exist_ok=True)
        
        default_name = f"vram_export_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        initial_path = os.path.join(os.getcwd(), screenshots_dir, default_name)
        
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Complete VRAM Data Export", initial_path, "PNG Image (*.png)"
        )
        
        if not filepath:
            return # User canceled
            
        vram_bytes = self.worker.get_memory_region(VRAM_ID)
        if not vram_bytes:
            QMessageBox.critical(self, "Error", "VRAM Extraction Failed.")
            return
        
        img_data = np.zeros((768, 256), dtype=np.uint8) 
        max_tiles = min(len(vram_bytes) // 32, 3072) 
        
        # Decode raw tiles
        if max_tiles > 0:
            arr = np.frombuffer(vram_bytes[:max_tiles*32], dtype=np.uint8).reshape((max_tiles, 8, 4))
            p1 = (arr & 0x0F) * 17        
            p2 = ((arr >> 4) & 0x0F) * 17 
            pixels = np.empty((max_tiles, 8, 8), dtype=np.uint8)
            pixels[:, :, 0::2] = p1
            pixels[:, :, 1::2] = p2
            for t in range(max_tiles):
                tile_x = (t % 32) * 8
                tile_y = (t // 32) * 8
                img_data[tile_y:tile_y+8, tile_x:tile_x+8] = pixels[t]

        img_raw = Image.fromarray(img_data, 'L')
        
        # Extract Palette RAM (PRAM)
        pram_bytes = self.worker.get_memory_region(PRAM_ID)
        palette = np.zeros((256, 3), dtype=np.uint8)
        
        if pram_bytes and len(pram_bytes) >= 32:
            pram_arr = np.frombuffer(pram_bytes[:32], dtype=np.uint16)
            for i in range(16):
                c = pram_arr[i]
                r = (c & 0x1F) << 3
                g = ((c >> 5) & 0x1F) << 3
                b = ((c >> 10) & 0x1F) << 3
                palette[i] = [r, g, b]
        else:
            colors = [[0,0,0], [0,128,0], [128,0,0], [0,0,128], [128,128,0]]
            for i in range(16): palette[i] = colors[i % len(colors)]

        # Apply Palette
        index_data = (img_data // 17).astype(np.uint8)
        img_colored = Image.fromarray(index_data, 'P')
        img_colored.putpalette(palette.flatten().tolist())

        # Build JSON Map
        tile_map = {}
        for t in range(max_tiles):
            tile_map[f"tile_{t}"] = {
                "index": t,
                "x_pixel": (t % 32) * 8,
                "y_pixel": (t // 32) * 8,
                "vram_address": f"0x{0x06000000 + t * 32:08X}"
            }
            
        base_path = filepath.rsplit('.', 1)[0] if '.' in filepath else filepath
        raw_path = base_path + "_raw.png"
        col_path = base_path + "_colored.png"
        json_path = base_path + "_map.json"
        
        img_raw.save(raw_path)
        img_colored.save(col_path)
        
        with open(json_path, "w") as f:
            json.dump(tile_map, f, indent=4)
            
        base_name = os.path.basename(filepath).replace(".png", "")
        QMessageBox.information(self, "Exported", 
            f"Success! 3 Files created:\n\n1. {base_name}_raw.png\n2. {base_name}_colored.png\n3. {base_name}_map.json")

# GBA OAM Memory ID in mGBA libretro is typically 5 (or accessed via system ram offsets)
OAM_ID = 5 

class OAMViewerWindow(QMainWindow):
    """Replicates mGBA's Native Sprite (OAM) Viewer"""
    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.setWindowTitle("OAM (Sprite) Viewer")
        self.resize(600, 400)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        self.refresh_btn = QPushButton("Refresh Sprite Data")
        self.refresh_btn.clicked.connect(self.refresh_oam)
        layout.addWidget(self.refresh_btn)
        
        self.info_label = QLabel("Click Refresh to dump active sprites.")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.info_label.setStyleSheet("font-family: monospace; font-size: 12px;")
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.info_label)
        layout.addWidget(scroll)

    def refresh_oam(self):
        oam_bytes = self.worker.get_memory_region(OAM_ID)
        if not oam_bytes or len(oam_bytes) < 1024:
            self.info_label.setText("OAM Memory not exposed by this specific libretro core version.\n(Requires core compiled with advanced memory mapping).")
            return
            
        output = "ID  | X    | Y    | Tile | Palette\n"
        output += "-" * 40 + "\n"
        
        # Parse exactly like mGBA's GBAObj struct
        arr = np.frombuffer(oam_bytes[:1024], dtype=np.uint16)
        
        for i in range(128):
            attr0 = arr[i*4 + 0]
            attr1 = arr[i*4 + 1]
            attr2 = arr[i*4 + 2]
            
            y = attr0 & 0xFF
            x = attr1 & 0x1FF
            tile = attr2 & 0x3FF
            palette = (attr2 >> 12) & 0xF
            
            # Filter out hidden/disabled sprites (Y=160 or 254 usually means hidden in GBA)
            if y < 160:
                output += f"{i:<3} | {x:<4} | {y:<4} | {tile:<4} | {palette:<4}\n"
                
        self.info_label.setText(output)
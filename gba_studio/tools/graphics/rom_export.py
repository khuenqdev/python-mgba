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

class RomExportMixin:
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


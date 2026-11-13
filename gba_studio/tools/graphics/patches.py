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



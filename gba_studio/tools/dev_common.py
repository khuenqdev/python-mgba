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

from .hex_editor import HexEditorDialog

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

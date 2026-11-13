# scene_dump.py
import os
import csv
import json
import struct
import hashlib
import datetime
import numpy as np
from PIL import Image

from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QFileDialog, 
                             QMessageBox, QProgressBar)
from PyQt6.QtCore import Qt

try:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
    CAPSTONE_AVAILABLE = True
except ImportError:
    CAPSTONE_AVAILABLE = False


def bgr555_to_rgb(val: int):
    val = int(val)
    r = (val & 0x1F) * 255 // 31
    g = ((val >> 5) & 0x1F) * 255 // 31
    b = ((val >> 10) & 0x1F) * 255 // 31
    return (int(r), int(g), int(b))


from .scene_engine import SceneDumpEngine

class SceneDumpDialog(QDialog):
    """GUI Trigger dialog with progress bar."""
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.engine = SceneDumpEngine(worker, config)

        self.setWindowTitle("Export Full Screen Forensic Dossier")
        self.resize(560, 310)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        desc = QLabel(
            "<b style='color: #00FF66; font-size: 14px;'>Forensic Screen &amp; State Snapshot Studio</b><br><br>"
            "Captures a complete, self-contained forensic dossier of this frame:<br>"
            "• Exact emulator save state snapshot (<code>.state</code> file)<br>"
            "• Complete technical summary in <code>scene_data.json</code><br>"
            "• Factual hardware report in <code>scene_description.md</code><br>"
            "• Visual Layer PNGs and composite <code>screen.png</code>"
        )
        desc.setStyleSheet("""
            QLabel {
                background-color: #1e1e1e;
                color: #e0e0e0;
                padding: 12px;
                border-radius: 8px;
                border: 1px solid #333333;
                font-size: 13px;
                line-height: 1.4;
            }
            code {
                background-color: #2b2b2b;
                color: #00FF66;
                padding: 2px 4px;
                border-radius: 4px;
                font-family: monospace;
            }
        """)
        layout.addWidget(desc)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_lbl = QLabel("Ready to export.")
        self.status_lbl.setStyleSheet("color: #888; font-weight: bold;")
        layout.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        export_btn = QPushButton("🚀 Generate Forensic Dossier...")
        export_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 10px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        export_btn.clicked.connect(self.run_export)
        btn_row.addWidget(export_btn)
        layout.addLayout(btn_row)

    def run_export(self):
        rom_path = self.worker.rom_path if self.worker else None
        if not rom_path:
            QMessageBox.warning(self, "Error", "No game ROM currently running.")
            return

        try:
            self.progress_bar.setValue(40)
            self.status_lbl.setText("Dumping memory, state, and rendering layers...")
            self.repaint()

            out_dir = self.engine.create_compact_dump(rom_path)
            self.progress_bar.setValue(100)
            self.status_lbl.setText("Done!")

            QMessageBox.information(
                self, "Export Complete", 
                f"Forensic dossier and save state exported to:\n{out_dir}\n\n"
                f"Exported Files:\n"
                f"• *.state (Exact save state for this frame)\n"
                f"• scene_data.json (Complete machine-readable forensics)\n"
                f"• scene_description.md (Factual report)\n"
                f"• screen.png & layers/ (Visual assets)"
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"An error occurred: {e}")

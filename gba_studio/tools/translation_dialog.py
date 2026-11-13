# translation_tools.py
import os
import struct
import csv
import re
import numpy as np
from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QComboBox, QLineEdit, 
                             QTableWidget, QTableWidgetItem, QHeaderView, 
                             QFileDialog, QMessageBox, QTabWidget, QGroupBox,
                             QTextEdit, QSpinBox, QCheckBox)
from PyQt6.QtCore import Qt, QTimer

from .encoding import EncodingManager

from .translation_viewer_tab import ViewerTabMixin
from .translation_script_tab import ScriptTabMixin
from .translation_search_tab import SearchTabMixin
from .translation_injector_tab import InjectorTabMixin

class TranslationToolsDialog(QDialog, ViewerTabMixin, ScriptTabMixin, SearchTabMixin, InjectorTabMixin):
    """Integrated Translation Suite for ROM Hackers."""
    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.encoding = EncodingManager()
        
        self.setWindowTitle("Translation Tools - Text Inspector & Script Patcher")
        self.resize(1000, 700)
        
        self._init_ui()
        self.refresh_memory()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # --- Top Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<b>Global Encoding Mode:</b>"))
        
        self.enc_combo = QComboBox()
        self.enc_combo.addItems(["ASCII", "Shift-JIS", "Custom TBL"])
        self.enc_combo.currentIndexChanged.connect(self.on_encoding_changed)
        toolbar.addWidget(self.enc_combo)
        
        self.load_tbl_btn = QPushButton("Load .tbl File...")
        self.load_tbl_btn.clicked.connect(self.on_load_tbl)
        toolbar.addWidget(self.load_tbl_btn)
        
        self.tbl_status = QLabel("No table loaded.")
        self.tbl_status.setStyleSheet("color: #888; font-style: italic;")
        toolbar.addWidget(self.tbl_status)
        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # --- Tabs ---
        self.tabs = QTabWidget()
        
        self.tab_viewer = QWidget()
        self._init_viewer_tab(self.tab_viewer)
        self.tabs.addTab(self.tab_viewer, "Hex / Text Viewer")
        
        self.tab_script = QWidget()
        self._init_script_tab(self.tab_script)
        self.tabs.addTab(self.tab_script, "Script Extractor & Auto-Repointer")

        self.tab_search = QWidget()
        self._init_search_tab(self.tab_search)
        self.tabs.addTab(self.tab_search, "Relative Value Search")

        self.tab_injector = QWidget()
        self._init_injector_tab(self.tab_injector)
        self.tabs.addTab(self.tab_injector, "WYSIWYG Live RAM Text Injector")
        
        main_layout.addWidget(self.tabs)

    # =========================================================================
    # TAB 1: HEX / TEXT VIEWER
    # =========================================================================
    def closeEvent(self, event):
        if hasattr(self, 'auto_inject_timer'):
            self.auto_inject_timer.stop()
        event.accept()

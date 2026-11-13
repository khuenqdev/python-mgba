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

class EncodingManager:
    """Handles Shift-JIS, ASCII, and Custom Hex .tbl decoding and encoding."""
    def __init__(self):
        self.mode = "ASCII"
        self.custom_tbl = {}
        self.rev_tbl = {}
        self.rev_keys = []
        self.max_key_len = 1

    def load_tbl(self, filepath: str) -> bool:
        self.custom_tbl.clear()
        self.rev_tbl.clear()
        self.rev_keys.clear()
        self.max_key_len = 1
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or '=' not in line: continue
                    hex_part, text_part = line.split('=', 1)
                    
                    try:
                        key_bytes = bytes.fromhex(hex_part)
                        self.custom_tbl[key_bytes] = text_part
                        if len(key_bytes) > self.max_key_len:
                            self.max_key_len = len(key_bytes)
                    except ValueError:
                        continue
            
            # Build reverse lookup table for encoding
            self.rev_tbl = {v: k for k, v in self.custom_tbl.items()}
            self.rev_keys = sorted(self.rev_tbl.keys(), key=len, reverse=True)
            self.mode = "Custom TBL"
            return True
        except Exception as e:
            print(f"TBL Load Error: {e}")
            return False

    def decode(self, data: bytes) -> str:
        if not data: return ""
        
        if self.mode == "Shift-JIS":
            return data.decode('shift_jis', errors='replace').replace('\n', ' ')
        elif self.mode == "ASCII":
            return "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
        elif self.mode == "Custom TBL" and self.custom_tbl:
            decoded = ""
            i = 0
            while i < len(data):
                matched = False
                for length in range(self.max_key_len, 0, -1):
                    if i + length > len(data): continue
                    chunk = data[i:i+length]
                    if chunk in self.custom_tbl:
                        decoded += self.custom_tbl[chunk]
                        i += length
                        matched = True
                        break
                if not matched:
                    decoded += "."
                    i += 1
            return decoded
        return "".join(chr(b) if 32 <= b <= 126 else "." for b in data)

    def encode(self, text: str) -> bytes:
        if not text: return b""
        out = bytearray()
        
        parts = re.split(r'\[([0-9A-Fa-f]{2})\]', text)
        
        for i, part in enumerate(parts):
            if i % 2 == 1:
                out.append(int(part, 16))
            else:
                if not part: continue
                if self.mode == "Shift-JIS":
                    out.extend(part.encode('shift_jis', errors='ignore'))
                elif self.mode == "ASCII":
                    out.extend(part.encode('ascii', errors='ignore'))
                elif self.mode == "Custom TBL" and self.rev_tbl:
                    pi = 0
                    while pi < len(part):
                        matched = False
                        for k in self.rev_keys:
                            if part.startswith(k, pi):
                                out.extend(self.rev_tbl[k])
                                pi += len(k)
                                matched = True
                                break
                        if not matched:
                            out.extend(part[pi].encode('ascii', errors='ignore'))
                            pi += 1
        return bytes(out)



# cheats.py
import re
import os
import json
import urllib.request
import urllib.parse
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QTableWidget, QTableWidgetItem, 
                             QFileDialog, QMessageBox, QHeaderView, QInputDialog, 
                             QComboBox, QDialog, QListWidget)
from PyQt6.QtCore import Qt, QMutexLocker, QThread, pyqtSignal

class CheatDownloader(QThread):
    """Background thread to fetch cheat list from public Libretro DB without freezing UI"""
    results_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def run(self):
        try:
            # Query the Libretro GBA Cheat Database on GitHub
            url = "https://api.github.com/repos/libretro/libretro-database/contents/cht/Nintendo%20-%20Game%20Boy%20Advance"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                games = [{"name": item["name"].replace(".cht", ""), "url": item["download_url"]} 
                         for item in data if item["name"].endswith(".cht")]
                self.results_ready.emit(games)
        except Exception as e:
            self.error_occurred.emit(str(e))

class CheatEngine:
    def __init__(self, worker):
        self.worker = worker
        self.cheats = [] 
        
    def apply_cheats(self):
        """Applies active raw memory codes every frame."""
        if not self.cheats: return

        with QMutexLocker(self.worker.mutex):
            if not self.worker.emu: return
            
            for cheat in self.cheats:
                if not cheat.get("enabled", False): continue
                
                lines = cheat["code"].replace('\r', '').split('\n')
                for line in lines:
                    line = re.sub(r'[^a-fA-F0-9]', '', line).upper()
                    if len(line) >= 16:
                        addr = int(line[:8], 16)
                        val = int(line[8:16], 16)
                        
                        if 0x02000000 <= addr <= 0x0203FFFF:
                            self._write_mem(2, addr - 0x02000000, val)
                        elif 0x03000000 <= addr <= 0x03007FFF:
                            self._write_mem(3, addr - 0x03000000, val)

    def _write_mem(self, region_id, offset, val):
        try:
            mem = self.worker.emu.get_memory(region_id)
            if mem and hasattr(mem, 'data'):
                mem.data[offset] = val & 0xFF
                mem.data[offset+1] = (val >> 8) & 0xFF
        except Exception:
            pass

    def load_cht_file(self, filepath):
        self.cheats.clear()
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            cheat_dict = {}
            for line in lines:
                if '=' not in line: continue
                key, val = line.split('=', 1)
                key, val = key.strip(), val.strip().strip('"')
                
                if key.startswith('cheat'):
                    idx_match = re.search(r'cheat(\d+)_', key)
                    if idx_match:
                        idx = int(idx_match.group(1))
                        prop = key.split('_')[1]
                        
                        if idx not in cheat_dict:
                            cheat_dict[idx] = {"desc": "Unknown", "code": "", "type": "Auto", "enabled": False}
                            
                        if prop == "desc": cheat_dict[idx]["desc"] = val
                        elif prop == "code": cheat_dict[idx]["code"] = val.replace('+', '\n')
                        elif prop == "enable": cheat_dict[idx]["enabled"] = (val.lower() == 'true')
            
            for i in sorted(cheat_dict.keys()):
                self.cheats.append(cheat_dict[i])
            return True
        except Exception as e:
            print(f"Cheat load error: {e}")
            return False

    def save_cht_file(self, filepath):
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"cheats = {len(self.cheats)}\n\n")
                for i, cheat in enumerate(self.cheats):
                    f.write(f"cheat{i}_desc = \"{cheat['desc']}\"\n")
                    f.write(f"cheat{i}_code = \"{cheat['code'].replace(chr(10), '+')}\"\n")
                    f.write(f"cheat{i}_enable = {'true' if cheat['enabled'] else 'false'}\n")
            return True
        except Exception:
            return False

class CheatManagerWindow(QMainWindow):
    def __init__(self, engine, config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.setWindowTitle("Cheats Manager")
        self.resize(750, 450)
        self._init_ui()
        self.refresh_table()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        btn_add = QPushButton("Add New Cheat")
        btn_add.clicked.connect(self.add_cheat)
        btn_del = QPushButton("Remove Selected")
        btn_del.clicked.connect(self.remove_cheat)
        btn_load = QPushButton("Load .cht")
        btn_load.clicked.connect(self.load_cheats)
        btn_save = QPushButton("Save .cht")
        btn_save.clicked.connect(self.save_cheats)
        btn_db = QPushButton("🌐 Download from Database")
        btn_db.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        btn_db.clicked.connect(self.open_db_downloader)

        toolbar.addWidget(btn_add)
        toolbar.addWidget(btn_del)
        toolbar.addWidget(btn_load)
        toolbar.addWidget(btn_save)
        toolbar.addWidget(btn_db)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Enabled", "Description", "Type", "Code"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.on_item_changed)
        layout.addWidget(self.table)

    def refresh_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.engine.cheats))
        for i, cheat in enumerate(self.engine.cheats):
            item_cb = QTableWidgetItem()
            item_cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item_cb.setCheckState(Qt.CheckState.Checked if cheat.get("enabled") else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, item_cb)
            
            self.table.setItem(i, 1, QTableWidgetItem(cheat.get("desc", "")))
            
            combo = QComboBox()
            combo.addItems(["Auto", "GameShark", "Action Replay MAX", "CodeBreaker", "Raw"])
            combo.setCurrentText(cheat.get("type", "Auto"))
            combo.currentTextChanged.connect(lambda text, row=i: self.update_type(row, text))
            self.table.setCellWidget(i, 2, combo)
            
            self.table.setItem(i, 3, QTableWidgetItem(cheat.get("code", "").replace('\n', ' ')))
        self.table.blockSignals(False)

    def update_type(self, row, text):
        if row < len(self.engine.cheats):
            self.engine.cheats[row]["type"] = text

    def on_item_changed(self, item):
        row = item.row()
        col = item.column()
        if col == 0:
            self.engine.cheats[row]["enabled"] = (item.checkState() == Qt.CheckState.Checked)
        elif col == 1:
            self.engine.cheats[row]["desc"] = item.text()
        elif col == 3:
            self.engine.cheats[row]["code"] = item.text().replace(' ', '\n')

    def add_cheat(self):
        desc, ok1 = QInputDialog.getText(self, "Cheat Name", "Description:")
        if not ok1: return
        code, ok2 = QInputDialog.getMultiLineText(self, "Cheat Code", "Enter Code:")
        if not ok2: return
        self.engine.cheats.append({"desc": desc, "code": code, "type": "Auto", "enabled": True})
        self.refresh_table()

    def remove_cheat(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.engine.cheats[row]
            self.refresh_table()

    def load_cheats(self):
        cht_dir = self.config.get("paths", "cheats")
        filepath, _ = QFileDialog.getOpenFileName(self, "Load Libretro Cheats", cht_dir, "Cheat Files (*.cht)")
        if filepath and self.engine.load_cht_file(filepath):
            self.refresh_table()

    def save_cheats(self):
        cht_dir = self.config.get("paths", "cheats")
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Libretro Cheats", cht_dir, "Cheat Files (*.cht)")
        if filepath:
            self.engine.save_cht_file(filepath)
            QMessageBox.information(self, "Saved", "Cheats saved successfully.")

    def open_db_downloader(self):
        self.db_dialog = QDialog(self)
        self.db_dialog.setWindowTitle("Public Cheat Database")
        self.db_dialog.resize(400, 500)
        layout = QVBoxLayout(self.db_dialog)
        
        layout.addWidget(QLabel("Fetching game list from Libretro GitHub... Please wait."))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        
        self.dl_btn = QPushButton("Download Selected Cheats")
        self.dl_btn.setEnabled(False)
        layout.addWidget(self.dl_btn)
        
        self.fetcher = CheatDownloader()
        self.fetcher.results_ready.connect(self.populate_db_list)
        self.fetcher.error_occurred.connect(lambda e: QMessageBox.warning(self, "Error", f"Failed to fetch: {e}"))
        self.fetcher.start()
        
        self.db_dialog.exec()

    def populate_db_list(self, games):
        self.db_games = games
        for game in games:
            self.list_widget.addItem(game["name"])
            
        self.dl_btn.setEnabled(True)
        self.dl_btn.clicked.connect(self.download_selected_cheat)

    def download_selected_cheat(self):
        row = self.list_widget.currentRow()
        if row < 0: return
        
        game = self.db_games[row]
        try:
            req = urllib.request.Request(game["url"], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                content = response.read().decode('utf-8')
                
            cht_dir = self.config.get("paths", "cheats")
            os.makedirs(cht_dir, exist_ok=True)
            save_path = os.path.join(cht_dir, f"{game['name']}.cht")
            
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(content)
                
            if self.engine.load_cht_file(save_path):
                self.refresh_table()
                QMessageBox.information(self, "Success", f"Downloaded and loaded {len(self.engine.cheats)} cheats for {game['name']}!")
                self.db_dialog.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Download failed: {e}")
# glossary_dialog.py
import os
import json

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget, QWidget,
    QMessageBox, QFileDialog, QComboBox, QCheckBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt

from .engines import LANG_NAMES
from .glossary import GlossaryAndCacheStore


class TranslationGlossaryDialog(QDialog):
    """Per-Game Glossary & Translation Cache Editor."""

    def __init__(self, rom_path: str, config, game_code: str = "", parent=None):
        super().__init__(parent)
        self.rom_path = rom_path
        self.config = config
        self.game_code = game_code

        trans_dir = self.config.get("paths", "translations") or "translations"
        self.store = GlossaryAndCacheStore(self.rom_path, trans_dir=trans_dir, game_code=self.game_code)

        self.setWindowTitle(f"Per-Game Glossary & Translation Cache [{self.store.game_id}]")
        self.resize(920, 600)

        self._init_ui()
        self.refresh_glossary_table()
        self.refresh_cache_table()

    def _init_ui(self):
        root = QVBoxLayout(self)

        info = QLabel(
            f"<b>Game ID:</b> <code>{self.store.game_id}</code> &nbsp;|&nbsp; "
            f"<b>Glossary:</b> <code>{os.path.basename(self.store.glossary_path)}</code> &nbsp;|&nbsp; "
            f"<b>Cache:</b> <code>{os.path.basename(self.store.cache_path)}</code>"
        )
        info.setStyleSheet("background: #181C24; color: #CFD8DC; padding: 8px; border-radius: 5px;")
        root.addWidget(info)

        self.tabs = QTabWidget()

        # =====================================================================
        # TAB 1: PER-GAME GLOSSARY
        # =====================================================================
        tab_glossary = QWidget()
        gl_layout = QVBoxLayout(tab_glossary)

        add_row = QHBoxLayout()
        self.gl_src_edit = QLineEdit()
        self.gl_src_edit.setPlaceholderText("Original term / name (e.g. 曹操 or はい)")
        add_row.addWidget(self.gl_src_edit, 2)

        self.gl_tgt_edit = QLineEdit()
        self.gl_tgt_edit.setPlaceholderText("Target translation (e.g. Tào Tháo or Có)")
        add_row.addWidget(self.gl_tgt_edit, 2)

        btn_add_term = QPushButton("+ Add / Update Term")
        btn_add_term.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 5px 10px;")
        btn_add_term.clicked.connect(self.add_glossary_term)
        add_row.addWidget(btn_add_term)
        gl_layout.addLayout(add_row)

        self.gl_table = QTableWidget(0, 2)
        self.gl_table.setHorizontalHeaderLabels(["Original Term", "Translated Term"])
        self.gl_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.gl_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.gl_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.gl_table.itemChanged.connect(self.on_glossary_item_changed)
        gl_layout.addWidget(self.gl_table)

        gl_btns = QHBoxLayout()
        btn_del_term = QPushButton("Delete Selected")
        btn_del_term.clicked.connect(self.delete_glossary_term)
        gl_btns.addWidget(btn_del_term)

        btn_import_gl = QPushButton("Import JSON...")
        btn_import_gl.clicked.connect(self.import_glossary_json)
        gl_btns.addWidget(btn_import_gl)

        btn_export_gl = QPushButton("Export JSON...")
        btn_export_gl.clicked.connect(self.export_glossary_json)
        gl_btns.addWidget(btn_export_gl)

        gl_btns.addStretch()
        gl_layout.addLayout(gl_btns)
        self.tabs.addTab(tab_glossary, "📖 Per-Game Glossary")

        # =====================================================================
        # TAB 2: TRANSLATION CACHE
        # =====================================================================
        tab_cache = QWidget()
        ca_layout = QVBoxLayout(tab_cache)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search:"))
        self.cache_search_edit = QLineEdit()
        self.cache_search_edit.setPlaceholderText("Filter by original, translated, or speaker text...")
        self.cache_search_edit.textChanged.connect(self.refresh_cache_table)
        filter_row.addWidget(self.cache_search_edit)

        self.cache_count_lbl = QLabel("0 entries")
        self.cache_count_lbl.setStyleSheet("color: #90A4AE;")
        filter_row.addWidget(self.cache_count_lbl)
        ca_layout.addLayout(filter_row)

        self.cache_table = QTableWidget(0, 5)
        self.cache_table.setHorizontalHeaderLabels(["Category", "Speaker", "Original Text", "Translated Text", "Locked"])
        self.cache_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.cache_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.cache_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.cache_table.itemChanged.connect(self.on_cache_item_changed)
        ca_layout.addWidget(self.cache_table)

        ca_btns = QHBoxLayout()
        btn_promote = QPushButton("⬆ Promote Selected to Glossary")
        btn_promote.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold;")
        btn_promote.clicked.connect(self.promote_cache_to_glossary)
        ca_btns.addWidget(btn_promote)

        btn_del_cache = QPushButton("Delete Selected")
        btn_del_cache.clicked.connect(self.delete_selected_cache)
        ca_btns.addWidget(btn_del_cache)

        btn_clear_unlocked = QPushButton("Clear Unlocked Cache")
        btn_clear_unlocked.clicked.connect(self.clear_unlocked_cache)
        ca_btns.addWidget(btn_clear_unlocked)

        ca_btns.addStretch()
        ca_layout.addLayout(ca_btns)
        self.tabs.addTab(tab_cache, "⚡ Translation Cache")

        root.addWidget(self.tabs)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)

    # =========================================================================
    # GLOSSARY ACTIONS
    # =========================================================================
    def refresh_glossary_table(self):
        self.gl_table.blockSignals(True)
        items = sorted(self.store.terms.items(), key=lambda kv: kv[0])
        self.gl_table.setRowCount(len(items))
        for row, (src, tgt) in enumerate(items):
            src_item = QTableWidgetItem(src)
            src_item.setData(Qt.ItemDataRole.UserRole, src)
            tgt_item = QTableWidgetItem(tgt)
            self.gl_table.setItem(row, 0, src_item)
            self.gl_table.setItem(row, 1, tgt_item)
        self.gl_table.blockSignals(False)

    def add_glossary_term(self):
        src = self.gl_src_edit.text().strip()
        tgt = self.gl_tgt_edit.text().strip()
        if not src or not tgt:
            QMessageBox.warning(self, "Notice", "Please enter both the original term and its translation.")
            return
        self.store.set_term(src, tgt, save_now=True)
        self.gl_src_edit.clear()
        self.gl_tgt_edit.clear()
        self.refresh_glossary_table()

    def on_glossary_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        src_item = self.gl_table.item(row, 0)
        tgt_item = self.gl_table.item(row, 1)
        if not src_item or not tgt_item:
            return

        old_src = src_item.data(Qt.ItemDataRole.UserRole)
        new_src = src_item.text().strip()
        new_tgt = tgt_item.text().strip()

        if not new_src or not new_tgt:
            self.refresh_glossary_table()
            return

        if old_src and old_src != new_src:
            self.store.delete_term(old_src, save_now=False)
        self.store.set_term(new_src, new_tgt, save_now=True)
        src_item.setData(Qt.ItemDataRole.UserRole, new_src)

    def delete_glossary_term(self):
        rows = self.gl_table.selectionModel().selectedRows()
        if not rows:
            return
        for r in rows:
            src_item = self.gl_table.item(r.row(), 0)
            if src_item:
                self.store.delete_term(src_item.text().strip(), save_now=False)
        self.store.save_glossary()
        self.refresh_glossary_table()

    def import_glossary_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Glossary JSON", self.store.trans_dir, "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            terms = data.get("terms", data) if isinstance(data, dict) else {}
            if not isinstance(terms, dict):
                raise ValueError("Expected a JSON object mapping source terms to translations.")
            added = 0
            for k, v in terms.items():
                if str(k).strip() and str(v).strip():
                    self.store.set_term(str(k), str(v), save_now=False)
                    added += 1
            self.store.save_glossary()
            self.refresh_glossary_table()
            QMessageBox.information(self, "Imported", f"Imported {added} glossary term(s).")
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import glossary:\n{e}")

    def export_glossary_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Glossary JSON", self.store.glossary_path, "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"game_id": self.store.game_id, "terms": self.store.terms}, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "Exported", f"Exported glossary to:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export glossary:\n{e}")

    # =========================================================================
    # CACHE ACTIONS
    # =========================================================================
    def refresh_cache_table(self):
        query = self.cache_search_edit.text().strip().lower() if hasattr(self, "cache_search_edit") else ""
        filtered = []
        for key, entry in self.store.entries.items():
            if query:
                hay = " ".join([
                    entry.get("original", ""),
                    entry.get("translated", ""),
                    entry.get("speaker_orig", ""),
                    entry.get("speaker_trans", ""),
                ]).lower()
                if query not in hay:
                    continue
            filtered.append((key, entry))

        self.cache_table.blockSignals(True)
        self.cache_table.setRowCount(len(filtered))
        for row, (key, entry) in enumerate(filtered):
            cat_item = QTableWidgetItem(entry.get("category", "dialogue"))
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            cat_item.setData(Qt.ItemDataRole.UserRole, key)
            self.cache_table.setItem(row, 0, cat_item)

            sp_txt = entry.get("speaker_trans") or entry.get("speaker_orig") or ""
            self.cache_table.setItem(row, 1, QTableWidgetItem(sp_txt))

            orig_item = QTableWidgetItem(entry.get("original", ""))
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.cache_table.setItem(row, 2, orig_item)

            self.cache_table.setItem(row, 3, QTableWidgetItem(entry.get("translated", "")))

            lock_item = QTableWidgetItem()
            lock_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            lock_item.setCheckState(Qt.CheckState.Checked if entry.get("locked") else Qt.CheckState.Unchecked)
            self.cache_table.setItem(row, 4, lock_item)

        self.cache_table.blockSignals(False)
        self.cache_count_lbl.setText(f"{len(filtered)} / {len(self.store.entries)} entries")

    def on_cache_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        col = item.column()
        cat_item = self.cache_table.item(row, 0)
        if not cat_item:
            return
        key = cat_item.data(Qt.ItemDataRole.UserRole)
        entry = self.store.entries.get(key)
        if not entry:
            return

        if col == 1:
            entry["speaker_trans"] = item.text().strip()
            entry["locked"] = True
        elif col == 3:
            entry["translated"] = item.text().strip()
            entry["locked"] = True
        elif col == 4:
            entry["locked"] = (item.checkState() == Qt.CheckState.Checked)

        self.store.save_cache()
        if col in (1, 3):
            self.cache_table.blockSignals(True)
            lock_item = self.cache_table.item(row, 4)
            if lock_item:
                lock_item.setCheckState(Qt.CheckState.Checked if entry["locked"] else Qt.CheckState.Unchecked)
            self.cache_table.blockSignals(False)

    def promote_cache_to_glossary(self):
        rows = self.cache_table.selectionModel().selectedRows()
        if not rows:
            return
        promoted = 0
        for r in rows:
            cat_item = self.cache_table.item(r.row(), 0)
            if not cat_item:
                continue
            key = cat_item.data(Qt.ItemDataRole.UserRole)
            entry = self.store.entries.get(key)
            if not entry:
                continue
            orig = entry.get("original", "").strip()
            trans = entry.get("translated", "").strip()
            if orig and trans:
                self.store.set_term(orig, trans, save_now=False)
                promoted += 1
            sp_orig = entry.get("speaker_orig", "").strip()
            sp_tr = entry.get("speaker_trans", "").strip()
            if sp_orig and sp_tr:
                self.store.set_term(sp_orig, sp_tr, save_now=False)

        if promoted:
            self.store.save_glossary()
            self.refresh_glossary_table()
            QMessageBox.information(self, "Promoted", f"Added {promoted} entry/entries to the Per-Game Glossary.")

    def delete_selected_cache(self):
        rows = self.cache_table.selectionModel().selectedRows()
        if not rows:
            return
        for r in rows:
            cat_item = self.cache_table.item(r.row(), 0)
            if cat_item:
                key = cat_item.data(Qt.ItemDataRole.UserRole)
                self.store.delete_entry(key, save_now=False)
        self.store.save_cache()
        self.refresh_cache_table()

    def clear_unlocked_cache(self):
        reply = QMessageBox.question(
            self,
            "Clear Unlocked Cache",
            "Clear all unlocked AI translations and frame snapshots?\n(Manually edited/locked entries and your Glossary will be kept.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.store.clear_unlocked_cache()
            self.refresh_cache_table()


class AITranslationConfigDialog(QDialog):
    """Configures AI translation languages, fullscreen overlay, and opens the Glossary editor."""

    def __init__(self, config, parent=None, rom_path: str = "", game_code: str = ""):
        super().__init__(parent)
        self.config = config
        self.rom_path = rom_path
        self.game_code = game_code
        self.setWindowTitle("Configure AI Translation")
        self.resize(400, 220)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source Language:"))
        self.src_combo = QComboBox()
        for code, name in LANG_NAMES.items():
            self.src_combo.addItem(f"{name} ({code})", code)
        cur_src = self.config.get("ai_translation", "source_lang", "ja")
        idx = self.src_combo.findData(cur_src)
        if idx >= 0:
            self.src_combo.setCurrentIndex(idx)
        src_row.addWidget(self.src_combo)
        layout.addLayout(src_row)

        tgt_row = QHBoxLayout()
        tgt_row.addWidget(QLabel("Target Language:"))
        self.tgt_combo = QComboBox()
        for code, name in LANG_NAMES.items():
            self.tgt_combo.addItem(f"{name} ({code})", code)
        cur_tgt = self.config.get("ai_translation", "target_lang", "vi")
        idx = self.tgt_combo.findData(cur_tgt)
        if idx >= 0:
            self.tgt_combo.setCurrentIndex(idx)
        tgt_row.addWidget(self.tgt_combo)
        layout.addLayout(tgt_row)

        self.overlay_cb = QCheckBox("Show Floating Subtitle Overlay in Fullscreen Mode")
        self.overlay_cb.setChecked(bool(self.config.get("ai_translation", "overlay_enabled", True)))
        layout.addWidget(self.overlay_cb)

        btn_glossary = QPushButton("📖 Open Per-Game Glossary & Cache...")
        btn_glossary.setEnabled(bool(self.rom_path))
        btn_glossary.clicked.connect(self._open_glossary)
        layout.addWidget(btn_glossary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_glossary(self):
        if not self.rom_path:
            return
        dlg = TranslationGlossaryDialog(self.rom_path, self.config, game_code=self.game_code, parent=self)
        dlg.exec()

    def _save(self):
        self.config.set("ai_translation", "source_lang", self.src_combo.currentData())
        self.config.set("ai_translation", "target_lang", self.tgt_combo.currentData())
        self.config.set("ai_translation", "overlay_enabled", self.overlay_cb.isChecked())
        self.accept()
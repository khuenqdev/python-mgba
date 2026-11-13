# state_browser.py
import os
import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame,
    QLabel, QPushButton, QMessageBox
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt, pyqtSignal


class ClickableThumbLabel(QLabel):
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class SaveStateBrowserDialog(QDialog):
    """Visual 3x3 Save State Slot Browser with PNG thumbnails and slot management."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.mw = main_window
        rom_name = os.path.splitext(os.path.basename(self.mw.current_rom or "ROM"))[0]
        self.setWindowTitle(f"Save State Slot Browser - {rom_name}")
        self.resize(780, 680)

        self.slot_widgets = {}
        self._init_ui()
        self.refresh_all_slots()

    def _init_ui(self):
        root = QVBoxLayout(self)

        header = QLabel(
            "<b>Save State Manager (Slots 1–9)</b> — "
            "Click a slot to select it, or double-click a thumbnail to load immediately."
        )
        header.setStyleSheet("color: #CFD8DC; padding: 4px 2px;")
        root.addWidget(header)

        grid = QGridLayout()
        grid.setSpacing(10)

        for slot in range(1, 10):
            card = QFrame()
            card.setFrameShape(QFrame.Shape.StyledPanel)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 8, 8, 8)
            card_layout.setSpacing(6)

            title_lbl = QLabel(f"Slot {slot}")
            title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_layout.addWidget(title_lbl)

            thumb_lbl = ClickableThumbLabel("Empty")
            thumb_lbl.setFixedSize(210, 140)
            thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_lbl.setStyleSheet("background-color: #11141A; color: #607D8B; border: 1px solid #2A303C;")
            thumb_lbl.clicked.connect(lambda s=slot: self._select_slot(s))
            thumb_lbl.double_clicked.connect(lambda s=slot: self._on_double_click_slot(s))
            card_layout.addWidget(thumb_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

            meta_lbl = QLabel("Empty")
            meta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            meta_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #90A4AE;")
            card_layout.addWidget(meta_lbl)

            btn_row = QHBoxLayout()
            btn_load = QPushButton("Load")
            btn_load.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 4px;")
            btn_load.clicked.connect(lambda _, s=slot: self._load_slot(s))
            btn_row.addWidget(btn_load)

            btn_save = QPushButton("Save")
            btn_save.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 4px;")
            btn_save.clicked.connect(lambda _, s=slot: self._save_slot(s))
            btn_row.addWidget(btn_save)

            btn_del = QPushButton("Delete")
            btn_del.setStyleSheet("background-color: #B71C1C; color: white; padding: 4px;")
            btn_del.clicked.connect(lambda _, s=slot: self._delete_slot(s))
            btn_row.addWidget(btn_del)

            card_layout.addLayout(btn_row)

            row_idx = (slot - 1) // 3
            col_idx = (slot - 1) % 3
            grid.addWidget(card, row_idx, col_idx)

            self.slot_widgets[slot] = {
                "card": card,
                "title": title_lbl,
                "thumb": thumb_lbl,
                "meta": meta_lbl,
                "btn_load": btn_load,
                "btn_save": btn_save,
                "btn_del": btn_del,
            }

        root.addLayout(grid)

        footer = QHBoxLayout()
        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh_all_slots)
        footer.addWidget(btn_refresh)
        footer.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)
        root.addLayout(footer)

    def refresh_all_slots(self):
        has_worker = bool(self.mw.worker)
        active_slot = int(getattr(self.mw, "current_slot", 1) or 1)

        for slot, w in self.slot_widgets.items():
            is_active = (slot == active_slot)
            border_color = "#00E676" if is_active else "#2C3340"
            w["card"].setStyleSheet(
                f"QFrame {{ background-color: #181C24; border: 2px solid {border_color}; border-radius: 6px; }}"
            )
            active_badge = ' <span style="color:#00E676;">[Active]</span>' if is_active else ""
            w["title"].setText(f"<b>Slot {slot}</b>{active_badge}")

            state_path = self.mw.get_state_path(slot)
            exists = bool(state_path and os.path.exists(state_path))

            w["btn_load"].setEnabled(exists and has_worker)
            w["btn_save"].setEnabled(has_worker)
            w["btn_del"].setEnabled(exists)

            if exists:
                thumb_path = state_path + ".png"
                if os.path.exists(thumb_path):
                    pix = QPixmap(thumb_path)
                    if not pix.isNull():
                        w["thumb"].setPixmap(
                            pix.scaled(
                                w["thumb"].size(),
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.FastTransformation,
                            )
                        )
                    else:
                        w["thumb"].setPixmap(QPixmap())
                        w["thumb"].setText("Preview Unavailable")
                else:
                    w["thumb"].setPixmap(QPixmap())
                    w["thumb"].setText("No PNG Preview\n(Legacy Save State)")

                try:
                    mtime = os.path.getmtime(state_path)
                    ts_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                    size_kb = os.path.getsize(state_path) / 1024.0
                    w["meta"].setText(f"{ts_str}  ({size_kb:.0f} KB)")
                except OSError:
                    w["meta"].setText("Saved")
            else:
                w["thumb"].setPixmap(QPixmap())
                w["thumb"].setText("Empty Slot")
                w["meta"].setText("No save state in this slot")

    def _select_slot(self, slot: int):
        self.mw.set_active_slot(slot)
        self.refresh_all_slots()

    def _on_double_click_slot(self, slot: int):
        state_path = self.mw.get_state_path(slot)
        if state_path and os.path.exists(state_path) and self.mw.worker:
            self._load_slot(slot)

    def _load_slot(self, slot: int):
        self.mw.set_active_slot(slot)
        self.mw.quick_load_state(slot)
        self.accept()

    def _save_slot(self, slot: int):
        state_path = self.mw.get_state_path(slot)
        if state_path and os.path.exists(state_path):
            reply = QMessageBox.question(
                self,
                "Overwrite Save State",
                f"Overwrite existing save state in Slot {slot}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.mw.set_active_slot(slot)
        self.mw.quick_save_state(slot)
        self.refresh_all_slots()

    def _delete_slot(self, slot: int):
        state_path = self.mw.get_state_path(slot)
        if not state_path or not os.path.exists(state_path):
            return

        reply = QMessageBox.question(
            self,
            "Delete Save State",
            f"Permanently delete Slot {slot} and its thumbnail?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for p in (state_path, state_path + ".png"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError as e:
                    QMessageBox.warning(self, "Delete Error", f"Failed to remove {p}:\n{e}")

        self.refresh_all_slots()
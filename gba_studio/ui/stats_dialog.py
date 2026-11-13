# stats_dialog.py
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PyQt6.QtCore import Qt

from .sensor_hud import format_playtime


def _format_human_time(seconds: float) -> str:
    sec = max(0, int(seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s ({format_playtime(sec)})"
    return f"{m}m {s:02d}s"


class GameStatsDialog(QDialog):
    """Displays per-game playtime, launch count, and timestamps from profiles/<game_code>.json."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.mw = main_window
        self.config = main_window.config
        self.setWindowTitle("Per-Game Playtime & Statistics")
        self.resize(780, 440)
        self._init_ui()
        self.refresh_stats()

    def _init_ui(self):
        root = QVBoxLayout(self)

        self.summary_lbl = QLabel("Loading statistics...")
        self.summary_lbl.setStyleSheet(
            "background: #161A22; color: #A5D6A7; font-weight: bold; "
            "padding: 8px 10px; border-radius: 5px; border: 1px solid #263042;"
        )
        root.addWidget(self.summary_lbl)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Game ID", "Title", "Total Playtime", "Launches", "Last Session", "Last Played"
        ])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.table)

        btns = QHBoxLayout()
        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh_stats)
        btns.addWidget(btn_refresh)

        btn_reset = QPushButton("🗑 Reset Selected Stats")
        btn_reset.clicked.connect(self.reset_selected)
        btns.addWidget(btn_reset)

        btns.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        root.addLayout(btns)

    def refresh_stats(self):
        if hasattr(self.mw, "_flush_playtime"):
            self.mw._flush_playtime()

        rows = self.config.list_all_game_stats()
        self.rows_data = rows
        self.table.setRowCount(len(rows))

        total_sec = 0.0
        total_launches = 0

        for idx, item in enumerate(rows):
            pt = float(item.get("playtime_seconds", 0.0) or 0.0)
            lc = int(item.get("launch_count", 0) or 0)
            last_sess = float(item.get("last_session_seconds", 0.0) or 0.0)
            last_played = str(item.get("last_played") or "--").replace("T", " ")

            total_sec += pt
            total_launches += lc

            self.table.setItem(idx, 0, QTableWidgetItem(item["profile_id"]))
            self.table.setItem(idx, 1, QTableWidgetItem(item["game_title"]))
            self.table.setItem(idx, 2, QTableWidgetItem(_format_human_time(pt)))
            self.table.setItem(idx, 3, QTableWidgetItem(str(lc)))
            self.table.setItem(idx, 4, QTableWidgetItem(_format_human_time(last_sess)))
            self.table.setItem(idx, 5, QTableWidgetItem(last_played))

        self.summary_lbl.setText(
            f"Tracked Games: {len(rows)}   |   Total Playtime: {_format_human_time(total_sec)}   |   Total Launches: {total_launches}"
        )

    def reset_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows_data):
            return
        pid = self.rows_data[row]["profile_id"]
        reply = QMessageBox.question(
            self,
            "Reset Statistics",
            f"Reset playtime & launch count statistics for [{pid}]?\n(Your custom per-game settings will be kept.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.config.reset_game_stats(pid)
            if getattr(self.mw, "_active_stats_pid", None) == pid:
                self.mw._base_playtime_sec = 0.0
                self.mw._session_playtime_sec = 0.0
                self.mw._unsaved_playtime_sec = 0.0
                self.mw._active_launch_count = 0
                self.mw.sensor_hud.on_playtime_updated(0.0, 0)
            self.refresh_stats()

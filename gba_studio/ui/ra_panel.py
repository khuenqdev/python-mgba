# ra_panel.py
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QScrollArea, QFrame, QComboBox, QSizePolicy
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt, pyqtSignal


class AchievementCardWidget(QFrame):
    """Individual achievement row inside the right-side RetroAchievements panel."""

    def __init__(self, item, ra_manager, parent=None):
        super().__init__(parent)
        self.item = item
        self.ra = ra_manager
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(8)

        self.badge_lbl = QLabel()
        self.badge_lbl.setFixedSize(44, 44)
        self.badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_lbl.setStyleSheet("background: #10131A; border: 1px solid #2A3140; border-radius: 4px;")
        root.addWidget(self.badge_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(3)

        top_row = QHBoxLayout()
        self.title_lbl = QLabel(self.item.title)
        self.title_lbl.setWordWrap(True)
        top_row.addWidget(self.title_lbl, 1)

        self.pts_lbl = QLabel(f"{self.item.points}p")
        self.pts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top_row.addWidget(self.pts_lbl)
        col.addLayout(top_row)

        self.desc_lbl = QLabel(self.item.description)
        self.desc_lbl.setWordWrap(True)
        col.addWidget(self.desc_lbl)

        self.meta_lbl = QLabel("")
        col.addWidget(self.meta_lbl)

        self.prog_row = QHBoxLayout()
        self.prog_bar = QProgressBar()
        self.prog_bar.setFixedHeight(8)
        self.prog_bar.setTextVisible(False)
        self.prog_txt = QLabel("")
        self.prog_txt.setStyleSheet("font-family: monospace; font-size: 10px; color: #80CBC4;")
        self.prog_row.addWidget(self.prog_bar, 1)
        self.prog_row.addWidget(self.prog_txt)
        col.addLayout(self.prog_row)

        root.addLayout(col, 1)

    def refresh_badge(self):
        path = self.ra.get_badge_path(self.item.badge_name, locked=not self.item.unlocked)
        if os.path.exists(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.badge_lbl.setPixmap(
                    pix.scaled(42, 42, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                return
        self.badge_lbl.setPixmap(QPixmap())
        self.badge_lbl.setText("🏆" if self.item.unlocked else "🔒")

    def refresh_progress(self, val: int, target: int):
        show_prog = (not self.item.unlocked) and (self.item.has_measured or target > 0)
        self.prog_bar.setVisible(show_prog)
        self.prog_txt.setVisible(show_prog)
        if show_prog:
            safe_t = max(1, int(target))
            safe_v = max(0, min(safe_t, int(val)))
            self.prog_bar.setRange(0, safe_t)
            self.prog_bar.setValue(safe_v)
            self.prog_txt.setText(f"{safe_v}/{safe_t}")

    def refresh(self):
        self.refresh_badge()
        unlocked = self.item.unlocked
        hc = self.item.unlocked_hardcore

        if unlocked:
            border_col = "#FFD54F" if hc else "#4CAF50"
            bg_col = "#242014" if hc else "#16261C"
            title_col = "#FFE082" if hc else "#A5D6A7"
            pts_col = "#FFD54F" if hc else "#81C784"
            status_txt = "★ UNLOCKED (HC)" if hc else "✓ UNLOCKED"
        else:
            border_col = "#28303E"
            bg_col = "#151922"
            title_col = "#CFD8DC"
            pts_col = "#90A4AE"
            status_txt = "Locked"

        cat = self.item.category.replace("_", " ").title() if self.item.category else ""
        meta_parts = [status_txt]
        if cat:
            meta_parts.append(cat)

        self.setStyleSheet(
            f"AchievementCardWidget {{ background-color: {bg_col}; border: 1px solid {border_col}; border-radius: 6px; }}"
        )
        self.title_lbl.setStyleSheet(f"color: {title_col}; font-weight: bold; font-size: 12px;")
        self.pts_lbl.setStyleSheet(f"color: {pts_col}; font-weight: bold; font-size: 11px;")
        self.desc_lbl.setStyleSheet("color: #B0BEC5; font-size: 11px;")
        self.meta_lbl.setStyleSheet(f"color: {pts_col}; font-size: 10px; font-weight: bold;")
        self.meta_lbl.setText("  •  ".join(meta_parts))

        self.prog_bar.setStyleSheet(
            "QProgressBar { background: #10131A; border: 1px solid #2A3140; border-radius: 3px; }"
            "QProgressBar::chunk { background-color: #26A69A; border-radius: 2px; }"
        )
        self.refresh_progress(self.item.measured_val, self.item.measured_target)


class AchievementsPanelWidget(QWidget):
    """Right-hand dock panel displaying game achievements, earned highlights & progress."""

    open_settings_requested = pyqtSignal()

    def __init__(self, ra_manager, parent=None):
        super().__init__(parent)
        self.ra = ra_manager
        self.cards_by_id = {}
        self.cards_list = []
        self.setMinimumWidth(250)
        self._init_ui()

        self.ra.session_updated.connect(self.on_session_updated)
        self.ra.achievement_unlocked.connect(self.on_achievement_unlocked)
        self.ra.progress_updated.connect(self.on_progress_updated)
        self.ra.badge_downloaded.connect(self.on_badge_downloaded)

    def _init_ui(self):
        self.setStyleSheet("background-color: #101218; color: #E0E0E0;")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Header Summary Card
        hdr_frame = QFrame()
        hdr_frame.setStyleSheet(
            "QFrame { background-color: #161A23; border: 1px solid #283040; border-radius: 6px; }"
        )
        hdr_lay = QVBoxLayout(hdr_frame)
        hdr_lay.setContentsMargins(10, 8, 10, 8)
        hdr_lay.setSpacing(4)

        top_row = QHBoxLayout()
        self.lbl_header = QLabel("🏆 RetroAchievements")
        self.lbl_header.setStyleSheet("color: #FFD54F; font-weight: bold; font-size: 13px; border: none;")
        top_row.addWidget(self.lbl_header, 1)

        btn_cfg = QPushButton("⚙")
        btn_cfg.setToolTip("RetroAchievements Account & Settings")
        btn_cfg.setFixedSize(26, 24)
        btn_cfg.setStyleSheet("background-color: #232A38; color: #CFD8DC; border: 1px solid #354054; border-radius: 4px;")
        btn_cfg.clicked.connect(self.open_settings_requested.emit)
        top_row.addWidget(btn_cfg)
        hdr_lay.addLayout(top_row)

        self.lbl_user = QLabel("Not Connected")
        self.lbl_user.setStyleSheet("color: #90CAF9; font-size: 11px; border: none;")
        self.lbl_user.setWordWrap(True)
        hdr_lay.addWidget(self.lbl_user)

        self.lbl_progress = QLabel("0 / 0 Unlocked (0%)")
        self.lbl_progress.setStyleSheet("color: #A5D6A7; font-weight: bold; font-size: 11px; border: none;")
        hdr_lay.addWidget(self.lbl_progress)

        self.total_prog_bar = QProgressBar()
        self.total_prog_bar.setFixedHeight(10)
        self.total_prog_bar.setTextVisible(False)
        self.total_prog_bar.setRange(0, 1)
        self.total_prog_bar.setValue(0)
        self.total_prog_bar.setStyleSheet(
            "QProgressBar { background: #0E1117; border: 1px solid #283040; border-radius: 4px; }"
            "QProgressBar::chunk { background-color: #4CAF50; border-radius: 3px; }"
        )
        hdr_lay.addWidget(self.total_prog_bar)

        root.addWidget(hdr_frame)

        # Filter Bar
        filt_row = QHBoxLayout()
        filt_row.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All Achievements", "Earned Only", "Locked Only"])
        self.filter_combo.setStyleSheet("background: #181D27; color: #E0E0E0; border: 1px solid #2A3242; padding: 3px;")
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        filt_row.addWidget(self.filter_combo, 1)
        root.addLayout(filt_row)

        # Scrollable Cards List
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 2, 0)
        self.list_layout.setSpacing(6)

        self.empty_lbl = QLabel(
            "Load a supported GBA ROM and sign in to RetroAchievements to track achievements."
        )
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet("color: #78909C; padding: 20px;")
        self.list_layout.addWidget(self.empty_lbl)
        self.list_layout.addStretch()

        self.scroll.setWidget(self.list_container)
        root.addWidget(self.scroll, 1)

    def _clear_cards(self):
        for card in self.cards_list:
            self.list_layout.removeWidget(card)
            card.deleteLater()
        self.cards_list.clear()
        self.cards_by_id.clear()

    def _update_summary_counts(self):
        items = self.ra.achievements
        total = len(items)
        earned = sum(1 for a in items if a.unlocked)
        total_pts = sum(a.points for a in items)
        earned_pts = sum(a.points for a in items if a.unlocked)
        pct = int((earned * 100) / total) if total > 0 else 0

        self.lbl_progress.setText(f"{earned} / {total} Unlocked ({pct}%)  •  {earned_pts}/{total_pts} pts")
        self.total_prog_bar.setRange(0, max(1, total))
        self.total_prog_bar.setValue(earned)

    def on_session_updated(self, info: dict):
        status = info.get("status", "OFFLINE")
        user = info.get("user") or "Guest"
        score = int(info.get("score", 0) or 0)
        hc = bool(info.get("hardcore", False))
        mode_txt = "HARDCORE" if hc else "SOFTCORE"
        msg = info.get("message", "")
        title = info.get("game_title", "")

        if status == "ACTIVE":
            self.lbl_header.setText(f"🏆 {title}" if title else "🏆 RetroAchievements")
            self.lbl_user.setText(f"{user} ({score:,} pts)  •  [{mode_txt}]")
        else:
            self.lbl_header.setText("🏆 RetroAchievements")
            self.lbl_user.setText(msg or status)

        self._clear_cards()
        items = info.get("achievements", []) or []

        if not items:
            self.empty_lbl.setText(msg or "No achievements loaded.")
            self.empty_lbl.setVisible(True)
            self._update_summary_counts()
            return

        self.empty_lbl.setVisible(False)
        # Sort earned first or preserve natural set order? Preserve set order with earned highlighted clearly
        for item in items:
            card = AchievementCardWidget(item, self.ra, self.list_container)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self.cards_list.append(card)
            self.cards_by_id[item.ach_id] = card

        self._update_summary_counts()
        self._apply_filter()

    def on_achievement_unlocked(self, payload: dict):
        aid = int(payload.get("id", 0))
        card = self.cards_by_id.get(aid)
        if card:
            card.refresh()
        self._update_summary_counts()
        self._apply_filter()

    def on_progress_updated(self, changes: list):
        for aid, val, target in changes:
            card = self.cards_by_id.get(aid)
            if card:
                card.refresh_progress(val, target)

    def on_badge_downloaded(self, badge_name: str):
        for card in self.cards_list:
            if card.item.badge_name == badge_name:
                card.refresh_badge()

    def _apply_filter(self):
        mode = self.filter_combo.currentIndex()  # 0=All, 1=Earned, 2=Locked
        for card in self.cards_list:
            if mode == 1:
                card.setVisible(card.item.unlocked)
            elif mode == 2:
                card.setVisible(not card.item.unlocked)
            else:
                card.setVisible(True)
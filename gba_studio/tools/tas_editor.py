# tas_editor.py
import os
import zlib
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QAbstractScrollArea, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QMessageBox, QMenu,
    QCheckBox, QSpinBox, QFrame
)
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QKeySequence, QShortcut
from PyQt6.QtCore import Qt, QRect, pyqtSignal

from ..core.session_recorder import (
    SessionRecorder,
    TAS_BUTTON_ORDER,
    tas_mask_to_buttons,
)

TAS_COLUMN_META = [
    ("UP", "▲", QColor(38, 166, 154)),
    ("DOWN", "▼", QColor(38, 166, 154)),
    ("LEFT", "◀", QColor(38, 166, 154)),
    ("RIGHT", "▶", QColor(38, 166, 154)),
    ("A", "A", QColor(76, 175, 80)),
    ("B", "B", QColor(239, 83, 80)),
    ("L", "L", QColor(255, 167, 38)),
    ("R", "R", QColor(255, 167, 38)),
    ("START", "ST", QColor(171, 71, 188)),
    ("SELECT", "SL", QColor(126, 87, 194)),
]


class PianoRollCanvas(QAbstractScrollArea):
    """Virtualized BizHawk/FCEUX-style Piano-Roll TAS Input Grid."""

    timeline_edited = pyqtSignal(int)      # earliest modified frame index
    seek_requested = pyqtSignal(int)       # target frame index
    selection_changed = pyqtSignal(int, int)

    HEADER_H = 28
    ROW_H = 20
    FRAME_COL_W = 86
    GZ_COL_W = 14
    BTN_COL_W = 38

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.sel_start = 0
        self.sel_end = 0
        self._dragging_paint = False
        self._paint_col = -1
        self._paint_target_val = True
        self._dragging_sel = False
        self._last_painted_row = -1

        self.font_mono = QFont("Cascadia Code, Consolas, Courier New, monospace", 9)
        self.font_header = QFont("Segoe UI, Arial, sans-serif", 9, QFont.Weight.Bold)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setStyleSheet("background-color: #11141B;")
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())

    def total_grid_width(self) -> int:
        return self.FRAME_COL_W + self.GZ_COL_W + (len(TAS_COLUMN_META) * self.BTN_COL_W)

    def update_scroll_limits(self):
        total_rows = len(self.editor.frames)
        visible_rows = max(1, (self.viewport().height() - self.HEADER_H) // self.ROW_H)
        self.verticalScrollBar().setRange(0, max(0, total_rows - visible_rows))
        self.verticalScrollBar().setSingleStep(1)
        self.verticalScrollBar().setPageStep(max(1, visible_rows - 2))
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scroll_limits()

    def ensure_frame_visible(self, frame_idx: int):
        if not self.editor.frames:
            return
        frame_idx = max(0, min(len(self.editor.frames) - 1, frame_idx))
        first_row = self.verticalScrollBar().value()
        visible_rows = max(1, (self.viewport().height() - self.HEADER_H) // self.ROW_H)
        if frame_idx < first_row:
            self.verticalScrollBar().setValue(frame_idx)
        elif frame_idx >= first_row + visible_rows:
            self.verticalScrollBar().setValue(frame_idx - visible_rows + 1)

    def _hit_test(self, pos) -> tuple[int | None, int | None]:
        """Returns (frame_row, button_col_idx or -1 if frame/gz column)."""
        y = pos.y()
        x = pos.x()
        if y < self.HEADER_H:
            return None, None

        row = self.verticalScrollBar().value() + ((y - self.HEADER_H) // self.ROW_H)
        if row < 0 or row >= len(self.editor.frames):
            return None, None

        btn_start_x = self.FRAME_COL_W + self.GZ_COL_W
        if x < btn_start_x:
            return row, -1

        col = (x - btn_start_x) // self.BTN_COL_W
        if 0 <= col < len(TAS_COLUMN_META):
            return row, col
        return row, None

    def get_selection_range(self) -> tuple[int, int]:
        if not self.editor.frames:
            return 0, -1
        s = max(0, min(self.sel_start, self.sel_end))
        e = min(len(self.editor.frames) - 1, max(self.sel_start, self.sel_end))
        return s, e

    def mousePressEvent(self, event):
        row, col = self._hit_test(event.pos())
        if row is None:
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if col == -1:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.sel_end = row
                else:
                    self.sel_start = row
                    self.sel_end = row
                self._dragging_sel = True
                self.selection_changed.emit(*self.get_selection_range())
                self.viewport().update()
            elif col is not None and col >= 0:
                curr_on = bool(self.editor.frames[row] & (1 << col))
                self._paint_col = col
                self._paint_target_val = not curr_on
                self._dragging_paint = True
                self._last_painted_row = row
                self.sel_start = row
                self.sel_end = row
                self.editor.begin_edit()
                self.editor.set_cell(row, col, self._paint_target_val)
                self.timeline_edited.emit(row)
                self.viewport().update()

        elif event.button() == Qt.MouseButton.RightButton:
            s, e = self.get_selection_range()
            if not (s <= row <= e):
                self.sel_start = row
                self.sel_end = row
                self.selection_changed.emit(row, row)
                self.viewport().update()
            self._show_context_menu(event.globalPosition().toPoint(), row)

    def mouseDoubleClickEvent(self, event):
        row, col = self._hit_test(event.pos())
        if row is not None and col == -1 and event.button() == Qt.MouseButton.LeftButton:
            self.seek_requested.emit(row)

    def mouseMoveEvent(self, event):
        row, _ = self._hit_test(event.pos())
        if row is None:
            return

        if self._dragging_sel:
            self.sel_end = row
            self.selection_changed.emit(*self.get_selection_range())
            self.viewport().update()
        elif self._dragging_paint and self._paint_col >= 0:
            r0 = min(self._last_painted_row, row)
            r1 = max(self._last_painted_row, row)
            for r in range(r0, r1 + 1):
                self.editor.set_cell(r, self._paint_col, self._paint_target_val)
            self._last_painted_row = row
            self.timeline_edited.emit(r0)
            self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging_paint:
                self._dragging_paint = False
                self._paint_col = -1
                self.editor.commit_edit()
            self._dragging_sel = False

    def _show_context_menu(self, global_pos, clicked_row: int):
        menu = QMenu(self)
        act_seek = menu.addAction(f"🎯 Seek Emulator to Frame {clicked_row}")
        menu.addSeparator()
        act_ins = menu.addAction("➕ Insert Blank Frame Above")
        act_clone = menu.addAction("📋 Clone Selected Frame(s)")
        act_clear = menu.addAction("🧹 Clear Inputs in Selection")
        act_del = menu.addAction("🗑 Delete Selected Frame(s)")

        chosen = menu.exec(global_pos)
        if chosen == act_seek:
            self.seek_requested.emit(clicked_row)
        elif chosen == act_ins:
            self.editor.insert_blank_frames(clicked_row, 1)
        elif chosen == act_clone:
            self.editor.clone_selected_frames()
        elif chosen == act_clear:
            self.editor.clear_selected_inputs()
        elif chosen == act_del:
            self.editor.delete_selected_frames()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        w = self.viewport().width()
        h = self.viewport().height()

        painter.fillRect(0, 0, w, h, QColor(17, 20, 27))

        first_row = self.verticalScrollBar().value()
        visible_rows = ((h - self.HEADER_H) // self.ROW_H) + 2
        total_rows = len(self.editor.frames)
        last_row = min(total_rows, first_row + visible_rows)

        sel_s, sel_e = self.get_selection_range()
        active_f = self.editor.current_frame
        btn_start_x = self.FRAME_COL_W + self.GZ_COL_W

        # 1. Rows
        painter.setFont(self.font_mono)
        for r in range(first_row, last_row):
            y = self.HEADER_H + ((r - first_row) * self.ROW_H)
            mask = self.editor.frames[r]

            is_active = (r == active_f)
            is_selected = (sel_s <= r <= sel_e)

            if is_active:
                row_bg = QColor(24, 68, 102)
            elif is_selected:
                row_bg = QColor(36, 48, 72)
            elif (r // 10) % 2 == 1:
                row_bg = QColor(22, 26, 35)
            else:
                row_bg = QColor(17, 20, 27)

            painter.fillRect(0, y, w, self.ROW_H, row_bg)

            # Frame index & playback pointer
            painter.setPen(QColor(0, 230, 118) if is_active else QColor(150, 165, 185))
            prefix = "▶ " if is_active else "  "
            painter.drawText(
                QRect(4, y, self.FRAME_COL_W - 6, self.ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"{prefix}{r:06d}",
            )

            # Greenzone state cache indicator
            gz_rect = QRect(self.FRAME_COL_W + 2, y + 3, self.GZ_COL_W - 4, self.ROW_H - 6)
            if r in self.editor.greenzone:
                painter.fillRect(gz_rect, QColor(0, 200, 83))
            else:
                painter.fillRect(gz_rect, QColor(34, 40, 52))

            # Button cells
            for col_idx, (_, short_lbl, col_color) in enumerate(TAS_COLUMN_META):
                cx = btn_start_x + (col_idx * self.BTN_COL_W)
                cell_rect = QRect(cx + 2, y + 2, self.BTN_COL_W - 4, self.ROW_H - 4)
                pressed = bool(mask & (1 << col_idx))

                if pressed:
                    painter.fillRect(cell_rect, col_color)
                    painter.setPen(QColor(10, 12, 16))
                    painter.drawText(cell_rect, Qt.AlignmentFlag.AlignCenter, short_lbl)
                else:
                    painter.setPen(QColor(35, 42, 56))
                    painter.drawRect(cell_rect)

            # Subtle horizontal grid line
            painter.setPen(QColor(30, 36, 48))
            painter.drawLine(0, y + self.ROW_H - 1, w, y + self.ROW_H - 1)

        # Vertical column dividers
        painter.setPen(QColor(48, 56, 72))
        painter.drawLine(self.FRAME_COL_W, self.HEADER_H, self.FRAME_COL_W, h)
        painter.drawLine(btn_start_x, self.HEADER_H, btn_start_x, h)
        for col_idx in range(len(TAS_COLUMN_META) + 1):
            vx = btn_start_x + (col_idx * self.BTN_COL_W)
            painter.drawLine(vx, self.HEADER_H, vx, h)

        # 2. Sticky Header Bar
        painter.fillRect(0, 0, w, self.HEADER_H, QColor(24, 29, 40))
        painter.setFont(self.font_header)
        painter.setPen(QColor(210, 222, 240))
        painter.drawText(
            QRect(6, 0, self.FRAME_COL_W - 6, self.HEADER_H),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "Frame",
        )
        painter.setPen(QColor(0, 230, 118))
        painter.drawText(
            QRect(self.FRAME_COL_W, 0, self.GZ_COL_W, self.HEADER_H),
            Qt.AlignmentFlag.AlignCenter,
            "G",
        )
        for col_idx, (full_name, short_lbl, col_color) in enumerate(TAS_COLUMN_META):
            cx = btn_start_x + (col_idx * self.BTN_COL_W)
            painter.setPen(col_color)
            painter.drawText(
                QRect(cx, 0, self.BTN_COL_W, self.HEADER_H),
                Qt.AlignmentFlag.AlignCenter,
                short_lbl,
            )

        painter.setPen(QColor(60, 72, 94))
        painter.drawLine(0, self.HEADER_H - 1, w, self.HEADER_H - 1)
        painter.end()


class TASTimelineDialog(QDialog):
    """BizHawk TAStudio-style Piano-Roll TAS Input Timeline Editor."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.mw = main_window
        self.config = main_window.config
        self.setWindowTitle("Piano-Roll TAS Input Timeline Editor (TAStudio)")
        self.resize(620, 740)

        self.frames = [0] * 600
        self.current_frame = 0
        self.anchor_state_bytes = None   # zlib-compressed frame-0 state
        self.anchor_state_filename = ""
        self.greenzone = {}              # frame_idx -> zlib-compressed state bytes
        self.current_json_path = None

        self.undo_stack = []
        self.redo_stack = []
        self._pending_snapshot = None

        self._init_ui()
        self._bind_shortcuts()
        self._connect_worker_signals()
        self._bootstrap_from_active_session()

    @property
    def worker(self):
        return getattr(self.mw, "worker", None)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # 1. File & Anchor Ribbon
        file_bar = QHBoxLayout()
        btn_new = QPushButton("📸 Capture Anchor (Frame 0)")
        btn_new.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 5px 8px;")
        btn_new.clicked.connect(self.capture_anchor_from_live)
        file_bar.addWidget(btn_new)

        btn_load = QPushButton("📂 Load .replay.json")
        btn_load.clicked.connect(self.load_tas_file)
        file_bar.addWidget(btn_load)

        btn_save = QPushButton("💾 Save .replay.json")
        btn_save.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 5px 8px;")
        btn_save.clicked.connect(self.save_tas_file)
        file_bar.addWidget(btn_save)

        self.cb_overwrite = QCheckBox("🔴 Record-Overwrite")
        self.cb_overwrite.setToolTip("When checked, playing/stepping records your live controller inputs directly into the timeline.")
        self.cb_overwrite.toggled.connect(self.on_overwrite_toggled)
        file_bar.addWidget(self.cb_overwrite)

        file_bar.addStretch()
        root.addLayout(file_bar)

        # 2. Transport Controls
        trans_bar = QHBoxLayout()
        btn_f0 = QPushButton("⏮ Frame 0")
        btn_f0.clicked.connect(lambda: self.seek_to_frame(0))
        trans_bar.addWidget(btn_f0)

        btn_prev = QPushButton("◀ Step -1")
        btn_prev.clicked.connect(self.step_backward)
        trans_bar.addWidget(btn_prev)

        self.btn_play_pause = QPushButton("▶ Play / ⏸ Pause")
        self.btn_play_pause.setStyleSheet("background-color: #00897B; color: white; font-weight: bold; padding: 5px 10px;")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        trans_bar.addWidget(self.btn_play_pause)

        btn_next = QPushButton("Step +1 ▶")
        btn_next.clicked.connect(self.step_forward)
        trans_bar.addWidget(btn_next)

        btn_stop = QPushButton("⏹ Stop TAS")
        btn_stop.clicked.connect(self.stop_tas_playback)
        trans_bar.addWidget(btn_stop)

        trans_bar.addStretch()
        root.addLayout(trans_bar)

        # 3. Timeline Editing Controls
        edit_bar = QHBoxLayout()
        self.btn_undo = QPushButton("↩ Undo")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.undo)
        edit_bar.addWidget(self.btn_undo)

        self.btn_redo = QPushButton("↪ Redo")
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self.redo)
        edit_bar.addWidget(self.btn_redo)

        btn_ins = QPushButton("+ Row")
        btn_ins.clicked.connect(lambda: self.insert_blank_frames(self.canvas.get_selection_range()[0], 1))
        edit_bar.addWidget(btn_ins)

        btn_del = QPushButton("- Del")
        btn_del.clicked.connect(self.delete_selected_frames)
        edit_bar.addWidget(btn_del)

        btn_clone = QPushButton("Clone")
        btn_clone.clicked.connect(self.clone_selected_frames)
        edit_bar.addWidget(btn_clone)

        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.clear_selected_inputs)
        edit_bar.addWidget(btn_clear)

        btn_append60 = QPushButton("+60F")
        btn_append60.clicked.connect(lambda: self.append_blank_frames(60))
        edit_bar.addWidget(btn_append60)

        self.cb_follow = QCheckBox("Auto-Scroll")
        self.cb_follow.setChecked(True)
        edit_bar.addWidget(self.cb_follow)

        edit_bar.addStretch()
        root.addLayout(edit_bar)

        # 4. Piano-Roll Canvas
        self.canvas = PianoRollCanvas(self, self)
        self.canvas.timeline_edited.connect(self.on_timeline_edited)
        self.canvas.seek_requested.connect(self.seek_to_frame)
        self.canvas.selection_changed.connect(self._update_status_label)
        root.addWidget(self.canvas)

        # 5. Status Bar
        self.status_lbl = QLabel("Ready.")
        self.status_lbl.setStyleSheet(
            "font-family: monospace; font-size: 11px; color: #90CAF9; "
            "background: #161A22; padding: 6px; border-radius: 4px;"
        )
        root.addWidget(self.status_lbl)

    def _bind_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        QShortcut(QKeySequence("Space"), self, activated=self.toggle_play_pause)
        QShortcut(QKeySequence("."), self, activated=self.step_forward)
        QShortcut(QKeySequence(","), self, activated=self.step_backward)
        QShortcut(QKeySequence("Delete"), self, activated=self.clear_selected_inputs)

    def _connect_worker_signals(self):
        if not self.worker:
            return
        self.worker.input_hud_updated.connect(self._on_worker_hud_tick)
        self.worker.tas_seek_completed.connect(self._on_tas_seek_completed)
        self.worker.tas_greenzone_captured.connect(self._on_greenzone_captured)
        self.worker.tas_frame_recorded.connect(self._on_live_frame_recorded)

    def _bootstrap_from_active_session(self):
        if not self.worker:
            self.status_lbl.setText("No active emulator session. Load a ROM first.")
            return

        rec = self.worker.recorder
        if rec.is_playing and rec.playback_max_frame > 0:
            total = max(1, rec.playback_max_frame)
            dense = [0] * total
            curr_mask = 0
            for f_idx in range(total):
                if f_idx in rec.playback_timeline:
                    btns = rec.playback_timeline[f_idx].get("btn", set())
                    mask = 0
                    for b_idx, b_name in enumerate(TAS_BUTTON_ORDER):
                        if b_name in btns:
                            mask |= (1 << b_idx)
                    curr_mask = mask
                dense[f_idx] = curr_mask
            self.frames = dense
            self.current_frame = max(0, min(total - 1, self.worker.replay_frame_counter))

        raw_state = self.worker.serialize_state_bytes()
        if raw_state:
            comp = zlib.compress(raw_state, level=1)
            self.anchor_state_bytes = comp
            self.greenzone[self.current_frame] = comp

        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.canvas.update_scroll_limits()
        self.canvas.ensure_frame_visible(self.current_frame)
        self._update_status_label()

    # =========================================================================
    # TIMELINE EDITING + UNDO/REDO + GREENZONE INVALIDATION
    # =========================================================================
    def begin_edit(self):
        self._pending_snapshot = list(self.frames)

    def commit_edit(self):
        if self._pending_snapshot is not None and self._pending_snapshot != self.frames:
            self.undo_stack.append(self._pending_snapshot)
            if len(self.undo_stack) > 80:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self._update_undo_redo_btns()
        self._pending_snapshot = None

    def set_cell(self, row: int, col: int, enabled: bool):
        if not (0 <= row < len(self.frames)):
            return
        if enabled:
            self.frames[row] |= (1 << col)
        else:
            self.frames[row] &= ~(1 << col)

    def _invalidate_greenzone_after(self, frame_idx: int):
        stale = [k for k in self.greenzone.keys() if k > frame_idx]
        for k in stale:
            del self.greenzone[k]

    def on_timeline_edited(self, earliest_frame: int):
        self._invalidate_greenzone_after(earliest_frame)
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self._update_status_label()

    def _update_undo_redo_btns(self):
        self.btn_undo.setEnabled(len(self.undo_stack) > 0)
        self.btn_redo.setEnabled(len(self.redo_stack) > 0)

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(list(self.frames))
        self.frames = self.undo_stack.pop()
        self.greenzone = {k: v for k, v in self.greenzone.items() if k == 0}
        self._update_undo_redo_btns()
        self._sync_timeline_to_worker(start_frame=min(self.current_frame, len(self.frames) - 1))
        self.canvas.update_scroll_limits()
        self._update_status_label()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(list(self.frames))
        self.frames = self.redo_stack.pop()
        self.greenzone = {k: v for k, v in self.greenzone.items() if k == 0}
        self._update_undo_redo_btns()
        self._sync_timeline_to_worker(start_frame=min(self.current_frame, len(self.frames) - 1))
        self.canvas.update_scroll_limits()
        self._update_status_label()

    def insert_blank_frames(self, at_row: int, count: int = 1):
        self.begin_edit()
        at_row = max(0, min(len(self.frames), at_row))
        self.frames[at_row:at_row] = [0] * count
        self.commit_edit()
        self._invalidate_greenzone_after(at_row)
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.canvas.update_scroll_limits()
        self._update_status_label()

    def append_blank_frames(self, count: int = 60):
        self.begin_edit()
        self.frames.extend([0] * count)
        self.commit_edit()
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.canvas.update_scroll_limits()
        self._update_status_label()

    def clone_selected_frames(self):
        s, e = self.canvas.get_selection_range()
        if e < s:
            return
        self.begin_edit()
        cloned = list(self.frames[s:e + 1])
        self.frames[e + 1:e + 1] = cloned
        self.commit_edit()
        self._invalidate_greenzone_after(e + 1)
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.canvas.update_scroll_limits()
        self._update_status_label()

    def clear_selected_inputs(self):
        s, e = self.canvas.get_selection_range()
        if e < s:
            return
        self.begin_edit()
        for r in range(s, e + 1):
            self.frames[r] = 0
        self.commit_edit()
        self._invalidate_greenzone_after(s)
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.canvas.viewport().update()
        self._update_status_label()

    def delete_selected_frames(self):
        s, e = self.canvas.get_selection_range()
        if e < s:
            return
        if len(self.frames) - (e - s + 1) < 1:
            QMessageBox.warning(self, "Notice", "Timeline must contain at least 1 frame.")
            return
        self.begin_edit()
        del self.frames[s:e + 1]
        self.commit_edit()
        self.canvas.sel_start = min(s, len(self.frames) - 1)
        self.canvas.sel_end = self.canvas.sel_start
        self._invalidate_greenzone_after(s)
        self.current_frame = min(self.current_frame, len(self.frames) - 1)
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.canvas.update_scroll_limits()
        self._update_status_label()

    # =========================================================================
    # EMULATOR SYNC, TRANSPORT & SEEKING
    # =========================================================================
    def _sync_timeline_to_worker(self, start_frame: int = 0):
        if not self.worker:
            return
        self.worker.recorder.set_dense_tas_timeline(
            self.frames,
            start_frame=start_frame,
            overwrite_mode=self.cb_overwrite.isChecked(),
        )

    def on_overwrite_toggled(self, checked: bool):
        if self.worker:
            self.worker.recorder.tas_overwrite_mode = checked
        self._update_status_label()

    def capture_anchor_from_live(self):
        if not self.worker:
            return
        was_paused = self.worker.is_paused
        self.worker.is_paused = True
        raw_state = self.worker.serialize_state_bytes()
        if not raw_state:
            QMessageBox.warning(self, "Capture Failed", "Could not serialize emulator state.")
            self.worker.is_paused = was_paused
            return

        comp = zlib.compress(raw_state, level=1)
        self.anchor_state_bytes = comp
        self.anchor_state_filename = ""
        self.greenzone.clear()
        self.greenzone[0] = comp
        self.current_frame = 0
        self.worker.replay_frame_counter = 0
        self._sync_timeline_to_worker(start_frame=0)
        self.mw.pause_action.setChecked(True)
        self.canvas.ensure_frame_visible(0)
        self.canvas.viewport().update()
        self._update_status_label()
        self.mw.video_widget.show_osd("📸 Captured TAS Frame 0 Anchor", 2000)

    def toggle_play_pause(self):
        if not self.worker:
            return
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.worker.is_paused = not self.worker.is_paused
        self.mw.pause_action.setChecked(self.worker.is_paused)
        self._update_status_label()

    def step_forward(self):
        if not self.worker:
            return
        if self.current_frame >= len(self.frames) - 1:
            self.append_blank_frames(60)
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.worker.step_one_frame()
        self.mw.pause_action.setChecked(True)

    def step_backward(self):
        if not self.worker:
            return
        target = max(0, self.current_frame - 1)
        self.seek_to_frame(target)

    def seek_to_frame(self, target_frame: int):
        if not self.worker:
            return
        target_frame = max(0, min(len(self.frames) - 1, int(target_frame)))

        # Pick nearest greenzone state <= target_frame
        candidates = [f for f in self.greenzone.keys() if f <= target_frame]
        if candidates:
            anchor_f = max(candidates)
            anchor_bytes = self.greenzone[anchor_f]
        elif self.anchor_state_bytes is not None:
            anchor_f = 0
            anchor_bytes = self.anchor_state_bytes
        else:
            QMessageBox.information(
                self,
                "No Frame 0 Anchor",
                "Click 'Capture Anchor (Frame 0)' or load a .replay.json file first to enable frame seeking.",
            )
            return

        self.worker.is_paused = True
        self.mw.pause_action.setChecked(True)
        self.status_lbl.setText(f"⏳ Seeking from frame {anchor_f} to {target_frame}...")
        self.worker.request_tas_seek(target_frame, anchor_f, anchor_bytes, list(self.frames))

    def stop_tas_playback(self):
        if self.worker:
            self.worker.stop_replay_session()
            self.mw.video_widget.show_osd("⏹ TAS Playback Stopped")

    def _on_worker_hud_tick(self, mode: str, frame_num: int, max_frame: int, btn_mask: int):
        if mode in ("PLAYBACK", "IDLE"):
            self.current_frame = max(0, min(len(self.frames) - 1, frame_num))
            if self.cb_follow.isChecked():
                self.canvas.ensure_frame_visible(self.current_frame)
            self.canvas.viewport().update()
            self._update_status_label()

    def _on_greenzone_captured(self, frame_num: int, comp_bytes: bytes):
        self.greenzone[int(frame_num)] = comp_bytes
        if len(self.greenzone) > 250:
            # Keep frame 0 and prune oldest non-zero intermediate snapshots
            keys = sorted(k for k in self.greenzone.keys() if k != 0)
            for k in keys[:len(keys) - 240]:
                del self.greenzone[k]

    def _on_tas_seek_completed(self, target_frame: int, comp_bytes: bytes):
        self.current_frame = max(0, min(len(self.frames) - 1, target_frame))
        if comp_bytes:
            self.greenzone[self.current_frame] = comp_bytes
        self._sync_timeline_to_worker(start_frame=self.current_frame)
        self.canvas.sel_start = self.current_frame
        self.canvas.sel_end = self.current_frame
        self.canvas.ensure_frame_visible(self.current_frame)
        self.canvas.viewport().update()
        self._update_status_label()

    def _on_live_frame_recorded(self, frame_num: int, mask: int):
        if not self.cb_overwrite.isChecked():
            return
        while frame_num >= len(self.frames):
            self.frames.append(0)
        self.frames[frame_num] = mask
        self._invalidate_greenzone_after(frame_num)
        self.canvas.update_scroll_limits()

    def _update_status_label(self, *_):
        total = len(self.frames)
        sec = self.current_frame / 59.7275
        total_sec = total / 59.7275
        mode_str = "OVERWRITE" if self.cb_overwrite.isChecked() else "READ-ONLY"
        s, e = self.canvas.get_selection_range()
        self.status_lbl.setText(
            f"Frame: {self.current_frame:05d}/{total:05d} ({sec:05.2f}s / {total_sec:05.2f}s) | "
            f"Sel: {s}..{e} | Greenzone: {len(self.greenzone)} | Mode: {mode_str}"
        )

    # =========================================================================
    # SAVE / LOAD .REPLAY.JSON
    # =========================================================================
    def load_tas_file(self):
        replays_dir = self.config.get("paths", "replays") or "replays"
        os.makedirs(replays_dir, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open TAS / Replay Timeline", replays_dir, "Replay Files (*.replay.json *.json)"
        )
        if not path:
            return

        dense, state_path, meta = SessionRecorder.load_dense_from_file(path)
        if not dense:
            QMessageBox.warning(self, "Load Error", "Failed to parse replay timeline.")
            return

        if not state_path or not os.path.exists(state_path):
            QMessageBox.warning(
                self, "Missing State File",
                "Could not find the initial .state snapshot referenced by this replay."
            )
            return

        with open(state_path, "rb") as sf:
            raw_state = sf.read()

        self.frames = dense
        self.current_json_path = path
        self.anchor_state_filename = os.path.basename(state_path)
        self.anchor_state_bytes = zlib.compress(raw_state, level=1)
        self.greenzone = {0: self.anchor_state_bytes}
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._update_undo_redo_btns()

        self.current_frame = 0
        self.canvas.update_scroll_limits()
        self.seek_to_frame(0)
        self.mw.video_widget.show_osd(f"Loaded TAS: {os.path.basename(path)}", 2500)

    def save_tas_file(self):
        replays_dir = self.config.get("paths", "replays") or "replays"
        os.makedirs(replays_dir, exist_ok=True)

        rom_path = getattr(self.mw, "current_rom", "") or "game.gba"
        rom_base = os.path.splitext(os.path.basename(rom_path))[0]
        default_path = self.current_json_path or os.path.join(
            replays_dir, f"{rom_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.replay.json"
        )

        json_path, _ = QFileDialog.getSaveFileName(
            self, "Save TAS Timeline", default_path, "Replay JSON (*.replay.json);;JSON (*.json)"
        )
        if not json_path:
            return

        # Ensure a matching .state file sits next to the saved .replay.json
        if self.anchor_state_bytes is None and self.worker:
            raw_state = self.worker.serialize_state_bytes()
            if raw_state:
                self.anchor_state_bytes = zlib.compress(raw_state, level=1)

        if self.anchor_state_bytes is not None:
            stem = os.path.basename(json_path)
            if stem.endswith(".replay.json"):
                stem = stem[:-12]
            elif stem.endswith(".json"):
                stem = stem[:-5]
            state_filename = f"{stem}.state"
            full_state_path = os.path.join(os.path.dirname(json_path), state_filename)
            with open(full_state_path, "wb") as sf:
                sf.write(zlib.decompress(self.anchor_state_bytes))
            self.anchor_state_filename = state_filename

        SessionRecorder.save_dense_to_file(
            json_path,
            self.frames,
            rom_path=rom_path,
            initial_state_filename=self.anchor_state_filename,
        )
        self.current_json_path = json_path
        self.mw.video_widget.show_osd(f"💾 Saved TAS: {os.path.basename(json_path)}", 3000)
        QMessageBox.information(
            self,
            "TAS Saved",
            f"Saved {len(self.frames)} frames to:\n{json_path}",
        )
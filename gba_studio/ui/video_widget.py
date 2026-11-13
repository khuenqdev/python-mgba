# video_widget.py
import math
import numpy as np

from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen
from PyQt6.QtCore import Qt, QTimer, QPoint, QSize, QRect
from PyQt6.QtOpenGLWidgets import QOpenGLWidget


# Matches HUD_BTN_BITS order in core_worker.py (bits 0..9)
OVERLAY_BUTTON_LABELS = [
    (4, "▲"),
    (5, "▼"),
    (6, "◀"),
    (7, "▶"),
    (2, "L"),
    (3, "R"),
    (1, "B"),
    (0, "A"),
    (9, "SL"),
    (8, "ST"),
]


class VideoGLWidget(QOpenGLWidget):
    """Hardware-accelerated OpenGL rendering surface with RetroArch shader pipeline."""

    def __init__(self, shader_manager, parent=None):
        super().__init__(parent)
        self.sm = shader_manager
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.current_frame_data = None
        self.tex_id = None
        self._need_texture_update = False

        # OSD Setup
        self.osd_text = ""
        self.osd_timer = QTimer()
        self.osd_timer.setSingleShot(True)
        self.osd_timer.timeout.connect(self.clear_osd)

        # Live Input / TAS Frame HUD State
        self.input_hud_mode = "IDLE"
        self.input_hud_frame = 0
        self.input_hud_max_frame = 0
        self.input_hud_mask = 0

        # Clip Recording Badge
        self.clip_rec_label = ""

        # Fullscreen Floating Subtitle Overlay State
        self.subtitle_boxes = []

        # Pixel Ruler State
        self.ruler_enabled = False
        self.hover_pos = None
        self.ruler_start = None
        self.ruler_end = None
        self.is_dragging = False

        # Mouse Gyro State
        self.mouse_gyro_active = False
        self.gyro_drag_center = None

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def initializeGL(self):
        from OpenGL import GL

        while GL.glGetError() != GL.GL_NO_ERROR:
            pass

        self.tex_id = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex_id)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB8, 240, 160, 0, GL.GL_RGB, GL.GL_UNSIGNED_BYTE, None)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

        self.sm.set_gl_widget(self)
        self.sm.init_gl()

    def set_frame(self, frame_data: np.ndarray):
        self.current_frame_data = frame_data
        self._need_texture_update = True
        self.update()

    def set_input_hud(self, mode: str, frame_num: int, max_frame: int, btn_mask: int):
        self.input_hud_mode = mode
        self.input_hud_frame = frame_num
        self.input_hud_max_frame = max_frame
        self.input_hud_mask = btn_mask

        show_always = bool(self.sm.config.get("video", "show_input_overlay", False)) if self.sm and self.sm.config else False
        if show_always or mode != "IDLE":
            self.update()

    def set_clip_status(self, label: str):
        self.clip_rec_label = label
        self.update()

    def set_translation_boxes(self, boxes: list):
        self.subtitle_boxes = list(boxes or [])
        parent_win = self.window()
        if parent_win and parent_win.isFullScreen():
            self.update()

    def clear_translation_boxes(self):
        if self.subtitle_boxes:
            self.subtitle_boxes = []
            parent_win = self.window()
            if parent_win and parent_win.isFullScreen():
                self.update()

    def _get_viewport_transform(self) -> tuple[float, float, float]:
        """Returns (logical_scale, offset_x, offset_y) matching the GL viewport."""
        dpr = self.devicePixelRatioF() or 1.0
        phys_w = max(1, int(self.width() * dpr))
        phys_h = max(1, int(self.height() * dpr))

        integer_scaling = bool(self.sm.config.get("video", "integer_scaling", False)) if self.sm and self.sm.config else False
        if integer_scaling and phys_w >= 240 and phys_h >= 160:
            phys_scale = float(max(1, min(phys_w // 240, phys_h // 160)))
        else:
            phys_scale = min(phys_w / 240.0, phys_h / 160.0)

        scale = phys_scale / dpr
        ox = (self.width() - (240.0 * scale)) / 2.0
        oy = (self.height() - (160.0 * scale)) / 2.0
        return scale, ox, oy

    def _is_input_hud_visible(self) -> bool:
        show_always = bool(self.sm.config.get("video", "show_input_overlay", False)) if self.sm and self.sm.config else False
        return (self.input_hud_mode != "IDLE") or show_always

    @staticmethod
    def _make_font(families: list[str], pt_size: int, weight: QFont.Weight) -> QFont:
        f = QFont()
        f.setFamilies(families)
        f.setPointSize(pt_size)
        f.setWeight(weight)
        return f

    def _draw_input_hud(self, painter: QPainter):
        if not self._is_input_hud_visible():
            return

        compact = self.width() < 400
        font_sz = 7 if compact else 9
        pill_h = 15 if compact else 19
        pad_x = 4 if compact else 7
        gap = 2 if compact else 4

        font = self._make_font(["Cascadia Code", "Consolas", "Courier New", "Monospace"], font_sz, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()

        if self.input_hud_mode == "RECORDING":
            status_txt = f"● REC F:{self.input_hud_frame:05d}"
            status_col = QColor(255, 82, 82)
        elif self.input_hud_mode == "PLAYBACK":
            status_txt = f"▶ PLAY {self.input_hud_frame:04d}/{self.input_hud_max_frame:04d}"
            status_col = QColor(77, 208, 225)
        elif self.input_hud_mode == "REWIND":
            status_txt = f"⏪ REW F:{self.input_hud_frame:05d}"
            status_col = QColor(255, 213, 79)
        else:
            status_txt = f"F:{self.input_hud_frame:05d}"
            status_col = QColor(144, 202, 249)

        status_w = fm.horizontalAdvance(status_txt) + (6 if compact else 10)
        pill_widths = []
        total_pills_w = 0
        for _, lbl in OVERLAY_BUTTON_LABELS:
            pw = max(14 if compact else 20, fm.horizontalAdvance(lbl) + (6 if compact else 10))
            pill_widths.append(pw)
            total_pills_w += pw + gap

        total_w = pad_x * 2 + status_w + 6 + total_pills_w
        bar_h = pill_h + 8
        bx = 8
        by = max(4, self.height() - bar_h - 8)

        painter.setPen(QPen(QColor(60, 72, 92, 200), 1))
        painter.setBrush(QColor(12, 16, 22, 195))
        painter.drawRoundedRect(bx, by, total_w, bar_h, 5, 5)

        py = by + 4
        painter.setPen(status_col)
        painter.drawText(
            QRect(bx + pad_x, py, status_w, pill_h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            status_txt,
        )

        cx = bx + pad_x + status_w + 4
        for (bit_idx, lbl), pw in zip(OVERLAY_BUTTON_LABELS, pill_widths):
            pressed = bool(self.input_hud_mask & (1 << bit_idx))
            if pressed:
                painter.setPen(QPen(QColor(0, 230, 118), 1))
                painter.setBrush(QColor(0, 200, 100, 210))
                txt_col = QColor(10, 15, 12)
            else:
                painter.setPen(QPen(QColor(55, 65, 80, 180), 1))
                painter.setBrush(QColor(26, 31, 40, 180))
                txt_col = QColor(120, 135, 150)

            rect = QRect(cx, py, pw, pill_h)
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(txt_col)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, lbl)
            cx += pw + gap

    def _draw_clip_badge(self, painter: QPainter):
        if not self.clip_rec_label:
            return
        font = self._make_font(["Cascadia Code", "Consolas", "Courier New", "Monospace"], 9, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(self.clip_rec_label) + 16
        th = 22
        rect = QRect(8, 8, tw, th)
        painter.setPen(QPen(QColor(255, 82, 82, 220), 1))
        painter.setBrush(QColor(25, 12, 16, 210))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor(255, 110, 110))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.clip_rec_label)

    def _build_subtitle_layout(self, inner_max_w: int, shrink_pt: int = 0):
        families = ["Segoe UI", "Meiryo", "Arial", "Sans Serif"]
        sp_pt = max(9, min(15, int(self.height() / 76)) - shrink_pt)
        dlg_pt = max(10, min(18, int(self.height() / 62)) - shrink_pt)
        tag_pt = max(9, min(15, int(self.height() / 72)) - shrink_pt)

        speaker_font = self._make_font(families, sp_pt, QFont.Weight.Bold)
        dialogue_font = self._make_font(families, dlg_pt, QFont.Weight.Bold)
        tag_font = self._make_font(families, tag_pt, QFont.Weight.DemiBold)

        fm_speaker = QFontMetrics(speaker_font)
        fm_dialogue = QFontMetrics(dialogue_font)
        fm_tag = QFontMetrics(tag_font)

        entries = []
        used_w = 0

        for b in self.subtitle_boxes:
            trans = (getattr(b, "translated", "") or "").strip()
            if not trans:
                continue

            cat = getattr(b, "category", "dialogue")
            sp_orig = (getattr(b, "speaker_orig", "") or "").strip()
            sp_trans = (getattr(b, "speaker_trans", "") or "").strip()

            if cat == "dialogue":
                if sp_trans and sp_orig and sp_trans.lower() != sp_orig.lower():
                    sp_line = f"💬 {sp_trans} ({sp_orig})"
                elif sp_trans or sp_orig:
                    sp_line = f"💬 {sp_trans or sp_orig}"
                else:
                    sp_line = ""

                if sp_line:
                    sp_rect = fm_speaker.boundingRect(
                        QRect(0, 0, inner_max_w, 2000),
                        Qt.TextFlag.TextWordWrap,
                        sp_line,
                    )
                    entries.append((speaker_font, QColor(255, 213, 79), sp_line, sp_rect.height() + 2, 2))
                    used_w = max(used_w, sp_rect.width() + 8)

                body_rect = fm_dialogue.boundingRect(
                    QRect(0, 0, inner_max_w, 2000),
                    Qt.TextFlag.TextWordWrap,
                    trans,
                )
                entries.append((dialogue_font, QColor(235, 245, 255), trans, body_rect.height() + 2, 7))
                used_w = max(used_w, body_rect.width() + 8)

            elif cat == "menu":
                line = f"[MENU] {trans}"
                t_rect = fm_tag.boundingRect(
                    QRect(0, 0, inner_max_w, 2000),
                    Qt.TextFlag.TextWordWrap,
                    line,
                )
                entries.append((tag_font, QColor(129, 199, 132), line, t_rect.height() + 2, 4))
                used_w = max(used_w, t_rect.width() + 8)

            else:
                line = f"[LABEL] {trans}"
                t_rect = fm_tag.boundingRect(
                    QRect(0, 0, inner_max_w, 2000),
                    Qt.TextFlag.TextWordWrap,
                    line,
                )
                entries.append((tag_font, QColor(255, 213, 79), line, t_rect.height() + 2, 4))
                used_w = max(used_w, t_rect.width() + 8)

        if not entries:
            return [], 0, 0

        total_h = sum(item_h + gap for _, _, _, item_h, gap in entries[:-1]) + entries[-1][3]
        return entries, used_w, total_h

    def _draw_subtitle_overlay(self, painter: QPainter):
        """Renders floating AI translation subtitles ONLY when in Fullscreen mode."""
        parent_win = self.window()
        if not parent_win or not parent_win.isFullScreen():
            return

        overlay_enabled = bool(self.sm.config.get("ai_translation", "overlay_enabled", True)) if self.sm and self.sm.config else True
        if not overlay_enabled or not self.subtitle_boxes:
            return

        max_box_w = max(280, min(int(self.width() * 0.82), 1040))
        pad_x = 18
        pad_y = 14
        inner_max_w = max_box_w - (pad_x * 2)
        max_allowed_h = int(self.height() * 0.72)

        entries, used_w, total_h = self._build_subtitle_layout(inner_max_w, shrink_pt=0)
        for shrink in (2, 4, 6):
            if not entries or (total_h + pad_y * 2) <= max_allowed_h:
                break
            entries, used_w, total_h = self._build_subtitle_layout(inner_max_w, shrink_pt=shrink)

        if not entries:
            return

        box_w = min(max_box_w, max(260, used_w + (pad_x * 2)))
        box_h = min(max_allowed_h, total_h + (pad_y * 2))

        bottom_margin = 48 if self._is_input_hud_visible() else 20
        bx = (self.width() - box_w) // 2
        by = max(16, self.height() - box_h - bottom_margin)

        painter.setPen(QPen(QColor(100, 150, 220, 195), 2))
        painter.setBrush(QColor(12, 16, 24, 224))
        painter.drawRoundedRect(bx, by, box_w, box_h, 10, 10)

        painter.save()
        content_rect = QRect(bx + pad_x, by + pad_y, box_w - (pad_x * 2), box_h - (pad_y * 2))
        painter.setClipRect(content_rect)

        cur_y = by + pad_y
        draw_w = box_w - (pad_x * 2)
        for font, color, text, item_h, gap in entries:
            if cur_y >= by + box_h - pad_y:
                break
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(
                QRect(bx + pad_x, cur_y, draw_w, item_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.TextWordWrap,
                text,
            )
            cur_y += item_h + gap

        painter.restore()

    def paintGL(self):
        from OpenGL import GL

        if self._need_texture_update and self.current_frame_data is not None and self.tex_id is not None:
            h, w, _ = self.current_frame_data.shape
            buf = np.ascontiguousarray(self.current_frame_data, dtype=np.uint8)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex_id)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_RGB, GL.GL_UNSIGNED_BYTE, buf)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            self._need_texture_update = False

        dpr = self.devicePixelRatioF() or 1.0
        phys_w = max(1, int(self.width() * dpr))
        phys_h = max(1, int(self.height() * dpr))
        default_fbo = self.defaultFramebufferObject()

        if self.tex_id is not None:
            self.sm.render(self.tex_id, 240, 160, phys_w, phys_h, default_fbo)

        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, default_fbo)
        GL.glViewport(0, 0, phys_w, phys_h)
        GL.glUseProgram(0)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)

        painter = QPainter(self)

        # Pixel Ruler Overlay
        if self.ruler_enabled:
            img_w, img_h = 240, 160
            scale, ox, oy = self._get_viewport_transform()

            if self.hover_pos:
                hx, hy = self.hover_pos
                sx, sy = ox + hx * scale, oy + hy * scale
                painter.setPen(QPen(QColor(0, 255, 255, 160), 1))
                painter.drawLine(int(ox), int(sy), int(ox + img_w * scale), int(sy))
                painter.drawLine(int(sx), int(oy), int(sx), int(oy + img_h * scale))

                painter.setPen(QColor(0, 255, 255))
                painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
                painter.drawText(int(ox) + 5, int(oy) + 15, f"Cursor: {hx}, {hy}")

            if self.ruler_start and self.ruler_end:
                x1, y1 = self.ruler_start
                x2, y2 = self.ruler_end
                rx, ry = min(x1, x2), min(y1, y2)
                rw, rh = abs(x2 - x1) + 1, abs(y2 - y1) + 1

                screen_x = ox + rx * scale
                screen_y = oy + ry * scale
                screen_w = rw * scale
                screen_h = rh * scale

                painter.setPen(QPen(QColor(255, 0, 255), 2))
                painter.setBrush(QColor(255, 0, 255, 60))
                painter.drawRect(int(screen_x), int(screen_y), int(screen_w), int(screen_h))

                label = f" W: {rw}px \n H: {rh}px "
                painter.setPen(QColor(255, 255, 255))
                painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
                tr = painter.fontMetrics().boundingRect(label)
                tr.moveTo(int(screen_x), int(screen_y) - tr.height())
                painter.fillRect(tr, QColor(0, 0, 0, 180))
                painter.drawText(tr, Qt.AlignmentFlag.AlignLeft, label)

        self._draw_clip_badge(painter)
        self._draw_input_hud(painter)
        self._draw_subtitle_overlay(painter)

        # Global OSD Message (Top-Right)
        if self.osd_text:
            painter.setPen(QPen(QColor(0, 255, 0)))
            painter.setFont(QFont("Arial", 14, QFont.Weight.Bold))
            painter.drawText(
                self.rect().adjusted(10, 10, -10, -10),
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
                self.osd_text
            )

        painter.end()

    def show_osd(self, text: str, duration_ms: int = 2000):
        self.osd_text = text
        self.osd_timer.start(duration_ms)
        self.update()

    def clear_osd(self):
        self.osd_text = ""
        self.update()

    def get_gba_coords(self, pos) -> tuple:
        img_w, img_h = 240, 160
        scale, offset_x, offset_y = self._get_viewport_transform()

        gba_x = int((pos.x() - offset_x) / scale)
        gba_y = int((pos.y() - offset_y) / scale)
        return max(0, min(img_w - 1, gba_x)), max(0, min(img_h - 1, gba_y))

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() in [Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton]:
            parent_win = self.window()
            if hasattr(parent_win, "config") and parent_win.config.get("sensors", "mouse_gyro"):
                self.mouse_gyro_active = True
                self.gyro_drag_center = QPoint(self.width() // 2, self.height() // 2)
                self._update_mouse_gyro(event.position().toPoint())
                self.update()
                return

        if self.ruler_enabled and event.button() == Qt.MouseButton.LeftButton:
            self.ruler_start = self.get_gba_coords(event.position())
            self.ruler_end = self.ruler_start
            self.is_dragging = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.mouse_gyro_active:
            self._update_mouse_gyro(event.position().toPoint())
            self.update()
            return

        if self.ruler_enabled:
            self.hover_pos = self.get_gba_coords(event.position())
            if self.is_dragging and self.ruler_start:
                self.ruler_end = self.hover_pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() in [Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton] and self.mouse_gyro_active:
            self.mouse_gyro_active = False
            self.update()
            return

        if self.ruler_enabled and event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
            self.update()

    def _update_mouse_gyro(self, pos: QPoint):
        if not self.gyro_drag_center:
            return
        dx = pos.x() - self.gyro_drag_center.x()
        dy = pos.y() - self.gyro_drag_center.y()
        if dx == 0 and dy == 0:
            return

        angle = math.degrees(math.atan2(dx, -dy))
        parent_win = self.window()
        if hasattr(parent_win, "worker") and parent_win.worker:
            parent_win.worker.set_gyro_angle(angle)
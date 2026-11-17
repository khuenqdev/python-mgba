# video_widget.py
import os
import re
import math
import time
from collections import deque
import numpy as np

from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QPixmap, QImage
from PyQt6.QtCore import Qt, QTimer, QPoint, QSize, QRect
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from ..tools.ai.inpaint import build_inpaint_patches, check_textbox_still_present


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

        self.osd_text = ""
        self.osd_timer = QTimer()
        self.osd_timer.setSingleShot(True)
        self.osd_timer.timeout.connect(self.clear_osd)

        self.input_hud_mode = "IDLE"
        self.input_hud_frame = 0
        self.input_hud_max_frame = 0
        self.input_hud_mask = 0
        self.clip_rec_label = ""

        # Translation & In-Place Inpainting State
        self.subtitle_boxes = []
        self.inpaint_patches = []
        self._inpaint_qimages = []
        self._inpaint_check_tick = 0

        # RetroArch-Style Achievement Unlock Toast Queue
        self._ach_queue = deque()
        self._active_ach_toast = None
        self._ach_toast_start = 0.0
        self._ach_toast_duration = 4.6
        self._ach_anim_timer = QTimer(self)
        self._ach_anim_timer.setInterval(33)
        self._ach_anim_timer.timeout.connect(self._tick_ach_toast)

        # Pixel Ruler State
        self.ruler_enabled = False
        self.hover_pos = None
        self.ruler_start = None
        self.ruler_end = None
        self.is_dragging = False
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

        if self.inpaint_patches and self.sm and self.sm.config:
            if bool(self.sm.config.get("ai_translation", "inpaint_auto_clear", True)):
                self._inpaint_check_tick += 1
                if self._inpaint_check_tick % 10 == 0:
                    if not check_textbox_still_present(frame_data, self.inpaint_patches):
                        self.inpaint_patches = []
                        self._inpaint_qimages = []

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

    def set_translation_boxes(
        self,
        boxes: list,
        source_frame: np.ndarray | None = None,
        precomputed_patches: list | None = None,
    ):
        self.subtitle_boxes = list(boxes or [])
        self.inpaint_patches = []
        self._inpaint_qimages = []

        inpaint_on = bool(self.sm.config.get("ai_translation", "inpaint_enabled", True)) if self.sm and self.sm.config else True
        inpaint_mode = str(self.sm.config.get("ai_translation", "inpaint_mode", "auto") or "auto") if self.sm and self.sm.config else "auto"
        ref_frame = source_frame if source_frame is not None else self.current_frame_data

        if inpaint_on and self.subtitle_boxes:
            if precomputed_patches is not None:
                patches = list(precomputed_patches)
            elif ref_frame is not None:
                patches = build_inpaint_patches(ref_frame, self.subtitle_boxes, mode=inpaint_mode)
            else:
                patches = []

            self.inpaint_patches = patches
            for p in patches:
                if getattr(p, "is_baked_graphic", False):
                    self._inpaint_qimages.append((p, None))
                else:
                    arr = p.patch_rgb
                    h, w, _ = arr.shape
                    qimg = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
                    self._inpaint_qimages.append((p, qimg))

        self.update()

    def clear_translation_boxes(self):
        if self.subtitle_boxes or self.inpaint_patches:
            self.subtitle_boxes = []
            self.inpaint_patches = []
            self._inpaint_qimages = []
            self.update()

    # =========================================================================
    # ADAPTIVE IN-PLACE TEXTBOX REPLACEMENT RENDERER
    # =========================================================================
    def _draw_inpaint_overlays(self, painter: QPainter):
        if not self._inpaint_qimages:
            return
        if self.sm and self.sm.config and not bool(self.sm.config.get("ai_translation", "inpaint_enabled", True)):
            return

        scale, ox, oy = self._get_viewport_transform()
        if scale <= 0.1:
            return

        raw_fscale = self.sm.config.get("ai_translation", "inpaint_font_scale", 1.0) if self.sm and self.sm.config else 1.0
        try:
            user_font_scale = max(0.6, min(1.6, float(raw_fscale if raw_fscale is not None else 1.0)))
        except (ValueError, TypeError):
            user_font_scale = 1.0

        families = ["Segoe UI", "Arial", "Tahoma", "Sans Serif"]
        flags_wrap = Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignTop.value | Qt.TextFlag.TextWordWrap.value
        flags_center = Qt.AlignmentFlag.AlignCenter.value

        for patch, qimg in self._inpaint_qimages:
            sx = int(round(ox + patch.gx * scale))
            sy = int(round(oy + patch.gy * scale))
            sw = max(8, int(round(patch.gw * scale)))
            sh = max(8, int(round(patch.gh * scale)))
            target_rect = QRect(sx, sy, sw, sh)

            b = patch.box
            trans = (getattr(b, "translated", "") or "").strip()
            if not trans:
                continue

            cat = getattr(b, "category", "dialogue")
            sp_orig = (getattr(b, "speaker_orig", "") or "").strip()
            sp_trans = (getattr(b, "speaker_trans", "") or "").strip()
            speaker = sp_trans or sp_orig

            if speaker:
                trans = re.sub(
                    rf"^\s*{re.escape(speaker)}\s*[:：\-]\s*",
                    "",
                    trans,
                    flags=re.IGNORECASE,
                ).strip() or trans

            is_baked = getattr(patch, "is_baked_graphic", False)
            painter.save()

            # Case A: Complex baked art fallback when MI-GAN ONNX is unavailable
            if is_baked or qimg is None:
                badge_rect = target_rect.adjusted(-2, -2, 2, 2)
                painter.setPen(QPen(QColor(255, 255, 255, 65), 1))
                painter.setBrush(QColor(16, 20, 28, 225))
                painter.drawRoundedRect(badge_rect, 4, 4)

                min_px = max(8, int(round(4.0 * scale * user_font_scale)))
                max_px = max(min_px, int(round(min(target_rect.height() * 0.80, 11.5 * scale * user_font_scale))))
                best_px = min_px
                low, high = min_px, max_px
                while low <= high:
                    mid = (low + high) // 2
                    f = self._make_font(families, 10, QFont.Weight.Bold)
                    f.setPixelSize(mid)
                    fm = QFontMetrics(f)
                    if fm.horizontalAdvance(trans) <= target_rect.width() and fm.height() <= target_rect.height() + 2:
                        best_px = mid
                        low = mid + 1
                    else:
                        high = mid - 1

                chosen_font = self._make_font(families, 10, QFont.Weight.Bold)
                chosen_font.setPixelSize(best_px)
                adv = QFontMetrics(chosen_font).horizontalAdvance(trans)
                if adv > target_rect.width() and adv > 0:
                    chosen_font.setStretch(max(65, int((target_rect.width() / float(adv)) * 96)))

                painter.setFont(chosen_font)
                painter.setPen(QColor(0, 0, 0, 200))
                painter.drawText(target_rect.translated(1, 1), flags_center, trans)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(target_rect, flags_center, trans)
                painter.restore()
                continue

            # Case B: Draw MI-GAN / Algorithmic clean reconstructed background patch
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(target_rect, qimg)

            is_button = (cat in ("menu", "label")) or (patch.gh <= 24)
            pad_x = max(2, int(round((1.5 if is_button else 4.0) * scale)))
            pad_y = max(1, int(round((1.0 if is_button else 3.0) * scale)))
            text_rect = target_rect.adjusted(pad_x, pad_y, -pad_x, -pad_y)
            if text_rect.width() <= 4 or text_rect.height() <= 4:
                painter.restore()
                continue

            if patch.is_dark_bg:
                ink_col = QColor(245, 248, 255)
                sp_col = QColor(255, 215, 64)
                shadow_col = QColor(0, 0, 0, 185)
            else:
                ink_col = QColor(18, 22, 32)
                sp_col = QColor(14, 72, 136)
                shadow_col = QColor(255, 255, 255, 120) if patch.bg_luma > 200 else None

            painter.setClipRect(target_rect)

            if is_button:
                min_px = max(8, int(round(4.0 * scale * user_font_scale)))
                max_px = max(min_px, int(round(min(text_rect.height() * 0.80, 11.5 * scale * user_font_scale))))
                best_px = min_px
                low, high = min_px, max_px
                while low <= high:
                    mid = (low + high) // 2
                    f = self._make_font(families, 10, QFont.Weight.Bold)
                    f.setPixelSize(mid)
                    fm = QFontMetrics(f)
                    if fm.horizontalAdvance(trans) <= text_rect.width() and fm.height() <= text_rect.height() + 2:
                        best_px = mid
                        low = mid + 1
                    else:
                        high = mid - 1

                chosen_font = self._make_font(families, 10, QFont.Weight.Bold)
                chosen_font.setPixelSize(best_px)
                adv = QFontMetrics(chosen_font).horizontalAdvance(trans)
                if adv > text_rect.width() and adv > 0:
                    chosen_font.setStretch(max(65, int((text_rect.width() / float(adv)) * 96)))

                painter.setFont(chosen_font)
                if shadow_col:
                    painter.setPen(shadow_col)
                    painter.drawText(text_rect.translated(1, 1), flags_center, trans)
                painter.setPen(ink_col)
                painter.drawText(text_rect, flags_center, trans)
            else:
                # Dialogue font scales natively to match 10-12px GBA bitmap glyphs
                sp_label = f"{speaker}:" if speaker else ""
                min_px = max(9, int(round(5.0 * scale * user_font_scale)))
                max_px = max(min_px, int(round(10.5 * scale * user_font_scale)))

                best_px = min_px
                best_sp_h = 0
                low, high = min_px, max_px
                while low <= high:
                    mid = (low + high) // 2
                    f_body = self._make_font(families, 10, QFont.Weight.DemiBold)
                    f_body.setPixelSize(mid)
                    fm_body = QFontMetrics(f_body)

                    sp_h = 0
                    if sp_label:
                        f_sp = self._make_font(families, 10, QFont.Weight.Bold)
                        f_sp.setPixelSize(mid)
                        fm_sp = QFontMetrics(f_sp)
                        sp_r = fm_sp.boundingRect(QRect(0, 0, text_rect.width(), 500), flags_wrap, sp_label)
                        sp_h = sp_r.height() + max(2, int(round(scale * 0.8)))

                    avail_h = text_rect.height() - sp_h
                    if avail_h > 0:
                        body_r = fm_body.boundingRect(QRect(0, 0, text_rect.width(), 2000), flags_wrap, trans)
                        if body_r.height() <= avail_h + 3 and body_r.width() <= text_rect.width() + 2:
                            best_px = mid
                            best_sp_h = sp_h
                            low = mid + 1
                            continue
                    high = mid - 1

                chosen_font = self._make_font(families, 10, QFont.Weight.DemiBold)
                chosen_font.setPixelSize(best_px)
                body_rect = QRect(text_rect)

                if sp_label and best_sp_h > 0:
                    sp_font = self._make_font(families, 10, QFont.Weight.Bold)
                    sp_font.setPixelSize(best_px)
                    painter.setFont(sp_font)
                    sp_rect = QRect(text_rect.x(), text_rect.y(), text_rect.width(), best_sp_h)
                    if shadow_col:
                        painter.setPen(shadow_col)
                        painter.drawText(sp_rect.translated(1, 1), flags_wrap, sp_label)
                    painter.setPen(sp_col)
                    painter.drawText(sp_rect, flags_wrap, sp_label)
                    body_rect.setTop(text_rect.y() + best_sp_h)

                painter.setFont(chosen_font)
                if shadow_col:
                    painter.setPen(shadow_col)
                    painter.drawText(body_rect.translated(1, 1), flags_wrap, trans)
                painter.setPen(ink_col)
                painter.drawText(body_rect, flags_wrap, trans)

            painter.restore()

    # =========================================================================
    # RETROARCH-STYLE ACHIEVEMENT UNLOCK TOAST
    # =========================================================================
    def show_achievement_popup(self, payload: dict):
        if self.sm and self.sm.config:
            if not bool(self.sm.config.get("retroachievements", "popup_notifications", True)):
                return

        badge_pix = None
        bp = payload.get("badge_path", "")
        if bp and os.path.exists(bp):
            px = QPixmap(bp)
            if not px.isNull():
                badge_pix = px

        entry = {
            "title": str(payload.get("title", "Achievement Unlocked")),
            "description": str(payload.get("description", "")),
            "points": int(payload.get("points", 0) or 0),
            "hardcore": bool(payload.get("hardcore", False)),
            "pixmap": badge_pix,
        }
        self._ach_queue.append(entry)
        if self._active_ach_toast is None:
            self._start_next_ach_toast()

    def _start_next_ach_toast(self):
        if not self._ach_queue:
            self._active_ach_toast = None
            self._ach_anim_timer.stop()
            self.update()
            return
        self._active_ach_toast = self._ach_queue.popleft()
        self._ach_toast_start = time.monotonic()
        if not self._ach_anim_timer.isActive():
            self._ach_anim_timer.start()
        self.update()

    def _tick_ach_toast(self):
        if self._active_ach_toast is None:
            self._ach_anim_timer.stop()
            return
        if (time.monotonic() - self._ach_toast_start) >= self._ach_toast_duration:
            self._start_next_ach_toast()
        else:
            self.update()

    def _draw_achievement_toast(self, painter: QPainter):
        if self._active_ach_toast is None:
            return

        elapsed = time.monotonic() - self._ach_toast_start
        dur, in_dur, out_dur = self._ach_toast_duration, 0.28, 0.35
        if elapsed < in_dur:
            t = elapsed / in_dur
            alpha = 1.0 - ((1.0 - t) ** 3)
            slide_y = int(-80 + (96 * alpha))
        elif elapsed > (dur - out_dur):
            alpha = max(0.0, (dur - elapsed) / out_dur)
            slide_y = int(-80 + (96 * alpha))
        else:
            alpha, slide_y = 1.0, 16

        toast = self._active_ach_toast
        hc = toast["hardcore"]
        box_w = max(270, min(420, int(self.width() * 0.44)))
        box_h, bx, by = 74, 16, slide_y

        painter.save()
        painter.setOpacity(max(0.0, min(1.0, alpha)))
        accent = QColor(255, 213, 79) if hc else QColor(76, 175, 80)
        painter.setPen(QPen(accent, 2))
        painter.setBrush(QColor(15, 19, 28, 238))
        painter.drawRoundedRect(bx, by, box_w, box_h, 8, 8)

        icon_sz = 52
        icon_rect = QRect(bx + 11, by + (box_h - icon_sz) // 2, icon_sz, icon_sz)
        painter.setPen(QPen(accent, 1))
        painter.setBrush(QColor(10, 12, 18))
        painter.drawRoundedRect(icon_rect, 4, 4)

        if toast["pixmap"] is not None:
            painter.drawPixmap(icon_rect.adjusted(2, 2, -2, -2), toast["pixmap"])
        else:
            painter.setFont(self._make_font(["Segoe UI Emoji", "Segoe UI", "Arial"], 20, QFont.Weight.Bold))
            painter.setPen(accent)
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, "🏆")

        tx = icon_rect.right() + 12
        tw = (bx + box_w) - tx - 10
        hdr_font = self._make_font(["Segoe UI", "Arial", "Sans Serif"], 8, QFont.Weight.Bold)
        title_font = self._make_font(["Segoe UI", "Arial", "Sans Serif"], 10, QFont.Weight.Bold)
        desc_font = self._make_font(["Segoe UI", "Arial", "Sans Serif"], 8, QFont.Weight.Normal)

        painter.setFont(hdr_font)
        painter.setPen(accent)
        painter.drawText(
            QRect(tx, by + 8, tw, 15),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "🏆 HARDCORE ACHIEVEMENT UNLOCKED!" if hc else "🏆 ACHIEVEMENT UNLOCKED!",
        )

        pts = toast["points"]
        title_str = f"{toast['title']} ({pts} pts)" if pts > 0 else toast["title"]
        painter.setFont(title_font)
        painter.setPen(QColor(245, 248, 255))
        painter.drawText(
            QRect(tx, by + 24, tw, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(title_font).elidedText(title_str, Qt.TextElideMode.ElideRight, tw),
        )

        painter.setFont(desc_font)
        painter.setPen(QColor(176, 190, 197))
        painter.drawText(
            QRect(tx, by + 43, tw, 24),
            Qt.AlignmentFlag.AlignLeft.value | Qt.TextFlag.TextWordWrap.value,
            toast["description"],
        )
        painter.restore()

    def _get_viewport_transform(self) -> tuple[float, float, float]:
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
            status_txt, status_col = f"● REC F:{self.input_hud_frame:05d}", QColor(255, 82, 82)
        elif self.input_hud_mode == "PLAYBACK":
            status_txt, status_col = f"▶ PLAY {self.input_hud_frame:04d}/{self.input_hud_max_frame:04d}", QColor(77, 208, 225)
        elif self.input_hud_mode == "REWIND":
            status_txt, status_col = f"⏪ REW F:{self.input_hud_frame:05d}", QColor(255, 213, 79)
        else:
            status_txt, status_col = f"F:{self.input_hud_frame:05d}", QColor(144, 202, 249)

        status_w = fm.horizontalAdvance(status_txt) + (6 if compact else 10)
        pill_widths = [max(14 if compact else 20, fm.horizontalAdvance(lbl) + (6 if compact else 10)) for _, lbl in OVERLAY_BUTTON_LABELS]
        total_w = pad_x * 2 + status_w + 6 + sum(pw + gap for pw in pill_widths)
        bar_h = pill_h + 8
        bx, by = 8, max(4, self.height() - bar_h - 8)

        painter.setPen(QPen(QColor(60, 72, 92, 200), 1))
        painter.setBrush(QColor(12, 16, 22, 195))
        painter.drawRoundedRect(bx, by, total_w, bar_h, 5, 5)

        py = by + 4
        painter.setPen(status_col)
        painter.drawText(QRect(bx + pad_x, py, status_w, pill_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, status_txt)

        cx = bx + pad_x + status_w + 4
        for (bit_idx, lbl), pw in zip(OVERLAY_BUTTON_LABELS, pill_widths):
            if self.input_hud_mask & (1 << bit_idx):
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
        tw = painter.fontMetrics().horizontalAdvance(self.clip_rec_label) + 16
        rect = QRect(8, 8, tw, 22)
        painter.setPen(QPen(QColor(255, 82, 82, 220), 1))
        painter.setBrush(QColor(25, 12, 16, 210))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor(255, 110, 110))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.clip_rec_label)

    def _build_subtitle_layout(self, boxes_to_draw: list, inner_max_w: int, shrink_pt: int = 0):
        families = ["Segoe UI", "Meiryo", "Arial", "Sans Serif"]
        sp_pt = max(9, min(15, int(self.height() / 76)) - shrink_pt)
        dlg_pt = max(10, min(18, int(self.height() / 62)) - shrink_pt)
        tag_pt = max(9, min(15, int(self.height() / 72)) - shrink_pt)

        speaker_font = self._make_font(families, sp_pt, QFont.Weight.Bold)
        dialogue_font = self._make_font(families, dlg_pt, QFont.Weight.Bold)
        tag_font = self._make_font(families, tag_pt, QFont.Weight.DemiBold)
        fm_sp, fm_dlg, fm_tag = QFontMetrics(speaker_font), QFontMetrics(dialogue_font), QFontMetrics(tag_font)

        entries, used_w = [], 0
        for b in boxes_to_draw:
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
                    sp_r = fm_sp.boundingRect(QRect(0, 0, inner_max_w, 2000), Qt.TextFlag.TextWordWrap, sp_line)
                    entries.append((speaker_font, QColor(255, 213, 79), sp_line, sp_r.height() + 2, 2))
                    used_w = max(used_w, sp_r.width() + 8)
                body_r = fm_dlg.boundingRect(QRect(0, 0, inner_max_w, 2000), Qt.TextFlag.TextWordWrap, trans)
                entries.append((dialogue_font, QColor(235, 245, 255), trans, body_r.height() + 2, 7))
                used_w = max(used_w, body_r.width() + 8)
            else:
                tag_lbl = "MENU" if cat == "menu" else "LABEL"
                tag_col = QColor(129, 199, 132) if cat == "menu" else QColor(255, 213, 79)
                line = f"[{tag_lbl}] {trans}"
                t_r = fm_tag.boundingRect(QRect(0, 0, inner_max_w, 2000), Qt.TextFlag.TextWordWrap, line)
                entries.append((tag_font, tag_col, line, t_r.height() + 2, 4))
                used_w = max(used_w, t_r.width() + 8)

        if not entries:
            return [], 0, 0
        total_h = sum(item_h + gap for _, _, _, item_h, gap in entries[:-1]) + entries[-1][3]
        return entries, used_w, total_h

    def _draw_subtitle_overlay(self, painter: QPainter):
        parent_win = self.window()
        if not parent_win or not parent_win.isFullScreen():
            return

        overlay_enabled = bool(self.sm.config.get("ai_translation", "overlay_enabled", True)) if self.sm and self.sm.config else True
        if not overlay_enabled or not self.subtitle_boxes:
            return

        inpaint_on = bool(self.sm.config.get("ai_translation", "inpaint_enabled", True)) if self.sm and self.sm.config else True
        if inpaint_on and self._inpaint_qimages:
            inpainted_ids = {id(p.box) for p, _ in self._inpaint_qimages}
            boxes_to_draw = [b for b in self.subtitle_boxes if id(b) not in inpainted_ids]
        else:
            boxes_to_draw = self.subtitle_boxes

        if not boxes_to_draw:
            return

        max_box_w = max(280, min(int(self.width() * 0.82), 1040))
        pad_x, pad_y = 18, 14
        inner_max_w = max_box_w - (pad_x * 2)
        max_allowed_h = int(self.height() * 0.72)

        entries, used_w, total_h = self._build_subtitle_layout(boxes_to_draw, inner_max_w, shrink_pt=0)
        for shrink in (2, 4, 6):
            if not entries or (total_h + pad_y * 2) <= max_allowed_h:
                break
            entries, used_w, total_h = self._build_subtitle_layout(boxes_to_draw, inner_max_w, shrink_pt=shrink)

        if not entries:
            return

        box_w = min(max_box_w, max(260, used_w + (pad_x * 2)))
        box_h = min(max_allowed_h, total_h + (pad_y * 2))
        bottom_margin = 48 if self._is_input_hud_visible() else 20
        bx, by = (self.width() - box_w) // 2, max(16, self.height() - box_h - bottom_margin)

        painter.setPen(QPen(QColor(100, 150, 220, 195), 2))
        painter.setBrush(QColor(12, 16, 24, 224))
        painter.drawRoundedRect(bx, by, box_w, box_h, 10, 10)

        painter.save()
        painter.setClipRect(QRect(bx + pad_x, by + pad_y, box_w - (pad_x * 2), box_h - (pad_y * 2)))
        draw_flags = Qt.AlignmentFlag.AlignLeft.value | Qt.TextFlag.TextWordWrap.value
        cur_y = by + pad_y
        draw_w = box_w - (pad_x * 2)
        for font, color, text, item_h, gap in entries:
            if cur_y >= by + box_h - pad_y:
                break
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(QRect(bx + pad_x, cur_y, draw_w, item_h), draw_flags, text)
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
        self._draw_inpaint_overlays(painter)

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
                screen_x, screen_y = ox + rx * scale, oy + ry * scale
                screen_w, screen_h = rw * scale, rh * scale

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
        self._draw_achievement_toast(painter)

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
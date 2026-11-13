# gui.py
import os
import sys
import html
import math
import datetime
import numpy as np

from PyQt6.QtWidgets import (QMainWindow, QWidget, QFileDialog, QMessageBox, 
                             QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QComboBox, QPushButton, QTextEdit, QSplitter, 
                             QFrame, QSizePolicy)
from PyQt6.QtGui import (QImage, QPainter, QAction, QActionGroup, QKeySequence, 
                         QColor, QFont, QPen, QBrush)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QEvent, QSize
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import QOpenGLTexture

from ..core.core_worker import EmulatorWorker
from .controller import ControllerManager, ControllerConfigDialog, LIBRETRO_BTNS, QT_KEY_MAP
from ..tools.dev_tools import (PaletteViewerDialog, SpriteViewerDialog, TileViewerDialog,
                               MapViewerDialog, FrameInspectorDialog, MemoryViewerDialog,
                               MemorySearchDialog, IORegisterViewerDialog)
from ..tools.ai_translator import TranslationWorker
from .shader_manager import RetroArchShaderManager
from .shader_dialog import ShaderConfigDialog
from ..paths import PROJECT_ROOT


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

        # Clear any pre-existing GL errors
        while GL.glGetError() != GL.GL_NO_ERROR:
            pass

        # Create clean 240x160 RGB OpenGL texture
        self.tex_id = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex_id)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB8, 240, 160, 0, GL.GL_RGB, GL.GL_UNSIGNED_BYTE, None)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)  # Always restore default alignment for Qt QPainter
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

        # Initialize Shader Manager VBO/VAO & compile shaders
        self.sm.set_gl_widget(self)
        self.sm.init_gl()

    def set_frame(self, frame_data: np.ndarray):
        self.current_frame_data = frame_data
        self._need_texture_update = True
        self.update()

    def paintGL(self):
        from OpenGL import GL

        # 1. Zero-copy pixel upload to GPU VRAM
        if self._need_texture_update and self.current_frame_data is not None and self.tex_id is not None:
            h, w, _ = self.current_frame_data.shape
            buf = np.ascontiguousarray(self.current_frame_data, dtype=np.uint8)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex_id)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_RGB, GL.GL_UNSIGNED_BYTE, buf)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)  # Restore default 4-byte alignment for Qt font glyphs
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            self._need_texture_update = False

        # 2. Calculate High-DPI aware viewport size
        dpr = self.devicePixelRatioF()
        phys_w = max(1, int(self.width() * dpr))
        phys_h = max(1, int(self.height() * dpr))
        default_fbo = self.defaultFramebufferObject()

        # 3. Render through RetroArch Shader Chain
        if self.tex_id is not None:
            self.sm.render(self.tex_id, 240, 160, phys_w, phys_h, default_fbo)

        # 4. Restore default widget FBO, viewport, and unpack alignment cleanly before QPainter overlay
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, default_fbo)
        GL.glViewport(0, 0, phys_w, phys_h)
        GL.glUseProgram(0)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)

        painter = QPainter(self)

        # Pixel Ruler Overlay
        if self.ruler_enabled:
            img_w, img_h = 240, 160
            scale = min(self.width() / img_w, self.height() / img_h)
            ox = (self.width() - img_w * scale) / 2
            oy = (self.height() - img_h * scale) / 2

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

        # Global OSD Message (Top-Right)
        if self.osd_text:
            painter.setPen(QPen(QColor(0, 255, 0)))
            painter.setFont(QFont("Arial", 14, QFont.Weight.Bold))
            painter.drawText(self.rect().adjusted(10, 10, -10, -10), 
                             Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, 
                             self.osd_text)

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
        scale = min(self.width() / img_w, self.height() / img_h)
        offset_x = (self.width() - (img_w * scale)) / 2
        offset_y = (self.height() - (img_h * scale)) / 2
        
        gba_x = int((pos.x() - offset_x) / scale)
        gba_y = int((pos.y() - offset_y) / scale)
        return max(0, min(img_w - 1, gba_x)), max(0, min(img_h - 1, gba_y))

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() in [Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton]:
            parent_win = self.window()
            if hasattr(parent_win, 'config') and parent_win.config.get("sensors", "mouse_gyro"):
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
        if not self.gyro_drag_center: return
        dx = pos.x() - self.gyro_drag_center.x()
        dy = pos.y() - self.gyro_drag_center.y()
        if dx == 0 and dy == 0: return

        angle = math.degrees(math.atan2(dx, -dy))
        parent_win = self.window()
        if hasattr(parent_win, 'worker') and parent_win.worker:
            parent_win.worker.set_gyro_angle(angle)



# gui.py
import os
import sys
import html
import math
import datetime
import numpy as np
from PIL import Image

from PyQt6.QtWidgets import (QMainWindow, QWidget, QFileDialog, QMessageBox, 
                             QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QComboBox, QPushButton, QTextEdit, QSplitter, 
                             QFrame, QSizePolicy)
from PyQt6.QtGui import (QImage, QPainter, QAction, QActionGroup, QKeySequence, 
                         QColor, QFont, QPen, QBrush)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QEvent, QSize

from core_worker import EmulatorWorker
from controller import ControllerManager, ControllerConfigDialog, LIBRETRO_BTNS, QT_KEY_MAP
from dev_tools import (PaletteViewerDialog, SpriteViewerDialog, TileViewerDialog, 
                       MapViewerDialog, FrameInspectorDialog, MemoryViewerDialog, 
                       MemorySearchDialog, IORegisterViewerDialog)
from ai_translator import TranslationWorker


class VideoWidget(QWidget):
    """High-speed rendering surface for emulator video with pixel ruler support."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        # Allow fluid scaling down to 1x without enforcing minimum dimensions
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True) 
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.current_frame_data = None
        self.qimage = None
        
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

    def set_frame(self, frame_data: np.ndarray):
        self.current_frame_data = frame_data
        height, width, channel = frame_data.shape
        bytes_per_line = 3 * width
        self.qimage = QImage(self.current_frame_data.data, width, height, 
                             bytes_per_line, QImage.Format.Format_RGB888)
        self.update()

    def show_osd(self, text: str, duration_ms: int = 2000):
        self.osd_text = text
        self.osd_timer.start(duration_ms)
        self.update()

    def clear_osd(self):
        self.osd_text = ""
        self.update()

    def get_gba_coords(self, pos) -> tuple:
        if not self.qimage:
            return 0, 0
        widget_w, widget_h = self.width(), self.height()
        img_w, img_h = self.qimage.width(), self.qimage.height()
        
        scale = min(widget_w / img_w, widget_h / img_h)
        offset_x = (widget_w - (img_w * scale)) / 2
        offset_y = (widget_h - (img_h * scale)) / 2
        
        gba_x = int((pos.x() - offset_x) / scale)
        gba_y = int((pos.y() - offset_y) / scale)
        
        return max(0, min(img_w - 1, gba_x)), max(0, min(img_h - 1, gba_y))

    def mousePressEvent(self, event):
        self.setFocus()

        # Mouse Gyro Twist Gesture (Right-Click or Middle-Click drag)
        if event.button() in [Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton]:
            parent_win = self.window()
            if hasattr(parent_win, 'config') and parent_win.config.get("sensors", "mouse_gyro"):
                self.mouse_gyro_active = True
                self.gyro_drag_center = QPoint(self.width() // 2, self.height() // 2)
                self._update_mouse_gyro(event.position().toPoint())
                self.update()
                return

        # Pixel Ruler Drag
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        if self.qimage:
            scaled_img = self.qimage.scaled(self.rect().size(), 
                                            Qt.AspectRatioMode.KeepAspectRatio, 
                                            Qt.TransformationMode.FastTransformation)
            x = (self.width() - scaled_img.width()) // 2
            y = (self.height() - scaled_img.height()) // 2
            painter.drawImage(x, y, scaled_img)

            img_w, img_h = self.qimage.width(), self.qimage.height()
            scale = min(self.width() / img_w, self.height() / img_h)
            ox = (self.width() - img_w * scale) / 2
            oy = (self.height() - img_h * scale) / 2

            # Pixel Ruler Overlay
            if self.ruler_enabled:
                if self.hover_pos:
                    hx, hy = self.hover_pos
                    sx, sy = ox + hx * scale, oy + hy * scale
                    painter.setPen(QPen(QColor(0, 255, 255, 150), 1))
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
            painter.setFont(QFont("Arial", 15, QFont.Weight.Bold))
            painter.drawText(self.rect().adjusted(10, 10, -10, -10), 
                             Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, 
                             self.osd_text)
        painter.end()


class SensorHudWidget(QFrame):
    """Dynamically responsive status bar with fluid multi-tier scaling."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            SensorHudWidget {
                background-color: #12151D;
                border-top: 1px solid #232938;
                border-bottom: 1px solid #232938;
            }
            QLabel {
                font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                font-weight: bold;
            }
        """)

        self.game_title = "Unknown"
        self.game_code = "----"
        self.hw_type = "None"
        self.current_solar_level = 5
        self.rumble_active = False
        self.rumble_strength = 0.0
        self.current_tilt_x = 0.0
        self.current_tilt_y = 0.0
        self.current_gyro_angle = 0.0

        self._rumble_off_timer = QTimer(self)
        self._rumble_off_timer.setSingleShot(True)
        self._rumble_off_timer.timeout.connect(self._clear_rumble)

        self.layout_box = QHBoxLayout(self)
        self.layout_box.setContentsMargins(6, 0, 6, 0)
        self.layout_box.setSpacing(6)

        # Ignored size policies & 0 minimum width prevent any restriction on window resizing
        self.lbl_game = QLabel()
        self.lbl_game.setStyleSheet("color: #90CAF9;")
        self.lbl_game.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_game.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_game, 2)

        self.lbl_solar = QLabel()
        self.lbl_solar.setStyleSheet("color: #FFD54F;")
        self.lbl_solar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_solar.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_solar, 1)

        self.lbl_rumble = QLabel()
        self.lbl_rumble.setStyleSheet("color: #78909C;")
        self.lbl_rumble.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_rumble.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_rumble, 1)

        self.lbl_gyro = QLabel()
        self.lbl_gyro.setStyleSheet("color: #4DD0E1;")
        self.lbl_gyro.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_gyro.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_gyro, 1)

        self.update_display()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def sizeHint(self) -> QSize:
        return QSize(240, 30)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_display()

    def update_display(self):
        w = self.width()

        # 1. Game Title Label
        type_str = f" ({self.hw_type.replace('_', ' ').title()})" if self.hw_type not in ["None", ""] else ""
        if w < 380:
            self.lbl_game.setText(f"[{self.game_code}]")
        elif w < 650:
            short_t = self.game_title[:10] + ".." if len(self.game_title) > 10 else self.game_title
            self.lbl_game.setText(f"🎮 [{self.game_code}] {short_t}")
        else:
            self.lbl_game.setText(f"🎮 Game: [{self.game_code}] {self.game_title}{type_str}")

        # 2. Solar Indicator (Boktai)
        if w < 480:
            self.lbl_solar.setText(f"☀️ {self.current_solar_level}/10")
        else:
            sun_filled = "■" * self.current_solar_level
            sun_empty = "□" * (10 - self.current_solar_level)
            self.lbl_solar.setText(f"☀️ Solar: [{sun_filled}{sun_empty}] {self.current_solar_level}/10")

        # 3. Rumble Motor Indicator
        if self.rumble_active:
            pct = int(self.rumble_strength * 100)
            filled = max(1, min(5, int(self.rumble_strength * 5)))
            bars = "■" * filled + "□" * (5 - filled)
            if w < 480:
                self.lbl_rumble.setText(f"📳 {pct}%")
            else:
                self.lbl_rumble.setText(f"📳 Rumble: [{bars}] {pct}%")
            self.lbl_rumble.setStyleSheet("color: #FF5252;")
        else:
            if w < 480:
                self.lbl_rumble.setText("📳 OFF")
            else:
                self.lbl_rumble.setText("📳 Rumble: [□□□□□] OFF")
            self.lbl_rumble.setStyleSheet("color: #78909C;")

        # 4. Gyro / Tilt Indicator
        deg = int(self.current_gyro_angle) % 360
        if w < 480:
            self.lbl_gyro.setText(f"🎯 {deg}°")
        else:
            idx = max(0, min(10, int((self.current_tilt_x + 1.0) * 5.0)))
            meter = list("─────●─────")
            meter[idx] = "●"
            meter_str = "".join(meter)
            self.lbl_gyro.setText(f"🎯 Gyro: [{meter_str}] {deg:3d}°")

    def on_game_identified(self, title: str, code: str, hw_type: str):
        self.game_title = title
        self.game_code = code
        self.hw_type = hw_type
        self.update_display()

    def on_solar_updated(self, level: int):
        self.current_solar_level = level
        self.update_display()

    def on_rumble_triggered(self, low_freq: float, high_freq: float, duration_ms: int):
        if low_freq <= 0.0 and high_freq <= 0.0:
            self._clear_rumble()
            return

        self.rumble_active = True
        self.rumble_strength = max(low_freq, high_freq)
        self.update_display()
        self._rumble_off_timer.start(max(90, duration_ms))

    def _clear_rumble(self):
        self.rumble_active = False
        self.rumble_strength = 0.0
        self.update_display()

    def on_tilt_updated(self, x: float, y: float, angle: float):
        self.current_tilt_x = x
        self.current_tilt_y = y
        self.current_gyro_angle = angle
        self.update_display()


class AITranslationConfigDialog(QDialog):
    LANGS = [
        ("Japanese", "ja"),
        ("English", "en"),
        ("Vietnamese", "vi")
    ]

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Configure AI Translation")
        self.resize(380, 200)

        layout = QVBoxLayout(self)

        row_src = QHBoxLayout()
        row_src.addWidget(QLabel("Source Language (Original):"))
        self.src_combo = QComboBox()
        for label, code in self.LANGS:
            self.src_combo.addItem(label, code)
        row_src.addWidget(self.src_combo)
        layout.addLayout(row_src)

        row_tgt = QHBoxLayout()
        row_tgt.addWidget(QLabel("Target Language (Translate to):"))
        self.tgt_combo = QComboBox()
        for label, code in self.LANGS:
            self.tgt_combo.addItem(label, code)
        row_tgt.addWidget(self.tgt_combo)
        layout.addLayout(row_tgt)

        curr_src = self.config.get("ai_translation", "source_lang") or "ja"
        curr_tgt = self.config.get("ai_translation", "target_lang") or "vi"

        idx_src = self.src_combo.findData(curr_src)
        if idx_src >= 0:
            self.src_combo.setCurrentIndex(idx_src)

        idx_tgt = self.tgt_combo.findData(curr_tgt)
        if idx_tgt >= 0:
            self.tgt_combo.setCurrentIndex(idx_tgt)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save Settings")
        btn_save.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 6px;")
        btn_save.clicked.connect(self.save_and_close)
        btn_row.addWidget(btn_save)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def save_and_close(self):
        self.config.set("ai_translation", "source_lang", self.src_combo.currentData())
        self.config.set("ai_translation", "target_lang", self.tgt_combo.currentData())
        self.config.save()
        self.accept()


class MainWindow(QMainWindow):
    if sys.platform.startswith('win'):
        DEFAULT_CORE = "mgba_libretro.dll"
    elif sys.platform.startswith('linux'):
        DEFAULT_CORE = "mgba_libretro.so"
    else:
        DEFAULT_CORE = "mgba_libretro.dylib"

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.worker = None
        self.current_rom = None
        
        # Restore active scale factor
        saved_scale = self.config.get("video", "scale")
        try:
            self.scale_factor = max(1, min(6, int(saved_scale))) if saved_scale is not None else 3
        except (ValueError, TypeError):
            self.scale_factor = 3

        self.current_slot = self.config.get("emulation", "state_slot") or 1
        self.active_tool_window = None 
        self.trans_worker = None
        self._auto_paused = False
        
        self.setWindowTitle("mGBA (PyQt6 Replica)")
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.core_path = os.path.join(script_dir, self.DEFAULT_CORE)

        self.controller_manager = ControllerManager(self.config)
        self.gamepad_timer = QTimer(self)
        self.gamepad_timer.timeout.connect(self.poll_gamepad)
        self.gamepad_timer.start(8)

        # Vertical Splitter: Video -> HUD Panel -> Subtitle Panel
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setHandleWidth(3)
        self.splitter.setStyleSheet("QSplitter::handle { background-color: #282828; }")
        self.splitter.splitterMoved.connect(self.on_splitter_moved)

        # 1. Game Screen Widget
        self.video_widget = VideoWidget()
        self.splitter.addWidget(self.video_widget)

        # 2. Separated Sensor & Rumble HUD Panel
        self.sensor_hud = SensorHudWidget()
        show_hud_pref = bool(self.config.get("sensors", "show_hud", True))
        self.sensor_hud.setVisible(show_hud_pref)
        self.splitter.addWidget(self.sensor_hud)

        # 3. Translation Subtitle Panel
        self.translation_panel = QTextEdit()
        self.translation_panel.setReadOnly(True)
        self.translation_panel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.translation_panel.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.translation_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.translation_panel.setMinimumSize(0, 50)
        self.translation_panel.setStyleSheet("""
            QTextEdit {
                background-color: #101216;
                color: #E0E0E0;
                font-family: 'Segoe UI', 'Meiryo', sans-serif;
                font-size: 13px;
                border: none;
                padding: 6px 10px;
                line-height: 1.4;
            }
        """)
        self.translation_panel.setPlaceholderText("Press [T] to translate screen with AI...")
        self.splitter.addWidget(self.translation_panel)

        self.splitter.setStretchFactor(0, 8)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 2)

        # Restore window geometry & splitter heights from persistent configuration
        saved_w = self.config.get("video", "window_width")
        saved_h = self.config.get("video", "window_height")
        saved_splitter = self.config.get("video", "splitter_sizes")

        default_hud_h = 30 if show_hud_pref else 0
        default_panel_h = 75 if self.scale_factor <= 2 else 115
        default_vid_h = 160 * self.scale_factor
        default_vid_w = 240 * self.scale_factor

        if saved_w and saved_h:
            try:
                self.resize(int(saved_w), int(saved_h))
            except Exception:
                self.resize(default_vid_w, default_vid_h + default_hud_h + default_panel_h)
        else:
            self.resize(default_vid_w, default_vid_h + default_hud_h + default_panel_h)

        if saved_splitter and isinstance(saved_splitter, list) and len(saved_splitter) == 3:
            try:
                sz = [int(s) for s in saved_splitter]
                if not show_hud_pref:
                    sz[1] = 0
                elif sz[1] <= 0:
                    sz[1] = 30
                self.splitter.setSizes(sz)
            except Exception:
                self.splitter.setSizes([default_vid_h, default_hud_h, default_panel_h])
        else:
            self.splitter.setSizes([default_vid_h, default_hud_h, default_panel_h])

        self.setCentralWidget(self.splitter)
        
        self._create_menus()
        self._sync_ui_with_config()
        self.update_recent_menu()
        self.video_widget.setFocus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.isMaximized() and not self.isMinimized():
            self.config.set("video", "window_width", self.width())
            self.config.set("video", "window_height", self.height())
            if hasattr(self, 'splitter'):
                self.config.set("video", "splitter_sizes", self.splitter.sizes())

    def on_splitter_moved(self, pos, index):
        if not self.isMaximized() and not self.isMinimized():
            self.config.set("video", "splitter_sizes", self.splitter.sizes())

    def changeEvent(self, event):
        """Automatically pauses emulation when the window loses focus and resumes when focused."""
        if event.type() == QEvent.Type.ActivationChange:
            pause_enabled = bool(self.config.get("emulation", "pause_on_unfocus", True))
            if not self.isActiveWindow():
                if pause_enabled and self.worker and hasattr(self, 'is_running') and self.is_running and not self.worker.is_paused:
                    self._auto_paused = True
                    self.worker.is_paused = True
                    self.pause_action.setChecked(True)
                    self.video_widget.show_osd("⏸ Auto-Paused (Window Inactive)", 1500)
            else:
                if getattr(self, '_auto_paused', False):
                    self._auto_paused = False
                    if self.worker and hasattr(self, 'is_running') and self.is_running:
                        self.worker.is_paused = False
                        self.pause_action.setChecked(False)
                        self.video_widget.show_osd("▶ Resumed", 1000)
        super().changeEvent(event)

    def on_manual_rumble_test(self):
        self.sensor_hud.on_rumble_triggered(1.0, 1.0, 300)

    def on_game_identified(self, title: str, code: str, hw_type: str):
        display_title = f"mGBA - {title} [{code}]"
        if hw_type != "None":
            display_title += f" ({hw_type.replace('_', ' ').title()})"
        self.setWindowTitle(display_title)
        self.video_widget.show_osd(f"Loaded: {title} [{code}]", 2500)

    def _create_menus(self):
        menubar = self.menuBar()

        # 1. FILE MENU
        file_menu = menubar.addMenu("File")
        
        load_action = QAction("Load ROM...", self)
        load_action.setShortcut(QKeySequence("Ctrl+O"))
        load_action.triggered.connect(self.load_rom)
        file_menu.addAction(load_action)

        self.recent_menu = file_menu.addMenu("Open Recent")
        file_menu.addSeparator()

        save_file_act = QAction("Save State to File...", self)
        save_file_act.triggered.connect(self.save_state_to_file)
        file_menu.addAction(save_file_act)

        load_file_act = QAction("Load State from File...", self)
        load_file_act.triggered.connect(self.load_state_from_file)
        file_menu.addAction(load_file_act)
        
        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 2. EMULATION MENU
        emu_menu = menubar.addMenu("Emulation")
        
        self.pause_action = QAction("Pause", self, checkable=True)
        self.pause_action.setShortcut(QKeySequence("Ctrl+P"))
        self.pause_action.triggered.connect(self.toggle_pause)
        emu_menu.addAction(self.pause_action)

        self.pause_unfocus_action = QAction("Pause When Unfocused / Inactive", self, checkable=True)
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))
        self.pause_unfocus_action.triggered.connect(self.toggle_pause_on_unfocus)
        emu_menu.addAction(self.pause_unfocus_action)

        step_act = QAction("Step Frame", self)
        step_act.setShortcut(QKeySequence("Ctrl+N"))
        step_act.triggered.connect(self.step_frame)
        emu_menu.addAction(step_act)

        reset_action = QAction("Reset", self)
        reset_action.setShortcut(QKeySequence("Ctrl+R"))
        reset_action.triggered.connect(self.reset_emulation)
        emu_menu.addAction(reset_action)
        
        emu_menu.addSeparator()

        self.skip_bios_action = QAction("Skip BIOS Intro", self, checkable=True)
        self.skip_bios_action.setChecked(bool(self.config.get("emulation", "skip_bios")))
        self.skip_bios_action.triggered.connect(self.toggle_skip_bios)
        emu_menu.addAction(self.skip_bios_action)

        emu_menu.addSeparator()

        quick_save_act = QAction("Quick Save State", self)
        quick_save_act.setShortcut(QKeySequence("Shift+F10"))
        quick_save_act.triggered.connect(lambda: self.quick_save_state(self.current_slot))
        emu_menu.addAction(quick_save_act)

        quick_load_act = QAction("Quick Load State", self)
        quick_load_act.setShortcut(QKeySequence("F10"))
        quick_load_act.triggered.connect(lambda: self.quick_load_state(self.current_slot))
        emu_menu.addAction(quick_load_act)

        slot_menu = emu_menu.addMenu("Select State Slot")
        self.slot_group = QActionGroup(self)
        self.slot_group.setExclusive(True)
        self.slot_actions = {}

        for slot_num in range(1, 10):
            slot_act = QAction(f"Slot {slot_num}", self, checkable=True)
            slot_act.triggered.connect(lambda checked, s=slot_num: self.set_active_slot(s))
            self.slot_group.addAction(slot_act)
            slot_menu.addAction(slot_act)
            self.slot_actions[slot_num] = slot_act

        emu_menu.addSeparator()

        self.ff_action = QAction("Fast Forward (Hotkey: = / -)", self, checkable=True)
        self.ff_action.triggered.connect(self.toggle_ff)
        emu_menu.addAction(self.ff_action)

        ff_speed_menu = emu_menu.addMenu("Fast Forward Speed")
        self.ff_group = QActionGroup(self)
        self.ff_group.setExclusive(True)
        self.ff_actions = {}
        
        speeds = [1.5, 2.0, 3.0, 4.0, 8.0, 10.0]
        for spd in speeds:
            spd_act = QAction(f"{spd}x", self, checkable=True)
            spd_act.triggered.connect(lambda checked, s=spd: self.set_ff_speed(s))
            self.ff_group.addAction(spd_act)
            ff_speed_menu.addAction(spd_act)
            self.ff_actions[spd] = spd_act

        act_ff_up = QAction("Faster Fast-Forward Speed (+)", self)
        act_ff_up.setShortcut(QKeySequence("="))
        act_ff_up.triggered.connect(lambda: self.cycle_ff_speed(forward=True))
        ff_speed_menu.addAction(act_ff_up)

        act_ff_down = QAction("Slower Fast-Forward Speed (-)", self)
        act_ff_down.setShortcut(QKeySequence("-"))
        act_ff_down.triggered.connect(lambda: self.cycle_ff_speed(forward=False))
        ff_speed_menu.addAction(act_ff_down)

        emu_menu.addSeparator()
        replay_menu = emu_menu.addMenu("Gameplay Replay & Recording")
        
        self.act_record_session = QAction("⏺ Start Recording Session...", self)
        self.act_record_session.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.act_record_session.triggered.connect(self.toggle_session_recording)
        replay_menu.addAction(self.act_record_session)

        self.act_play_session = QAction("▶ Load & Play Replay (.json)...", self)
        self.act_play_session.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.act_play_session.triggered.connect(self.load_replay_session)
        replay_menu.addAction(self.act_play_session)

        self.act_stop_replay = QAction("⏹ Stop Current Replay", self)
        self.act_stop_replay.triggered.connect(self.stop_replay_session)
        replay_menu.addAction(self.act_stop_replay)

        # 3. SENSORS & PERIPHERALS MENU
        sensors_menu = menubar.addMenu("Sensors & Rumble")

        self.act_sensor_hud = QAction("Show Sensor / Rumble Status Panel", self, checkable=True)
        self.act_sensor_hud.setChecked(bool(self.config.get("sensors", "show_hud", True)))
        self.act_sensor_hud.triggered.connect(self.toggle_sensor_hud)
        sensors_menu.addAction(self.act_sensor_hud)
        sensors_menu.addSeparator()

        solar_menu = sensors_menu.addMenu("☀️ Solar Sensor (Boktai)")
        self.solar_group = QActionGroup(self)
        self.solar_group.setExclusive(True)
        self.solar_actions = {}

        for lvl in range(11):
            act = QAction(f"Sun Level {lvl}" + (" (Maximum)" if lvl == 10 else (" (Dark)" if lvl == 0 else "")), self, checkable=True)
            act.triggered.connect(lambda checked, l=lvl: self.set_solar_level(l))
            self.solar_group.addAction(act)
            solar_menu.addAction(act)
            self.solar_actions[lvl] = act

        act_brighten = QAction("Brighten Sunlight (+1)", self)
        act_brighten.setShortcut(QKeySequence("]"))
        act_brighten.triggered.connect(lambda: self.adjust_solar_sensor(1))
        solar_menu.addAction(act_brighten)

        act_darken = QAction("Darken Sunlight (-1)", self)
        act_darken.setShortcut(QKeySequence("["))
        act_darken.triggered.connect(lambda: self.adjust_solar_sensor(-1))
        solar_menu.addAction(act_darken)

        sensors_menu.addSeparator()

        self.act_rumble = QAction("🎮 Controller Motor Rumble", self, checkable=True)
        self.act_rumble.setChecked(bool(self.config.get("sensors", "rumble_enabled")))
        self.act_rumble.triggered.connect(self.toggle_rumble)
        sensors_menu.addAction(self.act_rumble)

        self.act_mouse_gyro = QAction("🖱️ Mouse Twist to Gyro (Right-Click Drag)", self, checkable=True)
        self.act_mouse_gyro.setChecked(bool(self.config.get("sensors", "mouse_gyro")))
        self.act_mouse_gyro.triggered.connect(self.toggle_mouse_gyro)
        sensors_menu.addAction(self.act_mouse_gyro)

        act_reset_tilt = QAction("🎯 Center Gyro & Tilt Sensor", self)
        act_reset_tilt.setShortcut(QKeySequence("Backspace"))
        act_reset_tilt.triggered.connect(self.reset_sensors)
        sensors_menu.addAction(act_reset_tilt)

        # 4. AUDIO MENU
        audio_menu = menubar.addMenu("Audio")
        self.mute_action = QAction("Mute", self, checkable=True)
        self.mute_action.setChecked(self.config.get("audio", "mute"))
        self.mute_action.triggered.connect(self.toggle_mute)
        audio_menu.addAction(self.mute_action)

        audio_menu.addSeparator()

        lpf_menu = audio_menu.addMenu("Low-Pass Filter")
        self.lpf_group = QActionGroup(self)
        self.lpf_group.setExclusive(True)
        self.lpf_actions = {}

        lpf_modes = [
            ("off", "Off (Raw DirectSound)"),
            ("light", "Light (~12 kHz - Mild Hiss Reduction)"),
            ("medium", "Medium (~8 kHz - Warm Speaker Sound)"),
            ("heavy", "Heavy (~5 kHz - Smooth Vintage Sound)")
        ]
        for mode_key, label in lpf_modes:
            act = QAction(label, self, checkable=True)
            act.triggered.connect(lambda checked, m=mode_key: self.set_lpf_mode(m))
            self.lpf_group.addAction(act)
            lpf_menu.addAction(act)
            self.lpf_actions[mode_key] = act

        enh_menu = audio_menu.addMenu("Enhancements")
        self.widener_action = QAction("Stereo Widener (Surround Sound)", self, checkable=True)
        self.widener_action.triggered.connect(self.toggle_stereo_widener)
        enh_menu.addAction(self.widener_action)

        self.bass_action = QAction("Bass Boost (Low-End Warmth)", self, checkable=True)
        self.bass_action.triggered.connect(self.toggle_bass_boost)
        enh_menu.addAction(self.bass_action)

        self.limiter_action = QAction("Soft Limiter (Anti-Clipping)", self, checkable=True)
        self.limiter_action.triggered.connect(self.toggle_soft_limiter)
        enh_menu.addAction(self.limiter_action)

        vol_menu = audio_menu.addMenu("Volume Gain")
        self.vol_group = QActionGroup(self)
        self.vol_group.setExclusive(True)
        self.vol_actions = {}

        vol_levels = [0.25, 0.5, 0.75, 1.0, 1.25]
        for vol in vol_levels:
            lbl = f"{int(vol * 100)}%" + (" (Default)" if abs(vol - 0.5) < 0.01 else "")
            act = QAction(lbl, self, checkable=True)
            act.triggered.connect(lambda checked, v=vol: self.set_volume_gain(v))
            self.vol_group.addAction(act)
            vol_menu.addAction(act)
            self.vol_actions[vol] = act

        # 5. VIDEO MENU
        video_menu = menubar.addMenu("Video")

        self.blend_action = QAction("Interframe Blending", self, checkable=True)
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.blend_action.triggered.connect(self.toggle_interframe_blending)
        video_menu.addAction(self.blend_action)

        frameskip_menu = video_menu.addMenu("Frameskip")
        self.fs_group = QActionGroup(self)
        self.fs_group.setExclusive(True)
        self.fs_actions = {}

        for fs in range(0, 6):
            label = "0 (Disabled)" if fs == 0 else f"{fs} Frames"
            fs_act = QAction(label, self, checkable=True)
            fs_act.triggered.connect(lambda checked, count=fs: self.set_frameskip(count))
            self.fs_group.addAction(fs_act)
            frameskip_menu.addAction(fs_act)
            self.fs_actions[fs] = fs_act

        video_menu.addSeparator()

        screenshot_action = QAction("Screenshot", self)
        screenshot_action.setShortcut(QKeySequence("F12"))
        screenshot_action.triggered.connect(self.take_screenshot)
        video_menu.addAction(screenshot_action)
        
        video_menu.addSeparator()

        # Scale Factor Menu with Exclusive Selection & Clean 3:2 Geometric Resizing
        scale_menu = video_menu.addMenu("Window Scale")
        self.scale_group = QActionGroup(self)
        self.scale_group.setExclusive(True)
        self.scale_actions = {}

        for s in range(1, 7):
            scale_act = QAction(f"{s}x Scale ({240*s}×{160*s})", self, checkable=True)
            scale_act.triggered.connect(lambda checked, sc=s: self.set_scale(sc))
            self.scale_group.addAction(scale_act)
            scale_menu.addAction(scale_act)
            self.scale_actions[s] = scale_act

        # 6. GAME STATE VIEWS MENU
        views_menu = menubar.addMenu("Game state views")

        dossier_act = QAction("🚀 Instant AI Scene Dump...", self)
        dossier_act.setShortcut(QKeySequence("Ctrl+Shift+D"))
        dossier_act.triggered.connect(self.trigger_instant_scene_dump)
        views_menu.addAction(dossier_act)
        views_menu.addSeparator()

        views_menu.addAction("View palettes...", lambda: self._open_dev_tool(PaletteViewerDialog))
        views_menu.addAction("View sprites...", lambda: self._open_dev_tool(SpriteViewerDialog))
        views_menu.addAction("View tiles...", lambda: self._open_dev_tool(TileViewerDialog))
        views_menu.addAction("View map...", lambda: self._open_dev_tool(MapViewerDialog))
        views_menu.addSeparator()
        views_menu.addAction("Frame inspector...", lambda: self._open_dev_tool(FrameInspectorDialog))

        hex_act = QAction("View memory (Hex Editor)...", self)
        hex_act.setShortcut(QKeySequence("Ctrl+H"))
        hex_act.triggered.connect(lambda: self._open_dev_tool(MemoryViewerDialog))
        views_menu.addAction(hex_act)

        views_menu.addAction("Search memory...", lambda: self._open_dev_tool(MemorySearchDialog))
        views_menu.addAction("View I/O registers...", lambda: self._open_dev_tool(IORegisterViewerDialog))

        # 7. TRANSLATION MENU
        translation_menu = menubar.addMenu("Translation")
        
        ai_trans_act = QAction("⚡ Translate Screen with AI (Hotkey: T)", self)
        ai_trans_act.setShortcut(QKeySequence("T"))
        ai_trans_act.triggered.connect(self.trigger_ai_translation)
        translation_menu.addAction(ai_trans_act)

        clear_panel_act = QAction("Clear Subtitle Panel", self)
        clear_panel_act.triggered.connect(self.translation_panel.clear)
        translation_menu.addAction(clear_panel_act)

        ai_cfg_act = QAction("Configure AI Translation...", self)
        ai_cfg_act.triggered.connect(self.open_ai_translation_config)
        translation_menu.addAction(ai_cfg_act)

        translation_menu.addSeparator()

        text_inspector_act = QAction("Text Inspector & Script Auto-Repointer...", self)
        text_inspector_act.triggered.connect(self.open_translation_tools)
        translation_menu.addAction(text_inspector_act)

        font_tools_act = QAction("Font Studio & TTF Generator...", self)
        font_tools_act.triggered.connect(self.open_font_tools)
        translation_menu.addAction(font_tools_act)
        
        translation_menu.addSeparator()
        
        self.ruler_action = QAction("Screen Pixel Ruler Overlay", self, checkable=True)
        self.ruler_action.triggered.connect(self.toggle_pixel_ruler)
        translation_menu.addAction(self.ruler_action)

        # 8. TOOLS MENU
        tools_menu = menubar.addMenu("Tools")

        editor_act = QAction("In-Game Graphic Editor (YY-CHR)...", self)
        editor_act.setShortcut(QKeySequence("Ctrl+G"))
        editor_act.triggered.connect(self.open_graphic_editor)
        tools_menu.addAction(editor_act)

        hex_tool_act = QAction("Interactive Hex Editor (HxD / ImHex)...", self)
        hex_tool_act.triggered.connect(lambda: self._open_dev_tool(MemoryViewerDialog))
        tools_menu.addAction(hex_tool_act)

        tools_menu.addSeparator()
        tools_menu.addAction("Cheats Manager...", self.open_cheats_manager)
        tools_menu.addSeparator()
        tools_menu.addAction("Configure Controls & Motion Feedback...", self.open_controller_config)
        tools_menu.addSeparator()
        
        reset_settings_act = QAction("Reset Settings to Default...", self)
        reset_settings_act.triggered.connect(self.reset_settings_to_default)
        tools_menu.addAction(reset_settings_act)

    def set_scale(self, scale: int):
        """Applies exact GBA 3:2 geometric scaling with zero pillarboxing and full persistence."""
        self.scale_factor = scale
        self.config.set("video", "scale", scale)
        if scale in self.scale_actions:
            self.scale_actions[scale].setChecked(True)

        hud_h = 30 if self.sensor_hud.isVisible() else 0
        panel_h = 75 if scale <= 2 else 115
        video_h = 160 * scale
        video_w = 240 * scale
        total_h = video_h + hud_h + panel_h

        # Update splitter elements proportionally
        self.splitter.setSizes([video_h, hud_h, panel_h])
        # Resize window without any minimum width constraint blocking the dimensions
        self.resize(video_w, total_h)
        
        self.config.set("video", "window_width", video_w)
        self.config.set("video", "window_height", total_h)
        self.config.set("video", "splitter_sizes", [video_h, hud_h, panel_h])
        self.config.save()
        self.video_widget.show_osd(f"Window Scale: {scale}x ({video_w}×{video_h})")

    # =========================================================================
    # SENSORS & MOTION SLOTS
    # =========================================================================
    def toggle_pause_on_unfocus(self):
        val = self.pause_unfocus_action.isChecked()
        self.config.set("emulation", "pause_on_unfocus", val)
        self.video_widget.show_osd(f"Pause When Unfocused: {'ON' if val else 'OFF'}")

    def toggle_sensor_hud(self):
        val = self.act_sensor_hud.isChecked()
        self.config.set("sensors", "show_hud", val)
        self.sensor_hud.setVisible(val)

        sizes = self.splitter.sizes()
        if len(sizes) == 3:
            sizes[1] = 30 if val else 0
            self.splitter.setSizes(sizes)
            self.config.set("video", "splitter_sizes", sizes)
        self.config.save()
        self.video_widget.show_osd(f"Sensor HUD: {'ON' if val else 'OFF'}")

    def set_solar_level(self, level: int):
        if self.worker:
            self.worker.set_solar_level(level)
            self.sensor_hud.on_solar_updated(level)
            self.video_widget.show_osd(f"☀️ Solar Sensor: Level {level}/10")

    def adjust_solar_sensor(self, delta: int):
        if self.worker:
            self.worker.step_solar_level(delta)
            cur = self.worker.solar_level
            self.sensor_hud.on_solar_updated(cur)
            if cur in self.solar_actions:
                self.solar_actions[cur].setChecked(True)
            self.video_widget.show_osd(f"☀️ Solar Sensor: Level {cur}/10")

    def toggle_rumble(self):
        val = self.act_rumble.isChecked()
        self.config.set("sensors", "rumble_enabled", val)
        self.video_widget.show_osd(f"Rumble Motor: {'ON' if val else 'OFF'}")

    def toggle_mouse_gyro(self):
        val = self.act_mouse_gyro.isChecked()
        self.config.set("sensors", "mouse_gyro", val)
        self.video_widget.show_osd(f"Mouse Gyro Drag: {'ON' if val else 'OFF'}")

    def reset_sensors(self):
        if self.worker:
            self.worker.reset_sensors()
            self.sensor_hud.on_tilt_updated(0.0, 0.0, 0.0)
            self.video_widget.show_osd("🎯 Gyro/Tilt Centered (0°)")

    # =========================================================================
    # REPLAY & EMULATION SLOTS
    # =========================================================================
    def toggle_session_recording(self):
        if not self.worker or not self.current_rom:
            self.video_widget.show_osd("Error: No ROM running.")
            return

        replays_dir = self.config.get("paths", "replays") or "replays"

        if not self.worker.recorder.is_recording:
            saved_state_path = self.worker.start_recording_session(replays_dir)
            if saved_state_path:
                self.act_record_session.setText("⏹ Stop Recording Session")
                self.video_widget.show_osd("⏺ RECORDING STARTED", 3000)
            else:
                self.video_widget.show_osd("Error: Could not save initial state.")
        else:
            json_path = self.worker.stop_recording_session(replays_dir)
            self.act_record_session.setText("⏺ Start Recording Session...")
            if json_path:
                self.video_widget.show_osd(f"Saved Replay: {os.path.basename(json_path)}", 4000)
                QMessageBox.information(
                    self, "Session Recorded", 
                    f"Gameplay session and initial snapshot saved successfully!\n\n"
                    f"Replay File (AI-Friendly JSON):\n{json_path}"
                )

    def load_replay_session(self):
        if not self.worker or not self.current_rom:
            self.video_widget.show_osd("Error: Load ROM first.")
            return

        replays_dir = self.config.get("paths", "replays") or "replays"
        os.makedirs(replays_dir, exist_ok=True)
        
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Load Replay Session", replays_dir, "Replay Files (*.replay.json *.json)"
        )
        if not filepath:
            return

        success = self.worker.start_replay_session(filepath)
        if success:
            self.video_widget.show_osd(f"▶ REPLAYING: {os.path.basename(filepath)}", 3000)
        else:
            QMessageBox.critical(
                self, "Replay Error", 
                "Failed to start replay. Ensure the corresponding .state file exists in the same folder."
            )

    def stop_replay_session(self):
        if self.worker and self.worker.recorder.is_playing:
            self.worker.stop_replay_session()
            self.video_widget.show_osd("⏹ Replay Stopped. Control Restored.")

    def on_replay_finished(self):
        self.video_widget.show_osd("🏁 Replay Complete! Control Restored.", 3000)

    def trigger_ai_translation(self):
        if not self.worker or not self.current_rom or self.video_widget.current_frame_data is None:
            self.video_widget.show_osd("Error: Game not running.")
            return

        if self.trans_worker and self.trans_worker.isRunning():
            self.video_widget.show_osd("AI Translation in progress...")
            return

        self.video_widget.show_osd("🔍 Running AI Screen Translation...")

        self.trans_worker = TranslationWorker(
            frame_data=self.video_widget.current_frame_data.copy(),
            rom_path=self.current_rom,
            config=self.config
        )
        self.trans_worker.finished.connect(self.on_ai_translation_finished)
        self.trans_worker.error.connect(lambda err: QMessageBox.warning(self, "AI Translation Error", err))
        self.trans_worker.start()

    def on_ai_translation_finished(self, boxes: list, msg: str):
        if boxes:
            blocks_html = []
            for b in boxes:
                orig = html.escape((b.original or "").strip())
                trans = html.escape((b.translated or "").strip())
                if not orig or not trans:
                    continue

                cat = getattr(b, "category", "dialogue")
                sp_orig = html.escape(getattr(b, "speaker_orig", "") or "")
                sp_trans = html.escape(getattr(b, "speaker_trans", "") or "")

                if cat == "dialogue":
                    accent = "#64B5F6" if sp_orig else "#BA68C8"
                    if sp_orig and sp_trans and sp_trans.lower() != sp_orig.lower():
                        speaker_html = (
                            f'<div style="color: #FFD54F; font-weight: bold; font-size: 12px; margin-bottom: 2px;">'
                            f'💬 {sp_trans} <span style="color: #90A4AE; font-weight: normal;">({sp_orig})</span></div>'
                        )
                    elif sp_orig:
                        speaker_html = (
                            f'<div style="color: #FFD54F; font-weight: bold; font-size: 12px; margin-bottom: 2px;">'
                            f'💬 {sp_orig}</div>'
                        )
                    else:
                        speaker_html = ""

                    blocks_html.append(
                        f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom: 6px;">'
                        f'<tr>'
                        f'<td width="4" bgcolor="{accent}"></td>'
                        f'<td bgcolor="#181C24" style="padding: 6px 10px;">'
                        f'{speaker_html}'
                        f'<div style="color: #90CAF9; font-size: 12px; margin-bottom: 2px;">{orig}</div>'
                        f'<div style="color: #A5D6A7; font-weight: bold; font-size: 14px;">→ {trans}</div>'
                        f'</td>'
                        f'</tr></table>'
                    )

                elif cat == "menu":
                    blocks_html.append(
                        f'<div style="margin-bottom: 3px; padding: 2px 4px;">'
                        f'<span style="background-color: #1E3A44; color: #4DD0E1; font-size: 11px; font-weight: bold;">'
                        f'&nbsp;MENU&nbsp;</span>&nbsp;&nbsp;'
                        f'<span style="color: #B0BEC5;">{orig}</span> '
                        f'<span style="color: #78909C;">→</span> '
                        f'<span style="color: #81C784; font-weight: bold;">{trans}</span>'
                        f'</div>'
                    )

                else:
                    blocks_html.append(
                        f'<div style="margin-bottom: 4px; padding: 2px 4px;">'
                        f'<span style="background-color: #3E2C18; color: #FFB74D; font-size: 11px; font-weight: bold;">'
                        f'&nbsp;LABEL&nbsp;</span>&nbsp;&nbsp;'
                        f'<span style="color: #FFE082;">{orig}</span> '
                        f'<span style="color: #78909C;">→</span> '
                        f'<span style="color: #FFD54F; font-weight: bold;">{trans}</span>'
                        f'</div>'
                    )

            self.translation_panel.setHtml("".join(blocks_html))
            sb = self.translation_panel.verticalScrollBar()
            sb.setValue(0)

        self.video_widget.show_osd(msg, 3000)

    def open_ai_translation_config(self):
        dlg = AITranslationConfigDialog(self.config, self)
        dlg.exec()

    def trigger_instant_scene_dump(self):
        if not self.worker or not self.current_rom:
            self.video_widget.show_osd("Error: No active ROM to dump")
            return
        try:
            from scene_dump import SceneDumpEngine
            engine = SceneDumpEngine(self.worker, self.config)
            dump_folder = engine.create_compact_dump(self.current_rom)
            short_path = os.path.basename(dump_folder)
            self.video_widget.show_osd(f"AI Dossier Dumped: {short_path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Dump Error", f"Failed to create scene dump: {e}")

    def update_recent_menu(self):
        self.recent_menu.clear()
        recent_files = self.config.get_recent_files()

        if not recent_files:
            no_recents = QAction("No Recent Files", self)
            no_recents.setEnabled(False)
            self.recent_menu.addAction(no_recents)
            return

        for path in recent_files:
            file_name = os.path.basename(path)
            action = QAction(f"{file_name} ({path})", self)
            action.triggered.connect(lambda checked, p=path: self.load_rom_file(p))
            self.recent_menu.addAction(action)

        self.recent_menu.addSeparator()
        clear_act = QAction("Clear Recent Files", self)
        clear_act.triggered.connect(self.clear_recent_files)
        self.recent_menu.addAction(clear_act)

    def clear_recent_files(self):
        self.config.clear_recent_files()
        self.update_recent_menu()
        self.video_widget.show_osd("Cleared Recent Files")

    def _sync_ui_with_config(self):
        self.mute_action.setChecked(bool(self.config.get("audio", "mute")))
        
        lpf = str(self.config.get("audio", "low_pass_filter") or "medium").lower()
        if lpf in self.lpf_actions:
            self.lpf_actions[lpf].setChecked(True)
        self.widener_action.setChecked(bool(self.config.get("audio", "stereo_widener")))
        self.bass_action.setChecked(bool(self.config.get("audio", "bass_boost")))
        self.limiter_action.setChecked(bool(self.config.get("audio", "soft_limiter")))

        raw_vol = self.config.get("audio", "volume")
        current_vol = float(raw_vol) if raw_vol is not None else 0.5
        if current_vol > 2.0:
            current_vol = 0.5
            self.config.set("audio", "volume", 0.5)

        closest_vol = min(self.vol_actions.keys(), key=lambda v: abs(v - current_vol))
        self.vol_actions[closest_vol].setChecked(True)

        self.skip_bios_action.setChecked(bool(self.config.get("emulation", "skip_bios")))
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))

        self.current_slot = int(self.config.get("emulation", "state_slot") or 1)
        if self.current_slot in self.slot_actions:
            self.slot_actions[self.current_slot].setChecked(True)

        current_ff = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
        for spd, act in self.ff_actions.items():
            if abs(spd - current_ff) < 0.01:
                act.setChecked(True)
                break

        current_fs = int(self.config.get("video", "frameskip") or 0)
        if current_fs in self.fs_actions:
            self.fs_actions[current_fs].setChecked(True)

        # Sync active scale factor radio item
        if self.scale_factor in self.scale_actions:
            self.scale_actions[self.scale_factor].setChecked(True)

        solar_lvl = int(self.config.get("sensors", "solar_level") if self.config.get("sensors") else 5)
        if solar_lvl in self.solar_actions:
            self.solar_actions[solar_lvl].setChecked(True)
        self.sensor_hud.on_solar_updated(solar_lvl)

        self._build_keymap()

    def set_lpf_mode(self, mode: str):
        self.config.set("audio", "low_pass_filter", mode)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Low-Pass Filter: {mode.capitalize()}")

    def toggle_stereo_widener(self):
        val = self.widener_action.isChecked()
        self.config.set("audio", "stereo_widener", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Stereo Widener: {'ON' if val else 'OFF'}")

    def toggle_bass_boost(self):
        val = self.bass_action.isChecked()
        self.config.set("audio", "bass_boost", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Bass Boost: {'ON' if val else 'OFF'}")

    def toggle_soft_limiter(self):
        val = self.limiter_action.isChecked()
        self.config.set("audio", "soft_limiter", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Soft Limiter: {'ON' if val else 'OFF'}")

    def set_volume_gain(self, vol: float):
        self.config.set("audio", "volume", vol)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Volume: {int(vol * 100)}%")

    def toggle_mute(self):
        self.config.set("audio", "mute", self.mute_action.isChecked())
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd("Audio settings applied")

    def reset_settings_to_default(self):
        confirm = QMessageBox.question(
            self, "Reset Settings",
            "Are you sure you want to reset all settings, controls, sensors, and audio options back to default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.config.reset_to_defaults()
            self._sync_ui_with_config()
            self.update_recent_menu()
            if self.worker:
                self.worker.update_audio_settings()
            self.video_widget.show_osd("Settings Reset to Default")
            QMessageBox.information(self, "Success", "All settings have been restored to factory defaults.")

    def _open_dev_tool(self, dialog_cls):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to inspect game state.")
            return
        self.active_tool_window = dialog_cls(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_graphic_editor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to edit graphics.")
            return
        from graphic_editor import GraphicEditorDialog
        self.active_tool_window = GraphicEditorDialog(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_translation_tools(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to inspect memory.")
            return
        try:
            from translation_tools import TranslationToolsDialog
            self.active_tool_window = TranslationToolsDialog(self.worker, self.config, self)
            self.active_tool_window.show()
        except ImportError as e:
            QMessageBox.critical(self, "Error", f"Failed to load translation tools: {e}")

    def open_font_tools(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to scan fonts.")
            return
        try:
            from font_tools import FontToolsDialog
            self.active_tool_window = FontToolsDialog(self.worker, self.config, self)
            self.active_tool_window.show()
        except ImportError as e:
            QMessageBox.critical(self, "Error", f"Failed to load font tools: {e}")

    def open_cheats_manager(self):
        if not self.worker:
            QMessageBox.warning(self, "Error", "Emulation must be running to manage cheats.")
            return
        from cheats import CheatManagerWindow
        self.active_tool_window = CheatManagerWindow(self.worker.cheat_engine, self.config, self)
        self.active_tool_window.show()

    def open_controller_config(self):
        dlg = ControllerConfigDialog(self.config, self.controller_manager, self)
        if dlg.exec():
            self._build_keymap()

    def toggle_pixel_ruler(self):
        self.video_widget.ruler_enabled = self.ruler_action.isChecked()
        self.video_widget.show_osd("Pixel Ruler: " + ("ON" if self.ruler_action.isChecked() else "OFF"))
        self.video_widget.update()

    def _build_keymap(self):
        self.keymap = {}
        self.autofire_keymap = {}
        keyboard_cfg = self.config.get("input", "keyboard") or {}
        
        for gba_btn, user_key in keyboard_cfg.items():
            if gba_btn in LIBRETRO_BTNS:
                qt_key = QT_KEY_MAP.get(str(user_key).lower())
                if qt_key:
                    self.keymap[qt_key] = LIBRETRO_BTNS[gba_btn]

        for af_btn in ["autofire_a", "autofire_b", "autofire_l", "autofire_r"]:
            user_key = keyboard_cfg.get(af_btn)
            if user_key:
                qt_key = QT_KEY_MAP.get(str(user_key).lower())
                if qt_key:
                    self.autofire_keymap[qt_key] = af_btn.replace("autofire_", "")

    def poll_gamepad(self):
        if self.worker and hasattr(self, 'is_running') and self.is_running:
            self.controller_manager.poll_gamepad_events(self.worker)

    def load_rom(self):
        filters = "Supported ROMs (*.gba *.gb *.gbc *.sgb *.zip);;GBA ROMs (*.gba);;GB/GBC ROMs (*.gb *.gbc);;All Files (*.*)"
        filepath, _ = QFileDialog.getOpenFileName(self, "Load Game", "", filters)
        if filepath:
            self.load_rom_file(filepath)

    def load_rom_file(self, filepath: str):
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "File Error", f"ROM not found:\n{filepath}")
            return
        self.current_rom = filepath
        self.config.add_recent_file(filepath)
        self.update_recent_menu()
        self.start_emulation()

    def start_emulation(self):
        if not os.path.exists(self.core_path):
            QMessageBox.critical(self, "Core Error", f"Core not found:\n{self.core_path}")
            return

        if self.worker:
            self.worker.stop()
            self.worker.wait()

        self.worker = EmulatorWorker(self.core_path, self.current_rom, self.config)
        self.worker.frame_ready.connect(self.video_widget.set_frame)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.replay_finished.connect(self.on_replay_finished)
        
        # Connect Sensors, Dedicated HUD, and Physical Rumble Signals
        self.worker.rumble_triggered.connect(self.sensor_hud.on_rumble_triggered)
        self.worker.rumble_triggered.connect(self.controller_manager.trigger_rumble)
        self.worker.solar_changed.connect(self.sensor_hud.on_solar_updated)
        self.worker.tilt_changed.connect(self.sensor_hud.on_tilt_updated)
        self.worker.game_identified.connect(self.sensor_hud.on_game_identified)
        self.worker.game_identified.connect(self.on_game_identified)
        
        self.worker.fast_forward = self.ff_action.isChecked()
        self.worker.is_paused = self.pause_action.isChecked()
        
        self.worker.start()
        self.is_running = True

    def reset_emulation(self):
        if self.worker:
            self.worker.request_reset()
            self.video_widget.show_osd("Reset")

    def step_frame(self):
        if self.worker:
            self.worker.step_one_frame()
            self.pause_action.setChecked(True)
            self.video_widget.show_osd("Stepped 1 Frame")

    def handle_error(self, err_msg):
        QMessageBox.warning(self, "Emulation Error", err_msg)

    def toggle_pause(self):
        if self.worker:
            self.worker.is_paused = self.pause_action.isChecked()
            self.video_widget.show_osd("Paused" if self.worker.is_paused else "Resumed")

    def toggle_ff(self):
        if self.worker:
            self.worker.fast_forward = self.ff_action.isChecked()
            spd = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
            self.video_widget.show_osd(f"Fast Forward: {'ON (' + str(spd) + 'x)' if self.worker.fast_forward else 'OFF'}")

    def cycle_ff_speed(self, forward: bool = True):
        speeds = [1.5, 2.0, 3.0, 4.0, 8.0, 10.0]
        cur = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
        closest_idx = min(range(len(speeds)), key=lambda i: abs(speeds[i] - cur))

        if forward:
            new_idx = min(len(speeds) - 1, closest_idx + 1)
        else:
            new_idx = max(0, closest_idx - 1)

        new_spd = speeds[new_idx]
        self.set_ff_speed(new_spd)
        if not self.ff_action.isChecked():
            self.ff_action.setChecked(True)
            self.toggle_ff()

    def toggle_skip_bios(self):
        enabled = self.skip_bios_action.isChecked()
        self.config.set("emulation", "skip_bios", enabled)
        self.video_widget.show_osd(f"Skip BIOS: {'ON' if enabled else 'OFF'} (Applies on next load)")

    def set_ff_speed(self, ratio: float):
        self.config.set("emulation", "fast_forward_ratio", ratio)
        if ratio in self.ff_actions:
            self.ff_actions[ratio].setChecked(True)
        self.video_widget.show_osd(f"Fast Forward Speed: {ratio}x")

    def toggle_interframe_blending(self):
        enabled = self.blend_action.isChecked()
        self.config.set("video", "interframe_blending", enabled)
        self.video_widget.show_osd(f"Interframe Blending: {'ON' if enabled else 'OFF'}")

    def set_frameskip(self, count: int):
        self.config.set("video", "frameskip", count)
        self.video_widget.show_osd(f"Frameskip: {count}")

    def set_active_slot(self, slot: int):
        self.current_slot = slot
        self.config.set("emulation", "state_slot", slot)
        self.video_widget.show_osd(f"Selected State Slot {slot}")

    def take_screenshot(self):
        if not self.worker or self.video_widget.current_frame_data is None:
            return
        out_dir = self.config.get("paths", "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        filename = f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        Image.fromarray(self.video_widget.current_frame_data, 'RGB').save(os.path.join(out_dir, filename))
        self.video_widget.show_osd("Screenshot Captured!")

    def get_state_path(self, slot: int):
        state_dir = self.config.get("paths", "states")
        os.makedirs(state_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(self.current_rom))[0]
        return os.path.join(state_dir, f"{base_name}.ss{slot}")

    def quick_save_state(self, slot: int):
        if not self.worker:
            return
        self.worker.save_state(self.get_state_path(slot))
        self.video_widget.show_osd(f"Saved to Slot {slot}")

    def quick_load_state(self, slot: int):
        if not self.worker:
            return
        path = self.get_state_path(slot)
        if os.path.exists(path):
            self.worker.load_state(path)
            self.video_widget.show_osd(f"Loaded from Slot {slot}")
        else:
            self.video_widget.show_osd(f"Slot {slot} is empty!")

    def save_state_to_file(self):
        if not self.worker:
            return
        state_dir = self.config.get("paths", "states")
        os.makedirs(state_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(self.current_rom))[0]
        default_path = os.path.join(state_dir, f"{base_name}.state")
        filepath, _ = QFileDialog.getSaveFileName(self, "Save State to File", default_path, "Save States (*.state *.ss*)")
        if filepath:
            self.worker.save_state(filepath)
            self.video_widget.show_osd(f"Saved: {os.path.basename(filepath)}")

    def load_state_from_file(self):
        if not self.worker:
            return
        state_dir = self.config.get("paths", "states")
        filepath, _ = QFileDialog.getOpenFileName(self, "Load State from File", state_dir, "Save States (*.state *.ss*)")
        if filepath:
            self.worker.load_state(filepath)
            self.video_widget.show_osd(f"Loaded: {os.path.basename(filepath)}")

    def keyPressEvent(self, event):
        if not self.worker:
            return

        # Fast-Forward Hotkeys: '=' (Toggle / Speed Up) and '-' (Toggle / Slow Down)
        if event.key() in [Qt.Key.Key_Equal, Qt.Key.Key_Plus] and not event.isAutoRepeat():
            if not self.ff_action.isChecked():
                self.ff_action.setChecked(True)
                self.toggle_ff()
            else:
                self.cycle_ff_speed(forward=True)
            return

        if event.key() in [Qt.Key.Key_Minus, Qt.Key.Key_Underscore] and not event.isAutoRepeat():
            if not self.ff_action.isChecked():
                self.ff_action.setChecked(True)
                self.toggle_ff()
            else:
                cur_ratio = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
                if abs(cur_ratio - 1.5) < 0.01:
                    self.ff_action.setChecked(False)
                    self.toggle_ff()
                else:
                    self.cycle_ff_speed(forward=False)
            return

        # Solar Sensor Hotkeys (Boktai)
        if event.key() == Qt.Key.Key_BracketRight:
            self.adjust_solar_sensor(1)
            return
        if event.key() == Qt.Key.Key_BracketLeft:
            self.adjust_solar_sensor(-1)
            return

        # Gyro / Tilt Simulation Hotkeys
        if event.key() == Qt.Key.Key_Q:
            new_ang = (self.sensor_hud.current_gyro_angle - 15) % 360
            self.worker.set_gyro_angle(new_ang)
            return
        if event.key() == Qt.Key.Key_E:
            new_ang = (self.sensor_hud.current_gyro_angle + 15) % 360
            self.worker.set_gyro_angle(new_ang)
            return
        if event.key() == Qt.Key.Key_Backspace:
            self.reset_sensors()
            return

        if event.key() == Qt.Key.Key_T:
            self.trigger_ai_translation()
            return

        if ((event.key() == Qt.Key.Key_D and 
             event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)) 
            or event.key() == Qt.Key.Key_F11):
            self.trigger_instant_scene_dump()
            return

        if event.key() == Qt.Key.Key_G and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.open_graphic_editor()
            return

        if event.key() == Qt.Key.Key_H and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._open_dev_tool(MemoryViewerDialog)
            return

        if event.key() == Qt.Key.Key_Tab and not event.isAutoRepeat():
            self.ff_action.setChecked(True)
            self.toggle_ff()
            return

        if event.key() == Qt.Key.Key_R and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.reset_emulation()
            return

        if event.key() == Qt.Key.Key_N and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.step_frame()
            return

        if event.key() == Qt.Key.Key_F10:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.quick_save_state(self.current_slot)
            else:
                self.quick_load_state(self.current_slot)
            return

        if Qt.Key.Key_F1 <= event.key() <= Qt.Key.Key_F9:
            slot = event.key() - Qt.Key.Key_F1 + 1
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.quick_save_state(slot)
            else:
                self.quick_load_state(slot)
            return

        af_btn = self.autofire_keymap.get(event.key())
        if af_btn and not event.isAutoRepeat():
            self.worker.set_autofire(af_btn, True)
            return

        btn_id = self.keymap.get(event.key())
        if btn_id is not None and not event.isAutoRepeat():
            self.worker.set_input(btn_id, True)

    def keyReleaseEvent(self, event):
        if not self.worker:
            return

        if event.key() == Qt.Key.Key_Tab and not event.isAutoRepeat():
            self.ff_action.setChecked(False)
            self.toggle_ff()
            return

        af_btn = self.autofire_keymap.get(event.key())
        if af_btn and not event.isAutoRepeat():
            self.worker.set_autofire(af_btn, False)
            return

        btn_id = self.keymap.get(event.key())
        if btn_id is not None and not event.isAutoRepeat():
            self.worker.set_input(btn_id, False)

    def closeEvent(self, event):
        self.is_running = False
        if hasattr(self, 'gamepad_timer'):
            self.gamepad_timer.stop()
        if self.worker:
            self.worker.stop()
            self.worker.wait()
            
        # Persist window geometry, splitter sizes, scale factor, and HUD visibility
        if not self.isMaximized() and not self.isMinimized():
            self.config.set("video", "window_width", self.width())
            self.config.set("video", "window_height", self.height())
            if hasattr(self, 'splitter'):
                self.config.set("video", "splitter_sizes", self.splitter.sizes())
        self.config.set("video", "scale", self.scale_factor)
        self.config.set("sensors", "show_hud", self.sensor_hud.isVisible())
        self.config.save()
        event.accept()
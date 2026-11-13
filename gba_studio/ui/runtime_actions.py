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


from .video_widget import VideoGLWidget
from .sensor_hud import SensorHudWidget

class RuntimeActionsMixin:
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
        from PIL import Image
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


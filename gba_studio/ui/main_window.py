# main_window.py
import os
import sys

from PyQt6.QtWidgets import QMainWindow, QTextEdit, QSplitter, QSizePolicy, QMessageBox
from PyQt6.QtCore import Qt, QTimer, QEvent

from ..core.clip_recorder import ClipRecorder
from ..core.netplay import NetplayEngine
from .controller import ControllerManager
from ..tools.dev_tools import MemoryViewerDialog
from ..tools.ai_translator import AutoTranslateDetector
from .shader_manager import RetroArchShaderManager
from ..paths import PROJECT_ROOT

from .video_widget import VideoGLWidget
from .sensor_hud import SensorHudWidget

from .menu_actions import MenuBuilderMixin
from .application_actions import ApplicationActionsMixin
from .settings_actions import SettingsActionsMixin
from .runtime_actions import RuntimeActionsMixin

ROM_DROP_EXTS = {".gba", ".gb", ".gbc", ".sgb", ".zip", ".7z"}
PATCH_DROP_EXTS = {".ips", ".bps", ".ups"}


class MainWindow(QMainWindow, MenuBuilderMixin, ApplicationActionsMixin, SettingsActionsMixin, RuntimeActionsMixin):
    if sys.platform.startswith("win"):
        DEFAULT_CORE = "mgba_libretro.dll"
    elif sys.platform.startswith("linux"):
        DEFAULT_CORE = "mgba_libretro.so"
    else:
        DEFAULT_CORE = "mgba_libretro.dylib"

    def __init__(self, config, player_slot: int = 1):
        super().__init__()
        self.config = config
        self.player_slot = max(1, int(player_slot))
        self.worker = None
        self.current_rom = None
        self.clean_rom = None
        self.source_rom = None
        self.active_patch = None

        saved_scale = self.config.get("video", "scale")
        try:
            self.scale_factor = max(1, min(6, int(saved_scale))) if saved_scale is not None else 3
        except (ValueError, TypeError):
            self.scale_factor = 3

        self.current_slot = self.config.get("emulation", "state_slot") or 1
        self.active_tool_window = None
        self.trans_worker = None
        self._auto_paused = False

        # Per-Game Playtime & Stats State
        self._active_stats_pid = None
        self._active_stats_title = ""
        self._base_playtime_sec = 0.0
        self._session_playtime_sec = 0.0
        self._unsaved_playtime_sec = 0.0
        self._active_launch_count = 0
        self._launch_stats_recorded = False
        self._playtime_timer = QTimer(self)
        self._playtime_timer.setInterval(1000)
        self._playtime_timer.timeout.connect(self._on_playtime_tick)

        # Smart Auto-Translate State
        self.auto_trans_detector = AutoTranslateDetector()
        self._auto_cache_store = None
        self._auto_cache_key = None
        self._last_trans_was_auto = False
        self._last_trans_frame = None
        self.auto_trans_timer = QTimer(self)
        self.auto_trans_timer.setInterval(150)
        self.auto_trans_timer.timeout.connect(self._poll_auto_translate)

        # Fullscreen restoration cache
        self._pre_fs_geometry = None
        self._pre_fs_splitter = None
        self._pre_fs_hud_visible = None
        self._pre_fs_trans_visible = None

        slot_tag = f" [Peer {self.player_slot}]" if self.player_slot > 1 else ""
        self.setWindowTitle(f"mGBA (PyQt6 Replica){slot_tag}")
        self.setAcceptDrops(True)

        script_dir = str(PROJECT_ROOT)
        self.core_path = os.path.join(script_dir, self.DEFAULT_CORE)

        # Initialize RetroArch Shader Engine & Netplay/Link Engine
        self.shader_manager = RetroArchShaderManager(self.config)
        self.netplay_engine = NetplayEngine(self)
        self.netplay_engine.state_sync_received.connect(self._on_netplay_state_received)
        self.netplay_engine.status_changed.connect(
            lambda code, msg: self.video_widget.show_osd(f"🌐 {code}: {msg}", 2500)
        )
        self.netplay_engine.error_occurred.connect(
            lambda err: self.video_widget.show_osd(f"🌐 Netplay Error: {err}", 3500)
        )

        self.controller_manager = ControllerManager(self.config)
        self.gamepad_timer = QTimer(self)
        self.gamepad_timer.timeout.connect(self.poll_gamepad)
        self.gamepad_timer.start(8)

        # Vertical Splitter: OpenGL Video -> HUD Panel -> Subtitle Panel
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setHandleWidth(3)
        self.splitter.setStyleSheet("QSplitter::handle { background-color: #282828; }")
        self.splitter.splitterMoved.connect(self.on_splitter_moved)

        # 1. Hardware-Accelerated OpenGL Video Surface
        self.video_widget = VideoGLWidget(self.shader_manager)
        self.splitter.addWidget(self.video_widget)

        # Initialize Clip Recorder (GIF / MP4)
        self.clip_recorder = ClipRecorder(self)
        self.clip_recorder.status_changed.connect(self.video_widget.set_clip_status)
        self.clip_recorder.export_finished.connect(self.on_clip_export_finished)
        self.clip_recorder.export_error.connect(self.on_clip_export_error)
        self.clip_recorder.recording_stopped.connect(self._sync_clip_menu_idle)

        # 2. Separated Sensor & Rumble HUD Panel
        self.sensor_hud = SensorHudWidget()
        self.sensor_hud.setAcceptDrops(False)
        show_hud_pref = bool(self.config.get("sensors", "show_hud", True))
        self.sensor_hud.setVisible(show_hud_pref)
        self.splitter.addWidget(self.sensor_hud)

        # 3. Translation Subtitle Panel
        self.translation_panel = QTextEdit()
        self.translation_panel.setReadOnly(True)
        self.translation_panel.setAcceptDrops(False)
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
        self.translation_panel.setPlaceholderText("Press [T] to translate screen with AI... (or drag & drop ROM / Patch / State / Shader / Replay here)")
        self.splitter.addWidget(self.translation_panel)

        self.splitter.setStretchFactor(0, 8)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 2)

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

    # =========================================================================
    # PER-GAME PLAYTIME TRACKING
    # =========================================================================
    def _on_playtime_tick(self):
        if (
            not self._active_stats_pid
            or not self.worker
            or not getattr(self, "is_running", False)
            or self.worker.is_paused
            or self.worker.is_rewinding
        ):
            return

        self._session_playtime_sec += 1.0
        self._unsaved_playtime_sec += 1.0
        total_now = self._base_playtime_sec + self._session_playtime_sec
        self.sensor_hud.on_playtime_updated(total_now, self._active_launch_count)

        if self._unsaved_playtime_sec >= 15.0:
            self._flush_playtime()

    def _flush_playtime(self):
        if not self._active_stats_pid or self._unsaved_playtime_sec <= 0.0:
            return
        stats = self.config.add_game_playtime(
            self._active_stats_pid,
            self._unsaved_playtime_sec,
            title=self._active_stats_title,
            session_total=self._session_playtime_sec,
        )
        self._base_playtime_sec = max(
            0.0,
            float(stats.get("playtime_seconds", 0.0) or 0.0) - self._session_playtime_sec,
        )
        self._unsaved_playtime_sec = 0.0

    # =========================================================================
    # DRAG-AND-DROP SUPPORT
    # =========================================================================
    def _classify_dropped_file(self, path: str) -> str | None:
        if not path or not os.path.isfile(path):
            return None
        lower = path.lower()
        ext = os.path.splitext(lower)[1]

        if ext in ROM_DROP_EXTS:
            return "rom"
        if ext in PATCH_DROP_EXTS:
            return "patch"
        if ext == ".state" or (ext.startswith(".ss") and ext[3:].isdigit()):
            return "state"
        if ext == ".glslp":
            return "shader_preset"
        if ext == ".glsl":
            return "shader_pass"
        if lower.endswith(".replay.json") or ext == ".json":
            return "replay"
        if ext == ".cht":
            return "cheat"
        return None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and self._classify_dropped_file(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            kind = self._classify_dropped_file(path)
            if not kind:
                continue

            event.acceptProposedAction()
            self.activateWindow()
            self.video_widget.setFocus()

            if kind == "rom":
                self.load_rom_file(path)
            elif kind == "patch":
                self.apply_soft_patch(path)
            elif kind == "state":
                if self.worker:
                    ok = self.worker.load_state(path)
                    self.video_widget.show_osd(
                        f"Loaded State: {os.path.basename(path)}" if ok else "Failed to load state!"
                    )
                else:
                    QMessageBox.information(self, "Notice", "Load a ROM first before dropping a save state.")
            elif kind == "shader_preset":
                if self.shader_manager.load_preset_file(path):
                    self.shader_manager.enabled = True
                    self.act_shader_toggle.setChecked(True)
                    self.config.set("shaders", "enabled", True)
                    self.config.save()
                    self.video_widget.update()
                    self.video_widget.show_osd(f"Shader Preset: {os.path.basename(path)}")
                else:
                    self.video_widget.show_osd("Failed to load shader preset")
            elif kind == "shader_pass":
                if self.shader_manager.load_glsl_file(path, prepend=False):
                    self.shader_manager.enabled = True
                    self.act_shader_toggle.setChecked(True)
                    self.config.set("shaders", "enabled", True)
                    self.config.save()
                    self.video_widget.update()
                    self.video_widget.show_osd(f"Appended Shader: {os.path.basename(path)}")
                else:
                    self.video_widget.show_osd("Failed to compile shader")
            elif kind == "replay":
                if self.worker:
                    if self.worker.start_replay_session(path):
                        self.video_widget.show_osd(f"▶ REPLAYING: {os.path.basename(path)}", 3000)
                    else:
                        QMessageBox.warning(
                            self, "Replay Error",
                            "Failed to load replay. Ensure the corresponding .state file exists."
                        )
                else:
                    QMessageBox.information(self, "Notice", "Load a ROM first before dropping a replay file.")
            elif kind == "cheat":
                if self.worker:
                    if self.worker.cheat_engine.load_cht_file(path):
                        self.video_widget.show_osd(f"Loaded Cheats: {os.path.basename(path)}")
                    else:
                        self.video_widget.show_osd("Failed to load cheat file")
                else:
                    QMessageBox.information(self, "Notice", "Load a ROM first before dropping a cheat file.")
            return

        event.ignore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.isMaximized() and not self.isMinimized() and not self.isFullScreen():
            self.config.set("video", "window_width", self.width())
            self.config.set("video", "window_height", self.height())
            if hasattr(self, "splitter"):
                self.config.set("video", "splitter_sizes", self.splitter.sizes())

    def on_splitter_moved(self, pos, index):
        if not self.isMaximized() and not self.isMinimized() and not self.isFullScreen():
            self.config.set("video", "splitter_sizes", self.splitter.sizes())

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            pause_enabled = bool(self.config.get("emulation", "pause_on_unfocus", True))
            if getattr(self, "netplay_engine", None) and self.netplay_engine.is_connected:
                pause_enabled = False
            if not self.isActiveWindow():
                if self.worker and getattr(self.worker, "is_rewinding", False):
                    self.worker.set_rewinding(False)
                if pause_enabled and self.worker and hasattr(self, "is_running") and self.is_running and not self.worker.is_paused:
                    self._auto_paused = True
                    self.worker.is_paused = True
                    self.pause_action.setChecked(True)
                    self.video_widget.show_osd("⏸ Auto-Paused (Window Inactive)", 1500)
            else:
                if getattr(self, "_auto_paused", False):
                    self._auto_paused = False
                    if self.worker and hasattr(self, "is_running") and self.is_running:
                        self.worker.is_paused = False
                        self.pause_action.setChecked(False)
                        self.video_widget.show_osd("▶ Resumed", 1000)
        super().changeEvent(event)

    def on_manual_rumble_test(self):
        self.sensor_hud.on_rumble_triggered(1.0, 1.0, 300)

    def on_game_identified(self, title: str, code: str, hw_type: str):
        if self.worker and not self._launch_stats_recorded:
            self._launch_stats_recorded = True
            pid = self.worker.get_profile_id()
            self._active_stats_pid = pid
            self._active_stats_title = title or pid
            stats = self.config.record_game_launch(pid, title=self._active_stats_title)
            self._base_playtime_sec = float(stats.get("playtime_seconds", 0.0) or 0.0)
            self._session_playtime_sec = 0.0
            self._unsaved_playtime_sec = 0.0
            self._active_launch_count = int(stats.get("launch_count", 1) or 1)
            self.sensor_hud.on_playtime_updated(self._base_playtime_sec, self._active_launch_count)
            if not self._playtime_timer.isActive():
                self._playtime_timer.start()

        display_title = f"mGBA - {title} [{code}]"
        if self.player_slot > 1:
            display_title += f" [Peer {self.player_slot}]"
        if hw_type != "None":
            display_title += f" ({hw_type.replace('_', ' ').title()})"
        if self.active_patch:
            display_title += f" [Patched: {os.path.basename(self.active_patch)}]"
        if getattr(self.config, "active_profile_id", None):
            display_title += " [Profile]"
        self.setWindowTitle(display_title)
        if not self.active_patch and not getattr(self.config, "active_profile_id", None):
            self.video_widget.show_osd(f"Loaded: {title} [{code}]", 2500)

    def keyPressEvent(self, event):
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and (event.modifiers() & Qt.KeyboardModifier.AltModifier)
            and not event.isAutoRepeat()
        ) or (event.key() == Qt.Key.Key_F11 and not event.isAutoRepeat()):
            self.toggle_fullscreen()
            return

        if event.key() == Qt.Key.Key_Escape and self.isFullScreen() and not event.isAutoRepeat():
            self.toggle_fullscreen()
            return

        if self.isFullScreen() and not event.isAutoRepeat():
            cs_mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
            if event.modifiers() == cs_mods:
                if event.key() == Qt.Key.Key_G:
                    self.toggle_clip_gif()
                    return
                if event.key() == Qt.Key.Key_M:
                    self.toggle_clip_mp4()
                    return
                if event.key() == Qt.Key.Key_I:
                    self.input_overlay_action.trigger()
                    return
                if event.key() == Qt.Key.Key_O:
                    self.act_trans_overlay.trigger()
                    return

        if not self.worker:
            return

        if (
            event.key() in (Qt.Key.Key_Space, Qt.Key.Key_QuoteLeft)
            and event.key() not in self.keymap
            and event.key() not in self.autofire_keymap
            and not event.isAutoRepeat()
        ):
            self.worker.set_rewinding(True)
            if bool(self.config.get("emulation", "rewind_enabled", True)):
                self.video_widget.show_osd("⏪ Rewinding...", 1500)
            return

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

        if event.key() == Qt.Key.Key_BracketRight:
            self.adjust_solar_sensor(1)
            return
        if event.key() == Qt.Key.Key_BracketLeft:
            self.adjust_solar_sensor(-1)
            return

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

        if event.key() == Qt.Key.Key_T and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.trigger_ai_translation(False)
            return

        if (
            event.key() == Qt.Key.Key_D
            and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        ):
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

        if (
            event.key() in (Qt.Key.Key_Space, Qt.Key.Key_QuoteLeft)
            and event.key() not in self.keymap
            and event.key() not in self.autofire_keymap
            and not event.isAutoRepeat()
        ):
            if self.worker.is_rewinding:
                self.worker.set_rewinding(False)
                self.video_widget.show_osd("⏸ Paused" if self.worker.is_paused else "▶ Resumed", 1000)
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
        if hasattr(self, "_playtime_timer"):
            self._playtime_timer.stop()
        self._flush_playtime()

        if hasattr(self, "gamepad_timer"):
            self.gamepad_timer.stop()

        if hasattr(self, "auto_trans_timer"):
            self.auto_trans_timer.stop()

        if hasattr(self, "netplay_engine") and self.netplay_engine:
            self.netplay_engine.stop()

        if hasattr(self, "clip_recorder"):
            if self.clip_recorder.is_recording:
                self.clip_recorder.stop(wait=True)
            for th in list(self.clip_recorder._export_threads):
                th.wait()

        if self.worker:
            self.worker.set_rewinding(False)
            self.worker.stop()
            self.worker.wait()

        if not self.isMaximized() and not self.isMinimized() and not self.isFullScreen():
            self.config.set("video", "window_width", self.width())
            self.config.set("video", "window_height", self.height())
            if hasattr(self, "splitter"):
                self.config.set("video", "splitter_sizes", self.splitter.sizes())
            self.config.set("sensors", "show_hud", self.sensor_hud.isVisible())
        elif self._pre_fs_hud_visible is not None:
            self.config.set("sensors", "show_hud", self._pre_fs_hud_visible)

        self.config.set("video", "scale", self.scale_factor)
        self.config.set("shaders", "enabled", self.shader_manager.enabled)
        self.shader_manager.save_chain_to_config()
        self.config.save()
        event.accept()

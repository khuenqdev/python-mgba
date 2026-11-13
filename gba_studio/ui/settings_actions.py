# settings_actions.py
from PyQt6.QtWidgets import QMessageBox

from .controller import ControllerConfigDialog
from ..tools.dev_tools import MemoryViewerDialog


class SettingsActionsMixin:
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
        self.rewind_enabled_action.setChecked(bool(self.config.get("emulation", "rewind_enabled", True)))
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.integer_scale_action.setChecked(bool(self.config.get("video", "integer_scaling", False)))
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))
        self.act_shader_toggle.setChecked(bool(self.config.get("shaders", "enabled", False)))

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
        self.video_widget.show_osd("Audio Muted" if self.mute_action.isChecked() else "Audio Unmuted")

    def reset_settings_to_default(self):
        confirm = QMessageBox.question(
            self, "Reset Settings",
            "Are you sure you want to reset all settings, controls, sensors, shaders, and audio options back to default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.config.reset_to_defaults()
            self.shader_manager.enabled = False
            self.shader_manager.load_preset_by_name("gba_color", compile_now=True, save_to_config=False)
            self._sync_ui_with_config()
            self.update_recent_menu()
            self.video_widget.update()
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
        from ..tools.graphics.editor import GraphicEditorDialog
        self.active_tool_window = GraphicEditorDialog(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_translation_tools(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to inspect memory.")
            return
        try:
            from ..tools.translation_dialog import TranslationToolsDialog
            self.active_tool_window = TranslationToolsDialog(self.worker, self.config, self)
            self.active_tool_window.show()
        except ImportError as e:
            QMessageBox.critical(self, "Error", f"Failed to load translation tools: {e}")

    def open_font_tools(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to scan fonts.")
            return
        try:
            from ..tools.font_tools import FontToolsDialog
            self.active_tool_window = FontToolsDialog(self.worker, self.config, self)
            self.active_tool_window.show()
        except ImportError as e:
            QMessageBox.critical(self, "Error", f"Failed to load font tools: {e}")

    def open_cheats_manager(self):
        if not self.worker:
            QMessageBox.warning(self, "Error", "Emulation must be running to manage cheats.")
            return
        from ..core.cheats import CheatManagerWindow
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
# settings_actions.py
from PyQt6.QtWidgets import QMessageBox

from .controller import ControllerConfigDialog
from ..tools.dev_tools import MemoryViewerDialog


class SettingsActionsMixin:
    def _sync_ui_with_config(self):
        self.mute_action.setChecked(bool(self.config.get("audio", "mute")))
        if hasattr(self, "ff_pitch_action"):
            self.ff_pitch_action.setChecked(
                bool(self.config.get("audio", "fast_forward_pitch_corrected", True))
            )

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
        self.auto_patch_action.setChecked(bool(self.config.get("emulation", "auto_soft_patch", True)))
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.integer_scale_action.setChecked(bool(self.config.get("video", "integer_scaling", False)))
        self.input_overlay_action.setChecked(bool(self.config.get("video", "show_input_overlay", False)))
        self.act_trans_overlay.setChecked(bool(self.config.get("ai_translation", "overlay_enabled", True)))
        if hasattr(self, "act_auto_translate"):
            self.act_auto_translate.setChecked(bool(self.config.get("ai_translation", "auto_translate", False)))
        if hasattr(self, "_sync_auto_translate_timer"):
            self._sync_auto_translate_timer()
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))
        self.act_shader_toggle.setChecked(bool(self.config.get("shaders", "enabled", False)))

        clip_scale = int(self.config.get("video", "clip_scale", 2) or 2)
        if clip_scale in self.clip_scale_actions:
            self.clip_scale_actions[clip_scale].setChecked(True)

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

        if hasattr(self, "use_profile_action"):
            has_rom = bool(getattr(self, "worker", None) and getattr(self, "current_rom", None))
            active_pid = getattr(self.config, "active_profile_id", None)
            self.use_profile_action.blockSignals(True)
            self.use_profile_action.setEnabled(has_rom)
            self.use_profile_action.setChecked(bool(has_rom and active_pid))
            if has_rom:
                pid = self.worker.get_profile_id()
                self.use_profile_action.setText(f"Use Per-Game Profile [{pid}]")
                self.seed_profile_action.setEnabled(True)
                self.delete_profile_action.setEnabled(self.config.has_profile(pid))
            else:
                self.use_profile_action.setText("Use Per-Game Profile (No ROM)")
                self.seed_profile_action.setEnabled(False)
                self.delete_profile_action.setEnabled(False)
            self.use_profile_action.blockSignals(False)

        self._build_keymap()

    def toggle_ff_pitch_corrected(self):
        val = self.ff_pitch_action.isChecked()
        self.config.set("audio", "fast_forward_pitch_corrected", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Fast-Forward Pitch Correction: {'ON (WSOLA)' if val else 'OFF'}")

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
            "Are you sure you want to reset all global settings, controls, sensors, shaders, and audio options back to default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.config.reset_to_defaults()
            self._apply_effective_config_to_runtime()
            self.update_recent_menu()
            if self.worker:
                self.on_game_identified(self.worker.game_title, self.worker.game_code, self.worker.rumble_type)
            self.video_widget.show_osd("Settings Reset to Default")
            QMessageBox.information(self, "Success", "All settings have been restored to factory defaults.")

    def _open_dev_tool(self, dialog_cls):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to inspect game state.")
            return
        self.active_tool_window = dialog_cls(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_tas_editor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to use the Piano-Roll TAS Editor.")
            return
        from ..tools.tas_editor import TASTimelineDialog
        self.active_tool_window = TASTimelineDialog(self, self)
        self.active_tool_window.show()

    def open_sappy_extractor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Load a ROM first to scan for Sappy / M4A music.")
            return
        from ..tools.sappy_extractor import SappyExtractorDialog
        self.active_tool_window = SappyExtractorDialog(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_netplay_dialog(self):
        from .netplay_dialog import NetplayLinkDialog
        self.active_tool_window = NetplayLinkDialog(self, self)
        self.active_tool_window.show()

    def open_game_stats_dialog(self):
        from .stats_dialog import GameStatsDialog
        self.active_tool_window = GameStatsDialog(self, self)
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

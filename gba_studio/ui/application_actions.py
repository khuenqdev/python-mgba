# application_actions.py
import os
import html

from PyQt6.QtWidgets import QFileDialog, QMessageBox
from PyQt6.QtGui import QAction

from ..tools.ai_translator import TranslationWorker
from .shader_dialog import ShaderConfigDialog
from .state_browser import SaveStateBrowserDialog


class ApplicationActionsMixin:
    def toggle_input_overlay(self):
        val = self.input_overlay_action.isChecked()
        self.config.set("video", "show_input_overlay", val)
        self.video_widget.update()
        self.video_widget.show_osd(f"Input & TAS Frame HUD: {'Always ON' if val else 'Rec/Replay Only'}")

    def toggle_clip_gif(self):
        if self.clip_recorder.is_recording:
            self.stop_clip_recording()
        else:
            self._start_clip_recording("gif")

    def toggle_clip_mp4(self):
        if self.clip_recorder.is_recording:
            self.stop_clip_recording()
        else:
            self._start_clip_recording("mp4")

    def _start_clip_recording(self, fmt: str):
        if not self.worker or not self.current_rom:
            self.act_record_gif.setChecked(False)
            self.act_record_mp4.setChecked(False)
            QMessageBox.information(self, "Notice", "Load and run a ROM before recording a clip.")
            return

        scale = int(self.config.get("video", "clip_scale", 2) or 2)
        records_dir = self.config.get("paths", "records") or "records"
        rom_name = os.path.splitext(os.path.basename(self.current_rom))[0]

        ok, msg = self.clip_recorder.start(fmt, scale, records_dir, rom_name)
        if not ok:
            self.act_record_gif.setChecked(False)
            self.act_record_mp4.setChecked(False)
            QMessageBox.warning(self, "Clip Recorder", msg)
            return

        self.act_record_gif.setChecked(fmt == "gif")
        self.act_record_mp4.setChecked(fmt == "mp4")
        self.act_record_gif.setEnabled(fmt == "gif")
        self.act_record_mp4.setEnabled(fmt == "mp4")
        self.act_stop_clip.setEnabled(True)
        self.video_widget.show_osd(f"🔴 Recording {fmt.upper()} Clip ({scale}x)...", 2500)

    def stop_clip_recording(self):
        if self.clip_recorder.is_recording:
            self.clip_recorder.stop()
            self.video_widget.show_osd("💾 Saving Clip...", 2500)
        self._sync_clip_menu_idle()

    def _sync_clip_menu_idle(self):
        if hasattr(self, "act_record_gif"):
            self.act_record_gif.blockSignals(True)
            self.act_record_gif.setChecked(False)
            self.act_record_gif.setEnabled(True)
            self.act_record_gif.blockSignals(False)
        if hasattr(self, "act_record_mp4"):
            self.act_record_mp4.blockSignals(True)
            self.act_record_mp4.setChecked(False)
            self.act_record_mp4.setEnabled(True)
            self.act_record_mp4.blockSignals(False)
        if hasattr(self, "act_stop_clip"):
            self.act_stop_clip.setEnabled(False)

    def on_clip_export_finished(self, filepath: str):
        self._sync_clip_menu_idle()
        self.video_widget.show_osd(f"✅ Saved Clip: {os.path.basename(filepath)}", 4000)

    def on_clip_export_error(self, err_msg: str):
        self._sync_clip_menu_idle()
        QMessageBox.warning(self, "Clip Recorder Error", err_msg)

    def set_clip_scale(self, scale: int):
        self.config.set("video", "clip_scale", scale)
        if hasattr(self, "clip_scale_actions") and scale in self.clip_scale_actions:
            self.clip_scale_actions[scale].setChecked(True)
        self.video_widget.show_osd(f"Clip Export Scale: {scale}x")

    def open_state_browser(self):
        if not self.current_rom:
            QMessageBox.information(self, "Notice", "Please load a ROM first to view its save state slots.")
            return
        dlg = SaveStateBrowserDialog(self, self)
        dlg.exec()

    def toggle_auto_soft_patch(self):
        val = self.auto_patch_action.isChecked()
        self.config.set("emulation", "auto_soft_patch", val)
        self.video_widget.show_osd(f"Auto Soft-Patch: {'ON' if val else 'OFF'} (Applies on next load)")

    def open_shader_dialog(self):
        dlg = ShaderConfigDialog(self.shader_manager, self.video_widget, self.config, self)
        dlg.exec()
        self.act_shader_toggle.setChecked(self.shader_manager.enabled)

    def toggle_shaders(self):
        val = self.act_shader_toggle.isChecked()
        self.shader_manager.enabled = val
        self.config.set("shaders", "enabled", val)
        self.config.save()
        self.video_widget.update()
        self.video_widget.show_osd(f"Shaders: {'ON' if val else 'OFF'}")

    def apply_quick_shader_preset(self, preset_id: str):
        self.shader_manager.load_preset_by_name(preset_id)
        self.shader_manager.enabled = True
        self.act_shader_toggle.setChecked(True)
        self.config.set("shaders", "enabled", True)
        self.config.set("shaders", "preset", preset_id)
        self.config.save()
        self.video_widget.update()
        self.video_widget.show_osd(f"Preset Applied: {preset_id.replace('_', ' ').title()}")

    def toggle_fullscreen(self):
        if not self.isFullScreen():
            self._pre_fs_geometry = self.saveGeometry()
            self._pre_fs_splitter = self.splitter.sizes()
            self._pre_fs_hud_visible = self.sensor_hud.isVisible()
            self._pre_fs_trans_visible = self.translation_panel.isVisible()

            self.menuBar().hide()
            self.sensor_hud.hide()
            self.translation_panel.hide()
            self.showFullScreen()
            self.fullscreen_action.setChecked(True)
            self.video_widget.setFocus()
            self.video_widget.show_osd("Fullscreen (Alt+Enter / F11 / Esc to exit)", 2500)
        else:
            self.showNormal()
            self.menuBar().show()
            self.sensor_hud.setVisible(bool(getattr(self, "_pre_fs_hud_visible", True)))
            self.translation_panel.setVisible(bool(getattr(self, "_pre_fs_trans_visible", True)))

            if getattr(self, "_pre_fs_splitter", None):
                self.splitter.setSizes(self._pre_fs_splitter)
            if getattr(self, "_pre_fs_geometry", None):
                self.restoreGeometry(self._pre_fs_geometry)

            self.fullscreen_action.setChecked(False)
            self.video_widget.setFocus()
            self.video_widget.show_osd("Windowed Mode", 1500)

    def toggle_integer_scaling(self):
        val = self.integer_scale_action.isChecked()
        self.config.set("video", "integer_scaling", val)
        self.video_widget.update()
        self.video_widget.show_osd(f"Integer Scaling: {'ON' if val else 'OFF'}")

    def toggle_rewind_enabled(self):
        val = self.rewind_enabled_action.isChecked()
        self.config.set("emulation", "rewind_enabled", val)
        if not val and self.worker:
            self.worker.set_rewinding(False)
            self.worker.rewind_buffer.clear()
        self.video_widget.show_osd(f"Rewind Buffer: {'ON' if val else 'OFF'}")

    def set_scale(self, scale: int):
        """Applies exact GBA 3:2 geometric scaling with zero pillarboxing and full persistence."""
        if self.isFullScreen():
            self.toggle_fullscreen()

        self.scale_factor = scale
        self.config.set("video", "scale", scale)
        if scale in self.scale_actions:
            self.scale_actions[scale].setChecked(True)

        hud_h = 30 if self.sensor_hud.isVisible() else 0
        panel_h = 75 if scale <= 2 else 115
        video_h = 160 * scale
        video_w = 240 * scale
        total_h = video_h + hud_h + panel_h

        self.splitter.setSizes([video_h, hud_h, panel_h])
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

        if self.isFullScreen():
            self._pre_fs_hud_visible = val
            return

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
            from ..tools.scene_engine import SceneDumpEngine
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
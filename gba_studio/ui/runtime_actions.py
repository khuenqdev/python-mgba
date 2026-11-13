# runtime_actions.py
import os
import shutil
import hashlib
import zipfile
import tempfile
import datetime

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from ..core.core_worker import EmulatorWorker
from ..core.rom_patcher import PatchError, find_matching_patch, prepare_patched_rom
from .controller import LIBRETRO_BTNS, QT_KEY_MAP

SUPPORTED_ROM_EXTS = (".gba", ".gb", ".gbc", ".sgb")


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
        if self.worker and hasattr(self, "is_running") and self.is_running:
            self.controller_manager.poll_gamepad_events(self.worker)

    def _extract_zip_rom(self, zip_path: str) -> str:
        """Extracts the first supported ROM inside a .zip archive into a temp cache."""
        with zipfile.ZipFile(zip_path, "r") as zf:
            candidates = [
                zi for zi in zf.infolist()
                if not zi.is_dir()
                and not zi.filename.startswith("__MACOSX/")
                and zi.filename.lower().endswith(SUPPORTED_ROM_EXTS)
            ]
            if not candidates:
                raise ValueError("No supported ROM (.gba, .gb, .gbc, .sgb) found inside the ZIP archive.")

            candidates.sort(key=lambda zi: (0 if zi.filename.lower().endswith(".gba") else 1, zi.filename.lower()))
            target = candidates[0]

            zip_hash = hashlib.md5(os.path.abspath(zip_path).encode("utf-8")).hexdigest()[:12]
            cache_dir = os.path.join(tempfile.gettempdir(), "gba_studio_rom_cache", zip_hash)
            os.makedirs(cache_dir, exist_ok=True)

            safe_name = os.path.basename(target.filename)
            extracted_path = os.path.join(cache_dir, safe_name)

            if not os.path.exists(extracted_path) or os.path.getsize(extracted_path) != target.file_size:
                with zf.open(target, "r") as src, open(extracted_path, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        dst.write(chunk)

            return extracted_path

    def _extract_7z_rom(self, archive_path: str) -> str:
        """Extracts the first supported ROM inside a .7z archive into a temp cache."""
        try:
            import py7zr
        except ImportError as e:
            raise RuntimeError("Missing dependency 'py7zr'. Install it with: pip install py7zr") from e

        with py7zr.SevenZipFile(archive_path, mode="r") as sz:
            candidates = [
                fi for fi in sz.list()
                if not fi.is_directory
                and not fi.filename.startswith("__MACOSX/")
                and fi.filename.lower().endswith(SUPPORTED_ROM_EXTS)
            ]
            if not candidates:
                raise ValueError("No supported ROM (.gba, .gb, .gbc, .sgb) found inside the 7z archive.")

            candidates.sort(key=lambda fi: (0 if fi.filename.lower().endswith(".gba") else 1, fi.filename.lower()))
            target = candidates[0]

            arch_stat = os.stat(archive_path)
            key = f"{os.path.abspath(archive_path)}|{arch_stat.st_mtime_ns}|{arch_stat.st_size}"
            arch_hash = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
            cache_dir = os.path.join(tempfile.gettempdir(), "gba_studio_rom_cache", f"7z_{arch_hash}")
            os.makedirs(cache_dir, exist_ok=True)

            safe_name = os.path.basename(target.filename)
            extracted_path = os.path.join(cache_dir, safe_name)

            if not os.path.exists(extracted_path) or os.path.getsize(extracted_path) != target.uncompressed:
                stage_dir = os.path.join(cache_dir, "_stage")
                shutil.rmtree(stage_dir, ignore_errors=True)
                os.makedirs(stage_dir, exist_ok=True)
                try:
                    sz.extract(path=stage_dir, targets=[target.filename])
                    staged_file = os.path.join(stage_dir, target.filename)
                    if not os.path.exists(staged_file):
                        # Fallback search inside stage_dir
                        for root_dir, _, files in os.walk(stage_dir):
                            if safe_name in files:
                                staged_file = os.path.join(root_dir, safe_name)
                                break
                    if not os.path.exists(staged_file):
                        raise FileNotFoundError("Failed to locate extracted ROM inside 7z staging folder.")
                    shutil.move(staged_file, extracted_path)
                finally:
                    shutil.rmtree(stage_dir, ignore_errors=True)

            return extracted_path

    def load_rom(self):
        filters = (
            "Supported ROMs (*.gba *.gb *.gbc *.sgb *.zip *.7z);;"
            "GBA ROMs (*.gba);;GB/GBC ROMs (*.gb *.gbc);;"
            "Archives (*.zip *.7z);;All Files (*.*)"
        )
        filepath, _ = QFileDialog.getOpenFileName(self, "Load Game", "", filters)
        if filepath:
            self.load_rom_file(filepath)

    def load_rom_file(self, filepath: str):
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "File Error", f"ROM not found:\n{filepath}")
            return

        clean_path = filepath
        lower_path = filepath.lower()
        if lower_path.endswith(".zip"):
            try:
                clean_path = self._extract_zip_rom(filepath)
            except (zipfile.BadZipFile, ValueError, OSError) as e:
                QMessageBox.warning(self, "ZIP Error", f"Failed to load ZIP ROM:\n{e}")
                return
        elif lower_path.endswith(".7z"):
            try:
                clean_path = self._extract_7z_rom(filepath)
            except Exception as e:
                QMessageBox.warning(self, "7z Error", f"Failed to load 7z ROM:\n{e}")
                return

        self.source_rom = filepath
        self.clean_rom = clean_path
        self.active_patch = None
        boot_path = clean_path

        if bool(self.config.get("emulation", "auto_soft_patch", True)):
            patch_path = find_matching_patch(clean_path, source_path=filepath)
            if patch_path:
                try:
                    boot_path = prepare_patched_rom(clean_path, patch_path)
                    self.active_patch = patch_path
                except (PatchError, OSError) as e:
                    QMessageBox.warning(
                        self, "Soft-Patch Warning",
                        f"Found matching patch '{os.path.basename(patch_path)}' but failed to apply:\n{e}\n\nBooting unpatched ROM."
                    )

        self.current_rom = boot_path
        self.config.add_recent_file(filepath)
        self.update_recent_menu()
        self.start_emulation()

    def apply_patch_dialog(self):
        if not self.clean_rom or not os.path.exists(self.clean_rom):
            QMessageBox.information(self, "Notice", "Please load a ROM first before applying a soft-patch.")
            return

        start_dir = os.path.dirname(self.source_rom or self.clean_rom)
        patch_path, _ = QFileDialog.getOpenFileName(
            self, "Apply Soft-Patch", start_dir,
            "ROM Patches (*.ips *.bps *.ups);;IPS Patches (*.ips);;BPS Patches (*.bps);;UPS Patches (*.ups)"
        )
        if patch_path:
            self.apply_soft_patch(patch_path)

    def apply_soft_patch(self, patch_path: str):
        if not self.clean_rom or not os.path.exists(self.clean_rom):
            QMessageBox.information(self, "Notice", "Load a ROM first before applying a patch.")
            return

        try:
            patched_path = prepare_patched_rom(self.clean_rom, patch_path)
        except (PatchError, OSError) as e:
            QMessageBox.critical(self, "Patch Error", f"Failed to apply patch:\n{e}")
            return

        self.active_patch = patch_path
        self.current_rom = patched_path
        self.start_emulation()

    def start_emulation(self):
        if not os.path.exists(self.core_path):
            QMessageBox.critical(self, "Core Error", f"Core not found:\n{self.core_path}")
            return

        if hasattr(self, "clip_recorder") and self.clip_recorder.is_recording:
            self.clip_recorder.stop()

        if self.worker:
            self.worker.stop()
            self.worker.wait()

        # Reset back to Global settings before probing the new ROM header
        self.config.deactivate_profile()
        self.video_widget.set_input_hud("IDLE", 0, 0, 0)

        save_base = os.path.splitext(os.path.basename(self.current_rom))[0]
        self.worker = EmulatorWorker(
            self.core_path,
            self.current_rom,
            self.config,
            save_base_name=save_base,
        )

        # Load Per-Game Profile if one exists for this ROM's game_code / ID
        profile_id = self.worker.get_profile_id()
        profile_loaded = self.config.activate_profile_if_exists(profile_id, title=self.worker.game_title)
        self._apply_effective_config_to_runtime()

        self.worker.frame_ready.connect(self.video_widget.set_frame)
        if hasattr(self, "clip_recorder"):
            self.worker.frame_ready.connect(self.clip_recorder.push_frame)
        self.worker.input_hud_updated.connect(self.video_widget.set_input_hud)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.replay_finished.connect(self.on_replay_finished)
        self.worker.rewind_status.connect(self.video_widget.show_osd)

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

        osd_parts = []
        if self.active_patch:
            osd_parts.append(f"🩹 {os.path.basename(self.active_patch)}")
        if profile_loaded:
            osd_parts.append(f"🎮 Profile: {profile_id}")
        if osd_parts:
            self.video_widget.show_osd(" | ".join(osd_parts), 3500)

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
            state_txt = f"ON ({spd}x)" if self.worker.fast_forward else "OFF"
            self.video_widget.show_osd(f"Fast Forward: {state_txt}")

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
        if hasattr(self, "slot_actions") and slot in self.slot_actions:
            self.slot_actions[slot].setChecked(True)
        self.video_widget.show_osd(f"Selected State Slot {slot}")

    def take_screenshot(self):
        if not self.worker or self.video_widget.current_frame_data is None:
            return
        out_dir = self.config.get("paths", "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        filename = f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        from PIL import Image
        Image.fromarray(self.video_widget.current_frame_data, "RGB").save(os.path.join(out_dir, filename))
        self.video_widget.show_osd("Screenshot Captured!")

    def get_state_path(self, slot: int) -> str | None:
        if not self.current_rom:
            return None
        state_dir = self.config.get("paths", "states") or "states"
        os.makedirs(state_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(self.current_rom))[0]
        return os.path.join(state_dir, f"{base_name}.ss{slot}")

    def quick_save_state(self, slot: int):
        if not self.worker or not self.current_rom:
            return
        state_path = self.get_state_path(slot)
        if state_path and self.worker.save_state(state_path):
            frame = getattr(self.worker, "latest_video_frame", None)
            if frame is None:
                frame = self.video_widget.current_frame_data
            if frame is not None:
                try:
                    from PIL import Image
                    Image.fromarray(frame, "RGB").save(state_path + ".png", "PNG")
                except Exception as e:
                    print(f"[STATE THUMBNAIL WARNING] {e}")
            self.video_widget.show_osd(f"Saved to Slot {slot}")

    def quick_load_state(self, slot: int):
        if not self.worker or not self.current_rom:
            return
        path = self.get_state_path(slot)
        if path and os.path.exists(path):
            self.worker.load_state(path)
            self.video_widget.show_osd(f"Loaded from Slot {slot}")
        else:
            self.video_widget.show_osd(f"Slot {slot} is empty!")

    def save_state_to_file(self):
        if not self.worker or not self.current_rom:
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
        if not self.worker or not self.current_rom:
            return
        state_dir = self.config.get("paths", "states")
        filepath, _ = QFileDialog.getOpenFileName(self, "Load State from File", state_dir, "Save States (*.state *.ss*)")
        if filepath:
            self.worker.load_state(filepath)
            self.video_widget.show_osd(f"Loaded: {os.path.basename(filepath)}")
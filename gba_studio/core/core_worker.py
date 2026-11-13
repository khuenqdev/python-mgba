# core_worker.py
import os
import time
import zlib
import ctypes
from collections import deque
import numpy as np

try:
    from PyQt6.QtCore import QThread, pyqtSignal, QRecursiveMutex as QMutexClass, QMutexLocker
except ImportError:
    from PyQt6.QtCore import QThread, pyqtSignal, QMutex as QMutexClass, QMutexLocker

from pdretro import Emulator
from .cheats import CheatEngine
from .audio_dsp import AudioDSP
from .session_recorder import SessionRecorder, ID_TO_BTN

AF_TO_LIBRETRO = {
    "a": 8,
    "b": 0,
    "l": 10,
    "r": 11
}

HUD_BTN_BITS = [
    ("A", 8),
    ("B", 0),
    ("L", 10),
    ("R", 11),
    ("UP", 4),
    ("DOWN", 5),
    ("LEFT", 6),
    ("RIGHT", 7),
    ("START", 3),
    ("SELECT", 2),
]

RETRO_ENVIRONMENT_SET_PIXEL_FORMAT = 1
RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY = 9
RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS = 11
RETRO_ENVIRONMENT_GET_VARIABLE = 15
RETRO_ENVIRONMENT_SET_VARIABLES = 16
RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE = 17
RETRO_ENVIRONMENT_GET_RUMBLE_INTERFACE = 23
RETRO_ENVIRONMENT_GET_LOG_INTERFACE = 27
RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY = 31

from .libretro_types import (
    ENV_CB_TYPE,
    RUMBLE_CB_TYPE,
    RetroRumbleInterface,
    RetroVariable,
)

from .sensor_controls import SensorControlsMixin
from .state_storage import StateStorageMixin
from .replay_control import ReplayControlMixin


class EmulatorWorker(QThread, SensorControlsMixin, StateStorageMixin, ReplayControlMixin):
    frame_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)
    stopped = pyqtSignal()
    replay_finished = pyqtSignal()
    rewind_status = pyqtSignal(str)
    input_hud_updated = pyqtSignal(str, int, int, int)
    rumble_triggered = pyqtSignal(float, float, int)
    solar_changed = pyqtSignal(int)
    tilt_changed = pyqtSignal(float, float, float)
    game_identified = pyqtSignal(str, str, str)

    def __init__(self, core_path: str, rom_path: str, config, save_base_name: str = None):
        super().__init__()
        self.core_path = os.path.abspath(core_path)
        self.rom_path = os.path.abspath(rom_path) if rom_path else ""
        self.config = config
        self.save_base_name = save_base_name

        self.emu = None
        self._is_running = False
        self.fast_forward = False
        self.is_paused = False
        self.step_requested = False
        self.reset_requested = False

        # Rewind State
        buf_max = max(30, int(self.config.get("emulation", "rewind_buffer_size", 300) or 300))
        self.rewind_buffer = deque(maxlen=buf_max)
        self.is_rewinding = False
        self._notified_rewind_empty = False

        self.mutex = QMutexClass()
        self.cheat_engine = CheatEngine(self)
        self.dsp = AudioDSP(self.config)

        self.recorder = SessionRecorder()
        self.replay_frame_counter = 0

        self.latest_video_frame = None
        self.previous_frame = None
        self._last_gui_render_time = 0.0
        self._last_sram_digest = None
        self._last_hud_mode = None
        self._last_hud_mask = -1

        self.standard_buttons = {}
        self.autofire_buttons = {"a": False, "b": False, "l": False, "r": False}

        # Sensor State
        self.solar_level = int(self.config.get("sensors", "solar_level") if self.config.get("sensors") else 5)
        self.tilt_x = 0.0
        self.tilt_y = 0.0
        self.gyro_angle = 0.0

        # Native Libretro C-Types Handlers
        self._core_dll = None
        self._c_environ_cb = None
        self._c_rumble_cb = None
        self._active_rumble_low = 0.0
        self._active_rumble_high = 0.0

        saves_path = (self.config.get("paths", "saves") or "saves").encode("utf-8", errors="ignore")
        self._c_str_cache = {
            b"mgba_force_gbp": b"ON",
            b"mgba_use_bios": b"OFF",
            b"mgba_skip_bios": b"ON",
            b"mgba_gb_model": b"Autodetect",
            b"mgba_solar_sensor_level": str(self.solar_level).encode("ascii"),
            b"saves_dir": saves_path,
            b"current_dir": b"."
        }

        # Read Cartridge Header (0xAC: Game Code, 0xA0: Title)
        self.game_code = "UNKNOWN"
        self.game_title = "Unknown"
        self.rumble_type = "None"
        self._parse_rom_header()

    def get_profile_id(self) -> str:
        """Returns a stable identifier for per-game config profiles."""
        if self.game_code and self.game_code not in ("UNKNOWN", ""):
            return self.game_code
        if self.save_base_name:
            return self.save_base_name
        if self.rom_path:
            return os.path.splitext(os.path.basename(self.rom_path))[0]
        return "DEFAULT"

    def _parse_rom_header(self):
        try:
            if self.rom_path and os.path.exists(self.rom_path):
                with open(self.rom_path, "rb") as f:
                    f.seek(0xAC)
                    self.game_code = f.read(4).decode("latin-1", errors="ignore").strip().upper()
                    f.seek(0xA0)
                    self.game_title = f.read(12).decode("latin-1", errors="ignore").strip()

                if self.game_code in ["BPPE", "BPPJ", "BPPP", "A8NE", "A8NJ", "A8NP", "AX4E", "AX4J", "AX4P"]:
                    self.rumble_type = "GBP_RUMBLE"
                elif self.game_code in ["V49E", "V49J", "V49P", "2GBP"]:
                    self.rumble_type = "CARTRIDGE_RUMBLE"
                elif self.game_code in ["RZWE", "RZWJ", "RZWP"]:
                    self.rumble_type = "RUMBLE_AND_GYRO"
                else:
                    self.rumble_type = "None"
        except Exception:
            pass

    def _libretro_environ_cb(self, cmd: int, data: int) -> bool:
        """Acts as the Libretro environment provider, configuring mGBA natively."""
        if cmd == RETRO_ENVIRONMENT_GET_VARIABLE:
            if not data:
                return False
            var = ctypes.cast(data, ctypes.POINTER(RetroVariable))
            key = var.contents.key
            if not key:
                return False

            if key == b"mgba_force_gbp":
                var.contents.value = self._c_str_cache[b"mgba_force_gbp"]
                return True
            elif key == b"mgba_solar_sensor_level":
                self._c_str_cache[b"mgba_solar_sensor_level"] = str(self.solar_level).encode("ascii")
                var.contents.value = self._c_str_cache[b"mgba_solar_sensor_level"]
                return True
            elif key == b"mgba_use_bios":
                var.contents.value = self._c_str_cache[b"mgba_use_bios"]
                return True
            elif key == b"mgba_skip_bios":
                var.contents.value = self._c_str_cache[b"mgba_skip_bios"]
                return True
            elif key == b"mgba_gb_model":
                var.contents.value = self._c_str_cache[b"mgba_gb_model"]
                return True
            return False

        elif cmd == RETRO_ENVIRONMENT_GET_RUMBLE_INTERFACE:
            if not data:
                return False
            iface = ctypes.cast(data, ctypes.POINTER(RetroRumbleInterface))
            iface.contents.set_rumble_state = self._c_rumble_cb
            return True

        elif cmd == RETRO_ENVIRONMENT_SET_PIXEL_FORMAT:
            return True

        elif cmd == RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS:
            return True

        elif cmd == RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY:
            if data:
                ptr = ctypes.cast(data, ctypes.POINTER(ctypes.c_char_p))
                ptr.contents.value = self._c_str_cache[b"current_dir"]
                return True
            return False

        elif cmd == RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY:
            if data:
                ptr = ctypes.cast(data, ctypes.POINTER(ctypes.c_char_p))
                ptr.contents.value = self._c_str_cache[b"saves_dir"]
                return True
            return False

        return False

    def _libretro_rumble_cb(self, port: int, effect: int, strength: int) -> bool:
        """Dispatches genuine in-game vibration events fired directly by mGBA's CPU core."""
        if not self.config.get("sensors", "rumble_enabled"):
            return True

        norm_val = min(1.0, max(0.0, float(strength) / 65535.0))

        if effect == 0:
            self._active_rumble_low = norm_val
        else:
            self._active_rumble_high = norm_val

        if self._active_rumble_low <= 0.0 and self._active_rumble_high <= 0.0:
            self.rumble_triggered.emit(0.0, 0.0, 0)
        else:
            self.rumble_triggered.emit(self._active_rumble_low, self._active_rumble_high, 120)

        return True

    def _install_libretro_hooks(self):
        try:
            self._core_dll = ctypes.CDLL(self.core_path)
            if hasattr(self._core_dll, "retro_set_environment"):
                self._c_environ_cb = ENV_CB_TYPE(self._libretro_environ_cb)
                self._c_rumble_cb = RUMBLE_CB_TYPE(self._libretro_rumble_cb)

                self._core_dll.retro_set_environment.argtypes = [ENV_CB_TYPE]
                self._core_dll.retro_set_environment.restype = None
                self._core_dll.retro_set_environment(self._c_environ_cb)
                print("[CORE HOOK] Successfully attached native Libretro environment & rumble interfaces.")
        except Exception as e:
            print(f"[CORE HOOK WARNING] Failed to attach native environment hooks: {e}")

    def update_audio_settings(self):
        if hasattr(self, "dsp") and self.dsp:
            self.dsp.update_settings()

    def set_rewinding(self, active: bool):
        if active and not bool(self.config.get("emulation", "rewind_enabled", True)):
            self.rewind_status.emit("Rewind is disabled in settings")
            return
        self.is_rewinding = bool(active)
        if active:
            self._notified_rewind_empty = False

    def _push_rewind_snapshot(self, frame_idx: int):
        raw_state = self.serialize_state_bytes()
        if raw_state:
            compressed = zlib.compress(raw_state, level=1)
            self.rewind_buffer.append((frame_idx, compressed))

    def _extract_video_array(self, vid_obj) -> np.ndarray:
        if hasattr(vid_obj, "to_rgb"):
            return vid_obj.to_rgb()
        elif hasattr(vid_obj, "data"):
            return np.array(vid_obj.data, copy=False)
        return np.array(vid_obj, copy=False)

    def run(self):
        # Refresh per-game settings that may have been loaded via profile activation
        self.solar_level = int(self.config.get("sensors", "solar_level", 5) or 5)
        self.dsp.update_settings()

        buf_max = max(30, int(self.config.get("emulation", "rewind_buffer_size", 300) or 300))
        if self.rewind_buffer.maxlen != buf_max:
            self.rewind_buffer = deque(self.rewind_buffer, maxlen=buf_max)

        skip_bios = bool(self.config.get("emulation", "skip_bios"))
        self._c_str_cache[b"mgba_skip_bios"] = b"ON" if skip_bios else b"OFF"
        self._c_str_cache[b"mgba_use_bios"] = b"OFF" if skip_bios else b"ON"

        try:
            self.emu = Emulator(self.core_path)
            self._install_libretro_hooks()
            self.emu.load_game(self.rom_path)
            self.load_sram_from_disk()
            self.cheat_engine.load_default_cheats()

            print("\n=======================================================")
            print(f"[ROM IDENTIFICATION] Title: {self.game_title} | Code: {self.game_code}")
            print(f"[ROM IDENTIFICATION] Hardware Profile: {self.rumble_type}")
            print("[RUMBLE CONFIG] Native Libretro Emulation Active")
            print("=======================================================\n")

            self.game_identified.emit(self.game_title, self.game_code, self.rumble_type)

        except Exception as e:
            self.error_occurred.emit(f"Failed to load ROM: {e}")
            return

        self._is_running = True
        stream = None

        sample_rate = 44100
        try:
            if hasattr(self.emu, "av_info"):
                sample_rate = int(self.emu.av_info.sample_rate)
            import sounddevice as sd
            stream = sd.OutputStream(samplerate=sample_rate, channels=2, dtype="int16", latency="low")
            stream.start()
        except ImportError:
            self.error_occurred.emit("sounddevice module missing. Audio output disabled.")
        except Exception as e:
            self.error_occurred.emit(f"Audio init failed: {e}")

        gen_source = getattr(self.emu, "frames", getattr(self.emu, "video_frames", None))
        if not gen_source:
            self.error_occurred.emit("Frame generator not found in core.")
            return

        generator = gen_source() if callable(gen_source) else iter(gen_source)

        next_time = time.perf_counter()
        frame_counter = 0
        self._last_gui_render_time = time.perf_counter()

        while self._is_running:
            if self.is_rewinding:
                if self.rewind_buffer:
                    saved_fc, comp_bytes = self.rewind_buffer.pop()
                    try:
                        raw_state = zlib.decompress(comp_bytes)
                        if self.unserialize_state_bytes(raw_state):
                            with QMutexLocker(self.mutex):
                                self.replay_frame_counter = saved_fc
                                self.previous_frame = None
                                for btn_id in list(self.standard_buttons.keys()):
                                    if self.emu and hasattr(self.emu, "set_button"):
                                        self.emu.set_button(0, btn_id, False)
                                item = next(generator)

                            vid_obj = item[0] if isinstance(item, tuple) else getattr(item, "video", item)
                            frame_data = self._extract_video_array(vid_obj)
                            self.latest_video_frame = frame_data
                            self.frame_ready.emit(frame_data)
                            self.input_hud_updated.emit("REWIND", saved_fc, 0, 0)
                    except Exception as e:
                        print(f"[REWIND WARNING] Step failed: {e}")

                    time.sleep(0.065)
                else:
                    if not self._notified_rewind_empty:
                        self.rewind_status.emit("⏪ Rewind: Start of Buffer")
                        self._notified_rewind_empty = True
                    time.sleep(0.03)

                next_time = time.perf_counter()
                continue

            if self.is_paused and not self.step_requested:
                time.sleep(0.02)
                next_time = time.perf_counter()
                continue

            was_single_step = self.step_requested
            if was_single_step:
                self.step_requested = False

            if self.reset_requested and bool(self.config.get("emulation", "rewind_enabled", True)):
                self._push_rewind_snapshot(self.replay_frame_counter)

            hud_mode = "IDLE"
            hud_max_frame = 0
            btn_mask = 0

            with QMutexLocker(self.mutex):
                if self.reset_requested and hasattr(self.emu, "reset"):
                    self.flush_sram_to_disk()
                    self.emu.reset()
                    self.replay_frame_counter = 0
                    self.cheat_engine.mark_dirty()
                    self.reset_requested = False

                self.cheat_engine.apply_cheats()

                if self.recorder.is_playing:
                    hud_mode = "PLAYBACK"
                    hud_max_frame = self.recorder.playback_max_frame
                    pressed_btns, should_pause, is_done = self.recorder.step_playback(self.replay_frame_counter)

                    for libretro_id, btn_name in ID_TO_BTN.items():
                        is_down = (btn_name in pressed_btns)
                        if self.emu and hasattr(self.emu, "set_button"):
                            self.emu.set_button(0, libretro_id, is_down)

                    for bit_idx, (btn_name, _) in enumerate(HUD_BTN_BITS):
                        if btn_name in pressed_btns:
                            btn_mask |= (1 << bit_idx)

                    if should_pause:
                        self.is_paused = True

                    if is_done:
                        self.replay_finished.emit()

                elif self.recorder.is_recording:
                    hud_mode = "RECORDING"
                    active_now = []
                    for libretro_id, btn_name in ID_TO_BTN.items():
                        if self.standard_buttons.get(libretro_id, False):
                            active_now.append(btn_name)

                    self.recorder.record_frame(self.replay_frame_counter, active_now, self.is_paused)

                    for bit_idx, (_, lib_id) in enumerate(HUD_BTN_BITS):
                        if self.standard_buttons.get(lib_id, False):
                            btn_mask |= (1 << bit_idx)

                else:
                    hud_mode = "IDLE"
                    autofire_pulse = (frame_counter // 2) % 2 == 0
                    effective_states = dict(self.standard_buttons)
                    for btn_name, libretro_id in AF_TO_LIBRETRO.items():
                        is_af_held = self.autofire_buttons.get(btn_name, False)
                        std_held = self.standard_buttons.get(libretro_id, False)

                        if is_af_held:
                            desired = autofire_pulse or std_held
                            effective_states[libretro_id] = desired
                            if self.emu and hasattr(self.emu, "set_button"):
                                self.emu.set_button(0, libretro_id, desired)
                        else:
                            if self.emu and hasattr(self.emu, "set_button"):
                                self.emu.set_button(0, libretro_id, std_held)

                    for bit_idx, (_, lib_id) in enumerate(HUD_BTN_BITS):
                        if effective_states.get(lib_id, False):
                            btn_mask |= (1 << bit_idx)

                if self.emu:
                    val_x = int(self.tilt_x * 32767)
                    val_y = int(self.tilt_y * 32767)
                    for stick_id in [0, 1]:
                        for meth in ["set_analog", "set_analog_axis", "set_axis"]:
                            if hasattr(self.emu, meth):
                                try:
                                    getattr(self.emu, meth)(0, stick_id, 0, val_x)
                                    getattr(self.emu, meth)(0, stick_id, 1, val_y)
                                    break
                                except Exception:
                                    pass

                self.replay_frame_counter += 1
                current_frame_num = self.replay_frame_counter

                try:
                    item = next(generator)
                except StopIteration:
                    break
                except Exception as e:
                    print(f"Core step error: {e}")
                    break

            rewind_enabled = bool(self.config.get("emulation", "rewind_enabled", True))
            rewind_interval = max(1, int(self.config.get("emulation", "rewind_interval", 10) or 10))
            if rewind_enabled and not self.recorder.is_playing and (frame_counter % rewind_interval == 0):
                self._push_rewind_snapshot(current_frame_num)

            if frame_counter > 0 and frame_counter % 120 == 0:
                self.flush_sram_to_disk()

            if isinstance(item, tuple):
                vid_obj, aud_obj = item[0], item[1] if len(item) > 1 else None
            else:
                vid_obj, aud_obj = getattr(item, "video", item), getattr(item, "audio", None)

            frameskip = self.config.get("video", "frameskip") or 0
            render_this_frame = (frame_counter % (frameskip + 1) == 0) or was_single_step
            frame_counter += 1

            now_ts = time.perf_counter()
            should_emit_gui = True
            if self.fast_forward and not was_single_step:
                if (now_ts - self._last_gui_render_time) < 0.016:
                    should_emit_gui = False
                else:
                    self._last_gui_render_time = now_ts

            if render_this_frame:
                frame_data = self._extract_video_array(vid_obj)
                self.latest_video_frame = frame_data

                if self.config.get("video", "interframe_blending") and not was_single_step:
                    if self.previous_frame is not None and self.previous_frame.shape == frame_data.shape:
                        blended = ((frame_data.astype(np.uint16) + self.previous_frame.astype(np.uint16)) // 2).astype(np.uint8)
                        self.previous_frame = frame_data
                        frame_data = blended
                    else:
                        self.previous_frame = frame_data.copy()

                if should_emit_gui:
                    self.frame_ready.emit(frame_data)

            hud_active = (hud_mode != "IDLE") or bool(self.config.get("video", "show_input_overlay", False))
            if hud_active or (hud_mode != self._last_hud_mode):
                if should_emit_gui or (btn_mask != self._last_hud_mask) or (hud_mode != self._last_hud_mode):
                    self.input_hud_updated.emit(hud_mode, current_frame_num, hud_max_frame, btn_mask)
                    self._last_hud_mode = hud_mode
                    self._last_hud_mask = btn_mask

            is_ff = self.fast_forward
            ff_ratio = self.config.get("emulation", "fast_forward_ratio") or 2.0
            fps_target = (59.7275 * ff_ratio) if is_ff else 59.7275
            frame_time = 1.0 / fps_target
            played_audio = False

            raw_audio = None
            if aud_obj is not None:
                raw_audio = getattr(aud_obj, "data", aud_obj)

            if raw_audio is not None and len(raw_audio) > 0:
                try:
                    audio_arr = np.frombuffer(raw_audio, dtype=np.int16)
                    if len(audio_arr) % 2 == 0:
                        stereo_audio = audio_arr.reshape(-1, 2)
                        processed_audio = self.dsp.process(stereo_audio)

                        if stream and not is_ff and not was_single_step:
                            stream.write(processed_audio)
                            played_audio = True
                except Exception:
                    pass

            if not played_audio:
                now = time.perf_counter()
                sleep_time = next_time - now
                if sleep_time > 0.001 and not was_single_step:
                    time.sleep(sleep_time)
                    next_time += frame_time
                else:
                    if sleep_time < -0.05:
                        next_time = now + frame_time
                    else:
                        next_time += frame_time

        if stream:
            stream.stop()
            stream.close()

        self.flush_sram_to_disk()
        self.cheat_engine.save_default_cheats(only_if_exists_or_nonempty=True)

        with QMutexLocker(self.mutex):
            if self.emu:
                if hasattr(self.emu, "close"):
                    self.emu.close()
                elif hasattr(self.emu, "unload_game"):
                    self.emu.unload_game()
                self.emu = None

        self.stopped.emit()

    def set_input(self, button_id: int, pressed: bool, port: int = 0):
        with QMutexLocker(self.mutex):
            self.standard_buttons[button_id] = pressed
            if self.emu and hasattr(self.emu, "set_button"):
                self.emu.set_button(port, button_id, pressed)

    def set_autofire(self, btn_name: str, pressed: bool):
        with QMutexLocker(self.mutex):
            self.autofire_buttons[btn_name] = pressed

    def step_one_frame(self):
        self.is_paused = True
        self.step_requested = True

    def request_reset(self):
        self.reset_requested = True

    def stop(self):
        self._is_running = False
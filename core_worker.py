# core_worker.py
import os
import sys
import time
import math
import ctypes
import numpy as np

try:
    from PyQt6.QtCore import QThread, pyqtSignal, QRecursiveMutex as QMutexClass, QMutexLocker, QTimer
except ImportError:
    from PyQt6.QtCore import QThread, pyqtSignal, QMutex as QMutexClass, QMutexLocker, QTimer

from pdretro import Emulator, MemoryRegion
from cheats import CheatEngine
from audio_dsp import AudioDSP
from session_recorder import SessionRecorder, ID_TO_BTN

AF_TO_LIBRETRO = {
    "a": 8,
    "b": 0,
    "l": 10,
    "r": 11
}

# =============================================================================
# Libretro Native Environment & Rumble C-Types Specifications
# =============================================================================
RETRO_ENVIRONMENT_SET_PIXEL_FORMAT = 1
RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY = 9
RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS = 11
RETRO_ENVIRONMENT_GET_VARIABLE = 15
RETRO_ENVIRONMENT_SET_VARIABLES = 16
RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE = 17
RETRO_ENVIRONMENT_GET_RUMBLE_INTERFACE = 23
RETRO_ENVIRONMENT_GET_LOG_INTERFACE = 27
RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY = 31

class RetroVariable(ctypes.Structure):
    _fields_ = [
        ("key", ctypes.c_char_p),
        ("value", ctypes.c_char_p)
    ]

# typedef bool (*retro_set_rumble_state_t)(unsigned port, enum retro_rumble_effect effect, uint16_t strength);
RUMBLE_CB_TYPE = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint, ctypes.c_int, ctypes.c_ushort)

class RetroRumbleInterface(ctypes.Structure):
    _fields_ = [
        ("set_rumble_state", RUMBLE_CB_TYPE)
    ]

ENV_CB_TYPE = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint, ctypes.c_void_p)


class EmulatorWorker(QThread):
    frame_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)
    stopped = pyqtSignal()
    replay_finished = pyqtSignal()
    rumble_triggered = pyqtSignal(float, float, int)
    solar_changed = pyqtSignal(int)
    tilt_changed = pyqtSignal(float, float, float)
    game_identified = pyqtSignal(str, str, str)

    def __init__(self, core_path: str, rom_path: str, config):
        super().__init__()
        self.core_path = os.path.abspath(core_path)
        self.rom_path = os.path.abspath(rom_path) if rom_path else ""
        self.config = config

        self.emu = None
        self._is_running = False
        self.fast_forward = False
        self.is_paused = False
        self.step_requested = False
        self.reset_requested = False

        self.mutex = QMutexClass()
        self.cheat_engine = CheatEngine(self)
        self.dsp = AudioDSP(self.config)

        self.recorder = SessionRecorder()
        self.replay_frame_counter = 0

        self.latest_video_frame = None
        self.previous_frame = None
        self._last_gui_render_time = 0.0

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

        self._c_str_cache = {
            b"mgba_force_gbp": b"ON",
            b"mgba_use_bios": b"OFF",
            b"mgba_skip_bios": b"ON",
            b"mgba_gb_model": b"Autodetect",
            b"mgba_solar_sensor_level": str(self.solar_level).encode("ascii"),
            b"saves_dir": b"saves",
            b"current_dir": b"."
        }

        # Read Cartridge Header (0xAC: Game Code, 0xA0: Title)
        self.game_code = "UNKNOWN"
        self.game_title = "Unknown"
        self.rumble_type = "None"
        self._parse_rom_header()

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

        if effect == 0:  # RETRO_RUMBLE_STRONG (low frequency motor)
            self._active_rumble_low = norm_val
        else:            # RETRO_RUMBLE_WEAK (high frequency motor)
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
        if hasattr(self, 'dsp') and self.dsp:
            self.dsp.update_settings()

    def run(self):
        skip_bios = bool(self.config.get("emulation", "skip_bios"))
        self._c_str_cache[b"mgba_skip_bios"] = b"ON" if skip_bios else b"OFF"
        self._c_str_cache[b"mgba_use_bios"] = b"OFF" if skip_bios else b"ON"

        try:
            self.emu = Emulator(self.core_path)
            self._install_libretro_hooks()
            self.emu.load_game(self.rom_path)

            print(f"\n=======================================================")
            print(f"[ROM IDENTIFICATION] Title: {self.game_title} | Code: {self.game_code}")
            print(f"[ROM IDENTIFICATION] Hardware Profile: {self.rumble_type}")
            print(f"[RUMBLE CONFIG] Native Libretro Emulation Active")
            print(f"=======================================================\n")

            self.game_identified.emit(self.game_title, self.game_code, self.rumble_type)

        except Exception as e:
            self.error_occurred.emit(f"Failed to load ROM: {e}")
            return

        self._is_running = True
        stream = None

        if not self.config.get("audio", "mute"):
            sample_rate = 44100
            try:
                if hasattr(self.emu, 'av_info'):
                    sample_rate = int(self.emu.av_info.sample_rate)
                import sounddevice as sd
                stream = sd.OutputStream(samplerate=sample_rate, channels=2, dtype='int16', latency='low')
                stream.start()
            except ImportError:
                self.error_occurred.emit("sounddevice module missing. Audio output disabled.")
            except Exception as e:
                self.error_occurred.emit(f"Audio init failed: {e}")

        gen_source = getattr(self.emu, 'frames', getattr(self.emu, 'video_frames', None))
        if not gen_source:
            self.error_occurred.emit("Frame generator not found in core.")
            return

        generator = gen_source() if callable(gen_source) else iter(gen_source)

        next_time = time.perf_counter()
        frame_counter = 0
        self._last_gui_render_time = time.perf_counter()

        while self._is_running:
            if self.is_paused and not self.step_requested:
                time.sleep(0.02)
                next_time = time.perf_counter()
                continue

            was_single_step = self.step_requested
            if was_single_step:
                self.step_requested = False

            with QMutexLocker(self.mutex):
                if self.reset_requested and hasattr(self.emu, 'reset'):
                    self.emu.reset()
                    self.replay_frame_counter = 0
                    self.reset_requested = False

                self.cheat_engine.apply_cheats()

                # Process Replay vs Normal Input
                if self.recorder.is_playing:
                    pressed_btns, should_pause, is_done = self.recorder.step_playback(self.replay_frame_counter)

                    for libretro_id, btn_name in ID_TO_BTN.items():
                        is_down = (btn_name in pressed_btns)
                        if self.emu and hasattr(self.emu, 'set_button'):
                            self.emu.set_button(0, libretro_id, is_down)

                    if should_pause:
                        self.is_paused = True

                    if is_done:
                        self.replay_finished.emit()

                elif self.recorder.is_recording:
                    active_now = []
                    for libretro_id, btn_name in ID_TO_BTN.items():
                        if self.standard_buttons.get(libretro_id, False):
                            active_now.append(btn_name)

                    self.recorder.record_frame(self.replay_frame_counter, active_now, self.is_paused)

                else:
                    autofire_pulse = (frame_counter // 2) % 2 == 0
                    for btn_name, libretro_id in AF_TO_LIBRETRO.items():
                        is_af_held = self.autofire_buttons.get(btn_name, False)
                        std_held = self.standard_buttons.get(libretro_id, False)

                        if is_af_held:
                            desired = autofire_pulse or std_held
                            if self.emu and hasattr(self.emu, 'set_button'):
                                self.emu.set_button(0, libretro_id, desired)
                        else:
                            if self.emu and hasattr(self.emu, 'set_button'):
                                self.emu.set_button(0, libretro_id, std_held)

                # Send Analog Tilt / Gyro Sensor Axes
                if self.emu:
                    val_x = int(self.tilt_x * 32767)
                    val_y = int(self.tilt_y * 32767)
                    for stick_id in [0, 1]:
                        for meth in ['set_analog', 'set_analog_axis', 'set_axis']:
                            if hasattr(self.emu, meth):
                                try:
                                    getattr(self.emu, meth)(0, stick_id, 0, val_x)
                                    getattr(self.emu, meth)(0, stick_id, 1, val_y)
                                    break
                                except Exception: pass

                self.replay_frame_counter += 1

                try:
                    item = next(generator)
                except StopIteration:
                    break
                except Exception as e:
                    print(f"Core step error: {e}")
                    break

            if isinstance(item, tuple):
                vid_obj, aud_obj = item[0], item[1] if len(item) > 1 else None
            else:
                vid_obj, aud_obj = getattr(item, 'video', item), getattr(item, 'audio', None)

            # Video Processing
            frameskip = self.config.get("video", "frameskip") or 0
            render_this_frame = (frame_counter % (frameskip + 1) == 0) or was_single_step
            frame_counter += 1

            if render_this_frame:
                if hasattr(vid_obj, 'to_rgb'): frame_data = vid_obj.to_rgb()
                elif hasattr(vid_obj, 'data'): frame_data = np.array(vid_obj.data, copy=False)
                else: frame_data = np.array(vid_obj, copy=False)

                self.latest_video_frame = frame_data

                if self.config.get("video", "interframe_blending") and not was_single_step:
                    if self.previous_frame is not None and self.previous_frame.shape == frame_data.shape:
                        blended = ((frame_data.astype(np.uint16) + self.previous_frame.astype(np.uint16)) // 2).astype(np.uint8)
                        self.previous_frame = frame_data
                        frame_data = blended
                    else:
                        self.previous_frame = frame_data.copy()

                # =============================================================
                # FAST-FORWARD GUI EVENT QUEUE THROTTLING
                # Caps display updates to ~60 FPS during fast forward so Qt's
                # event loop is never saturated and the UI never freezes.
                # =============================================================
                now_ts = time.perf_counter()
                should_emit_gui = True
                if self.fast_forward and not was_single_step:
                    if (now_ts - self._last_gui_render_time) < 0.016:
                        should_emit_gui = False
                    else:
                        self._last_gui_render_time = now_ts

                if should_emit_gui:
                    self.frame_ready.emit(frame_data)

            # Audio Processing
            is_ff = self.fast_forward
            ff_ratio = self.config.get("emulation", "fast_forward_ratio") or 2.0
            fps_target = (59.7275 * ff_ratio) if is_ff else 59.7275
            frame_time = 1.0 / fps_target
            played_audio = False

            raw_audio = None
            if aud_obj is not None:
                raw_audio = getattr(aud_obj, 'data', aud_obj)

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

            # =============================================================
            # CLOCK PACING FOR NORMAL & FAST-FORWARD SPEED
            # Caps emulation speed to fps_target precisely, eliminating 
            # uncontrolled 1000+ FPS spinlocks that cause "Not Responding".
            # =============================================================
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

        with QMutexLocker(self.mutex):
            if self.emu:
                if hasattr(self.emu, 'close'): self.emu.close()
                elif hasattr(self.emu, 'unload_game'): self.emu.unload_game()
                self.emu = None

        self.stopped.emit()

    def set_solar_level(self, level: int):
        level = max(0, min(10, int(level)))
        self.solar_level = level
        self.config.set("sensors", "solar_level", level)
        self.solar_changed.emit(self.solar_level)

    def step_solar_level(self, delta: int):
        new_val = max(0, min(10, self.solar_level + delta))
        if new_val != self.solar_level:
            self.set_solar_level(new_val)
            btn = 15 if delta > 0 else 14
            self.set_input(btn, True)
            QTimer.singleShot(100, lambda: self.set_input(btn, False))

    def set_analog_motion(self, x: float, y: float):
        self.tilt_x = max(-1.0, min(1.0, float(x)))
        self.tilt_y = max(-1.0, min(1.0, float(y)))
        angle = math.degrees(math.atan2(self.tilt_x, -self.tilt_y)) if (self.tilt_x != 0 or self.tilt_y != 0) else 0.0
        self.gyro_angle = angle
        self.tilt_changed.emit(self.tilt_x, self.tilt_y, self.gyro_angle)

    def set_gyro_angle(self, angle_degrees: float):
        self.gyro_angle = float(angle_degrees)
        rad = math.radians(self.gyro_angle)
        self.tilt_x = math.sin(rad)
        self.tilt_y = -math.cos(rad)
        self.tilt_changed.emit(self.tilt_x, self.tilt_y, self.gyro_angle)

    def reset_sensors(self):
        self.set_analog_motion(0.0, 0.0)

    def set_input(self, button_id: int, pressed: bool, port: int = 0):
        with QMutexLocker(self.mutex):
            self.standard_buttons[button_id] = pressed
            if self.emu and hasattr(self.emu, 'set_button'):
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

    def save_state(self, filepath: str) -> bool:
        with QMutexLocker(self.mutex):
            if not self.emu: return False

            state_bytes = None
            try:
                import _ra_wrapper
                state_size = _ra_wrapper.get_state_size()
                if state_size > 0:
                    state_bytes = _ra_wrapper.serialize_state()
            except (ImportError, AttributeError):
                try:
                    from pdretro import _ra_wrapper
                    state_size = _ra_wrapper.get_state_size()
                    if state_size > 0:
                        state_bytes = _ra_wrapper.serialize_state()
                except (ImportError, AttributeError): pass

            if state_bytes is None:
                wrapper = getattr(self.emu, '_wrapper', getattr(self.emu, '_ra_wrapper', None))
                if wrapper and hasattr(wrapper, 'serialize_state'):
                    try: state_bytes = wrapper.serialize_state()
                    except Exception: pass

            if state_bytes is None:
                for method_name in ["serialize", "save_state", "get_state", "serialize_state"]:
                    if hasattr(self.emu, method_name):
                        try:
                            state_bytes = getattr(self.emu, method_name)()
                            if state_bytes: break
                        except Exception: pass

            if state_bytes:
                os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
                with open(filepath, "wb") as f:
                    f.write(state_bytes)
                return True
            return False

    def load_state(self, filepath: str) -> bool:
        if not os.path.exists(filepath): return False
        with open(filepath, "rb") as f: state_bytes = f.read()
        if not state_bytes: return False

        with QMutexLocker(self.mutex):
            if not self.emu: return False

            try:
                import _ra_wrapper
                if hasattr(_ra_wrapper, 'unserialize_state'):
                    _ra_wrapper.unserialize_state(state_bytes)
                    return True
            except (ImportError, AttributeError):
                try:
                    from pdretro import _ra_wrapper
                    if hasattr(_ra_wrapper, 'unserialize_state'):
                        _ra_wrapper.unserialize_state(state_bytes)
                        return True
                except (ImportError, AttributeError): pass

            wrapper = getattr(self.emu, '_wrapper', getattr(self.emu, '_ra_wrapper', None))
            if wrapper and hasattr(wrapper, 'unserialize_state'):
                try:
                    wrapper.unserialize_state(state_bytes)
                    return True
                except Exception: pass

            for method_name in ["unserialize", "load_state", "set_state", "unserialize_state"]:
                if hasattr(self.emu, method_name):
                    try:
                        getattr(self.emu, method_name)(state_bytes)
                        return True
                    except Exception: pass

            return False

    def get_memory_region(self, region_name) -> bytes:
        with QMutexLocker(self.mutex):
            if not self.emu: return None
            try:
                target_enum = None
                if region_name in ["VRAM", "VIDEO_RAM", 3]:
                    target_enum = getattr(MemoryRegion, 'VIDEO_RAM', 3)
                elif region_name in ["EWRAM", "SYSTEM_RAM", 2]:
                    target_enum = getattr(MemoryRegion, 'SYSTEM_RAM', 2)
                elif region_name in ["SRAM", "SAVE_RAM", 0]:
                    target_enum = getattr(MemoryRegion, 'SAVE_RAM', 0)
                elif region_name in ["RTC", 1]:
                    target_enum = getattr(MemoryRegion, 'RTC', 1)
                elif region_name in ["PRAM", "PALETTE_RAM", 4]:
                    target_enum = getattr(MemoryRegion, 'PALETTE_RAM', 4)
                elif region_name in ["OAM", 5]:
                    target_enum = getattr(MemoryRegion, 'OAM', 5)

                if target_enum is not None:
                    mem = self.emu.get_memory(target_enum)
                    if mem and hasattr(mem, 'data'):
                        return bytes(mem.data)
                    elif mem and hasattr(mem, 'read_bytes'):
                        return mem.read_bytes(0, mem.size)
            except Exception: pass

            if region_name in ["IWRAM"]:
                try:
                    sys_enum = getattr(MemoryRegion, 'SYSTEM_RAM', 2)
                    mem = self.emu.get_memory(sys_enum)
                    if mem and hasattr(mem, 'data'):
                        raw = bytes(mem.data)
                        if len(raw) >= (256 + 32) * 1024:
                            return raw[256 * 1024:(256 + 32) * 1024]
                        return raw[:32 * 1024]
                except Exception: pass

            if region_name in ["ROM", "CARTRIDGE"] and self.rom_path and os.path.exists(self.rom_path):
                try:
                    with open(self.rom_path, 'rb') as f:
                        return f.read()
                except Exception: pass

        return None

    def write_memory_region(self, region_name, offset: int, data: bytes) -> bool:
        with QMutexLocker(self.mutex):
            if not self.emu: return False
            try:
                target_enum = None
                if region_name in ["VRAM", "VIDEO_RAM", 3]:
                    target_enum = getattr(MemoryRegion, 'VIDEO_RAM', 3)
                elif region_name in ["EWRAM", "SYSTEM_RAM", 2]:
                    target_enum = getattr(MemoryRegion, 'SYSTEM_RAM', 2)
                elif region_name in ["SRAM", "SAVE_RAM", 0]:
                    target_enum = getattr(MemoryRegion, 'SAVE_RAM', 0)
                elif region_name in ["RTC", 1]:
                    target_enum = getattr(MemoryRegion, 'RTC', 1)
                elif region_name in ["PRAM", "PALETTE_RAM", 4]:
                    target_enum = getattr(MemoryRegion, 'PALETTE_RAM', 4)
                elif region_name in ["OAM", 5]:
                    target_enum = getattr(MemoryRegion, 'OAM', 5)
                elif region_name in ["IWRAM"]:
                    target_enum = getattr(MemoryRegion, 'SYSTEM_RAM', 2)
                    offset += 256 * 1024

                if target_enum is not None:
                    mem = self.emu.get_memory(target_enum)
                    if mem:
                        if hasattr(mem, 'write_bytes'):
                            mem.write_bytes(offset, data)
                            return True
                        elif hasattr(mem, 'data'):
                            mem.data[offset:offset + len(data)] = np.frombuffer(data, dtype=np.uint8)
                            return True
            except Exception as e:
                print(f"Write memory error: {e}")
        return False

    def start_recording_session(self, replays_dir: str = "replays") -> str:
        state_path = self.recorder.start_recording(self, self.rom_path, replays_dir)
        if state_path:
            with QMutexLocker(self.mutex):
                self.replay_frame_counter = 0
        return state_path

    def stop_recording_session(self, replays_dir: str = "replays") -> str:
        with QMutexLocker(self.mutex):
            total_frames = self.replay_frame_counter
        return self.recorder.stop_recording(total_frames, replays_dir)

    def start_replay_session(self, json_path: str) -> bool:
        success = self.recorder.load_replay(json_path, self)
        if success:
            with QMutexLocker(self.mutex):
                self.replay_frame_counter = 0
        return success

    def stop_replay_session(self):
        with QMutexLocker(self.mutex):
            self.recorder.stop_playback()
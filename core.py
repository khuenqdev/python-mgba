# core.py
import os
import time
import threading
import numpy as np
from pdretro import Emulator

class EmulatorCore:
    def __init__(self, core_path: str, rom_path: str, config: dict):
        if not os.path.exists(core_path):
            raise FileNotFoundError(f"mGBA Core not found at {core_path}")
        if not os.path.exists(rom_path):
            raise FileNotFoundError(f"GBA ROM not found at {rom_path}")

        self.emu = Emulator(core_path)
        self.config = config
        self.fast_forward = False
        
        # Configure BIOS based on config dictionary
        skip_bios = self.config.get("skip_bios", True)
        try:
            if hasattr(self.emu, 'options'):
                self.emu.options["mgba_use_bios"] = "ON"
                self.emu.options["mgba_skip_bios"] = "ON" if skip_bios else "OFF"
                
            set_opt = getattr(self.emu, 'set_core_option', getattr(self.emu, 'set_option', None))
            if set_opt:
                set_opt("mgba_use_bios", "ON")
                set_opt("mgba_skip_bios", "ON" if skip_bios else "OFF")  
        except Exception as e:
            print("Failed to set BIOS options:", e)

        self.emu.load_game(rom_path)
        
        # Threading mechanisms
        self.latest_video_frame = None
        self._stop_event = threading.Event()
        self.core_lock = threading.Lock() 
        self.audio_error = None

    def run_loop(self):
        """The main execution thread for the emulator."""
        stream = None
        
        if self.config.get("enable_audio", False):
            sample_rate = 44100 
            try:
                if hasattr(self.emu, 'av_info'):
                    sample_rate = int(self.emu.av_info.sample_rate)
            except Exception: pass

            try:
                import sounddevice as sd
                stream = sd.OutputStream(samplerate=sample_rate, channels=2, dtype='int16', latency='low')
                stream.start()
            except ImportError:
                self.audio_error = "sounddevice module not installed. Audio disabled."
            except Exception as e:
                self.audio_error = f"Audio failed to initialize: {e}"

        gen_source = getattr(self.emu, 'frames', getattr(self.emu, 'video_frames', None))
        if not gen_source: 
            raise RuntimeError("Valid frame generator not found in core!")

        generator = gen_source() if callable(gen_source) else iter(gen_source)
        next_time = time.perf_counter()

        try:
            while not self._stop_event.is_set():
                with self.core_lock:
                    try:
                        item = next(generator)
                    except StopIteration:
                        break 
                
                # Unpack Frame
                if isinstance(item, tuple):
                    vid_obj, aud_obj = item[0], item[1] if len(item) > 1 else None
                else:
                    vid_obj, aud_obj = getattr(item, 'video', item), getattr(item, 'audio', None)
                
                # Assign Video Array
                if hasattr(vid_obj, 'to_rgb'): 
                    self.latest_video_frame = vid_obj.to_rgb()
                elif hasattr(vid_obj, 'data'): 
                    self.latest_video_frame = np.array(vid_obj.data, copy=False)
                else: 
                    self.latest_video_frame = np.array(vid_obj, copy=False)
                    
                # Calculate Framerate
                is_ff = self.fast_forward
                ff_mult = self.config.get("fast_forward_speed", 2.0)
                fps_target = (59.7275 * ff_mult) if is_ff else 59.7275
                frame_time = 1.0 / fps_target
                played_audio = False
                
                # Handle Audio
                if stream and aud_obj is not None and not is_ff:
                    raw_audio = getattr(aud_obj, 'data', aud_obj)
                    if raw_audio is not None and len(raw_audio) > 0:
                        try:
                            audio_arr = np.frombuffer(raw_audio, dtype=np.int16)
                            if len(audio_arr) % 2 == 0:
                                stream.write(audio_arr.reshape(-1, 2))
                                played_audio = True
                        except Exception: pass

                # Sync Clock
                if not played_audio:
                    now = time.perf_counter()
                    sleep_time = next_time - now
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                        next_time += frame_time
                    else:
                        next_time = now + frame_time 

        finally:
            if stream:
                stream.stop()
                stream.close()

    def get_memory_region(self, region_id: int) -> bytes:
        """Safely extracts raw memory from the running core."""
        with self.core_lock:
            try:
                mem = self.emu.get_memory(region_id)
                if mem is not None:
                    if hasattr(mem, 'data'): return bytes(mem.data)
                    elif hasattr(mem, 'read'): return mem.read(0, getattr(mem, 'size', 98304))
            except Exception as e:
                print(f"Failed to fetch memory ID {region_id}: {e}")
        return None

    def set_input(self, button_id: int, pressed: bool, port: int = 0):
        with self.core_lock:
            if hasattr(self.emu, 'set_button'):
                self.emu.set_button(port, button_id, pressed)

    def save_state(self, filepath: str):
        with self.core_lock:
            state_bytes = self.emu.save_state()
        with open(filepath, "wb") as f: 
            f.write(state_bytes)

    def load_state(self, filepath: str):
        if not os.path.exists(filepath): return
        with open(filepath, "rb") as f: 
            state_bytes = f.read()
        with self.core_lock: 
            self.emu.load_state(state_bytes)

    def stop(self):
        self._stop_event.set()

    def close(self):
        self.stop()
        with self.core_lock:
            if hasattr(self, 'emu') and self.emu:
                if hasattr(self.emu, 'close'): self.emu.close()
                elif hasattr(self.emu, 'unload_game'): self.emu.unload_game()
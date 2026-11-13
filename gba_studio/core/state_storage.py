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
from .cheats import CheatEngine
from .audio_dsp import AudioDSP
from .session_recorder import SessionRecorder, ID_TO_BTN

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

from .libretro_types import RetroVariable, RetroRumbleInterface

class StateStorageMixin:
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


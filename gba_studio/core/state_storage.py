# state_storage.py
import os
import hashlib
import numpy as np

try:
    from PyQt6.QtCore import QMutexLocker
except ImportError:
    from PyQt6.QtCore import QMutexLocker

from pdretro import MemoryRegion


class StateStorageMixin:
    def serialize_state_bytes(self) -> bytes | None:
        """Serializes the current emulator state into an in-memory byte buffer."""
        with QMutexLocker(self.mutex):
            if not self.emu:
                return None

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
                except (ImportError, AttributeError):
                    pass

            if state_bytes is None:
                wrapper = getattr(self.emu, "_wrapper", getattr(self.emu, "_ra_wrapper", None))
                if wrapper and hasattr(wrapper, "serialize_state"):
                    try:
                        state_bytes = wrapper.serialize_state()
                    except Exception:
                        pass

            if state_bytes is None:
                for method_name in ["serialize", "save_state", "get_state", "serialize_state"]:
                    if hasattr(self.emu, method_name):
                        try:
                            state_bytes = getattr(self.emu, method_name)()
                            if state_bytes:
                                break
                        except Exception:
                            pass

            return bytes(state_bytes) if state_bytes else None

    def unserialize_state_bytes(self, state_bytes: bytes) -> bool:
        """Restores emulator state directly from an in-memory byte buffer."""
        if not state_bytes:
            return False

        with QMutexLocker(self.mutex):
            if not self.emu:
                return False

            try:
                import _ra_wrapper
                if hasattr(_ra_wrapper, "unserialize_state"):
                    _ra_wrapper.unserialize_state(state_bytes)
                    return True
            except (ImportError, AttributeError):
                try:
                    from pdretro import _ra_wrapper
                    if hasattr(_ra_wrapper, "unserialize_state"):
                        _ra_wrapper.unserialize_state(state_bytes)
                        return True
                except (ImportError, AttributeError):
                    pass

            wrapper = getattr(self.emu, "_wrapper", getattr(self.emu, "_ra_wrapper", None))
            if wrapper and hasattr(wrapper, "unserialize_state"):
                try:
                    wrapper.unserialize_state(state_bytes)
                    return True
                except Exception:
                    pass

            for method_name in ["unserialize", "load_state", "set_state", "unserialize_state"]:
                if hasattr(self.emu, method_name):
                    try:
                        getattr(self.emu, method_name)(state_bytes)
                        return True
                    except Exception:
                        pass

            return False

    def save_state(self, filepath: str) -> bool:
        state_bytes = self.serialize_state_bytes()
        if state_bytes:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            with open(filepath, "wb") as f:
                f.write(state_bytes)
            return True
        return False

    def load_state(self, filepath: str) -> bool:
        if not os.path.exists(filepath):
            return False
        with open(filepath, "rb") as f:
            state_bytes = f.read()
        return self.unserialize_state_bytes(state_bytes)

    def _get_sram_path(self) -> str:
        saves_dir = self.config.get("paths", "saves") or "saves"
        os.makedirs(saves_dir, exist_ok=True)
        base_name = getattr(self, "save_base_name", None)
        if not base_name and self.rom_path:
            base_name = os.path.splitext(os.path.basename(self.rom_path))[0]
        if not base_name:
            base_name = "game"
        return os.path.join(saves_dir, f"{base_name}.sav")

    def load_sram_from_disk(self) -> bool:
        """Loads battery save (.sav) into SRAM on ROM boot and establishes hash baseline."""
        sav_path = self._get_sram_path()
        loaded = False
        if os.path.exists(sav_path):
            try:
                with open(sav_path, "rb") as f:
                    sav_data = f.read()
                if sav_data:
                    loaded = self.write_memory_region("SRAM", 0, sav_data)
                    if loaded:
                        print(f"[SAVE RAM] Loaded battery save: {sav_path} ({len(sav_data)} bytes)")
            except Exception as e:
                print(f"[SAVE RAM WARNING] Failed to load {sav_path}: {e}")

        sram_now = self.get_memory_region("SRAM")
        self._last_sram_digest = hashlib.sha1(sram_now).digest() if sram_now else None
        return loaded

    def flush_sram_to_disk(self, force: bool = False) -> bool:
        """Writes SRAM changes atomically to saves/<rom>.sav when modified."""
        sram_bytes = self.get_memory_region("SRAM")
        if not sram_bytes:
            return False

        digest = hashlib.sha1(sram_bytes).digest()
        last_digest = getattr(self, "_last_sram_digest", None)
        if not force and digest == last_digest:
            return False

        sav_path = self._get_sram_path()
        # Avoid creating blank .sav files for games that haven't written non-empty save data yet
        if not os.path.exists(sav_path) and not force:
            if not any(b not in (0x00, 0xFF) for b in sram_bytes):
                self._last_sram_digest = digest
                return False

        try:
            tmp_path = sav_path + ".tmp"
            with open(tmp_path, "wb") as f:
                f.write(sram_bytes)
            os.replace(tmp_path, sav_path)
            self._last_sram_digest = digest
            return True
        except Exception as e:
            print(f"[SAVE RAM WARNING] Failed to save {sav_path}: {e}")
            return False

    def get_memory_region(self, region_name) -> bytes:
        with QMutexLocker(self.mutex):
            if not self.emu:
                return None
            try:
                target_enum = None
                if region_name in ["VRAM", "VIDEO_RAM", 3]:
                    target_enum = getattr(MemoryRegion, "VIDEO_RAM", 3)
                elif region_name in ["EWRAM", "SYSTEM_RAM", 2]:
                    target_enum = getattr(MemoryRegion, "SYSTEM_RAM", 2)
                elif region_name in ["SRAM", "SAVE_RAM", 0]:
                    target_enum = getattr(MemoryRegion, "SAVE_RAM", 0)
                elif region_name in ["RTC", 1]:
                    target_enum = getattr(MemoryRegion, "RTC", 1)
                elif region_name in ["PRAM", "PALETTE_RAM", 4]:
                    target_enum = getattr(MemoryRegion, "PALETTE_RAM", 4)
                elif region_name in ["OAM", 5]:
                    target_enum = getattr(MemoryRegion, "OAM", 5)

                if target_enum is not None:
                    mem = self.emu.get_memory(target_enum)
                    if mem and hasattr(mem, "data"):
                        return bytes(mem.data)
                    elif mem and hasattr(mem, "read_bytes"):
                        return mem.read_bytes(0, mem.size)
            except Exception:
                pass

            if region_name in ["IWRAM"]:
                try:
                    sys_enum = getattr(MemoryRegion, "SYSTEM_RAM", 2)
                    mem = self.emu.get_memory(sys_enum)
                    if mem and hasattr(mem, "data"):
                        raw = bytes(mem.data)
                        if len(raw) >= (256 + 32) * 1024:
                            return raw[256 * 1024:(256 + 32) * 1024]
                        return raw[:32 * 1024]
                except Exception:
                    pass

            if region_name in ["ROM", "CARTRIDGE"] and self.rom_path and os.path.exists(self.rom_path):
                try:
                    with open(self.rom_path, "rb") as f:
                        return f.read()
                except Exception:
                    pass

        return None

    def write_memory_region(self, region_name, offset: int, data: bytes) -> bool:
        if not data or offset < 0:
            return False

        with QMutexLocker(self.mutex):
            if not self.emu:
                return False
            try:
                target_enum = None
                if region_name in ["VRAM", "VIDEO_RAM", 3]:
                    target_enum = getattr(MemoryRegion, "VIDEO_RAM", 3)
                elif region_name in ["EWRAM", "SYSTEM_RAM", 2]:
                    target_enum = getattr(MemoryRegion, "SYSTEM_RAM", 2)
                elif region_name in ["SRAM", "SAVE_RAM", 0]:
                    target_enum = getattr(MemoryRegion, "SAVE_RAM", 0)
                elif region_name in ["RTC", 1]:
                    target_enum = getattr(MemoryRegion, "RTC", 1)
                elif region_name in ["PRAM", "PALETTE_RAM", 4]:
                    target_enum = getattr(MemoryRegion, "PALETTE_RAM", 4)
                elif region_name in ["OAM", 5]:
                    target_enum = getattr(MemoryRegion, "OAM", 5)
                elif region_name in ["IWRAM"]:
                    target_enum = getattr(MemoryRegion, "SYSTEM_RAM", 2)
                    offset += 256 * 1024

                if target_enum is not None:
                    mem = self.emu.get_memory(target_enum)
                    if mem:
                        if hasattr(mem, "write_bytes"):
                            mem_size = getattr(mem, "size", len(data) + offset)
                            if offset >= mem_size:
                                return False
                            write_len = min(len(data), mem_size - offset)
                            mem.write_bytes(offset, data[:write_len])
                            return True
                        elif hasattr(mem, "data"):
                            buf_len = len(mem.data)
                            if offset >= buf_len:
                                return False
                            write_len = min(len(data), buf_len - offset)
                            mem.data[offset:offset + write_len] = np.frombuffer(
                                data[:write_len], dtype=np.uint8
                            )
                            return True
            except Exception as e:
                print(f"Write memory error: {e}")
        return False
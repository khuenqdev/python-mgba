# watchpoints.py
import os
import json
import struct
import numpy as np

try:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
    CAPSTONE_AVAILABLE = True
except ImportError:
    CAPSTONE_AVAILABLE = False

WATCH_TYPES = ["uint8", "int8", "uint16", "int16", "uint32", "int32", "float", "hex", "string"]


def resolve_gba_address(addr: int) -> tuple[str | None, int | None, int]:
    """Maps a 32-bit GBA bus address to (region_name, libretro_mem_id, byte_offset)."""
    if 0x02000000 <= addr <= 0x0203FFFF:
        return "EWRAM", 2, addr - 0x02000000
    if 0x03000000 <= addr <= 0x03007FFF:
        return "IWRAM", 2, (256 * 1024) + (addr - 0x03000000)
    if 0x05000000 <= addr <= 0x050003FF:
        return "PRAM", 4, addr - 0x05000000
    if 0x06000000 <= addr <= 0x06017FFF:
        return "VRAM", 3, addr - 0x06000000
    if 0x07000000 <= addr <= 0x070003FF:
        return "OAM", 5, addr - 0x07000000
    if 0x0E000000 <= addr <= 0x0E00FFFF:
        return "SRAM", 0, addr - 0x0E000000
    return None, None, 0


def dtype_byte_width(dtype: str, length: int = 8) -> int:
    if dtype in ("uint8", "int8"):
        return 1
    if dtype in ("uint16", "int16"):
        return 2
    if dtype in ("uint32", "int32", "float"):
        return 4
    if dtype in ("hex", "string"):
        return max(1, min(64, int(length)))
    return 2


def decode_watch_bytes(raw: bytes, dtype: str) -> str:
    if not raw:
        return "?"
    try:
        if dtype == "uint8":
            return str(raw[0])
        if dtype == "int8":
            return str(struct.unpack("<b", raw[:1])[0])
        if dtype == "uint16":
            return str(struct.unpack("<H", raw[:2])[0])
        if dtype == "int16":
            return str(struct.unpack("<h", raw[:2])[0])
        if dtype == "uint32":
            return str(struct.unpack("<I", raw[:4])[0])
        if dtype == "int32":
            return str(struct.unpack("<i", raw[:4])[0])
        if dtype == "float":
            return f"{struct.unpack('<f', raw[:4])[0]:.5g}"
        if dtype == "hex":
            return " ".join(f"{b:02X}" for b in raw)
        if dtype == "string":
            return raw.split(b"\x00", 1)[0].decode("latin-1", errors="replace")
    except Exception:
        pass
    return " ".join(f"{b:02X}" for b in raw)


def encode_watch_value(text: str, dtype: str, width: int = 4) -> bytes | None:
    s = text.strip()
    if not s:
        return None
    try:
        if dtype == "uint8":
            v = int(s, 0) & 0xFF
            return struct.pack("<B", v)
        if dtype == "int8":
            v = max(-128, min(127, int(s, 0)))
            return struct.pack("<b", v)
        if dtype == "uint16":
            v = int(s, 0) & 0xFFFF
            return struct.pack("<H", v)
        if dtype == "int16":
            v = max(-32768, min(32767, int(s, 0)))
            return struct.pack("<h", v)
        if dtype == "uint32":
            v = int(s, 0) & 0xFFFFFFFF
            return struct.pack("<I", v)
        if dtype == "int32":
            v = max(-2147483648, min(2147483647, int(s, 0)))
            return struct.pack("<i", v)
        if dtype == "float":
            return struct.pack("<f", float(s))
        if dtype == "hex":
            clean = s.replace(" ", "").replace("0x", "").replace(",", "")
            if len(clean) % 2 != 0:
                clean = "0" + clean
            return bytes.fromhex(clean)
        if dtype == "string":
            raw = s.encode("latin-1", errors="replace")
            if len(raw) < width:
                raw = raw + b"\x00" * (width - len(raw))
            return raw[:width]
    except Exception:
        return None
    return None


def find_calling_instructions(rom_path: str, target_addr: int, max_hits: int = 6) -> str:
    """
    Scans the GBA ROM for literal-pool pointers to `target_addr` (or its enclosing struct base)
    and disassembles surrounding Thumb/ARM instructions via Capstone.
    """
    if not rom_path or not os.path.exists(rom_path):
        return "; ROM file not available on disk for disassembly."

    try:
        with open(rom_path, "rb") as f:
            rom_data = f.read()
    except OSError as e:
        return f"; Failed to read ROM: {e}"

    rom_len = len(rom_data)
    words = np.frombuffer(rom_data[:rom_len - (rom_len % 4)], dtype="<u4")

    # 1. Exact address match in literal pool
    exact_hits = np.where(words == (target_addr & 0xFFFFFFFF))[0]
    match_kind = "Exact Address Reference"

    # 2. Fallback: struct base pointer within [target_addr - 0x200, target_addr]
    if len(exact_hits) == 0:
        lo = (target_addr - 0x200) & 0xFFFFFFFF
        hi = target_addr & 0xFFFFFFFF
        exact_hits = np.where((words >= lo) & (words <= hi) & ((words & 3) == 0))[0]
        match_kind = "Nearby Struct Base Reference"

    if len(exact_hits) == 0:
        return (
            f"; No direct ROM literal-pool reference found for 0x{target_addr:08X}.\n"
            f"; (The address may be allocated on a heap/stack pointer or written via DMA.)"
        )

    lines = [
        f"; Capstone ROM XRef Analysis for 0x{target_addr:08X} ({match_kind})",
        f"; Found {len(exact_hits)} literal-pool candidate(s) in ROM (showing up to {max_hits}):",
        "",
    ]

    if not CAPSTONE_AVAILABLE:
        for w_idx in exact_hits[:max_hits]:
            pool_off = int(w_idx) * 4
            val = int(words[w_idx])
            lines.append(f"0x{0x08000000 + pool_off:08X}: .word 0x{val:08X}  (Install 'capstone' for disassembly)")
        return "\n".join(lines)

    cs_thumb = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    cs_arm = Cs(CS_ARCH_ARM, CS_MODE_ARM)

    for w_idx in exact_hits[:max_hits]:
        pool_off = int(w_idx) * 4
        pool_val = int(words[w_idx])
        pool_bus = 0x08000000 + pool_off

        # Preceding Thumb code window (Thumb LDR [pc, #imm] reaches up to +1020 bytes forward)
        code_start = max(0, pool_off - 96) & ~1
        code_slice = rom_data[code_start:pool_off]
        thumb_insns = list(cs_thumb.disasm(code_slice, 0x08000000 + code_start))

        # Keep instructions that highlight LDR/STR memory operations
        write_ops = [
            ins for ins in thumb_insns
            if ins.mnemonic.startswith(("str", "stm", "ldr", "swp"))
        ]

        lines.append(f"; === Literal Pool @ 0x{pool_bus:08X} -> 0x{pool_val:08X} (delta +{target_addr - pool_val:#x}) ===")
        if thumb_insns and write_ops:
            for ins in thumb_insns[-18:]:
                marker = "  <-- WRITE" if ins.mnemonic.startswith(("str", "stm")) else ""
                lines.append(f"  0x{ins.address:08X}:  {ins.mnemonic:<8} {ins.op_str}{marker}")
        else:
            arm_start = max(0, pool_off - 96) & ~3
            for ins in list(cs_arm.disasm(rom_data[arm_start:pool_off], 0x08000000 + arm_start))[-12:]:
                marker = "  <-- WRITE" if ins.mnemonic.startswith(("str", "stm")) else ""
                lines.append(f"  0x{ins.address:08X}:  {ins.mnemonic:<8} {ins.op_str}{marker}")
        lines.append("")

    return "\n".join(lines)


class WatchTableAndWatchpointEngine:
    """
    Manages persistent Cheat-Engine-style Watch Table entries (with 60 FPS freeze locks)
    and Frame-Boundary Memory Write Watchpoints inside EmulatorWorker.
    """

    def __init__(self, worker):
        self.worker = worker
        self.watches = []      # [{label, address, dtype, length, freeze, freeze_hex}]
        self.watchpoints = []  # [{label, address, width, condition, target_val, enabled, last_val}]

    def get_watches_path(self) -> str | None:
        rom_path = getattr(self.worker, "rom_path", None)
        if not rom_path:
            return None
        base_name = getattr(self.worker, "save_base_name", None) or os.path.splitext(os.path.basename(rom_path))[0]
        cht_dir = self.worker.config.get("paths", "cheats") or "cheats"
        os.makedirs(cht_dir, exist_ok=True)
        return os.path.join(cht_dir, f"{base_name}.watches.json")

    def load_default_watches(self):
        self.watches.clear()
        path = self.get_watches_path()
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.watches = data
            except Exception as e:
                print(f"[WATCHES] Load error: {e}")

    def save_default_watches(self):
        path = self.get_watches_path()
        if not path:
            return
        if not self.watches and not os.path.exists(path):
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.watches, f, indent=2)
        except Exception as e:
            print(f"[WATCHES] Save error: {e}")

    # =========================================================================
    # DIRECT UNLOCKED MEMORY HELPERS (CALLED INSIDE WORKER MUTEX)
    # =========================================================================
    def _read_raw_unlocked(self, addr: int, width: int) -> bytes | None:
        _, region_id, offset = resolve_gba_address(addr)
        if region_id is None or not self.worker.emu:
            return None
        try:
            mem = self.worker.emu.get_memory(region_id)
            if not mem or not hasattr(mem, "data"):
                return None
            if offset < 0 or offset + width > len(mem.data):
                return None
            return bytes(mem.data[offset:offset + width])
        except Exception:
            return None

    def _write_raw_unlocked(self, addr: int, payload: bytes):
        _, region_id, offset = resolve_gba_address(addr)
        if region_id is None or not self.worker.emu or not payload:
            return
        try:
            mem = self.worker.emu.get_memory(region_id)
            if not mem:
                return
            if hasattr(mem, "data"):
                if offset < 0 or offset + len(payload) > len(mem.data):
                    return
                mem.data[offset:offset + len(payload)] = np.frombuffer(payload, dtype=np.uint8)
            elif hasattr(mem, "write_bytes"):
                mem.write_bytes(offset, payload)
        except Exception:
            pass

    def apply_freezes_unlocked(self):
        for w in self.watches:
            if not w.get("freeze", False):
                continue
            hex_s = w.get("freeze_hex", "")
            if not hex_s:
                continue
            try:
                payload = bytes.fromhex(hex_s)
                self._write_raw_unlocked(int(w["address"]), payload)
            except Exception:
                continue

    def check_watchpoints_unlocked(self, frame_num: int) -> dict | None:
        for wp in self.watchpoints:
            if not wp.get("enabled", True):
                continue
            addr = int(wp["address"])
            width = int(wp.get("width", 2))
            raw = self._read_raw_unlocked(addr, width)
            if raw is None:
                continue
            cur_val = int.from_bytes(raw, "little")
            last_val = wp.get("last_val")
            wp["last_val"] = cur_val

            if last_val is None or cur_val == last_val:
                continue

            cond = wp.get("condition", "any_write")
            tgt = int(wp.get("target_val", 0))
            triggered = False
            if cond == "any_write":
                triggered = True
            elif cond == "equals":
                triggered = (cur_val == tgt)
            elif cond == "less":
                triggered = (cur_val < tgt)
            elif cond == "greater":
                triggered = (cur_val > tgt)

            if triggered:
                return {
                    "address": addr,
                    "width": width,
                    "old_val": last_val,
                    "new_val": cur_val,
                    "frame_num": frame_num,
                    "label": wp.get("label", f"WP 0x{addr:08X}"),
                }
        return None

# cheats.py
import os
import re
import json
import ctypes
import urllib.request
import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox, QHeaderView,
    QComboBox, QDialog, QListWidget, QLineEdit, QTextEdit, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QMutexLocker, QThread, pyqtSignal

# Default TEA Encryption Seeds (GBATEK / mGBA)
GSA_V1_SEEDS = (0x09F4FBBD, 0x9681884A, 0x352027E9, 0xF3DEE5A7)
PAR_V3_SEEDS = (0x7AA9648F, 0x7FAE6994, 0xC0EFAAD5, 0x42712C57)

VALID_CHEAT_TYPES = ["Auto", "GameShark", "Action Replay MAX", "CodeBreaker", "Raw"]


def tea_decrypt(op1: int, op2: int, seeds: tuple[int, int, int, int]) -> tuple[int, int]:
    """32-round TEA decryption used by GameShark Advance v1/v2 and Action Replay v3."""
    op1 &= 0xFFFFFFFF
    op2 &= 0xFFFFFFFF
    s0, s1, s2, s3 = seeds
    total = 0xC6EF3720
    delta = 0x9E3779B9

    for _ in range(32):
        op2 = (op2 - ((((op1 << 4) + s2) ^ (op1 + total) ^ ((op1 >> 5) + s3)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        op1 = (op1 - ((((op2 << 4) + s0) ^ (op2 + total) ^ ((op2 >> 5) + s1)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        total = (total - delta) & 0xFFFFFFFF

    return op1, op2


def _parse_line_tokens(raw_line: str) -> str:
    """Strips comments and normalizes separators while preserving colon raw format."""
    line = raw_line.split("#", 1)[0].split("//", 1)[0].strip()
    return line


def validate_cheat_code(code: str, cheat_type: str = "Auto") -> tuple[bool, str, str, list[str]]:
    """
    Validates a multi-line GBA cheat code.
    Returns: (is_valid, detected_or_effective_type, normalized_code, messages)
    """
    if not code or not code.strip():
        return False, cheat_type, "", ["Cheat code cannot be empty."]

    normalized_lines = []
    detected_formats = set()
    errors = []

    raw_lines = code.replace("\r", "").replace("+", "\n").split("\n")
    for idx, raw_line in enumerate(raw_lines, start=1):
        line = _parse_line_tokens(raw_line)
        if not line:
            continue

        # 1. Colon Raw Syntax: AAAAAAAA:VV / AAAAAAAA:VVVV / AAAAAAAA:VVVVVVVV
        m_colon = re.fullmatch(r"([0-9a-fA-F]{8}):([0-9a-fA-F]{2}|[0-9a-fA-F]{4}|[0-9a-fA-F]{8})", line)
        if m_colon:
            addr_hex, val_hex = m_colon.group(1).upper(), m_colon.group(2).upper()
            normalized_lines.append(f"{addr_hex}:{val_hex}")
            detected_formats.add("raw_colon")
            continue

        # 2. Space / Dash / Continuous Hex Formats
        clean_hex = re.sub(r"[^0-9a-fA-F]", "", line).upper()
        if not clean_hex or len(clean_hex) != len(re.sub(r"[\s\-:]", "", line)):
            errors.append(f"Line {idx}: Contains non-hex characters ('{line}').")
            continue

        if len(clean_hex) == 16:
            op1_hex, op2_hex = clean_hex[:8], clean_hex[8:]
            normalized_lines.append(f"{op1_hex} {op2_hex}")
            detected_formats.add("hex16")
        elif len(clean_hex) == 12:
            op1_hex, op2_hex = clean_hex[:8], clean_hex[8:]
            normalized_lines.append(f"{op1_hex} {op2_hex}")
            detected_formats.add("hex12")
        else:
            errors.append(
                f"Line {idx}: Expected 16 hex chars (GS/AR/Raw), 12 hex chars (CodeBreaker), "
                f"or ADDR:VAL raw syntax (got {len(clean_hex)} hex chars)."
            )

    if errors:
        return False, cheat_type, code.strip(), errors

    if not normalized_lines:
        return False, cheat_type, "", ["No valid cheat code lines found."]

    if "raw_colon" in detected_formats and len(detected_formats) > 1:
        return False, cheat_type, code.strip(), ["Cannot mix ADDR:VAL raw lines with 12/16-char lines in one entry."]

    if "hex12" in detected_formats and "hex16" in detected_formats:
        return False, cheat_type, code.strip(), ["Cannot mix 12-char CodeBreaker and 16-char GS/AR lines in one entry."]

    effective_type = cheat_type if cheat_type in VALID_CHEAT_TYPES else "Auto"
    if effective_type == "Auto":
        if "raw_colon" in detected_formats:
            effective_type = "Raw"
        elif "hex12" in detected_formats:
            effective_type = "CodeBreaker"
        else:
            effective_type = "Auto"

    if effective_type == "CodeBreaker" and "hex16" in detected_formats:
        return False, effective_type, "\n".join(normalized_lines), [
            "CodeBreaker codes must be 12 hex digits per line (XXXXXXXX YYYY)."
        ]
    if effective_type in ("GameShark", "Action Replay MAX") and "hex12" in detected_formats:
        return False, effective_type, "\n".join(normalized_lines), [
            f"{effective_type} codes must be 16 hex digits per line (XXXXXXXX YYYYYYYY)."
        ]

    notes = []
    if effective_type == "Action Replay MAX":
        preview_lines = []
        for ln in normalized_lines:
            p1, p2 = [int(x, 16) for x in ln.split()]
            d1, d2 = tea_decrypt(p1, p2, PAR_V3_SEEDS)
            preview_lines.append(f"{d1:08X} {d2:08X}")
        notes.append("ARv3 Decrypted Preview:\n" + "\n".join(preview_lines))
    elif effective_type == "GameShark":
        preview_lines = []
        for ln in normalized_lines:
            p1, p2 = [int(x, 16) for x in ln.split()]
            d1, d2 = tea_decrypt(p1, p2, GSA_V1_SEEDS)
            preview_lines.append(f"{d1:08X} {d2:08X}")
        notes.append("GSv1/v2 Decrypted Preview:\n" + "\n".join(preview_lines))
    elif effective_type == "CodeBreaker":
        notes.append("Validated 12-digit CodeBreaker / GameShark SP format.")
    elif effective_type == "Raw":
        notes.append("Validated Raw RAM write format.")
    else:
        notes.append("Validated 16-digit GameShark / Auto format.")

    return True, effective_type, "\n".join(normalized_lines), notes


class CheatDownloader(QThread):
    """Background thread to fetch cheat list from public Libretro DB without freezing UI."""
    results_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def run(self):
        try:
            url = "https://api.github.com/repos/libretro/libretro-database/contents/cht/Nintendo%20-%20Game%20Boy%20Advance"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                games = [
                    {"name": item["name"].replace(".cht", ""), "url": item["download_url"]}
                    for item in data if item["name"].endswith(".cht")
                ]
                self.results_ready.emit(games)
        except Exception as e:
            self.error_occurred.emit(str(e))


class CheatEngine:
    def __init__(self, worker):
        self.worker = worker
        self.cheats = []
        self._needs_core_sync = False

    def mark_dirty(self):
        self._needs_core_sync = True

    def get_default_cht_path(self) -> str | None:
        rom_path = getattr(self.worker, "rom_path", None)
        if not rom_path:
            return None
        base_name = getattr(self.worker, "save_base_name", None) or os.path.splitext(os.path.basename(rom_path))[0]
        cht_dir = self.worker.config.get("paths", "cheats") or "cheats"
        os.makedirs(cht_dir, exist_ok=True)
        return os.path.join(cht_dir, f"{base_name}.cht")

    def load_default_cheats(self) -> int:
        self.cheats.clear()
        cht_path = self.get_default_cht_path()
        if cht_path and os.path.exists(cht_path):
            if self.load_cht_file(cht_path):
                print(f"[CHEATS] Auto-loaded {len(self.cheats)} cheat(s) from {cht_path}")
                return len(self.cheats)
        self._needs_core_sync = True
        return 0

    def save_default_cheats(self, only_if_exists_or_nonempty: bool = False) -> bool:
        cht_path = self.get_default_cht_path()
        if not cht_path:
            return False
        if only_if_exists_or_nonempty and not self.cheats and not os.path.exists(cht_path):
            return False
        return self.save_cht_file(cht_path)

    # =========================================================================
    # MEMORY ACCESS HELPERS (EWRAM, IWRAM, PRAM, VRAM, OAM, SRAM)
    # =========================================================================
    def _resolve_gba_addr(self, addr: int) -> tuple[int | None, int]:
        if 0x02000000 <= addr <= 0x0203FFFF:
            return 2, addr - 0x02000000
        if 0x03000000 <= addr <= 0x03007FFF:
            return 2, (256 * 1024) + (addr - 0x03000000)
        if 0x05000000 <= addr <= 0x050003FF:
            return 4, addr - 0x05000000
        if 0x06000000 <= addr <= 0x06017FFF:
            return 3, addr - 0x06000000
        if 0x07000000 <= addr <= 0x070003FF:
            return 5, addr - 0x07000000
        if 0x0E000000 <= addr <= 0x0E00FFFF:
            return 0, addr - 0x0E000000
        return None, 0

    def _read_mem(self, addr: int, width: int = 2) -> int | None:
        region_id, offset = self._resolve_gba_addr(addr)
        if region_id is None or not self.worker.emu:
            return None
        try:
            mem = self.worker.emu.get_memory(region_id)
            if not mem or not hasattr(mem, "data"):
                return None
            if offset < 0 or offset + width > len(mem.data):
                return None
            raw = bytes(mem.data[offset:offset + width])
            return int.from_bytes(raw, "little")
        except Exception:
            return None

    def _write_mem_addr(self, addr: int, val: int, width: int = 2):
        region_id, offset = self._resolve_gba_addr(addr)
        if region_id is None or not self.worker.emu:
            return
        try:
            mem = self.worker.emu.get_memory(region_id)
            if not mem:
                return
            payload = (val & ((1 << (width * 8)) - 1)).to_bytes(width, "little")
            if hasattr(mem, "data"):
                if offset < 0 or offset + width > len(mem.data):
                    return
                mem.data[offset:offset + width] = np.frombuffer(payload, dtype=np.uint8)
            elif hasattr(mem, "write_bytes"):
                mem.write_bytes(offset, payload)
        except Exception:
            pass

    # =========================================================================
    # NATIVE LIBRETRO CORE BRIDGE (GS v1/v2 + CodeBreaker + Master/ROM Codes)
    # =========================================================================
    def _has_native_cheat_api(self) -> bool:
        dll = getattr(self.worker, "_core_dll", None)
        return bool(dll and hasattr(dll, "retro_cheat_reset") and hasattr(dll, "retro_cheat_set"))

    def _sync_core_cheats_unlocked(self):
        self._needs_core_sync = False
        if not self.worker.emu or not self._has_native_cheat_api():
            return

        dll = self.worker._core_dll
        try:
            dll.retro_cheat_reset.argtypes = []
            dll.retro_cheat_reset.restype = None
            dll.retro_cheat_set.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_char_p]
            dll.retro_cheat_set.restype = None

            dll.retro_cheat_reset()

            idx = 0
            for cheat in self.cheats:
                if not cheat.get("enabled", False):
                    continue
                ctype = cheat.get("type", "Auto")
                if ctype in ("Raw", "Action Replay MAX"):
                    continue

                ok, eff_type, norm_code, _ = validate_cheat_code(cheat.get("code", ""), ctype)
                if not ok or eff_type in ("Raw", "Action Replay MAX"):
                    continue

                joined = "+".join(norm_code.split("\n"))
                dll.retro_cheat_set(idx, True, joined.encode("ascii", errors="ignore"))
                idx += 1
        except Exception as e:
            print(f"[CHEATS WARNING] Native retro_cheat_set failed: {e}")

    # =========================================================================
    # PYTHON EXECUTORS (Raw + Action Replay MAX / PARv3 + Fallback)
    # =========================================================================
    def apply_cheats(self):
        """Synchronizes native core cheats and applies Python-handled codes every frame."""
        with QMutexLocker(self.worker.mutex):
            if not self.worker.emu:
                return

            if self._needs_core_sync:
                self._sync_core_cheats_unlocked()

            if not self.cheats:
                return

            has_native = self._has_native_cheat_api()
            for cheat in self.cheats:
                if not cheat.get("enabled", False):
                    continue

                ctype = cheat.get("type", "Auto")
                ok, eff_type, norm_code, _ = validate_cheat_code(cheat.get("code", ""), ctype)
                if not ok:
                    continue

                if eff_type == "Raw":
                    self._apply_raw_lines(norm_code.split("\n"))
                elif eff_type == "Action Replay MAX":
                    self._apply_parv3_lines(norm_code.split("\n"))
                elif not has_native:
                    if eff_type == "GameShark":
                        self._apply_gsa_v1_lines(norm_code.split("\n"))
                    elif eff_type == "CodeBreaker":
                        self._apply_cb_raw_lines(norm_code.split("\n"))

    def _apply_raw_lines(self, lines: list[str]):
        skip = 0
        for ln in lines:
            if skip > 0:
                skip -= 1
                continue

            if ":" in ln:
                addr_s, val_s = ln.split(":", 1)
                addr = int(addr_s, 16)
                val = int(val_s, 16)
                width = max(1, len(val_s) // 2)
                self._write_mem_addr(addr, val, width=width)
                continue

            parts = ln.split()
            if len(parts) != 2:
                continue
            op1 = int(parts[0], 16)
            op2 = int(parts[1], 16)

            top = (op1 >> 28) & 0xF
            addr = op1 & 0x0FFFFFFF

            if top == 0xD:
                cur = self._read_mem(addr, width=2)
                if cur != (op2 & 0xFFFF):
                    skip = 1
            elif top == 0xE:
                cnt = (op1 >> 16) & 0xFF
                cmp_val = op1 & 0xFFFF
                cur = self._read_mem(op2 & 0x0FFFFFFF, width=2)
                if cur != cmp_val:
                    skip = cnt
            elif top == 0x0:
                width = 1 if op2 <= 0xFF else (2 if op2 <= 0xFFFF else 4)
                self._write_mem_addr(addr, op2, width=width)
            elif top == 0x1:
                self._write_mem_addr(addr, op2 & 0xFFFF, width=2)
            elif top == 0x2:
                self._write_mem_addr(addr, op2, width=4)

    def _apply_parv3_lines(self, lines: list[str]):
        """Decrypts and executes Pro Action Replay v3 / Action Replay MAX codes."""
        decrypted = []
        for ln in lines:
            parts = ln.split()
            if len(parts) != 2:
                continue
            op1, op2 = tea_decrypt(int(parts[0], 16), int(parts[1], 16), PAR_V3_SEEDS)
            if op2 == 0x001DC0DE or op1 == 0xDEADFACE or (op1 >> 24) == 0xC4:
                continue
            decrypted.append((op1, op2))

        i = 0
        while i < len(decrypted):
            op1, op2 = decrypted[i]
            sub = (op1 >> 30) & 0x3
            typ = (op1 >> 27) & 0x7
            siz = (op1 >> 25) & 0x3
            xtr = (op1 >> 24) & 0x1
            adr = op1 & 0x003FFFFF
            gba_addr = 0x02000000 + (adr & 0x000FFFFF) + ((adr & 0x00300000) << 4) + (xtr << 26)

            width = 1 if siz == 0 else (2 if siz == 1 else 4)

            if typ == 0:
                if sub == 0:
                    if siz == 0:
                        count = min((op2 >> 8) + 1, 2048)
                        val = op2 & 0xFF
                        for k in range(count):
                            self._write_mem_addr(gba_addr + k, val, width=1)
                    elif siz == 1:
                        count = min((op2 >> 16) + 1, 1024)
                        val = op2 & 0xFFFF
                        for k in range(count):
                            self._write_mem_addr(gba_addr + (k * 2), val, width=2)
                    elif siz == 2:
                        self._write_mem_addr(gba_addr, op2, width=4)
                elif sub == 1:  # Indirect pointer write
                    ptr = self._read_mem(gba_addr, width=4)
                    if ptr is not None:
                        if siz == 0:
                            self._write_mem_addr(ptr + (op2 >> 8), op2 & 0xFF, width=1)
                        elif siz == 1:
                            self._write_mem_addr(ptr + ((op2 >> 16) * 2), op2 & 0xFFFF, width=2)
                        elif siz == 2:
                            self._write_mem_addr(ptr, op2, width=4)
                i += 1
                continue

            # Conditional Codes (typ 1..7)
            if siz == 3:  # Always skip/stop
                if sub == 0:
                    i += 2
                elif sub == 1:
                    i += 3
                else:
                    break
                continue

            cur = self._read_mem(gba_addr, width=width)
            if cur is None:
                i += 1
                continue

            bits = width * 8
            mask = (1 << bits) - 1
            rhs = op2 & mask

            def _to_signed(v: int) -> int:
                sign = 1 << (bits - 1)
                return v - (1 << bits) if (v & sign) else v

            cond = False
            if typ == 1:
                cond = (cur == rhs)
            elif typ == 2:
                cond = (cur != rhs)
            elif typ == 3:
                cond = (_to_signed(cur) < _to_signed(rhs))
            elif typ == 4:
                cond = (_to_signed(cur) > _to_signed(rhs))
            elif typ == 5:
                cond = (cur < rhs)
            elif typ == 6:
                cond = (cur > rhs)
            elif typ == 7:
                cond = bool(cur & rhs)

            if cond:
                i += 1
            else:
                if sub == 0:
                    i += 2
                elif sub == 1:
                    i += 3
                else:
                    break

    def _apply_gsa_v1_lines(self, lines: list[str]):
        """Fallback Python executor for GameShark v1/v2 default-seed codes."""
        raw_lines = []
        for ln in lines:
            parts = ln.split()
            if len(parts) != 2:
                continue
            d1, d2 = tea_decrypt(int(parts[0], 16), int(parts[1], 16), GSA_V1_SEEDS)
            if d2 == 0x001DC0DE or d1 == 0xDEADFACE or (d1 >> 28) == 0xF:
                continue
            raw_lines.append(f"{d1:08X} {d2:08X}")
        self._apply_raw_lines(raw_lines)

    def _apply_cb_raw_lines(self, lines: list[str]):
        """Fallback Python executor for unencrypted CodeBreaker lines."""
        skip = 0
        for ln in lines:
            if skip > 0:
                skip -= 1
                continue
            parts = ln.split()
            if len(parts) != 2:
                continue
            op1 = int(parts[0], 16)
            op2 = int(parts[1], 16)
            top = (op1 >> 28) & 0xF
            addr = op1 & 0x0FFFFFFF

            if top == 0x3:
                self._write_mem_addr(addr, op2 & 0xFF, width=1)
            elif top == 0x8:
                self._write_mem_addr(addr, op2 & 0xFFFF, width=2)
            elif top == 0x7:
                cur = self._read_mem(addr, width=2)
                if cur != (op2 & 0xFFFF):
                    skip = 1

    # =========================================================================
    # LIBRETRO .CHT FILE PERSISTENCE
    # =========================================================================
    def load_cht_file(self, filepath: str) -> bool:
        self.cheats.clear()
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            cheat_dict = {}
            for line in lines:
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip().strip('"')

                if key.startswith("cheat"):
                    idx_match = re.search(r"cheat(\d+)_(\w+)", key)
                    if idx_match:
                        idx = int(idx_match.group(1))
                        prop = idx_match.group(2)

                        if idx not in cheat_dict:
                            cheat_dict[idx] = {
                                "desc": "Unknown",
                                "code": "",
                                "type": "Auto",
                                "enabled": False,
                            }

                        if prop == "desc":
                            cheat_dict[idx]["desc"] = val
                        elif prop == "code":
                            cheat_dict[idx]["code"] = val.replace("+", "\n")
                        elif prop == "enable":
                            cheat_dict[idx]["enabled"] = (val.lower() == "true")
                        elif prop == "type" and val in VALID_CHEAT_TYPES:
                            cheat_dict[idx]["type"] = val

            for i in sorted(cheat_dict.keys()):
                entry = cheat_dict[i]
                if entry["type"] == "Auto":
                    _, eff_type, norm_code, _ = validate_cheat_code(entry["code"], "Auto")
                    if norm_code:
                        entry["code"] = norm_code
                    if eff_type == "Raw":
                        entry["type"] = "Raw"
                    elif eff_type == "CodeBreaker":
                        entry["type"] = "CodeBreaker"
                self.cheats.append(entry)

            self.mark_dirty()
            return True
        except Exception as e:
            print(f"Cheat load error: {e}")
            return False

    def save_cht_file(self, filepath: str) -> bool:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"cheats = {len(self.cheats)}\n\n")
                for i, cheat in enumerate(self.cheats):
                    desc = cheat.get("desc", "Cheat").replace('"', "'")
                    code_joined = cheat.get("code", "").replace("\r", "").replace("\n", "+")
                    enabled_str = "true" if cheat.get("enabled", False) else "false"
                    ctype = cheat.get("type", "Auto")
                    f.write(f'cheat{i}_desc = "{desc}"\n')
                    f.write(f'cheat{i}_code = "{code_joined}"\n')
                    f.write(f"cheat{i}_enable = {enabled_str}\n")
                    f.write(f'cheat{i}_type = "{ctype}"\n\n')
            return True
        except Exception as e:
            print(f"Cheat save error: {e}")
            return False


class CheatEditorDialog(QDialog):
    """Dialog for adding/validating GBA cheat codes with live decryption preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add & Validate Cheat Code")
        self.resize(480, 360)
        self.validated_data = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Description:"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("e.g., Infinite HP, Max Money")
        layout.addWidget(self.desc_edit)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Code Format:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(VALID_CHEAT_TYPES)
        type_row.addWidget(self.type_combo)
        type_row.addStretch()
        layout.addLayout(type_row)

        layout.addWidget(QLabel("Cheat Code (16-hex GS/AR, 12-hex CodeBreaker, or ADDR:VAL Raw):"))
        self.code_edit = QTextEdit()
        self.code_edit.setPlaceholderText("Example:\n02001234:03E7\nor\nCD93194F 089CE0B4")
        self.code_edit.setStyleSheet("font-family: monospace;")
        layout.addWidget(self.code_edit)

        btn_validate = QPushButton("🔍 Validate & Decode Preview")
        btn_validate.clicked.connect(self.run_validation)
        layout.addWidget(btn_validate)

        self.status_lbl = QLabel("Enter code above and click Validate or OK.")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #90A4AE;")
        layout.addWidget(self.status_lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def run_validation(self) -> bool:
        ok, eff_type, norm_code, notes = validate_cheat_code(
            self.code_edit.toPlainText(),
            self.type_combo.currentText(),
        )
        if ok:
            self.status_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #81C784;")
            self.status_lbl.setText("\n".join(notes))
            if self.type_combo.currentText() == "Auto" and eff_type != "Auto":
                idx = self.type_combo.findText(eff_type)
                if idx >= 0:
                    self.type_combo.setCurrentIndex(idx)
            self.validated_data = {
                "desc": self.desc_edit.text().strip() or "New Cheat",
                "code": norm_code,
                "type": eff_type,
                "enabled": True,
            }
            return True
        else:
            self.status_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #EF5350;")
            self.status_lbl.setText("\n".join(notes))
            self.validated_data = None
            return False

    def on_accept(self):
        if self.run_validation():
            self.accept()
        else:
            QMessageBox.warning(self, "Invalid Cheat Code", self.status_lbl.text())


class CheatManagerWindow(QMainWindow):
    def __init__(self, engine: CheatEngine, config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.setWindowTitle("Cheats Manager")
        self.resize(780, 480)
        self._init_ui()
        self.refresh_table()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        btn_add = QPushButton("Add New Cheat...")
        btn_add.clicked.connect(self.add_cheat)
        btn_del = QPushButton("Remove Selected")
        btn_del.clicked.connect(self.remove_cheat)
        btn_load = QPushButton("Import .cht...")
        btn_load.clicked.connect(self.load_cheats)
        btn_save = QPushButton("Export .cht...")
        btn_save.clicked.connect(self.save_cheats)
        btn_db = QPushButton("🌐 Download from Database")
        btn_db.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        btn_db.clicked.connect(self.open_db_downloader)

        toolbar.addWidget(btn_add)
        toolbar.addWidget(btn_del)
        toolbar.addWidget(btn_load)
        toolbar.addWidget(btn_save)
        toolbar.addWidget(btn_db)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Enabled", "Description", "Type", "Code"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.on_item_changed)
        layout.addWidget(self.table)

        cht_path = self.engine.get_default_cht_path() or "No ROM loaded"
        self.lbl_path = QLabel(f"Auto-Save File: {cht_path}")
        self.lbl_path.setStyleSheet("color: #90A4AE; font-size: 11px;")
        layout.addWidget(self.lbl_path)

    def refresh_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.engine.cheats))
        for i, cheat in enumerate(self.engine.cheats):
            item_cb = QTableWidgetItem()
            item_cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item_cb.setCheckState(Qt.CheckState.Checked if cheat.get("enabled") else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, item_cb)

            self.table.setItem(i, 1, QTableWidgetItem(cheat.get("desc", "")))

            combo = QComboBox()
            combo.addItems(VALID_CHEAT_TYPES)
            combo.setCurrentText(cheat.get("type", "Auto"))
            combo.currentTextChanged.connect(lambda text, row=i: self.update_type(row, text))
            self.table.setCellWidget(i, 2, combo)

            self.table.setItem(i, 3, QTableWidgetItem(cheat.get("code", "").replace("\n", " + ")))
        self.table.blockSignals(False)

    def _commit_changes(self):
        self.engine.mark_dirty()
        self.engine.save_default_cheats(only_if_exists_or_nonempty=True)

    def update_type(self, row: int, text: str):
        if row < len(self.engine.cheats):
            ok, eff_type, _, errs = validate_cheat_code(self.engine.cheats[row].get("code", ""), text)
            if not ok:
                QMessageBox.warning(self, "Cheat Format Mismatch", "\n".join(errs))
                self.refresh_table()
                return
            self.engine.cheats[row]["type"] = eff_type
            self._commit_changes()

    def on_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        col = item.column()
        if row >= len(self.engine.cheats):
            return

        if col == 0:
            self.engine.cheats[row]["enabled"] = (item.checkState() == Qt.CheckState.Checked)
            self._commit_changes()
        elif col == 1:
            self.engine.cheats[row]["desc"] = item.text().strip()
            self._commit_changes()
        elif col == 3:
            ctype = self.engine.cheats[row].get("type", "Auto")
            ok, eff_type, norm_code, errs = validate_cheat_code(item.text(), ctype)
            if not ok:
                QMessageBox.warning(self, "Invalid Cheat Code", "\n".join(errs))
                self.refresh_table()
                return
            self.engine.cheats[row]["code"] = norm_code
            self.engine.cheats[row]["type"] = eff_type
            self.refresh_table()
            self._commit_changes()

    def add_cheat(self):
        dlg = CheatEditorDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.validated_data:
            self.engine.cheats.append(dlg.validated_data)
            self.refresh_table()
            self._commit_changes()

    def remove_cheat(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.engine.cheats[row]
            self.refresh_table()
            self._commit_changes()

    def load_cheats(self):
        cht_dir = self.config.get("paths", "cheats") or "cheats"
        filepath, _ = QFileDialog.getOpenFileName(self, "Load Libretro Cheats", cht_dir, "Cheat Files (*.cht)")
        if filepath and self.engine.load_cht_file(filepath):
            self.refresh_table()
            self._commit_changes()

    def save_cheats(self):
        cht_dir = self.config.get("paths", "cheats") or "cheats"
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Libretro Cheats", cht_dir, "Cheat Files (*.cht)")
        if filepath:
            self.engine.save_cht_file(filepath)
            QMessageBox.information(self, "Saved", "Cheats saved successfully.")

    def open_db_downloader(self):
        self.db_dialog = QDialog(self)
        self.db_dialog.setWindowTitle("Public Cheat Database")
        self.db_dialog.resize(420, 520)
        layout = QVBoxLayout(self.db_dialog)

        self.db_status_lbl = QLabel("Fetching game list from Libretro GitHub... Please wait.")
        layout.addWidget(self.db_status_lbl)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        self.dl_btn = QPushButton("Download Selected Cheats")
        self.dl_btn.setEnabled(False)
        layout.addWidget(self.dl_btn)

        self.fetcher = CheatDownloader()
        self.fetcher.results_ready.connect(self.populate_db_list)
        self.fetcher.error_occurred.connect(
            lambda e: QMessageBox.warning(self, "Error", f"Failed to fetch: {e}")
        )
        self.fetcher.start()

        self.db_dialog.exec()

    def populate_db_list(self, games):
        self.db_games = games
        self.db_status_lbl.setText(f"Found {len(games)} GBA cheat files:")
        for game in games:
            self.list_widget.addItem(game["name"])

        self.dl_btn.setEnabled(True)
        self.dl_btn.clicked.connect(self.download_selected_cheat)

    def download_selected_cheat(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return

        game = self.db_games[row]
        try:
            req = urllib.request.Request(game["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode("utf-8", errors="ignore")

            cht_dir = self.config.get("paths", "cheats") or "cheats"
            os.makedirs(cht_dir, exist_ok=True)
            save_path = os.path.join(cht_dir, f"{game['name']}.cht")

            with open(save_path, "w", encoding="utf-8") as f:
                f.write(content)

            if self.engine.load_cht_file(save_path):
                self.refresh_table()
                self._commit_changes()
                QMessageBox.information(
                    self, "Success",
                    f"Downloaded and loaded {len(self.engine.cheats)} cheats for {game['name']}!"
                )
                self.db_dialog.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Download failed: {e}")
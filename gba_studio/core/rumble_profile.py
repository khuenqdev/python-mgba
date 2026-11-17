# rumble_profile.py
import os
import re
import json
import time
import struct
import datetime
import numpy as np

from ..tools.sappy_extractor import (
    scan_sappy_song_tables,
    read_voicegroup,
    scan_universal_rom_samples,
)

PROFILE_VERSION = 2

RUMBLE_SFX_CATEGORIES = {
    "hit_heavy": {"label": "Heavy Hit / Explosion", "low": 1.00, "high": 0.95, "duration_ms": 420},
    "hit_light": {"label": "Light Hit / Punch / Slash", "low": 0.95, "high": 1.00, "duration_ms": 340},
    "magic":     {"label": "Magic / Special Skill", "low": 0.90, "high": 1.00, "duration_ms": 400},
    "item":      {"label": "Item / Power-Up Pickup", "low": 0.75, "high": 0.95, "duration_ms": 320},
    "ignore":    {"label": "Ignore (Music / Voice / Menu)", "low": 0.00, "high": 0.00, "duration_ms": 0},
}

RAM_EVENT_TRIGGERS = [
    ("decreased", "Value Decreased (HP / Shield)"),
    ("zero_to_nonzero", "0 -> Non-Zero (Hitstun / Shake / Flash Timer)"),
    ("increased", "Value Increased (Damage Taken / Combo)"),
    ("equals", "Equals Control Code (Value == Target)"),
    ("bit_set", "Bitflag Set ((Value & Mask) != 0)"),
    ("changed", "Any Value Change (SFX ID / Action State)"),
]


def _resolve_sysram_offset(addr: int) -> int | None:
    addr = int(addr) & 0xFFFFFFFF
    if 0x02000000 <= addr <= 0x0203FFFF:
        return addr - 0x02000000
    if 0x03000000 <= addr <= 0x03007FFF:
        return (256 * 1024) + (addr - 0x03000000)
    return None


def analyze_pcm_sfx_features(pcm_i8: np.ndarray, sample_rate: int, loop: bool = False) -> dict:
    """
    Classifies an extracted GBA PCM sample into a suggested rumble category based on
    duration, attack sharpness, low/mid/high spectral balance, and broadband flatness.
    """
    n = len(pcm_i8)
    sr = max(4000, int(sample_rate or 13379))
    dur_sec = float(n) / float(sr)

    if loop or dur_sec < 0.055 or n < 256:
        return {"category": "ignore", "dur_sec": round(dur_sec, 3), "low_ratio": 0.0, "noise_ratio": 0.0}

    x = pcm_i8.astype(np.float32) / 128.0
    rms = float(np.sqrt(np.mean(x * x)))
    if rms < 0.05:
        return {"category": "ignore", "dur_sec": round(dur_sec, 3), "low_ratio": 0.0, "noise_ratio": 0.0}

    head_len = max(32, min(n // 4, int(sr * 0.08)))
    head_peak = float(np.max(np.abs(x[:head_len])))
    crest = float(np.max(np.abs(x))) / (rms + 1e-6)

    seg = x[:min(n, 4096)]
    win = np.hanning(len(seg)).astype(np.float32)
    mag = np.abs(np.fft.rfft(seg * win))
    freqs = np.fft.rfftfreq(len(seg), d=1.0 / float(sr))

    total_e = float(np.sum(mag * mag)) + 1e-9
    low_e = float(np.sum(mag[(freqs >= 35) & (freqs < 380)] ** 2))
    mid_e = float(np.sum(mag[(freqs >= 380) & (freqs < 1600)] ** 2))
    high_e = float(np.sum(mag[freqs >= 1600] ** 2))

    low_ratio = low_e / total_e
    mid_ratio = mid_e / total_e
    high_ratio = high_e / total_e
    noise_ratio = 1.0 - (float(np.max(mag) ** 2) / total_e)

    if dur_sec > 2.0:
        cat = "ignore"
    elif (low_ratio >= 0.28 and noise_ratio >= 0.72) or (dur_sec >= 0.25 and low_ratio >= 0.35 and head_peak >= 0.45):
        cat = "hit_heavy"
    elif dur_sec >= 0.32 and noise_ratio >= 0.80 and (mid_ratio + low_ratio) >= 0.45:
        cat = "magic"
    elif crest >= 2.6 and noise_ratio >= 0.70 and (low_ratio + mid_ratio) >= 0.35 and dur_sec <= 0.65:
        cat = "hit_light"
    elif high_ratio < 0.75 and mid_ratio >= 0.42 and noise_ratio < 0.72 and 0.12 <= dur_sec <= 0.90:
        cat = "item"
    elif high_ratio >= 0.65 and dur_sec < 0.14:
        cat = "ignore"
    elif noise_ratio >= 0.78 and head_peak >= 0.35:
        cat = "hit_light"
    else:
        cat = "ignore"

    return {
        "category": cat,
        "dur_sec": round(dur_sec, 3),
        "low_ratio": round(low_ratio, 3),
        "noise_ratio": round(noise_ratio, 3),
    }


def build_rumble_profile_from_rom(
    rom_path: str,
    game_code: str = "",
    game_title: str = "",
    existing_ram_events: list | None = None,
) -> dict:
    """
    Scans a GBA ROM for M4A SFX & verified PCM samples, classifies them,
    and constructs a v2 per-game rumble profile dictionary (preserving any RAM event rules).
    """
    with open(rom_path, "rb") as f:
        rom_data = f.read()

    m4a_bgm_sample_offs = set()
    m4a_sfx_sample_offs = set()
    try:
        tables = scan_sappy_song_tables(rom_data)
        if tables:
            vg_cache = {}
            for s in tables[0]["songs"]:
                vp = s["voicegroup_ptr"]
                if vp not in vg_cache:
                    vg_cache[vp] = read_voicegroup(rom_data, vp)
                target_set = m4a_bgm_sample_offs if s["player_group"] == 0 else m4a_sfx_sample_offs
                for inst in vg_cache[vp]:
                    smp = inst.get("sample")
                    if smp:
                        target_set.add(smp["offset"])
                    for sub in inst.get("sub_entries", []):
                        ssmp = sub.get("sample")
                        if ssmp:
                            target_set.add(ssmp["offset"])
    except Exception:
        pass

    raw_samples = scan_universal_rom_samples(rom_data)
    sfx_entries = []

    for idx, smp in enumerate(raw_samples):
        off = int(smp["offset"])
        length = int(smp["length"])
        sr = int(smp["sample_rate"])
        loop = bool(smp.get("loop", False))
        pcm = smp["pcm"]

        is_bgm_only = (off in m4a_bgm_sample_offs) and (off not in m4a_sfx_sample_offs)
        feats = analyze_pcm_sfx_features(pcm, sr, loop=loop or is_bgm_only)
        cat = feats["category"]
        preset = RUMBLE_SFX_CATEGORIES[cat]

        data_start = off + 16 if smp["kind"] == "M4A WaveData" else off
        sfx_entries.append({
            "id": f"sfx_{idx:03d}",
            "label": f"{smp['kind']} @ 0x{off:06X}",
            "kind": smp["kind"],
            "offset": off,
            "data_offset": data_start,
            "length": length,
            "sample_rate": sr,
            "duration_sec": feats["dur_sec"],
            "category": cat,
            "enabled": (cat != "ignore"),
            "low": preset["low"],
            "high": preset["high"],
            "duration_ms": preset["duration_ms"],
        })

    return {
        "profile_version": PROFILE_VERSION,
        "game_code": game_code or "UNKNOWN",
        "game_title": game_title or os.path.splitext(os.path.basename(rom_path))[0],
        "rom_file": os.path.basename(rom_path),
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "sfx_entries": sfx_entries,
        "ram_events": list(existing_ram_events or []),
    }


def get_rumble_profile_path(profiles_dir: str, profile_id: str) -> str:
    os.makedirs(profiles_dir, exist_ok=True)
    safe_id = re.sub(r"[^\w\-]", "_", str(profile_id or "DEFAULT")).strip("_") or "DEFAULT"
    return os.path.join(profiles_dir, f"{safe_id}.rumble.json")


class RumbleProfileStore:
    """
    Fast vectorized runtime lookup for per-game ROM SFX pointers AND
    GBA RAM Control-Code / Event Triggers (`ram_events`).
    """

    def __init__(self, config):
        self.config = config
        self.profile_path = ""
        self.entries = []
        self.ram_events = []
        self._active_entries = []
        self._active_ram_events = []
        self._starts_bus = np.empty(0, dtype=np.uint32)
        self._ends_bus = np.empty(0, dtype=np.uint32)
        self._min_bus = 0x08000000
        self._max_bus = 0x08000000
        self._prev_ptrs = {}
        self._prev_ram_vals = {}
        self._baseline_ready = False
        self._ram_baseline_ready = False
        self._cooldown_until = {}
        self._ram_cooldown_until = {}
        self._boot_suppress_until = 0.0

    def has_active_entries(self) -> bool:
        return bool(self._active_entries or self._active_ram_events)

    def has_active_ram_events(self) -> bool:
        return bool(self._active_ram_events)

    def reset_runtime_state(self):
        self._prev_ptrs.clear()
        self._prev_ram_vals.clear()
        self._baseline_ready = False
        self._ram_baseline_ready = False
        self._cooldown_until.clear()
        self._ram_cooldown_until.clear()
        self._boot_suppress_until = time.monotonic() + 0.6

    def load_for_game(self, profile_id: str, rom_path: str = "") -> bool:
        self.entries = []
        self.ram_events = []
        self._active_entries = []
        self._active_ram_events = []
        self._starts_bus = np.empty(0, dtype=np.uint32)
        self._ends_bus = np.empty(0, dtype=np.uint32)
        self.reset_runtime_state()

        if not profile_id:
            return False

        profiles_dir = self.config.get("paths", "profiles") or "profiles"
        path = get_rumble_profile_path(profiles_dir, profile_id)
        self.profile_path = path
        if not os.path.exists(path):
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if int(data.get("profile_version", 1)) < PROFILE_VERSION:
                if rom_path and os.path.exists(rom_path):
                    print(f"[RUMBLE PROFILE] Upgrading legacy v1 profile {os.path.basename(path)} to v{PROFILE_VERSION}...")
                    data = build_rumble_profile_from_rom(
                        rom_path,
                        game_code=data.get("game_code", profile_id),
                        game_title=data.get("game_title", ""),
                        existing_ram_events=data.get("ram_events", []),
                    )
                    self.save_profile(data, profile_id)
                    return self.has_active_entries()
                return False

            raw_entries = data.get("sfx_entries", [])
            raw_ram_events = data.get("ram_events", [])
            if isinstance(raw_entries, list):
                self.entries = raw_entries
            if isinstance(raw_ram_events, list):
                self.ram_events = raw_ram_events
            self._rebuild_index()
            return self.has_active_entries()
        except Exception as e:
            print(f"[RUMBLE PROFILE WARNING] Failed to load {path}: {e}")
        return False

    def save_profile(self, profile_data: dict, profile_id: str):
        profile_data["profile_version"] = PROFILE_VERSION
        if "ram_events" not in profile_data:
            profile_data["ram_events"] = list(self.ram_events)
        profiles_dir = self.config.get("paths", "profiles") or "profiles"
        path = get_rumble_profile_path(profiles_dir, profile_id)
        self.profile_path = path
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=2)
        os.replace(tmp, path)
        self.entries = profile_data.get("sfx_entries", [])
        self.ram_events = profile_data.get("ram_events", [])
        self._rebuild_index()

    def _rebuild_index(self):
        active = [
            e for e in self.entries
            if e.get("enabled", False) and e.get("category", "ignore") != "ignore"
            and (float(e.get("low", 0)) > 0.0 or float(e.get("high", 0)) > 0.0)
        ]
        active.sort(key=lambda e: int(e["offset"]))
        self._active_entries = active

        active_ram = []
        for ev in self.ram_events:
            if not ev.get("enabled", False):
                continue
            off = _resolve_sysram_offset(int(ev.get("address", 0)))
            if off is None:
                continue
            width = int(ev.get("width", 1))
            if width not in (1, 2, 4):
                width = 1
            active_ram.append({**ev, "_sys_off": off, "_width": width})
        self._active_ram_events = active_ram

        if not active:
            self._starts_bus = np.empty(0, dtype=np.uint32)
            self._ends_bus = np.empty(0, dtype=np.uint32)
            return

        starts = np.array([0x08000000 + int(e["offset"]) for e in active], dtype=np.uint32)
        ends = np.array(
            [0x08000000 + int(e["data_offset"]) + int(e["length"]) for e in active],
            dtype=np.uint32,
        )
        self._starts_bus = starts
        self._ends_bus = ends
        self._min_bus = int(starts[0])
        self._max_bus = int(ends.max())

    def detect_triggered_sfx(
        self,
        system_ram_u32: np.ndarray | None,
        has_audio_energy: bool = True,
    ) -> tuple[float, float, int] | None:
        if system_ram_u32 is None or len(self._active_entries) == 0:
            return None

        rom_words = system_ram_u32[(system_ram_u32 >= self._min_bus) & (system_ram_u32 < self._max_bus)]
        if len(rom_words) == 0:
            self._prev_ptrs.clear()
            self._baseline_ready = True
            return None

        idx = np.searchsorted(self._starts_bus, rom_words, side="right") - 1
        valid = (idx >= 0) & (rom_words < self._ends_bus[np.maximum(0, idx)])
        if not np.any(valid):
            self._prev_ptrs.clear()
            self._baseline_ready = True
            return None

        matched_idxs = idx[valid]
        matched_ptrs = rom_words[valid]

        seen_now = {}
        for e_idx_np, ptr_np in zip(matched_idxs, matched_ptrs):
            e_idx = int(e_idx_np)
            ptr = int(ptr_np)
            if e_idx not in seen_now or ptr < seen_now[e_idx]:
                seen_now[e_idx] = ptr

        now = time.monotonic()
        if now < self._boot_suppress_until or not self._baseline_ready:
            self._prev_ptrs = seen_now
            self._baseline_ready = True
            return None

        best_low = 0.0
        best_high = 0.0
        best_dur = 0

        for e_idx, ptr in seen_now.items():
            entry = self._active_entries[e_idx]
            start_bus = int(self._starts_bus[e_idx])
            prev_ptr = self._prev_ptrs.get(e_idx)
            is_m4a = (entry.get("kind") == "M4A WaveData")

            near_head = (ptr - start_bus) <= 512
            if prev_ptr is None:
                is_trigger = near_head and (is_m4a or has_audio_energy)
            else:
                delta = ptr - prev_ptr
                is_trigger = (16 <= delta <= 1024) or (is_m4a and delta < -64 and near_head)

            if is_trigger and now >= self._cooldown_until.get(e_idx, 0.0):
                self._cooldown_until[e_idx] = now + max(0.11, float(entry.get("duration_ms", 160)) / 1000.0 * 0.75)
                low = float(entry.get("low", 0.88))
                high = float(entry.get("high", 0.95))
                dur = int(entry.get("duration_ms", 165))
                if (low + high) > (best_low + best_high):
                    best_low, best_high, best_dur = low, high, dur

        self._prev_ptrs = seen_now
        if best_low > 0.0 or best_high > 0.0:
            return (best_low, best_high, best_dur)
        return None

    def detect_triggered_ram_events(self, system_ram_bytes: np.ndarray | bytes | None) -> tuple[float, float, int, str] | None:
        """Evaluates learned/configured RAM control-code & state-event triggers in microseconds."""
        if system_ram_bytes is None or not self._active_ram_events:
            return None

        buf_len = len(system_ram_bytes)
        if buf_len < 1024:
            return None

        now = time.monotonic()
        suppress = (now < self._boot_suppress_until) or (not self._ram_baseline_ready)

        best_low = 0.0
        best_high = 0.0
        best_dur = 0
        best_label = ""

        for idx, ev in enumerate(self._active_ram_events):
            off = ev["_sys_off"]
            width = ev["_width"]
            if off + width > buf_len:
                continue

            chunk = bytes(system_ram_bytes[off:off + width])
            if width == 1:
                cur_val = chunk[0]
            elif width == 2:
                cur_val = struct.unpack_from("<H", chunk, 0)[0]
            else:
                cur_val = struct.unpack_from("<I", chunk, 0)[0]

            prev_val = self._prev_ram_vals.get(idx)
            self._prev_ram_vals[idx] = cur_val

            if suppress or prev_val is None or cur_val == prev_val:
                continue

            trig = ev.get("trigger", "changed")
            tgt = int(ev.get("target_val", 0))
            matched = False

            if trig == "decreased":
                matched = (cur_val < prev_val) and ((prev_val - cur_val) >= max(1, tgt))
            elif trig == "increased":
                matched = (cur_val > prev_val) and ((cur_val - prev_val) >= max(1, tgt))
            elif trig == "zero_to_nonzero":
                matched = (prev_val == 0 and cur_val > 0)
            elif trig == "equals":
                matched = (cur_val == tgt and prev_val != tgt)
            elif trig == "bit_set":
                mask = tgt if tgt > 0 else 1
                matched = ((prev_val & mask) == 0) and ((cur_val & mask) != 0)
            elif trig == "changed":
                matched = True

            if matched and now >= self._ram_cooldown_until.get(idx, 0.0):
                dur = int(ev.get("duration_ms", 340))
                self._ram_cooldown_until[idx] = now + max(0.12, (dur / 1000.0) * 0.75)
                low = float(ev.get("low", 0.95))
                high = float(ev.get("high", 1.0))
                if (low + high) > (best_low + best_high):
                    best_low, best_high, best_dur = low, high, dur
                    best_label = str(ev.get("label") or f"0x{int(ev['address']):08X}")

        self._ram_baseline_ready = True
        if best_low > 0.0 or best_high > 0.0:
            return (best_low, best_high, best_dur, best_label)
        return None
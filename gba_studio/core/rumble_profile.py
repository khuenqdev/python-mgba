# rumble_profile.py
import os
import re
import json
import time
import datetime
import numpy as np

from ..tools.sappy_extractor import (
    scan_sappy_song_tables,
    read_voicegroup,
    scan_universal_rom_samples,
)

PROFILE_VERSION = 2

RUMBLE_SFX_CATEGORIES = {
    "hit_heavy": {"label": "Heavy Hit / Explosion", "low": 1.00, "high": 0.95, "duration_ms": 270},
    "hit_light": {"label": "Light Hit / Punch / Slash", "low": 0.92, "high": 1.00, "duration_ms": 210},
    "magic":     {"label": "Magic / Special Skill", "low": 0.88, "high": 1.00, "duration_ms": 280},
    "item":      {"label": "Item / Power-Up Pickup", "low": 0.72, "high": 0.95, "duration_ms": 210},
    "ignore":    {"label": "Ignore (Music / Voice / Menu)", "low": 0.00, "high": 0.00, "duration_ms": 0},
}


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


def build_rumble_profile_from_rom(rom_path: str, game_code: str = "", game_title: str = "") -> dict:
    """
    Scans a GBA ROM for M4A SFX & verified PCM samples, classifies them,
    and constructs a v2 per-game rumble profile dictionary.
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
    }


def get_rumble_profile_path(profiles_dir: str, profile_id: str) -> str:
    os.makedirs(profiles_dir, exist_ok=True)
    safe_id = re.sub(r"[^\w\-]", "_", str(profile_id or "DEFAULT")).strip("_") or "DEFAULT"
    return os.path.join(profiles_dir, f"{safe_id}.rumble.json")


class RumbleProfileStore:
    """
    Fast vectorized runtime lookup for per-game ROM SFX rumble profiles.
    Requires active streaming progression (`16..1024 bytes/frame`) for Raw PCM8
    so static graphics/tile pointers never false-trigger.
    """

    def __init__(self, config):
        self.config = config
        self.profile_path = ""
        self.entries = []
        self._active_entries = []
        self._starts_bus = np.empty(0, dtype=np.uint32)
        self._ends_bus = np.empty(0, dtype=np.uint32)
        self._min_bus = 0x08000000
        self._max_bus = 0x08000000
        self._prev_ptrs = {}
        self._cooldown_until = {}
        self._boot_suppress_until = 0.0

    def has_active_entries(self) -> bool:
        return len(self._active_entries) > 0

    def reset_runtime_state(self):
        self._prev_ptrs.clear()
        self._cooldown_until.clear()
        self._boot_suppress_until = time.monotonic() + 0.6

    def load_for_game(self, profile_id: str, rom_path: str = "") -> bool:
        self.entries = []
        self._active_entries = []
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

            # Upgrade legacy v1 profiles that lacked autocorrelation filtering
            if int(data.get("profile_version", 1)) < PROFILE_VERSION:
                if rom_path and os.path.exists(rom_path):
                    print(f"[RUMBLE PROFILE] Upgrading legacy v1 profile {os.path.basename(path)} to v{PROFILE_VERSION}...")
                    data = build_rumble_profile_from_rom(
                        rom_path,
                        game_code=data.get("game_code", profile_id),
                        game_title=data.get("game_title", ""),
                    )
                    self.save_profile(data, profile_id)
                    return len(self._active_entries) > 0
                return False

            raw_entries = data.get("sfx_entries", [])
            if isinstance(raw_entries, list):
                self.entries = raw_entries
                self._rebuild_index()
                return len(self._active_entries) > 0
        except Exception as e:
            print(f"[RUMBLE PROFILE WARNING] Failed to load {path}: {e}")
        return False

    def save_profile(self, profile_data: dict, profile_id: str):
        profile_data["profile_version"] = PROFILE_VERSION
        profiles_dir = self.config.get("paths", "profiles") or "profiles"
        path = get_rumble_profile_path(profiles_dir, profile_id)
        self.profile_path = path
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=2)
        os.replace(tmp, path)
        self.entries = profile_data.get("sfx_entries", [])
        self._rebuild_index()

    def _rebuild_index(self):
        active = [
            e for e in self.entries
            if e.get("enabled", False) and e.get("category", "ignore") != "ignore"
            and (float(e.get("low", 0)) > 0.0 or float(e.get("high", 0)) > 0.0)
        ]
        active.sort(key=lambda e: int(e["offset"]))
        self._active_entries = active

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

    def detect_triggered_sfx(self, system_ram_u32: np.ndarray | None) -> tuple[float, float, int] | None:
        if system_ram_u32 is None or len(self._active_entries) == 0:
            return None

        now = time.monotonic()
        if now < self._boot_suppress_until:
            return None

        rom_words = system_ram_u32[(system_ram_u32 >= self._min_bus) & (system_ram_u32 < self._max_bus)]
        if len(rom_words) == 0:
            self._prev_ptrs.clear()
            return None

        idx = np.searchsorted(self._starts_bus, rom_words, side="right") - 1
        valid = (idx >= 0) & (rom_words < self._ends_bus[np.maximum(0, idx)])
        if not np.any(valid):
            self._prev_ptrs.clear()
            return None

        matched_idxs = idx[valid]
        matched_ptrs = rom_words[valid]

        best_low = 0.0
        best_high = 0.0
        best_dur = 0
        seen_now = {}

        for e_idx_np, ptr_np in zip(matched_idxs, matched_ptrs):
            e_idx = int(e_idx_np)
            ptr = int(ptr_np)
            if e_idx not in seen_now or ptr < seen_now[e_idx]:
                seen_now[e_idx] = ptr

        for e_idx, ptr in seen_now.items():
            entry = self._active_entries[e_idx]
            start_bus = int(self._starts_bus[e_idx])
            prev_ptr = self._prev_ptrs.get(e_idx)
            is_m4a = (entry.get("kind") == "M4A WaveData")

            near_head = (ptr - start_bus) <= 512
            if prev_ptr is None:
                # Require streaming advancement for Raw PCM8; allow immediate start for verified M4A WaveData
                is_trigger = is_m4a and near_head
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
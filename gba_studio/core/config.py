# config.py
import os
import re
import json
import copy
import glob
import datetime

CONFIG_FILE = "mgba_config.json"

GLOBAL_ONLY_TOP_SECTIONS = {"recent_files", "paths", "netplay"}
GLOBAL_ONLY_KEY_PAIRS = {
    ("video", "window_width"),
    ("video", "window_height"),
    ("video", "splitter_sizes"),
    ("video", "scale"),
    ("sensors", "show_hud"),
}
PER_GAME_SECTIONS = ("audio", "emulation", "sensors", "video", "shaders", "ai_translation", "input")

DEFAULT_CONFIG = {
    "audio": {
        "mute": False,
        "volume": 0.5,
        "low_pass_filter": "medium",
        "stereo_widener": True,
        "bass_boost": False,
        "soft_limiter": True,
        "fast_forward_pitch_corrected": True
    },
    "emulation": {
        "skip_bios": True,
        "fast_forward_ratio": 2.0,
        "state_slot": 1,
        "pause_on_unfocus": True,
        "rewind_enabled": True,
        "rewind_buffer_size": 300,
        "rewind_interval": 10,
        "auto_soft_patch": True
    },
    "sensors": {
        "solar_level": 5,
        "rumble_enabled": True,
        "rumble_strength": 1.0,
        "gyro_enabled": True,
        "gyro_sensitivity": 1.0,
        "mouse_gyro": True,
        "show_hud": True
    },
    "video": {
        "frameskip": 0,
        "interframe_blending": False,
        "integer_scaling": False,
        "show_input_overlay": False,
        "clip_scale": 2,
        "scale": 3,
        "window_width": 720,
        "window_height": 649,
        "splitter_sizes": [480, 34, 135]
    },
    "shaders": {
        "enabled": False,
        "preset": "gba_color",
        "chain": []
    },
    "ai_translation": {
        "source_lang": "ja",
        "target_lang": "vi",
        "overlay_enabled": True,
        "auto_translate": False,
        "auto_debounce_ms": 1200,
        "auto_cooldown_ms": 4000,
        "auto_roi_mode": "auto"
    },
    "netplay": {
        "host": "127.0.0.1",
        "port": 5738,
        "mode": "lockstep",
        "frame_delay": 1,
        "player_name": "Player"
    },
    "recent_files": [],
    "paths": {
        "screenshots": "screenshots",
        "saves": "saves",
        "states": "states",
        "cheats": "cheats",
        "exports": "exports",
        "records": "records",
        "translations": "translations",
        "replays": "replays",
        "profiles": "profiles"
    },
    "input": {
        "keyboard": {
            "b": "x", "a": "z",
            "start": "Return", "select": "Tab",
            "up": "Up", "down": "Down",
            "left": "Left", "right": "Right",
            "l": "a", "r": "s",
            "autofire_a": "c",
            "autofire_b": "v",
            "autofire_l": "d",
            "autofire_r": "f",
            "fast_forward": "=",
            "fast_forward_down": "-",
            "solar_brighten": "]",
            "solar_darken": "[",
            "tilt_left": "q",
            "tilt_right": "e"
        },
        "gamepad": {
            "enabled": True,
            "device_index": 0,
            "deadzone": 0.2,
            "b": "button_0",
            "a": "button_1",
            "select": "button_6",
            "start": "button_7",
            "up": "hat_0_up",
            "down": "hat_0_down",
            "left": "hat_0_left",
            "right": "hat_0_right",
            "l": "button_4",
            "r": "button_5",
            "autofire_a": "button_2",
            "autofire_b": "button_3",
            "autofire_l": "button_9",
            "autofire_r": "button_10",
            "solar_darken": "button_14",
            "solar_brighten": "button_15"
        }
    }
}


class EmulatorConfig:
    def __init__(self):
        self.global_settings = copy.deepcopy(DEFAULT_CONFIG)
        self.settings = copy.deepcopy(DEFAULT_CONFIG)
        self.active_profile_id = None
        self.active_profile_title = ""
        self.profile_overrides = {}
        self.load()

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    user_config = json.load(f)

                    vol = user_config.get("audio", {}).get("volume", 0.5)
                    if vol is not None and float(vol) > 2.0:
                        user_config["audio"]["volume"] = 0.5

                    if "input" in user_config and "keyboard" not in user_config["input"]:
                        old_keys = user_config["input"]
                        user_config["input"] = {
                            "keyboard": old_keys,
                            "gamepad": DEFAULT_CONFIG["input"]["gamepad"]
                        }

                    self._update_dict(self.global_settings, user_config)
            except Exception as e:
                print(f"Config load error: {e}")

        self._rebuild_effective_settings()
        self.save()

    def _update_dict(self, base, new):
        for k, v in new.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                self._update_dict(base[k], v)
            else:
                base[k] = copy.deepcopy(v)

    def _rebuild_effective_settings(self):
        self.settings = copy.deepcopy(self.global_settings)
        if self.active_profile_id and self.profile_overrides:
            self._update_dict(self.settings, self.profile_overrides)

    def _is_global_only(self, section: str, key: str = None) -> bool:
        if section in GLOBAL_ONLY_TOP_SECTIONS:
            return True
        if key is not None and (section, key) in GLOBAL_ONLY_KEY_PAIRS:
            return True
        return False

    def save(self):
        for _, path_val in self.global_settings.get("paths", {}).items():
            os.makedirs(path_val, exist_ok=True)

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.global_settings, f, indent=4)
        except Exception as e:
            print(f"Config save error: {e}")

        if self.active_profile_id:
            self.save_active_profile()

    def reset_to_defaults(self):
        self.active_profile_id = None
        self.active_profile_title = ""
        self.profile_overrides = {}
        self.global_settings = copy.deepcopy(DEFAULT_CONFIG)
        self._rebuild_effective_settings()
        self.save()

    def get(self, section, key=None, default=None):
        """Safely retrieves effective config values (global + active game profile)."""
        if key is None:
            return self.settings.get(section, default)
        sec = self.settings.get(section)
        if isinstance(sec, dict):
            return sec.get(key, default)
        return default

    def set(self, section, key, value):
        val_copy = copy.deepcopy(value)
        if self.active_profile_id and not self._is_global_only(section, key):
            if section not in self.profile_overrides:
                self.profile_overrides[section] = {}
            self.profile_overrides[section][key] = val_copy
            if section not in self.settings:
                self.settings[section] = {}
            self.settings[section][key] = copy.deepcopy(val_copy)
            self.save_active_profile()
        else:
            if section not in self.global_settings:
                self.global_settings[section] = {}
            self.global_settings[section][key] = val_copy
            if section not in self.settings:
                self.settings[section] = {}
            self.settings[section][key] = copy.deepcopy(val_copy)
            self.save()

    # =========================================================================
    # PER-GAME CONFIGURATION PROFILES & PLAYTIME STATISTICS
    # =========================================================================
    def get_profile_path(self, profile_id: str) -> str:
        profiles_dir = self.global_settings.get("paths", {}).get("profiles", "profiles") or "profiles"
        os.makedirs(profiles_dir, exist_ok=True)
        safe_id = re.sub(r"[^\w\-]", "_", str(profile_id or "DEFAULT")).strip("_") or "DEFAULT"
        return os.path.join(profiles_dir, f"{safe_id}.json")

    def _read_profile_file(self, profile_id: str) -> dict:
        path = self.get_profile_path(profile_id)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            print(f"[PROFILE WARNING] Failed to read {path}: {e}")
            return {}

    def _write_profile_file(self, profile_id: str, payload: dict):
        path = self.get_profile_path(profile_id)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[PROFILE WARNING] Failed to write {path}: {e}")

    @staticmethod
    def _has_custom_config_flag(raw: dict) -> bool:
        if not raw:
            return False
        meta = raw.get("_meta", {})
        if isinstance(meta, dict) and "use_custom_config" in meta:
            return bool(meta["use_custom_config"])
        # Legacy profile files without explicit flag are custom profiles if overrides exist
        overrides = raw.get("overrides")
        return bool(isinstance(overrides, dict) and overrides)

    def has_profile(self, profile_id: str) -> bool:
        if not profile_id:
            return False
        raw = self._read_profile_file(profile_id)
        return self._has_custom_config_flag(raw)

    def deactivate_profile(self):
        self.active_profile_id = None
        self.active_profile_title = ""
        self.profile_overrides = {}
        self._rebuild_effective_settings()

    def activate_profile_if_exists(self, profile_id: str, title: str = "") -> bool:
        self.deactivate_profile()
        if not profile_id:
            return False

        raw = self._read_profile_file(profile_id)
        if not self._has_custom_config_flag(raw):
            return False

        overrides = raw.get("overrides", {})
        cleaned = {}
        if isinstance(overrides, dict):
            for sec, sec_val in overrides.items():
                if sec == "_meta" or sec in GLOBAL_ONLY_TOP_SECTIONS or not isinstance(sec_val, dict):
                    continue
                filtered = {
                    k: v for k, v in sec_val.items()
                    if (sec, k) not in GLOBAL_ONLY_KEY_PAIRS
                }
                if filtered:
                    cleaned[sec] = filtered

        self.active_profile_id = profile_id
        self.active_profile_title = title or raw.get("_meta", {}).get("game_title", "")
        self.profile_overrides = cleaned
        self._rebuild_effective_settings()
        return True

    def create_profile(self, profile_id: str, title: str = "", seed_from_current: bool = False) -> str:
        self.active_profile_id = profile_id
        self.active_profile_title = title
        self.profile_overrides = {}

        if seed_from_current:
            for sec in PER_GAME_SECTIONS:
                src_sec = self.settings.get(sec)
                if isinstance(src_sec, dict):
                    copied = {
                        k: copy.deepcopy(v)
                        for k, v in src_sec.items()
                        if (sec, k) not in GLOBAL_ONLY_KEY_PAIRS
                    }
                    if copied:
                        self.profile_overrides[sec] = copied

        self._rebuild_effective_settings()
        self.save_active_profile()
        return self.get_profile_path(profile_id)

    def save_active_profile(self):
        if not self.active_profile_id:
            return
        existing = self._read_profile_file(self.active_profile_id)
        stats = existing.get("stats") if isinstance(existing.get("stats"), dict) else {}
        payload = {
            "_meta": {
                "profile_id": self.active_profile_id,
                "game_title": self.active_profile_title,
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "use_custom_config": True,
            },
            "stats": stats,
            "overrides": self.profile_overrides,
        }
        self._write_profile_file(self.active_profile_id, payload)

    def delete_profile(self, profile_id: str) -> bool:
        path = self.get_profile_path(profile_id)
        if self.active_profile_id == profile_id:
            self.deactivate_profile()
        if not os.path.exists(path):
            return False

        raw = self._read_profile_file(profile_id)
        stats = raw.get("stats")
        if isinstance(stats, dict) and stats:
            # Preserve playtime/launch statistics when removing custom config overrides
            meta = raw.get("_meta", {}) if isinstance(raw.get("_meta"), dict) else {}
            meta["use_custom_config"] = False
            meta["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            self._write_profile_file(profile_id, {
                "_meta": meta,
                "stats": stats,
                "overrides": {},
            })
            return True

        try:
            os.remove(path)
            return True
        except OSError as e:
            print(f"[PROFILE WARNING] Failed to delete profile {path}: {e}")
            return False

    # =========================================================================
    # PER-GAME PLAYTIME & STATISTICS API
    # =========================================================================
    @staticmethod
    def _default_stats() -> dict:
        return {
            "playtime_seconds": 0.0,
            "launch_count": 0,
            "first_played": "",
            "last_played": "",
            "last_session_seconds": 0.0,
        }

    def get_game_stats(self, profile_id: str) -> dict:
        if not profile_id:
            return self._default_stats()
        raw = self._read_profile_file(profile_id)
        stats = self._default_stats()
        if isinstance(raw.get("stats"), dict):
            stats.update(raw["stats"])
        return stats

    def record_game_launch(self, profile_id: str, title: str = "") -> dict:
        if not profile_id:
            return self._default_stats()

        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        raw = self._read_profile_file(profile_id)
        meta = raw.get("_meta", {}) if isinstance(raw.get("_meta"), dict) else {}
        stats = self._default_stats()
        if isinstance(raw.get("stats"), dict):
            stats.update(raw["stats"])

        stats["launch_count"] = int(stats.get("launch_count", 0) or 0) + 1
        if not stats.get("first_played"):
            stats["first_played"] = now_iso
        stats["last_played"] = now_iso
        stats["last_session_seconds"] = 0.0

        meta["profile_id"] = profile_id
        if title:
            meta["game_title"] = title
        meta["updated_at"] = now_iso
        if "use_custom_config" not in meta:
            meta["use_custom_config"] = self._has_custom_config_flag(raw)

        payload = {
            "_meta": meta,
            "stats": stats,
            "overrides": raw.get("overrides", {}) if isinstance(raw.get("overrides"), dict) else {},
        }
        self._write_profile_file(profile_id, payload)
        return stats

    def add_game_playtime(self, profile_id: str, delta_seconds: float, title: str = "",
                          session_total: float | None = None) -> dict:
        if not profile_id or delta_seconds <= 0:
            return self.get_game_stats(profile_id)

        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        raw = self._read_profile_file(profile_id)
        meta = raw.get("_meta", {}) if isinstance(raw.get("_meta"), dict) else {}
        stats = self._default_stats()
        if isinstance(raw.get("stats"), dict):
            stats.update(raw["stats"])

        stats["playtime_seconds"] = round(float(stats.get("playtime_seconds", 0.0) or 0.0) + float(delta_seconds), 1)
        stats["last_played"] = now_iso
        if not stats.get("first_played"):
            stats["first_played"] = now_iso
        if session_total is not None:
            stats["last_session_seconds"] = round(float(session_total), 1)

        meta["profile_id"] = profile_id
        if title:
            meta["game_title"] = title
        meta["updated_at"] = now_iso
        if "use_custom_config" not in meta:
            meta["use_custom_config"] = self._has_custom_config_flag(raw)

        payload = {
            "_meta": meta,
            "stats": stats,
            "overrides": raw.get("overrides", {}) if isinstance(raw.get("overrides"), dict) else {},
        }
        self._write_profile_file(profile_id, payload)
        return stats

    def reset_game_stats(self, profile_id: str):
        if not profile_id:
            return
        raw = self._read_profile_file(profile_id)
        if not raw:
            return
        meta = raw.get("_meta", {}) if isinstance(raw.get("_meta"), dict) else {}
        meta["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        raw["_meta"] = meta
        raw["stats"] = self._default_stats()
        self._write_profile_file(profile_id, raw)

    def list_all_game_stats(self) -> list:
        profiles_dir = self.global_settings.get("paths", {}).get("profiles", "profiles") or "profiles"
        os.makedirs(profiles_dir, exist_ok=True)
        rows = []
        for path in sorted(glob.glob(os.path.join(profiles_dir, "*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    continue
                meta = raw.get("_meta", {}) if isinstance(raw.get("_meta"), dict) else {}
                stats = self._default_stats()
                if isinstance(raw.get("stats"), dict):
                    stats.update(raw["stats"])
                pid = meta.get("profile_id") or os.path.splitext(os.path.basename(path))[0]
                rows.append({
                    "profile_id": pid,
                    "game_title": meta.get("game_title", pid),
                    "use_custom_config": self._has_custom_config_flag(raw),
                    **stats,
                })
            except Exception:
                continue
        rows.sort(key=lambda r: float(r.get("playtime_seconds", 0.0) or 0.0), reverse=True)
        return rows

    # =========================================================================
    # RECENT FILES (ALWAYS GLOBAL)
    # =========================================================================
    def get_recent_files(self):
        recent = self.global_settings.get("recent_files", [])
        return [f for f in recent if os.path.exists(f)]

    def add_recent_file(self, filepath: str):
        if not filepath:
            return
        filepath = os.path.abspath(filepath)
        recent = self.global_settings.get("recent_files", [])
        if filepath in recent:
            recent.remove(filepath)
        recent.insert(0, filepath)
        self.global_settings["recent_files"] = recent[:10]
        self.settings["recent_files"] = list(self.global_settings["recent_files"])
        self.save()

    def clear_recent_files(self):
        self.global_settings["recent_files"] = []
        self.settings["recent_files"] = []
        self.save()

I will implement enhancement **#14: Per-Game Playtime & Statistics Tracking** in 2 parts:

- **Part 1 (this response):** Persistent per-game `stats` (`playtime_seconds`, `launch_count`, `last_played`, `first_played`, `last_session_seconds`) in `profiles/<game_code>.json`, live HUD readout in `SensorHudWidget`, and a `GameStatsDialog` summary viewer (`config.py`, `sensor_hud.py`, `stats_dialog.py`).
- **Part 2:** Active-playtime 1s tracker, safe 15s/exit disk flushing, and menu wiring (`menu_actions.py`, `settings_actions.py`, `runtime_actions.py`, `main_window.py`).

---

### Part 1: Stats Persistence, Sensor HUD & Statistics Viewer

#### 1. `gba_studio/core/config.py`
```python
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
```

---

#### 2. `gba_studio/ui/sensor_hud.py`
```python
# sensor_hud.py
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import QTimer, QSize


def format_playtime(total_seconds: float) -> str:
    sec = max(0, int(total_seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class SensorHudWidget(QFrame):
    """Dynamically responsive status bar with fluid multi-tier scaling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            SensorHudWidget {
                background-color: #12151D;
                border-top: 1px solid #232938;
                border-bottom: 1px solid #232938;
            }
            QLabel {
                font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                font-weight: bold;
            }
        """)

        self.game_title = "Unknown"
        self.game_code = "----"
        self.hw_type = "None"
        self.current_solar_level = 5
        self.rumble_active = False
        self.rumble_strength = 0.0
        self.current_tilt_x = 0.0
        self.current_tilt_y = 0.0
        self.current_gyro_angle = 0.0
        self.total_playtime_seconds = 0.0
        self.launch_count = 0

        self._rumble_off_timer = QTimer(self)
        self._rumble_off_timer.setSingleShot(True)
        self._rumble_off_timer.timeout.connect(self._clear_rumble)

        self.layout_box = QHBoxLayout(self)
        self.layout_box.setContentsMargins(6, 0, 6, 0)
        self.layout_box.setSpacing(6)

        self.lbl_game = QLabel()
        self.lbl_game.setStyleSheet("color: #90CAF9;")
        self.lbl_game.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_game.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_game, 3)

        self.lbl_solar = QLabel()
        self.lbl_solar.setStyleSheet("color: #FFD54F;")
        self.lbl_solar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_solar.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_solar, 3)

        self.lbl_rumble = QLabel()
        self.lbl_rumble.setStyleSheet("color: #78909C;")
        self.lbl_rumble.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_rumble.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_rumble, 2)

        self.lbl_gyro = QLabel()
        self.lbl_gyro.setStyleSheet("color: #4DD0E1;")
        self.lbl_gyro.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_gyro.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_gyro, 2)

        self.lbl_playtime = QLabel()
        self.lbl_playtime.setStyleSheet("color: #A5D6A7;")
        self.lbl_playtime.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_playtime.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_playtime, 2)

        self.update_display()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def sizeHint(self) -> QSize:
        return QSize(240, 30)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_display()

    def update_display(self):
        w = self.width()

        type_str = f" ({self.hw_type.replace('_', ' ').title()})" if (w >= 920 and self.hw_type not in ["None", ""]) else ""
        if w < 380:
            self.lbl_game.setText(f"[{self.game_code}]")
        elif w < 650:
            short_t = self.game_title[:10] + ".." if len(self.game_title) > 10 else self.game_title
            self.lbl_game.setText(f"🎮 [{self.game_code}] {short_t}")
        else:
            self.lbl_game.setText(f"🎮 [{self.game_code}] {self.game_title}{type_str}")

        if w < 540:
            self.lbl_solar.setText(f"☀️ {self.current_solar_level}/10")
        else:
            sun_filled = "■" * self.current_solar_level
            sun_empty = "□" * (10 - self.current_solar_level)
            self.lbl_solar.setText(f"☀️ [{sun_filled}{sun_empty}] {self.current_solar_level}/10")

        if self.rumble_active:
            pct = int(self.rumble_strength * 100)
            filled = max(1, min(5, int(self.rumble_strength * 5)))
            bars = "■" * filled + "□" * (5 - filled)
            if w < 560:
                self.lbl_rumble.setText(f"📳 {pct}%")
            else:
                self.lbl_rumble.setText(f"📳 [{bars}] {pct}%")
            self.lbl_rumble.setStyleSheet("color: #FF5252;")
        else:
            if w < 560:
                self.lbl_rumble.setText("📳 OFF")
            else:
                self.lbl_rumble.setText("📳 [□□□□□] OFF")
            self.lbl_rumble.setStyleSheet("color: #78909C;")

        deg = int(self.current_gyro_angle) % 360
        if w < 560:
            self.lbl_gyro.setText(f"🎯 {deg}°")
        else:
            idx = max(0, min(6, int((self.current_tilt_x + 1.0) * 3.0)))
            meter = list("───●───")
            meter[idx] = "●"
            meter_str = "".join(meter)
            self.lbl_gyro.setText(f"🎯 [{meter_str}] {deg:3d}°")

        pt_str = format_playtime(self.total_playtime_seconds)
        if w < 440:
            self.lbl_playtime.setText(f"⏱ {pt_str}")
        elif self.launch_count > 0:
            self.lbl_playtime.setText(f"⏱ {pt_str} (#{self.launch_count})")
        else:
            self.lbl_playtime.setText(f"⏱ {pt_str}")

    def on_game_identified(self, title: str, code: str, hw_type: str):
        self.game_title = title
        self.game_code = code
        self.hw_type = hw_type
        self.update_display()

    def on_playtime_updated(self, total_seconds: float, launch_count: int = 0):
        self.total_playtime_seconds = max(0.0, float(total_seconds))
        self.launch_count = max(0, int(launch_count))
        self.update_display()

    def on_solar_updated(self, level: int):
        self.current_solar_level = level
        self.update_display()

    def on_rumble_triggered(self, low_freq: float, high_freq: float, duration_ms: int):
        if low_freq <= 0.0 and high_freq <= 0.0:
            self._clear_rumble()
            return

        self.rumble_active = True
        self.rumble_strength = max(low_freq, high_freq)
        self.update_display()
        self._rumble_off_timer.start(max(90, duration_ms))

    def _clear_rumble(self):
        self.rumble_active = False
        self.rumble_strength = 0.0
        self.update_display()

    def on_tilt_updated(self, x: float, y: float, angle: float):
        self.current_tilt_x = x
        self.current_tilt_y = y
        self.current_gyro_angle = angle
        self.update_display()
```

---

#### 3. `gba_studio/ui/stats_dialog.py`
```python
# stats_dialog.py
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PyQt6.QtCore import Qt

from .sensor_hud import format_playtime


def _format_human_time(seconds: float) -> str:
    sec = max(0, int(seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s ({format_playtime(sec)})"
    return f"{m}m {s:02d}s"


class GameStatsDialog(QDialog):
    """Displays per-game playtime, launch count, and timestamps from profiles/<game_code>.json."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.mw = main_window
        self.config = main_window.config
        self.setWindowTitle("Per-Game Playtime & Statistics")
        self.resize(780, 440)
        self._init_ui()
        self.refresh_stats()

    def _init_ui(self):
        root = QVBoxLayout(self)

        self.summary_lbl = QLabel("Loading statistics...")
        self.summary_lbl.setStyleSheet(
            "background: #161A22; color: #A5D6A7; font-weight: bold; "
            "padding: 8px 10px; border-radius: 5px; border: 1px solid #263042;"
        )
        root.addWidget(self.summary_lbl)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Game ID", "Title", "Total Playtime", "Launches", "Last Session", "Last Played"
        ])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.table)

        btns = QHBoxLayout()
        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh_stats)
        btns.addWidget(btn_refresh)

        btn_reset = QPushButton("🗑 Reset Selected Stats")
        btn_reset.clicked.connect(self.reset_selected)
        btns.addWidget(btn_reset)

        btns.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        root.addLayout(btns)

    def refresh_stats(self):
        if hasattr(self.mw, "_flush_playtime"):
            self.mw._flush_playtime()

        rows = self.config.list_all_game_stats()
        self.rows_data = rows
        self.table.setRowCount(len(rows))

        total_sec = 0.0
        total_launches = 0

        for idx, item in enumerate(rows):
            pt = float(item.get("playtime_seconds", 0.0) or 0.0)
            lc = int(item.get("launch_count", 0) or 0)
            last_sess = float(item.get("last_session_seconds", 0.0) or 0.0)
            last_played = str(item.get("last_played") or "--").replace("T", " ")

            total_sec += pt
            total_launches += lc

            self.table.setItem(idx, 0, QTableWidgetItem(item["profile_id"]))
            self.table.setItem(idx, 1, QTableWidgetItem(item["game_title"]))
            self.table.setItem(idx, 2, QTableWidgetItem(_format_human_time(pt)))
            self.table.setItem(idx, 3, QTableWidgetItem(str(lc)))
            self.table.setItem(idx, 4, QTableWidgetItem(_format_human_time(last_sess)))
            self.table.setItem(idx, 5, QTableWidgetItem(last_played))

        self.summary_lbl.setText(
            f"Tracked Games: {len(rows)}   |   Total Playtime: {_format_human_time(total_sec)}   |   Total Launches: {total_launches}"
        )

    def reset_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows_data):
            return
        pid = self.rows_data[row]["profile_id"]
        reply = QMessageBox.question(
            self,
            "Reset Statistics",
            f"Reset playtime & launch count statistics for [{pid}]?\n(Your custom per-game settings will be kept.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.config.reset_game_stats(pid)
            if getattr(self.mw, "_active_stats_pid", None) == pid:
                self.mw._base_playtime_sec = 0.0
                self.mw._session_playtime_sec = 0.0
                self.mw._unsaved_playtime_sec = 0.0
                self.mw._active_launch_count = 0
                self.mw.sensor_hud.on_playtime_updated(0.0, 0)
            self.refresh_stats()
```

Reply **`Continue`** to receive **Part 2** (`menu_actions.py`, `settings_actions.py`, `runtime_actions.py`, `main_window.py`).
### Part 2: Playtime Timer, HUD Sync & Menu Integration

#### 1. `gba_studio/ui/menu_actions.py`
```python
# menu_actions.py
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence

from ..tools.dev_tools import (
    PaletteViewerDialog, SpriteViewerDialog, TileViewerDialog,
    MapViewerDialog, FrameInspectorDialog, MemoryViewerDialog,
    MemorySearchDialog, IORegisterViewerDialog
)


class MenuBuilderMixin:
    def _create_menus(self):
        menubar = self.menuBar()

        # 1. FILE MENU
        file_menu = menubar.addMenu("File")

        load_action = QAction("Load ROM...", self)
        load_action.setShortcut(QKeySequence("Ctrl+O"))
        load_action.triggered.connect(self.load_rom)
        file_menu.addAction(load_action)

        self.recent_menu = file_menu.addMenu("Open Recent")

        apply_patch_act = QAction("Apply Soft-Patch (.ips / .bps / .ups)...", self)
        apply_patch_act.triggered.connect(self.apply_patch_dialog)
        file_menu.addAction(apply_patch_act)

        file_menu.addSeparator()

        save_file_act = QAction("Save State to File...", self)
        save_file_act.triggered.connect(self.save_state_to_file)
        file_menu.addAction(save_file_act)

        load_file_act = QAction("Load State from File...", self)
        load_file_act.triggered.connect(self.load_state_from_file)
        file_menu.addAction(load_file_act)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 2. EMULATION MENU
        emu_menu = menubar.addMenu("Emulation")

        self.pause_action = QAction("Pause", self, checkable=True)
        self.pause_action.setShortcut(QKeySequence("Ctrl+P"))
        self.pause_action.triggered.connect(self.toggle_pause)
        emu_menu.addAction(self.pause_action)

        self.pause_unfocus_action = QAction("Pause When Unfocused / Inactive", self, checkable=True)
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))
        self.pause_unfocus_action.triggered.connect(self.toggle_pause_on_unfocus)
        emu_menu.addAction(self.pause_unfocus_action)

        step_act = QAction("Step Frame", self)
        step_act.setShortcut(QKeySequence("Ctrl+N"))
        step_act.triggered.connect(self.step_frame)
        emu_menu.addAction(step_act)

        reset_action = QAction("Reset", self)
        reset_action.setShortcut(QKeySequence("Ctrl+R"))
        reset_action.triggered.connect(self.reset_emulation)
        emu_menu.addAction(reset_action)

        emu_menu.addSeparator()

        self.rewind_enabled_action = QAction("Enable Rewind Buffer (Hold: Space or `)", self, checkable=True)
        self.rewind_enabled_action.setChecked(bool(self.config.get("emulation", "rewind_enabled", True)))
        self.rewind_enabled_action.triggered.connect(self.toggle_rewind_enabled)
        emu_menu.addAction(self.rewind_enabled_action)

        self.auto_patch_action = QAction("Auto Soft-Patch (.ips / .bps / .ups)", self, checkable=True)
        self.auto_patch_action.setChecked(bool(self.config.get("emulation", "auto_soft_patch", True)))
        self.auto_patch_action.triggered.connect(self.toggle_auto_soft_patch)
        emu_menu.addAction(self.auto_patch_action)

        emu_menu.addSeparator()

        self.skip_bios_action = QAction("Skip BIOS Intro", self, checkable=True)
        self.skip_bios_action.setChecked(bool(self.config.get("emulation", "skip_bios")))
        self.skip_bios_action.triggered.connect(self.toggle_skip_bios)
        emu_menu.addAction(self.skip_bios_action)

        emu_menu.addSeparator()

        quick_save_act = QAction("Quick Save State", self)
        quick_save_act.setShortcut(QKeySequence("Shift+F10"))
        quick_save_act.triggered.connect(lambda: self.quick_save_state(self.current_slot))
        emu_menu.addAction(quick_save_act)

        quick_load_act = QAction("Quick Load State", self)
        quick_load_act.setShortcut(QKeySequence("F10"))
        quick_load_act.triggered.connect(lambda: self.quick_load_state(self.current_slot))
        emu_menu.addAction(quick_load_act)

        state_browser_act = QAction("Save State Slot Browser...", self)
        state_browser_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        state_browser_act.triggered.connect(self.open_state_browser)
        emu_menu.addAction(state_browser_act)

        slot_menu = emu_menu.addMenu("Select State Slot")
        self.slot_group = QActionGroup(self)
        self.slot_group.setExclusive(True)
        self.slot_actions = {}

        for slot_num in range(1, 10):
            slot_act = QAction(f"Slot {slot_num}", self, checkable=True)
            slot_act.triggered.connect(lambda checked, s=slot_num: self.set_active_slot(s))
            self.slot_group.addAction(slot_act)
            slot_menu.addAction(slot_act)
            self.slot_actions[slot_num] = slot_act

        emu_menu.addSeparator()

        self.ff_action = QAction("Fast Forward (Hotkey: = / -)", self, checkable=True)
        self.ff_action.triggered.connect(self.toggle_ff)
        emu_menu.addAction(self.ff_action)

        ff_speed_menu = emu_menu.addMenu("Fast Forward Speed")
        self.ff_group = QActionGroup(self)
        self.ff_group.setExclusive(True)
        self.ff_actions = {}

        speeds = [1.5, 2.0, 3.0, 4.0, 8.0, 10.0]
        for spd in speeds:
            spd_act = QAction(f"{spd}x", self, checkable=True)
            spd_act.triggered.connect(lambda checked, s=spd: self.set_ff_speed(s))
            self.ff_group.addAction(spd_act)
            ff_speed_menu.addAction(spd_act)
            self.ff_actions[spd] = spd_act

        act_ff_up = QAction("Faster Fast-Forward Speed (+)", self)
        act_ff_up.setShortcut(QKeySequence("="))
        act_ff_up.triggered.connect(lambda: self.cycle_ff_speed(forward=True))
        ff_speed_menu.addAction(act_ff_up)

        act_ff_down = QAction("Slower Fast-Forward Speed (-)", self)
        act_ff_down.setShortcut(QKeySequence("-"))
        act_ff_down.triggered.connect(lambda: self.cycle_ff_speed(forward=False))
        ff_speed_menu.addAction(act_ff_down)

        emu_menu.addSeparator()
        replay_menu = emu_menu.addMenu("Gameplay Replay & TAS")

        self.act_record_session = QAction("⏺ Start Recording Session...", self)
        self.act_record_session.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.act_record_session.triggered.connect(self.toggle_session_recording)
        replay_menu.addAction(self.act_record_session)

        self.act_play_session = QAction("▶ Load & Play Replay (.json)...", self)
        self.act_play_session.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.act_play_session.triggered.connect(self.load_replay_session)
        replay_menu.addAction(self.act_play_session)

        self.act_stop_replay = QAction("⏹ Stop Current Replay", self)
        self.act_stop_replay.triggered.connect(self.stop_replay_session)
        replay_menu.addAction(self.act_stop_replay)

        replay_menu.addSeparator()

        self.act_tas_editor = QAction("🎹 Piano-Roll TAS Timeline Editor...", self)
        self.act_tas_editor.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.act_tas_editor.triggered.connect(self.open_tas_editor)
        replay_menu.addAction(self.act_tas_editor)

        # 3. SENSORS & PERIPHERALS MENU
        sensors_menu = menubar.addMenu("Sensors & Rumble")

        self.act_sensor_hud = QAction("Show Sensor / Rumble Status Panel", self, checkable=True)
        self.act_sensor_hud.setChecked(bool(self.config.get("sensors", "show_hud", True)))
        self.act_sensor_hud.triggered.connect(self.toggle_sensor_hud)
        sensors_menu.addAction(self.act_sensor_hud)
        sensors_menu.addSeparator()

        solar_menu = sensors_menu.addMenu("☀️ Solar Sensor (Boktai)")
        self.solar_group = QActionGroup(self)
        self.solar_group.setExclusive(True)
        self.solar_actions = {}

        for lvl in range(11):
            act = QAction(
                f"Sun Level {lvl}" + (" (Maximum)" if lvl == 10 else (" (Dark)" if lvl == 0 else "")),
                self, checkable=True
            )
            act.triggered.connect(lambda checked, l=lvl: self.set_solar_level(l))
            self.solar_group.addAction(act)
            solar_menu.addAction(act)
            self.solar_actions[lvl] = act

        act_brighten = QAction("Brighten Sunlight (+1)", self)
        act_brighten.setShortcut(QKeySequence("]"))
        act_brighten.triggered.connect(lambda: self.adjust_solar_sensor(1))
        solar_menu.addAction(act_brighten)

        act_darken = QAction("Darken Sunlight (-1)", self)
        act_darken.setShortcut(QKeySequence("["))
        act_darken.triggered.connect(lambda: self.adjust_solar_sensor(-1))
        solar_menu.addAction(act_darken)

        sensors_menu.addSeparator()

        self.act_rumble = QAction("🎮 Controller Motor Rumble", self, checkable=True)
        self.act_rumble.setChecked(bool(self.config.get("sensors", "rumble_enabled")))
        self.act_rumble.triggered.connect(self.toggle_rumble)
        sensors_menu.addAction(self.act_rumble)

        self.act_mouse_gyro = QAction("🖱️ Mouse Twist to Gyro (Right-Click Drag)", self, checkable=True)
        self.act_mouse_gyro.setChecked(bool(self.config.get("sensors", "mouse_gyro")))
        self.act_mouse_gyro.triggered.connect(self.toggle_mouse_gyro)
        sensors_menu.addAction(self.act_mouse_gyro)

        act_reset_tilt = QAction("🎯 Center Gyro & Tilt Sensor", self)
        act_reset_tilt.setShortcut(QKeySequence("Backspace"))
        act_reset_tilt.triggered.connect(self.reset_sensors)
        sensors_menu.addAction(act_reset_tilt)

        # 4. AUDIO MENU
        audio_menu = menubar.addMenu("Audio")
        self.mute_action = QAction("Mute", self, checkable=True)
        self.mute_action.setChecked(self.config.get("audio", "mute"))
        self.mute_action.triggered.connect(self.toggle_mute)
        audio_menu.addAction(self.mute_action)

        self.ff_pitch_action = QAction("Pitch-Corrected Fast-Forward Audio (WSOLA)", self, checkable=True)
        self.ff_pitch_action.setChecked(bool(self.config.get("audio", "fast_forward_pitch_corrected", True)))
        self.ff_pitch_action.triggered.connect(self.toggle_ff_pitch_corrected)
        audio_menu.addAction(self.ff_pitch_action)

        audio_menu.addSeparator()

        lpf_menu = audio_menu.addMenu("Low-Pass Filter")
        self.lpf_group = QActionGroup(self)
        self.lpf_group.setExclusive(True)
        self.lpf_actions = {}

        lpf_modes = [
            ("off", "Off (Raw DirectSound)"),
            ("light", "Light (~12 kHz - Mild Hiss Reduction)"),
            ("medium", "Medium (~8 kHz - Warm Speaker Sound)"),
            ("heavy", "Heavy (~5 kHz - Smooth Vintage Sound)")
        ]
        for mode_key, label in lpf_modes:
            act = QAction(label, self, checkable=True)
            act.triggered.connect(lambda checked, m=mode_key: self.set_lpf_mode(m))
            self.lpf_group.addAction(act)
            lpf_menu.addAction(act)
            self.lpf_actions[mode_key] = act

        enh_menu = audio_menu.addMenu("Enhancements")
        self.widener_action = QAction("Stereo Widener (Surround Sound)", self, checkable=True)
        self.widener_action.triggered.connect(self.toggle_stereo_widener)
        enh_menu.addAction(self.widener_action)

        self.bass_action = QAction("Bass Boost (Low-End Warmth)", self, checkable=True)
        self.bass_action.triggered.connect(self.toggle_bass_boost)
        enh_menu.addAction(self.bass_action)

        self.limiter_action = QAction("Soft Limiter (Anti-Clipping)", self, checkable=True)
        self.limiter_action.triggered.connect(self.toggle_soft_limiter)
        enh_menu.addAction(self.limiter_action)

        vol_menu = audio_menu.addMenu("Volume Gain")
        self.vol_group = QActionGroup(self)
        self.vol_group.setExclusive(True)
        self.vol_actions = {}

        vol_levels = [0.25, 0.5, 0.75, 1.0, 1.25]
        for vol in vol_levels:
            lbl = f"{int(vol * 100)}%" + (" (Default)" if abs(vol - 0.5) < 0.01 else "")
            act = QAction(lbl, self, checkable=True)
            act.triggered.connect(lambda checked, v=vol: self.set_volume_gain(v))
            self.vol_group.addAction(act)
            vol_menu.addAction(act)
            self.vol_actions[vol] = act

        # 5. VIDEO MENU
        video_menu = menubar.addMenu("Video")

        self.fullscreen_action = QAction("Fullscreen (Alt+Enter / F11)", self, checkable=True)
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        video_menu.addAction(self.fullscreen_action)

        self.integer_scale_action = QAction("Integer Scaling Only", self, checkable=True)
        self.integer_scale_action.setChecked(bool(self.config.get("video", "integer_scaling", False)))
        self.integer_scale_action.triggered.connect(self.toggle_integer_scaling)
        video_menu.addAction(self.integer_scale_action)

        self.input_overlay_action = QAction("Always Show Live Input & Frame HUD", self, checkable=True)
        self.input_overlay_action.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self.input_overlay_action.setChecked(bool(self.config.get("video", "show_input_overlay", False)))
        self.input_overlay_action.triggered.connect(self.toggle_input_overlay)
        video_menu.addAction(self.input_overlay_action)

        video_menu.addSeparator()

        shader_menu = video_menu.addMenu("RetroArch Shaders")

        self.act_shader_toggle = QAction("Enable Shaders", self, checkable=True)
        self.act_shader_toggle.setChecked(self.shader_manager.enabled)
        self.act_shader_toggle.triggered.connect(self.toggle_shaders)
        shader_menu.addAction(self.act_shader_toggle)

        act_shader_cfg = QAction("Configure Shaders & Parameters...", self)
        act_shader_cfg.setShortcut(QKeySequence("F8"))
        act_shader_cfg.triggered.connect(self.open_shader_dialog)
        shader_menu.addAction(act_shader_cfg)
        shader_menu.addSeparator()

        sub_presets = shader_menu.addMenu("Quick Presets")
        presets = [
            ("GBA Color & LCD Grid", "gba_color"),
            ("CRT-Easymode", "crt"),
            ("Scanlines Classic", "scanlines"),
            ("Sharp Bilinear", "sharp"),
            ("Stock (Passthrough)", "stock")
        ]
        for label, p_id in presets:
            act = sub_presets.addAction(label)
            act.triggered.connect(lambda _, pid=p_id: self.apply_quick_shader_preset(pid))

        video_menu.addSeparator()

        self.blend_action = QAction("Interframe Blending", self, checkable=True)
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.blend_action.triggered.connect(self.toggle_interframe_blending)
        video_menu.addAction(self.blend_action)

        frameskip_menu = video_menu.addMenu("Frameskip")
        self.fs_group = QActionGroup(self)
        self.fs_group.setExclusive(True)
        self.fs_actions = {}

        for fs in range(0, 6):
            label = "0 (Disabled)" if fs == 0 else f"{fs} Frames"
            fs_act = QAction(label, self, checkable=True)
            fs_act.triggered.connect(lambda checked, count=fs: self.set_frameskip(count))
            self.fs_group.addAction(fs_act)
            frameskip_menu.addAction(fs_act)
            self.fs_actions[fs] = fs_act

        video_menu.addSeparator()

        screenshot_action = QAction("Screenshot", self)
        screenshot_action.setShortcut(QKeySequence("F12"))
        screenshot_action.triggered.connect(self.take_screenshot)
        video_menu.addAction(screenshot_action)

        clip_menu = video_menu.addMenu("Clip Recorder (GIF / MP4)")

        self.act_record_gif = QAction("🎬 Record Animated GIF (30 FPS)", self, checkable=True)
        self.act_record_gif.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self.act_record_gif.triggered.connect(self.toggle_clip_gif)
        clip_menu.addAction(self.act_record_gif)

        self.act_record_mp4 = QAction("🎥 Record MP4 Video (60 FPS)", self, checkable=True)
        self.act_record_mp4.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.act_record_mp4.triggered.connect(self.toggle_clip_mp4)
        clip_menu.addAction(self.act_record_mp4)

        self.act_stop_clip = QAction("⏹ Stop Clip Recording", self)
        self.act_stop_clip.setEnabled(False)
        self.act_stop_clip.triggered.connect(self.stop_clip_recording)
        clip_menu.addAction(self.act_stop_clip)

        clip_menu.addSeparator()
        clip_scale_menu = clip_menu.addMenu("Clip Export Scale")
        self.clip_scale_group = QActionGroup(self)
        self.clip_scale_group.setExclusive(True)
        self.clip_scale_actions = {}

        for sc in range(1, 5):
            sc_act = QAction(f"{sc}x ({240*sc}×{160*sc})", self, checkable=True)
            sc_act.triggered.connect(lambda checked, s=sc: self.set_clip_scale(s))
            self.clip_scale_group.addAction(sc_act)
            clip_scale_menu.addAction(sc_act)
            self.clip_scale_actions[sc] = sc_act

        video_menu.addSeparator()

        scale_menu = video_menu.addMenu("Window Scale")
        self.scale_group = QActionGroup(self)
        self.scale_group.setExclusive(True)
        self.scale_actions = {}

        for s in range(1, 7):
            scale_act = QAction(f"{s}x Scale ({240*s}×{160*s})", self, checkable=True)
            scale_act.triggered.connect(lambda checked, sc=s: self.set_scale(sc))
            self.scale_group.addAction(scale_act)
            scale_menu.addAction(scale_act)
            self.scale_actions[s] = scale_act

        # 6. GAME STATE VIEWS MENU
        views_menu = menubar.addMenu("Game state views")

        dossier_act = QAction("🚀 Instant AI Scene Dump...", self)
        dossier_act.setShortcut(QKeySequence("Ctrl+Shift+D"))
        dossier_act.triggered.connect(self.trigger_instant_scene_dump)
        views_menu.addAction(dossier_act)
        views_menu.addSeparator()

        views_menu.addAction("View palettes...", lambda: self._open_dev_tool(PaletteViewerDialog))
        views_menu.addAction("View sprites...", lambda: self._open_dev_tool(SpriteViewerDialog))
        views_menu.addAction("View tiles...", lambda: self._open_dev_tool(TileViewerDialog))
        views_menu.addAction("View & edit map (SBB)...", lambda: self._open_dev_tool(MapViewerDialog))
        views_menu.addSeparator()
        views_menu.addAction("Frame inspector...", lambda: self._open_dev_tool(FrameInspectorDialog))

        hex_act = QAction("View memory (Hex Editor & Watch Table)...", self)
        hex_act.setShortcut(QKeySequence("Ctrl+H"))
        hex_act.triggered.connect(lambda: self._open_dev_tool(MemoryViewerDialog))
        views_menu.addAction(hex_act)

        views_menu.addAction("Search memory...", lambda: self._open_dev_tool(MemorySearchDialog))
        views_menu.addAction("View I/O registers...", lambda: self._open_dev_tool(IORegisterViewerDialog))

        # 7. TRANSLATION MENU
        translation_menu = menubar.addMenu("Translation")

        ai_trans_act = QAction("⚡ Translate Screen with AI (Hotkey: T)", self)
        ai_trans_act.setShortcut(QKeySequence("T"))
        ai_trans_act.triggered.connect(lambda: self.trigger_ai_translation(False))
        translation_menu.addAction(ai_trans_act)

        self.act_auto_translate = QAction("🤖 Auto-Translate on Dialogue Change", self, checkable=True)
        self.act_auto_translate.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.act_auto_translate.setChecked(bool(self.config.get("ai_translation", "auto_translate", False)))
        self.act_auto_translate.triggered.connect(self.toggle_auto_translate)
        translation_menu.addAction(self.act_auto_translate)

        self.act_trans_overlay = QAction("Fullscreen Floating Subtitle Overlay", self, checkable=True)
        self.act_trans_overlay.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.act_trans_overlay.setChecked(bool(self.config.get("ai_translation", "overlay_enabled", True)))
        self.act_trans_overlay.triggered.connect(self.toggle_translation_overlay)
        translation_menu.addAction(self.act_trans_overlay)

        clear_panel_act = QAction("Clear Subtitle Panel", self)
        clear_panel_act.triggered.connect(self.clear_translation_subtitles)
        translation_menu.addAction(clear_panel_act)

        glossary_act = QAction("Per-Game Glossary & Translation Cache...", self)
        glossary_act.setShortcut(QKeySequence("Ctrl+Shift+T"))
        glossary_act.triggered.connect(self.open_translation_glossary)
        translation_menu.addAction(glossary_act)

        ai_cfg_act = QAction("Configure AI Translation...", self)
        ai_cfg_act.triggered.connect(self.open_ai_translation_config)
        translation_menu.addAction(ai_cfg_act)

        translation_menu.addSeparator()

        text_inspector_act = QAction("Text Inspector & Script Auto-Repointer...", self)
        text_inspector_act.triggered.connect(self.open_translation_tools)
        translation_menu.addAction(text_inspector_act)

        font_tools_act = QAction("Font Studio & TTF Generator...", self)
        font_tools_act.triggered.connect(self.open_font_tools)
        translation_menu.addAction(font_tools_act)

        translation_menu.addSeparator()

        self.ruler_action = QAction("Screen Pixel Ruler Overlay", self, checkable=True)
        self.ruler_action.triggered.connect(self.toggle_pixel_ruler)
        translation_menu.addAction(self.ruler_action)

        # 8. TOOLS MENU
        tools_menu = menubar.addMenu("Tools")

        editor_act = QAction("In-Game Graphic Editor (YY-CHR)...", self)
        editor_act.setShortcut(QKeySequence("Ctrl+G"))
        editor_act.triggered.connect(self.open_graphic_editor)
        tools_menu.addAction(editor_act)

        hex_tool_act = QAction("Interactive Hex Editor & Watch Table...", self)
        hex_tool_act.triggered.connect(lambda: self._open_dev_tool(MemoryViewerDialog))
        tools_menu.addAction(hex_tool_act)

        sappy_act = QAction("🎵 Sappy (M4A) Music & Soundbank Extractor...", self)
        sappy_act.triggered.connect(self.open_sappy_extractor)
        tools_menu.addAction(sappy_act)

        tools_menu.addSeparator()
        tools_menu.addAction("Cheats Manager...", self.open_cheats_manager)

        self.act_netplay = QAction("🌐 Netplay & Link Cable...", self)
        self.act_netplay.triggered.connect(self.open_netplay_dialog)
        tools_menu.addAction(self.act_netplay)

        tools_menu.addSeparator()
        tools_menu.addAction("Configure Controls & Motion Feedback...", self.open_controller_config)

        profile_menu = tools_menu.addMenu("Per-Game Profile")

        self.use_profile_action = QAction("Use Per-Game Profile (No ROM)", self, checkable=True)
        self.use_profile_action.setEnabled(False)
        self.use_profile_action.triggered.connect(self.toggle_game_profile)
        profile_menu.addAction(self.use_profile_action)

        self.seed_profile_action = QAction("Copy Current Settings to Game Profile", self)
        self.seed_profile_action.setEnabled(False)
        self.seed_profile_action.triggered.connect(self.copy_global_to_game_profile)
        profile_menu.addAction(self.seed_profile_action)

        self.delete_profile_action = QAction("Delete Current Game Profile...", self)
        self.delete_profile_action.setEnabled(False)
        self.delete_profile_action.triggered.connect(self.delete_game_profile)
        profile_menu.addAction(self.delete_profile_action)

        stats_act = QAction("📊 Playtime & Game Statistics...", self)
        stats_act.triggered.connect(self.open_game_stats_dialog)
        tools_menu.addAction(stats_act)

        tools_menu.addSeparator()

        reset_settings_act = QAction("Reset Settings to Default...", self)
        reset_settings_act.triggered.connect(self.reset_settings_to_default)
        tools_menu.addAction(reset_settings_act)
```

---

#### 2. `gba_studio/ui/settings_actions.py`
```python
# settings_actions.py
from PyQt6.QtWidgets import QMessageBox

from .controller import ControllerConfigDialog
from ..tools.dev_tools import MemoryViewerDialog


class SettingsActionsMixin:
    def _sync_ui_with_config(self):
        self.mute_action.setChecked(bool(self.config.get("audio", "mute")))
        if hasattr(self, "ff_pitch_action"):
            self.ff_pitch_action.setChecked(
                bool(self.config.get("audio", "fast_forward_pitch_corrected", True))
            )

        lpf = str(self.config.get("audio", "low_pass_filter") or "medium").lower()
        if lpf in self.lpf_actions:
            self.lpf_actions[lpf].setChecked(True)
        self.widener_action.setChecked(bool(self.config.get("audio", "stereo_widener")))
        self.bass_action.setChecked(bool(self.config.get("audio", "bass_boost")))
        self.limiter_action.setChecked(bool(self.config.get("audio", "soft_limiter")))

        raw_vol = self.config.get("audio", "volume")
        current_vol = float(raw_vol) if raw_vol is not None else 0.5
        if current_vol > 2.0:
            current_vol = 0.5
            self.config.set("audio", "volume", 0.5)

        closest_vol = min(self.vol_actions.keys(), key=lambda v: abs(v - current_vol))
        self.vol_actions[closest_vol].setChecked(True)

        self.skip_bios_action.setChecked(bool(self.config.get("emulation", "skip_bios")))
        self.rewind_enabled_action.setChecked(bool(self.config.get("emulation", "rewind_enabled", True)))
        self.auto_patch_action.setChecked(bool(self.config.get("emulation", "auto_soft_patch", True)))
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.integer_scale_action.setChecked(bool(self.config.get("video", "integer_scaling", False)))
        self.input_overlay_action.setChecked(bool(self.config.get("video", "show_input_overlay", False)))
        self.act_trans_overlay.setChecked(bool(self.config.get("ai_translation", "overlay_enabled", True)))
        if hasattr(self, "act_auto_translate"):
            self.act_auto_translate.setChecked(bool(self.config.get("ai_translation", "auto_translate", False)))
        if hasattr(self, "_sync_auto_translate_timer"):
            self._sync_auto_translate_timer()
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))
        self.act_shader_toggle.setChecked(bool(self.config.get("shaders", "enabled", False)))

        clip_scale = int(self.config.get("video", "clip_scale", 2) or 2)
        if clip_scale in self.clip_scale_actions:
            self.clip_scale_actions[clip_scale].setChecked(True)

        self.current_slot = int(self.config.get("emulation", "state_slot") or 1)
        if self.current_slot in self.slot_actions:
            self.slot_actions[self.current_slot].setChecked(True)

        current_ff = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
        for spd, act in self.ff_actions.items():
            if abs(spd - current_ff) < 0.01:
                act.setChecked(True)
                break

        current_fs = int(self.config.get("video", "frameskip") or 0)
        if current_fs in self.fs_actions:
            self.fs_actions[current_fs].setChecked(True)

        if self.scale_factor in self.scale_actions:
            self.scale_actions[self.scale_factor].setChecked(True)

        solar_lvl = int(self.config.get("sensors", "solar_level") if self.config.get("sensors") else 5)
        if solar_lvl in self.solar_actions:
            self.solar_actions[solar_lvl].setChecked(True)
        self.sensor_hud.on_solar_updated(solar_lvl)

        if hasattr(self, "use_profile_action"):
            has_rom = bool(getattr(self, "worker", None) and getattr(self, "current_rom", None))
            active_pid = getattr(self.config, "active_profile_id", None)
            self.use_profile_action.blockSignals(True)
            self.use_profile_action.setEnabled(has_rom)
            self.use_profile_action.setChecked(bool(has_rom and active_pid))
            if has_rom:
                pid = self.worker.get_profile_id()
                self.use_profile_action.setText(f"Use Per-Game Profile [{pid}]")
                self.seed_profile_action.setEnabled(True)
                self.delete_profile_action.setEnabled(self.config.has_profile(pid))
            else:
                self.use_profile_action.setText("Use Per-Game Profile (No ROM)")
                self.seed_profile_action.setEnabled(False)
                self.delete_profile_action.setEnabled(False)
            self.use_profile_action.blockSignals(False)

        self._build_keymap()

    def toggle_ff_pitch_corrected(self):
        val = self.ff_pitch_action.isChecked()
        self.config.set("audio", "fast_forward_pitch_corrected", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Fast-Forward Pitch Correction: {'ON (WSOLA)' if val else 'OFF'}")

    def set_lpf_mode(self, mode: str):
        self.config.set("audio", "low_pass_filter", mode)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Low-Pass Filter: {mode.capitalize()}")

    def toggle_stereo_widener(self):
        val = self.widener_action.isChecked()
        self.config.set("audio", "stereo_widener", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Stereo Widener: {'ON' if val else 'OFF'}")

    def toggle_bass_boost(self):
        val = self.bass_action.isChecked()
        self.config.set("audio", "bass_boost", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Bass Boost: {'ON' if val else 'OFF'}")

    def toggle_soft_limiter(self):
        val = self.limiter_action.isChecked()
        self.config.set("audio", "soft_limiter", val)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Soft Limiter: {'ON' if val else 'OFF'}")

    def set_volume_gain(self, vol: float):
        self.config.set("audio", "volume", vol)
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd(f"Volume: {int(vol * 100)}%")

    def toggle_mute(self):
        self.config.set("audio", "mute", self.mute_action.isChecked())
        if self.worker:
            self.worker.update_audio_settings()
        self.video_widget.show_osd("Audio Muted" if self.mute_action.isChecked() else "Audio Unmuted")

    def reset_settings_to_default(self):
        confirm = QMessageBox.question(
            self, "Reset Settings",
            "Are you sure you want to reset all global settings, controls, sensors, shaders, and audio options back to default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.config.reset_to_defaults()
            self._apply_effective_config_to_runtime()
            self.update_recent_menu()
            if self.worker:
                self.on_game_identified(self.worker.game_title, self.worker.game_code, self.worker.rumble_type)
            self.video_widget.show_osd("Settings Reset to Default")
            QMessageBox.information(self, "Success", "All settings have been restored to factory defaults.")

    def _open_dev_tool(self, dialog_cls):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to inspect game state.")
            return
        self.active_tool_window = dialog_cls(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_tas_editor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to use the Piano-Roll TAS Editor.")
            return
        from ..tools.tas_editor import TASTimelineDialog
        self.active_tool_window = TASTimelineDialog(self, self)
        self.active_tool_window.show()

    def open_sappy_extractor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Load a ROM first to scan for Sappy / M4A music.")
            return
        from ..tools.sappy_extractor import SappyExtractorDialog
        self.active_tool_window = SappyExtractorDialog(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_netplay_dialog(self):
        from .netplay_dialog import NetplayLinkDialog
        self.active_tool_window = NetplayLinkDialog(self, self)
        self.active_tool_window.show()

    def open_game_stats_dialog(self):
        from .stats_dialog import GameStatsDialog
        self.active_tool_window = GameStatsDialog(self, self)
        self.active_tool_window.show()

    def open_graphic_editor(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to edit graphics.")
            return
        from ..tools.graphics.editor import GraphicEditorDialog
        self.active_tool_window = GraphicEditorDialog(self.worker, self.config, self)
        self.active_tool_window.show()

    def open_translation_tools(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to inspect memory.")
            return
        try:
            from ..tools.translation_dialog import TranslationToolsDialog
            self.active_tool_window = TranslationToolsDialog(self.worker, self.config, self)
            self.active_tool_window.show()
        except ImportError as e:
            QMessageBox.critical(self, "Error", f"Failed to load translation tools: {e}")

    def open_font_tools(self):
        if not self.worker:
            QMessageBox.warning(self, "Notice", "Emulation must be running to scan fonts.")
            return
        try:
            from ..tools.font_tools import FontToolsDialog
            self.active_tool_window = FontToolsDialog(self.worker, self.config, self)
            self.active_tool_window.show()
        except ImportError as e:
            QMessageBox.critical(self, "Error", f"Failed to load font tools: {e}")

    def open_cheats_manager(self):
        if not self.worker:
            QMessageBox.warning(self, "Error", "Emulation must be running to manage cheats.")
            return
        from ..core.cheats import CheatManagerWindow
        self.active_tool_window = CheatManagerWindow(self.worker.cheat_engine, self.config, self)
        self.active_tool_window.show()

    def open_controller_config(self):
        dlg = ControllerConfigDialog(self.config, self.controller_manager, self)
        if dlg.exec():
            self._build_keymap()

    def toggle_pixel_ruler(self):
        self.video_widget.ruler_enabled = self.ruler_action.isChecked()
        self.video_widget.show_osd("Pixel Ruler: " + ("ON" if self.ruler_action.isChecked() else "OFF"))
        self.video_widget.update()
```

---

#### 3. `gba_studio/ui/runtime_actions.py`
```python
# runtime_actions.py
import os
import shutil
import hashlib
import zipfile
import tempfile
import datetime

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from ..core.core_worker import EmulatorWorker
from ..core.rom_patcher import PatchError, find_matching_patch, prepare_patched_rom
from .controller import LIBRETRO_BTNS, QT_KEY_MAP

SUPPORTED_ROM_EXTS = (".gba", ".gb", ".gbc", ".sgb")


class RuntimeActionsMixin:
    def _build_keymap(self):
        self.keymap = {}
        self.autofire_keymap = {}
        keyboard_cfg = self.config.get("input", "keyboard") or {}

        for gba_btn, user_key in keyboard_cfg.items():
            if gba_btn in LIBRETRO_BTNS:
                qt_key = QT_KEY_MAP.get(str(user_key).lower())
                if qt_key:
                    self.keymap[qt_key] = LIBRETRO_BTNS[gba_btn]

        for af_btn in ["autofire_a", "autofire_b", "autofire_l", "autofire_r"]:
            user_key = keyboard_cfg.get(af_btn)
            if user_key:
                qt_key = QT_KEY_MAP.get(str(user_key).lower())
                if qt_key:
                    self.autofire_keymap[qt_key] = af_btn.replace("autofire_", "")

    def poll_gamepad(self):
        if self.worker and hasattr(self, "is_running") and self.is_running:
            self.controller_manager.poll_gamepad_events(self.worker)

    def _extract_zip_rom(self, zip_path: str) -> str:
        with zipfile.ZipFile(zip_path, "r") as zf:
            candidates = [
                zi for zi in zf.infolist()
                if not zi.is_dir()
                and not zi.filename.startswith("__MACOSX/")
                and zi.filename.lower().endswith(SUPPORTED_ROM_EXTS)
            ]
            if not candidates:
                raise ValueError("No supported ROM (.gba, .gb, .gbc, .sgb) found inside the ZIP archive.")

            candidates.sort(key=lambda zi: (0 if zi.filename.lower().endswith(".gba") else 1, zi.filename.lower()))
            target = candidates[0]

            zip_hash = hashlib.md5(os.path.abspath(zip_path).encode("utf-8")).hexdigest()[:12]
            cache_dir = os.path.join(tempfile.gettempdir(), "gba_studio_rom_cache", zip_hash)
            os.makedirs(cache_dir, exist_ok=True)

            safe_name = os.path.basename(target.filename)
            extracted_path = os.path.join(cache_dir, safe_name)

            if not os.path.exists(extracted_path) or os.path.getsize(extracted_path) != target.file_size:
                with zf.open(target, "r") as src, open(extracted_path, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        dst.write(chunk)

            return extracted_path

    def _extract_7z_rom(self, archive_path: str) -> str:
        try:
            import py7zr
        except ImportError as e:
            raise RuntimeError("Missing dependency 'py7zr'. Install it with: pip install py7zr") from e

        with py7zr.SevenZipFile(archive_path, mode="r") as sz:
            candidates = [
                fi for fi in sz.list()
                if not fi.is_directory
                and not fi.filename.startswith("__MACOSX/")
                and fi.filename.lower().endswith(SUPPORTED_ROM_EXTS)
            ]
            if not candidates:
                raise ValueError("No supported ROM (.gba, .gb, .gbc, .sgb) found inside the 7z archive.")

            candidates.sort(key=lambda fi: (0 if fi.filename.lower().endswith(".gba") else 1, fi.filename.lower()))
            target = candidates[0]

            arch_stat = os.stat(archive_path)
            key = f"{os.path.abspath(archive_path)}|{arch_stat.st_mtime_ns}|{arch_stat.st_size}"
            arch_hash = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
            cache_dir = os.path.join(tempfile.gettempdir(), "gba_studio_rom_cache", f"7z_{arch_hash}")
            os.makedirs(cache_dir, exist_ok=True)

            safe_name = os.path.basename(target.filename)
            extracted_path = os.path.join(cache_dir, safe_name)

            if not os.path.exists(extracted_path) or os.path.getsize(extracted_path) != target.uncompressed:
                stage_dir = os.path.join(cache_dir, "_stage")
                shutil.rmtree(stage_dir, ignore_errors=True)
                os.makedirs(stage_dir, exist_ok=True)
                try:
                    sz.extract(path=stage_dir, targets=[target.filename])
                    staged_file = os.path.join(stage_dir, target.filename)
                    if not os.path.exists(staged_file):
                        for root_dir, _, files in os.walk(stage_dir):
                            if safe_name in files:
                                staged_file = os.path.join(root_dir, safe_name)
                                break
                    if not os.path.exists(staged_file):
                        raise FileNotFoundError("Failed to locate extracted ROM inside 7z staging folder.")
                    shutil.move(staged_file, extracted_path)
                finally:
                    shutil.rmtree(stage_dir, ignore_errors=True)

            return extracted_path

    def load_rom(self):
        filters = (
            "Supported ROMs (*.gba *.gb *.gbc *.sgb *.zip *.7z);;"
            "GBA ROMs (*.gba);;GB/GBC ROMs (*.gb *.gbc);;"
            "Archives (*.zip *.7z);;All Files (*.*)"
        )
        filepath, _ = QFileDialog.getOpenFileName(self, "Load Game", "", filters)
        if filepath:
            self.load_rom_file(filepath)

    def load_rom_file(self, filepath: str):
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "File Error", f"ROM not found:\n{filepath}")
            return

        clean_path = filepath
        lower_path = filepath.lower()
        if lower_path.endswith(".zip"):
            try:
                clean_path = self._extract_zip_rom(filepath)
            except (zipfile.BadZipFile, ValueError, OSError) as e:
                QMessageBox.warning(self, "ZIP Error", f"Failed to load ZIP ROM:\n{e}")
                return
        elif lower_path.endswith(".7z"):
            try:
                clean_path = self._extract_7z_rom(filepath)
            except Exception as e:
                QMessageBox.warning(self, "7z Error", f"Failed to load 7z ROM:\n{e}")
                return

        self.source_rom = filepath
        self.clean_rom = clean_path
        self.active_patch = None
        boot_path = clean_path

        if bool(self.config.get("emulation", "auto_soft_patch", True)):
            patch_path = find_matching_patch(clean_path, source_path=filepath)
            if patch_path:
                try:
                    boot_path = prepare_patched_rom(clean_path, patch_path)
                    self.active_patch = patch_path
                except (PatchError, OSError) as e:
                    QMessageBox.warning(
                        self, "Soft-Patch Warning",
                        f"Found matching patch '{os.path.basename(patch_path)}' but failed to apply:\n{e}\n\nBooting unpatched ROM."
                    )

        self.current_rom = boot_path
        self.config.add_recent_file(filepath)
        self.update_recent_menu()
        self.start_emulation()

    def apply_patch_dialog(self):
        if not self.clean_rom or not os.path.exists(self.clean_rom):
            QMessageBox.information(self, "Notice", "Please load a ROM first before applying a soft-patch.")
            return

        start_dir = os.path.dirname(self.source_rom or self.clean_rom)
        patch_path, _ = QFileDialog.getOpenFileName(
            self, "Apply Soft-Patch", start_dir,
            "ROM Patches (*.ips *.bps *.ups);;IPS Patches (*.ips);;BPS Patches (*.bps);;UPS Patches (*.ups)"
        )
        if patch_path:
            self.apply_soft_patch(patch_path)

    def apply_soft_patch(self, patch_path: str):
        if not self.clean_rom or not os.path.exists(self.clean_rom):
            QMessageBox.information(self, "Notice", "Load a ROM first before applying a patch.")
            return

        try:
            patched_path = prepare_patched_rom(self.clean_rom, patch_path)
        except (PatchError, OSError) as e:
            QMessageBox.critical(self, "Patch Error", f"Failed to apply patch:\n{e}")
            return

        self.active_patch = patch_path
        self.current_rom = patched_path
        self.start_emulation()

    def start_emulation(self):
        if not os.path.exists(self.core_path):
            QMessageBox.critical(self, "Core Error", f"Core not found:\n{self.core_path}")
            return

        if hasattr(self, "_flush_playtime"):
            self._flush_playtime()
        self._active_stats_pid = None
        self._active_stats_title = ""
        self._base_playtime_sec = 0.0
        self._session_playtime_sec = 0.0
        self._unsaved_playtime_sec = 0.0
        self._active_launch_count = 0
        self._launch_stats_recorded = False

        if hasattr(self, "clip_recorder") and self.clip_recorder.is_recording:
            self.clip_recorder.stop()

        if self.worker:
            self.worker.stop()
            self.worker.wait()

        self.config.deactivate_profile()
        self.video_widget.set_input_hud("IDLE", 0, 0, 0)
        self.video_widget.clear_translation_boxes()

        save_base = os.path.splitext(os.path.basename(self.current_rom))[0]
        slot = int(getattr(self, "player_slot", 1) or 1)
        if slot > 1:
            save_base = f"{save_base}_p{slot}"

        self.worker = EmulatorWorker(
            self.core_path,
            self.current_rom,
            self.config,
            save_base_name=save_base,
            netplay=getattr(self, "netplay_engine", None),
        )

        profile_id = self.worker.get_profile_id()
        profile_loaded = self.config.activate_profile_if_exists(profile_id, title=self.worker.game_title)
        self._apply_effective_config_to_runtime()

        self.worker.frame_ready.connect(self.video_widget.set_frame)
        if hasattr(self, "clip_recorder"):
            self.worker.frame_ready.connect(self.clip_recorder.push_frame)
        self.worker.input_hud_updated.connect(self.video_widget.set_input_hud)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.replay_finished.connect(self.on_replay_finished)
        self.worker.rewind_status.connect(self.video_widget.show_osd)
        self.worker.watchpoint_hit.connect(self._on_watchpoint_hit)

        self.worker.rumble_triggered.connect(self.sensor_hud.on_rumble_triggered)
        self.worker.rumble_triggered.connect(self.controller_manager.trigger_rumble)
        self.worker.solar_changed.connect(self.sensor_hud.on_solar_updated)
        self.worker.tilt_changed.connect(self.sensor_hud.on_tilt_updated)
        self.worker.game_identified.connect(self.sensor_hud.on_game_identified)
        self.worker.game_identified.connect(self.on_game_identified)

        self.worker.fast_forward = self.ff_action.isChecked()
        self.worker.is_paused = self.pause_action.isChecked()

        self.worker.start()
        self.is_running = True

        if hasattr(self, "netplay_engine") and self.netplay_engine:
            self.netplay_engine.set_local_rom_info(self.worker.game_title, self.worker.game_code)

        osd_parts = []
        if self.active_patch:
            osd_parts.append(f"🩹 {os.path.basename(self.active_patch)}")
        if profile_loaded:
            osd_parts.append(f"🎮 Profile: {profile_id}")
        if slot > 1:
            osd_parts.append(f"🔗 Peer {slot}")
        if osd_parts:
            self.video_widget.show_osd(" | ".join(osd_parts), 3500)

    def _on_netplay_state_received(self, raw_state: bytes):
        if self.worker and raw_state:
            ok = self.worker.unserialize_state_bytes(raw_state)
            if ok:
                self.worker.replay_frame_counter = 0
                self.video_widget.show_osd("🤝 Netplay State Synced!", 2000)

    def _on_watchpoint_hit(self, hit_info: dict):
        if hasattr(self, "pause_action"):
            self.pause_action.setChecked(True)
        self.video_widget.show_osd(f"🎯 Watchpoint Hit: 0x{hit_info['address']:08X}", 3000)
        from ..tools.watch_table import WatchpointHitDialog
        self._active_wp_dialog = WatchpointHitDialog(hit_info, self.worker, self)
        self._active_wp_dialog.show()

    def reset_emulation(self):
        if self.worker:
            self.worker.request_reset()
            self.video_widget.show_osd("Reset")

    def step_frame(self):
        if self.worker:
            self.worker.step_one_frame()
            self.pause_action.setChecked(True)
            self.video_widget.show_osd("Stepped 1 Frame")

    def handle_error(self, err_msg):
        QMessageBox.warning(self, "Emulation Error", err_msg)

    def toggle_pause(self):
        if self.worker:
            self.worker.is_paused = self.pause_action.isChecked()
            self.video_widget.show_osd("Paused" if self.worker.is_paused else "Resumed")

    def toggle_ff(self):
        if self.worker:
            self.worker.fast_forward = self.ff_action.isChecked()
            spd = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
            state_txt = f"ON ({spd}x)" if self.worker.fast_forward else "OFF"
            self.video_widget.show_osd(f"Fast Forward: {state_txt}")

    def cycle_ff_speed(self, forward: bool = True):
        speeds = [1.5, 2.0, 3.0, 4.0, 8.0, 10.0]
        cur = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
        closest_idx = min(range(len(speeds)), key=lambda i: abs(speeds[i] - cur))

        if forward:
            new_idx = min(len(speeds) - 1, closest_idx + 1)
        else:
            new_idx = max(0, closest_idx - 1)

        new_spd = speeds[new_idx]
        self.set_ff_speed(new_spd)
        if not self.ff_action.isChecked():
            self.ff_action.setChecked(True)
            self.toggle_ff()

    def toggle_skip_bios(self):
        enabled = self.skip_bios_action.isChecked()
        self.config.set("emulation", "skip_bios", enabled)
        self.video_widget.show_osd(f"Skip BIOS: {'ON' if enabled else 'OFF'} (Applies on next load)")

    def set_ff_speed(self, ratio: float):
        self.config.set("emulation", "fast_forward_ratio", ratio)
        if ratio in self.ff_actions:
            self.ff_actions[ratio].setChecked(True)
        self.video_widget.show_osd(f"Fast Forward Speed: {ratio}x")

    def toggle_interframe_blending(self):
        enabled = self.blend_action.isChecked()
        self.config.set("video", "interframe_blending", enabled)
        self.video_widget.show_osd(f"Interframe Blending: {'ON' if enabled else 'OFF'}")

    def set_frameskip(self, count: int):
        self.config.set("video", "frameskip", count)
        self.video_widget.show_osd(f"Frameskip: {count}")

    def set_active_slot(self, slot: int):
        self.current_slot = slot
        self.config.set("emulation", "state_slot", slot)
        if hasattr(self, "slot_actions") and slot in self.slot_actions:
            self.slot_actions[slot].setChecked(True)
        self.video_widget.show_osd(f"Selected State Slot {slot}")

    def take_screenshot(self):
        if not self.worker or self.video_widget.current_frame_data is None:
            return
        out_dir = self.config.get("paths", "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        filename = f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        from PIL import Image
        Image.fromarray(self.video_widget.current_frame_data, "RGB").save(os.path.join(out_dir, filename))
        self.video_widget.show_osd("Screenshot Captured!")

    def get_state_path(self, slot: int) -> str | None:
        if not self.current_rom:
            return None
        state_dir = self.config.get("paths", "states") or "states"
        os.makedirs(state_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(self.current_rom))[0]
        return os.path.join(state_dir, f"{base_name}.ss{slot}")

    def quick_save_state(self, slot: int):
        if not self.worker or not self.current_rom:
            return
        state_path = self.get_state_path(slot)
        if state_path and self.worker.save_state(state_path):
            frame = getattr(self.worker, "latest_video_frame", None)
            if frame is None:
                frame = self.video_widget.current_frame_data
            if frame is not None:
                try:
                    from PIL import Image
                    Image.fromarray(frame, "RGB").save(state_path + ".png", "PNG")
                except Exception as e:
                    print(f"[STATE THUMBNAIL WARNING] {e}")
            self.video_widget.show_osd(f"Saved to Slot {slot}")

    def quick_load_state(self, slot: int):
        if not self.worker or not self.current_rom:
            return
        path = self.get_state_path(slot)
        if path and os.path.exists(path):
            self.worker.load_state(path)
            self.video_widget.show_osd(f"Loaded from Slot {slot}")
        else:
            self.video_widget.show_osd(f"Slot {slot} is empty!")

    def save_state_to_file(self):
        if not self.worker or not self.current_rom:
            return
        state_dir = self.config.get("paths", "states")
        os.makedirs(state_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(self.current_rom))[0]
        default_path = os.path.join(state_dir, f"{base_name}.state")
        filepath, _ = QFileDialog.getSaveFileName(self, "Save State to File", default_path, "Save States (*.state *.ss*)")
        if filepath:
            self.worker.save_state(filepath)
            self.video_widget.show_osd(f"Saved: {os.path.basename(filepath)}")

    def load_state_from_file(self):
        if not self.worker or not self.current_rom:
            return
        state_dir = self.config.get("paths", "states")
        filepath, _ = QFileDialog.getOpenFileName(self, "Load State from File", state_dir, "Save States (*.state *.ss*)")
        if filepath:
            self.worker.load_state(filepath)
            self.video_widget.show_osd(f"Loaded: {os.path.basename(filepath)}")
```

---

#### 4. `gba_studio/ui/main_window.py`
```python
# main_window.py
import os
import sys

from PyQt6.QtWidgets import QMainWindow, QTextEdit, QSplitter, QSizePolicy, QMessageBox
from PyQt6.QtCore import Qt, QTimer, QEvent

from ..core.clip_recorder import ClipRecorder
from ..core.netplay import NetplayEngine
from .controller import ControllerManager
from ..tools.dev_tools import MemoryViewerDialog
from ..tools.ai_translator import AutoTranslateDetector
from .shader_manager import RetroArchShaderManager
from ..paths import PROJECT_ROOT

from .video_widget import VideoGLWidget
from .sensor_hud import SensorHudWidget

from .menu_actions import MenuBuilderMixin
from .application_actions import ApplicationActionsMixin
from .settings_actions import SettingsActionsMixin
from .runtime_actions import RuntimeActionsMixin

ROM_DROP_EXTS = {".gba", ".gb", ".gbc", ".sgb", ".zip", ".7z"}
PATCH_DROP_EXTS = {".ips", ".bps", ".ups"}


class MainWindow(QMainWindow, MenuBuilderMixin, ApplicationActionsMixin, SettingsActionsMixin, RuntimeActionsMixin):
    if sys.platform.startswith("win"):
        DEFAULT_CORE = "mgba_libretro.dll"
    elif sys.platform.startswith("linux"):
        DEFAULT_CORE = "mgba_libretro.so"
    else:
        DEFAULT_CORE = "mgba_libretro.dylib"

    def __init__(self, config, player_slot: int = 1):
        super().__init__()
        self.config = config
        self.player_slot = max(1, int(player_slot))
        self.worker = None
        self.current_rom = None
        self.clean_rom = None
        self.source_rom = None
        self.active_patch = None

        saved_scale = self.config.get("video", "scale")
        try:
            self.scale_factor = max(1, min(6, int(saved_scale))) if saved_scale is not None else 3
        except (ValueError, TypeError):
            self.scale_factor = 3

        self.current_slot = self.config.get("emulation", "state_slot") or 1
        self.active_tool_window = None
        self.trans_worker = None
        self._auto_paused = False

        # Per-Game Playtime & Stats State
        self._active_stats_pid = None
        self._active_stats_title = ""
        self._base_playtime_sec = 0.0
        self._session_playtime_sec = 0.0
        self._unsaved_playtime_sec = 0.0
        self._active_launch_count = 0
        self._launch_stats_recorded = False
        self._playtime_timer = QTimer(self)
        self._playtime_timer.setInterval(1000)
        self._playtime_timer.timeout.connect(self._on_playtime_tick)

        # Smart Auto-Translate State
        self.auto_trans_detector = AutoTranslateDetector()
        self._auto_cache_store = None
        self._auto_cache_key = None
        self._last_trans_was_auto = False
        self._last_trans_frame = None
        self.auto_trans_timer = QTimer(self)
        self.auto_trans_timer.setInterval(150)
        self.auto_trans_timer.timeout.connect(self._poll_auto_translate)

        # Fullscreen restoration cache
        self._pre_fs_geometry = None
        self._pre_fs_splitter = None
        self._pre_fs_hud_visible = None
        self._pre_fs_trans_visible = None

        slot_tag = f" [Peer {self.player_slot}]" if self.player_slot > 1 else ""
        self.setWindowTitle(f"mGBA (PyQt6 Replica){slot_tag}")
        self.setAcceptDrops(True)

        script_dir = str(PROJECT_ROOT)
        self.core_path = os.path.join(script_dir, self.DEFAULT_CORE)

        # Initialize RetroArch Shader Engine & Netplay/Link Engine
        self.shader_manager = RetroArchShaderManager(self.config)
        self.netplay_engine = NetplayEngine(self)
        self.netplay_engine.state_sync_received.connect(self._on_netplay_state_received)
        self.netplay_engine.status_changed.connect(
            lambda code, msg: self.video_widget.show_osd(f"🌐 {code}: {msg}", 2500)
        )
        self.netplay_engine.error_occurred.connect(
            lambda err: self.video_widget.show_osd(f"🌐 Netplay Error: {err}", 3500)
        )

        self.controller_manager = ControllerManager(self.config)
        self.gamepad_timer = QTimer(self)
        self.gamepad_timer.timeout.connect(self.poll_gamepad)
        self.gamepad_timer.start(8)

        # Vertical Splitter: OpenGL Video -> HUD Panel -> Subtitle Panel
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setHandleWidth(3)
        self.splitter.setStyleSheet("QSplitter::handle { background-color: #282828; }")
        self.splitter.splitterMoved.connect(self.on_splitter_moved)

        # 1. Hardware-Accelerated OpenGL Video Surface
        self.video_widget = VideoGLWidget(self.shader_manager)
        self.splitter.addWidget(self.video_widget)

        # Initialize Clip Recorder (GIF / MP4)
        self.clip_recorder = ClipRecorder(self)
        self.clip_recorder.status_changed.connect(self.video_widget.set_clip_status)
        self.clip_recorder.export_finished.connect(self.on_clip_export_finished)
        self.clip_recorder.export_error.connect(self.on_clip_export_error)
        self.clip_recorder.recording_stopped.connect(self._sync_clip_menu_idle)

        # 2. Separated Sensor & Rumble HUD Panel
        self.sensor_hud = SensorHudWidget()
        self.sensor_hud.setAcceptDrops(False)
        show_hud_pref = bool(self.config.get("sensors", "show_hud", True))
        self.sensor_hud.setVisible(show_hud_pref)
        self.splitter.addWidget(self.sensor_hud)

        # 3. Translation Subtitle Panel
        self.translation_panel = QTextEdit()
        self.translation_panel.setReadOnly(True)
        self.translation_panel.setAcceptDrops(False)
        self.translation_panel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.translation_panel.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.translation_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.translation_panel.setMinimumSize(0, 50)
        self.translation_panel.setStyleSheet("""
            QTextEdit {
                background-color: #101216;
                color: #E0E0E0;
                font-family: 'Segoe UI', 'Meiryo', sans-serif;
                font-size: 13px;
                border: none;
                padding: 6px 10px;
                line-height: 1.4;
            }
        """)
        self.translation_panel.setPlaceholderText("Press [T] to translate screen with AI... (or drag & drop ROM / Patch / State / Shader / Replay here)")
        self.splitter.addWidget(self.translation_panel)

        self.splitter.setStretchFactor(0, 8)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 2)

        saved_w = self.config.get("video", "window_width")
        saved_h = self.config.get("video", "window_height")
        saved_splitter = self.config.get("video", "splitter_sizes")

        default_hud_h = 30 if show_hud_pref else 0
        default_panel_h = 75 if self.scale_factor <= 2 else 115
        default_vid_h = 160 * self.scale_factor
        default_vid_w = 240 * self.scale_factor

        if saved_w and saved_h:
            try:
                self.resize(int(saved_w), int(saved_h))
            except Exception:
                self.resize(default_vid_w, default_vid_h + default_hud_h + default_panel_h)
        else:
            self.resize(default_vid_w, default_vid_h + default_hud_h + default_panel_h)

        if saved_splitter and isinstance(saved_splitter, list) and len(saved_splitter) == 3:
            try:
                sz = [int(s) for s in saved_splitter]
                if not show_hud_pref:
                    sz[1] = 0
                elif sz[1] <= 0:
                    sz[1] = 30
                self.splitter.setSizes(sz)
            except Exception:
                self.splitter.setSizes([default_vid_h, default_hud_h, default_panel_h])
        else:
            self.splitter.setSizes([default_vid_h, default_hud_h, default_panel_h])

        self.setCentralWidget(self.splitter)

        self._create_menus()
        self._sync_ui_with_config()
        self.update_recent_menu()
        self.video_widget.setFocus()

    # =========================================================================
    # PER-GAME PLAYTIME TRACKING
    # =========================================================================
    def _on_playtime_tick(self):
        if (
            not self._active_stats_pid
            or not self.worker
            or not getattr(self, "is_running", False)
            or self.worker.is_paused
            or self.worker.is_rewinding
        ):
            return

        self._session_playtime_sec += 1.0
        self._unsaved_playtime_sec += 1.0
        total_now = self._base_playtime_sec + self._session_playtime_sec
        self.sensor_hud.on_playtime_updated(total_now, self._active_launch_count)

        if self._unsaved_playtime_sec >= 15.0:
            self._flush_playtime()

    def _flush_playtime(self):
        if not self._active_stats_pid or self._unsaved_playtime_sec <= 0.0:
            return
        stats = self.config.add_game_playtime(
            self._active_stats_pid,
            self._unsaved_playtime_sec,
            title=self._active_stats_title,
            session_total=self._session_playtime_sec,
        )
        self._base_playtime_sec = max(
            0.0,
            float(stats.get("playtime_seconds", 0.0) or 0.0) - self._session_playtime_sec,
        )
        self._unsaved_playtime_sec = 0.0

    # =========================================================================
    # DRAG-AND-DROP SUPPORT
    # =========================================================================
    def _classify_dropped_file(self, path: str) -> str | None:
        if not path or not os.path.isfile(path):
            return None
        lower = path.lower()
        ext = os.path.splitext(lower)[1]

        if ext in ROM_DROP_EXTS:
            return "rom"
        if ext in PATCH_DROP_EXTS:
            return "patch"
        if ext == ".state" or (ext.startswith(".ss") and ext[3:].isdigit()):
            return "state"
        if ext == ".glslp":
            return "shader_preset"
        if ext == ".glsl":
            return "shader_pass"
        if lower.endswith(".replay.json") or ext == ".json":
            return "replay"
        if ext == ".cht":
            return "cheat"
        return None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and self._classify_dropped_file(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            kind = self._classify_dropped_file(path)
            if not kind:
                continue

            event.acceptProposedAction()
            self.activateWindow()
            self.video_widget.setFocus()

            if kind == "rom":
                self.load_rom_file(path)
            elif kind == "patch":
                self.apply_soft_patch(path)
            elif kind == "state":
                if self.worker:
                    ok = self.worker.load_state(path)
                    self.video_widget.show_osd(
                        f"Loaded State: {os.path.basename(path)}" if ok else "Failed to load state!"
                    )
                else:
                    QMessageBox.information(self, "Notice", "Load a ROM first before dropping a save state.")
            elif kind == "shader_preset":
                if self.shader_manager.load_preset_file(path):
                    self.shader_manager.enabled = True
                    self.act_shader_toggle.setChecked(True)
                    self.config.set("shaders", "enabled", True)
                    self.config.save()
                    self.video_widget.update()
                    self.video_widget.show_osd(f"Shader Preset: {os.path.basename(path)}")
                else:
                    self.video_widget.show_osd("Failed to load shader preset")
            elif kind == "shader_pass":
                if self.shader_manager.load_glsl_file(path, prepend=False):
                    self.shader_manager.enabled = True
                    self.act_shader_toggle.setChecked(True)
                    self.config.set("shaders", "enabled", True)
                    self.config.save()
                    self.video_widget.update()
                    self.video_widget.show_osd(f"Appended Shader: {os.path.basename(path)}")
                else:
                    self.video_widget.show_osd("Failed to compile shader")
            elif kind == "replay":
                if self.worker:
                    if self.worker.start_replay_session(path):
                        self.video_widget.show_osd(f"▶ REPLAYING: {os.path.basename(path)}", 3000)
                    else:
                        QMessageBox.warning(
                            self, "Replay Error",
                            "Failed to load replay. Ensure the corresponding .state file exists."
                        )
                else:
                    QMessageBox.information(self, "Notice", "Load a ROM first before dropping a replay file.")
            elif kind == "cheat":
                if self.worker:
                    if self.worker.cheat_engine.load_cht_file(path):
                        self.video_widget.show_osd(f"Loaded Cheats: {os.path.basename(path)}")
                    else:
                        self.video_widget.show_osd("Failed to load cheat file")
                else:
                    QMessageBox.information(self, "Notice", "Load a ROM first before dropping a cheat file.")
            return

        event.ignore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.isMaximized() and not self.isMinimized() and not self.isFullScreen():
            self.config.set("video", "window_width", self.width())
            self.config.set("video", "window_height", self.height())
            if hasattr(self, "splitter"):
                self.config.set("video", "splitter_sizes", self.splitter.sizes())

    def on_splitter_moved(self, pos, index):
        if not self.isMaximized() and not self.isMinimized() and not self.isFullScreen():
            self.config.set("video", "splitter_sizes", self.splitter.sizes())

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            pause_enabled = bool(self.config.get("emulation", "pause_on_unfocus", True))
            if getattr(self, "netplay_engine", None) and self.netplay_engine.is_connected:
                pause_enabled = False
            if not self.isActiveWindow():
                if self.worker and getattr(self.worker, "is_rewinding", False):
                    self.worker.set_rewinding(False)
                if pause_enabled and self.worker and hasattr(self, "is_running") and self.is_running and not self.worker.is_paused:
                    self._auto_paused = True
                    self.worker.is_paused = True
                    self.pause_action.setChecked(True)
                    self.video_widget.show_osd("⏸ Auto-Paused (Window Inactive)", 1500)
            else:
                if getattr(self, "_auto_paused", False):
                    self._auto_paused = False
                    if self.worker and hasattr(self, "is_running") and self.is_running:
                        self.worker.is_paused = False
                        self.pause_action.setChecked(False)
                        self.video_widget.show_osd("▶ Resumed", 1000)
        super().changeEvent(event)

    def on_manual_rumble_test(self):
        self.sensor_hud.on_rumble_triggered(1.0, 1.0, 300)

    def on_game_identified(self, title: str, code: str, hw_type: str):
        if self.worker and not self._launch_stats_recorded:
            self._launch_stats_recorded = True
            pid = self.worker.get_profile_id()
            self._active_stats_pid = pid
            self._active_stats_title = title or pid
            stats = self.config.record_game_launch(pid, title=self._active_stats_title)
            self._base_playtime_sec = float(stats.get("playtime_seconds", 0.0) or 0.0)
            self._session_playtime_sec = 0.0
            self._unsaved_playtime_sec = 0.0
            self._active_launch_count = int(stats.get("launch_count", 1) or 1)
            self.sensor_hud.on_playtime_updated(self._base_playtime_sec, self._active_launch_count)
            if not self._playtime_timer.isActive():
                self._playtime_timer.start()

        display_title = f"mGBA - {title} [{code}]"
        if self.player_slot > 1:
            display_title += f" [Peer {self.player_slot}]"
        if hw_type != "None":
            display_title += f" ({hw_type.replace('_', ' ').title()})"
        if self.active_patch:
            display_title += f" [Patched: {os.path.basename(self.active_patch)}]"
        if getattr(self.config, "active_profile_id", None):
            display_title += " [Profile]"
        self.setWindowTitle(display_title)
        if not self.active_patch and not getattr(self.config, "active_profile_id", None):
            self.video_widget.show_osd(f"Loaded: {title} [{code}]", 2500)

    def keyPressEvent(self, event):
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and (event.modifiers() & Qt.KeyboardModifier.AltModifier)
            and not event.isAutoRepeat()
        ) or (event.key() == Qt.Key.Key_F11 and not event.isAutoRepeat()):
            self.toggle_fullscreen()
            return

        if event.key() == Qt.Key.Key_Escape and self.isFullScreen() and not event.isAutoRepeat():
            self.toggle_fullscreen()
            return

        if self.isFullScreen() and not event.isAutoRepeat():
            cs_mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
            if event.modifiers() == cs_mods:
                if event.key() == Qt.Key.Key_G:
                    self.toggle_clip_gif()
                    return
                if event.key() == Qt.Key.Key_M:
                    self.toggle_clip_mp4()
                    return
                if event.key() == Qt.Key.Key_I:
                    self.input_overlay_action.trigger()
                    return
                if event.key() == Qt.Key.Key_O:
                    self.act_trans_overlay.trigger()
                    return

        if not self.worker:
            return

        if (
            event.key() in (Qt.Key.Key_Space, Qt.Key.Key_QuoteLeft)
            and event.key() not in self.keymap
            and event.key() not in self.autofire_keymap
            and not event.isAutoRepeat()
        ):
            self.worker.set_rewinding(True)
            if bool(self.config.get("emulation", "rewind_enabled", True)):
                self.video_widget.show_osd("⏪ Rewinding...", 1500)
            return

        if event.key() in [Qt.Key.Key_Equal, Qt.Key.Key_Plus] and not event.isAutoRepeat():
            if not self.ff_action.isChecked():
                self.ff_action.setChecked(True)
                self.toggle_ff()
            else:
                self.cycle_ff_speed(forward=True)
            return

        if event.key() in [Qt.Key.Key_Minus, Qt.Key.Key_Underscore] and not event.isAutoRepeat():
            if not self.ff_action.isChecked():
                self.ff_action.setChecked(True)
                self.toggle_ff()
            else:
                cur_ratio = float(self.config.get("emulation", "fast_forward_ratio") or 2.0)
                if abs(cur_ratio - 1.5) < 0.01:
                    self.ff_action.setChecked(False)
                    self.toggle_ff()
                else:
                    self.cycle_ff_speed(forward=False)
            return

        if event.key() == Qt.Key.Key_BracketRight:
            self.adjust_solar_sensor(1)
            return
        if event.key() == Qt.Key.Key_BracketLeft:
            self.adjust_solar_sensor(-1)
            return

        if event.key() == Qt.Key.Key_Q:
            new_ang = (self.sensor_hud.current_gyro_angle - 15) % 360
            self.worker.set_gyro_angle(new_ang)
            return
        if event.key() == Qt.Key.Key_E:
            new_ang = (self.sensor_hud.current_gyro_angle + 15) % 360
            self.worker.set_gyro_angle(new_ang)
            return
        if event.key() == Qt.Key.Key_Backspace:
            self.reset_sensors()
            return

        if event.key() == Qt.Key.Key_T and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.trigger_ai_translation(False)
            return

        if (
            event.key() == Qt.Key.Key_D
            and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        ):
            self.trigger_instant_scene_dump()
            return

        if event.key() == Qt.Key.Key_G and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.open_graphic_editor()
            return

        if event.key() == Qt.Key.Key_H and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._open_dev_tool(MemoryViewerDialog)
            return

        if event.key() == Qt.Key.Key_Tab and not event.isAutoRepeat():
            self.ff_action.setChecked(True)
            self.toggle_ff()
            return

        if event.key() == Qt.Key.Key_R and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.reset_emulation()
            return

        if event.key() == Qt.Key.Key_N and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.step_frame()
            return

        if event.key() == Qt.Key.Key_F10:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.quick_save_state(self.current_slot)
            else:
                self.quick_load_state(self.current_slot)
            return

        if Qt.Key.Key_F1 <= event.key() <= Qt.Key.Key_F9:
            slot = event.key() - Qt.Key.Key_F1 + 1
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.quick_save_state(slot)
            else:
                self.quick_load_state(slot)
            return

        af_btn = self.autofire_keymap.get(event.key())
        if af_btn and not event.isAutoRepeat():
            self.worker.set_autofire(af_btn, True)
            return

        btn_id = self.keymap.get(event.key())
        if btn_id is not None and not event.isAutoRepeat():
            self.worker.set_input(btn_id, True)

    def keyReleaseEvent(self, event):
        if not self.worker:
            return

        if (
            event.key() in (Qt.Key.Key_Space, Qt.Key.Key_QuoteLeft)
            and event.key() not in self.keymap
            and event.key() not in self.autofire_keymap
            and not event.isAutoRepeat()
        ):
            if self.worker.is_rewinding:
                self.worker.set_rewinding(False)
                self.video_widget.show_osd("⏸ Paused" if self.worker.is_paused else "▶ Resumed", 1000)
            return

        if event.key() == Qt.Key.Key_Tab and not event.isAutoRepeat():
            self.ff_action.setChecked(False)
            self.toggle_ff()
            return

        af_btn = self.autofire_keymap.get(event.key())
        if af_btn and not event.isAutoRepeat():
            self.worker.set_autofire(af_btn, False)
            return

        btn_id = self.keymap.get(event.key())
        if btn_id is not None and not event.isAutoRepeat():
            self.worker.set_input(btn_id, False)

    def closeEvent(self, event):
        self.is_running = False
        if hasattr(self, "_playtime_timer"):
            self._playtime_timer.stop()
        self._flush_playtime()

        if hasattr(self, "gamepad_timer"):
            self.gamepad_timer.stop()

        if hasattr(self, "auto_trans_timer"):
            self.auto_trans_timer.stop()

        if hasattr(self, "netplay_engine") and self.netplay_engine:
            self.netplay_engine.stop()

        if hasattr(self, "clip_recorder"):
            if self.clip_recorder.is_recording:
                self.clip_recorder.stop(wait=True)
            for th in list(self.clip_recorder._export_threads):
                th.wait()

        if self.worker:
            self.worker.set_rewinding(False)
            self.worker.stop()
            self.worker.wait()

        if not self.isMaximized() and not self.isMinimized() and not self.isFullScreen():
            self.config.set("video", "window_width", self.width())
            self.config.set("video", "window_height", self.height())
            if hasattr(self, "splitter"):
                self.config.set("video", "splitter_sizes", self.splitter.sizes())
            self.config.set("sensors", "show_hud", self.sensor_hud.isVisible())
        elif self._pre_fs_hud_visible is not None:
            self.config.set("sensors", "show_hud", self._pre_fs_hud_visible)

        self.config.set("video", "scale", self.scale_factor)
        self.config.set("shaders", "enabled", self.shader_manager.enabled)
        self.shader_manager.save_chain_to_config()
        self.config.save()
        event.accept()
```
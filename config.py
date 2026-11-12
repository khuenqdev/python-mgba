# config.py
import os
import json
import copy

CONFIG_FILE = "mgba_config.json"

DEFAULT_CONFIG = {
    "audio": {
        "mute": False,
        "volume": 0.5,
        "low_pass_filter": "medium",
        "stereo_widener": True,
        "bass_boost": False,
        "soft_limiter": True
    },
    "emulation": {
        "skip_bios": True,
        "fast_forward_ratio": 2.0,
        "state_slot": 1,
        "pause_on_unfocus": True
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
        "scale": 3,
        "window_width": 720,
        "window_height": 649,
        "splitter_sizes": [480, 34, 135]
    },
    "ai_translation": {
        "source_lang": "ja",
        "target_lang": "vi",
        "overlay_enabled": True
    },
    "recent_files": [],
    "paths": {
        "screenshots": "screenshots",
        "saves": "saves",
        "states": "states",
        "cheats": "cheats",
        "exports": "exports",
        "translations": "translations",
        "replays": "replays"
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
        self.settings = copy.deepcopy(DEFAULT_CONFIG)
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

                    self._update_dict(self.settings, user_config)
            except Exception as e:
                print(f"Config load error: {e}")
        self.save()

    def _update_dict(self, base, new):
        for k, v in new.items():
            if isinstance(v, dict) and k in base:
                self._update_dict(base[k], v)
            else:
                base[k] = v

    def save(self):
        for path_name, path_val in self.settings.get("paths", {}).items():
            os.makedirs(path_val, exist_ok=True)

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Config save error: {e}")

    def reset_to_defaults(self):
        self.settings = copy.deepcopy(DEFAULT_CONFIG)
        self.save()

    def get(self, section, key=None, default=None):
        """Safely retrieves config values, accepting an optional default argument."""
        if key is None:
            return self.settings.get(section, default)
        sec = self.settings.get(section)
        if isinstance(sec, dict):
            return sec.get(key, default)
        return default

    def set(self, section, key, value):
        if section not in self.settings:
            self.settings[section] = {}
        self.settings[section][key] = value
        self.save()

    def get_recent_files(self):
        recent = self.settings.get("recent_files", [])
        return [f for f in recent if os.path.exists(f)]

    def add_recent_file(self, filepath: str):
        if not filepath: return
        filepath = os.path.abspath(filepath)
        recent = self.settings.get("recent_files", [])
        if filepath in recent:
            recent.remove(filepath)
        recent.insert(0, filepath)
        self.settings["recent_files"] = recent[:10]
        self.save()

    def clear_recent_files(self):
        self.settings["recent_files"] = []
        self.save()

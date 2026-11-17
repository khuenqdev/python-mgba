Below is the complete integration of the **Hugging Face Space (`brianknight144/Qwen3.8-27B-Uncensored-Demo`)** via `gradio_client` with **higher priority than Groq** across vision translation, per-game localization criteria, text fallback, configuration, and UI settings.

The response is split into 4 parts containing the full updated files:
- **Part 1:** Dependencies & Configuration (`requirements.txt`, `.env.sample`, `gba_studio/core/config.py`)
- **Part 2:** Lazy Gradio Client & Text Translation (`gba_studio/tools/ai/engines.py`, `gba_studio/tools/ai/local.py`)
- **Part 3:** Multimodal Vision & Priority Pipeline (`gba_studio/tools/ai/vision.py`, `gba_studio/tools/ai/worker.py`)
- **Part 4:** Configuration Dialogs (`gba_studio/tools/ai/glossary_dialog.py`, `gba_studio/ui/ra_dialog.py`)

---

## Part 1: Dependencies & Configuration

### 1.1 `requirements.txt`
```text
# =============================================================================
# Core GUI & Video/Audio Processing
# =============================================================================
PyQt6>=6.5.0
numpy>=1.24.0
Pillow>=10.0.0
opencv-python>=4.8.0
sounddevice>=0.4.6
pygame>=2.5.0
pdretro>=0.2.0
py7zr>=0.20.0

# =============================================================================
# Security & Environment
# =============================================================================
python-dotenv>=1.0.0

# =============================================================================
# Cloud AI Vision / Gradio Space Client
# =============================================================================
gradio_client>=1.0.0

# =============================================================================
# Primary OCR: MeikiOCR (Specialized Retro Game Japanese OCR)
# =============================================================================
meikiocr>=0.1.0
onnxruntime>=1.16.0
# For NVIDIA GPU acceleration with ONNX (optional):
# onnxruntime-gpu>=1.16.0

# =============================================================================
# Fallback OCR: PaddleOCR & EasyOCR
# =============================================================================
paddleocr>=2.7.0
# Pin paddlepaddle <= 3.2.2 to prevent the CPU oneDNN PIR instruction converter crash
paddlepaddle>=2.5.0,<=3.2.2
easyocr>=1.7.0

# =============================================================================
# Local LLM Inference Engine (Qwen 14B / 7B GGUF)
# =============================================================================
llama-cpp-python>=0.2.80

# =============================================================================
# ROM Forensics & Disassembly (Optional Scene Dossier Tools)
# =============================================================================
capstone>=5.0.0
```

### 1.2 `.env.sample`
```text
HF_TOKEN=
GROQ_API_KEY=
```

### 1.3 `gba_studio/core/config.py`
```python
# config.py
import os
import re
import json
import copy
import glob
import datetime

CONFIG_FILE = "mgba_config.json"

GLOBAL_ONLY_TOP_SECTIONS = {"recent_files", "paths", "netplay", "credentials", "retroachievements"}
GLOBAL_ONLY_KEY_PAIRS = {
    ("video", "window_width"),
    ("video", "window_height"),
    ("video", "splitter_sizes"),
    ("video", "scale"),
    ("sensors", "show_hud"),
}
PER_GAME_SECTIONS = ("audio", "emulation", "sensors", "video", "shaders", "ai_translation", "input")

DEFAULT_CONFIG = {
    "credentials": {
        "hf_token": "",
        "groq_api_key": "",
        "ra_username": "",
        "ra_password": "",
        "ra_token": "",
        "ra_web_api_key": ""
    },
    "retroachievements": {
        "enabled": False,
        "hardcore": False,
        "show_panel": True,
        "popup_notifications": True
    },
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
        "hf_space_enabled": True,
        "hf_space_id": "brianknight144/Qwen3.8-27B-Uncensored-Demo",
        "overlay_enabled": True,
        "inpaint_enabled": True,
        "inpaint_auto_clear": True,
        "inpaint_mode": "auto",
        "inpaint_font_scale": 1.0,
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

        if not str(self.global_settings.get("credentials", {}).get("groq_api_key", "")).strip():
            env_key = os.environ.get("GROQ_API_KEY", "").strip()
            if env_key:
                self.global_settings.setdefault("credentials", {})["groq_api_key"] = env_key

        if not str(self.global_settings.get("credentials", {}).get("hf_token", "")).strip():
            env_hf = os.environ.get("HF_TOKEN", "").strip()
            if env_hf:
                self.global_settings.setdefault("credentials", {})["hf_token"] = env_hf

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

## Part 2: Lazy Gradio Client & Text Translation

### 2.1 `gba_studio/tools/ai/engines.py`
```python
# engines.py
import os
import csv
import json
import re
import ssl
import glob
import io
import time
import base64
import inspect
import threading
import statistics
import urllib.request
import urllib.parse
import urllib.error
import numpy as np
import cv2
from PIL import Image

from PyQt6.QtCore import QThread, pyqtSignal
from ...paths import PROJECT_ROOT


# =============================================================================
# 1. ENVIRONMENT CONFIGURATION & SECURITY (.env LOADER)
# =============================================================================
def load_env_file():
    """Loads environment variables from .env in project root."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip("'\"")
        except Exception as e:
            print(f"[Config] Error reading .env: {e}")


load_env_file()

# Clean up stale/expired Hugging Face CLI tokens so public model downloads never 401
os.environ.pop("HF_TOKEN", None)
os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
token_path = os.path.expanduser("~/.cache/huggingface/token")
if os.path.exists(token_path):
    try:
        os.remove(token_path)
    except Exception:
        pass

# Bypass SSL verification for model downloads
ssl._create_default_https_context = ssl._create_unverified_context
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''
os.environ['PYTHONHTTPSVERIFY'] = '0'
os.environ['PADDLE_PDX_MODEL_SOURCE'] = 'BOS'
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

# Global model instances cached once loaded
_GLOBAL_LLM_INSTANCE = None
_GLOBAL_LLM_PATH = None
_GLOBAL_MEIKI_READER = None
_GLOBAL_PADDLE_READER = None
_GLOBAL_EASY_READER = None
_GLOBAL_HF_CLIENT = None
_GLOBAL_HF_KEY = None
_HF_CLIENT_LOCK = threading.Lock()
_LAST_GROQ_CALL_TIME = 0.0
_GAME_CRITERIA_CACHE = {}

DEFAULT_HF_SPACE_ID = "brianknight144/Qwen3.8-27B-Uncensored-Demo"

LANG_NAMES = {"ja": "Japanese", "en": "English", "vi": "Vietnamese"}

UNIVERSAL_GBA_CRITERIA = (
    "- Preserve original meaning, dramatic weight, and character voice.\n"
    "- Maintain authentic naming conventions (strictly use Sino-Vietnamese / Hán-Việt for Chinese/historical settings like Sangokushi, e.g., 曹操 = Tào Tháo, 献帝 = Hiến Đế, 孔明 = Khổng Minh).\n"
    "- Match pronouns and honorifics to speaker rank and relationship (e.g., Ta, Quả nhân, Trẫm, Thần, Chúa công).\n"
    "- Unwrap multi-line dialogue into natural, fluent target-language syntax.\n"
    "- Translate menu commands concisely as crisp, actionable UI verbs/nouns.\n"
    "- Translate standalone graphic banners, map names, and HUD stats as concise proper nouns or labels."
)


# =============================================================================
# 2. LAZY ON-DEMAND ENGINE LOADERS (Zero Startup Delay)
# =============================================================================
def get_hf_client(space_id: str = DEFAULT_HF_SPACE_ID, hf_token: str = ""):
    """Lazily connects and caches the Gradio Client for the Hugging Face Space."""
    global _GLOBAL_HF_CLIENT, _GLOBAL_HF_KEY
    space_id = (space_id or DEFAULT_HF_SPACE_ID).strip()
    hf_token = (hf_token or "").strip()
    if not space_id:
        return None

    cache_key = (space_id, hf_token)
    with _HF_CLIENT_LOCK:
        if _GLOBAL_HF_CLIENT is not None and _GLOBAL_HF_KEY == cache_key:
            return _GLOBAL_HF_CLIENT
        try:
            from gradio_client import Client
            kwargs = {}
            if hf_token:
                sig = inspect.signature(Client.__init__).parameters
                if "token" in sig:
                    kwargs["token"] = hf_token
                elif "hf_token" in sig:
                    kwargs["hf_token"] = hf_token

            print(f"[AI Translator] Connecting to Hugging Face Space ({space_id})...")
            _GLOBAL_HF_CLIENT = Client(space_id, **kwargs)
            _GLOBAL_HF_KEY = cache_key
            return _GLOBAL_HF_CLIENT
        except Exception as e:
            print(f"[HF Space Connect Error] {e}")
            _GLOBAL_HF_CLIENT = None
            _GLOBAL_HF_KEY = None
            return None


def reset_hf_client():
    """Clears cached Gradio Client after transport/session errors so next call reconnects."""
    global _GLOBAL_HF_CLIENT, _GLOBAL_HF_KEY
    with _HF_CLIENT_LOCK:
        _GLOBAL_HF_CLIENT = None
        _GLOBAL_HF_KEY = None


def extract_hf_text(result) -> str:
    """Normalizes /respond output (str | list | dict) into a plain text string."""
    if result is None:
        return ""
    if isinstance(result, str):
        return re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
    if isinstance(result, dict):
        for key in ("response", "text", "content", "output", "answer", "reply", "message"):
            if key in result and result[key] is not None:
                val = result[key]
                if isinstance(val, str):
                    return re.sub(r"<think>.*?</think>", "", val, flags=re.DOTALL).strip()
                if isinstance(val, (dict, list)):
                    return extract_hf_text(val)
        return json.dumps(result, ensure_ascii=False)
    if isinstance(result, list):
        if not result:
            return ""
        if all(isinstance(x, str) for x in result):
            return re.sub(r"<think>.*?</think>", "", "\n".join(result), flags=re.DOTALL).strip()
        if len(result) == 1:
            return extract_hf_text(result[0])
        return json.dumps(result, ensure_ascii=False)
    return str(result).strip()


def call_hf_respond(
    text: str,
    image_path: str | None = None,
    space_id: str = DEFAULT_HF_SPACE_ID,
    hf_token: str = "",
    reasoning: str = "off",
    temperature: float = 0.1,
    top_p: float = 0.95,
    top_k: int = 20,
):
    """Invokes /respond on the Hugging Face Space with optional image file input."""
    client = get_hf_client(space_id=space_id, hf_token=hf_token)
    if client is None:
        return None

    try:
        files = []
        if image_path:
            from gradio_client import handle_file
            files = [handle_file(image_path)]

        return client.predict(
            message={"text": text, "files": files},
            reasoning=reasoning,
            temperature=float(temperature),
            top_p=float(top_p),
            top_k=int(top_k),
            api_name="/respond",
        )
    except Exception as e:
        print(f"[HF Space Predict Error] {e}")
        reset_hf_client()
        return None


def get_meiki_reader():
    """Lazily loads MeikiOCR in the worker thread only when invoked."""
    global _GLOBAL_MEIKI_READER
    if _GLOBAL_MEIKI_READER is None:
        try:
            print("[AI Translator] Initializing MeikiOCR (Japanese Video Game OCR)...")
            from meikiocr import MeikiOCR
            _GLOBAL_MEIKI_READER = MeikiOCR()
        except Exception as e:
            print(f"[AI Translator] MeikiOCR unavailable: {e}")
            _GLOBAL_MEIKI_READER = False
    return _GLOBAL_MEIKI_READER if _GLOBAL_MEIKI_READER is not False else None


def get_paddle_reader(src_lang: str):
    """Lazily loads PaddleOCR without slowing down application launch."""
    global _GLOBAL_PADDLE_READER
    if _GLOBAL_PADDLE_READER is None:
        try:
            print("[AI Translator] Initializing PaddleOCR backend...")
            import logging
            logging.getLogger('ppocr').setLevel(logging.ERROR)
            from paddleocr import PaddleOCR
            _GLOBAL_PADDLE_READER = PaddleOCR(
                lang='japan' if src_lang == 'ja' else 'en',
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False
            )
        except Exception as e:
            print(f"[AI Translator] PaddleOCR unavailable: {e}")
            _GLOBAL_PADDLE_READER = False
    return _GLOBAL_PADDLE_READER if _GLOBAL_PADDLE_READER is not False else None


def get_easy_reader(src_lang: str, models_dir: str):
    """Lazily loads EasyOCR as tertiary fallback."""
    global _GLOBAL_EASY_READER
    if _GLOBAL_EASY_READER is None:
        try:
            print("[AI Translator] Initializing EasyOCR fallback...")
            import easyocr
            ocr_langs = [src_lang] if src_lang != "vi" else ["en"]
            if "en" not in ocr_langs:
                ocr_langs.append("en")
            _GLOBAL_EASY_READER = easyocr.Reader(
                ocr_langs, gpu=False, verbose=False, model_storage_directory=models_dir
            )
        except Exception as e:
            print(f"[AI Translator] EasyOCR unavailable: {e}")
            _GLOBAL_EASY_READER = False
    return _GLOBAL_EASY_READER if _GLOBAL_EASY_READER is not False else None


def get_llama_instance(model_path: str):
    """Lazily loads llama-cpp-python in the background thread."""
    global _GLOBAL_LLM_INSTANCE, _GLOBAL_LLM_PATH
    if _GLOBAL_LLM_INSTANCE is None or _GLOBAL_LLM_PATH != model_path:
        if not os.path.exists(model_path):
            return None
        try:
            from llama_cpp import Llama
            threads = min(6, os.cpu_count() or 4)
            print(f"[AI Translator] Loading {os.path.basename(model_path)} ({threads} threads)...")
            _GLOBAL_LLM_INSTANCE = Llama(
                model_path=model_path,
                n_ctx=1536,
                n_threads=threads,
                verbose=False
            )
            _GLOBAL_LLM_PATH = model_path
            print(f"[AI Translator] {os.path.basename(model_path)} loaded successfully.")
        except Exception as e:
            print(f"[Local LLM Error] {e}")
            _GLOBAL_LLM_INSTANCE = None
    return _GLOBAL_LLM_INSTANCE


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


def _has_meaningful_text(text: str) -> bool:
    """Filters out decorative dot bubbles like '....' or pure symbol noise."""
    if not text:
        return False
    return bool(re.search(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fafA-Za-z0-9]', text))


def find_best_llm_model(models_dir: str) -> str:
    """Auto-detects the highest quality local GGUF model in models/."""
    candidates = [
        "Qwen2.5-14B-Instruct-Q4_K_L.gguf",
        "qwen2.5-14b-instruct-q4_k_m.gguf",
        "qwen2.5-14b-instruct-q4_0-00001-of-00003.gguf",
        "qwen2.5-14b-instruct-q4_k_s.gguf",
        "qwen2.5-14b-instruct-q4_0.gguf",
        "qwen2.5-14b-instruct-q3_k_m.gguf",
        "gemma-2-9b-it-Q5_K_M.gguf",
        "gemma-2-9b-it-Q4_K_M.gguf",
        "qwen2.5-7b-instruct-q6_k.gguf",
        "qwen2.5-7b-instruct-q4_k_m.gguf",
        "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    ]
    for name in candidates:
        full_path = os.path.join(models_dir, name)
        if os.path.exists(full_path):
            return full_path

    all_ggufs = glob.glob(os.path.join(models_dir, "*.gguf"))
    if all_ggufs:
        all_ggufs.sort(key=os.path.getsize, reverse=True)
        return all_ggufs[0]

    return os.path.join(models_dir, "Qwen2.5-14B-Instruct-Q4_K_L.gguf")
```

### 2.2 `gba_studio/tools/ai/local.py`
```python
# local.py
import json
import re
import urllib.request
import urllib.parse

from .engines import (
    LANG_NAMES,
    DEFAULT_HF_SPACE_ID,
    get_llama_instance,
    call_hf_respond,
    extract_hf_text,
)


class LocalQwenTranslator:
    @classmethod
    def translate_block(
        cls,
        model_path: str,
        text: str,
        category: str,
        speaker: str,
        src_lang: str,
        tgt_lang: str,
        criteria: str,
        api_key: str,
        glossary_text: str = "",
        hf_enabled: bool = True,
        hf_space_id: str = DEFAULT_HF_SPACE_ID,
        hf_token: str = "",
    ) -> tuple:
        """Translates a categorized block ('dialogue', 'menu', or 'label') and optional speaker."""
        src_name = LANG_NAMES.get(src_lang, src_lang)
        tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang)

        if category == "menu":
            role_hint = "This is a selectable game MENU command/option. Translate concisely as a crisp UI action/option (1-6 words)."
        elif category == "label":
            role_hint = "This is a standalone GRAPHIC LABEL, title banner, location name, or HUD tag. Translate concisely as a proper noun or short UI label."
        else:
            sp_ctx = f" spoken by '{speaker}'" if speaker else ""
            role_hint = (
                f"This is a complete unwrapped DIALOGUE/NARRATION sentence{sp_ctx}. "
                "Translate naturally with fluent grammar and appropriate character pronouns/register."
            )

        glossary_section = f"\n{glossary_text}" if glossary_text else ""
        user_prompt = f"Speaker: {speaker}\nText: {text}" if speaker else text
        sys_prompt = (
            f"You are an expert retro video game localizer translating {src_name} GBA text to {tgt_name}.\n"
            f"TASK: {role_hint}\n"
            f"STYLE CRITERIA:\n{criteria}{glossary_section}\n"
            + (
                "Output strictly in format: TranslatedSpeaker | TranslatedText"
                if speaker else
                "Output ONLY the translated text. No notes, no explanations, no romaji."
            )
        )

        raw_out = ""

        # 1. Local GGUF LLM (if installed)
        llm = get_llama_instance(model_path)
        if llm is not None:
            try:
                resp = llm.create_chat_completion(
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.1,
                    max_tokens=200
                )
                raw_out = resp["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"[Local LLM Error] {e}")

        # 2. Cloud Priority 1: Hugging Face Space
        if not raw_out and hf_enabled and hf_space_id:
            hf_res = call_hf_respond(
                text=f"{sys_prompt}\n\n{user_prompt}",
                image_path=None,
                space_id=hf_space_id,
                hf_token=hf_token,
                reasoning="off",
                temperature=0.1,
            )
            raw_out = extract_hf_text(hf_res)

        # 3. Cloud Priority 2 (Fallback): Groq API
        if not raw_out and api_key:
            for model_id in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
                try:
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0"
                    }
                    payload = {
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.1,
                        "max_completion_tokens": 200
                    }
                    req = urllib.request.Request(
                        "https://api.groq.com/openai/v1/chat/completions",
                        data=json.dumps(payload).encode("utf-8"),
                        headers=headers
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        raw_out = data["choices"][0]["message"]["content"].strip()
                        if raw_out:
                            break
                except Exception:
                    continue

        # 4. Last-resort Google Translate fallback
        if not raw_out:
            raw_out = _google_translate_fallback(text, src_lang, tgt_lang)
            sp_tr = _google_translate_fallback(speaker, src_lang, tgt_lang) if speaker else ""
            return sp_tr, raw_out

        raw_out = re.sub(r"<think>.*?</think>", "", raw_out, flags=re.DOTALL).strip()
        if speaker and "|" in raw_out:
            sp_tr, tr_text = raw_out.split("|", 1)
            return sp_tr.strip(), tr_text.strip()

        return speaker, raw_out


def _google_translate_fallback(text: str, src: str, tgt: str) -> str:
    if not text.strip():
        return ""
    try:
        url = (
            f"https://translate.googleapis.com/translate_a/single?client=gtx"
            f"&sl={src}&tl={tgt}&dt=t&q={urllib.parse.quote(text)}"
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return "".join(part[0] for part in data[0] if part and part[0]).strip()
    except Exception:
        return text
```

---

## Part 3: Multimodal Vision & Priority Pipeline

### 3.1 `gba_studio/tools/ai/vision.py`
```python
# vision.py
import os
import json
import re
import io
import time
import base64
import tempfile
import urllib.request
import urllib.error
import numpy as np
from PIL import Image

from .engines import (
    LANG_NAMES,
    DEFAULT_HF_SPACE_ID,
    _GAME_CRITERIA_CACHE,
    UNIVERSAL_GBA_CRITERIA,
    _has_meaningful_text,
    call_hf_respond,
    extract_hf_text,
)

_LAST_GROQ_CALL_TIME = 0.0


class TranslatedBox:
    """Represents a categorized UI text element ('dialogue', 'menu', or 'label')."""
    def __init__(self, x, y, w, h, original, translated,
                 category: str = "dialogue", speaker_orig: str = "", speaker_trans: str = "",
                 has_box: bool = True):
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)
        self.original = original
        self.translated = translated
        self.category = category if category in ("dialogue", "menu", "label") else "dialogue"
        self.speaker_orig = speaker_orig.strip()
        self.speaker_trans = speaker_trans.strip()
        self.has_box = bool(has_box)


def get_or_create_game_criteria(
    rom_path: str,
    src_lang: str,
    tgt_lang: str,
    api_key: str,
    trans_dir: str,
    hf_enabled: bool = True,
    hf_space_id: str = DEFAULT_HF_SPACE_ID,
    hf_token: str = "",
) -> str:
    """Generates and caches localization criteria once per game ROM (HF Space first, Groq fallback)."""
    raw_name = os.path.splitext(os.path.basename(rom_path))[0]
    clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', raw_name)
    clean_title = re.sub(r'[_\-]+(VI|EN|stage\d+|patched).*$', '', clean_title, flags=re.IGNORECASE).strip() or raw_name

    cache_key = (clean_title, src_lang, tgt_lang)
    if cache_key in _GAME_CRITERIA_CACHE:
        return _GAME_CRITERIA_CACHE[cache_key]

    os.makedirs(trans_dir, exist_ok=True)
    safe_name = re.sub(r'[^\w\- ]', '_', clean_title).strip()
    criteria_file = os.path.join(trans_dir, f"{safe_name}_criteria_{src_lang}_{tgt_lang}.txt")

    if os.path.exists(criteria_file):
        try:
            with open(criteria_file, "r", encoding="utf-8") as f:
                cached = f.read().strip()
                if cached:
                    _GAME_CRITERIA_CACHE[cache_key] = cached
                    return cached
        except Exception:
            pass

    src_name = LANG_NAMES.get(src_lang, src_lang)
    tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang)
    prompt = (
        f"Give me a concise list of 6 localization criteria (under 110 words total) for translating the video game "
        f"'{clean_title}' from {src_name} to {tgt_name}. Cover: naming/transliteration conventions (e.g. Sino-Vietnamese/Hán-Việt "
        f"if East Asian/historical), character pronouns/honorifics, dialogue tone, and concise UI/menu phrasing. "
        f"Provide ONLY bullet points starting with '- ', no intro or commentary."
    )

    # Priority 1: Hugging Face Space
    if hf_enabled and hf_space_id:
        hf_res = call_hf_respond(
            text=prompt,
            image_path=None,
            space_id=hf_space_id,
            hf_token=hf_token,
            reasoning="off",
            temperature=0.2,
        )
        content = extract_hf_text(hf_res)
        if content and "-" in content:
            try:
                with open(criteria_file, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
            _GAME_CRITERIA_CACHE[cache_key] = content
            return content

    # Priority 2: Groq API Fallback
    if api_key:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }

        for model_id in ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]:
            try:
                payload = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_completion_tokens": 220
                }
                if "qwen" in model_id:
                    payload["reasoning_effort"] = "none"

                req = urllib.request.Request(
                    "https://api.groq.com/openai/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    content = data["choices"][0]["message"]["content"]
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                    if content and "-" in content:
                        with open(criteria_file, "w", encoding="utf-8") as f:
                            f.write(content)
                        _GAME_CRITERIA_CACHE[cache_key] = content
                        return content
            except Exception:
                continue

    _GAME_CRITERIA_CACHE[cache_key] = UNIVERSAL_GBA_CRITERIA
    return UNIVERSAL_GBA_CRITERIA


def _parse_box_coords(item: dict) -> tuple[int, int, int, int, bool]:
    raw_box = item.get("box") or item.get("bbox")
    if isinstance(raw_box, (list, tuple)) and len(raw_box) == 4:
        try:
            bx, by, bw, bh = [int(round(float(v))) for v in raw_box]
            if 0 <= bx < 236 and 0 <= by < 156 and bw >= 6 and bh >= 6:
                return (
                    max(0, min(234, bx)),
                    max(0, min(154, by)),
                    max(8, min(240 - max(0, bx), bw)),
                    max(8, min(160 - max(0, by), bh)),
                    True,
                )
        except (ValueError, TypeError):
            pass
    return 10, 10, 220, 18, False


def _items_to_boxes(items: list) -> list:
    boxes = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        cat = str(it.get("type", "dialogue")).lower().strip()
        if cat not in ("dialogue", "menu", "label"):
            cat = "dialogue"
        orig = str(it.get("ja") or it.get("original") or "").strip()
        trans = str(it.get("tr") or it.get("translated") or "").strip()
        sp_ja = str(it.get("speaker") or it.get("speaker_ja") or "").strip()
        sp_tr = str(it.get("speaker_tr") or "").strip()

        if not _has_meaningful_text(orig) or not trans:
            continue

        bx, by, bw, bh, has_box = _parse_box_coords(it)
        if not has_box:
            by = min(140, 10 + idx * 20)

        boxes.append(TranslatedBox(
            bx, by, bw, bh,
            orig, trans,
            category=cat,
            speaker_orig=sp_ja,
            speaker_trans=sp_tr,
            has_box=has_box,
        ))
    return boxes


def _parse_structured_items(content: str) -> list:
    """Parses structured JSON items into categorized TranslatedBoxes (with partial JSON recovery)."""
    clean = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.MULTILINE)
    clean = re.sub(r"```\s*$", "", clean, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(clean)
        items = parsed.get("items", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
        boxes = _items_to_boxes(items)
        if boxes:
            return boxes
    except Exception:
        match = re.search(r'\[\s*\{.*\}\s*\]', clean, re.DOTALL)
        if match:
            try:
                items = json.loads(match.group(0))
                boxes = _items_to_boxes(items)
                if boxes:
                    return boxes
            except Exception:
                pass

        recovered = []
        for obj_m in re.finditer(r'\{[^{}]*\}', clean, re.DOTALL):
            try:
                recovered.append(json.loads(obj_m.group(0)))
            except Exception:
                continue
        boxes = _items_to_boxes(recovered)
        if boxes:
            return boxes

    boxes = []
    for idx, line in enumerate(clean.split("\n")):
        line = line.strip()
        if "->" in line:
            parts = line.split("->", 1)
            orig = parts[0].strip().strip("*-• ")
            trans = parts[1].strip()
            if _has_meaningful_text(orig) and trans:
                cat = "dialogue" if re.search(r'[「」。、…！？]', orig) else "menu"
                boxes.append(TranslatedBox(10, 10 + idx * 20, 220, 18, orig, trans, category=cat, has_box=False))

    return boxes


def _parse_hf_structured_result(result) -> list:
    """Parses Gradio /respond JSON/dict/list/str output into TranslatedBox items."""
    if result is None:
        return []
    if isinstance(result, dict):
        if isinstance(result.get("items"), list):
            boxes = _items_to_boxes(result["items"])
            if boxes:
                return boxes
        for key in ("response", "text", "content", "output", "answer", "reply", "message"):
            if key in result and result[key] is not None:
                boxes = _parse_hf_structured_result(result[key])
                if boxes:
                    return boxes
        return _parse_structured_items(json.dumps(result, ensure_ascii=False))
    if isinstance(result, list):
        if result and all(isinstance(x, dict) for x in result):
            boxes = _items_to_boxes(result)
            if boxes:
                return boxes
        text = extract_hf_text(result)
        return _parse_structured_items(text)
    return _parse_structured_items(extract_hf_text(result))


def _build_vision_prompt(src_lang: str, tgt_lang: str, criteria: str, glossary_text: str = "") -> str:
    src_name = LANG_NAMES.get(src_lang, src_lang)
    tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang)
    glossary_section = f"\n\n{glossary_text}" if glossary_text else ""

    return (
        f"Analyze the ENTIRE GBA game screen (240x160 native pixels) from top to bottom and localize EVERY meaningful {src_name} or UI text block into {tgt_name}.\n"
        "IMPORTANT: Include ALL panels on screen — main menus, dimmed/grayed-out options, AND bottom 'Information' / help / description boxes!\n\n"
        "Classify each element into ONE of 3 types:\n"
        "1. \"dialogue\": Speech boxes, story narration scrolls, OR help/information/description panels.\n"
        "   - UNWRAP AND MERGE multi-line sentences inside the same box into ONE complete sentence before translating.\n"
        "   - If a speaker nameplate appears above/beside a quote, strip its furigana reading into \"speaker\", put the translated name in \"speaker_tr\", and do NOT output the speaker as a separate item.\n"
        "   - Strip inline furigana readings attached to Kanji in \"ja\".\n"
        "2. \"menu\": Selectable menu choices, setting options, or command lists (including grayed-out options).\n"
        "   - Keep each option as a SEPARATE item in top-to-bottom order.\n"
        "3. \"label\": Panel headers, chapter banners, map locations, or HUD/button tags. Ignore decorative dot bubbles like \"....\".\n\n"
        "For EVERY item, also include \"box\": [x, y, w, h] giving its approximate pixel bounding box in native 240x160 GBA coordinates (including speaker nameplate if inside the same dialogue box).\n\n"
        f"STYLE CRITERIA:\n{criteria}{glossary_section}\n\n"
        "Return ONLY a valid JSON object (no markdown fences, no extra commentary) with key \"items\":\n"
        "{\"items\": [{\"type\": \"dialogue|menu|label\", \"speaker\": \"\", \"speaker_tr\": \"\", \"ja\": \"...\", \"tr\": \"...\", \"box\": [x, y, w, h]}]}"
    )


def run_hf_vision(
    frame_data: np.ndarray,
    src_lang: str,
    tgt_lang: str,
    criteria: str,
    glossary_text: str = "",
    space_id: str = DEFAULT_HF_SPACE_ID,
    hf_token: str = "",
):
    """Primary multimodal vision recognition & translation via Hugging Face Gradio Space."""
    space_id = (space_id or "").strip()
    if not space_id:
        return None, None

    pil_img = Image.fromarray(frame_data, "RGB")
    if pil_img.width <= 320:
        pil_img = pil_img.resize((pil_img.width * 3, pil_img.height * 3), Image.Resampling.NEAREST)

    prompt = _build_vision_prompt(src_lang, tgt_lang, criteria, glossary_text=glossary_text)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tmp_path = tf.name
        pil_img.save(tmp_path, format="PNG", optimize=True)

        result = call_hf_respond(
            text=prompt,
            image_path=tmp_path,
            space_id=space_id,
            hf_token=hf_token,
            reasoning="off",
            temperature=0.1,
            top_p=0.95,
            top_k=20,
        )
        if result is not None:
            boxes = _parse_hf_structured_result(result)
            if boxes:
                short_name = space_id.split("/")[-1]
                return boxes, f"HF:{short_name}"
    except Exception as e:
        print(f"[HF Vision on {space_id}] {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return None, None


def run_groq_vision(frame_data: np.ndarray, src_lang: str, tgt_lang: str, api_key: str,
                    criteria: str, glossary_text: str = ""):
    """Fallback multimodal vision recognition and translation with Groq Vision."""
    global _LAST_GROQ_CALL_TIME
    if not api_key:
        return None, None

    elapsed = time.time() - _LAST_GROQ_CALL_TIME
    if elapsed < 2.5:
        time.sleep(2.5 - elapsed)

    # Nearest-neighbor 3x upscale makes 11px pixel-art hiragana and dimmed options crisp for Vision
    pil_img = Image.fromarray(frame_data, "RGB")
    if pil_img.width <= 320:
        pil_img = pil_img.resize((pil_img.width * 3, pil_img.height * 3), Image.Resampling.NEAREST)

    buffered = io.BytesIO()
    pil_img.save(buffered, format="PNG", optimize=True)
    b64_img = base64.b64encode(buffered.getvalue()).decode("utf-8")
    data_url = f"data:image/png;base64,{b64_img}"

    prompt = _build_vision_prompt(src_lang, tgt_lang, criteria, glossary_text=glossary_text)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    for model_id in ["qwen/qwen3.8-27b"]:
        try:
            payload = {
                "model": model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}}
                        ]
                    }
                ],
                "reasoning_effort": "none",
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
                "max_completion_tokens": 1600
            }

            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                content = resp_data["choices"][0]["message"]["content"].strip()
                _LAST_GROQ_CALL_TIME = time.time()
                boxes = _parse_structured_items(content)
                if boxes:
                    return boxes, model_id
        except urllib.error.HTTPError as e:
            try:
                err_json = e.read().decode("utf-8")
                print(f"[Groq Vision on {model_id}] {e}: {err_json}")
            except Exception:
                print(f"[Groq Vision on {model_id}] {e}")
            continue
        except Exception as e:
            print(f"[Groq Vision on {model_id}] {e}")
            continue

    return None, None
```

### 3.2 `gba_studio/tools/ai/worker.py`
```python
# worker.py
import os
import re
import csv
from difflib import SequenceMatcher
import numpy as np
import cv2

from PyQt6.QtCore import QThread, pyqtSignal
from ...paths import PROJECT_ROOT

from .engines import (
    DEFAULT_HF_SPACE_ID,
    get_meiki_reader,
    get_paddle_reader,
    get_easy_reader,
    _has_meaningful_text,
    find_best_llm_model,
    get_llama_instance,
)
from .vision import TranslatedBox, get_or_create_game_criteria, run_hf_vision, run_groq_vision
from .ocr import _clean_meiki_chars_furigana, _classify_and_group_ocr_lines
from .local import LocalQwenTranslator
from .glossary import GlossaryAndCacheStore
from .inpaint import align_vision_boxes_with_ocr, build_inpaint_patches


def _normalize_ocr_signature(text: str) -> str:
    """Normalizes OCR/vision text by stripping cursor glyphs, brackets, and whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r'[▶▷►▼▽▲△◀◁\s\-_=•·・]+', '', text)
    return cleaned.strip()


def _has_dialogue_or_menu_text(raw_lines: list, src_lang: str) -> bool:
    """Filters out pure numeric action-game HUDs (HP, timers, scores, FPS)."""
    joined = "".join(t for _, t, _ in raw_lines).strip()
    if not _has_meaningful_text(joined):
        return False
    if src_lang == "ja":
        jp_chars = re.findall(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]', joined)
        alpha_chars = re.findall(r'[A-Za-z]', joined)
        return len(jp_chars) >= 2 or len(alpha_chars) >= 4
    alpha_chars = re.findall(r'[A-Za-z\u00C0-\u1EF9]', joined)
    return len(alpha_chars) >= 3


class TranslationWorker(QThread):
    finished = pyqtSignal(list, str)
    skipped = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        frame_data: np.ndarray,
        rom_path: str,
        config,
        game_code: str = "",
        auto_triggered: bool = False,
        last_auto_ocr_sig: str = "",
        auto_ocr_cache: dict | None = None,
    ):
        super().__init__()
        self.frame_data = frame_data
        self.rom_path = rom_path
        self.config = config
        self.game_code = game_code
        self.auto_triggered = bool(auto_triggered)
        self.last_auto_ocr_sig = last_auto_ocr_sig or ""
        self.auto_ocr_cache = auto_ocr_cache if auto_ocr_cache is not None else {}
        self.result_ocr_sig = ""
        self.result_patches = []

    def preprocess_image(self, img_array: np.ndarray, scale: int = 4) -> np.ndarray:
        height, width, _ = img_array.shape
        return cv2.resize(img_array, (width * scale, height * scale), interpolation=cv2.INTER_LANCZOS4)

    def _run_local_ocr_raw(self, img_np: np.ndarray, src_lang: str, models_dir: str) -> tuple[list, str]:
        results = []
        ocr_engine_name = "None"

        if src_lang == "ja":
            meiki_reader = get_meiki_reader()
            if meiki_reader:
                try:
                    img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
                    meiki_lines = meiki_reader.run_ocr(img_bgr)
                    for line in meiki_lines:
                        chars = line.get("chars", [])
                        if not chars:
                            continue
                        clean_text = _clean_meiki_chars_furigana(chars).strip()
                        if not clean_text:
                            continue
                        xs = [c["bbox"][0] for c in chars] + [c["bbox"][2] for c in chars]
                        ys = [c["bbox"][1] for c in chars] + [c["bbox"][3] for c in chars]
                        bbox = [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
                        results.append((bbox, clean_text, 1.0))
                    return results, "MeikiOCR"
                except Exception as e:
                    print(f"[OCR Warning] MeikiOCR failed ({e}). Trying PaddleOCR...")

        paddle_reader = get_paddle_reader(src_lang)
        if paddle_reader:
            try:
                paddle_res = paddle_reader.predict(img_np)
                if paddle_res:
                    for res in paddle_res:
                        if isinstance(res, dict):
                            for poly, text, conf in zip(res.get("dt_polys", []), res.get("rec_text", []), res.get("rec_score", [])):
                                results.append((poly, text, conf))
                        elif hasattr(res, "keys"):
                            for poly, text, conf in zip(res["dt_polys"], res["rec_text"], res["rec_score"]):
                                results.append((poly, text, conf))
                return results, "PaddleOCR"
            except Exception as e:
                print(f"[OCR Warning] PaddleOCR failed ({e}). Trying EasyOCR...")

        easy_reader = get_easy_reader(src_lang, models_dir)
        if easy_reader:
            try:
                results = easy_reader.readtext(img_np, text_threshold=0.5, low_text=0.3)
                return results, "EasyOCR"
            except Exception as e:
                print(f"[OCR Warning] EasyOCR failed: {e}")

        return results, ocr_engine_name

    def _raw_ocr_to_240(self, results: list, scale: int) -> tuple[list, list]:
        merged_rows = self._merge_horizontal_row_fragments(results)
        lines_240 = []
        for (bbox, raw_text, _) in merged_rows:
            xs = [pt[0] / float(scale) for pt in bbox]
            ys = [pt[1] / float(scale) for pt in bbox]
            x1, y1 = max(0.0, min(xs)), max(0.0, min(ys))
            x2, y2 = min(239.0, max(xs)), min(159.0, max(ys))
            lines_240.append({
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "w": x2 - x1, "h": y2 - y1,
                "cx": (x1 + x2) / 2.0, "cy": (y1 + y2) / 2.0,
                "text": raw_text.strip()
            })
        semantic_blocks = _classify_and_group_ocr_lines(lines_240)
        return lines_240, semantic_blocks

    def _prepare_patches(self, boxes: list, models_dir: str):
        inpaint_enabled = bool(self.config.get("ai_translation", "inpaint_enabled", True))
        inpaint_mode = str(self.config.get("ai_translation", "inpaint_mode", "auto") or "auto")
        if inpaint_enabled and boxes:
            self.result_patches = build_inpaint_patches(
                self.frame_data, boxes, mode=inpaint_mode, models_dir=models_dir
            )
        else:
            self.result_patches = []

    def _apply_store_overrides(self, store: GlossaryAndCacheStore, boxes: list, src_lang: str, tgt_lang: str):
        for b in boxes:
            if b.speaker_orig:
                gl_sp = store.lookup_term(b.speaker_orig)
                if gl_sp:
                    b.speaker_trans = gl_sp

            gl_term = store.lookup_term(b.original)
            if gl_term:
                b.translated = gl_term

            cached = store.lookup_entry(src_lang, tgt_lang, b.category, b.speaker_orig, b.original)
            if cached and cached.get("locked"):
                b.translated = cached.get("translated") or b.translated
                if cached.get("speaker_trans"):
                    b.speaker_trans = cached["speaker_trans"]
            else:
                store.upsert_entry(
                    src_lang, tgt_lang, b.category,
                    b.speaker_orig, b.speaker_trans,
                    b.original, b.translated,
                    locked=False, save_now=False,
                )

    def run(self):
        if self.frame_data is None:
            self.error.emit("No active video frame to capture.")
            return

        src_lang = self.config.get("ai_translation", "source_lang") or "ja"
        tgt_lang = self.config.get("ai_translation", "target_lang") or "vi"
        inpaint_enabled = bool(self.config.get("ai_translation", "inpaint_enabled", True))

        app_dir = str(PROJECT_ROOT)
        models_dir = os.path.join(app_dir, "models")
        os.makedirs(models_dir, exist_ok=True)

        trans_dir = self.config.get("paths", "translations") or "translations"
        os.makedirs(trans_dir, exist_ok=True)

        store = GlossaryAndCacheStore(self.rom_path, trans_dir=trans_dir, game_code=self.game_code)
        frame_hash = store.hash_frame(self.frame_data)

        # 0. Instant Frame + Cache Hit
        cached_frame_items = store.resolve_cached_frame(frame_hash, src_lang, tgt_lang)
        if cached_frame_items:
            boxes = []
            for it in cached_frame_items:
                is_stub = (it.get("x") == 10 and it.get("w") == 220 and it.get("h") == 18)
                boxes.append(TranslatedBox(
                    it["x"], it["y"], it["w"], it["h"],
                    it["original"], it["translated"],
                    category=it["category"],
                    speaker_orig=it["speaker_orig"],
                    speaker_trans=it["speaker_trans"],
                    has_box=not is_stub,
                ))
            self.result_ocr_sig = _normalize_ocr_signature("".join(b.original for b in boxes))
            self._prepare_patches(boxes, models_dir)
            self.finished.emit(boxes, f"Translated {len(boxes)} block(s) via Instant Cache [{store.game_id}]")
            return

        SCALE = 4
        img_np = None
        precomputed_ocr = None
        precomputed_engine = "None"
        local_ocr_sig = ""

        # 1. Local OCR Text-Change Gate
        if self.auto_triggered:
            img_np = self.preprocess_image(self.frame_data, scale=SCALE)
            precomputed_ocr, precomputed_engine = self._run_local_ocr_raw(img_np, src_lang, models_dir)

            if precomputed_engine != "None":
                if not precomputed_ocr or not _has_dialogue_or_menu_text(precomputed_ocr, src_lang):
                    self.skipped.emit("Auto-skip: No dialogue/menu text on screen")
                    return

                merged_preview = self._merge_horizontal_row_fragments(precomputed_ocr)
                local_ocr_sig = _normalize_ocr_signature("".join(t for _, t, _ in merged_preview))
                if not local_ocr_sig:
                    self.skipped.emit("Auto-skip: No meaningful text")
                    return

                if self.last_auto_ocr_sig:
                    sim = SequenceMatcher(None, local_ocr_sig, self.last_auto_ocr_sig).ratio()
                    if sim >= 0.82:
                        self.skipped.emit("Auto-skip: Text unchanged")
                        return

                if local_ocr_sig in self.auto_ocr_cache:
                    cached_boxes = self.auto_ocr_cache[local_ocr_sig]
                    self.result_ocr_sig = local_ocr_sig
                    self._prepare_patches(cached_boxes, models_dir)
                    self.finished.emit(cached_boxes, f"Translated {len(cached_boxes)} block(s) via Text Cache [{store.game_id}]")
                    return

        game_name = os.path.splitext(os.path.basename(self.rom_path))[0]
        csv_path = os.path.join(trans_dir, f"{game_name}_translations.csv")

        hf_enabled = bool(self.config.get("ai_translation", "hf_space_enabled", True))
        hf_space_id = str(
            self.config.get("ai_translation", "hf_space_id", DEFAULT_HF_SPACE_ID) or DEFAULT_HF_SPACE_ID
        ).strip()
        hf_token = str(self.config.get("credentials", "hf_token") or "").strip()

        groq_api_key = str(self.config.get("credentials", "groq_api_key") or "").strip()
        if not groq_api_key:
            groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()

        criteria = get_or_create_game_criteria(
            self.rom_path,
            src_lang,
            tgt_lang,
            groq_api_key,
            trans_dir,
            hf_enabled=hf_enabled,
            hf_space_id=hf_space_id,
            hf_token=hf_token,
        )
        glossary_prompt = store.format_glossary_prompt()

        # 2. Primary Multimodal Vision: Hugging Face Space FIRST, Groq Vision SECOND
        vision_boxes, vision_model = None, None
        vision_source = ""

        if hf_enabled and hf_space_id:
            vision_boxes, vision_model = run_hf_vision(
                self.frame_data,
                src_lang,
                tgt_lang,
                criteria,
                glossary_text=glossary_prompt,
                space_id=hf_space_id,
                hf_token=hf_token,
            )
            if vision_boxes:
                vision_source = "Hugging Face Space"
            else:
                print("[AI Translator] Hugging Face Space unavailable or returned no items. Falling back to Groq Vision...")

        if not vision_boxes and groq_api_key:
            vision_boxes, vision_model = run_groq_vision(
                self.frame_data, src_lang, tgt_lang, groq_api_key, criteria, glossary_text=glossary_prompt
            )
            if vision_boxes:
                vision_source = "Groq Vision"
            else:
                print("[AI Translator] Groq Vision rate limited or unavailable. Falling back to local OCR...")

        if vision_boxes:
            if inpaint_enabled:
                if precomputed_ocr is None:
                    if img_np is None:
                        img_np = self.preprocess_image(self.frame_data, scale=SCALE)
                    precomputed_ocr, precomputed_engine = self._run_local_ocr_raw(img_np, src_lang, models_dir)
                if precomputed_ocr:
                    lines_240, semantic_blocks = self._raw_ocr_to_240(precomputed_ocr, SCALE)
                    align_vision_boxes_with_ocr(vision_boxes, lines_240, semantic_blocks)

            self._apply_store_overrides(store, vision_boxes, src_lang, tgt_lang)
            store.save_frame_layout(frame_hash, vision_boxes)
            store.save_cache()
            self._export_csv_mirror(csv_path, store)
            self.result_ocr_sig = local_ocr_sig or _normalize_ocr_signature(
                "".join((b.speaker_orig + b.original) for b in vision_boxes)
            )
            self._prepare_patches(vision_boxes, models_dir)
            status_msg = f"Translated {len(vision_boxes)} block(s) via {vision_source} [{vision_model}]"
            self.finished.emit(vision_boxes, status_msg)
            return

        # 3. Offline OCR + LLM / HF Space / Groq Text Fallback
        if precomputed_ocr is not None and precomputed_engine != "None":
            results, ocr_engine_name = precomputed_ocr, precomputed_engine
        else:
            if img_np is None:
                img_np = self.preprocess_image(self.frame_data, scale=SCALE)
            results, ocr_engine_name = self._run_local_ocr_raw(img_np, src_lang, models_dir)

        if not results:
            if self.auto_triggered:
                self.skipped.emit("No text detected on screen.")
            else:
                self.error.emit("No text detected on screen.")
            return

        _, semantic_blocks = self._raw_ocr_to_240(results, SCALE)

        llm_model_path = find_best_llm_model(models_dir)
        using_local_llm = os.path.exists(llm_model_path) and (get_llama_instance(llm_model_path) is not None)

        translated_boxes = []
        cache_hits = 0

        for blk in semantic_blocks:
            orig = blk["original"]
            speaker_orig = blk["speaker_orig"]
            cat = blk["category"]

            gl_term = store.lookup_term(orig)
            gl_speaker = store.lookup_term(speaker_orig) if speaker_orig else None
            cached_entry = store.lookup_entry(src_lang, tgt_lang, cat, speaker_orig, orig)

            if gl_term:
                translated_text = gl_term
                speaker_trans = gl_speaker or (cached_entry.get("speaker_trans") if cached_entry else speaker_orig)
                cache_hits += 1
            elif cached_entry and cached_entry.get("translated"):
                translated_text = cached_entry["translated"]
                speaker_trans = gl_speaker or cached_entry.get("speaker_trans", "")
                cache_hits += 1
            else:
                speaker_trans, translated_text = LocalQwenTranslator.translate_block(
                    llm_model_path,
                    orig,
                    cat,
                    speaker_orig,
                    src_lang,
                    tgt_lang,
                    criteria,
                    groq_api_key,
                    glossary_text=glossary_prompt,
                    hf_enabled=hf_enabled,
                    hf_space_id=hf_space_id,
                    hf_token=hf_token,
                )
                if gl_speaker:
                    speaker_trans = gl_speaker
                store.upsert_entry(
                    src_lang, tgt_lang, cat, speaker_orig, speaker_trans,
                    orig, translated_text, locked=False, save_now=False
                )

            translated_boxes.append(TranslatedBox(
                blk["x"], blk["y"], blk["w"], blk["h"],
                orig, translated_text,
                category=cat,
                speaker_orig=speaker_orig,
                speaker_trans=speaker_trans,
                has_box=True,
            ))

        store.save_frame_layout(frame_hash, translated_boxes)
        store.save_cache()
        self._export_csv_mirror(csv_path, store)

        self.result_ocr_sig = local_ocr_sig or _normalize_ocr_signature(
            "".join((b.speaker_orig + b.original) for b in translated_boxes)
        )
        self._prepare_patches(translated_boxes, models_dir)

        if cache_hits == len(translated_boxes) and translated_boxes:
            backend_tag = f"Cache/Glossary [{store.game_id}]"
        elif using_local_llm:
            backend_tag = os.path.basename(llm_model_path).replace(".gguf", "")
        elif hf_enabled and hf_space_id:
            backend_tag = f"HF:{hf_space_id.split('/')[-1]}"
        else:
            backend_tag = "Cloud Text API"

        status_msg = f"Translated {len(translated_boxes)} block(s) via {ocr_engine_name} & {backend_tag}"
        self.finished.emit(translated_boxes, status_msg)

    def _merge_horizontal_row_fragments(self, results) -> list:
        boxes = []
        for (bbox, text, conf) in results:
            if conf < 0.05 or not text.strip():
                continue
            xs = [pt[0] for pt in bbox]
            ys = [pt[1] for pt in bbox]
            boxes.append({
                "x1": min(xs), "y1": min(ys),
                "x2": max(xs), "y2": max(ys),
                "text": text.strip()
            })

        boxes.sort(key=lambda b: b["y1"])
        lines = []

        for box in boxes:
            box_cy = (box["y1"] + box["y2"]) / 2.0
            box_h = box["y2"] - box["y1"]
            merged = False

            for line in lines:
                line_cy = (line["y1"] + line["y2"]) / 2.0
                line_h = line["y2"] - line["y1"]
                overlap_h = max(0, min(line["y2"], box["y2"]) - max(line["y1"], box["y1"]))

                if abs(line_cy - box_cy) < (max(line_h, box_h) * 0.45) or overlap_h > (box_h * 0.35):
                    line["boxes"].append(box)
                    line["y1"] = min(line["y1"], box["y1"])
                    line["y2"] = max(line["y2"], box["y2"])
                    merged = True
                    break

            if not merged:
                lines.append({
                    "y1": box["y1"], "y2": box["y2"],
                    "boxes": [box]
                })

        final_boxes = []
        for line in lines:
            row_items = sorted(line["boxes"], key=lambda b: b["x1"])
            segments = []
            for item in row_items:
                item_h = max(8.0, item["y2"] - item["y1"])
                if not segments:
                    segments.append([item])
                else:
                    prev = segments[-1][-1]
                    prev_h = max(8.0, prev["y2"] - prev["y1"])
                    x_gap = item["x1"] - prev["x2"]
                    if x_gap <= max(prev_h, item_h) * 0.90:
                        segments[-1].append(item)
                    else:
                        segments.append([item])

            for seg in segments:
                combined = "".join(b["text"] for b in seg).strip()
                if combined:
                    sx1 = min(b["x1"] for b in seg)
                    sy1 = min(b["y1"] for b in seg)
                    sx2 = max(b["x2"] for b in seg)
                    sy2 = max(b["y2"] for b in seg)
                    final_boxes.append((
                        [[sx1, sy1], [sx2, sy1], [sx2, sy2], [sx1, sy2]],
                        combined,
                        1.0
                    ))
        return final_boxes

    def _export_csv_mirror(self, filepath: str, store: GlossaryAndCacheStore):
        try:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "SourceLang", "TargetLang", "Category",
                    "SpeakerOrig", "SpeakerTrans", "OriginalText", "TranslatedText"
                ])
                for entry in store.entries.values():
                    writer.writerow([
                        entry.get("source_lang", ""),
                        entry.get("target_lang", ""),
                        entry.get("category", "dialogue"),
                        entry.get("speaker_orig", ""),
                        entry.get("speaker_trans", ""),
                        entry.get("original", ""),
                        entry.get("translated", ""),
                    ])
        except Exception:
            pass
```

---

## Part 4: Configuration Dialogs

### 4.1 `gba_studio/tools/ai/glossary_dialog.py`
```python
# glossary_dialog.py
import os
import json

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget, QWidget,
    QMessageBox, QFileDialog, QComboBox, QCheckBox, QDialogButtonBox,
    QGroupBox, QSpinBox, QProgressBar, QSlider
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from .engines import LANG_NAMES, DEFAULT_HF_SPACE_ID, reset_hf_client
from .glossary import GlossaryAndCacheStore
from .auto_trigger import AutoTranslateDetector
from .neural_inpaint import is_migan_available, download_migan_model


class _MiganDownloadThread(QThread):
    progress = pyqtSignal(int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def run(self):
        try:
            def _on_prog(done, total):
                pct = max(0, min(100, int((done * 100) / max(1, total))))
                self.progress.emit(pct)

            path = download_migan_model(progress_cb=_on_prog)
            self.finished_ok.emit(path)
        except Exception as e:
            self.failed.emit(str(e))


class TranslationGlossaryDialog(QDialog):
    """Per-Game Glossary & Translation Cache Editor."""

    def __init__(self, rom_path: str, config, game_code: str = "", parent=None):
        super().__init__(parent)
        self.rom_path = rom_path
        self.config = config
        self.game_code = game_code

        trans_dir = self.config.get("paths", "translations") or "translations"
        self.store = GlossaryAndCacheStore(self.rom_path, trans_dir=trans_dir, game_code=self.game_code)

        self.setWindowTitle(f"Per-Game Glossary & Translation Cache [{self.store.game_id}]")
        self.resize(920, 600)

        self._init_ui()
        self.refresh_glossary_table()
        self.refresh_cache_table()

    def _init_ui(self):
        root = QVBoxLayout(self)

        info = QLabel(
            f"<b>Game ID:</b> <code>{self.store.game_id}</code> &nbsp;|&nbsp; "
            f"<b>Glossary:</b> <code>{os.path.basename(self.store.glossary_path)}</code> &nbsp;|&nbsp; "
            f"<b>Cache:</b> <code>{os.path.basename(self.store.cache_path)}</code>"
        )
        info.setStyleSheet("background: #181C24; color: #CFD8DC; padding: 8px; border-radius: 5px;")
        root.addWidget(info)

        self.tabs = QTabWidget()

        tab_glossary = QWidget()
        gl_layout = QVBoxLayout(tab_glossary)

        add_row = QHBoxLayout()
        self.gl_src_edit = QLineEdit()
        self.gl_src_edit.setPlaceholderText("Original term / name (e.g. 曹操 or はい)")
        add_row.addWidget(self.gl_src_edit, 2)

        self.gl_tgt_edit = QLineEdit()
        self.gl_tgt_edit.setPlaceholderText("Target translation (e.g. Tào Tháo or Có)")
        add_row.addWidget(self.gl_tgt_edit, 2)

        btn_add_term = QPushButton("+ Add / Update Term")
        btn_add_term.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 5px 10px;")
        btn_add_term.clicked.connect(self.add_glossary_term)
        add_row.addWidget(btn_add_term)
        gl_layout.addLayout(add_row)

        self.gl_table = QTableWidget(0, 2)
        self.gl_table.setHorizontalHeaderLabels(["Original Term", "Translated Term"])
        self.gl_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.gl_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.gl_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.gl_table.itemChanged.connect(self.on_glossary_item_changed)
        gl_layout.addWidget(self.gl_table)

        gl_btns = QHBoxLayout()
        btn_del_term = QPushButton("Delete Selected")
        btn_del_term.clicked.connect(self.delete_glossary_term)
        gl_btns.addWidget(btn_del_term)

        btn_import_gl = QPushButton("Import JSON...")
        btn_import_gl.clicked.connect(self.import_glossary_json)
        gl_btns.addWidget(btn_import_gl)

        btn_export_gl = QPushButton("Export JSON...")
        btn_export_gl.clicked.connect(self.export_glossary_json)
        gl_btns.addWidget(btn_export_gl)

        gl_btns.addStretch()
        gl_layout.addLayout(gl_btns)
        self.tabs.addTab(tab_glossary, "📖 Per-Game Glossary")

        tab_cache = QWidget()
        ca_layout = QVBoxLayout(tab_cache)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search:"))
        self.cache_search_edit = QLineEdit()
        self.cache_search_edit.setPlaceholderText("Filter by original, translated, or speaker text...")
        self.cache_search_edit.textChanged.connect(self.refresh_cache_table)
        filter_row.addWidget(self.cache_search_edit)

        self.cache_count_lbl = QLabel("0 entries")
        self.cache_count_lbl.setStyleSheet("color: #90A4AE;")
        filter_row.addWidget(self.cache_count_lbl)
        ca_layout.addLayout(filter_row)

        self.cache_table = QTableWidget(0, 5)
        self.cache_table.setHorizontalHeaderLabels(["Category", "Speaker", "Original Text", "Translated Text", "Locked"])
        self.cache_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.cache_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.cache_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.cache_table.itemChanged.connect(self.on_cache_item_changed)
        ca_layout.addWidget(self.cache_table)

        ca_btns = QHBoxLayout()
        btn_promote = QPushButton("⬆ Promote Selected to Glossary")
        btn_promote.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold;")
        btn_promote.clicked.connect(self.promote_cache_to_glossary)
        ca_btns.addWidget(btn_promote)

        btn_del_cache = QPushButton("Delete Selected")
        btn_del_cache.clicked.connect(self.delete_selected_cache)
        ca_btns.addWidget(btn_del_cache)

        btn_clear_unlocked = QPushButton("Clear Unlocked Cache")
        btn_clear_unlocked.clicked.connect(self.clear_unlocked_cache)
        ca_btns.addWidget(btn_clear_unlocked)

        ca_btns.addStretch()
        ca_layout.addLayout(ca_btns)
        self.tabs.addTab(tab_cache, "⚡ Translation Cache")

        root.addWidget(self.tabs)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)

    def refresh_glossary_table(self):
        self.gl_table.blockSignals(True)
        items = sorted(self.store.terms.items(), key=lambda kv: kv[0])
        self.gl_table.setRowCount(len(items))
        for row, (src, tgt) in enumerate(items):
            src_item = QTableWidgetItem(src)
            src_item.setData(Qt.ItemDataRole.UserRole, src)
            tgt_item = QTableWidgetItem(tgt)
            self.gl_table.setItem(row, 0, src_item)
            self.gl_table.setItem(row, 1, tgt_item)
        self.gl_table.blockSignals(False)

    def add_glossary_term(self):
        src = self.gl_src_edit.text().strip()
        tgt = self.gl_tgt_edit.text().strip()
        if not src or not tgt:
            QMessageBox.warning(self, "Notice", "Please enter both the original term and its translation.")
            return
        self.store.set_term(src, tgt, save_now=True)
        self.gl_src_edit.clear()
        self.gl_tgt_edit.clear()
        self.refresh_glossary_table()

    def on_glossary_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        src_item = self.gl_table.item(row, 0)
        tgt_item = self.gl_table.item(row, 1)
        if not src_item or not tgt_item:
            return
        old_src = src_item.data(Qt.ItemDataRole.UserRole)
        new_src = src_item.text().strip()
        new_tgt = tgt_item.text().strip()
        if not new_src or not new_tgt:
            self.refresh_glossary_table()
            return
        if old_src and old_src != new_src:
            self.store.delete_term(old_src, save_now=False)
        self.store.set_term(new_src, new_tgt, save_now=True)
        src_item.setData(Qt.ItemDataRole.UserRole, new_src)

    def delete_glossary_term(self):
        rows = self.gl_table.selectionModel().selectedRows()
        if not rows:
            return
        for r in rows:
            src_item = self.gl_table.item(r.row(), 0)
            if src_item:
                self.store.delete_term(src_item.text().strip(), save_now=False)
        self.store.save_glossary()
        self.refresh_glossary_table()

    def import_glossary_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Glossary JSON", self.store.trans_dir, "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            terms = data.get("terms", data) if isinstance(data, dict) else {}
            if not isinstance(terms, dict):
                raise ValueError("Expected a JSON object mapping source terms to translations.")
            added = 0
            for k, v in terms.items():
                if str(k).strip() and str(v).strip():
                    self.store.set_term(str(k), str(v), save_now=False)
                    added += 1
            self.store.save_glossary()
            self.refresh_glossary_table()
            QMessageBox.information(self, "Imported", f"Imported {added} glossary term(s).")
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import glossary:\n{e}")

    def export_glossary_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Glossary JSON", self.store.glossary_path, "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"game_id": self.store.game_id, "terms": self.store.terms}, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "Exported", f"Exported glossary to:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export glossary:\n{e}")

    def refresh_cache_table(self):
        query = self.cache_search_edit.text().strip().lower() if hasattr(self, "cache_search_edit") else ""
        filtered = []
        for key, entry in self.store.entries.items():
            if query:
                hay = " ".join([
                    entry.get("original", ""),
                    entry.get("translated", ""),
                    entry.get("speaker_orig", ""),
                    entry.get("speaker_trans", ""),
                ]).lower()
                if query not in hay:
                    continue
            filtered.append((key, entry))

        self.cache_table.blockSignals(True)
        self.cache_table.setRowCount(len(filtered))
        for row, (key, entry) in enumerate(filtered):
            cat_item = QTableWidgetItem(entry.get("category", "dialogue"))
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            cat_item.setData(Qt.ItemDataRole.UserRole, key)
            self.cache_table.setItem(row, 0, cat_item)

            sp_txt = entry.get("speaker_trans") or entry.get("speaker_orig") or ""
            self.cache_table.setItem(row, 1, QTableWidgetItem(sp_txt))

            orig_item = QTableWidgetItem(entry.get("original", ""))
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.cache_table.setItem(row, 2, orig_item)

            self.cache_table.setItem(row, 3, QTableWidgetItem(entry.get("translated", "")))

            lock_item = QTableWidgetItem()
            lock_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            lock_item.setCheckState(Qt.CheckState.Checked if entry.get("locked") else Qt.CheckState.Unchecked)
            self.cache_table.setItem(row, 4, lock_item)

        self.cache_table.blockSignals(False)
        self.cache_count_lbl.setText(f"{len(filtered)} / {len(self.store.entries)} entries")

    def on_cache_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        col = item.column()
        cat_item = self.cache_table.item(row, 0)
        if not cat_item:
            return
        key = cat_item.data(Qt.ItemDataRole.UserRole)
        entry = self.store.entries.get(key)
        if not entry:
            return

        if col == 1:
            entry["speaker_trans"] = item.text().strip()
            entry["locked"] = True
        elif col == 3:
            entry["translated"] = item.text().strip()
            entry["locked"] = True
        elif col == 4:
            entry["locked"] = (item.checkState() == Qt.CheckState.Checked)

        self.store.save_cache()
        if col in (1, 3):
            self.cache_table.blockSignals(True)
            lock_item = self.cache_table.item(row, 4)
            if lock_item:
                lock_item.setCheckState(Qt.CheckState.Checked if entry["locked"] else Qt.CheckState.Unchecked)
            self.cache_table.blockSignals(False)

    def promote_cache_to_glossary(self):
        rows = self.cache_table.selectionModel().selectedRows()
        if not rows:
            return
        promoted = 0
        for r in rows:
            cat_item = self.cache_table.item(r.row(), 0)
            if not cat_item:
                continue
            key = cat_item.data(Qt.ItemDataRole.UserRole)
            entry = self.store.entries.get(key)
            if not entry:
                continue
            orig = entry.get("original", "").strip()
            trans = entry.get("translated", "").strip()
            if orig and trans:
                self.store.set_term(orig, trans, save_now=False)
                promoted += 1
            sp_orig = entry.get("speaker_orig", "").strip()
            sp_tr = entry.get("speaker_trans", "").strip()
            if sp_orig and sp_tr:
                self.store.set_term(sp_orig, sp_tr, save_now=False)

        if promoted:
            self.store.save_glossary()
            self.refresh_glossary_table()
            QMessageBox.information(self, "Promoted", f"Added {promoted} entry/entries to the Per-Game Glossary.")

    def delete_selected_cache(self):
        rows = self.cache_table.selectionModel().selectedRows()
        if not rows:
            return
        for r in rows:
            cat_item = self.cache_table.item(r.row(), 0)
            if cat_item:
                key = cat_item.data(Qt.ItemDataRole.UserRole)
                self.store.delete_entry(key, save_now=False)
        self.store.save_cache()
        self.refresh_cache_table()

    def clear_unlocked_cache(self):
        reply = QMessageBox.question(
            self,
            "Clear Unlocked Cache",
            "Clear all unlocked AI translations and frame snapshots?\n(Manually edited/locked entries and your Glossary will be kept.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.store.clear_unlocked_cache()
            self.refresh_cache_table()


class AITranslationConfigDialog(QDialog):
    """Configures AI translation languages, HF Space (primary), Groq fallback, MI-GAN inpainting & auto-trigger."""

    def __init__(self, config, parent=None, rom_path: str = "", game_code: str = ""):
        super().__init__(parent)
        self.config = config
        self.rom_path = rom_path
        self.game_code = game_code
        self._dl_thread = None
        self.setWindowTitle("Configure AI Translation")
        self.resize(540, 680)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source Language:"))
        self.src_combo = QComboBox()
        for code, name in LANG_NAMES.items():
            self.src_combo.addItem(f"{name} ({code})", code)
        cur_src = self.config.get("ai_translation", "source_lang", "ja")
        idx = self.src_combo.findData(cur_src)
        if idx >= 0:
            self.src_combo.setCurrentIndex(idx)
        src_row.addWidget(self.src_combo)
        layout.addLayout(src_row)

        tgt_row = QHBoxLayout()
        tgt_row.addWidget(QLabel("Target Language:"))
        self.tgt_combo = QComboBox()
        for code, name in LANG_NAMES.items():
            self.tgt_combo.addItem(f"{name} ({code})", code)
        cur_tgt = self.config.get("ai_translation", "target_lang", "vi")
        idx = self.tgt_combo.findData(cur_tgt)
        if idx >= 0:
            self.tgt_combo.setCurrentIndex(idx)
        tgt_row.addWidget(self.tgt_combo)
        layout.addLayout(tgt_row)

        # Primary Cloud Engine: Hugging Face Space
        hf_grp = QGroupBox("Primary Cloud Engine: Hugging Face Space (Priority #1)")
        hf_lay = QVBoxLayout(hf_grp)

        self.hf_enabled_cb = QCheckBox("Use Hugging Face Space before Groq API")
        self.hf_enabled_cb.setChecked(bool(self.config.get("ai_translation", "hf_space_enabled", True)))
        hf_lay.addWidget(self.hf_enabled_cb)

        space_row = QHBoxLayout()
        space_row.addWidget(QLabel("Space ID:"))
        self.hf_space_edit = QLineEdit()
        self.hf_space_edit.setPlaceholderText(DEFAULT_HF_SPACE_ID)
        self.hf_space_edit.setText(
            str(self.config.get("ai_translation", "hf_space_id", DEFAULT_HF_SPACE_ID) or DEFAULT_HF_SPACE_ID)
        )
        space_row.addWidget(self.hf_space_edit, 1)
        hf_lay.addLayout(space_row)

        hf_tok_row = QHBoxLayout()
        hf_tok_row.addWidget(QLabel("HF Token (Optional):"))
        self.hf_token_edit = QLineEdit()
        self.hf_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token_edit.setPlaceholderText("hf_... (only needed for private spaces)")
        self.hf_token_edit.setText(str(self.config.get("credentials", "hf_token", "") or ""))
        hf_tok_row.addWidget(self.hf_token_edit, 1)

        self.btn_show_hf = QPushButton("👁")
        self.btn_show_hf.setCheckable(True)
        self.btn_show_hf.setFixedWidth(32)
        self.btn_show_hf.toggled.connect(
            lambda ch: self.hf_token_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password
            )
        )
        hf_tok_row.addWidget(self.btn_show_hf)
        hf_lay.addLayout(hf_tok_row)
        layout.addWidget(hf_grp)

        # Fallback Cloud Engine: Groq
        cred_grp = QGroupBox("Fallback Cloud Engine: Groq API (Priority #2)")
        cred_lay = QHBoxLayout(cred_grp)
        cred_lay.addWidget(QLabel("Groq API Key:"))
        self.groq_key_edit = QLineEdit()
        self.groq_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_key_edit.setPlaceholderText("gsk_...")
        self.groq_key_edit.setText(str(self.config.get("credentials", "groq_api_key", "") or ""))
        cred_lay.addWidget(self.groq_key_edit, 1)

        self.btn_show_key = QPushButton("👁")
        self.btn_show_key.setCheckable(True)
        self.btn_show_key.setFixedWidth(32)
        self.btn_show_key.toggled.connect(
            lambda ch: self.groq_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password
            )
        )
        cred_lay.addWidget(self.btn_show_key)
        layout.addWidget(cred_grp)

        disp_grp = QGroupBox("Display & Neural Visual Inpainting")
        disp_lay = QVBoxLayout(disp_grp)

        self.inpaint_cb = QCheckBox("In-Place Visual Inpainting (Replace Text Directly in Textbox)")
        self.inpaint_cb.setChecked(bool(self.config.get("ai_translation", "inpaint_enabled", True)))
        disp_lay.addWidget(self.inpaint_cb)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Inpainting Engine:"))
        self.inpaint_mode_combo = QComboBox()
        self.inpaint_mode_combo.addItem("Auto (MI-GAN ONNX if available, else Fast)", "auto")
        self.inpaint_mode_combo.addItem("MI-GAN Neural ONNX (Best for Baked Art & Textboxes)", "onnx")
        self.inpaint_mode_combo.addItem("Fast Algorithmic (Zero-Model Stroke/Gradient)", "fast")
        cur_mode = str(self.config.get("ai_translation", "inpaint_mode", "auto") or "auto")
        m_idx = self.inpaint_mode_combo.findData(cur_mode)
        if m_idx >= 0:
            self.inpaint_mode_combo.setCurrentIndex(m_idx)
        mode_row.addWidget(self.inpaint_mode_combo, 1)
        disp_lay.addLayout(mode_row)

        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Inpainted Text Size:"))
        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(60, 160)
        self.font_slider.setSingleStep(5)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(60, 160)
        self.font_spin.setSingleStep(5)
        self.font_spin.setSuffix("%")

        cur_fscale = int(round(float(self.config.get("ai_translation", "inpaint_font_scale", 1.0) or 1.0) * 100))
        cur_fscale = max(60, min(160, cur_fscale))
        self.font_slider.setValue(cur_fscale)
        self.font_spin.setValue(cur_fscale)
        self.font_slider.valueChanged.connect(self.font_spin.setValue)
        self.font_spin.valueChanged.connect(self.font_slider.setValue)

        font_row.addWidget(self.font_slider, 1)
        font_row.addWidget(self.font_spin)
        disp_lay.addLayout(font_row)

        migan_row = QHBoxLayout()
        self.migan_status_lbl = QLabel()
        self.btn_dl_migan = QPushButton("⬇ Download MI-GAN ONNX (28 MB)")
        self.btn_dl_migan.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 4px 8px;")
        self.btn_dl_migan.clicked.connect(self._start_migan_download)
        migan_row.addWidget(self.migan_status_lbl, 1)
        migan_row.addWidget(self.btn_dl_migan)
        disp_lay.addLayout(migan_row)

        self.migan_prog = QProgressBar()
        self.migan_prog.setRange(0, 100)
        self.migan_prog.setVisible(False)
        disp_lay.addWidget(self.migan_prog)
        self._refresh_migan_status()

        self.inpaint_clear_cb = QCheckBox("Auto-Clear Inpainting When Textbox Closes")
        self.inpaint_clear_cb.setChecked(bool(self.config.get("ai_translation", "inpaint_auto_clear", True)))
        disp_lay.addWidget(self.inpaint_clear_cb)

        self.overlay_cb = QCheckBox("Show Floating Subtitle Overlay in Fullscreen Mode (Fallback)")
        self.overlay_cb.setChecked(bool(self.config.get("ai_translation", "overlay_enabled", True)))
        disp_lay.addWidget(self.overlay_cb)

        layout.addWidget(disp_grp)

        auto_grp = QGroupBox("Smart Auto-Translate Detector")
        auto_lay = QVBoxLayout(auto_grp)

        self.auto_cb = QCheckBox("Auto-Translate on Scene / Dialogue Change")
        self.auto_cb.setChecked(bool(self.config.get("ai_translation", "auto_translate", False)))
        auto_lay.addWidget(self.auto_cb)

        roi_row = QHBoxLayout()
        roi_row.addWidget(QLabel("Watch Region:"))
        self.roi_combo = QComboBox()
        for key, lbl in AutoTranslateDetector.ROI_MODES:
            self.roi_combo.addItem(lbl, key)
        cur_roi = self.config.get("ai_translation", "auto_roi_mode", "auto")
        r_idx = self.roi_combo.findData(cur_roi)
        if r_idx >= 0:
            self.roi_combo.setCurrentIndex(r_idx)
        roi_row.addWidget(self.roi_combo)
        auto_lay.addLayout(roi_row)

        timing_row = QHBoxLayout()
        timing_row.addWidget(QLabel("Settle Debounce:"))
        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(250, 3000)
        self.debounce_spin.setSingleStep(100)
        self.debounce_spin.setSuffix(" ms")
        self.debounce_spin.setValue(int(self.config.get("ai_translation", "auto_debounce_ms", 1200) or 1200))
        timing_row.addWidget(self.debounce_spin)

        timing_row.addWidget(QLabel("API Cooldown:"))
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(500, 10000)
        self.cooldown_spin.setSingleStep(200)
        self.cooldown_spin.setSuffix(" ms")
        self.cooldown_spin.setValue(int(self.config.get("ai_translation", "auto_cooldown_ms", 4000) or 4000))
        timing_row.addWidget(self.cooldown_spin)
        auto_lay.addLayout(timing_row)

        layout.addWidget(auto_grp)

        btn_glossary = QPushButton("📖 Open Per-Game Glossary & Cache...")
        btn_glossary.setEnabled(bool(self.rom_path))
        btn_glossary.clicked.connect(self._open_glossary)
        layout.addWidget(btn_glossary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_migan_status(self):
        if is_migan_available():
            self.migan_status_lbl.setText("✅ MI-GAN ONNX Ready (models/migan_pipeline_v2.onnx)")
            self.migan_status_lbl.setStyleSheet("color: #81C784; font-size: 11px;")
            self.btn_dl_migan.setText("Re-download MI-GAN")
        else:
            self.migan_status_lbl.setText("⚠ MI-GAN not downloaded (using Fast fallback)")
            self.migan_status_lbl.setStyleSheet("color: #FFB74D; font-size: 11px;")
            self.btn_dl_migan.setText("⬇ Download MI-GAN ONNX (28 MB)")

    def _start_migan_download(self):
        if self._dl_thread and self._dl_thread.isRunning():
            return
        self.btn_dl_migan.setEnabled(False)
        self.migan_prog.setValue(0)
        self.migan_prog.setVisible(True)
        self.migan_status_lbl.setText("Downloading MI-GAN ONNX...")
        self.migan_status_lbl.setStyleSheet("color: #4DD0E1; font-size: 11px;")

        self._dl_thread = _MiganDownloadThread(self)
        self._dl_thread.progress.connect(self.migan_prog.setValue)
        self._dl_thread.finished_ok.connect(self._on_migan_downloaded)
        self._dl_thread.failed.connect(self._on_migan_failed)
        self._dl_thread.start()

    def _on_migan_downloaded(self, path: str):
        self.migan_prog.setVisible(False)
        self.btn_dl_migan.setEnabled(True)
        self._refresh_migan_status()
        QMessageBox.information(self, "Download Complete", f"MI-GAN ONNX model saved to:\n{path}")

    def _on_migan_failed(self, err: str):
        self.migan_prog.setVisible(False)
        self.btn_dl_migan.setEnabled(True)
        self._refresh_migan_status()
        QMessageBox.warning(self, "Download Error", f"Failed to download MI-GAN model:\n{err}")

    def _open_glossary(self):
        if not self.rom_path:
            return
        dlg = TranslationGlossaryDialog(self.rom_path, self.config, game_code=self.game_code, parent=self)
        dlg.exec()

    def _save(self):
        self.config.set("ai_translation", "hf_space_enabled", self.hf_enabled_cb.isChecked())
        self.config.set("ai_translation", "hf_space_id", self.hf_space_edit.text().strip() or DEFAULT_HF_SPACE_ID)
        self.config.set("credentials", "hf_token", self.hf_token_edit.text().strip())
        self.config.set("credentials", "groq_api_key", self.groq_key_edit.text().strip())
        reset_hf_client()

        self.config.set("ai_translation", "source_lang", self.src_combo.currentData())
        self.config.set("ai_translation", "target_lang", self.tgt_combo.currentData())
        self.config.set("ai_translation", "inpaint_enabled", self.inpaint_cb.isChecked())
        self.config.set("ai_translation", "inpaint_mode", self.inpaint_mode_combo.currentData())
        self.config.set("ai_translation", "inpaint_font_scale", round(self.font_spin.value() / 100.0, 2))
        self.config.set("ai_translation", "inpaint_auto_clear", self.inpaint_clear_cb.isChecked())
        self.config.set("ai_translation", "overlay_enabled", self.overlay_cb.isChecked())
        self.config.set("ai_translation", "auto_translate", self.auto_cb.isChecked())
        self.config.set("ai_translation", "auto_roi_mode", self.roi_combo.currentData())
        self.config.set("ai_translation", "auto_debounce_ms", self.debounce_spin.value())
        self.config.set("ai_translation", "auto_cooldown_ms", self.cooldown_spin.value())
        self.accept()
```

### 4.2 `gba_studio/ui/ra_dialog.py`
```python
# ra_dialog.py
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QGroupBox, QDialogButtonBox
)

from ..tools.ai.engines import DEFAULT_HF_SPACE_ID, reset_hf_client


class RetroAchievementsSettingsDialog(QDialog):
    """Configures RetroAchievements & AI API credentials stored directly in mgba_config.json."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.mw = main_window
        self.config = main_window.config
        self.ra = main_window.ra_manager

        self.setWindowTitle("RetroAchievements & Credentials Settings")
        self.resize(500, 560)
        self._init_ui()
        self.ra.login_tested.connect(self._on_login_tested)

    def _init_ui(self):
        root = QVBoxLayout(self)

        note = QLabel(
            "All credentials are stored locally inside <code>mgba_config.json</code> "
            "(no environment variables required)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("background: #161A22; color: #B0BEC5; padding: 8px; border-radius: 5px;")
        root.addWidget(note)

        # 1. RetroAchievements Account
        ra_grp = QGroupBox("RetroAchievements Account")
        ra_lay = QVBoxLayout(ra_grp)

        r_user = QHBoxLayout()
        r_user.addWidget(QLabel("Username:"))
        self.ra_user_edit = QLineEdit(str(self.config.get("credentials", "ra_username", "") or ""))
        r_user.addWidget(self.ra_user_edit)
        ra_lay.addLayout(r_user)

        r_pass = QHBoxLayout()
        r_pass.addWidget(QLabel("Password:"))
        self.ra_pass_edit = QLineEdit(str(self.config.get("credentials", "ra_password", "") or ""))
        self.ra_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        r_pass.addWidget(self.ra_pass_edit)
        ra_lay.addLayout(r_pass)

        r_tok = QHBoxLayout()
        r_tok.addWidget(QLabel("Connect Token:"))
        self.ra_token_edit = QLineEdit(str(self.config.get("credentials", "ra_token", "") or ""))
        self.ra_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ra_token_edit.setPlaceholderText("(Auto-filled on login)")
        r_tok.addWidget(self.ra_token_edit)
        ra_lay.addLayout(r_tok)

        r_web = QHBoxLayout()
        r_web.addWidget(QLabel("Web API Key (Optional):"))
        self.ra_web_edit = QLineEdit(str(self.config.get("credentials", "ra_web_api_key", "") or ""))
        self.ra_web_edit.setEchoMode(QLineEdit.EchoMode.Password)
        r_web.addWidget(self.ra_web_edit)
        ra_lay.addLayout(r_web)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("🔑 Test Login & Save Token")
        self.btn_test.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 5px 10px;")
        self.btn_test.clicked.connect(self._test_login)
        test_row.addWidget(self.btn_test)

        self.login_status_lbl = QLabel("Ready.")
        self.login_status_lbl.setStyleSheet("color: #90A4AE; font-size: 11px;")
        test_row.addWidget(self.login_status_lbl, 1)
        ra_lay.addLayout(test_row)

        root.addWidget(ra_grp)

        # 2. RetroAchievements Behavior
        opt_grp = QGroupBox("RetroAchievements Options")
        opt_lay = QVBoxLayout(opt_grp)

        self.cb_enabled = QCheckBox("Enable RetroAchievements")
        self.cb_enabled.setChecked(bool(self.config.get("retroachievements", "enabled", False)))
        opt_lay.addWidget(self.cb_enabled)

        self.cb_hardcore = QCheckBox("Hardcore Mode (Disables Cheats & Save State Loading)")
        self.cb_hardcore.setChecked(bool(self.config.get("retroachievements", "hardcore", False)))
        opt_lay.addWidget(self.cb_hardcore)

        self.cb_panel = QCheckBox("Show Right-Side Achievements Panel")
        self.cb_panel.setChecked(bool(self.config.get("retroachievements", "show_panel", True)))
        opt_lay.addWidget(self.cb_panel)

        self.cb_popup = QCheckBox("Show RetroArch-Style Achievement Unlock Popups (Fullscreen & Windowed)")
        self.cb_popup.setChecked(bool(self.config.get("retroachievements", "popup_notifications", True)))
        opt_lay.addWidget(self.cb_popup)

        root.addWidget(opt_grp)

        # 3. AI Translation Credentials (HF Space Priority #1, Groq Fallback)
        ai_grp = QGroupBox("AI Translation Credentials (HF Space Primary, Groq Fallback)")
        ai_lay = QVBoxLayout(ai_grp)

        hf_row = QHBoxLayout()
        hf_row.addWidget(QLabel("HF Space ID:"))
        self.hf_space_edit = QLineEdit(
            str(self.config.get("ai_translation", "hf_space_id", DEFAULT_HF_SPACE_ID) or DEFAULT_HF_SPACE_ID)
        )
        hf_row.addWidget(self.hf_space_edit, 1)
        ai_lay.addLayout(hf_row)

        hf_tok_row = QHBoxLayout()
        hf_tok_row.addWidget(QLabel("HF Token (Optional):"))
        self.hf_tok_edit = QLineEdit(str(self.config.get("credentials", "hf_token", "") or ""))
        self.hf_tok_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_tok_edit.setPlaceholderText("hf_... (optional)")
        hf_tok_row.addWidget(self.hf_tok_edit, 1)

        btn_eye_hf = QPushButton("👁")
        btn_eye_hf.setCheckable(True)
        btn_eye_hf.setFixedWidth(32)
        btn_eye_hf.toggled.connect(
            lambda ch: self.hf_tok_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password
            )
        )
        hf_tok_row.addWidget(btn_eye_hf)
        ai_lay.addLayout(hf_tok_row)

        groq_row = QHBoxLayout()
        groq_row.addWidget(QLabel("Groq API Key:"))
        self.groq_edit = QLineEdit(str(self.config.get("credentials", "groq_api_key", "") or ""))
        self.groq_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_edit.setPlaceholderText("gsk_...")
        groq_row.addWidget(self.groq_edit, 1)

        btn_eye = QPushButton("👁")
        btn_eye.setCheckable(True)
        btn_eye.setFixedWidth(32)
        btn_eye.toggled.connect(
            lambda ch: self.groq_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password
            )
        )
        groq_row.addWidget(btn_eye)
        ai_lay.addLayout(groq_row)

        root.addWidget(ai_grp)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save_and_close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _test_login(self):
        self.btn_test.setEnabled(False)
        self.login_status_lbl.setText("Authenticating...")
        self.login_status_lbl.setStyleSheet("color: #FFD54F; font-size: 11px;")
        self.ra.test_login_async(
            self.ra_user_edit.text().strip(),
            self.ra_pass_edit.text(),
            self.ra_token_edit.text().strip(),
        )

    def _on_login_tested(self, ok: bool, msg: str):
        self.btn_test.setEnabled(True)
        self.login_status_lbl.setText(msg)
        self.login_status_lbl.setStyleSheet(
            f"color: {'#81C784' if ok else '#EF5350'}; font-size: 11px;"
        )
        if ok:
            tok = str(self.config.get("credentials", "ra_token", "") or "")
            if tok:
                self.ra_token_edit.setText(tok)

    def _save_and_close(self):
        self.config.set("credentials", "ra_username", self.ra_user_edit.text().strip())
        self.config.set("credentials", "ra_password", self.ra_pass_edit.text())
        self.config.set("credentials", "ra_token", self.ra_token_edit.text().strip())
        self.config.set("credentials", "ra_web_api_key", self.ra_web_edit.text().strip())
        self.config.set("credentials", "hf_token", self.hf_tok_edit.text().strip())
        self.config.set("credentials", "groq_api_key", self.groq_edit.text().strip())
        self.config.set("ai_translation", "hf_space_id", self.hf_space_edit.text().strip() or DEFAULT_HF_SPACE_ID)
        reset_hf_client()

        self.config.set("retroachievements", "enabled", self.cb_enabled.isChecked())
        self.config.set("retroachievements", "hardcore", self.cb_hardcore.isChecked())
        self.config.set("retroachievements", "show_panel", self.cb_panel.isChecked())
        self.config.set("retroachievements", "popup_notifications", self.cb_popup.isChecked())
        self.accept()
```

---

### Setup & Verification Notes
1. Install the new dependency in your virtual environment:
   ```bash
   pip install -r requirements.txt
   ```
2. **Execution Priority Order:**
   - **Vision Screen Translation (`T` or Auto-Translate):** Frame Cache → Text Cache → **Hugging Face Space (`brianknight144/Qwen3.8-27B-Uncensored-Demo` via `/respond`)** → Groq Vision (`qwen/qwen3.8-27b`) → Local OCR + Local GGUF LLM / HF Space / Groq / Google Translate.
   - **Per-Game Localization Criteria Generation:** Cached `.txt` → **Hugging Face Space** → Groq API → `UNIVERSAL_GBA_CRITERIA`.
   - **Offline Text Translation Fallback:** Local GGUF LLM → **Hugging Face Space** → Groq API → Google Translate.
3. Because `brianknight144/Qwen3.8-27B-Uncensored-Demo` is enabled by default in `DEFAULT_CONFIG`, it will be used first immediately without needing to remove your Groq key—and if the Space sleeps or errors, the worker automatically resets the Gradio client and falls back to Groq seamlessly.
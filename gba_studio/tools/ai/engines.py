# ai_translator.py
import os
import csv
import json
import re
import ssl
import glob
import io
import time
import base64
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

# Clean up stale/expired Hugging Face tokens to prevent 401 Unauthorized errors
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
_LAST_GROQ_CALL_TIME = 0.0
_GAME_CRITERIA_CACHE = {}

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


# =============================================================================
# 3. DATA MODEL & GAME CRITERIA INITIALIZER
# =============================================================================

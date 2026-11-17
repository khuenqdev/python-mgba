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
def _patch_gradio_client_bool_schema():
    """Workaround for gradio_client crashing when JSON Schema contains boolean values (e.g. additionalProperties: true/false)."""
    try:
        import gradio_client.utils as gc_utils
        import gradio_client.client as gc_client
    except Exception:
        return

    if getattr(gc_utils, "_gba_bool_schema_patched", False):
        return

    orig_get_type = getattr(gc_utils, "get_type", None)
    if callable(orig_get_type):
        def safe_get_type(schema, *args, **kwargs):
            if isinstance(schema, bool):
                return "boolean"
            if not isinstance(schema, dict):
                return "Any"
            return orig_get_type(schema, *args, **kwargs)
        gc_utils.get_type = safe_get_type

    orig_private = getattr(gc_utils, "_json_schema_to_python_type", None)
    if callable(orig_private):
        def safe_private(schema, *args, **kwargs):
            if isinstance(schema, bool):
                return "Any" if schema else "None"
            if not isinstance(schema, dict):
                return "Any"
            return orig_private(schema, *args, **kwargs)
        gc_utils._json_schema_to_python_type = safe_private

    orig_public = getattr(gc_utils, "json_schema_to_python_type", None)
    if callable(orig_public):
        def safe_public(schema, *args, **kwargs):
            if isinstance(schema, bool):
                return "Any" if schema else "None"
            if not isinstance(schema, dict):
                return "Any"
            return orig_public(schema, *args, **kwargs)
        gc_utils.json_schema_to_python_type = safe_public
        if hasattr(gc_client, "json_schema_to_python_type"):
            gc_client.json_schema_to_python_type = safe_public

    gc_utils._gba_bool_schema_patched = True


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
            _patch_gradio_client_bool_schema()
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
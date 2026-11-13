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
from .engines import (
    LANG_NAMES,
    _GAME_CRITERIA_CACHE,
    UNIVERSAL_GBA_CRITERIA,
    _has_meaningful_text,
)

class TranslatedBox:
    """Represents a categorized UI text element ('dialogue', 'menu', or 'label')."""
    def __init__(self, x, y, w, h, original, translated,
                 category: str = "dialogue", speaker_orig: str = "", speaker_trans: str = ""):
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)
        self.original = original
        self.translated = translated
        self.category = category if category in ("dialogue", "menu", "label") else "dialogue"
        self.speaker_orig = speaker_orig.strip()
        self.speaker_trans = speaker_trans.strip()


def get_or_create_game_criteria(rom_path: str, src_lang: str, tgt_lang: str, api_key: str, trans_dir: str) -> str:
    """Generates and caches localization criteria once per game ROM."""
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

    if not api_key:
        _GAME_CRITERIA_CACHE[cache_key] = UNIVERSAL_GBA_CRITERIA
        return UNIVERSAL_GBA_CRITERIA

    src_name = LANG_NAMES.get(src_lang, src_lang)
    tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang)
    prompt = (
        f"Give me a concise list of 6 localization criteria (under 110 words total) for translating the video game "
        f"'{clean_title}' from {src_name} to {tgt_name}. Cover: naming/transliteration conventions (e.g. Sino-Vietnamese/Hán-Việt "
        f"if East Asian/historical), character pronouns/honorifics, dialogue tone, and concise UI/menu phrasing. "
        f"Provide ONLY bullet points starting with '- ', no intro or commentary."
    )

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
            with urllib.request.urlopen(req, timeout=5) as resp:
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


# =============================================================================
# 4. GROQ MULTIMODAL VISION ENGINE (Semantic Grouping)
# =============================================================================
def _parse_structured_items(content: str) -> list:
    """Parses structured JSON items into categorized TranslatedBoxes."""
    clean = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.MULTILINE)
    clean = re.sub(r"```\s*$", "", clean, flags=re.MULTILINE).strip()

    boxes = []
    try:
        parsed = json.loads(clean)
        items = parsed.get("items", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
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

            boxes.append(TranslatedBox(
                10, 10 + idx * 20, 220, 18,
                orig, trans,
                category=cat,
                speaker_orig=sp_ja,
                speaker_trans=sp_tr
            ))
        if boxes:
            return boxes
    except Exception:
        match = re.search(r'\[\s*\{.*\}\s*\]', clean, re.DOTALL)
        if match:
            try:
                items = json.loads(match.group(0))
                for idx, it in enumerate(items):
                    cat = str(it.get("type", "dialogue")).lower().strip()
                    orig = str(it.get("ja") or it.get("original") or "").strip()
                    trans = str(it.get("tr") or it.get("translated") or "").strip()
                    sp_ja = str(it.get("speaker") or "").strip()
                    sp_tr = str(it.get("speaker_tr") or "").strip()
                    if _has_meaningful_text(orig) and trans:
                        boxes.append(TranslatedBox(10, 10 + idx * 20, 220, 18, orig, trans, cat, sp_ja, sp_tr))
                if boxes:
                    return boxes
            except Exception:
                pass

    for idx, line in enumerate(clean.split("\n")):
        line = line.strip()
        if "->" in line:
            parts = line.split("->", 1)
            orig = parts[0].strip().strip("*-• ")
            trans = parts[1].strip()
            if _has_meaningful_text(orig) and trans:
                cat = "dialogue" if re.search(r'[「」。、…！？]', orig) else "menu"
                boxes.append(TranslatedBox(10, 10 + idx * 20, 220, 18, orig, trans, category=cat))

    return boxes


def run_groq_vision(frame_data: np.ndarray, src_lang: str, tgt_lang: str, api_key: str, criteria: str):
    """Executes single-call vision recognition and translation with Groq Vision."""
    global _LAST_GROQ_CALL_TIME
    if not api_key:
        return None, None

    elapsed = time.time() - _LAST_GROQ_CALL_TIME
    if elapsed < 2.5:
        time.sleep(2.5 - elapsed)

    pil_img = Image.fromarray(frame_data, 'RGB')
    buffered = io.BytesIO()
    pil_img.save(buffered, format="PNG", optimize=True)
    b64_img = base64.b64encode(buffered.getvalue()).decode("utf-8")
    data_url = f"data:image/png;base64,{b64_img}"

    src_name = LANG_NAMES.get(src_lang, src_lang)
    tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang)

    prompt = (
        f"Analyze this GBA game screen and localize all meaningful {src_name} text into {tgt_name}.\n"
        "Classify each element into ONE of 3 types:\n"
        "1. \"dialogue\": Speech boxes or story narration scrolls.\n"
        "   - UNWRAP AND MERGE multi-line sentences inside the same box into ONE complete sentence before translating! Never split a sentence across visual line breaks.\n"
        "   - If a speaker nameplate appears above/beside the quote (e.g. \"曹操 ソウソウ\"), strip its furigana reading into \"speaker\" (\"曹操\"), put the translated name in \"speaker_tr\" (\"Tào Tháo\"), and do NOT output the speaker as a separate item.\n"
        "   - Strip inline furigana readings attached to Kanji in \"ja\".\n"
        "2. \"menu\": Selectable menu choices or command lists.\n"
        "   - Keep every selectable option as a SEPARATE item in top-to-bottom order. Translate as concise UI commands.\n"
        "3. \"label\": Graphic titles, chapter banners, map locations, or HUD/stat labels.\n"
        "   - Translate concisely as proper nouns or short UI tags. Ignore decorative dot bubbles like \"....\".\n\n"
        f"STYLE CRITERIA:\n{criteria}\n\n"
        "Return a JSON object with key \"items\":\n"
        "{\"items\": [{\"type\": \"dialogue|menu|label\", \"speaker\": \"\", \"speaker_tr\": \"\", \"ja\": \"...\", \"tr\": \"...\"}]}"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    for model_id in ["qwen/qwen3.8-27b", "qwen/qwen3.6-27b"]:
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
                "max_completion_tokens": 450
            }

            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=7) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                content = resp_data["choices"][0]["message"]["content"].strip()
                _LAST_GROQ_CALL_TIME = time.time()
                boxes = _parse_structured_items(content)
                if boxes:
                    return boxes, model_id
        except urllib.error.HTTPError as e:
            try:
                err_json = e.read().decode('utf-8')
                print(f"[Groq Vision on {model_id}] {e}: {err_json}")
            except Exception:
                print(f"[Groq Vision on {model_id}] {e}")
            continue
        except Exception as e:
            print(f"[Groq Vision on {model_id}] {e}")
            continue

    return None, None


# =============================================================================
# 5. OFFLINE SPATIAL LAYOUT CLASSIFIER & BATCH TRANSLATOR (MeikiOCR Fallback)
# =============================================================================

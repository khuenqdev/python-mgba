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

    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
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
def _clean_meiki_chars_furigana(chars: list) -> str:
    """Strips half-height katakana furigana readings using MeikiOCR bounding boxes."""
    if not chars:
        return ""

    kanji_heights = [
        (c['bbox'][3] - c['bbox'][1])
        for c in chars
        if re.match(r'[\u4e00-\u9faf]', c.get('char', ''))
    ]
    if not kanji_heights:
        return "".join(c.get('char', '') for c in chars)

    ref_kanji_h = statistics.median(kanji_heights)
    out = []
    i = 0
    n = len(chars)

    while i < n:
        ch = chars[i].get('char', '')
        if re.match(r'[\u4e00-\u9faf]', ch):
            out.append(ch)
            j = i + 1
            kata_run = []
            while j < n and re.match(r'[\u30a0-\u30ff]', chars[j].get('char', '')):
                kata_run.append(chars[j])
                j += 1

            if kata_run:
                measurable = [
                    (kc['bbox'][3] - kc['bbox'][1])
                    for kc in kata_run
                    if kc.get('char') not in ('ー', '・')
                ]
                if measurable and all(h < ref_kanji_h * 0.68 for h in measurable):
                    i = j
                    continue
        else:
            out.append(ch)
        i += 1

    return "".join(out)


def _classify_and_group_ocr_lines(lines_240: list) -> list:
    """Groups physical 240x160 OCR lines into semantic 'dialogue', 'menu', and 'label' blocks."""
    valid = [l for l in lines_240 if _has_meaningful_text(l['text'])]
    if not valid:
        return []

    valid.sort(key=lambda b: (b['y1'], b['x1']))

    def is_numbered_opt(t: str) -> bool:
        return bool(re.match(r'^([一二三四五六七八九十0-9０-９]+[\.．・\s]|▶|▷|►)', t))

    def has_prose_markers(t: str) -> bool:
        return bool(re.search(r'[「」『』。、…‥！？!?]', t))

    def is_prose_row(l_item: dict) -> bool:
        t = l_item['text']
        if is_numbered_opt(t):
            return False
        if has_prose_markers(t):
            return True
        has_hira = bool(re.search(r'[\u3040-\u309f]', t))
        return len(t) >= 11 and has_hira

    def is_aligned_menu_neighbor(idx_a: int, idx_b: int) -> bool:
        a, b = valid[idx_a], valid[idx_b]
        v_gap = abs(b['y1'] - a['y2']) if b['y1'] >= a['y1'] else abs(a['y1'] - b['y2'])
        left_aligned = abs(a['x1'] - b['x1']) <= 12
        center_aligned = abs(a['cx'] - b['cx']) <= 12
        return v_gap <= 26 and (left_aligned or center_aligned)

    blocks = []
    consumed = set()
    n = len(valid)
    i = 0

    while i < n:
        if i in consumed:
            i += 1
            continue

        curr = valid[i]
        t = curr['text']

        # Speaker nameplate detected above dialogue line
        if (
            not is_prose_row(curr)
            and not is_numbered_opt(t)
            and 1 <= len(t) <= 10
            and i + 1 < n
            and is_prose_row(valid[i + 1])
        ):
            nxt = valid[i + 1]
            v_gap = nxt['y1'] - curr['y2']
            if -3 <= v_gap <= 15 and abs(nxt['x1'] - curr['x1']) <= 30:
                speaker = t
                consumed.add(i)
                i += 1
                curr = valid[i]
            else:
                speaker = ""
        else:
            speaker = ""

        # Dialogue sentence unwrapping
        if is_prose_row(curr):
            consumed.add(i)
            acc_text = curr['text']
            base_x1 = curr['x1']
            box_x1, box_y1, box_x2, box_y2 = curr['x1'], curr['y1'], curr['x2'], curr['y2']

            k = i
            while k + 1 < n:
                prev_line = valid[k]
                nxt_line = valid[k + 1]
                v_gap = nxt_line['y1'] - prev_line['y2']
                same_col = abs(nxt_line['x1'] - base_x1) <= 24
                open_quote = (acc_text.count('「') > acc_text.count('」')) or (acc_text.count('『') > acc_text.count('』'))
                starts_new_quote = nxt_line['text'].startswith(('「', '『'))
                ended_cleanly = acc_text.endswith(('」', '』', '。', '！', '？', '!', '?')) and not open_quote

                if (
                    -3 <= v_gap <= 13
                    and same_col
                    and not is_numbered_opt(nxt_line['text'])
                    and not starts_new_quote
                    and (not ended_cleanly or is_prose_row(nxt_line))
                ):
                    sep = " " if (re.search(r'[A-Za-z0-9]$', acc_text) and re.search(r'^[A-Za-z0-9]', nxt_line['text'])) else ""
                    acc_text += sep + nxt_line['text']
                    box_x1 = min(box_x1, nxt_line['x1'])
                    box_y1 = min(box_y1, nxt_line['y1'])
                    box_x2 = max(box_x2, nxt_line['x2'])
                    box_y2 = max(box_y2, nxt_line['y2'])
                    consumed.add(k + 1)
                    k += 1
                else:
                    break

            blocks.append({
                "category": "dialogue",
                "speaker_orig": speaker,
                "original": acc_text,
                "x": box_x1, "y": box_y1,
                "w": max(16, box_x2 - box_x1), "h": max(12, box_y2 - box_y1)
            })
            i = k + 1
            continue

        # Menu vs Graphic Label classification
        consumed.add(i)
        menu_keywords = r'(始める|消す|見る|モード|ロード|セーブ|設定|終了|待機|攻撃|移動|策|アイテム|ステータス|やめる|はい|いいえ)'
        has_menu_peer = any(
            j != i and not is_prose_row(valid[j]) and is_aligned_menu_neighbor(i, j)
            for j in range(n)
        )

        if is_numbered_opt(t) or re.search(menu_keywords, t) or has_menu_peer:
            cat = "menu"
        else:
            cat = "label"

        blocks.append({
            "category": cat,
            "speaker_orig": "",
            "original": t,
            "x": curr['x1'], "y": curr['y1'],
            "w": max(16, curr['x2'] - curr['x1']), "h": max(12, curr['y2'] - curr['y1'])
        })
        i += 1

    return blocks


class LocalQwenTranslator:
    @classmethod
    def translate_block(cls, model_path: str, text: str, category: str, speaker: str,
                        src_lang: str, tgt_lang: str, criteria: str, api_key: str) -> tuple:
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

        user_prompt = f"Speaker: {speaker}\nText: {text}" if speaker else text
        sys_prompt = (
            f"You are an expert retro video game localizer translating {src_name} GBA text to {tgt_name}.\n"
            f"TASK: {role_hint}\n"
            f"STYLE CRITERIA:\n{criteria}\n"
            + (
                "Output strictly in format: TranslatedSpeaker | TranslatedText"
                if speaker else
                "Output ONLY the translated text. No notes, no explanations, no romaji."
            )
        )

        raw_out = ""
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


# =============================================================================
# 6. TRANSLATION WORKER THREAD
# =============================================================================
class TranslationWorker(QThread):
    finished = pyqtSignal(list, str)
    error = pyqtSignal(str)

    def __init__(self, frame_data: np.ndarray, rom_path: str, config):
        super().__init__()
        self.frame_data = frame_data
        self.rom_path = rom_path
        self.config = config

    def preprocess_image(self, img_array: np.ndarray, scale: int = 4) -> np.ndarray:
        height, width, _ = img_array.shape
        return cv2.resize(img_array, (width * scale, height * scale), interpolation=cv2.INTER_LANCZOS4)

    def run(self):
        if self.frame_data is None:
            self.error.emit("No active video frame to capture.")
            return

        src_lang = self.config.get("ai_translation", "source_lang") or "ja"
        tgt_lang = self.config.get("ai_translation", "target_lang") or "vi"

        app_dir = os.path.dirname(os.path.abspath(__file__))
        models_dir = os.path.join(app_dir, "models")
        os.makedirs(models_dir, exist_ok=True)

        game_name = os.path.splitext(os.path.basename(self.rom_path))[0]
        trans_dir = self.config.get("paths", "translations") or "translations"
        os.makedirs(trans_dir, exist_ok=True)
        csv_path = os.path.join(trans_dir, f"{game_name}_translations.csv")

        cache = self._load_csv_cache(csv_path)
        groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()

        criteria = get_or_create_game_criteria(self.rom_path, src_lang, tgt_lang, groq_api_key, trans_dir)

        # 1. Primary: Groq Multimodal Vision
        if groq_api_key:
            vision_boxes, vision_model = run_groq_vision(self.frame_data, src_lang, tgt_lang, groq_api_key, criteria)
            if vision_boxes:
                for b in vision_boxes:
                    cache_key = (_normalize_text(b.original), b.speaker_orig, b.category, src_lang, tgt_lang)
                    cache[cache_key] = (b.speaker_trans, b.translated)
                self._save_csv_cache(csv_path, cache)
                status_msg = f"Translated {len(vision_boxes)} block(s) via Groq Vision [{vision_model}]"
                self.finished.emit(vision_boxes, status_msg)
                return
            else:
                print("[AI Translator] Groq Vision rate limited or unavailable. Falling back to local OCR...")

        # 2. Offline Fallback (MeikiOCR > PaddleOCR > EasyOCR)
        SCALE = 4
        img_np = self.preprocess_image(self.frame_data, scale=SCALE)

        results = []
        ocr_engine_name = "None"

        # Try MeikiOCR
        if src_lang == "ja":
            meiki_reader = get_meiki_reader()
            if meiki_reader:
                try:
                    img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
                    meiki_lines = meiki_reader.run_ocr(img_bgr)
                    for line in meiki_lines:
                        chars = line.get('chars', [])
                        if not chars:
                            continue
                        clean_text = _clean_meiki_chars_furigana(chars).strip()
                        if not clean_text:
                            continue

                        xs = [c['bbox'][0] for c in chars] + [c['bbox'][2] for c in chars]
                        ys = [c['bbox'][1] for c in chars] + [c['bbox'][3] for c in chars]
                        bbox = [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
                        results.append((bbox, clean_text, 1.0))
                    if results:
                        ocr_engine_name = "MeikiOCR"
                except Exception as e:
                    print(f"[OCR Warning] MeikiOCR failed ({e}). Trying PaddleOCR...")

        # Try PaddleOCR
        if not results:
            paddle_reader = get_paddle_reader(src_lang)
            if paddle_reader:
                try:
                    paddle_res = paddle_reader.predict(img_np)
                    if paddle_res:
                        for res in paddle_res:
                            if isinstance(res, dict):
                                for poly, text, conf in zip(res.get('dt_polys', []), res.get('rec_text', []), res.get('rec_score', [])):
                                    results.append((poly, text, conf))
                            elif hasattr(res, 'keys'):
                                for poly, text, conf in zip(res['dt_polys'], res['rec_text'], res['rec_score']):
                                    results.append((poly, text, conf))
                    if results:
                        ocr_engine_name = "PaddleOCR"
                except Exception as e:
                    print(f"[OCR Warning] PaddleOCR failed ({e}). Trying EasyOCR...")

        # Try EasyOCR
        if not results:
            easy_reader = get_easy_reader(src_lang, models_dir)
            if easy_reader:
                try:
                    results = easy_reader.readtext(img_np, text_threshold=0.5, low_text=0.3)
                    if results:
                        ocr_engine_name = "EasyOCR"
                except Exception as e:
                    self.error.emit(f"All OCR backends failed: {e}")
                    return

        if not results:
            self.error.emit("No text detected on screen.")
            return

        merged_rows = self._merge_horizontal_row_fragments(results)
        lines_240 = []
        for (bbox, raw_text, _) in merged_rows:
            xs = [pt[0] / float(SCALE) for pt in bbox]
            ys = [pt[1] / float(SCALE) for pt in bbox]
            x1, y1 = max(0.0, min(xs)), max(0.0, min(ys))
            x2, y2 = min(239.0, max(xs)), min(159.0, max(ys))
            lines_240.append({
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "w": x2 - x1, "h": y2 - y1,
                "cx": (x1 + x2) / 2.0, "cy": (y1 + y2) / 2.0,
                "text": raw_text.strip()
            })

        semantic_blocks = _classify_and_group_ocr_lines(lines_240)

        llm_model_path = find_best_llm_model(models_dir)
        using_local_llm = os.path.exists(llm_model_path) and (get_llama_instance(llm_model_path) is not None)

        translated_boxes = []
        new_translations_found = False

        for blk in semantic_blocks:
            orig = blk["original"]
            speaker_orig = blk["speaker_orig"]
            cat = blk["category"]
            norm_key = (_normalize_text(orig), speaker_orig, cat, src_lang, tgt_lang)

            if norm_key in cache:
                speaker_trans, translated_text = cache[norm_key]
            else:
                speaker_trans, translated_text = LocalQwenTranslator.translate_block(
                    llm_model_path, orig, cat, speaker_orig, src_lang, tgt_lang, criteria, groq_api_key
                )
                cache[norm_key] = (speaker_trans, translated_text)
                new_translations_found = True

            translated_boxes.append(TranslatedBox(
                blk["x"], blk["y"], blk["w"], blk["h"],
                orig, translated_text,
                category=cat,
                speaker_orig=speaker_orig,
                speaker_trans=speaker_trans
            ))

        if new_translations_found:
            self._save_csv_cache(csv_path, cache)

        backend_tag = os.path.basename(llm_model_path).replace(".gguf", "") if using_local_llm else "Cloud Text API"
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
                'x1': min(xs), 'y1': min(ys),
                'x2': max(xs), 'y2': max(ys),
                'text': text.strip()
            })

        boxes.sort(key=lambda b: b['y1'])
        lines = []

        for box in boxes:
            box_cy = (box['y1'] + box['y2']) / 2.0
            box_h = box['y2'] - box['y1']
            merged = False

            for line in lines:
                line_cy = (line['y1'] + line['y2']) / 2.0
                line_h = line['y2'] - line['y1']
                overlap_h = max(0, min(line['y2'], box['y2']) - max(line['y1'], box['y1']))

                if abs(line_cy - box_cy) < (max(line_h, box_h) * 0.45) or overlap_h > (box_h * 0.35):
                    line['boxes'].append(box)
                    line['x1'] = min(line['x1'], box['x1'])
                    line['y1'] = min(line['y1'], box['y1'])
                    line['x2'] = max(line['x2'], box['x2'])
                    line['y2'] = max(line['y2'], box['y2'])
                    merged = True
                    break

            if not merged:
                lines.append({
                    'y1': box['y1'], 'y2': box['y2'],
                    'x1': box['x1'], 'x2': box['x2'],
                    'boxes': [box]
                })

        final_boxes = []
        for line in lines:
            line['boxes'].sort(key=lambda b: b['x1'])
            combined = "".join(b['text'] for b in line['boxes']).strip()
            if combined:
                final_boxes.append((
                    [[line['x1'], line['y1']], [line['x2'], line['y1']],
                     [line['x2'], line['y2']], [line['x1'], line['y2']]],
                    combined,
                    1.0
                ))
        return final_boxes

    def _load_csv_cache(self, filepath: str) -> dict:
        cache = {}
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    next(reader, None)
                    for row in reader:
                        if len(row) >= 6:
                            src, tgt, cat, sp_orig, sp_tr_or_orig, rest = row[0], row[1], row[2], row[3], row[4], row[5]
                            if len(row) >= 7:
                                cache[(_normalize_text(row[5]), sp_orig, cat, src, tgt)] = (row[4], row[6])
                            else:
                                cache[(_normalize_text(sp_tr_or_orig), sp_orig, cat, src, tgt)] = ("", rest)
                        elif len(row) >= 4:
                            src, tgt, orig, trans = row[0], row[1], row[2], row[3]
                            cache[(_normalize_text(orig), "", "dialogue", src, tgt)] = ("", trans)
            except Exception:
                pass
        return cache

    def _save_csv_cache(self, filepath: str, cache: dict):
        try:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "SourceLang", "TargetLang", "Category",
                    "SpeakerOrig", "SpeakerTrans", "OriginalText", "TranslatedText"
                ])
                for (orig, sp_orig, cat, src, tgt), (sp_tr, trans) in cache.items():
                    writer.writerow([src, tgt, cat, sp_orig, sp_tr, orig, trans])
        except Exception:
            pass
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

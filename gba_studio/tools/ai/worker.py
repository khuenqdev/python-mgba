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
    get_meiki_reader,
    get_paddle_reader,
    get_easy_reader,
    _has_meaningful_text,
    find_best_llm_model,
    get_llama_instance,
)
from .vision import TranslatedBox, get_or_create_game_criteria, run_groq_vision
from .ocr import _clean_meiki_chars_furigana, _classify_and_group_ocr_lines
from .local import LocalQwenTranslator
from .glossary import GlossaryAndCacheStore


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
        # Require at least 2 Japanese kana/kanji characters OR >= 4 alphabetic characters
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

        app_dir = str(PROJECT_ROOT)
        models_dir = os.path.join(app_dir, "models")
        os.makedirs(models_dir, exist_ok=True)

        trans_dir = self.config.get("paths", "translations") or "translations"
        os.makedirs(trans_dir, exist_ok=True)

        store = GlossaryAndCacheStore(self.rom_path, trans_dir=trans_dir, game_code=self.game_code)
        frame_hash = store.hash_frame(self.frame_data)

        # 0. Instant Frame + Cache Hit (Zero API / LLM latency)
        cached_frame_items = store.resolve_cached_frame(frame_hash, src_lang, tgt_lang)
        if cached_frame_items:
            boxes = [
                TranslatedBox(
                    it["x"], it["y"], it["w"], it["h"],
                    it["original"], it["translated"],
                    category=it["category"],
                    speaker_orig=it["speaker_orig"],
                    speaker_trans=it["speaker_trans"],
                )
                for it in cached_frame_items
            ]
            self.result_ocr_sig = _normalize_ocr_signature("".join(b.original for b in boxes))
            self.finished.emit(boxes, f"Translated {len(boxes)} block(s) via Instant Cache [{store.game_id}]")
            return

        SCALE = 4
        precomputed_ocr = None
        precomputed_engine = "None"
        local_ocr_sig = ""

        # 1. Local OCR Text-Change Gate (prevents API spam in action games & animated menus)
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

                # Skip if on-screen text is >= 82% identical to the previous screen (animated cursor/portrait)
                if self.last_auto_ocr_sig:
                    sim = SequenceMatcher(None, local_ocr_sig, self.last_auto_ocr_sig).ratio()
                    if sim >= 0.82:
                        self.skipped.emit("Auto-skip: Text unchanged")
                        return

                # Reuse in-memory session translation if exact normalized text was already seen
                if local_ocr_sig in self.auto_ocr_cache:
                    cached_boxes = self.auto_ocr_cache[local_ocr_sig]
                    self.result_ocr_sig = local_ocr_sig
                    self.finished.emit(cached_boxes, f"Translated {len(cached_boxes)} block(s) via Text Cache [{store.game_id}]")
                    return

        game_name = os.path.splitext(os.path.basename(self.rom_path))[0]
        csv_path = os.path.join(trans_dir, f"{game_name}_translations.csv")

        groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
        criteria = get_or_create_game_criteria(self.rom_path, src_lang, tgt_lang, groq_api_key, trans_dir)
        glossary_prompt = store.format_glossary_prompt()

        # 2. Primary: Groq Multimodal Vision
        if groq_api_key:
            vision_boxes, vision_model = run_groq_vision(
                self.frame_data, src_lang, tgt_lang, groq_api_key, criteria, glossary_text=glossary_prompt
            )
            if vision_boxes:
                self._apply_store_overrides(store, vision_boxes, src_lang, tgt_lang)
                store.save_frame_layout(frame_hash, vision_boxes)
                store.save_cache()
                self._export_csv_mirror(csv_path, store)
                self.result_ocr_sig = local_ocr_sig or _normalize_ocr_signature(
                    "".join((b.speaker_orig + b.original) for b in vision_boxes)
                )
                status_msg = f"Translated {len(vision_boxes)} block(s) via Groq Vision [{vision_model}]"
                self.finished.emit(vision_boxes, status_msg)
                return
            else:
                print("[AI Translator] Groq Vision rate limited or unavailable. Falling back to local OCR...")

        # 3. Offline Fallback (reuse precomputed OCR if auto-triggered already scanned it)
        if precomputed_ocr is not None and precomputed_engine != "None":
            results, ocr_engine_name = precomputed_ocr, precomputed_engine
        else:
            img_np = self.preprocess_image(self.frame_data, scale=SCALE)
            results, ocr_engine_name = self._run_local_ocr_raw(img_np, src_lang, models_dir)

        if not results:
            if self.auto_triggered:
                self.skipped.emit("No text detected on screen.")
            else:
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
                    llm_model_path, orig, cat, speaker_orig, src_lang, tgt_lang,
                    criteria, groq_api_key, glossary_text=glossary_prompt
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
                speaker_trans=speaker_trans
            ))

        store.save_frame_layout(frame_hash, translated_boxes)
        store.save_cache()
        self._export_csv_mirror(csv_path, store)

        self.result_ocr_sig = local_ocr_sig or _normalize_ocr_signature(
            "".join((b.speaker_orig + b.original) for b in translated_boxes)
        )

        if cache_hits == len(translated_boxes) and translated_boxes:
            backend_tag = f"Cache/Glossary [{store.game_id}]"
        else:
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
                    line["x1"] = min(line["x1"], box["x1"])
                    line["y1"] = min(line["y1"], box["y1"])
                    line["x2"] = max(line["x2"], box["x2"])
                    line["y2"] = max(line["y2"], box["y2"])
                    merged = True
                    break

            if not merged:
                lines.append({
                    "y1": box["y1"], "y2": box["y2"],
                    "x1": box["x1"], "x2": box["x2"],
                    "boxes": [box]
                })

        final_boxes = []
        for line in lines:
            line["boxes"].sort(key=lambda b: b["x1"])
            combined = "".join(b["text"] for b in line["boxes"]).strip()
            if combined:
                final_boxes.append((
                    [[line["x1"], line["y1"]], [line["x2"], line["y1"]],
                     [line["x2"], line["y2"]], [line["x1"], line["y2"]]],
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
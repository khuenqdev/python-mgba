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
from .engines import (get_meiki_reader, get_paddle_reader, get_easy_reader, _normalize_text, find_best_llm_model, get_llama_instance)
from .vision import TranslatedBox, get_or_create_game_criteria, run_groq_vision
from .ocr import _clean_meiki_chars_furigana, _classify_and_group_ocr_lines
from .local import LocalQwenTranslator, _google_translate_fallback

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

        app_dir = str(PROJECT_ROOT)
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

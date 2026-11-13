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
from .engines import _has_meaningful_text

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



# glossary.py
import os
import re
import csv
import json
import hashlib
import datetime

from .engines import _normalize_text


def resolve_game_id(rom_path: str, game_code: str = "") -> str:
    """Resolves a clean per-game ID (4-char ROM game code if valid, else ROM filename stem)."""
    code = (game_code or "").strip().upper()
    if (not code or code == "UNKNOWN") and rom_path and os.path.exists(rom_path):
        try:
            with open(rom_path, "rb") as f:
                f.seek(0xAC)
                raw = f.read(4).decode("latin-1", errors="ignore").strip().upper()
                if re.fullmatch(r"[A-Z0-9]{4}", raw):
                    code = raw
        except Exception:
            pass

    if code and code != "UNKNOWN" and re.fullmatch(r"[A-Z0-9_-]{3,16}", code):
        return code

    stem = os.path.splitext(os.path.basename(rom_path or "unknown"))[0]
    safe = re.sub(r"[^\w\-]", "_", stem).strip("_")
    return safe or "UNKNOWN"


def make_cache_key(src_lang: str, tgt_lang: str, category: str, speaker_orig: str, orig: str) -> str:
    norm_orig = _normalize_text(orig)
    norm_sp = _normalize_text(speaker_orig)
    cat = (category or "dialogue").strip().lower()
    return f"{src_lang}|{tgt_lang}|{cat}|{norm_sp}|{norm_orig}"


class GlossaryAndCacheStore:
    """Persistent per-game glossary (<game_id>_glossary.json) and translation cache (<game_id>_cache.json)."""

    def __init__(self, rom_path: str, trans_dir: str = "translations", game_code: str = ""):
        self.rom_path = rom_path or ""
        self.trans_dir = trans_dir or "translations"
        os.makedirs(self.trans_dir, exist_ok=True)

        self.game_id = resolve_game_id(self.rom_path, game_code)
        self.glossary_path = os.path.join(self.trans_dir, f"{self.game_id}_glossary.json")
        self.cache_path = os.path.join(self.trans_dir, f"{self.game_id}_cache.json")

        rom_stem = os.path.splitext(os.path.basename(self.rom_path))[0] if self.rom_path else self.game_id
        self.legacy_csv_path = os.path.join(self.trans_dir, f"{rom_stem}_translations.csv")

        self.terms = {}     # src_term -> tgt_term
        self.entries = {}   # cache_key -> entry dict
        self.frames = {}    # frame_sha1 -> list of box stubs
        self.load()

    def load(self):
        # 1. Load Glossary
        self.terms = {}
        if os.path.exists(self.glossary_path):
            try:
                with open(self.glossary_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw_terms = data.get("terms", data) if isinstance(data, dict) else {}
                if isinstance(raw_terms, dict):
                    for k, v in raw_terms.items():
                        ks, vs = str(k).strip(), str(v).strip()
                        if ks and vs:
                            self.terms[ks] = vs
            except Exception as e:
                print(f"[Glossary] Failed to load {self.glossary_path}: {e}")

        # 2. Load JSON Cache
        self.entries = {}
        self.frames = {}
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    entries = data.get("entries", {})
                    frames = data.get("frames", {})
                    if isinstance(entries, dict):
                        self.entries = entries
                    if isinstance(frames, dict):
                        self.frames = frames
            except Exception as e:
                print(f"[Cache] Failed to load {self.cache_path}: {e}")
        elif os.path.exists(self.legacy_csv_path):
            self._migrate_legacy_csv()

    def _migrate_legacy_csv(self):
        try:
            with open(self.legacy_csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if len(row) >= 7:
                        src, tgt, cat, sp_orig, sp_tr, orig, trans = row[:7]
                    elif len(row) >= 6:
                        src, tgt, cat, sp_orig, orig, trans = row[:6]
                        sp_tr = ""
                    elif len(row) >= 4:
                        src, tgt, orig, trans = row[:4]
                        cat, sp_orig, sp_tr = "dialogue", "", ""
                    else:
                        continue
                    self.upsert_entry(src, tgt, cat, sp_orig, sp_tr, orig, trans, locked=False, save_now=False)
            if self.entries:
                self.save_cache()
        except Exception:
            pass

    def save_glossary(self):
        payload = {
            "game_id": self.game_id,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "terms": dict(sorted(self.terms.items(), key=lambda kv: kv[0])),
        }
        tmp = self.glossary_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.glossary_path)

    def save_cache(self):
        # Bound stored frame snapshots to latest 400 screens to keep file compact
        if len(self.frames) > 400:
            keys = list(self.frames.keys())[-400:]
            self.frames = {k: self.frames[k] for k in keys}

        payload = {
            "game_id": self.game_id,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "entries": self.entries,
            "frames": self.frames,
        }
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.cache_path)

    # =========================================================================
    # GLOSSARY HELPERS
    # =========================================================================
    def set_term(self, source_term: str, target_term: str, save_now: bool = True):
        src = source_term.strip()
        tgt = target_term.strip()
        if src and tgt:
            self.terms[src] = tgt
            if save_now:
                self.save_glossary()

    def delete_term(self, source_term: str, save_now: bool = True):
        if source_term in self.terms:
            del self.terms[source_term]
            if save_now:
                self.save_glossary()

    def lookup_term(self, text: str) -> str | None:
        if not text or not self.terms:
            return None
        clean = text.strip()
        if clean in self.terms:
            return self.terms[clean]
        norm = _normalize_text(clean)
        for k, v in self.terms.items():
            if _normalize_text(k) == norm:
                return v
        return None

    def format_glossary_prompt(self, max_terms: int = 60) -> str:
        if not self.terms:
            return ""
        lines = ["GLOSSARY (Strictly use these exact translations whenever these names or terms appear):"]
        for k, v in list(self.terms.items())[:max_terms]:
            lines.append(f'- "{k}" => "{v}"')
        return "\n".join(lines)

    # =========================================================================
    # CACHE HELPERS
    # =========================================================================
    def lookup_entry(self, src_lang: str, tgt_lang: str, category: str, speaker_orig: str, orig: str) -> dict | None:
        key = make_cache_key(src_lang, tgt_lang, category, speaker_orig, orig)
        if key in self.entries:
            return self.entries[key]

        # Cross-category fallback for identical short UI text with no speaker
        if not _normalize_text(speaker_orig):
            norm_orig = _normalize_text(orig)
            for alt_cat in ("menu", "label", "dialogue"):
                alt_key = f"{src_lang}|{tgt_lang}|{alt_cat}||{norm_orig}"
                if alt_key in self.entries:
                    return self.entries[alt_key]
        return None

    def upsert_entry(
        self,
        src_lang: str,
        tgt_lang: str,
        category: str,
        speaker_orig: str,
        speaker_trans: str,
        orig: str,
        translated: str,
        locked: bool = False,
        save_now: bool = False,
    ) -> str:
        key = make_cache_key(src_lang, tgt_lang, category, speaker_orig, orig)
        existing = self.entries.get(key)
        if existing and existing.get("locked") and not locked:
            return key

        self.entries[key] = {
            "source_lang": src_lang,
            "target_lang": tgt_lang,
            "category": (category or "dialogue").strip().lower(),
            "speaker_orig": speaker_orig.strip(),
            "speaker_trans": speaker_trans.strip(),
            "original": _normalize_text(orig),
            "translated": translated.strip(),
            "locked": bool(locked),
        }
        if save_now:
            self.save_cache()
        return key

    def delete_entry(self, key: str, save_now: bool = True):
        if key in self.entries:
            del self.entries[key]
            if save_now:
                self.save_cache()

    def clear_unlocked_cache(self):
        self.entries = {k: v for k, v in self.entries.items() if v.get("locked")}
        self.frames.clear()
        self.save_cache()

    @staticmethod
    def hash_frame(frame_data) -> str:
        return hashlib.sha1(frame_data.tobytes()).hexdigest()

    def save_frame_layout(self, frame_hash: str, boxes: list):
        self.frames[frame_hash] = [
            {
                "x": b.x,
                "y": b.y,
                "w": b.w,
                "h": b.h,
                "category": b.category,
                "speaker_orig": b.speaker_orig,
                "original": _normalize_text(b.original),
            }
            for b in boxes
        ]

    def resolve_cached_frame(self, frame_hash: str, src_lang: str, tgt_lang: str) -> list | None:
        stubs = self.frames.get(frame_hash)
        if not stubs:
            return None

        resolved = []
        for st in stubs:
            orig = st.get("original", "")
            cat = st.get("category", "dialogue")
            sp_orig = st.get("speaker_orig", "")
            entry = self.lookup_entry(src_lang, tgt_lang, cat, sp_orig, orig)
            if not entry or not entry.get("translated"):
                return None

            sp_tr = self.lookup_term(sp_orig) or entry.get("speaker_trans", "")
            resolved.append({
                "x": st.get("x", 10),
                "y": st.get("y", 10),
                "w": st.get("w", 220),
                "h": st.get("h", 18),
                "category": cat,
                "speaker_orig": sp_orig,
                "speaker_trans": sp_tr,
                "original": orig,
                "translated": entry["translated"],
            })
        return resolved if resolved else None
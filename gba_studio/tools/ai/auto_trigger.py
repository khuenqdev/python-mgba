# auto_trigger.py
import time
import cv2
import numpy as np

from .glossary import GlossaryAndCacheStore


class AutoTranslateDetector:
    """
    Smart scene/dialogue change detector for Auto-Translate.
    Filters out animated cursors, action-game movement, and fade transitions,
    waits for dialogue text to settle (debounce), backs off when screens contain
    no dialogue text, and allows instant zero-API hits from the frame cache.
    """

    ROI_MODES = [
        ("auto", "Auto (Pixel Ruler if drawn, else Full Screen)"),
        ("bottom", "Bottom Dialogue Region (y: 96..160)"),
        ("full", "Full Screen (240×160)"),
        ("ruler", "Pixel Ruler Box Only"),
    ]

    def __init__(self):
        self.reset()

    def reset(self):
        self._prev_gray = None
        self._prev_roi_key = None
        self._last_translated_gray = None
        self._last_translated_dhash = None
        self._last_change_ts = 0.0
        self._last_api_trigger_ts = 0.0
        self._pending_settle = False
        self._consecutive_skips = 0

    @staticmethod
    def _resolve_roi(frame_data: np.ndarray, roi_mode: str, ruler_rect: tuple | None) -> tuple[int, int, int, int]:
        h, w = frame_data.shape[:2]
        if roi_mode == "bottom":
            return 0, max(0, int(h * 0.60)), w, h
        if roi_mode in ("auto", "ruler") and ruler_rect is not None:
            x1, y1, x2, y2 = ruler_rect
            rx1 = max(0, min(w - 2, min(x1, x2)))
            ry1 = max(0, min(h - 2, min(y1, y2)))
            rx2 = max(rx1 + 2, min(w, max(x1, x2) + 1))
            ry2 = max(ry1 + 2, min(h, max(y1, y2) + 1))
            if (rx2 - rx1) >= 16 and (ry2 - ry1) >= 8:
                return rx1, ry1, rx2, ry2
        return 0, 0, w, h

    @staticmethod
    def _extract_gray_roi(frame_data: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
        x1, y1, x2, y2 = rect
        crop = frame_data[y1:y2, x1:x2]
        return cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)

    @staticmethod
    def _compute_dhash(gray: np.ndarray) -> int:
        small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        diff = small[:, 1:] > small[:, :-1]
        bits = 0
        for val in diff.flatten():
            bits = (bits << 1) | int(bool(val))
        return bits

    @staticmethod
    def _hamming_distance(a: int | None, b: int | None) -> int:
        if a is None or b is None:
            return 64
        return (a ^ b).bit_count()

    @staticmethod
    def _changed_pixel_ratio(img_a: np.ndarray, img_b: np.ndarray, threshold: int = 20) -> float:
        if img_a is None or img_b is None or img_a.shape != img_b.shape:
            return 1.0
        diff = cv2.absdiff(img_a, img_b)
        return float(np.mean(diff > threshold))

    @staticmethod
    def _has_text_like_contrast(gray: np.ndarray) -> bool:
        """Filters solid black/white fade screens and flat blank backgrounds."""
        if float(np.std(gray)) < 10.0:
            return False
        edges = cv2.Canny(gray, 55, 160)
        edge_density = float(np.mean(edges > 0))
        return edge_density >= 0.010

    def notify_skipped(self, frame_data: np.ndarray | None, roi_mode: str = "auto", ruler_rect: tuple | None = None):
        """Called when auto-trigger found no new dialogue text; updates baseline and applies backoff."""
        self._consecutive_skips = min(4, self._consecutive_skips + 1)
        self._last_api_trigger_ts = time.monotonic()
        if frame_data is not None:
            rect = self._resolve_roi(frame_data, roi_mode, ruler_rect)
            gray = self._extract_gray_roi(frame_data, rect)
            self._prev_gray = gray
            self._last_translated_gray = gray
            self._last_translated_dhash = self._compute_dhash(gray)
            self._pending_settle = False

    def mark_translated(self, frame_data: np.ndarray, roi_mode: str = "auto",
                        ruler_rect: tuple | None = None, is_api_call: bool = True):
        if frame_data is None:
            return
        rect = self._resolve_roi(frame_data, roi_mode, ruler_rect)
        gray = self._extract_gray_roi(frame_data, rect)
        self._prev_gray = gray
        self._prev_roi_key = rect
        self._last_translated_gray = gray
        self._last_translated_dhash = self._compute_dhash(gray)
        self._pending_settle = False
        self._consecutive_skips = 0
        if is_api_call:
            self._last_api_trigger_ts = time.monotonic()

    def evaluate(
        self,
        frame_data: np.ndarray,
        roi_mode: str = "auto",
        debounce_ms: int = 900,
        cooldown_ms: int = 2800,
        ruler_rect: tuple | None = None,
        cache_store: GlossaryAndCacheStore | None = None,
        src_lang: str = "ja",
        tgt_lang: str = "vi",
    ) -> tuple[bool, bool]:
        """
        Evaluates the current frame.
        Returns (should_trigger, is_cached_hit).
        """
        if frame_data is None:
            return False, False

        now = time.monotonic()
        rect = self._resolve_roi(frame_data, roi_mode, ruler_rect)
        gray = self._extract_gray_roi(frame_data, rect)

        # Reset tracking if ROI geometry changed (e.g., user drew a new Ruler box)
        if rect != self._prev_roi_key:
            self._prev_roi_key = rect
            self._prev_gray = gray
            self._last_translated_gray = None
            self._last_translated_dhash = None
            self._last_change_ts = now
            self._pending_settle = True
            self._consecutive_skips = 0
            return False, False

        # Ignore solid fade-in/fade-out screens
        if not self._has_text_like_contrast(gray):
            self._prev_gray = gray
            self._pending_settle = False
            return False, False

        # 1. Motion check (>1.0% pixel delta keeps resetting debounce during walking or cursor animation)
        motion_ratio = self._changed_pixel_ratio(gray, self._prev_gray, threshold=20)
        if motion_ratio > 0.010:
            self._prev_gray = gray
            self._last_change_ts = now
            self._pending_settle = True
            return False, False

        # 2. Screen is currently stable; wait for full settle debounce
        if not self._pending_settle:
            return False, False

        eff_debounce_sec = max(0.50, int(debounce_ms) / 1000.0)
        if (now - self._last_change_ts) < eff_debounce_sec:
            return False, False

        # 3. Require meaningful difference vs last translated/checked screen
        #    (>4.5% pixel change AND dHash distance > 4 filters out localized animated menu cursors)
        dhash = self._compute_dhash(gray)
        diff_vs_last = self._changed_pixel_ratio(gray, self._last_translated_gray, threshold=20)
        hamming = self._hamming_distance(dhash, self._last_translated_dhash)
        if self._last_translated_gray is not None and (diff_vs_last <= 0.045 or hamming <= 4):
            self._pending_settle = False
            return False, False

        # Major scene transition resets empty-screen backoff
        if diff_vs_last > 0.25:
            self._consecutive_skips = 0

        # 4. Instant Frame Cache bypasses API cooldown throttling
        is_cache_hit = False
        if cache_store is not None:
            f_hash = cache_store.hash_frame(frame_data)
            if cache_store.resolve_cached_frame(f_hash, src_lang, tgt_lang):
                is_cache_hit = True

        if not is_cache_hit:
            base_cd_sec = max(1.5, int(cooldown_ms) / 1000.0)
            eff_cd_sec = min(18.0, base_cd_sec * (1.0 + (0.75 * self._consecutive_skips)))
            if (now - self._last_api_trigger_ts) < eff_cd_sec:
                return False, False

        # Commit baseline and fire trigger
        self._last_translated_gray = gray
        self._last_translated_dhash = dhash
        self._pending_settle = False
        if not is_cache_hit:
            self._last_api_trigger_ts = now

        return True, is_cache_hit
# auto_trigger.py
import time
from collections import deque
import cv2
import numpy as np

from .glossary import GlossaryAndCacheStore


class AutoTranslateDetector:
    """
    Smart scene/dialogue change detector for Auto-Translate.
    Uses a 16x16 tile motion history to isolate and ignore continuously
    oscillating regions (animated menu cursors, animated character portraits,
    background holograms, blinking prompts) while reacting reliably when
    actual text blocks change and settle.
    """

    ROI_MODES = [
        ("auto", "Auto (Pixel Ruler if drawn, else Full Screen)"),
        ("bottom", "Bottom Dialogue Region (y: 96..160)"),
        ("full", "Full Screen (240×160)"),
        ("ruler", "Pixel Ruler Box Only"),
    ]

    BLOCK_SIZE = 16

    def __init__(self):
        self.reset()

    def reset(self):
        self._prev_gray = None
        self._prev_roi_key = None
        self._last_translated_gray = None
        self._last_change_ts = 0.0
        self._last_api_trigger_ts = 0.0
        self._pending_settle = False
        self._consecutive_skips = 0
        self._block_history = deque(maxlen=10)

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

    @classmethod
    def _block_diff_map(cls, img_a: np.ndarray, img_b: np.ndarray, threshold: int = 20) -> np.ndarray | None:
        if img_a is None or img_b is None or img_a.shape != img_b.shape:
            return None
        diff_bin = (cv2.absdiff(img_a, img_b) > threshold).astype(np.float32)
        h, w = diff_bin.shape
        bs = cls.BLOCK_SIZE
        rows = max(1, (h + bs - 1) // bs)
        cols = max(1, (w + bs - 1) // bs)
        pad_h = (rows * bs) - h
        pad_w = (cols * bs) - w
        if pad_h > 0 or pad_w > 0:
            diff_bin = np.pad(diff_bin, ((0, pad_h), (0, pad_w)), mode="constant", constant_values=0.0)
        return diff_bin.reshape(rows, bs, cols, bs).mean(axis=(1, 3))

    @staticmethod
    def _has_text_like_contrast(gray: np.ndarray) -> bool:
        """Filters solid black/white fade screens and flat blank backgrounds."""
        if float(np.std(gray)) < 10.0:
            return False
        edges = cv2.Canny(gray, 55, 160)
        edge_density = float(np.mean(edges > 0))
        return edge_density >= 0.010

    def _current_osc_mask(self, shape_rc: tuple[int, int]) -> np.ndarray:
        if len(self._block_history) < 3:
            return np.zeros(shape_rc, dtype=bool)
        if self._block_history[0].shape != shape_rc:
            return np.zeros(shape_rc, dtype=bool)
        hits = np.sum(self._block_history, axis=0)
        return hits >= 3

    def notify_skipped(self, frame_data: np.ndarray | None, roi_mode: str = "auto", ruler_rect: tuple | None = None):
        """Called when auto-trigger found no new dialogue text; updates baseline and applies backoff."""
        self._consecutive_skips = min(4, self._consecutive_skips + 1)
        self._last_api_trigger_ts = time.monotonic()
        if frame_data is not None:
            rect = self._resolve_roi(frame_data, roi_mode, ruler_rect)
            gray = self._extract_gray_roi(frame_data, rect)
            self._prev_gray = gray
            self._last_translated_gray = gray
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
        self._pending_settle = False
        self._consecutive_skips = 0
        if is_api_call:
            self._last_api_trigger_ts = time.monotonic()

    def evaluate(
        self,
        frame_data: np.ndarray,
        roi_mode: str = "auto",
        debounce_ms: int = 1200,
        cooldown_ms: int = 4000,
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
            self._last_change_ts = now
            self._pending_settle = True
            self._consecutive_skips = 0
            self._block_history.clear()
            return False, False

        # Ignore solid fade-in/fade-out screens
        if not self._has_text_like_contrast(gray):
            self._prev_gray = gray
            self._pending_settle = False
            self._block_history.clear()
            return False, False

        # 1. Per-block motion & oscillating-animation detection
        block_motion = self._block_diff_map(gray, self._prev_gray, threshold=20)
        if block_motion is None:
            self._prev_gray = gray
            self._last_change_ts = now
            self._pending_settle = True
            return False, False

        raw_active = block_motion > 0.03
        active_frac = float(np.mean(raw_active))

        if active_frac > 0.55:
            # Major scene/camera transition: reset oscillation history
            self._block_history.clear()
        else:
            self._block_history.append(raw_active)

        osc_mask = self._current_osc_mask(raw_active.shape)
        non_osc_active = raw_active & (~osc_mask)
        non_osc_motion_blocks = int(np.sum(non_osc_active))

        self._prev_gray = gray

        # Reset settle timer only if non-oscillating blocks changed (e.g. text printing or walking)
        if non_osc_motion_blocks >= 2 or float(np.mean(block_motion * (~osc_mask))) > 0.015:
            self._last_change_ts = now
            self._pending_settle = True
            return False, False

        # 2. Wait for non-oscillating regions to stay settled for debounce_ms
        if not self._pending_settle:
            return False, False

        eff_debounce_sec = max(0.50, int(debounce_ms) / 1000.0)
        if (now - self._last_change_ts) < eff_debounce_sec:
            return False, False

        # 3. Compare settled screen against last translated screen, ignoring oscillating tiles
        if self._last_translated_gray is not None:
            block_vs_last = self._block_diff_map(gray, self._last_translated_gray, threshold=20)
            if block_vs_last is not None:
                eff_mask = osc_mask if osc_mask.shape == block_vs_last.shape else np.zeros_like(block_vs_last, dtype=bool)
                changed_blocks = int(np.sum((block_vs_last > 0.06) & (~eff_mask)))
                if changed_blocks < 2:
                    self._pending_settle = False
                    return False, False
                diff_ratio_non_osc = changed_blocks / float(max(1, block_vs_last.size))
            else:
                diff_ratio_non_osc = 1.0
        else:
            diff_ratio_non_osc = 1.0

        # Major scene transition resets empty-screen backoff
        if diff_ratio_non_osc > 0.25:
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
        self._pending_settle = False
        if not is_cache_hit:
            self._last_api_trigger_ts = now

        return True, is_cache_hit
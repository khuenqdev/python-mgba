# inpaint.py
import re
from difflib import SequenceMatcher
import cv2
import numpy as np


def _norm_match_str(s: str) -> str:
    if not s:
        return ""
    return re.sub(
        r'[▶▷►▼▽▲△◀◁\s「」『』【】\[\]\(\)（）〈〉《》、。！？!?.,:;\-_=•·・]+',
        '',
        s,
    ).strip()


def align_vision_boxes_with_ocr(
    vision_boxes: list,
    ocr_lines_240: list,
    semantic_blocks: list | None = None,
):
    """
    Matches Groq Vision TranslatedBox items to exact 240x160 pixel bounding boxes
    produced by local OCR, including bracketed speaker nameplates (e.g. 【タツキチ】)
    and multi-line dialogue rows while keeping separate menu columns isolated.
    """
    if not vision_boxes or not ocr_lines_240:
        return

    used_line_indices = set()

    for vb in vision_boxes:
        orig_norm = _norm_match_str(vb.original)
        sp_norm = _norm_match_str(getattr(vb, "speaker_orig", ""))
        full_norm = sp_norm + orig_norm
        if not full_norm:
            continue

        vision_had_box = getattr(vb, "has_box", False)
        vision_y = vb.y if vision_had_box else None

        matched_indices = []

        # 1. Try matching a semantic block first
        best_blk = None
        best_blk_score = 0.0
        if semantic_blocks:
            for blk in semantic_blocks:
                blk_norm = _norm_match_str(blk.get("speaker_orig", "") + blk.get("original", ""))
                if not blk_norm:
                    continue
                score = SequenceMatcher(None, full_norm, blk_norm).ratio()
                if score > best_blk_score:
                    best_blk_score = score
                    best_blk = blk

        if best_blk is not None and best_blk_score >= 0.72:
            for idx, ln in enumerate(ocr_lines_240):
                if (
                    ln["cx"] >= best_blk["x"] - 3 and ln["cx"] <= best_blk["x"] + best_blk["w"] + 3
                    and ln["cy"] >= best_blk["y"] - 3 and ln["cy"] <= best_blk["y"] + best_blk["h"] + 3
                ):
                    matched_indices.append(idx)

        # 2. Match individual OCR lines for dialogue text and speaker nameplate
        if not matched_indices:
            for idx, ln in enumerate(ocr_lines_240):
                if idx in used_line_indices:
                    continue
                ln_norm = _norm_match_str(ln["text"])
                if not ln_norm:
                    continue
                if ln_norm == orig_norm or ln_norm == sp_norm:
                    matched_indices.append(idx)
                elif len(ln_norm) >= 2 and (ln_norm in orig_norm or (sp_norm and ln_norm in sp_norm)):
                    matched_indices.append(idx)
                elif SequenceMatcher(None, ln_norm, orig_norm).ratio() >= 0.72:
                    matched_indices.append(idx)

        # 3. Also ensure speaker line directly above dialogue is captured even if bracketed
        if matched_indices and sp_norm:
            cur_y1 = min(ocr_lines_240[i]["y1"] for i in matched_indices)
            cur_x1 = min(ocr_lines_240[i]["x1"] for i in matched_indices)
            for idx, ln in enumerate(ocr_lines_240):
                if idx in matched_indices or idx in used_line_indices:
                    continue
                ln_norm = _norm_match_str(ln["text"])
                if not ln_norm:
                    continue
                is_above = (-4 <= (cur_y1 - ln["y2"]) <= 18) and abs(ln["x1"] - cur_x1) <= 28
                if is_above and (
                    ln_norm == sp_norm
                    or SequenceMatcher(None, ln_norm, sp_norm).ratio() >= 0.65
                ):
                    matched_indices.append(idx)

        if matched_indices:
            for idx in matched_indices:
                used_line_indices.add(idx)
            x1 = min(ocr_lines_240[i]["x1"] for i in matched_indices)
            y1 = min(ocr_lines_240[i]["y1"] for i in matched_indices)
            x2 = max(ocr_lines_240[i]["x2"] for i in matched_indices)
            y2 = max(ocr_lines_240[i]["y2"] for i in matched_indices)

            # If speaker was present in Vision's box above OCR's y1 (e.g. underlined bracketed nameplate)
            if sp_norm and vision_y is not None and vision_y < y1 - 8 and (y1 - vision_y) <= 24:
                y1 = max(0, vision_y)

            vb.x = int(round(x1))
            vb.y = int(round(y1))
            vb.w = max(12, int(round(x2 - x1)))
            vb.h = max(10, int(round(y2 - y1)))
            vb.has_box = True


class InpaintPatch:
    """Precomputed clean background patch + adaptive styling for one TranslatedBox."""

    def __init__(
        self,
        gx: int,
        gy: int,
        gw: int,
        gh: int,
        patch_rgb: np.ndarray,
        bg_rgb: tuple[int, int, int],
        bg_luma: float,
        is_dark_bg: bool,
        box,
    ):
        self.gx = gx
        self.gy = gy
        self.gw = gw
        self.gh = gh
        self.patch_rgb = patch_rgb
        self.bg_rgb = bg_rgb
        self.bg_luma = bg_luma
        self.is_dark_bg = is_dark_bg
        self.box = box


def _sample_ring_bg(frame_data: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> tuple[np.ndarray, float, float]:
    h_img, w_img = frame_data.shape[:2]
    rx1 = max(0, x1 - 2)
    ry1 = max(0, y1 - 2)
    rx2 = min(w_img, x2 + 2)
    ry2 = min(h_img, y2 + 2)

    samples = []
    if ry1 < y1:
        samples.append(frame_data[ry1:y1, rx1:rx2].reshape(-1, 3))
    if y2 < ry2:
        samples.append(frame_data[y2:ry2, rx1:rx2].reshape(-1, 3))
    if rx1 < x1:
        samples.append(frame_data[y1:y2, rx1:x1].reshape(-1, 3))
    if x2 < rx2:
        samples.append(frame_data[y1:y2, x2:rx2].reshape(-1, 3))

    if not samples:
        crop = frame_data[y1:y2, x1:x2].reshape(-1, 3)
        med = np.median(crop, axis=0)
        std = float(np.mean(np.std(crop, axis=0)))
        return med.astype(np.uint8), std, 0.0

    ring = np.concatenate(samples, axis=0)
    med = np.median(ring, axis=0)
    std = float(np.mean(np.std(ring, axis=0)))

    # Measure vertical gradient across left+right side margins (common in shaded GBA UI buttons)
    side_rows = []
    for y in range(y1, y2):
        row_pts = []
        if rx1 < x1:
            row_pts.append(frame_data[y, rx1:x1])
        if x2 < rx2:
            row_pts.append(frame_data[y, x2:rx2])
        if row_pts:
            side_rows.append(np.median(np.concatenate(row_pts, axis=0), axis=0))
    v_grad = float(np.mean(np.std(side_rows, axis=0))) if len(side_rows) >= 3 else 0.0

    return med.astype(np.uint8), std, v_grad


def _row_is_uniform_bg(row_pixels: np.ndarray, bg_rgb: np.ndarray, tol: float = 18.0) -> bool:
    if len(row_pixels) == 0:
        return False
    diff = np.max(np.abs(row_pixels.astype(np.int16) - bg_rgb.astype(np.int16)), axis=1)
    return float(np.mean(diff <= tol)) >= 0.92


def _expand_dialogue_textbox(
    frame_data: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    bg_rgb: np.ndarray,
    other_rects: list,
) -> tuple[int, int, int, int]:
    """Expands a dialogue text region strictly into uniform empty bubble interior."""
    h_img, w_img = frame_data.shape[:2]

    ex1 = max(1, x1 - 2)
    ey1 = max(1, y1 - 2)
    ex2 = min(w_img - 1, x2 + 2)
    ey2 = min(h_img - 1, y2 + 2)

    def _hits_other(nx1, ny1, nx2, ny2) -> bool:
        for ox1, oy1, ox2, oy2 in other_rects:
            if nx1 < ox2 + 2 and nx2 > ox1 - 2 and ny1 < oy2 + 2 and ny2 > oy1 - 2:
                return True
        return False

    limit_r = min(w_img - 2, ex2 + 100)
    while ex2 < limit_r and _row_is_uniform_bg(frame_data[ey1:ey2, ex2], bg_rgb):
        if _hits_other(ex1, ey1, ex2 + 1, ey2):
            break
        ex2 += 1

    limit_l = max(1, ex1 - 6)
    while ex1 > limit_l and _row_is_uniform_bg(frame_data[ey1:ey2, ex1 - 1], bg_rgb):
        if _hits_other(ex1 - 1, ey1, ex2, ey2):
            break
        ex1 -= 1

    limit_b = min(h_img - 2, ey2 + 44)
    while ey2 < limit_b and _row_is_uniform_bg(frame_data[ey2, ex1:ex2], bg_rgb):
        if _hits_other(ex1, ey1, ex2, ey2 + 1):
            break
        ey2 += 1

    limit_t = max(1, ey1 - 4)
    while ey1 > limit_t and _row_is_uniform_bg(frame_data[ey1 - 1, ex1:ex2], bg_rgb):
        if _hits_other(ex1, ey1 - 1, ex2, ey2):
            break
        ey1 -= 1

    return ex1, ey1, ex2, ey2


def build_inpaint_patches(frame_data: np.ndarray, boxes: list) -> list[InpaintPatch]:
    """Generates clean background patches for every TranslatedBox with valid coordinates."""
    if frame_data is None or not boxes:
        return []

    h_img, w_img = frame_data.shape[:2]
    valid_boxes = [
        b for b in boxes
        if getattr(b, "has_box", True) and b.w > 4 and b.h > 4
    ]

    raw_rects = []
    for b in valid_boxes:
        x1 = max(0, min(w_img - 2, int(b.x)))
        y1 = max(0, min(h_img - 2, int(b.y)))
        x2 = max(x1 + 4, min(w_img, x1 + int(b.w)))
        y2 = max(y1 + 4, min(h_img, y1 + int(b.h)))
        raw_rects.append((x1, y1, x2, y2))

    patches = []
    for idx, b in enumerate(valid_boxes):
        x1, y1, x2, y2 = raw_rects[idx]
        other_rects = [raw_rects[j] for j in range(len(raw_rects)) if j != idx]
        cat = getattr(b, "category", "dialogue")

        bg_rgb, ring_std, v_grad = _sample_ring_bg(frame_data, x1, y1, x2, y2)

        if cat == "dialogue" and ring_std < 18.0:
            gx1, gy1, gx2, gy2 = _expand_dialogue_textbox(
                frame_data, x1, y1, x2, y2, bg_rgb, other_rects
            )
        else:
            # Keep menu/label boxes tight around the text so button borders and adjacent icons are untouched
            gx1 = max(0, x1 - 1)
            gy1 = max(0, y1 - 1)
            gx2 = min(w_img, x2 + 1)
            gy2 = min(h_img, y2 + 1)

        gw = max(6, gx2 - gx1)
        gh = max(6, gy2 - gy1)
        patch = frame_data[gy1:gy1 + gh, gx1:gx1 + gw].copy()

        # Build per-row background reference so shaded 3D pills preserve their gradient
        row_bg = np.zeros((gh, 3), dtype=np.int16)
        for ry in range(gh):
            edge_pts = []
            if gx1 > 0:
                edge_pts.append(frame_data[gy1 + ry, max(0, gx1 - 2):gx1 + 1])
            if gx2 < w_img:
                edge_pts.append(frame_data[gy1 + ry, max(0, gx2 - 1):min(w_img, gx2 + 2)])
            if edge_pts and (v_grad > 3.5 or cat in ("menu", "label")):
                row_bg[ry] = np.median(np.concatenate(edge_pts, axis=0), axis=0).astype(np.int16)
            else:
                row_bg[ry] = bg_rgb.astype(np.int16)

        dist = np.max(np.abs(patch.astype(np.int16) - row_bg[:, None, :]), axis=2)
        mask = (dist > 22).astype(np.uint8) * 255
        kernel = np.ones((2, 2), dtype=np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
        mask[0, :] = 0
        mask[-1, :] = 0

        if cat == "dialogue" and ring_std < 7.5 and v_grad < 3.5:
            patch[mask > 0] = bg_rgb
        elif cat in ("menu", "label") and v_grad >= 3.5 and ring_std < 22.0:
            # Row-wise clean gradient fill preserves horizontal pill shading
            for ry in range(gh):
                m_row = mask[ry] > 0
                if np.any(m_row):
                    patch[ry, m_row] = np.clip(row_bg[ry], 0, 255).astype(np.uint8)
        else:
            patch_bgr = cv2.cvtColor(patch, cv2.COLOR_RGB2BGR)
            inpainted_bgr = cv2.inpaint(patch_bgr, mask, 2, cv2.INPAINT_TELEA)
            patch = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)

        r, g, bl = int(bg_rgb[0]), int(bg_rgb[1]), int(bg_rgb[2])
        bg_luma = (0.299 * r) + (0.587 * g) + (0.114 * bl)
        is_dark = bg_luma < 135.0

        patches.append(InpaintPatch(
            gx=gx1,
            gy=gy1,
            gw=gw,
            gh=gh,
            patch_rgb=np.ascontiguousarray(patch, dtype=np.uint8),
            bg_rgb=(r, g, bl),
            bg_luma=bg_luma,
            is_dark_bg=is_dark,
            box=b,
        ))

    return patches


def check_textbox_still_present(current_frame: np.ndarray, patches: list[InpaintPatch]) -> bool:
    """
    Checks whether at least one inpainted textbox background still matches the current
    frame along its outer perimeter (auto-clears stale overlays when dialogue closes).
    """
    if current_frame is None or not patches:
        return True

    h_img, w_img = current_frame.shape[:2]
    for p in patches:
        x1 = max(0, p.gx)
        y1 = max(0, p.gy)
        x2 = min(w_img - 1, p.gx + p.gw - 1)
        y2 = min(h_img - 1, p.gy + p.gh - 1)
        if x2 <= x1 or y2 <= y1:
            continue

        top_row = current_frame[y1, x1:x2 + 1].astype(np.int16)
        bot_row = current_frame[y2, x1:x2 + 1].astype(np.int16)
        edge_pixels = np.concatenate((top_row, bot_row), axis=0)
        bg = np.array(p.bg_rgb, dtype=np.int16)
        match_ratio = float(np.mean(np.max(np.abs(edge_pixels - bg), axis=1) <= 28))
        if match_ratio >= 0.45:
            return True

    return False
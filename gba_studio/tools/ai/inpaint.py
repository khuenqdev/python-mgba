# inpaint.py
import re
from difflib import SequenceMatcher
import cv2
import numpy as np

from .neural_inpaint import run_migan_inpaint


def _norm_match_str(s: str) -> str:
    """Strips brackets, punctuation, cursor icons, and whitespace for robust string matching."""
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

            if sp_norm and vision_y is not None and vision_y < y1 - 8 and (y1 - vision_y) <= 24:
                y1 = max(0, vision_y)

            vb.x = int(round(x1))
            vb.y = int(round(y1))
            vb.w = max(12, int(round(x2 - x1)))
            vb.h = max(10, int(round(y2 - y1)))
            vb.has_box = True


class InpaintPatch:
    """Precomputed clean background patch + placement bounds for one TranslatedBox."""

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
        is_baked_graphic: bool,
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
        self.is_baked_graphic = is_baked_graphic
        self.box = box


def _sample_ring_bg(frame_data: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> tuple[np.ndarray, float, float]:
    h_img, w_img = frame_data.shape[:2]
    rx1 = max(0, x1 - 3)
    ry1 = max(0, y1 - 3)
    rx2 = min(w_img, x2 + 3)
    ry2 = min(h_img, y2 + 3)

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


def _find_dialogue_bubble_bounds(
    frame_data: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    bg_rgb: np.ndarray,
    other_rects: list,
) -> tuple[int, int, int, int]:
    """
    Expands dialogue placement bounds strictly inside the flat bubble interior,
    stopping cleanly before shaded bottom/side inner bevels and borders.
    """
    h_img, w_img = frame_data.shape[:2]
    diff = np.max(np.abs(frame_data.astype(np.int16) - bg_rgb.astype(np.int16)), axis=2)
    is_interior = diff <= 14

    ex1, ey1, ex2, ey2 = x1, y1, x2, y2

    def _hits_other(nx1, ny1, nx2, ny2) -> bool:
        for ox1, oy1, ox2, oy2 in other_rects:
            if nx1 < ox2 + 2 and nx2 > ox1 - 2 and ny1 < oy2 + 2 and ny2 > oy1 - 2:
                return True
        return False

    limit_r = min(w_img - 3, ex2 + 160)
    while ex2 < limit_r:
        if float(np.mean(is_interior[ey1:ey2, ex2 + 1])) < 0.92 or _hits_other(ex1, ey1, ex2 + 1, ey2):
            break
        ex2 += 1

    limit_l = max(3, ex1 - 32)
    while ex1 > limit_l:
        if float(np.mean(is_interior[ey1:ey2, ex1 - 1])) < 0.92 or _hits_other(ex1 - 1, ey1, ex2, ey2):
            break
        ex1 -= 1

    limit_b = min(h_img - 3, ey2 + 70)
    while ey2 < limit_b:
        if float(np.mean(is_interior[ey2 + 1, ex1:ex2])) < 0.92 or _hits_other(ex1, ey1, ex2, ey2 + 1):
            break
        ey2 += 1

    limit_t = max(3, ey1 - 8)
    while ey1 > limit_t:
        if float(np.mean(is_interior[ey1 - 1, ex1:ex2])) < 0.92 or _hits_other(ex1, ey1 - 1, ex2, ey2):
            break
        ey1 -= 1

    return ex1, ey1, ex2, ey2


def _extract_glyph_and_shadow_mask(crop_rgb: np.ndarray, bg_rgb: np.ndarray, close_blobs: bool = False) -> np.ndarray:
    diff = np.max(np.abs(crop_rgb.astype(np.int16) - bg_rgb.astype(np.int16)), axis=2)
    mask = (diff > 16).astype(np.uint8) * 255
    if close_blobs:
        # Merge intra-character strokes into solid character blobs so clean background rows stay untouched
        close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_k)
    dil_k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.dilate(mask, dil_k, iterations=1)
    mask[0, :] = 0
    mask[-1, :] = 0
    mask[:, 0] = 0
    mask[:, -1] = 0
    return mask


def _inpaint_button_clean_gradient(patch_rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = patch_rgb.copy()
    h, w, _ = patch_rgb.shape
    for y in range(h):
        masked = np.where(mask[y] > 0)[0]
        if len(masked) == 0:
            continue
        c_left = np.median(patch_rgb[y, :max(1, min(4, w // 6))], axis=0)
        c_right = np.median(patch_rgb[y, max(0, w - max(1, min(4, w // 6))):], axis=0)
        for x in masked:
            t = float(x) / float(max(1, w - 1))
            out[y, x] = np.clip((1.0 - t) * c_left + t * c_right, 0, 255).astype(np.uint8)
    return out


def build_inpaint_patches(
    frame_data: np.ndarray,
    boxes: list,
    mode: str = "auto",
    models_dir: str | None = None,
) -> list[InpaintPatch]:
    """
    Builds clean reconstructed background patches for every TranslatedBox:
      - Inside clean dialogue boxes & buttons, masks ONLY the closed text+shadow blobs
        so surrounding bubble bevels & clean rows stay 100% intact for MI-GAN.
      - On complex baked art, masks the full tight box so MI-GAN cleanly regenerates art.
    """
    if frame_data is None or not boxes:
        return []

    h_img, w_img = frame_data.shape[:2]
    valid_boxes = [
        b for b in boxes
        if getattr(b, "has_box", True) and b.w > 4 and b.h > 4
    ]
    if not valid_boxes:
        return []

    raw_rects = []
    for b in valid_boxes:
        x1 = max(0, min(w_img - 2, int(b.x)))
        y1 = max(0, min(h_img - 2, int(b.y)))
        x2 = max(x1 + 4, min(w_img, x1 + int(b.w)))
        y2 = max(y1 + 4, min(h_img, y1 + int(b.h)))
        raw_rects.append((x1, y1, x2, y2))

    meta_list = []
    full_erase_mask = np.zeros((h_img, w_img), dtype=np.uint8)

    for idx, b in enumerate(valid_boxes):
        x1, y1, x2, y2 = raw_rects[idx]
        other_rects = [raw_rects[j] for j in range(len(raw_rects)) if j != idx]
        cat = getattr(b, "category", "dialogue")

        bg_rgb, ring_std, v_grad = _sample_ring_bg(frame_data, x1, y1, x2, y2)
        is_baked = (ring_std > 28.0 and v_grad < 2.0)

        if cat == "dialogue" and ring_std < 22.0:
            gx1, gy1, gx2, gy2 = _find_dialogue_bubble_bounds(
                frame_data, x1, y1, x2, y2, bg_rgb, other_rects
            )
        else:
            gx1 = max(0, x1 - 1)
            gy1 = max(0, y1 - 1)
            gx2 = min(w_img, x2 + 1)
            gy2 = min(h_img, y2 + 1)

        if is_baked:
            ex1 = max(0, x1 - 2)
            ey1 = max(0, y1 - 2)
            ex2 = min(w_img, x2 + 2)
            ey2 = min(h_img, y2 + 2)
            full_erase_mask[ey1:ey2, ex1:ex2] = 255
        else:
            # Mask closed character blobs strictly inside the text region so borders/bevels are untouched
            cx1 = max(gx1, x1 - 1)
            cy1 = max(gy1, y1 - 1)
            cx2 = min(gx2, x2 + 1)
            cy2 = min(gy2, y2 + 1)
            if cx2 > cx1 + 2 and cy2 > cy1 + 2:
                local_crop = frame_data[cy1:cy2, cx1:cx2]
                local_mask = _extract_glyph_and_shadow_mask(local_crop, bg_rgb, close_blobs=True)
                full_erase_mask[cy1:cy2, cx1:cx2] = np.maximum(
                    full_erase_mask[cy1:cy2, cx1:cx2], local_mask
                )

        meta_list.append((b, cat, x1, y1, x2, y2, gx1, gy1, gx2, gy2, bg_rgb, ring_std, v_grad, is_baked))

    migan_scale = 2
    migan_cleaned = None
    if mode in ("auto", "onnx"):
        migan_cleaned = run_migan_inpaint(
            frame_data, full_erase_mask, models_dir=models_dir, upscale=migan_scale
        )

    patches = []
    for (b, cat, x1, y1, x2, y2, gx1, gy1, gx2, gy2, bg_rgb, ring_std, v_grad, is_baked) in meta_list:
        gw = max(6, gx2 - gx1)
        gh = max(6, gy2 - gy1)

        if migan_cleaned is not None:
            py1, py2 = gy1 * migan_scale, (gy1 + gh) * migan_scale
            px1, px2 = gx1 * migan_scale, (gx1 + gw) * migan_scale
            patch = migan_cleaned[py1:py2, px1:px2].copy()
            med_after = np.median(patch.reshape(-1, 3), axis=0)
            r, g, bl = int(med_after[0]), int(med_after[1]), int(med_after[2])
            bg_luma = (0.299 * r) + (0.587 * g) + (0.114 * bl)
            patches.append(InpaintPatch(
                gx=gx1, gy=gy1, gw=gw, gh=gh,
                patch_rgb=np.ascontiguousarray(patch, dtype=np.uint8),
                bg_rgb=(r, g, bl), bg_luma=bg_luma,
                is_dark_bg=(bg_luma < 135.0),
                is_baked_graphic=False,
                box=b,
            ))
            continue

        patch = frame_data[gy1:gy1 + gh, gx1:gx1 + gw].copy()
        if is_baked:
            bg_luma = float(np.mean(bg_rgb))
            patches.append(InpaintPatch(
                gx=gx1, gy=gy1, gw=gw, gh=gh,
                patch_rgb=patch, bg_rgb=tuple(int(c) for c in bg_rgb),
                bg_luma=bg_luma, is_dark_bg=(bg_luma < 135.0),
                is_baked_graphic=True, box=b,
            ))
            continue

        mask = _extract_glyph_and_shadow_mask(patch, bg_rgb, close_blobs=True)
        is_button_or_pill = (cat in ("menu", "label")) or (gh <= 24) or (v_grad >= 2.5)
        if is_button_or_pill:
            patch = _inpaint_button_clean_gradient(patch, mask)
        elif ring_std < 8.5:
            patch[mask > 0] = bg_rgb
        else:
            patch_bgr = cv2.cvtColor(patch, cv2.COLOR_RGB2BGR)
            inpainted_bgr = cv2.inpaint(patch_bgr, mask, 2, cv2.INPAINT_TELEA)
            patch = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)

        r, g, bl = int(bg_rgb[0]), int(bg_rgb[1]), int(bg_rgb[2])
        bg_luma = (0.299 * r) + (0.587 * g) + (0.114 * bl)
        patches.append(InpaintPatch(
            gx=gx1, gy=gy1, gw=gw, gh=gh,
            patch_rgb=np.ascontiguousarray(patch, dtype=np.uint8),
            bg_rgb=(r, g, bl), bg_luma=bg_luma,
            is_dark_bg=(bg_luma < 135.0),
            is_baked_graphic=False, box=b,
        ))

    return patches


def check_textbox_still_present(current_frame: np.ndarray, patches: list[InpaintPatch]) -> bool:
    if current_frame is None or not patches:
        return True

    h_img, w_img = current_frame.shape[:2]
    for p in patches:
        if p.is_baked_graphic:
            continue
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
# neural_inpaint.py
import os
import threading
import urllib.request
import cv2
import numpy as np

from ...paths import PROJECT_ROOT

MIGAN_MODEL_FILENAME = "migan_pipeline_v2.onnx"
MIGAN_MODEL_URL = (
    "https://huggingface.co/andraniksargsyan/migan/resolve/main/migan_pipeline_v2.onnx"
)

_MIGAN_LOCK = threading.Lock()
_MIGAN_SESSION = None
_MIGAN_PATH = None


def get_migan_model_path(models_dir: str | None = None) -> str:
    if not models_dir:
        models_dir = os.path.join(str(PROJECT_ROOT), "models")
    os.makedirs(models_dir, exist_ok=True)
    return os.path.join(models_dir, MIGAN_MODEL_FILENAME)


def is_migan_available(models_dir: str | None = None) -> bool:
    path = get_migan_model_path(models_dir)
    if not os.path.exists(path) or os.path.getsize(path) < 1_000_000:
        return False
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def download_migan_model(models_dir: str | None = None, progress_cb=None) -> str:
    """Downloads MI-GAN ONNX (~28 MB, MIT) atomically to models/migan_pipeline_v2.onnx."""
    dest = get_migan_model_path(models_dir)
    tmp = dest + ".downloading"
    req = urllib.request.Request(MIGAN_MODEL_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        read_bytes = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                read_bytes += len(chunk)
                if progress_cb and total > 0:
                    progress_cb(read_bytes, total)
    os.replace(tmp, dest)
    return dest


def get_migan_session(models_dir: str | None = None):
    """Lazily initializes and caches the ONNX Runtime MI-GAN InferenceSession."""
    global _MIGAN_SESSION, _MIGAN_PATH
    path = get_migan_model_path(models_dir)
    if not os.path.exists(path) or os.path.getsize(path) < 1_000_000:
        return None

    with _MIGAN_LOCK:
        if _MIGAN_SESSION is not None and _MIGAN_PATH == path:
            return _MIGAN_SESSION
        try:
            import onnxruntime as ort
            sess_opts = ort.SessionOptions()
            sess_opts.intra_op_num_threads = min(4, os.cpu_count() or 2)
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            print(f"[AI Inpainting] Loading MI-GAN ONNX ({os.path.basename(path)})...")
            _MIGAN_SESSION = ort.InferenceSession(
                path, sess_options=sess_opts, providers=["CPUExecutionProvider"]
            )
            _MIGAN_PATH = path
            return _MIGAN_SESSION
        except Exception as e:
            print(f"[AI Inpainting] Failed to load MI-GAN ONNX: {e}")
            _MIGAN_SESSION = False
            return None


def run_migan_inpaint(
    frame_rgb: np.ndarray,
    erase_mask_240: np.ndarray,
    models_dir: str | None = None,
    upscale: int = 2,
) -> np.ndarray | None:
    """
    Runs MI-GAN ONNX full-frame neural inpainting.
      - frame_rgb: HxWx3 uint8 RGB (240x160)
      - erase_mask_240: HxW uint8 where >0 marks pixels to ERASE.
      - Returns cleaned uint8 RGB image at (H * upscale) x (W * upscale) resolution,
        or None if MI-GAN is unavailable.
    """
    sess = get_migan_session(models_dir)
    if sess is None or sess is False:
        return None

    h, w = frame_rgb.shape[:2]
    scale = max(1, int(upscale))

    if scale > 1:
        # 2x nearest-neighbor preserves crisp pixel edges for MI-GAN's 512x512 crop window
        work_img = cv2.resize(frame_rgb, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
        work_erase = cv2.resize(erase_mask_240, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
    else:
        work_img = frame_rgb
        work_erase = erase_mask_240

    # migan_pipeline_v2.onnx convention: mask uint8 [1, 1, H, W], 0 = fill/erase, 255 = keep
    migan_mask = np.where(work_erase > 0, 0, 255).astype(np.uint8)

    img_nchw = np.ascontiguousarray(np.transpose(work_img, (2, 0, 1))[None, ...], dtype=np.uint8)
    mask_nchw = np.ascontiguousarray(migan_mask[None, None, ...], dtype=np.uint8)

    in_names = [inp.name for inp in sess.get_inputs()]
    out_name = sess.get_outputs()[0].name

    with _MIGAN_LOCK:
        out_nchw = sess.run([out_name], {in_names[0]: img_nchw, in_names[1]: mask_nchw})[0]

    cleaned_rgb = np.transpose(out_nchw[0], (1, 2, 0))
    return np.ascontiguousarray(np.clip(cleaned_rgb, 0, 255), dtype=np.uint8)
# clip_recorder.py
import os
import time
import queue
import datetime
import numpy as np
from PIL import Image

from PyQt6.QtCore import QObject, QThread, pyqtSignal


class _GifExportThread(QThread):
    finished_path = pyqtSignal(str)
    error_msg = pyqtSignal(str)

    def __init__(self, frames: list, out_path: str, scale: int):
        super().__init__()
        self.frames = frames
        self.out_path = out_path
        self.scale = max(1, int(scale))

    def run(self):
        try:
            pil_frames = []
            for arr in self.frames:
                img = Image.fromarray(arr, "RGB")
                if self.scale > 1:
                    w, h = img.size
                    img = img.resize((w * self.scale, h * self.scale), Image.Resampling.NEAREST)
                q_img = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
                pil_frames.append(q_img)

            if not pil_frames:
                self.error_msg.emit("No frames were captured for GIF export.")
                return

            tmp_path = self.out_path + ".tmp"
            pil_frames[0].save(
                tmp_path,
                format="GIF",
                save_all=True,
                append_images=pil_frames[1:],
                duration=33,
                loop=0,
                optimize=False,
            )
            os.replace(tmp_path, self.out_path)
            self.finished_path.emit(self.out_path)
        except Exception as e:
            self.error_msg.emit(f"GIF export failed: {e}")


class _Mp4WriterThread(QThread):
    finished_path = pyqtSignal(str)
    error_msg = pyqtSignal(str)

    def __init__(self, out_path: str, scale: int, fps: float = 60.0):
        super().__init__()
        self.out_path = out_path
        self.scale = max(1, int(scale))
        self.fps = fps
        self.frame_queue = queue.Queue(maxsize=240)
        self._stop_sentinel = object()

    def push(self, frame: np.ndarray):
        try:
            self.frame_queue.put_nowait(frame)
        except queue.Full:
            pass

    def finish(self):
        while True:
            try:
                self.frame_queue.put(self._stop_sentinel, timeout=0.5)
                break
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass

    def run(self):
        writer = None
        wrote_any = False
        try:
            import cv2

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")

            while True:
                item = self.frame_queue.get()
                if item is self._stop_sentinel:
                    break

                h, w = item.shape[:2]
                target_w = w * self.scale
                target_h = h * self.scale

                if writer is None:
                    writer = cv2.VideoWriter(self.out_path, fourcc, self.fps, (target_w, target_h))
                    if not writer.isOpened():
                        self.error_msg.emit("Failed to open MP4 VideoWriter (mp4v codec unavailable).")
                        return

                frame = item
                if self.scale > 1:
                    frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
                bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                writer.write(bgr)
                wrote_any = True

            if writer is not None:
                writer.release()

            if wrote_any and os.path.exists(self.out_path):
                self.finished_path.emit(self.out_path)
            else:
                self.error_msg.emit("No frames were captured for MP4 export.")
        except Exception as e:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            self.error_msg.emit(f"MP4 recording failed: {e}")


class ClipRecorder(QObject):
    """Records clean gameplay frames to Animated GIF (30 FPS) or MP4 (60 FPS)."""

    export_finished = pyqtSignal(str)
    export_error = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    recording_stopped = pyqtSignal()

    MAX_GIF_FRAMES = 1800  # ~60 seconds at 30 FPS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_recording = False
        self.format = "gif"
        self.scale = 2
        self.output_path = ""
        self._frame_counter = 0
        self._gif_frames = []
        self._mp4_thread = None
        self._export_threads = []
        self._start_time = 0.0
        self._last_badge_sec = -1

    def start(self, fmt: str, scale: int, records_dir: str = "records", rom_name: str = "gba") -> tuple[bool, str]:
        if self.is_recording:
            return False, "A clip recording is already active."

        fmt = fmt.lower().strip()
        if fmt not in ("gif", "mp4"):
            return False, f"Unsupported clip format: {fmt}"

        if fmt == "mp4":
            try:
                import cv2  # noqa: F401
            except ImportError:
                return False, "opencv-python (cv2) is required for MP4 recording."

        self.format = fmt
        self.scale = max(1, min(4, int(scale)))
        os.makedirs(records_dir, exist_ok=True)

        tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_rom = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (rom_name or "gba")).strip() or "gba"
        self.output_path = os.path.join(records_dir, f"{safe_rom}_clip_{tag}.{fmt}")

        self._frame_counter = 0
        self._gif_frames = []
        self._start_time = time.monotonic()
        self._last_badge_sec = 0

        if fmt == "mp4":
            t = _Mp4WriterThread(self.output_path, scale=self.scale, fps=60.0)
            t.finished_path.connect(self.export_finished.emit)
            t.error_msg.connect(self.export_error.emit)
            t.finished.connect(lambda th=t: self._cleanup_thread(th))
            self._mp4_thread = t
            self._export_threads.append(t)
            t.start()

        self.is_recording = True
        self.status_changed.emit(f"● REC {fmt.upper()} 00:00")
        return True, self.output_path

    def push_frame(self, frame_data: np.ndarray):
        if not self.is_recording or frame_data is None:
            return

        self._frame_counter += 1

        if self.format == "gif":
            # Sample at ~30 FPS so browser/viewer GIF playback timing stays accurate
            if self._frame_counter % 2 != 0:
                return
            self._gif_frames.append(frame_data.copy())
            if len(self._gif_frames) >= self.MAX_GIF_FRAMES:
                self.stop()
                return
        elif self._mp4_thread is not None:
            self._mp4_thread.push(frame_data.copy())

        elapsed = int(time.monotonic() - self._start_time)
        if elapsed != self._last_badge_sec:
            self._last_badge_sec = elapsed
            mm, ss = divmod(elapsed, 60)
            self.status_changed.emit(f"● REC {self.format.upper()} {mm:02d}:{ss:02d}")

    def stop(self, wait: bool = False):
        if not self.is_recording:
            return

        self.is_recording = False
        self.status_changed.emit("")
        self.recording_stopped.emit()

        if self.format == "mp4" and self._mp4_thread is not None:
            mp4_t = self._mp4_thread
            self._mp4_thread = None
            mp4_t.finish()
            if wait:
                mp4_t.wait()
            return

        if self.format == "gif":
            frames = self._gif_frames
            self._gif_frames = []
            if not frames:
                self.export_error.emit("No frames were captured for GIF export.")
                return

            gif_t = _GifExportThread(frames, self.output_path, self.scale)
            gif_t.finished_path.connect(self.export_finished.emit)
            gif_t.error_msg.connect(self.export_error.emit)
            gif_t.finished.connect(lambda th=gif_t: self._cleanup_thread(th))
            self._export_threads.append(gif_t)
            gif_t.start()
            if wait:
                gif_t.wait()

    def _cleanup_thread(self, thread: QThread):
        if thread in self._export_threads:
            self._export_threads.remove(thread)
        thread.deleteLater()
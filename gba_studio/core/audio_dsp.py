# audio_dsp.py
import numpy as np


class WSOLATimeStretcher:
    """
    Pure-NumPy real-time WSOLA (Waveform Similarity Overlap-Add) time-stretcher.
    Compresses stereo PCM16 audio duration by `speed` (e.g. 1.5x - 4.0x) while
    preserving original pitch using periodic Hann 50% OLA and normalized
    cross-correlation alignment.
    """

    def __init__(self, window_size: int = 1024, tolerance: int = 192):
        self.win_size = int(window_size)
        if self.win_size % 2 != 0:
            self.win_size += 1
        self.synth_hop = self.win_size // 2
        self.tolerance = int(tolerance)

        # Periodic Hann window: w[k] + w[k + win_size//2] == 1.0 everywhere
        n = np.arange(self.win_size, dtype=np.float32)
        self.window = 0.5 * (1.0 - np.cos((2.0 * np.pi * n) / self.win_size))
        self.win_first = self.window[:self.synth_hop][:, None]
        self.win_second = self.window[self.synth_hop:][:, None]

        self.reset()

    def reset(self):
        self._in_buf = np.empty((0, 2), dtype=np.float32)
        self._ola_buf = np.zeros((self.synth_hop, 2), dtype=np.float32)
        self._prev_cont = None
        self._cursor = 0
        self._initialized = False

    def process(self, audio_data: np.ndarray, speed: float) -> np.ndarray:
        if audio_data is None or len(audio_data) == 0:
            return np.empty((0, 2), dtype=np.int16)

        speed = max(1.0, float(speed))
        if speed <= 1.02:
            return audio_data

        incoming = audio_data.astype(np.float32)
        if len(self._in_buf) == 0:
            self._in_buf = incoming
        else:
            self._in_buf = np.concatenate((self._in_buf, incoming), axis=0)

        # Prevent unbounded lag if stream stalls
        if len(self._in_buf) > 24576:
            excess = len(self._in_buf) - 16384
            self._in_buf = self._in_buf[excess:]
            self._cursor = max(0, self._cursor - excess)

        analysis_hop = max(1, int(round(self.synth_hop * speed)))
        out_chunks = []

        # Bootstrap the very first overlap-add window
        if not self._initialized:
            if len(self._in_buf) < self.win_size:
                return np.empty((0, 2), dtype=np.int16)

            first_seg = self._in_buf[:self.win_size]
            out_chunks.append(first_seg[:self.synth_hop])
            self._ola_buf = first_seg[self.synth_hop:] * self.win_second

            cont_slice = self._in_buf[self.synth_hop:self.win_size]
            self._prev_cont = 0.5 * (cont_slice[:, 0] + cont_slice[:, 1])
            self._cursor = analysis_hop
            self._initialized = True

        # Generate pitch-preserved compressed blocks while enough lookahead exists
        while len(self._in_buf) >= self._cursor + self.tolerance + self.win_size:
            s_min = max(0, self._cursor - self.tolerance)
            s_max = self._cursor + self.tolerance

            # Search slice covers [s_min, s_max + synth_hop)
            search_end = s_max + self.synth_hop
            search_stereo = self._in_buf[s_min:search_end]
            search_mono = 0.5 * (search_stereo[:, 0] + search_stereo[:, 1])

            best_start = self._cursor
            if len(search_mono) >= self.synth_hop and self._prev_cont is not None:
                # Vectorized normalized cross-correlation over all candidate offsets
                corr = np.correlate(search_mono, self._prev_cont, mode="valid")
                sq = search_mono * search_mono
                cs = np.empty(len(sq) + 1, dtype=np.float32)
                cs[0] = 0.0
                np.cumsum(sq, out=cs[1:])
                cand_energy = cs[self.synth_hop:] - cs[:-self.synth_hop]
                score = corr / np.sqrt(cand_energy + 1e-6)
                best_offset = int(np.argmax(score))
                best_start = s_min + best_offset

            seg = self._in_buf[best_start:best_start + self.win_size]
            out_block = self._ola_buf + (seg[:self.synth_hop] * self.win_first)
            out_chunks.append(out_block)

            self._ola_buf = seg[self.synth_hop:] * self.win_second
            cont_slice = self._in_buf[best_start + self.synth_hop:best_start + self.win_size]
            self._prev_cont = 0.5 * (cont_slice[:, 0] + cont_slice[:, 1])
            self._cursor += analysis_hop

            # Trim already-consumed input prefix
            if self._cursor > self.tolerance + self.win_size:
                drop = self._cursor - self.tolerance
                self._in_buf = self._in_buf[drop:]
                self._cursor -= drop

        if not out_chunks:
            return np.empty((0, 2), dtype=np.int16)

        merged = np.concatenate(out_chunks, axis=0)
        return np.clip(merged, -32768.0, 32767.0).astype(np.int16)


class AudioDSP:
    """Zero-dependency, vectorized real-time audio enhancement DSP pipeline."""

    FIR_KERNELS = {
        # Light: Cutoff ~12 kHz
        "light": np.array([0.04, 0.11, 0.22, 0.26, 0.22, 0.11, 0.04], dtype=np.float32),

        # Medium: Cutoff ~8 kHz (Recommended authentic GBA warm sound)
        "medium": np.array([0.015, 0.045, 0.095, 0.160, 0.210, 0.160, 0.095, 0.045, 0.015], dtype=np.float32),

        # Heavy: Cutoff ~5 kHz (Maximum smoothness)
        "heavy": np.array([0.01, 0.02, 0.05, 0.09, 0.14, 0.19, 0.14, 0.09, 0.05, 0.02, 0.01], dtype=np.float32),

        # Bass extraction kernel (~300 Hz low-pass)
        "bass": np.ones(31, dtype=np.float32) / 31.0,
    }

    def __init__(self, config):
        self.config = config
        self.stretcher = WSOLATimeStretcher(window_size=1024, tolerance=192)
        self.update_settings()

    def update_settings(self):
        """Refreshes DSP settings directly from the active configuration."""
        self.muted = bool(self.config.get("audio", "mute"))
        self.lpf_mode = str(self.config.get("audio", "low_pass_filter") or "off").lower()
        self.stereo_widener = bool(self.config.get("audio", "stereo_widener"))
        self.bass_boost = bool(self.config.get("audio", "bass_boost"))
        self.soft_limiter = bool(self.config.get("audio", "soft_limiter"))
        self.ff_pitch_corrected = bool(self.config.get("audio", "fast_forward_pitch_corrected", True))

        # Safe volume parsing: heal old 0-256 scale if present in saved JSON
        raw_vol = self.config.get("audio", "volume")
        try:
            val = float(raw_vol) if raw_vol is not None else 0.5
            if val > 2.0:  # Detect and heal old 0-256 integer scale
                val = 0.5
            self.volume_gain = max(0.0, min(val, 1.5))
        except (ValueError, TypeError):
            self.volume_gain = 0.5

    def reset_stretcher(self):
        self.stretcher.reset()

    def process(self, audio_data: np.ndarray) -> np.ndarray:
        if audio_data is None or len(audio_data) == 0:
            return audio_data

        if self.muted or self.volume_gain <= 0.0:
            return np.zeros_like(audio_data)

        data = audio_data.astype(np.float32)
        left = data[:, 0]
        right = data[:, 1]

        # 1. Low-Pass Filter (LPF)
        if self.lpf_mode in self.FIR_KERNELS and self.lpf_mode != "off":
            kernel = self.FIR_KERNELS[self.lpf_mode]
            left = np.convolve(left, kernel, mode="same")
            right = np.convolve(right, kernel, mode="same")

        # 2. Bass Boost
        if self.bass_boost:
            bass_kernel = self.FIR_KERNELS["bass"]
            bass_l = np.convolve(left, bass_kernel, mode="same")
            bass_r = np.convolve(right, bass_kernel, mode="same")
            left = left + (bass_l * 0.45)
            right = right + (bass_r * 0.45)

        # 3. Stereo Widener (Mid-Side Matrix Expansion)
        if self.stereo_widener:
            mid = (left + right) * 0.5
            side = (left - right) * 0.5 * 1.45
            left = mid + side
            right = mid - side

        # 4. Volume Gain (scaled to comfortable master listening volume)
        left *= self.volume_gain
        right *= self.volume_gain

        # 5. Soft Limiter / Anti-Clipping (Analog Tanh Saturation)
        if self.soft_limiter:
            peak = 32767.0
            left = np.tanh(left / peak) * peak
            right = np.tanh(right / peak) * peak

        out = np.empty_like(audio_data)
        out[:, 0] = np.clip(left, -32768, 32767).astype(np.int16)
        out[:, 1] = np.clip(right, -32768, 32767).astype(np.int16)
        return out

    def process_fast_forward(self, audio_data: np.ndarray, speed: float) -> np.ndarray:
        """Processes fast-forward audio with pitch-preserving WSOLA time-stretching."""
        if audio_data is None or len(audio_data) == 0:
            return np.empty((0, 2), dtype=np.int16)

        if self.muted or self.volume_gain <= 0.0 or not self.ff_pitch_corrected:
            return np.empty((0, 2), dtype=np.int16)

        # Above 4.5x turbo speeds, audio stretching becomes sparse; mute cleanly
        if speed > 4.5:
            self.stretcher.reset()
            return np.empty((0, 2), dtype=np.int16)

        enhanced = self.process(audio_data)
        return self.stretcher.process(enhanced, speed)
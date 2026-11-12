# audio_dsp.py
import numpy as np

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
        "bass": np.ones(31, dtype=np.float32) / 31.0
    }

    def __init__(self, config):
        self.config = config
        self.update_settings()

    def update_settings(self):
        """Refreshes DSP settings directly from the active configuration."""
        self.lpf_mode = str(self.config.get("audio", "low_pass_filter") or "off").lower()
        self.stereo_widener = bool(self.config.get("audio", "stereo_widener"))
        self.bass_boost = bool(self.config.get("audio", "bass_boost"))
        self.soft_limiter = bool(self.config.get("audio", "soft_limiter"))
        
        # Safe volume parsing: heal old 0-256 scale if present in saved JSON
        raw_vol = self.config.get("audio", "volume")
        try:
            val = float(raw_vol) if raw_vol is not None else 0.5
            if val > 2.0: # Detect and heal old 0-256 integer scale
                val = 0.5
            self.volume_gain = max(0.0, min(val, 1.5))
        except (ValueError, TypeError):
            self.volume_gain = 0.5

    def process(self, audio_data: np.ndarray) -> np.ndarray:
        if audio_data is None or len(audio_data) == 0:
            return audio_data

        data = audio_data.astype(np.float32)
        left = data[:, 0]
        right = data[:, 1]

        # 1. Low-Pass Filter (LPF)
        if self.lpf_mode in self.FIR_KERNELS and self.lpf_mode != "off":
            kernel = self.FIR_KERNELS[self.lpf_mode]
            left = np.convolve(left, kernel, mode='same')
            right = np.convolve(right, kernel, mode='same')

        # 2. Bass Boost
        if self.bass_boost:
            bass_kernel = self.FIR_KERNELS["bass"]
            bass_l = np.convolve(left, bass_kernel, mode='same')
            bass_r = np.convolve(right, bass_kernel, mode='same')
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
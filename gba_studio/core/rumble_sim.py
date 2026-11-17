# rumble_sim.py
import time
from collections import deque
import numpy as np

from .rumble_profile import RumbleProfileStore


class SimulatedRumbleEngine:
    """
    Ultra-low-overhead (<0.15 ms/frame) multi-modal simulated rumble engine:
      1. Tier 1 (Exact): Per-game ROM SFX profile + streaming RAM pointer tracking.
      2. Tier 2 (Universal):
         - 2D motion-compensated screen-shake detector (ignores smooth scrolling)
         - Localized 6x10 playfield sprite hit-flash / spark / red damage detector
         - Positive multi-band FFT spectral flux (catches sharp punches over loud BGM)
         - Full-duration hold envelope (never cuts off motors mid-spin-up)
    """

    VALID_MODES = ("auto", "always", "off")
    VALID_PROFILES = ("balanced", "impacts_only", "rpg_magic")

    def __init__(self, config):
        self.config = config
        self.profile_store = RumbleProfileStore(config)
        self._fft_cache = {}
        self.update_settings()
        self.reset()

    def load_profile_for_rom(self, rom_path: str, profile_id: str) -> bool:
        loaded = self.profile_store.load_for_game(profile_id, rom_path=rom_path)
        if loaded:
            print(
                f"[RUMBLE PROFILE] Loaded {len(self.profile_store._active_entries)} "
                f"active SFX trigger(s) from {self.profile_store.profile_path}"
            )
        return loaded

    def has_rom_profile(self) -> bool:
        return self.profile_store.has_active_entries()

    def update_settings(self):
        self.mode = str(self.config.get("sensors", "sim_rumble_mode", "auto") or "auto").lower()
        if self.mode not in self.VALID_MODES:
            self.mode = "auto"

        self.profile = str(self.config.get("sensors", "sim_rumble_profile", "balanced") or "balanced").lower()
        if self.profile not in self.VALID_PROFILES:
            self.profile = "balanced"

        try:
            sens = float(self.config.get("sensors", "sim_rumble_sensitivity", 1.0) or 1.0)
        except (ValueError, TypeError):
            sens = 1.0
        self.sensitivity = max(0.3, min(2.2, sens))

    def should_run(self, native_rumble_type: str) -> bool:
        if not bool(self.config.get("sensors", "rumble_enabled", True)):
            return False
        if self.mode == "off":
            return False
        if self.mode == "always":
            return True
        return native_rumble_type in ("None", "", None)

    def reset(self):
        self._prev_gray_roi = None
        self._prev_col_prof = None
        self._prev_row_prof = None
        self._prev_rgb_coarse = None
        self._prev_blk_gray = None
        self._prev_blk_red = None
        self._prev_luma_mean = 0.0
        self._shift_hist = deque(maxlen=6)
        self._magic_streak = 0
        self._scene_cut_cooldown = 0

        self._low_base = 0.018
        self._mid_base = 0.018
        self._high_base = 0.018
        self._mag_ema = None
        self._low_peak_frames = deque(maxlen=8)
        self._last_low_peak_f = -100

        self._last_action_frame = -100
        self._dpad_menu_streak = 0
        self._last_item_ts = 0.0

        # Full-duration hold envelope
        self._hold_until = 0.0
        self._active_low = 0.0
        self._active_high = 0.0
        self._was_active = False

        self.profile_store.reset_runtime_state()

    # =========================================================================
    # FAST MOTION-COMPENSATED + LOCALIZED SPRITE-FLASH VIDEO ANALYZER
    # =========================================================================
    def _analyze_video(self, frame_rgb: np.ndarray | None) -> dict:
        out = {
            "shake": 0.0,
            "hit_flash": 0.0,
            "red_flash": 0.0,
            "local_flash": 0.0,
            "local_red": 0.0,
            "true_changed": 0.0,
            "raw_changed": 0.0,
            "is_menu_static": True,
            "is_scene_cut": False,
            "magic_visual": 0.0,
            "item_visual": 0.0,
        }
        if frame_rgb is None or frame_rgb.ndim != 3:
            return out

        # Coarse 4x4 subsampled RGB (40x60 = 2,400 px)
        coarse = frame_rgb[::4, ::4].astype(np.int16)
        gray_c = (coarse[:, :, 0] * 77 + coarse[:, :, 1] * 150 + coarse[:, :, 2] * 29) >> 8
        luma_mean = float(gray_c.mean())
        luma_std = float(gray_c.std())

        # 6x10 playfield blocks (coarse rows 5..35, cols 5..55 -> native x: 20..220, y: 20..140)
        # Ignores top/bottom HUDs while catching localized enemy hit-flashes & hit-sparks!
        play_c = coarse[5:35, 5:55]
        play_g = gray_c[5:35, 5:55]
        blk_gray = play_g.reshape(6, 5, 10, 5).mean(axis=(1, 3))
        red_c = play_c[:, :, 0] - ((play_c[:, :, 1] + play_c[:, :, 2]) >> 1)
        blk_red = red_c.reshape(6, 5, 10, 5).mean(axis=(1, 3))

        # 2x-strided center ROI for 2D shake/scroll alignment
        roi = frame_rgb[28:132:2, 32:208:2]
        gray_roi = (
            roi[:, :, 0].astype(np.int16) * 77
            + roi[:, :, 1].astype(np.int16) * 150
            + roi[:, :, 2].astype(np.int16) * 29
        ) >> 8
        col_prof = gray_roi.mean(axis=0)
        row_prof = gray_roi.mean(axis=1)

        if self._prev_gray_roi is None or self._prev_rgb_coarse is None or self._prev_blk_gray is None:
            self._prev_gray_roi = gray_roi
            self._prev_col_prof = col_prof
            self._prev_row_prof = row_prof
            self._prev_rgb_coarse = coarse
            self._prev_blk_gray = blk_gray
            self._prev_blk_red = blk_red
            self._prev_luma_mean = luma_mean
            return out

        prev_c_gray = (
            self._prev_rgb_coarse[:, :, 0] * 77
            + self._prev_rgb_coarse[:, :, 1] * 150
            + self._prev_rgb_coarse[:, :, 2] * 29
        ) >> 8
        raw_changed = float(np.mean(np.abs(gray_c - prev_c_gray) > 18))
        bright_delta = luma_mean - self._prev_luma_mean

        rgb_delta = coarse - self._prev_rgb_coarse
        red_excess = float(
            np.mean(np.maximum(0, rgb_delta[:, :, 0] - ((rgb_delta[:, :, 1] + rgb_delta[:, :, 2]) >> 1)))
        )

        # Localized block hit-flash / hit-spark peak
        local_flash = float(np.max(np.abs(blk_gray - self._prev_blk_gray)))
        local_red = float(np.max(np.maximum(0.0, blk_red - self._prev_blk_red)))

        out["raw_changed"] = raw_changed
        out["local_flash"] = local_flash
        out["local_red"] = local_red
        out["is_menu_static"] = (raw_changed < 0.042 and local_flash < 18.0)

        if raw_changed > 0.76 or (self._prev_luma_mean < 12.0 and luma_mean > 28.0):
            self._scene_cut_cooldown = 6
            self._magic_streak = 0
            out["is_scene_cut"] = True

        if self._scene_cut_cooldown > 0:
            self._scene_cut_cooldown -= 1
            self._prev_gray_roi = gray_roi
            self._prev_col_prof = col_prof
            self._prev_row_prof = row_prof
            self._prev_rgb_coarse = coarse
            self._prev_blk_gray = blk_gray
            self._prev_blk_red = blk_red
            self._prev_luma_mean = luma_mean
            return out

        pad = 3
        sad0 = float(np.abs(gray_roi[pad:-pad, pad:-pad] - self._prev_gray_roi[pad:-pad, pad:-pad]).mean())
        best_dx, best_dy = 0, 0
        true_changed = raw_changed

        if sad0 > 1.8 and luma_std > 10.0 and raw_changed < 0.72:
            cur_c = col_prof[pad:-pad]
            best_err_x = 1e9
            for dx in range(-pad, pad + 1):
                err = float(np.abs(cur_c - self._prev_col_prof[pad - dx:len(col_prof) - pad - dx]).mean())
                if err < best_err_x:
                    best_err_x = err
                    best_dx = dx

            cur_r = row_prof[pad:-pad]
            best_err_y = 1e9
            for dy in range(-pad, pad + 1):
                err = float(np.abs(cur_r - self._prev_row_prof[pad - dy:len(row_prof) - pad - dy]).mean())
                if err < best_err_y:
                    best_err_y = err
                    best_dy = dy

            shifted_prev = self._prev_gray_roi[
                pad - best_dy:gray_roi.shape[0] - pad - best_dy,
                pad - best_dx:gray_roi.shape[1] - pad - best_dx,
            ]
            comp_diff = np.abs(gray_roi[pad:-pad, pad:-pad] - shifted_prev)
            sad_shift = float(comp_diff.mean())
            true_changed = float(np.mean(comp_diff > 20))

            if (best_dx != 0 or best_dy != 0) and sad_shift < sad0 * 0.65:
                self._shift_hist.append((best_dx, best_dy))
                reversing = any(
                    (px != 0 and (px > 0) != (best_dx > 0)) or (py != 0 and (py > 0) != (best_dy > 0))
                    for px, py in list(self._shift_hist)[:-1]
                )
                mag = abs(best_dx) + abs(best_dy)
                if reversing or mag >= 2:
                    out["shake"] = min(1.0, mag / 3.0)
            else:
                self._shift_hist.append((0, 0))
        else:
            self._shift_hist.append((0, 0))

        out["true_changed"] = true_changed

        if abs(bright_delta) >= 9.0 and true_changed >= 0.03:
            out["hit_flash"] = min(1.0, abs(bright_delta) / 28.0)
        if red_excess >= 6.5 and true_changed >= 0.025:
            out["red_flash"] = min(1.0, red_excess / 18.0)

        if (0.085 <= true_changed <= 0.68) or out["hit_flash"] > 0.35:
            self._magic_streak = min(20, self._magic_streak + 1)
        else:
            self._magic_streak = max(0, self._magic_streak - 2)

        if self._magic_streak >= 3:
            out["magic_visual"] = min(1.0, (true_changed * 1.5) + (out["hit_flash"] * 0.6))

        if 0.045 <= true_changed <= 0.55 and (bright_delta > 2.5 or local_flash >= 26.0):
            out["item_visual"] = min(1.0, max(true_changed * 2.2, local_flash / 45.0))

        self._prev_gray_roi = gray_roi
        self._prev_col_prof = col_prof
        self._prev_row_prof = row_prof
        self._prev_rgb_coarse = coarse
        self._prev_blk_gray = blk_gray
        self._prev_blk_red = blk_red
        self._prev_luma_mean = luma_mean
        return out

    # =========================================================================
    # FAST CACHED-FFT AUDIO ANALYZER WITH SPECTRAL FLUX
    # =========================================================================
    def _get_fft_plan(self, n: int, sample_rate: int):
        key = (n, sample_rate)
        plan = self._fft_cache.get(key)
        if plan is None:
            win = np.hanning(n).astype(np.float32)
            freqs = np.fft.rfftfreq(n, d=1.0 / float(max(8000, sample_rate)))
            low_m = (freqs >= 45.0) & (freqs < 320.0)
            mid_m = (freqs >= 320.0) & (freqs < 1500.0)
            high_m = (freqs >= 1500.0) & (freqs < 8000.0)
            broad_m = (freqs >= 180.0) & (freqs < 4200.0)
            flux_m = (freqs >= 140.0) & (freqs < 3600.0)
            plan = (win, low_m, mid_m, high_m, broad_m, flux_m)
            self._fft_cache[key] = plan
        return plan

    def _analyze_audio(self, stereo_audio: np.ndarray | None, sample_rate: int, frame_num: int) -> dict:
        out = {
            "low_excess": 0.0,
            "mid_excess": 0.0,
            "flux_score": 0.0,
            "noise_ratio": 0.0,
            "crest": 0.0,
            "is_menu_beep": False,
            "is_bgm_beat": False,
            "impact_score": 0.0,
            "chime_score": 0.0,
        }
        if stereo_audio is None or len(stereo_audio) < 64:
            return out

        mono = (stereo_audio[:, 0].astype(np.float32) + stereo_audio[:, 1].astype(np.float32)) * (0.5 / 32768.0)
        n = len(mono)
        rms = float(np.sqrt(np.mean(mono * mono)))
        if rms < 0.004:
            self._low_base *= 0.94
            self._mid_base *= 0.94
            self._high_base *= 0.94
            if self._mag_ema is not None:
                self._mag_ema *= 0.92
            return out

        peak = float(np.max(np.abs(mono)))
        crest = peak / (rms + 1e-6)
        out["crest"] = crest

        win, low_m, mid_m, high_m, broad_m, flux_m = self._get_fft_plan(n, sample_rate)
        mag = np.abs(np.fft.rfft(mono * win)) / float(max(1, n // 2))

        if self._mag_ema is None or len(self._mag_ema) != len(mag):
            self._mag_ema = mag.copy()
            flux_score = 0.0
        else:
            pos_flux = np.maximum(0.0, mag[flux_m] - (1.35 * self._mag_ema[flux_m]))
            flux_score = float(np.sum(pos_flux)) / (float(np.sum(self._mag_ema[flux_m])) + 0.08)
            # Asymmetric spectral envelope tracking
            up = mag > self._mag_ema
            self._mag_ema[up] = (0.92 * self._mag_ema[up]) + (0.08 * mag[up])
            self._mag_ema[~up] = (0.86 * self._mag_ema[~up]) + (0.14 * mag[~up])
        out["flux_score"] = min(1.5, flux_score)

        low_e = float(np.sqrt(np.mean(mag[low_m] ** 2))) if np.any(low_m) else 0.0
        mid_e = float(np.sqrt(np.mean(mag[mid_m] ** 2))) if np.any(mid_m) else 0.0
        high_e = float(np.sqrt(np.mean(mag[high_m] ** 2))) if np.any(high_m) else 0.0

        b_mag = mag[broad_m]
        if len(b_mag) > 0:
            b_sq = b_mag * b_mag
            noise_ratio = 1.0 - (float(np.max(b_sq)) / (float(np.sum(b_sq)) + 1e-9))
        else:
            noise_ratio = 0.0
        out["noise_ratio"] = noise_ratio

        a_low = 0.05 if low_e > self._low_base else 0.09
        a_mid = 0.05 if mid_e > self._mid_base else 0.09
        a_high = 0.05 if high_e > self._high_base else 0.10

        low_excess = max(0.0, low_e - (1.38 * self._low_base)) / (self._low_base + 0.008)
        mid_excess = max(0.0, mid_e - (1.42 * self._mid_base)) / (self._mid_base + 0.008)
        high_excess = max(0.0, high_e - (1.40 * self._high_base)) / (self._high_base + 0.006)

        self._low_base = (1.0 - a_low) * self._low_base + a_low * low_e
        self._mid_base = (1.0 - a_mid) * self._mid_base + a_mid * mid_e
        self._high_base = (1.0 - a_high) * self._high_base + a_high * high_e

        out["low_excess"] = low_excess
        out["mid_excess"] = mid_excess

        if low_excess > 0.50 and (frame_num - self._last_low_peak_f) >= 6:
            self._low_peak_frames.append(frame_num)
            self._last_low_peak_f = frame_num
            if len(self._low_peak_frames) >= 4:
                diffs = np.diff(list(self._low_peak_frames))
                if 10 <= diffs[-1] <= 48 and abs(int(diffs[-1]) - int(diffs[-2])) <= 2 and abs(int(diffs[-2]) - int(diffs[-3])) <= 2:
                    out["is_bgm_beat"] = True

        if (high_e > (low_e * 2.2 + mid_e * 0.85) and noise_ratio < 0.72) or (low_e < 0.005 and mid_e < 0.012 and high_excess > 0.2):
            out["is_menu_beep"] = True

        if not out["is_menu_beep"]:
            broad_bonus = 1.25 if noise_ratio >= 0.72 else (0.80 if crest >= 3.2 else 0.45)
            raw_imp = ((low_excess * 0.50) + (mid_excess * 0.35) + (flux_score * 0.85)) * broad_bonus
            out["impact_score"] = min(1.0, raw_imp / 1.8)

        if not out["is_menu_beep"] and mid_excess > 0.70 and high_e >= low_e * 0.5 and noise_ratio < 0.76:
            out["chime_score"] = min(1.0, mid_excess / 2.2)

        return out

    # =========================================================================
    # MULTI-MODAL FUSION + FULL-DURATION HOLD ENVELOPE
    # =========================================================================
    def process_frame(
        self,
        frame_rgb: np.ndarray | None,
        stereo_audio: np.ndarray | None,
        sample_rate: int,
        btn_mask: int,
        frame_num: int,
        system_ram_u32: np.ndarray | None = None,
    ) -> tuple[float, float, int] | None:
        vis = self._analyze_video(frame_rgb)
        if vis["is_scene_cut"]:
            self.profile_store.reset_runtime_state()
            return self._step_hold(0.0, 0.0, 0)

        sfx_hit = self.profile_store.detect_triggered_sfx(system_ram_u32)

        action_held = bool(btn_mask & 0x0F)
        dpad_held = bool(btn_mask & 0xF0)
        if action_held:
            self._last_action_frame = frame_num
        recent_action = (frame_num - self._last_action_frame) <= 18

        if dpad_held and vis["is_menu_static"]:
            self._dpad_menu_streak = min(30, self._dpad_menu_streak + 1)
        elif not vis["is_menu_static"] and vis["true_changed"] > 0.06:
            self._dpad_menu_streak = max(0, self._dpad_menu_streak - 2)

        in_menu_context = (vis["is_menu_static"] and not vis["shake"]) or (self._dpad_menu_streak >= 3 and vis["true_changed"] < 0.05)

        tgt_low = 0.0
        tgt_high = 0.0
        tgt_dur = 210

        if sfx_hit is not None:
            s_low, s_high, s_dur = sfx_hit
            tgt_low = max(tgt_low, s_low)
            tgt_high = max(tgt_high, s_high)
            tgt_dur = max(tgt_dur, s_dur)

        # 1. Screen Shake
        if vis["shake"] > 0.0:
            s = vis["shake"]
            tgt_low = max(tgt_low, 0.85 + 0.15 * s)
            tgt_high = max(tgt_high, 0.75 + 0.25 * s)
            tgt_dur = max(tgt_dur, 250)

        # 2. Universal Audio + Localized Sprite-Flash Combat Detector
        aud = self._analyze_audio(stereo_audio, sample_rate, frame_num)
        flash_val = max(vis["hit_flash"], vis["red_flash"])
        local_flash = vis["local_flash"]
        local_red = vis["local_red"]

        if not in_menu_context and not aud["is_menu_beep"]:
            # A) Player Attack Hit: recent A/B/L/R + (localized sprite flash/spark OR strong audio spectral flux)
            if recent_action and (
                (local_flash >= 22.0 and aud["flux_score"] >= 0.18)
                or (aud["flux_score"] >= 0.42 and aud["noise_ratio"] >= 0.68 and not aud["is_bgm_beat"])
            ):
                hit_str = min(1.0, 0.85 + (local_flash / 120.0) + (aud["impact_score"] * 0.25))
                tgt_low = max(tgt_low, hit_str * 0.95)
                tgt_high = max(tgt_high, hit_str)
                tgt_dur = max(tgt_dur, 210)

            # B) Taking Damage / Screen-Wide or Localized Hit Flash
            elif (flash_val > 0.25 or local_red >= 19.0 or (local_flash >= 34.0 and aud["noise_ratio"] >= 0.70)) and (
                aud["impact_score"] > 0.34 or aud["flux_score"] > 0.36 or vis["red_flash"] > 0.30
            ):
                dmg_str = min(1.0, 0.88 + max(flash_val, local_red / 45.0) * 0.25)
                tgt_low = max(tgt_low, dmg_str)
                tgt_high = max(tgt_high, dmg_str * 0.92)
                tgt_dur = max(tgt_dur, 260)

            # C) Heavy Non-Action Impact Burst (Explosions / Boss Slams)
            elif aud["impact_score"] > 0.48 and not aud["is_bgm_beat"] and aud["noise_ratio"] >= 0.74 and vis["true_changed"] >= 0.055:
                imp = aud["impact_score"]
                tgt_low = max(tgt_low, min(1.0, 0.80 + 0.20 * imp))
                tgt_high = max(tgt_high, min(1.0, 0.80 + 0.20 * imp))
                tgt_dur = max(tgt_dur, 230)

        if self.profile != "impacts_only":
            # 3. Magic / Special Skills
            if vis["magic_visual"] > 0.22 and not aud["is_menu_beep"] and (aud["mid_excess"] > 0.40 or aud["low_excess"] > 0.40 or aud["flux_score"] >= 0.35):
                mag_boost = 1.15 if self.profile == "rpg_magic" else 1.0
                m = min(1.0, vis["magic_visual"] * mag_boost)
                tgt_low = max(tgt_low, min(1.0, 0.75 + m * 0.25))
                tgt_high = max(tgt_high, min(1.0, 0.82 + m * 0.18))
                tgt_dur = max(tgt_dur, 270)

            # 4. Receiving Items / Chests
            now = time.monotonic()
            if (
                not in_menu_context
                and self._dpad_menu_streak == 0
                and vis["item_visual"] > 0.14
                and aud["chime_score"] > 0.42
                and (now - self._last_item_ts) >= 0.80
            ):
                self._last_item_ts = now
                itm = min(1.0, (aud["chime_score"] * 0.65 + vis["item_visual"] * 0.35))
                tgt_low = max(tgt_low, 0.72 + 0.25 * itm)
                tgt_high = max(tgt_high, 0.85 + 0.15 * itm)
                tgt_dur = max(tgt_dur, 210)

        if self.profile == "rpg_magic" and (tgt_low > 0.0 or tgt_high > 0.0):
            tgt_low = min(1.0, tgt_low * 1.15)
            tgt_high = min(1.0, tgt_high * 1.15)

        if tgt_low > 0.0 or tgt_high > 0.0:
            tgt_low = min(1.0, tgt_low * self.sensitivity)
            tgt_high = min(1.0, tgt_high * self.sensitivity)

        return self._step_hold(tgt_low, tgt_high, tgt_dur)

    def _step_hold(self, tgt_low: float, tgt_high: float, tgt_dur: int) -> tuple[float, float, int] | None:
        """
        Holds each motor trigger for its full `duration_ms` so Xbox/Bluetooth motors
        reach full spin-up strength (no premature 66ms decay cutoffs).
        """
        now = time.monotonic()
        if tgt_low <= 0.0 and tgt_high <= 0.0:
            if now >= self._hold_until:
                self._was_active = False
                self._active_low = 0.0
                self._active_high = 0.0
            return None

        new_power = tgt_low + tgt_high
        cur_power = self._active_low + self._active_high
        expired_or_renew = (now >= self._hold_until - 0.045)
        much_stronger = (new_power > cur_power + 0.16)

        if not self._was_active or expired_or_renew or much_stronger:
            self._active_low = tgt_low
            self._active_high = tgt_high
            self._hold_until = now + (float(tgt_dur) / 1000.0)
            self._was_active = True
            return (round(tgt_low, 3), round(tgt_high, 3), int(tgt_dur))

        return None
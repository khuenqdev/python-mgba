# rumble_sim.py
import time
from collections import deque
import numpy as np

from .rumble_profile import RumbleProfileStore


class SimulatedRumbleEngine:
    """
    Low-overhead (<0.20 ms/frame) multi-modal simulated rumble engine:
      1. Tier 1 (Profiled): RAM control-code/event rules + ROM SFX pointer tracking.
      2. Tier 2 (Universal Hardware + Audio + Video - works without any profile):
         - GBA PRAM (0x05000000) hardware hit-flash / damage palette-flash detector
         - 2D motion-compensated screen-shake & playfield hit-flash/spark detector
         - Stabilized multi-band FFT spectral flux & periodic BGM beat rejection
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
                f"[RUMBLE PROFILE] Loaded {len(self.profile_store._active_entries)} SFX rule(s) "
                f"& {len(self.profile_store._active_ram_events)} RAM event rule(s) "
                f"from {self.profile_store.profile_path}"
            )
        return loaded

    def has_rom_profile(self) -> bool:
        return self.profile_store.has_active_entries()

    def update_settings(self):
        self.mode = str(self.config.get("sensors", "sim_rumble_mode", "auto") or "auto").lower()
        if self.mode not in self.VALID_MODES:
            self.mode = "auto"

        self.use_rom_profile = bool(self.config.get("sensors", "sim_rumble_use_profile", True))
        self.disable_gfx_with_ram = bool(self.config.get("sensors", "sim_rumble_disable_gfx_with_ram", False))

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
        self._prev_gray_h = None
        self._prev_roi = None
        self._prev_col_prof = None
        self._prev_row_prof = None
        self._prev_red_h = None
        self._prev_luma_mean = 0.0
        self._prev_pram_u16 = None
        self._shift_hist = deque(maxlen=6)
        self._magic_streak = 0
        self._scene_cut_cooldown = 0

        self._low_base = 0.025
        self._mid_base = 0.025
        self._high_base = 0.025
        self._mag_ema = None
        self._peak_frames = deque(maxlen=10)
        self._last_peak_f = -100

        self._last_action_frame = -100
        self._last_item_ts = 0.0

        self._hold_until = 0.0
        self._active_low = 0.0
        self._active_high = 0.0
        self._was_active = False

        self.profile_store.reset_runtime_state()

    def _analyze_pram(self, pram_u16: np.ndarray | None) -> float:
        if pram_u16 is None or len(pram_u16) < 512:
            return 0.0

        if self._prev_pram_u16 is None:
            self._prev_pram_u16 = pram_u16.copy()
            return 0.0

        diff_mask = pram_u16 != self._prev_pram_u16
        changed_cnt = int(np.count_nonzero(diff_mask))
        if changed_cnt == 0:
            return 0.0

        if changed_cnt > 160:
            self._prev_pram_u16 = pram_u16.copy()
            return 0.0

        prev_w = self._prev_pram_u16.astype(np.int16)
        cur_w = pram_u16.astype(np.int16)

        pr = prev_w & 0x1F
        pg = (prev_w >> 5) & 0x1F
        pb = (prev_w >> 10) & 0x1F

        cr = cur_w & 0x1F
        cg = (cur_w >> 5) & 0x1F
        cb = (cur_w >> 10) & 0x1F

        dr = cr - pr
        dg = cg - pg
        db = cb - pb

        bright_flash_cnt = int(np.count_nonzero((dr >= 8) & (dg >= 8) & (db >= 8)))
        red_flash_cnt = int(np.count_nonzero((dr >= 8) & (dg <= 1) & (db <= 1)))
        obj_jump_cnt = int(np.count_nonzero((np.abs(dr[256:]) + np.abs(dg[256:]) + np.abs(db[256:])) >= 24))

        self._prev_pram_u16 = pram_u16.copy()

        score = 0.0
        if bright_flash_cnt >= 4:
            score = max(score, min(1.0, bright_flash_cnt / 14.0))
        if red_flash_cnt >= 3:
            score = max(score, min(1.0, red_flash_cnt / 10.0))
        if obj_jump_cnt >= 6:
            score = max(score, min(1.0, obj_jump_cnt / 16.0))
        return score

    def _analyze_video(self, frame_rgb: np.ndarray | None) -> dict:
        out = {
            "shake": 0.0,
            "hit_flash": 0.0,
            "red_flash": 0.0,
            "local_flash": 0.0,
            "spark_frac": 0.0,
            "true_changed": 0.0,
            "raw_changed": 0.0,
            "is_ui_static": True,
            "is_scene_cut": False,
            "magic_visual": 0.0,
            "item_visual": 0.0,
        }
        if frame_rgb is None or frame_rgb.ndim != 3:
            return out

        half = frame_rgb[::2, ::2].astype(np.int16)
        gray_h = (half[:, :, 0] * 77 + half[:, :, 1] * 150 + half[:, :, 2] * 29) >> 8
        red_h = half[:, :, 0] - ((half[:, :, 1] + half[:, :, 2]) >> 1)
        luma_mean = float(gray_h.mean())
        luma_std = float(gray_h.std())

        roi = gray_h[10:70, 10:110]
        red_roi = red_h[10:70, 10:110]
        col_prof = roi.mean(axis=0)
        row_prof = roi.mean(axis=1)

        if self._prev_gray_h is None or self._prev_roi is None or self._prev_red_h is None:
            self._prev_gray_h = gray_h
            self._prev_roi = roi
            self._prev_col_prof = col_prof
            self._prev_row_prof = row_prof
            self._prev_red_h = red_roi
            self._prev_luma_mean = luma_mean
            return out

        raw_diff = np.abs(gray_h - self._prev_gray_h)
        raw_changed = float(np.mean(raw_diff > 18))
        bright_delta = luma_mean - self._prev_luma_mean

        out["raw_changed"] = raw_changed

        if raw_changed > 0.76 or (self._prev_luma_mean < 12.0 and luma_mean > 28.0):
            self._scene_cut_cooldown = 6
            self._magic_streak = 0
            out["is_scene_cut"] = True

        if self._scene_cut_cooldown > 0:
            self._scene_cut_cooldown -= 1
            self._prev_gray_h = gray_h
            self._prev_roi = roi
            self._prev_col_prof = col_prof
            self._prev_row_prof = row_prof
            self._prev_red_h = red_roi
            self._prev_luma_mean = luma_mean
            return out

        pad = 3
        sad0 = float(np.abs(roi[pad:-pad, pad:-pad] - self._prev_roi[pad:-pad, pad:-pad]).mean())
        best_dx, best_dy = 0, 0
        comp_diff = np.abs(roi[pad:-pad, pad:-pad] - self._prev_roi[pad:-pad, pad:-pad])
        comp_red_jump = np.maximum(0, red_roi[pad:-pad, pad:-pad] - self._prev_red_h[pad:-pad, pad:-pad])
        true_changed = raw_changed

        if sad0 > 1.6 and luma_std > 10.0 and raw_changed < 0.72:
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

            shifted_prev = self._prev_roi[
                pad - best_dy:roi.shape[0] - pad - best_dy,
                pad - best_dx:roi.shape[1] - pad - best_dx,
            ]
            shifted_prev_red = self._prev_red_h[
                pad - best_dy:red_roi.shape[0] - pad - best_dy,
                pad - best_dx:red_roi.shape[1] - pad - best_dx,
            ]
            comp_diff = np.abs(roi[pad:-pad, pad:-pad] - shifted_prev)
            comp_red_jump = np.maximum(0, red_roi[pad:-pad, pad:-pad] - shifted_prev_red)
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

        blk_mean = comp_diff[2:52, 2:92].reshape(10, 5, 18, 5).mean(axis=(1, 3))
        local_flash = float(np.max(blk_mean))
        spark_frac = float(np.mean(comp_diff > 55))
        red_excess = float(np.mean(comp_red_jump))

        out["true_changed"] = true_changed
        out["local_flash"] = local_flash
        out["spark_frac"] = spark_frac
        out["is_ui_static"] = (raw_changed < 0.025 and spark_frac < 0.012)

        if abs(bright_delta) >= 9.0 and true_changed >= 0.04:
            out["hit_flash"] = min(1.0, abs(bright_delta) / 26.0)
        if red_excess >= 7.5 and true_changed >= 0.04:
            out["red_flash"] = min(1.0, red_excess / 18.0)

        if (0.09 <= true_changed <= 0.68) or out["hit_flash"] > 0.35:
            self._magic_streak = min(20, self._magic_streak + 1)
        else:
            self._magic_streak = max(0, self._magic_streak - 2)

        if self._magic_streak >= 3:
            out["magic_visual"] = min(1.0, (true_changed * 1.5) + (out["hit_flash"] * 0.6))

        if 0.05 <= true_changed <= 0.55 and (bright_delta > 3.0 or local_flash >= 30.0):
            out["item_visual"] = min(1.0, max(true_changed * 2.2, local_flash / 45.0))

        self._prev_gray_h = gray_h
        self._prev_roi = roi
        self._prev_col_prof = col_prof
        self._prev_row_prof = row_prof
        self._prev_red_h = red_roi
        self._prev_luma_mean = luma_mean
        return out

    def _get_fft_plan(self, n: int, sample_rate: int):
        key = (n, sample_rate)
        plan = self._fft_cache.get(key)
        if plan is None:
            win = np.hanning(n).astype(np.float32)
            freqs = np.fft.rfftfreq(n, d=1.0 / float(max(8000, sample_rate)))
            low_m = (freqs >= 45.0) & (freqs < 320.0)
            mid_m = (freqs >= 320.0) & (freqs < 1500.0)
            high_m = (freqs >= 1500.0) & (freqs < 8000.0)
            broad_m = (freqs >= 160.0) & (freqs < 4200.0)
            flux_m = (freqs >= 120.0) & (freqs < 3800.0)
            plan = (win, low_m, mid_m, high_m, broad_m, flux_m)
            self._fft_cache[key] = plan
        return plan

    def _analyze_audio(self, stereo_audio: np.ndarray | None, sample_rate: int, frame_num: int) -> dict:
        out = {
            "has_energy": False,
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
        if rms < 0.006:
            self._low_base = max(0.015, self._low_base * 0.96)
            self._mid_base = max(0.015, self._mid_base * 0.96)
            self._high_base = max(0.015, self._high_base * 0.96)
            if self._mag_ema is not None:
                self._mag_ema *= 0.95
            return out

        out["has_energy"] = rms >= 0.016
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
            denom = max(0.035, float(np.sum(self._mag_ema[flux_m])))
            flux_score = float(np.sum(pos_flux)) / denom
            up = mag > self._mag_ema
            self._mag_ema[up] = (0.88 * self._mag_ema[up]) + (0.12 * mag[up])
            self._mag_ema[~up] = (0.85 * self._mag_ema[~up]) + (0.15 * mag[~up])
        out["flux_score"] = min(2.0, flux_score)

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

        a_low = 0.06 if low_e > self._low_base else 0.09
        a_mid = 0.06 if mid_e > self._mid_base else 0.09
        a_high = 0.06 if high_e > self._high_base else 0.10

        low_excess = max(0.0, low_e - (1.42 * self._low_base)) / max(0.015, self._low_base)
        mid_excess = max(0.0, mid_e - (1.45 * self._mid_base)) / max(0.015, self._mid_base)
        high_excess = max(0.0, high_e - (1.42 * self._high_base)) / max(0.012, self._high_base)

        self._low_base = (1.0 - a_low) * self._low_base + a_low * low_e
        self._mid_base = (1.0 - a_mid) * self._mid_base + a_mid * mid_e
        self._high_base = (1.0 - a_high) * self._high_base + a_high * high_e

        out["low_excess"] = low_excess
        out["mid_excess"] = mid_excess

        if (low_excess > 0.45 or flux_score > 0.45) and (frame_num - self._last_peak_f) >= 6:
            self._peak_frames.append(frame_num)
            self._last_peak_f = frame_num
            if len(self._peak_frames) >= 4:
                diffs = np.diff(list(self._peak_frames))
                if 10 <= diffs[-1] <= 48 and abs(int(diffs[-1]) - int(diffs[-2])) <= 2 and abs(int(diffs[-2]) - int(diffs[-3])) <= 2:
                    out["is_bgm_beat"] = True

        if (high_e > (low_e * 2.4 + mid_e * 0.95) and noise_ratio < 0.65) or (low_e < 0.005 and mid_e < 0.012 and high_excess > 0.25):
            out["is_menu_beep"] = True

        if not out["is_menu_beep"] and flux_score >= 0.16:
            broad_bonus = 1.20 if noise_ratio >= 0.72 else (0.85 if crest >= 3.2 else 0.45)
            raw_imp = ((low_excess * 0.42) + (mid_excess * 0.35) + (flux_score * 0.70)) * broad_bonus
            out["impact_score"] = min(1.0, raw_imp / 1.55)

        if not out["is_menu_beep"] and mid_excess > 0.70 and flux_score >= 0.24 and high_e >= low_e * 0.5 and noise_ratio < 0.76:
            out["chime_score"] = min(1.0, mid_excess / 2.1)

        return out

    def process_frame(
        self,
        frame_rgb: np.ndarray | None,
        stereo_audio: np.ndarray | None,
        sample_rate: int,
        btn_mask: int,
        frame_num: int,
        system_ram_u32: np.ndarray | None = None,
        pram_u16: np.ndarray | None = None,
    ) -> tuple[float, float, int] | None:
        ram_only_mode = (
            self.disable_gfx_with_ram
            and self.use_rom_profile
            and self.profile_store.has_active_ram_events()
        )

        tgt_low = 0.0
        tgt_high = 0.0
        tgt_dur = 260
        reason = ""

        # Fast-path: When user disabled GFX/Audio sim in favor of RAM Action Profile
        if ram_only_mode:
            if system_ram_u32 is not None:
                ram_evt = self.profile_store.detect_triggered_ram_events(system_ram_u32.view(np.uint8))
                if ram_evt is not None:
                    tgt_low, tgt_high, tgt_dur, r_lbl = ram_evt
                    reason = f"RAM_EVT({r_lbl})"

                if self.profile_store._active_entries:
                    has_aud = bool(stereo_audio is not None and len(stereo_audio) >= 64 and int(np.max(np.abs(stereo_audio))) >= 600)
                    sfx_hit = self.profile_store.detect_triggered_sfx(system_ram_u32, has_audio_energy=has_aud)
                    if sfx_hit is not None:
                        s_low, s_high, s_dur = sfx_hit
                        tgt_low = max(tgt_low, s_low)
                        tgt_high = max(tgt_high, s_high)
                        tgt_dur = max(tgt_dur, s_dur)
                        reason = reason or "ROM_SFX"

            if tgt_low > 0.0 or tgt_high > 0.0:
                tgt_low = min(1.0, tgt_low * self.sensitivity)
                tgt_high = min(1.0, tgt_high * self.sensitivity)

            res = self._step_hold(tgt_low, tgt_high, tgt_dur)
            if res is not None:
                print(f"[SIM RUMBLE] {reason}: L={res[0]:.2f} H={res[1]:.2f} D={res[2]}ms (RAM/Profile-Only Mode)")
            return res

        # Standard Hybrid Mode: RAM Events + ROM SFX + PRAM + Video + Audio
        vis = self._analyze_video(frame_rgb)
        pram_flash = self._analyze_pram(pram_u16)

        if vis["is_scene_cut"]:
            self.profile_store.reset_runtime_state()
            return self._step_hold(0.0, 0.0, 0)

        aud = self._analyze_audio(stereo_audio, sample_rate, frame_num)

        ram_evt = None
        sfx_hit = None
        if self.use_rom_profile and system_ram_u32 is not None:
            ram_evt = self.profile_store.detect_triggered_ram_events(system_ram_u32.view(np.uint8))
            sfx_hit = self.profile_store.detect_triggered_sfx(
                system_ram_u32, has_audio_energy=aud["has_energy"]
            )

        action_held = bool(btn_mask & 0x0F)
        if action_held:
            self._last_action_frame = frame_num
        recent_action = (frame_num - self._last_action_frame) <= 18

        if ram_evt is not None:
            r_low, r_high, r_dur, r_lbl = ram_evt
            tgt_low = max(tgt_low, r_low)
            tgt_high = max(tgt_high, r_high)
            tgt_dur = max(tgt_dur, r_dur)
            reason = f"RAM_EVT({r_lbl})"

        if sfx_hit is not None:
            s_low, s_high, s_dur = sfx_hit
            tgt_low = max(tgt_low, s_low)
            tgt_high = max(tgt_high, s_high)
            tgt_dur = max(tgt_dur, s_dur)
            reason = reason or "ROM_SFX"

        if vis["shake"] > 0.0:
            s = vis["shake"]
            tgt_low = max(tgt_low, 0.85 + 0.15 * s)
            tgt_high = max(tgt_high, 0.78 + 0.22 * s)
            tgt_dur = max(tgt_dur, 380)
            reason = reason or "SHAKE"

        flash_val = max(vis["hit_flash"], vis["red_flash"])
        local_flash = vis["local_flash"]
        spark_frac = vis["spark_frac"]

        if not aud["is_menu_beep"] and aud["has_energy"]:
            if recent_action and (
                (pram_flash >= 0.25 and aud["flux_score"] >= 0.20)
                or (spark_frac >= 0.025 and local_flash >= 28.0 and aud["flux_score"] >= 0.26)
                or (aud["impact_score"] >= 0.44 and aud["flux_score"] >= 0.36 and not aud["is_bgm_beat"] and spark_frac >= 0.015 and not vis["is_ui_static"])
            ):
                hit_str = min(0.95, 0.68 + (pram_flash * 0.25) + (aud["impact_score"] * 0.22))
                tgt_low = max(tgt_low, hit_str * 0.90)
                tgt_high = max(tgt_high, hit_str)
                tgt_dur = max(tgt_dur, 260)
                reason = reason or "ATTACK_HIT"

            elif (
                (pram_flash >= 0.35 and (aud["flux_score"] >= 0.22 or spark_frac >= 0.015))
                or (flash_val >= 0.34 and spark_frac >= 0.032 and aud["flux_score"] >= 0.32)
                or (spark_frac >= 0.045 and local_flash >= 45.0 and aud["flux_score"] >= 0.35 and aud["noise_ratio"] >= 0.70 and not aud["is_bgm_beat"])
            ):
                dmg_str = min(1.0, 0.85 + max(pram_flash, flash_val) * 0.20)
                tgt_low = max(tgt_low, dmg_str)
                tgt_high = max(tgt_high, dmg_str * 0.95)
                tgt_dur = max(tgt_dur, 380)
                reason = reason or "DAMAGE"

            elif (
                aud["impact_score"] >= 0.56
                and aud["flux_score"] >= 0.44
                and not aud["is_bgm_beat"]
                and aud["noise_ratio"] >= 0.75
                and spark_frac >= 0.028
                and vis["true_changed"] >= 0.08
            ):
                imp = aud["impact_score"]
                tgt_low = max(tgt_low, min(1.0, 0.82 + 0.18 * imp))
                tgt_high = max(tgt_high, min(1.0, 0.82 + 0.18 * imp))
                tgt_dur = max(tgt_dur, 340)
                reason = reason or "IMPACT"

        if self.profile != "impacts_only" and not aud["is_menu_beep"] and not vis["is_ui_static"] and aud["has_energy"]:
            if vis["magic_visual"] > 0.28 and aud["flux_score"] >= 0.34 and not aud["is_bgm_beat"] and (aud["mid_excess"] > 0.45 or aud["low_excess"] > 0.45):
                mag_boost = 1.15 if self.profile == "rpg_magic" else 1.0
                m = min(1.0, vis["magic_visual"] * mag_boost)
                tgt_low = max(tgt_low, min(1.0, 0.78 + m * 0.22))
                tgt_high = max(tgt_high, min(1.0, 0.85 + m * 0.15))
                tgt_dur = max(tgt_dur, 360)
                reason = reason or "MAGIC"

            now = time.monotonic()
            if (
                vis["item_visual"] > 0.18
                and aud["chime_score"] > 0.45
                and (now - self._last_item_ts) >= 0.85
            ):
                self._last_item_ts = now
                itm = min(1.0, (aud["chime_score"] * 0.65 + vis["item_visual"] * 0.35))
                tgt_low = max(tgt_low, 0.72 + 0.25 * itm)
                tgt_high = max(tgt_high, 0.85 + 0.15 * itm)
                tgt_dur = max(tgt_dur, 280)
                reason = reason or "ITEM"

        if self.profile == "rpg_magic" and (tgt_low > 0.0 or tgt_high > 0.0):
            tgt_low = min(1.0, tgt_low * 1.15)
            tgt_high = min(1.0, tgt_high * 1.15)

        if tgt_low > 0.0 or tgt_high > 0.0:
            tgt_low = min(1.0, tgt_low * self.sensitivity)
            tgt_high = min(1.0, tgt_high * self.sensitivity)

        res = self._step_hold(tgt_low, tgt_high, tgt_dur)
        if res is not None:
            print(
                f"[SIM RUMBLE] {reason}: L={res[0]:.2f} H={res[1]:.2f} D={res[2]}ms "
                f"(pram={pram_flash:.2f}, flash={local_flash:.1f}, spark={spark_frac:.3f}, flux={aud['flux_score']:.2f}, imp={aud['impact_score']:.2f}, shake={vis['shake']:.2f})"
            )
        return res

    def _step_hold(self, tgt_low: float, tgt_high: float, tgt_dur: int) -> tuple[float, float, int] | None:
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
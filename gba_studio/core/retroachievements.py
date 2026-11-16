# retroachievements.py
import os
import json
import time
import hashlib
import threading
import urllib.request
import urllib.parse
import urllib.error

from PyQt6.QtCore import QObject, pyqtSignal

from .ra_triggers import GBAMemoryView, RATriggerEvaluator

RA_CONNECT_URL = "https://retroachievements.org/dorequest.php"
RA_BADGE_BASE = "https://media.retroachievements.org/Badge"
RA_MEDIA_BASE = "https://media.retroachievements.org"
RA_USER_AGENT = "gba_studio/1.0"


def compute_gba_md5(rom_path: str) -> str:
    """Computes standard RetroAchievements GBA MD5 hash of the raw ROM."""
    if not rom_path or not os.path.exists(rom_path):
        return ""
    h = hashlib.md5()
    with open(rom_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


class RetroAchievementItem:
    def __init__(self, raw: dict):
        self.ach_id = int(raw.get("ID", 0))
        self.title = str(raw.get("Title", "Achievement"))
        self.description = str(raw.get("Description", ""))
        self.points = int(raw.get("Points", 0) or 0)
        self.badge_name = str(raw.get("BadgeName", "00000"))
        self.mem_addr = str(raw.get("MemAddr", "") or "")
        self.flags = int(raw.get("Flags", 3) or 3)
        self.category = str(raw.get("Type") or "")

        self.unlocked = False
        self.unlocked_hardcore = False
        self.evaluator = RATriggerEvaluator(self.mem_addr)
        self.has_measured = False
        self.measured_val = 0
        self.measured_target = 0


class RetroAchievementsManager(QObject):
    """
    Handles RetroAchievements authentication (persisted in mgba_config.json),
    ROM hash lookup, achievement set fetching, badge caching, 60 FPS trigger
    evaluation, and server-side award submission.
    """

    session_updated = pyqtSignal(dict)
    achievement_unlocked = pyqtSignal(dict)
    progress_updated = pyqtSignal(list)
    badge_downloaded = pyqtSignal(str)
    login_tested = pyqtSignal(bool, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._lock = threading.Lock()

        self.active = False
        self.hardcore = False
        self.game_id = 0
        self.game_title = ""
        self.rom_md5 = ""
        self.user_score = 0
        self.achievements = []          # list[RetroAchievementItem]
        self._awarding_ids = set()
        self._last_ping_ts = 0.0
        self._eval_tick = 0
        self._last_progress_snap = {}

    def get_cache_dir(self) -> str:
        profiles_dir = self.config.get("paths", "profiles") or "profiles"
        d = os.path.join(profiles_dir, "ra_cache")
        os.makedirs(d, exist_ok=True)
        os.makedirs(os.path.join(d, "badges"), exist_ok=True)
        return d

    def get_badge_path(self, badge_name: str, locked: bool = False) -> str:
        suffix = "_lock" if locked else ""
        return os.path.join(self.get_cache_dir(), "badges", f"{badge_name}{suffix}.png")

    # =========================================================================
    # HTTP CLIENT HELPERS
    # =========================================================================
    def _dorequest(self, params: dict, method: str = "POST", timeout: float = 10.0) -> dict:
        encoded = urllib.parse.urlencode(params).encode("utf-8")
        headers = {"User-Agent": RA_USER_AGENT}
        if method.upper() == "GET":
            url = f"{RA_CONNECT_URL}?{encoded.decode('utf-8')}"
            req = urllib.request.Request(url, headers=headers)
        else:
            req = urllib.request.Request(RA_CONNECT_URL, data=encoded, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)

    def _authenticate_sync(self, username: str, password: str, token: str) -> tuple[bool, str, str, int]:
        """Returns (ok, token, message, score)."""
        if not username:
            return False, "", "RetroAchievements username is empty.", 0

        params = {"r": "login2", "u": username}
        if token:
            params["t"] = token
        elif password:
            params["p"] = password
        else:
            return False, "", "Enter a password or Connect Token.", 0

        try:
            data = self._dorequest(params, method="POST", timeout=10.0)
        except Exception as e:
            return False, "", f"Network error: {e}", 0

        if not data.get("Success"):
            # Retry with password if saved token expired
            if token and password:
                try:
                    data = self._dorequest({"r": "login2", "u": username, "p": password}, method="POST", timeout=10.0)
                except Exception as e:
                    return False, "", f"Network error: {e}", 0

        if data.get("Success"):
            new_token = str(data.get("Token") or token or "")
            score = int(data.get("Score", 0) or 0)
            self.config.set("credentials", "ra_username", username)
            if password:
                self.config.set("credentials", "ra_password", password)
            if new_token:
                self.config.set("credentials", "ra_token", new_token)
            self.user_score = score
            return True, new_token, f"Logged in as {data.get('User', username)} ({score:,} pts)", score

        return False, "", str(data.get("Error") or "Invalid RetroAchievements credentials."), 0

    def test_login_async(self, username: str, password: str, token: str):
        def _run():
            ok, _, msg, _ = self._authenticate_sync(username.strip(), password, token.strip())
            self.login_tested.emit(ok, msg)

        threading.Thread(target=_run, daemon=True).start()

    # =========================================================================
    # SESSION LIFECYCLE
    # =========================================================================
    def stop_session(self):
        with self._lock:
            self.active = False
            self.game_id = 0
            self.game_title = ""
            self.rom_md5 = ""
            self.achievements = []
            self._awarding_ids.clear()
            self._last_progress_snap.clear()

        self.session_updated.emit({
            "status": "OFFLINE",
            "game_id": 0,
            "game_title": "",
            "user": str(self.config.get("credentials", "ra_username", "") or ""),
            "score": self.user_score,
            "hardcore": bool(self.config.get("retroachievements", "hardcore", False)),
            "achievements": [],
            "message": "RetroAchievements idle.",
        })

    def start_game_session(self, rom_path: str, fallback_title: str = ""):
        self.stop_session()
        if not bool(self.config.get("retroachievements", "enabled", False)):
            self.session_updated.emit({
                "status": "DISABLED",
                "game_id": 0,
                "game_title": fallback_title,
                "user": str(self.config.get("credentials", "ra_username", "") or ""),
                "score": self.user_score,
                "hardcore": bool(self.config.get("retroachievements", "hardcore", False)),
                "achievements": [],
                "message": "RetroAchievements is disabled. Enable it in RetroAchievements → Account & Settings.",
            })
            return

        username = str(self.config.get("credentials", "ra_username", "") or "").strip()
        password = str(self.config.get("credentials", "ra_password", "") or "")
        token = str(self.config.get("credentials", "ra_token", "") or "").strip()
        hardcore = bool(self.config.get("retroachievements", "hardcore", False))

        if not username or (not password and not token):
            self.session_updated.emit({
                "status": "NO_AUTH",
                "game_id": 0,
                "game_title": fallback_title,
                "user": username,
                "score": 0,
                "hardcore": hardcore,
                "achievements": [],
                "message": "Not logged in. Configure RetroAchievements credentials in Account & Settings.",
            })
            return

        self.session_updated.emit({
            "status": "CONNECTING",
            "game_id": 0,
            "game_title": fallback_title,
            "user": username,
            "score": self.user_score,
            "hardcore": hardcore,
            "achievements": [],
            "message": "Hashing ROM & connecting to RetroAchievements...",
        })

        threading.Thread(
            target=self._connect_session_worker,
            args=(rom_path, fallback_title, username, password, token, hardcore),
            daemon=True,
        ).start()

    def _connect_session_worker(self, rom_path: str, fallback_title: str,
                                username: str, password: str, token: str, hardcore: bool):
        md5_hex = compute_gba_md5(rom_path)
        if not md5_hex:
            self._emit_error_session(username, hardcore, fallback_title, "Could not compute ROM MD5 hash.")
            return

        ok, token, auth_msg, score = self._authenticate_sync(username, password, token)
        if not ok:
            self._emit_error_session(username, hardcore, fallback_title, auth_msg)
            return

        try:
            gid_resp = self._dorequest({"r": "gameid", "m": md5_hex}, method="GET")
            game_id = int(gid_resp.get("GameID", 0) or 0)
        except Exception as e:
            self._emit_error_session(username, hardcore, fallback_title, f"Game ID lookup failed: {e}")
            return

        if game_id <= 0:
            self._emit_error_session(
                username, hardcore, fallback_title,
                f"ROM hash ({md5_hex[:10]}...) is not recognized in RetroAchievements."
            )
            return

        try:
            patch_resp = self._dorequest({"r": "patch", "u": username, "t": token, "g": game_id}, method="GET")
            if not patch_resp.get("Success"):
                self._emit_error_session(username, hardcore, fallback_title, str(patch_resp.get("Error") or "Patch fetch failed."))
                return
            patch_data = patch_resp.get("PatchData", {}) or {}
        except Exception as e:
            self._emit_error_session(username, hardcore, fallback_title, f"Failed to fetch achievement set: {e}")
            return

        unlocked_ids = set()
        hardcore_ids = set()
        try:
            ss_resp = self._dorequest({
                "r": "startsession",
                "u": username,
                "t": token,
                "g": game_id,
                "h": 1 if hardcore else 0,
                "m": md5_hex,
            }, method="POST")
            for k in ("Unlocks", "HardcoreUnlocks"):
                for aid in (ss_resp.get(k) or []):
                    hardcore_ids.add(int(aid))
                    unlocked_ids.add(int(aid))
            if not hardcore:
                for aid in (ss_resp.get("SoftcoreUnlocks") or []):
                    unlocked_ids.add(int(aid))
        except Exception as e:
            print(f"[RA WARNING] startsession failed: {e}")

        raw_achs = patch_data.get("Achievements", []) or []
        items = []
        for raw in raw_achs:
            # Flags 3 = Official Core active set
            if int(raw.get("Flags", 3) or 3) != 3:
                continue
            item = RetroAchievementItem(raw)
            if item.ach_id in hardcore_ids:
                item.unlocked = True
                item.unlocked_hardcore = True
            elif not hardcore and item.ach_id in unlocked_ids:
                item.unlocked = True
            items.append(item)

        ra_title = str(patch_data.get("Title") or fallback_title or f"Game #{game_id}")
        with self._lock:
            self.active = True
            self.hardcore = hardcore
            self.game_id = game_id
            self.game_title = ra_title
            self.rom_md5 = md5_hex
            self.achievements = items
            self._awarding_ids.clear()
            self._last_progress_snap.clear()
            self._last_ping_ts = time.monotonic()

        mode_label = "Hardcore" if hardcore else "Softcore"
        earned_cnt = sum(1 for a in items if a.unlocked)
        self.session_updated.emit({
            "status": "ACTIVE",
            "game_id": game_id,
            "game_title": ra_title,
            "user": username,
            "score": score,
            "hardcore": hardcore,
            "achievements": items,
            "message": f"Connected ({mode_label}): {earned_cnt}/{len(items)} Unlocked",
        })

        threading.Thread(
            target=self._download_badges_worker,
            args=([(a.badge_name, a.unlocked) for a in items],),
            daemon=True,
        ).start()

    def _emit_error_session(self, username: str, hardcore: bool, title: str, message: str):
        self.session_updated.emit({
            "status": "ERROR",
            "game_id": 0,
            "game_title": title,
            "user": username,
            "score": self.user_score,
            "hardcore": hardcore,
            "achievements": [],
            "message": message,
        })

    def _download_badges_worker(self, badge_specs: list):
        for badge_name, is_unlocked in badge_specs:
            if not badge_name:
                continue
            for locked_flag in (False, True):
                dest = self.get_badge_path(badge_name, locked=locked_flag)
                if os.path.exists(dest) and os.path.getsize(dest) > 0:
                    continue
                suffix = "_lock" if locked_flag else ""
                url = f"{RA_BADGE_BASE}/{badge_name}{suffix}.png"
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": RA_USER_AGENT})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        data = resp.read()
                    tmp = dest + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(data)
                    os.replace(tmp, dest)
                    if (locked_flag and not is_unlocked) or (not locked_flag and is_unlocked):
                        self.badge_downloaded.emit(badge_name)
                except Exception:
                    continue

    # =========================================================================
    # PER-FRAME TRIGGER EVALUATION
    # =========================================================================
    def evaluate_frame(self, ewram: bytes, iwram: bytes):
        if not self.active:
            return

        now = time.monotonic()
        if (now - self._last_ping_ts) >= 120.0:
            self._last_ping_ts = now
            threading.Thread(target=self._send_ping_worker, daemon=True).start()

        mem = GBAMemoryView(ewram=ewram, iwram=iwram)
        newly_unlocked = []
        progress_changes = []

        with self._lock:
            if not self.active:
                return
            self._eval_tick += 1
            emit_progress = (self._eval_tick % 15 == 0)

            for item in self.achievements:
                if item.unlocked or item.ach_id in self._awarding_ids:
                    continue

                fired = item.evaluator.evaluate_frame(mem)
                if item.evaluator.has_measured:
                    item.has_measured = True
                    item.measured_val = item.evaluator.measured_value
                    item.measured_target = item.evaluator.measured_target
                    if emit_progress:
                        snap = (item.measured_val, item.measured_target)
                        if self._last_progress_snap.get(item.ach_id) != snap:
                            self._last_progress_snap[item.ach_id] = snap
                            progress_changes.append((item.ach_id, item.measured_val, item.measured_target))

                if fired:
                    item.unlocked = True
                    item.unlocked_hardcore = self.hardcore
                    self._awarding_ids.add(item.ach_id)
                    newly_unlocked.append(item)

        if progress_changes:
            self.progress_updated.emit(progress_changes)

        for item in newly_unlocked:
            badge_path = self.get_badge_path(item.badge_name, locked=False)
            self.achievement_unlocked.emit({
                "id": item.ach_id,
                "title": item.title,
                "description": item.description,
                "points": item.points,
                "badge_name": item.badge_name,
                "badge_path": badge_path if os.path.exists(badge_path) else "",
                "hardcore": self.hardcore,
            })
            threading.Thread(target=self._award_achievement_worker, args=(item,), daemon=True).start()

    def _award_achievement_worker(self, item: RetroAchievementItem):
        username = str(self.config.get("credentials", "ra_username", "") or "").strip()
        token = str(self.config.get("credentials", "ra_token", "") or "").strip()
        if not username or not token:
            return

        h_flag = 1 if self.hardcore else 0
        sig_str = f"{item.ach_id}{username}{h_flag}"
        validation = hashlib.md5(sig_str.encode("utf-8")).hexdigest().lower()

        try:
            resp = self._dorequest({
                "r": "awardachievement",
                "u": username,
                "t": token,
                "a": item.ach_id,
                "h": h_flag,
                "m": self.rom_md5,
                "v": validation,
            }, method="POST", timeout=10.0)
            if resp.get("Success"):
                new_score = resp.get("Score")
                if new_score is not None:
                    self.user_score = int(new_score)
        except Exception as e:
            print(f"[RA AWARD WARNING] Failed submitting #{item.ach_id}: {e}")
        finally:
            with self._lock:
                self._awarding_ids.discard(item.ach_id)

    def _send_ping_worker(self):
        username = str(self.config.get("credentials", "ra_username", "") or "").strip()
        token = str(self.config.get("credentials", "ra_token", "") or "").strip()
        if not username or not token or self.game_id <= 0:
            return
        try:
            self._dorequest({
                "r": "ping",
                "u": username,
                "t": token,
                "g": self.game_id,
                "m": f"Playing {self.game_title}",
            }, method="POST", timeout=8.0)
        except Exception:
            pass
# session_recorder.py
import os
import json
from datetime import datetime

# Libretro GBA Button Mapping
ID_TO_BTN = {
    8: "A",
    0: "B",
    2: "SELECT",
    3: "START",
    4: "UP",
    5: "DOWN",
    6: "LEFT",
    7: "RIGHT",
    10: "L",
    11: "R"
}

BTN_TO_ID = {v: k for k, v in ID_TO_BTN.items()}

# Canonical column order for Piano-Roll TAS Editor
TAS_BUTTON_ORDER = ["UP", "DOWN", "LEFT", "RIGHT", "A", "B", "L", "R", "START", "SELECT"]


def buttons_to_tas_mask(buttons) -> int:
    mask = 0
    for idx, btn in enumerate(TAS_BUTTON_ORDER):
        if btn in buttons:
            mask |= (1 << idx)
    return mask


def tas_mask_to_buttons(mask: int) -> set:
    return {btn for idx, btn in enumerate(TAS_BUTTON_ORDER) if (mask & (1 << idx))}


class SessionRecorder:
    """Records gameplay inputs into AI-friendly JSON and replays/edits them deterministically."""

    def __init__(self):
        self.mode = "IDLE"  # "IDLE", "RECORDING", "PLAYBACK"
        self.rom_path = None
        self.start_timestamp = None

        # Recording state
        self.recorded_events = []
        self.last_buttons = None
        self.last_paused = None
        self.initial_state_filename = None

        # Playback / TAS state
        self.playback_timeline = {}
        self.playback_max_frame = 0
        self.current_playback_frame = 0
        self.active_playback_buttons = set()
        self.tas_overwrite_mode = False

    @property
    def is_recording(self) -> bool:
        return self.mode == "RECORDING"

    @property
    def is_playing(self) -> bool:
        return self.mode == "PLAYBACK"

    # =========================================================================
    # RECORDING PIPELINE
    # =========================================================================
    def start_recording(self, worker, rom_path: str, base_export_dir: str = "replays") -> str:
        """Captures an initial snapshot state and starts frame logging."""
        os.makedirs(base_export_dir, exist_ok=True)
        self.rom_path = rom_path
        self.recorded_events = []
        self.last_buttons = None
        self.last_paused = None
        self.start_timestamp = datetime.now()

        tag = self.start_timestamp.strftime("%Y%m%d_%H%M%S")
        rom_base = os.path.splitext(os.path.basename(rom_path))[0]

        self.initial_state_filename = f"{rom_base}_{tag}.state"
        full_state_path = os.path.join(base_export_dir, self.initial_state_filename)

        saved = worker.save_state(full_state_path)
        if not saved:
            self.mode = "IDLE"
            return None

        self.mode = "RECORDING"
        return full_state_path

    def record_frame(self, frame_num: int, active_buttons: list, is_paused: bool):
        """Called every frame tick from core_worker. Stores input deltas only."""
        if not self.is_recording:
            return

        sorted_btns = sorted(list(set(active_buttons)))

        if (sorted_btns != self.last_buttons) or (is_paused != self.last_paused):
            self.recorded_events.append({
                "f": frame_num,
                "btn": sorted_btns,
                "paused": is_paused
            })
            self.last_buttons = sorted_btns
            self.last_paused = is_paused

    def stop_recording(self, total_frames: int, base_export_dir: str = "replays") -> str:
        """Generates the AI-friendly structured JSON file."""
        if not self.is_recording:
            return None

        self.mode = "IDLE"
        tag = self.start_timestamp.strftime("%Y%m%d_%H%M%S")
        rom_base = os.path.splitext(os.path.basename(self.rom_path))[0]
        json_filename = f"{rom_base}_{tag}.replay.json"
        full_json_path = os.path.join(base_export_dir, json_filename)

        summary_actions = self._build_summary_actions(self.recorded_events, total_frames)

        payload = {
            "format": "gba_replay_v1",
            "metadata": {
                "game_title": rom_base,
                "rom_file": os.path.basename(self.rom_path),
                "recorded_at": self.start_timestamp.isoformat(),
                "fps": 59.7275,
                "total_frames": total_frames,
                "duration_seconds": round(total_frames / 59.7275, 2),
                "initial_state_file": self.initial_state_filename
            },
            "ai_documentation": {
                "description": "Deterministic GBA gameplay log. 'summary_actions' groups sustained user actions into semantic intervals for LLMs. 'sparse_timeline' contains frame-accurate input changes.",
                "button_legend": ["A", "B", "SELECT", "START", "UP", "DOWN", "LEFT", "RIGHT", "L", "R"]
            },
            "summary_actions": summary_actions,
            "sparse_timeline": self.recorded_events
        }

        with open(full_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return full_json_path

    @staticmethod
    def _build_summary_actions(events: list, total_frames: int) -> list:
        """Converts frame deltas into continuous intervals with semantic descriptions."""
        actions = []
        if not events:
            return actions

        for i, ev in enumerate(events):
            start = ev["f"]
            end = events[i + 1]["f"] - 1 if (i + 1 < len(events)) else max(start, total_frames - 1)
            btns = ev["btn"]
            is_paused = ev.get("paused", False)

            if is_paused:
                desc = "Emulator Paused"
            elif not btns:
                desc = "Idle / Neutral"
            else:
                desc = f"Hold [{', '.join(btns)}]"

            actions.append({
                "start_frame": start,
                "end_frame": end,
                "duration_frames": max(1, (end - start) + 1),
                "buttons": btns,
                "action": desc
            })
        return actions

    # =========================================================================
    # PLAYBACK PIPELINE
    # =========================================================================
    def load_replay(self, json_path: str, worker) -> bool:
        """Loads JSON, restores the frame 0 state snapshot, and sets up playback."""
        if not os.path.exists(json_path):
            return False

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("metadata", {})
        state_file = meta.get("initial_state_file")
        if not state_file:
            return False

        json_dir = os.path.dirname(json_path)
        full_state_path = os.path.join(json_dir, state_file)
        if not os.path.exists(full_state_path):
            full_state_path = os.path.join("replays", state_file)
            if not os.path.exists(full_state_path):
                return False

        if not worker.load_state(full_state_path):
            return False

        self.playback_timeline = {}
        for ev in data.get("sparse_timeline", []):
            self.playback_timeline[int(ev["f"])] = {
                "btn": set(ev["btn"]),
                "paused": ev.get("paused", False)
            }

        self.playback_max_frame = int(meta.get("total_frames", 0))
        self.current_playback_frame = 0
        self.active_playback_buttons = set()
        self.tas_overwrite_mode = False
        self.mode = "PLAYBACK"
        return True

    def step_playback(self, frame_num: int) -> tuple:
        """Returns (active_buttons_set, should_pause, is_finished)."""
        if not self.is_playing:
            return set(), False, True

        if frame_num in self.playback_timeline:
            ev = self.playback_timeline[frame_num]
            self.active_playback_buttons = set(ev["btn"])
            if ev.get("paused", False):
                return self.active_playback_buttons, True, False

        self.current_playback_frame = frame_num

        if frame_num >= self.playback_max_frame:
            self.mode = "IDLE"
            return set(), False, True

        return self.active_playback_buttons, False, False

    def stop_playback(self):
        self.mode = "IDLE"
        self.active_playback_buttons = set()
        self.tas_overwrite_mode = False

    # =========================================================================
    # PIANO-ROLL TAS TIMELINE HELPERS
    # =========================================================================
    def set_dense_tas_timeline(self, dense_masks: list, start_frame: int = 0, overwrite_mode: bool = False):
        """Loads a dense per-frame bitmask list into the active playback timeline."""
        self.playback_timeline = {}
        prev_mask = None
        for f_idx, mask in enumerate(dense_masks):
            if mask != prev_mask:
                self.playback_timeline[f_idx] = {
                    "btn": tas_mask_to_buttons(mask),
                    "paused": False,
                }
                prev_mask = mask

        self.playback_max_frame = len(dense_masks)
        self.current_playback_frame = max(0, int(start_frame))
        if 0 <= self.current_playback_frame < len(dense_masks):
            self.active_playback_buttons = tas_mask_to_buttons(dense_masks[self.current_playback_frame])
        else:
            self.active_playback_buttons = set()

        self.tas_overwrite_mode = bool(overwrite_mode)
        self.mode = "PLAYBACK"

    def set_frame_buttons(self, frame_num: int, buttons: set):
        """Updates a single frame during live TAS record-overwrite."""
        if frame_num < 0:
            return
        self.playback_timeline[frame_num] = {
            "btn": set(buttons),
            "paused": False,
        }
        self.active_playback_buttons = set(buttons)
        if frame_num >= self.playback_max_frame:
            self.playback_max_frame = frame_num + 1

    @staticmethod
    def load_dense_from_file(json_path: str) -> tuple:
        """
        Parses a .replay.json file into (dense_masks, full_state_path, metadata).
        Returns ([], None, {}) if file cannot be read.
        """
        if not json_path or not os.path.exists(json_path):
            return [], None, {}

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("metadata", {})
        sparse = sorted(data.get("sparse_timeline", []), key=lambda e: int(e.get("f", 0)))
        total_frames = int(meta.get("total_frames", 0))
        if sparse:
            total_frames = max(total_frames, int(sparse[-1].get("f", 0)) + 1)
        total_frames = max(1, total_frames)

        dense = [0] * total_frames
        sparse_map = {int(ev["f"]): buttons_to_tas_mask(ev.get("btn", [])) for ev in sparse}
        curr_mask = 0
        for f_idx in range(total_frames):
            if f_idx in sparse_map:
                curr_mask = sparse_map[f_idx]
            dense[f_idx] = curr_mask

        state_file = meta.get("initial_state_file")
        full_state_path = None
        if state_file:
            cand = os.path.join(os.path.dirname(json_path), state_file)
            if os.path.exists(cand):
                full_state_path = cand
            else:
                cand2 = os.path.join("replays", state_file)
                if os.path.exists(cand2):
                    full_state_path = cand2

        return dense, full_state_path, meta

    @classmethod
    def save_dense_to_file(
        cls,
        json_path: str,
        dense_masks: list,
        rom_path: str,
        initial_state_filename: str | None = None,
    ) -> str:
        """Converts dense TAS bitmask frames into deterministic gba_replay_v1 JSON."""
        os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
        total_frames = len(dense_masks)

        sparse_events = []
        prev_mask = None
        for f_idx, mask in enumerate(dense_masks):
            if mask != prev_mask:
                sparse_events.append({
                    "f": f_idx,
                    "btn": sorted(tas_mask_to_buttons(mask)),
                    "paused": False,
                })
                prev_mask = mask

        rom_base = os.path.splitext(os.path.basename(rom_path or "game"))[0]
        summary_actions = cls._build_summary_actions(sparse_events, total_frames)

        payload = {
            "format": "gba_replay_v1",
            "metadata": {
                "game_title": rom_base,
                "rom_file": os.path.basename(rom_path or ""),
                "recorded_at": datetime.now().isoformat(),
                "fps": 59.7275,
                "total_frames": total_frames,
                "duration_seconds": round(total_frames / 59.7275, 2),
                "initial_state_file": initial_state_filename or "",
            },
            "ai_documentation": {
                "description": "Deterministic GBA gameplay log. 'summary_actions' groups sustained user actions into semantic intervals for LLMs. 'sparse_timeline' contains frame-accurate input changes.",
                "button_legend": ["A", "B", "SELECT", "START", "UP", "DOWN", "LEFT", "RIGHT", "L", "R"],
            },
            "summary_actions": summary_actions,
            "sparse_timeline": sparse_events,
        }

        tmp = json_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, json_path)
        return json_path
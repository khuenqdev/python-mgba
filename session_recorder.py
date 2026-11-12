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


class SessionRecorder:
    """Records gameplay inputs into AI-friendly JSON and replays them deterministically."""

    def __init__(self):
        self.mode = "IDLE"  # "IDLE", "RECORDING", "PLAYBACK"
        self.rom_path = None
        self.start_timestamp = None

        # Recording state
        self.recorded_events = []
        self.last_buttons = None
        self.last_paused = None
        self.initial_state_filename = None

        # Playback state
        self.playback_timeline = {}
        self.playback_max_frame = 0
        self.current_playback_frame = 0
        self.active_playback_buttons = set()

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

        # 1. Save base snapshot state for deterministic replay
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

        summary_actions = self._build_summary_actions(total_frames)

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

    def _build_summary_actions(self, total_frames: int) -> list:
        """Converts frame deltas into continuous intervals with semantic descriptions."""
        actions = []
        if not self.recorded_events:
            return actions

        for i, ev in enumerate(self.recorded_events):
            start = ev["f"]
            end = self.recorded_events[i + 1]["f"] - 1 if (i + 1 < len(self.recorded_events)) else total_frames
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
            self.playback_timeline[ev["f"]] = {
                "btn": set(ev["btn"]),
                "paused": ev.get("paused", False)
            }

        self.playback_max_frame = meta.get("total_frames", 0)
        self.current_playback_frame = 0
        self.active_playback_buttons = set()
        self.mode = "PLAYBACK"
        return True

    def step_playback(self, frame_num: int) -> tuple:
        """Returns (active_buttons_set, should_pause, is_finished)."""
        if not self.is_playing:
            return set(), False, True

        if frame_num in self.playback_timeline:
            ev = self.playback_timeline[frame_num]
            self.active_playback_buttons = ev["btn"]
            if ev["paused"]:
                return self.active_playback_buttons, True, False

        self.current_playback_frame = frame_num

        if frame_num >= self.playback_max_frame:
            self.mode = "IDLE"
            return set(), False, True

        return self.active_playback_buttons, False, False

    def stop_playback(self):
        self.mode = "IDLE"
        self.active_playback_buttons = set()
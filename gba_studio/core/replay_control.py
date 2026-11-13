# core_worker.py
import os
import sys
import time
import math
import ctypes
import numpy as np

try:
    from PyQt6.QtCore import QThread, pyqtSignal, QRecursiveMutex as QMutexClass, QMutexLocker, QTimer
except ImportError:
    from PyQt6.QtCore import QThread, pyqtSignal, QMutex as QMutexClass, QMutexLocker, QTimer

from pdretro import Emulator, MemoryRegion
from .cheats import CheatEngine
from .audio_dsp import AudioDSP
from .session_recorder import SessionRecorder, ID_TO_BTN

AF_TO_LIBRETRO = {
    "a": 8,
    "b": 0,
    "l": 10,
    "r": 11
}

# =============================================================================
# Libretro Native Environment & Rumble C-Types Specifications
# =============================================================================
RETRO_ENVIRONMENT_SET_PIXEL_FORMAT = 1
RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY = 9
RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS = 11
RETRO_ENVIRONMENT_GET_VARIABLE = 15
RETRO_ENVIRONMENT_SET_VARIABLES = 16
RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE = 17
RETRO_ENVIRONMENT_GET_RUMBLE_INTERFACE = 23
RETRO_ENVIRONMENT_GET_LOG_INTERFACE = 27
RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY = 31

from .libretro_types import RetroVariable, RetroRumbleInterface

class ReplayControlMixin:
    def start_recording_session(self, replays_dir: str = "replays") -> str:
        state_path = self.recorder.start_recording(self, self.rom_path, replays_dir)
        if state_path:
            with QMutexLocker(self.mutex):
                self.replay_frame_counter = 0
        return state_path

    def stop_recording_session(self, replays_dir: str = "replays") -> str:
        with QMutexLocker(self.mutex):
            total_frames = self.replay_frame_counter
        return self.recorder.stop_recording(total_frames, replays_dir)

    def start_replay_session(self, json_path: str) -> bool:
        success = self.recorder.load_replay(json_path, self)
        if success:
            with QMutexLocker(self.mutex):
                self.replay_frame_counter = 0
        return success

    def stop_replay_session(self):
        with QMutexLocker(self.mutex):
            self.recorder.stop_playback()

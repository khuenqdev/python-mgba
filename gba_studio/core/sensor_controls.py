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

class SensorControlsMixin:
    def set_solar_level(self, level: int):
        level = max(0, min(10, int(level)))
        self.solar_level = level
        self.config.set("sensors", "solar_level", level)
        self.solar_changed.emit(self.solar_level)

    def step_solar_level(self, delta: int):
        new_val = max(0, min(10, self.solar_level + delta))
        if new_val != self.solar_level:
            self.set_solar_level(new_val)
            btn = 15 if delta > 0 else 14
            self.set_input(btn, True)
            QTimer.singleShot(100, lambda: self.set_input(btn, False))

    def set_analog_motion(self, x: float, y: float):
        self.tilt_x = max(-1.0, min(1.0, float(x)))
        self.tilt_y = max(-1.0, min(1.0, float(y)))
        angle = math.degrees(math.atan2(self.tilt_x, -self.tilt_y)) if (self.tilt_x != 0 or self.tilt_y != 0) else 0.0
        self.gyro_angle = angle
        self.tilt_changed.emit(self.tilt_x, self.tilt_y, self.gyro_angle)

    def set_gyro_angle(self, angle_degrees: float):
        self.gyro_angle = float(angle_degrees)
        rad = math.radians(self.gyro_angle)
        self.tilt_x = math.sin(rad)
        self.tilt_y = -math.cos(rad)
        self.tilt_changed.emit(self.tilt_x, self.tilt_y, self.gyro_angle)

    def reset_sensors(self):
        self.set_analog_motion(0.0, 0.0)


# controller.py
import os
import sys
import ctypes
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame

from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QComboBox, QTableWidget, 
                             QTableWidgetItem, QHeaderView, QMessageBox, QGroupBox)
from PyQt6.QtCore import Qt, QTimer

GBA_BUTTON_NAMES = [
    ("a", "A Button"),
    ("b", "B Button"),
    ("l", "L Shoulder"),
    ("r", "R Shoulder"),
    ("start", "Start"),
    ("select", "Select"),
    ("up", "D-Pad Up"),
    ("down", "D-Pad Down"),
    ("left", "D-Pad Left"),
    ("right", "D-Pad Right"),
    ("solar_brighten", "Brighten Solar Sensor (R3)"),
    ("solar_darken", "Darken Solar Sensor (L3)"),
    ("autofire_a", "Autofire A (Turbo)"),
    ("autofire_b", "Autofire B (Turbo)"),
    ("autofire_l", "Autofire L (Turbo)"),
    ("autofire_r", "Autofire R (Turbo)")
]

LIBRETRO_BTNS = {
    "b": 0, "a": 8, "select": 2, "start": 3,
    "up": 4, "down": 5, "left": 6, "right": 7,
    "l": 10, "r": 11,
    "solar_darken": 14,
    "solar_brighten": 15
}

AUTOFIRE_ACTIONS = ["autofire_a", "autofire_b", "autofire_l", "autofire_r"]

QT_KEY_MAP = {
    "a": Qt.Key.Key_A, "b": Qt.Key.Key_B, "c": Qt.Key.Key_C, "d": Qt.Key.Key_D,
    "e": Qt.Key.Key_E, "f": Qt.Key.Key_F, "g": Qt.Key.Key_G, "h": Qt.Key.Key_H,
    "i": Qt.Key.Key_I, "j": Qt.Key.Key_J, "k": Qt.Key.Key_K, "l": Qt.Key.Key_L,
    "m": Qt.Key.Key_M, "n": Qt.Key.Key_N, "o": Qt.Key.Key_O, "p": Qt.Key.Key_P,
    "q": Qt.Key.Key_Q, "r": Qt.Key.Key_R, "s": Qt.Key.Key_S, "t": Qt.Key.Key_T,
    "u": Qt.Key.Key_U, "v": Qt.Key.Key_V, "w": Qt.Key.Key_W, "x": Qt.Key.Key_X,
    "y": Qt.Key.Key_Y, "z": Qt.Key.Key_Z,
    "0": Qt.Key.Key_0, "1": Qt.Key.Key_1, "2": Qt.Key.Key_2, "3": Qt.Key.Key_3,
    "4": Qt.Key.Key_4, "5": Qt.Key.Key_5, "6": Qt.Key.Key_6, "7": Qt.Key.Key_7,
    "8": Qt.Key.Key_8, "9": Qt.Key.Key_9,
    "return": Qt.Key.Key_Return, "enter": Qt.Key.Key_Return,
    "tab": Qt.Key.Key_Tab, "space": Qt.Key.Key_Space,
    "backspace": Qt.Key.Key_Backspace, "escape": Qt.Key.Key_Escape,
    "up": Qt.Key.Key_Up, "down": Qt.Key.Key_Down,
    "left": Qt.Key.Key_Left, "right": Qt.Key.Key_Right,
    "shift": Qt.Key.Key_Shift, "ctrl": Qt.Key.Key_Control, "alt": Qt.Key.Key_Alt,
    "[": Qt.Key.Key_BracketLeft, "]": Qt.Key.Key_BracketRight,
    "-": Qt.Key.Key_Minus, "=": Qt.Key.Key_Equal
}

# =============================================================================
# Windows Native XInput Vibration Definitions (Explicit 64-bit Safe C-Types)
# =============================================================================
class XINPUT_VIBRATION(ctypes.Structure):
    _fields_ = [
        ("wLeftMotorSpeed", ctypes.c_ushort),
        ("wRightMotorSpeed", ctypes.c_ushort)
    ]


class ControllerManager:
    def __init__(self, config):
        self.config = config
        self.joysticks = {}
        self.initialized = False
        self._xinput_dll = None
        self._init_subsystem()
        self._init_xinput()

        self._rumble_stop_timer = QTimer()
        self._rumble_stop_timer.setSingleShot(True)
        self._rumble_stop_timer.timeout.connect(self.stop_rumble)

    def _init_xinput(self):
        """Loads and configures Windows XInput with explicit 64-bit argument typing."""
        if sys.platform.startswith("win"):
            for name in ["xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"]:
                try:
                    dll = ctypes.windll.LoadLibrary(name)
                    if hasattr(dll, "XInputSetState"):
                        dll.XInputSetState.argtypes = [ctypes.c_uint, ctypes.POINTER(XINPUT_VIBRATION)]
                        dll.XInputSetState.restype = ctypes.c_uint
                        self._xinput_dll = dll
                        print(f"[RUMBLE DEBUG] Loaded native Windows vibration driver: {name}")
                        break
                except Exception as e:
                    print(f"[RUMBLE DEBUG] Failed loading {name}: {e}")

    def _init_subsystem(self):
        try:
            if not pygame.get_init():
                pygame.init()
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            self.initialized = True
            devs = self.rescan_devices()
            print(f"[CONTROLLER DEBUG] Initialized SDL subsystem. Found {len(devs)} device(s): {devs}")
        except Exception as e:
            print(f"[CONTROLLER DEBUG] Initialization error: {e}")

    def rescan_devices(self):
        if not self.initialized: return []
        pygame.joystick.quit()
        pygame.joystick.init()
        self.joysticks.clear()
        
        count = pygame.joystick.get_count()
        devices = []
        for i in range(count):
            try:
                js = pygame.joystick.Joystick(i)
                js.init()
                self.joysticks[i] = js
                devices.append(f"{i}: {js.get_name()}")
            except Exception: pass
        return devices

    def trigger_rumble(self, low_freq: float = 0.8, high_freq: float = 0.8, duration_ms: int = 150):
        """Sends vibration signals to physical gamepad motors and logs diagnostic state."""
        if not self.config.get("sensors", "rumble_enabled"):
            return False

        if low_freq <= 0.0 and high_freq <= 0.0:
            self.stop_rumble()
            return True

        strength = float(self.config.get("sensors", "rumble_strength") or 1.0)
        low = max(0.0, min(1.0, low_freq * strength))
        high = max(0.0, min(1.0, high_freq * strength))

        vibrated = False
        xinput_slots_success = []

        # 1. Native Windows XInput (probe all 4 controller player slots)
        if self._xinput_dll:
            vib = XINPUT_VIBRATION()
            vib.wLeftMotorSpeed = int(low * 65535)
            vib.wRightMotorSpeed = int(high * 65535)
            for user_idx in range(4):
                try:
                    res = self._xinput_dll.XInputSetState(user_idx, ctypes.byref(vib))
                    if res == 0:  # ERROR_SUCCESS
                        vibrated = True
                        xinput_slots_success.append(user_idx)
                except Exception:
                    pass

            if vibrated:
                self._rumble_stop_timer.start(max(75, duration_ms))

        # 2. Pygame / SDL2 Fallback: Run only if XInput did not claim the device
        sdl_vibrated = []
        if not xinput_slots_success:
            for idx, js in self.joysticks.items():
                if hasattr(js, 'rumble'):
                    try:
                        if js.rumble(low, high, duration_ms):
                            vibrated = True
                            sdl_vibrated.append(f"{idx} ({js.get_name()})")
                    except Exception:
                        pass

        # Diagnostic Debug Logging
        if vibrated:
            status_parts = []
            if xinput_slots_success:
                status_parts.append(f"XInput slot(s) {xinput_slots_success}")
            if sdl_vibrated:
                status_parts.append(f"SDL device(s) {sdl_vibrated}")
            print(f"[RUMBLE HARDWARE] Motor active -> Low: {low:.2f}, High: {high:.2f}, Dur: {duration_ms}ms on {', '.join(status_parts)}")
        else:
            print(f"[RUMBLE HARDWARE WARNING] Trigger called (L:{low:.2f}, H:{high:.2f}) but no physical gamepad accepted vibration!")

        return vibrated

    def stop_rumble(self):
        if self._xinput_dll:
            vib = XINPUT_VIBRATION(0, 0)
            for user_idx in range(4):
                try:
                    self._xinput_dll.XInputSetState(user_idx, ctypes.byref(vib))
                except Exception:
                    pass

        for js in self.joysticks.values():
            if hasattr(js, 'stop_rumble'):
                try: js.stop_rumble()
                except Exception: pass

    def _read_input_element(self, js, mapped_input, deadzone):
        if not mapped_input: return False
        
        if str(mapped_input).startswith("button_"):
            try:
                btn_num = int(mapped_input.split("_")[1])
                if btn_num < js.get_numbuttons():
                    return bool(js.get_button(btn_num))
            except Exception: pass

        elif str(mapped_input).startswith("hat_"):
            try:
                parts = mapped_input.split("_")
                hat_num, direction = int(parts[1]), parts[2]
                if hat_num < js.get_numhats():
                    hx, hy = js.get_hat(hat_num)
                    if direction == "up": return (hy > 0)
                    elif direction == "down": return (hy < 0)
                    elif direction == "left": return (hx < 0)
                    elif direction == "right": return (hx > 0)
            except Exception: pass

        elif str(mapped_input).startswith("axis_"):
            try:
                parts = mapped_input.split("_")
                axis_num, sign = int(parts[1]), parts[2]
                if axis_num < js.get_numaxes():
                    val = js.get_axis(axis_num)
                    if sign == "+": return (val > deadzone)
                    elif sign == "-": return (val < -deadzone)
            except Exception: pass
        return False

    def poll_gamepad_events(self, worker):
        if not self.initialized or not worker: return
        pygame.event.pump()

        gamepad_cfg = self.config.get("input", "gamepad")
        if not gamepad_cfg or not gamepad_cfg.get("enabled", True): return

        device_idx = gamepad_cfg.get("device_index", 0)
        js = self.joysticks.get(device_idx)
        if not js: return

        deadzone = float(gamepad_cfg.get("deadzone", 0.2))

        # 1. Standard Buttons & Solar Hotkeys
        for gba_key, action_id in LIBRETRO_BTNS.items():
            mapped_input = gamepad_cfg.get(gba_key)
            is_pressed = self._read_input_element(js, mapped_input, deadzone)
            worker.set_input(action_id, is_pressed)

        # 2. Autofire (Turbo)
        for af_key in AUTOFIRE_ACTIONS:
            target_btn = af_key.replace("autofire_", "")
            mapped_input = gamepad_cfg.get(af_key)
            is_pressed = self._read_input_element(js, mapped_input, deadzone)
            worker.set_autofire(target_btn, is_pressed)

        # 3. Analog Sticks -> Motion / Gyro / Tilt Sensor Simulation
        num_axes = js.get_numaxes()
        if num_axes >= 2:
            lx = js.get_axis(0)
            ly = js.get_axis(1)
            raw_x = 0.0 if abs(lx) < deadzone else lx
            raw_y = 0.0 if abs(ly) < deadzone else ly

            if num_axes >= 4:
                rx = js.get_axis(2 if num_axes == 4 else 3)
                if abs(rx) >= deadzone:
                    raw_x = rx

            worker.set_analog_motion(raw_x, raw_y)


class KeyCaptureDialog(QDialog):
    def __init__(self, mode="keyboard", joystick=None, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.joystick = joystick
        self.captured_value = None
        
        self.setWindowTitle("Press Key or Button")
        self.resize(320, 130)
        layout = QVBoxLayout(self)

        msg = "Press any KEY on your keyboard..." if mode == "keyboard" else "Press any BUTTON on controller..."
        self.label = QLabel(msg)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(self.label)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

        if self.mode == "gamepad" and self.joystick:
            self.poll_timer = QTimer(self)
            self.poll_timer.timeout.connect(self._poll_controller)
            self.poll_timer.start(20)

    def keyPressEvent(self, event):
        if self.mode == "keyboard":
            if event.key() == Qt.Key.Key_Escape:
                self.reject()
                return

            SPECIAL_KEYS = {
                Qt.Key.Key_Up: "up",
                Qt.Key.Key_Down: "down",
                Qt.Key.Key_Left: "left",
                Qt.Key.Key_Right: "right",
                Qt.Key.Key_Return: "return",
                Qt.Key.Key_Enter: "return",
                Qt.Key.Key_Tab: "tab",
                Qt.Key.Key_Space: "space",
                Qt.Key.Key_Backspace: "backspace",
                Qt.Key.Key_Shift: "shift",
                Qt.Key.Key_Control: "ctrl",
                Qt.Key.Key_Alt: "alt",
                Qt.Key.Key_BracketLeft: "[",
                Qt.Key.Key_BracketRight: "]",
                Qt.Key.Key_Minus: "-",
                Qt.Key.Key_Equal: "="
            }

            if event.key() in SPECIAL_KEYS:
                self.captured_value = SPECIAL_KEYS[event.key()]
                self.accept()
                return

            key_name = event.text().lower()
            if key_name:
                self.captured_value = key_name
                self.accept()
        else:
            super().keyPressEvent(event)

    def _poll_controller(self):
        pygame.event.pump()
        js = self.joystick
        if not js: return

        for b in range(js.get_numbuttons()):
            if js.get_button(b):
                self.captured_value = f"button_{b}"
                self.poll_timer.stop()
                self.accept()
                return

        for h in range(js.get_numhats()):
            hx, hy = js.get_hat(h)
            if hy > 0: self.captured_value = f"hat_{h}_up"
            elif hy < 0: self.captured_value = f"hat_{h}_down"
            elif hx < 0: self.captured_value = f"hat_{h}_left"
            elif hx > 0: self.captured_value = f"hat_{h}_right"
            if self.captured_value:
                self.poll_timer.stop()
                self.accept()
                return


class ControllerConfigDialog(QDialog):
    def __init__(self, config, controller_manager, parent=None):
        super().__init__(parent)
        self.config = config
        self.cm = controller_manager
        self.setWindowTitle("Configure Controls & Motion Feedback")
        self.resize(560, 620)

        self._init_ui()
        self.load_current_mappings()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Target Device:"))
        self.device_combo = QComboBox()
        self.device_combo.addItem("Keyboard")
        
        devices = self.cm.rescan_devices()
        for dev in devices:
            self.device_combo.addItem(f"Gamepad: {dev}")

        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        top_layout.addWidget(self.device_combo)

        btn_rescan = QPushButton("Rescan Controllers")
        btn_rescan.clicked.connect(self.rescan_gamepads)
        top_layout.addWidget(btn_rescan)
        layout.addLayout(top_layout)

        vib_group = QGroupBox("Gamepad Rumble Motor")
        vib_layout = QHBoxLayout(vib_group)
        btn_test_rumble = QPushButton("⚡ Test Motor Vibration")
        btn_test_rumble.setStyleSheet("background-color: #E65100; color: white; font-weight: bold; padding: 4px;")
        btn_test_rumble.clicked.connect(self.test_rumble)
        vib_layout.addWidget(btn_test_rumble)
        layout.addWidget(vib_group)

        self.table = QTableWidget(len(GBA_BUTTON_NAMES), 3)
        self.table.setHorizontalHeaderLabels(["GBA Action / Sensor", "Assigned Binding", "Remap"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        bottom_layout = QHBoxLayout()
        btn_save = QPushButton("Save & Apply")
        btn_save.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 6px;")
        btn_save.clicked.connect(self.save_and_close)
        
        btn_close = QPushButton("Cancel")
        btn_close.clicked.connect(self.reject)
        
        bottom_layout.addStretch()
        bottom_layout.addWidget(btn_save)
        bottom_layout.addWidget(btn_close)
        layout.addLayout(bottom_layout)

    def test_rumble(self):
        print("\n[RUMBLE TEST] Triggering test vibration...")
        success = self.cm.trigger_rumble(low_freq=1.0, high_freq=1.0, duration_ms=400)
        
        # Resolve MainWindow instance through parent hierarchy
        parent_win = self.parent() or self.window()
        if hasattr(parent_win, 'on_manual_rumble_test'):
            parent_win.on_manual_rumble_test()

        if success:
            QMessageBox.information(self, "Rumble Test", "Vibration signal sent successfully to gamepad!")
        else:
            QMessageBox.warning(self, "Rumble Test", "No controller responded to the vibration test. Verify your controller is plugged in and supported.")

    def rescan_gamepads(self):
        current_text = self.device_combo.currentText()
        self.device_combo.clear()
        self.device_combo.addItem("Keyboard")
        devices = self.cm.rescan_devices()
        for dev in devices:
            self.device_combo.addItem(f"Gamepad: {dev}")
        idx = self.device_combo.findText(current_text)
        if idx >= 0: self.device_combo.setCurrentIndex(idx)
        QMessageBox.information(self, "Rescan", f"Found {len(devices)} game controller(s).")

    def on_device_changed(self):
        self.load_current_mappings()

    def load_current_mappings(self):
        is_kb = (self.device_combo.currentIndex() == 0)
        bindings = self.config.get("input", "keyboard" if is_kb else "gamepad") or {}

        for row, (gba_key, label_name) in enumerate(GBA_BUTTON_NAMES):
            self.table.setItem(row, 0, QTableWidgetItem(label_name))
            
            val = str(bindings.get(gba_key, "Unbound"))
            item_val = QTableWidgetItem(val)
            item_val.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, item_val)

            btn = QPushButton("Bind")
            btn.clicked.connect(lambda _, r=row, k=gba_key: self.trigger_rebind(r, k))
            self.table.setCellWidget(row, 2, btn)

    def trigger_rebind(self, row, gba_key):
        is_kb = (self.device_combo.currentIndex() == 0)
        js = None
        if not is_kb:
            dev_idx = max(0, self.device_combo.currentIndex() - 1)
            js = self.cm.joysticks.get(dev_idx)

        dlg = KeyCaptureDialog(mode="keyboard" if is_kb else "gamepad", joystick=js, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.captured_value:
            new_val = dlg.captured_value
            self.table.item(row, 1).setText(str(new_val))

    def save_and_close(self):
        is_kb = (self.device_combo.currentIndex() == 0)
        target_section = "keyboard" if is_kb else "gamepad"
        current_map = dict(self.config.get("input", target_section) or {})

        for row, (gba_key, _) in enumerate(GBA_BUTTON_NAMES):
            current_map[gba_key] = self.table.item(row, 1).text()

        if not is_kb:
            current_map["device_index"] = max(0, self.device_combo.currentIndex() - 1)
            current_map["enabled"] = True

        self.config.set("input", target_section, current_map)
        self.config.save()
        self.accept()
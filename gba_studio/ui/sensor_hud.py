# sensor_hud.py
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import QTimer, QSize


def format_playtime(total_seconds: float) -> str:
    sec = max(0, int(total_seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class SensorHudWidget(QFrame):
    """Dynamically responsive status bar with fluid multi-tier scaling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            SensorHudWidget {
                background-color: #12151D;
                border-top: 1px solid #232938;
                border-bottom: 1px solid #232938;
            }
            QLabel {
                font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                font-weight: bold;
            }
        """)

        self.game_title = "Unknown"
        self.game_code = "----"
        self.hw_type = "None"
        self.current_solar_level = 5
        self.rumble_active = False
        self.rumble_strength = 0.0
        self.current_tilt_x = 0.0
        self.current_tilt_y = 0.0
        self.current_gyro_angle = 0.0
        self.total_playtime_seconds = 0.0
        self.launch_count = 0

        self._rumble_off_timer = QTimer(self)
        self._rumble_off_timer.setSingleShot(True)
        self._rumble_off_timer.timeout.connect(self._clear_rumble)

        self.layout_box = QHBoxLayout(self)
        self.layout_box.setContentsMargins(6, 0, 6, 0)
        self.layout_box.setSpacing(6)

        self.lbl_game = QLabel()
        self.lbl_game.setStyleSheet("color: #90CAF9;")
        self.lbl_game.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_game.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_game, 3)

        self.lbl_solar = QLabel()
        self.lbl_solar.setStyleSheet("color: #FFD54F;")
        self.lbl_solar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_solar.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_solar, 3)

        self.lbl_rumble = QLabel()
        self.lbl_rumble.setStyleSheet("color: #78909C;")
        self.lbl_rumble.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_rumble.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_rumble, 2)

        self.lbl_gyro = QLabel()
        self.lbl_gyro.setStyleSheet("color: #4DD0E1;")
        self.lbl_gyro.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_gyro.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_gyro, 2)

        self.lbl_playtime = QLabel()
        self.lbl_playtime.setStyleSheet("color: #A5D6A7;")
        self.lbl_playtime.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_playtime.setMinimumWidth(0)
        self.layout_box.addWidget(self.lbl_playtime, 2)

        self.update_display()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def sizeHint(self) -> QSize:
        return QSize(240, 30)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_display()

    def update_display(self):
        w = self.width()

        type_str = f" ({self.hw_type.replace('_', ' ').title()})" if (w >= 920 and self.hw_type not in ["None", ""]) else ""
        if w < 380:
            self.lbl_game.setText(f"[{self.game_code}]")
        elif w < 650:
            short_t = self.game_title[:10] + ".." if len(self.game_title) > 10 else self.game_title
            self.lbl_game.setText(f"🎮 [{self.game_code}] {short_t}")
        else:
            self.lbl_game.setText(f"🎮 [{self.game_code}] {self.game_title}{type_str}")

        if w < 540:
            self.lbl_solar.setText(f"☀️ {self.current_solar_level}/10")
        else:
            sun_filled = "■" * self.current_solar_level
            sun_empty = "□" * (10 - self.current_solar_level)
            self.lbl_solar.setText(f"☀️ [{sun_filled}{sun_empty}] {self.current_solar_level}/10")

        if self.rumble_active:
            pct = int(self.rumble_strength * 100)
            filled = max(1, min(5, int(self.rumble_strength * 5)))
            bars = "■" * filled + "□" * (5 - filled)
            if w < 560:
                self.lbl_rumble.setText(f"📳 {pct}%")
            else:
                self.lbl_rumble.setText(f"📳 [{bars}] {pct}%")
            self.lbl_rumble.setStyleSheet("color: #FF5252;")
        else:
            if w < 560:
                self.lbl_rumble.setText("📳 OFF")
            else:
                self.lbl_rumble.setText("📳 [□□□□□] OFF")
            self.lbl_rumble.setStyleSheet("color: #78909C;")

        deg = int(self.current_gyro_angle) % 360
        if w < 560:
            self.lbl_gyro.setText(f"🎯 {deg}°")
        else:
            idx = max(0, min(6, int((self.current_tilt_x + 1.0) * 3.0)))
            meter = list("───●───")
            meter[idx] = "●"
            meter_str = "".join(meter)
            self.lbl_gyro.setText(f"🎯 [{meter_str}] {deg:3d}°")

        pt_str = format_playtime(self.total_playtime_seconds)
        if w < 440:
            self.lbl_playtime.setText(f"⏱ {pt_str}")
        elif self.launch_count > 0:
            self.lbl_playtime.setText(f"⏱ {pt_str} (#{self.launch_count})")
        else:
            self.lbl_playtime.setText(f"⏱ {pt_str}")

    def on_game_identified(self, title: str, code: str, hw_type: str):
        self.game_title = title
        self.game_code = code
        self.hw_type = hw_type
        self.update_display()

    def on_playtime_updated(self, total_seconds: float, launch_count: int = 0):
        self.total_playtime_seconds = max(0.0, float(total_seconds))
        self.launch_count = max(0, int(launch_count))
        self.update_display()

    def on_solar_updated(self, level: int):
        self.current_solar_level = level
        self.update_display()

    def on_rumble_triggered(self, low_freq: float, high_freq: float, duration_ms: int):
        if low_freq <= 0.0 and high_freq <= 0.0:
            self._clear_rumble()
            return

        self.rumble_active = True
        self.rumble_strength = max(low_freq, high_freq)
        self.update_display()
        self._rumble_off_timer.start(max(90, duration_ms))

    def _clear_rumble(self):
        self.rumble_active = False
        self.rumble_strength = 0.0
        self.update_display()

    def on_tilt_updated(self, x: float, y: float, angle: float):
        self.current_tilt_x = x
        self.current_tilt_y = y
        self.current_gyro_angle = angle
        self.update_display()

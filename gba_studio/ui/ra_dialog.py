# ra_dialog.py
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QGroupBox, QDialogButtonBox, QMessageBox
)
from PyQt6.QtCore import Qt


class RetroAchievementsSettingsDialog(QDialog):
    """Configures RetroAchievements & API credentials stored directly in mgba_config.json."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.mw = main_window
        self.config = main_window.config
        self.ra = main_window.ra_manager

        self.setWindowTitle("RetroAchievements & Credentials Settings")
        self.resize(480, 470)
        self._init_ui()
        self.ra.login_tested.connect(self._on_login_tested)

    def _init_ui(self):
        root = QVBoxLayout(self)

        note = QLabel(
            "All credentials are stored locally inside <code>mgba_config.json</code> "
            "(no environment variables required)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("background: #161A22; color: #B0BEC5; padding: 8px; border-radius: 5px;")
        root.addWidget(note)

        # 1. RetroAchievements Account
        ra_grp = QGroupBox("RetroAchievements Account")
        ra_lay = QVBoxLayout(ra_grp)

        r_user = QHBoxLayout()
        r_user.addWidget(QLabel("Username:"))
        self.ra_user_edit = QLineEdit(str(self.config.get("credentials", "ra_username", "") or ""))
        r_user.addWidget(self.ra_user_edit)
        ra_lay.addLayout(r_user)

        r_pass = QHBoxLayout()
        r_pass.addWidget(QLabel("Password:"))
        self.ra_pass_edit = QLineEdit(str(self.config.get("credentials", "ra_password", "") or ""))
        self.ra_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        r_pass.addWidget(self.ra_pass_edit)
        ra_lay.addLayout(r_pass)

        r_tok = QHBoxLayout()
        r_tok.addWidget(QLabel("Connect Token:"))
        self.ra_token_edit = QLineEdit(str(self.config.get("credentials", "ra_token", "") or ""))
        self.ra_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ra_token_edit.setPlaceholderText("(Auto-filled on login)")
        r_tok.addWidget(self.ra_token_edit)
        ra_lay.addLayout(r_tok)

        r_web = QHBoxLayout()
        r_web.addWidget(QLabel("Web API Key (Optional):"))
        self.ra_web_edit = QLineEdit(str(self.config.get("credentials", "ra_web_api_key", "") or ""))
        self.ra_web_edit.setEchoMode(QLineEdit.EchoMode.Password)
        r_web.addWidget(self.ra_web_edit)
        ra_lay.addLayout(r_web)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("🔑 Test Login & Save Token")
        self.btn_test.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 5px 10px;")
        self.btn_test.clicked.connect(self._test_login)
        test_row.addWidget(self.btn_test)

        self.login_status_lbl = QLabel("Ready.")
        self.login_status_lbl.setStyleSheet("color: #90A4AE; font-size: 11px;")
        test_row.addWidget(self.login_status_lbl, 1)
        ra_lay.addLayout(test_row)

        root.addWidget(ra_grp)

        # 2. RetroAchievements Behavior
        opt_grp = QGroupBox("RetroAchievements Options")
        opt_lay = QVBoxLayout(opt_grp)

        self.cb_enabled = QCheckBox("Enable RetroAchievements")
        self.cb_enabled.setChecked(bool(self.config.get("retroachievements", "enabled", False)))
        opt_lay.addWidget(self.cb_enabled)

        self.cb_hardcore = QCheckBox("Hardcore Mode (Disables Cheats & Save State Loading)")
        self.cb_hardcore.setChecked(bool(self.config.get("retroachievements", "hardcore", False)))
        opt_lay.addWidget(self.cb_hardcore)

        self.cb_panel = QCheckBox("Show Right-Side Achievements Panel")
        self.cb_panel.setChecked(bool(self.config.get("retroachievements", "show_panel", True)))
        opt_lay.addWidget(self.cb_panel)

        self.cb_popup = QCheckBox("Show RetroArch-Style Achievement Unlock Popups (Fullscreen & Windowed)")
        self.cb_popup.setChecked(bool(self.config.get("retroachievements", "popup_notifications", True)))
        opt_lay.addWidget(self.cb_popup)

        root.addWidget(opt_grp)

        # 3. Groq AI Translation Credential
        ai_grp = QGroupBox("AI Translation Credential")
        ai_lay = QHBoxLayout(ai_grp)
        ai_lay.addWidget(QLabel("Groq API Key:"))
        self.groq_edit = QLineEdit(str(self.config.get("credentials", "groq_api_key", "") or ""))
        self.groq_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_edit.setPlaceholderText("gsk_...")
        ai_lay.addWidget(self.groq_edit, 1)

        btn_eye = QPushButton("👁")
        btn_eye.setCheckable(True)
        btn_eye.setFixedWidth(32)
        btn_eye.toggled.connect(
            lambda ch: self.groq_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password
            )
        )
        ai_lay.addWidget(btn_eye)
        root.addWidget(ai_grp)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save_and_close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _test_login(self):
        self.btn_test.setEnabled(False)
        self.login_status_lbl.setText("Authenticating...")
        self.login_status_lbl.setStyleSheet("color: #FFD54F; font-size: 11px;")
        self.ra.test_login_async(
            self.ra_user_edit.text().strip(),
            self.ra_pass_edit.text(),
            self.ra_token_edit.text().strip(),
        )

    def _on_login_tested(self, ok: bool, msg: str):
        self.btn_test.setEnabled(True)
        self.login_status_lbl.setText(msg)
        self.login_status_lbl.setStyleSheet(
            f"color: {'#81C784' if ok else '#EF5350'}; font-size: 11px;"
        )
        if ok:
            tok = str(self.config.get("credentials", "ra_token", "") or "")
            if tok:
                self.ra_token_edit.setText(tok)

    def _save_and_close(self):
        self.config.set("credentials", "ra_username", self.ra_user_edit.text().strip())
        self.config.set("credentials", "ra_password", self.ra_pass_edit.text())
        self.config.set("credentials", "ra_token", self.ra_token_edit.text().strip())
        self.config.set("credentials", "ra_web_api_key", self.ra_web_edit.text().strip())
        self.config.set("credentials", "groq_api_key", self.groq_edit.text().strip())

        self.config.set("retroachievements", "enabled", self.cb_enabled.isChecked())
        self.config.set("retroachievements", "hardcore", self.cb_hardcore.isChecked())
        self.config.set("retroachievements", "show_panel", self.cb_panel.isChecked())
        self.config.set("retroachievements", "popup_notifications", self.cb_popup.isChecked())
        self.accept()
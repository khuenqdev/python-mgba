# menu_actions.py
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence

from ..tools.dev_tools import (
    PaletteViewerDialog, SpriteViewerDialog, TileViewerDialog,
    MapViewerDialog, FrameInspectorDialog, MemoryViewerDialog,
    MemorySearchDialog, IORegisterViewerDialog
)


class MenuBuilderMixin:
    def _create_menus(self):
        menubar = self.menuBar()

        # 1. FILE MENU
        file_menu = menubar.addMenu("File")

        load_action = QAction("Load ROM...", self)
        load_action.setShortcut(QKeySequence("Ctrl+O"))
        load_action.triggered.connect(self.load_rom)
        file_menu.addAction(load_action)

        self.recent_menu = file_menu.addMenu("Open Recent")

        apply_patch_act = QAction("Apply Soft-Patch (.ips / .bps / .ups)...", self)
        apply_patch_act.triggered.connect(self.apply_patch_dialog)
        file_menu.addAction(apply_patch_act)

        file_menu.addSeparator()

        save_file_act = QAction("Save State to File...", self)
        save_file_act.triggered.connect(self.save_state_to_file)
        file_menu.addAction(save_file_act)

        load_file_act = QAction("Load State from File...", self)
        load_file_act.triggered.connect(self.load_state_from_file)
        file_menu.addAction(load_file_act)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 2. EMULATION MENU
        emu_menu = menubar.addMenu("Emulation")

        self.pause_action = QAction("Pause", self, checkable=True)
        self.pause_action.setShortcut(QKeySequence("Ctrl+P"))
        self.pause_action.triggered.connect(self.toggle_pause)
        emu_menu.addAction(self.pause_action)

        self.pause_unfocus_action = QAction("Pause When Unfocused / Inactive", self, checkable=True)
        self.pause_unfocus_action.setChecked(bool(self.config.get("emulation", "pause_on_unfocus", True)))
        self.pause_unfocus_action.triggered.connect(self.toggle_pause_on_unfocus)
        emu_menu.addAction(self.pause_unfocus_action)

        step_act = QAction("Step Frame", self)
        step_act.setShortcut(QKeySequence("Ctrl+N"))
        step_act.triggered.connect(self.step_frame)
        emu_menu.addAction(step_act)

        reset_action = QAction("Reset", self)
        reset_action.setShortcut(QKeySequence("Ctrl+R"))
        reset_action.triggered.connect(self.reset_emulation)
        emu_menu.addAction(reset_action)

        emu_menu.addSeparator()

        self.rewind_enabled_action = QAction("Enable Rewind Buffer (Hold: Space or `)", self, checkable=True)
        self.rewind_enabled_action.setChecked(bool(self.config.get("emulation", "rewind_enabled", True)))
        self.rewind_enabled_action.triggered.connect(self.toggle_rewind_enabled)
        emu_menu.addAction(self.rewind_enabled_action)

        self.auto_patch_action = QAction("Auto Soft-Patch (.ips / .bps / .ups)", self, checkable=True)
        self.auto_patch_action.setChecked(bool(self.config.get("emulation", "auto_soft_patch", True)))
        self.auto_patch_action.triggered.connect(self.toggle_auto_soft_patch)
        emu_menu.addAction(self.auto_patch_action)

        emu_menu.addSeparator()

        self.skip_bios_action = QAction("Skip BIOS Intro", self, checkable=True)
        self.skip_bios_action.setChecked(bool(self.config.get("emulation", "skip_bios")))
        self.skip_bios_action.triggered.connect(self.toggle_skip_bios)
        emu_menu.addAction(self.skip_bios_action)

        emu_menu.addSeparator()

        quick_save_act = QAction("Quick Save State", self)
        quick_save_act.setShortcut(QKeySequence("Shift+F10"))
        quick_save_act.triggered.connect(lambda: self.quick_save_state(self.current_slot))
        emu_menu.addAction(quick_save_act)

        quick_load_act = QAction("Quick Load State", self)
        quick_load_act.setShortcut(QKeySequence("F10"))
        quick_load_act.triggered.connect(lambda: self.quick_load_state(self.current_slot))
        emu_menu.addAction(quick_load_act)

        state_browser_act = QAction("Save State Slot Browser...", self)
        state_browser_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        state_browser_act.triggered.connect(self.open_state_browser)
        emu_menu.addAction(state_browser_act)

        slot_menu = emu_menu.addMenu("Select State Slot")
        self.slot_group = QActionGroup(self)
        self.slot_group.setExclusive(True)
        self.slot_actions = {}

        for slot_num in range(1, 10):
            slot_act = QAction(f"Slot {slot_num}", self, checkable=True)
            slot_act.triggered.connect(lambda checked, s=slot_num: self.set_active_slot(s))
            self.slot_group.addAction(slot_act)
            slot_menu.addAction(slot_act)
            self.slot_actions[slot_num] = slot_act

        emu_menu.addSeparator()

        self.ff_action = QAction("Fast Forward (Hotkey: = / -)", self, checkable=True)
        self.ff_action.triggered.connect(self.toggle_ff)
        emu_menu.addAction(self.ff_action)

        ff_speed_menu = emu_menu.addMenu("Fast Forward Speed")
        self.ff_group = QActionGroup(self)
        self.ff_group.setExclusive(True)
        self.ff_actions = {}

        speeds = [1.5, 2.0, 3.0, 4.0, 8.0, 10.0]
        for spd in speeds:
            spd_act = QAction(f"{spd}x", self, checkable=True)
            spd_act.triggered.connect(lambda checked, s=spd: self.set_ff_speed(s))
            self.ff_group.addAction(spd_act)
            ff_speed_menu.addAction(spd_act)
            self.ff_actions[spd] = spd_act

        act_ff_up = QAction("Faster Fast-Forward Speed (+)", self)
        act_ff_up.setShortcut(QKeySequence("="))
        act_ff_up.triggered.connect(lambda: self.cycle_ff_speed(forward=True))
        ff_speed_menu.addAction(act_ff_up)

        act_ff_down = QAction("Slower Fast-Forward Speed (-)", self)
        act_ff_down.setShortcut(QKeySequence("-"))
        act_ff_down.triggered.connect(lambda: self.cycle_ff_speed(forward=False))
        ff_speed_menu.addAction(act_ff_down)

        emu_menu.addSeparator()
        replay_menu = emu_menu.addMenu("Gameplay Replay & Recording")

        self.act_record_session = QAction("⏺ Start Recording Session...", self)
        self.act_record_session.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.act_record_session.triggered.connect(self.toggle_session_recording)
        replay_menu.addAction(self.act_record_session)

        self.act_play_session = QAction("▶ Load & Play Replay (.json)...", self)
        self.act_play_session.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.act_play_session.triggered.connect(self.load_replay_session)
        replay_menu.addAction(self.act_play_session)

        self.act_stop_replay = QAction("⏹ Stop Current Replay", self)
        self.act_stop_replay.triggered.connect(self.stop_replay_session)
        replay_menu.addAction(self.act_stop_replay)

        # 3. SENSORS & PERIPHERALS MENU
        sensors_menu = menubar.addMenu("Sensors & Rumble")

        self.act_sensor_hud = QAction("Show Sensor / Rumble Status Panel", self, checkable=True)
        self.act_sensor_hud.setChecked(bool(self.config.get("sensors", "show_hud", True)))
        self.act_sensor_hud.triggered.connect(self.toggle_sensor_hud)
        sensors_menu.addAction(self.act_sensor_hud)
        sensors_menu.addSeparator()

        solar_menu = sensors_menu.addMenu("☀️ Solar Sensor (Boktai)")
        self.solar_group = QActionGroup(self)
        self.solar_group.setExclusive(True)
        self.solar_actions = {}

        for lvl in range(11):
            act = QAction(
                f"Sun Level {lvl}" + (" (Maximum)" if lvl == 10 else (" (Dark)" if lvl == 0 else "")),
                self, checkable=True
            )
            act.triggered.connect(lambda checked, l=lvl: self.set_solar_level(l))
            self.solar_group.addAction(act)
            solar_menu.addAction(act)
            self.solar_actions[lvl] = act

        act_brighten = QAction("Brighten Sunlight (+1)", self)
        act_brighten.setShortcut(QKeySequence("]"))
        act_brighten.triggered.connect(lambda: self.adjust_solar_sensor(1))
        solar_menu.addAction(act_brighten)

        act_darken = QAction("Darken Sunlight (-1)", self)
        act_darken.setShortcut(QKeySequence("["))
        act_darken.triggered.connect(lambda: self.adjust_solar_sensor(-1))
        solar_menu.addAction(act_darken)

        sensors_menu.addSeparator()

        self.act_rumble = QAction("🎮 Controller Motor Rumble", self, checkable=True)
        self.act_rumble.setChecked(bool(self.config.get("sensors", "rumble_enabled")))
        self.act_rumble.triggered.connect(self.toggle_rumble)
        sensors_menu.addAction(self.act_rumble)

        self.act_mouse_gyro = QAction("🖱️ Mouse Twist to Gyro (Right-Click Drag)", self, checkable=True)
        self.act_mouse_gyro.setChecked(bool(self.config.get("sensors", "mouse_gyro")))
        self.act_mouse_gyro.triggered.connect(self.toggle_mouse_gyro)
        sensors_menu.addAction(self.act_mouse_gyro)

        act_reset_tilt = QAction("🎯 Center Gyro & Tilt Sensor", self)
        act_reset_tilt.setShortcut(QKeySequence("Backspace"))
        act_reset_tilt.triggered.connect(self.reset_sensors)
        sensors_menu.addAction(act_reset_tilt)

        # 4. AUDIO MENU
        audio_menu = menubar.addMenu("Audio")
        self.mute_action = QAction("Mute", self, checkable=True)
        self.mute_action.setChecked(self.config.get("audio", "mute"))
        self.mute_action.triggered.connect(self.toggle_mute)
        audio_menu.addAction(self.mute_action)

        audio_menu.addSeparator()

        lpf_menu = audio_menu.addMenu("Low-Pass Filter")
        self.lpf_group = QActionGroup(self)
        self.lpf_group.setExclusive(True)
        self.lpf_actions = {}

        lpf_modes = [
            ("off", "Off (Raw DirectSound)"),
            ("light", "Light (~12 kHz - Mild Hiss Reduction)"),
            ("medium", "Medium (~8 kHz - Warm Speaker Sound)"),
            ("heavy", "Heavy (~5 kHz - Smooth Vintage Sound)")
        ]
        for mode_key, label in lpf_modes:
            act = QAction(label, self, checkable=True)
            act.triggered.connect(lambda checked, m=mode_key: self.set_lpf_mode(m))
            self.lpf_group.addAction(act)
            lpf_menu.addAction(act)
            self.lpf_actions[mode_key] = act

        enh_menu = audio_menu.addMenu("Enhancements")
        self.widener_action = QAction("Stereo Widener (Surround Sound)", self, checkable=True)
        self.widener_action.triggered.connect(self.toggle_stereo_widener)
        enh_menu.addAction(self.widener_action)

        self.bass_action = QAction("Bass Boost (Low-End Warmth)", self, checkable=True)
        self.bass_action.triggered.connect(self.toggle_bass_boost)
        enh_menu.addAction(self.bass_action)

        self.limiter_action = QAction("Soft Limiter (Anti-Clipping)", self, checkable=True)
        self.limiter_action.triggered.connect(self.toggle_soft_limiter)
        enh_menu.addAction(self.limiter_action)

        vol_menu = audio_menu.addMenu("Volume Gain")
        self.vol_group = QActionGroup(self)
        self.vol_group.setExclusive(True)
        self.vol_actions = {}

        vol_levels = [0.25, 0.5, 0.75, 1.0, 1.25]
        for vol in vol_levels:
            lbl = f"{int(vol * 100)}%" + (" (Default)" if abs(vol - 0.5) < 0.01 else "")
            act = QAction(lbl, self, checkable=True)
            act.triggered.connect(lambda checked, v=vol: self.set_volume_gain(v))
            self.vol_group.addAction(act)
            vol_menu.addAction(act)
            self.vol_actions[vol] = act

        # 5. VIDEO MENU (Shaders, Fullscreen, Geometry Scaling, Filters)
        video_menu = menubar.addMenu("Video")

        self.fullscreen_action = QAction("Fullscreen (Alt+Enter / F11)", self, checkable=True)
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        video_menu.addAction(self.fullscreen_action)

        self.integer_scale_action = QAction("Integer Scaling Only", self, checkable=True)
        self.integer_scale_action.setChecked(bool(self.config.get("video", "integer_scaling", False)))
        self.integer_scale_action.triggered.connect(self.toggle_integer_scaling)
        video_menu.addAction(self.integer_scale_action)

        video_menu.addSeparator()

        shader_menu = video_menu.addMenu("RetroArch Shaders")

        self.act_shader_toggle = QAction("Enable Shaders", self, checkable=True)
        self.act_shader_toggle.setChecked(self.shader_manager.enabled)
        self.act_shader_toggle.triggered.connect(self.toggle_shaders)
        shader_menu.addAction(self.act_shader_toggle)

        act_shader_cfg = QAction("Configure Shaders & Parameters...", self)
        act_shader_cfg.setShortcut(QKeySequence("F8"))
        act_shader_cfg.triggered.connect(self.open_shader_dialog)
        shader_menu.addAction(act_shader_cfg)
        shader_menu.addSeparator()

        sub_presets = shader_menu.addMenu("Quick Presets")
        presets = [
            ("GBA Color & LCD Grid", "gba_color"),
            ("CRT-Easymode", "crt"),
            ("Scanlines Classic", "scanlines"),
            ("Sharp Bilinear", "sharp"),
            ("Stock (Passthrough)", "stock")
        ]
        for label, p_id in presets:
            act = sub_presets.addAction(label)
            act.triggered.connect(lambda _, pid=p_id: self.apply_quick_shader_preset(pid))

        video_menu.addSeparator()

        self.blend_action = QAction("Interframe Blending", self, checkable=True)
        self.blend_action.setChecked(bool(self.config.get("video", "interframe_blending")))
        self.blend_action.triggered.connect(self.toggle_interframe_blending)
        video_menu.addAction(self.blend_action)

        frameskip_menu = video_menu.addMenu("Frameskip")
        self.fs_group = QActionGroup(self)
        self.fs_group.setExclusive(True)
        self.fs_actions = {}

        for fs in range(0, 6):
            label = "0 (Disabled)" if fs == 0 else f"{fs} Frames"
            fs_act = QAction(label, self, checkable=True)
            fs_act.triggered.connect(lambda checked, count=fs: self.set_frameskip(count))
            self.fs_group.addAction(fs_act)
            frameskip_menu.addAction(fs_act)
            self.fs_actions[fs] = fs_act

        video_menu.addSeparator()

        screenshot_action = QAction("Screenshot", self)
        screenshot_action.setShortcut(QKeySequence("F12"))
        screenshot_action.triggered.connect(self.take_screenshot)
        video_menu.addAction(screenshot_action)

        video_menu.addSeparator()

        scale_menu = video_menu.addMenu("Window Scale")
        self.scale_group = QActionGroup(self)
        self.scale_group.setExclusive(True)
        self.scale_actions = {}

        for s in range(1, 7):
            scale_act = QAction(f"{s}x Scale ({240*s}×{160*s})", self, checkable=True)
            scale_act.triggered.connect(lambda checked, sc=s: self.set_scale(sc))
            self.scale_group.addAction(scale_act)
            scale_menu.addAction(scale_act)
            self.scale_actions[s] = scale_act

        # 6. GAME STATE VIEWS MENU
        views_menu = menubar.addMenu("Game state views")

        dossier_act = QAction("🚀 Instant AI Scene Dump...", self)
        dossier_act.setShortcut(QKeySequence("Ctrl+Shift+D"))
        dossier_act.triggered.connect(self.trigger_instant_scene_dump)
        views_menu.addAction(dossier_act)
        views_menu.addSeparator()

        views_menu.addAction("View palettes...", lambda: self._open_dev_tool(PaletteViewerDialog))
        views_menu.addAction("View sprites...", lambda: self._open_dev_tool(SpriteViewerDialog))
        views_menu.addAction("View tiles...", lambda: self._open_dev_tool(TileViewerDialog))
        views_menu.addAction("View map...", lambda: self._open_dev_tool(MapViewerDialog))
        views_menu.addSeparator()
        views_menu.addAction("Frame inspector...", lambda: self._open_dev_tool(FrameInspectorDialog))

        hex_act = QAction("View memory (Hex Editor)...", self)
        hex_act.setShortcut(QKeySequence("Ctrl+H"))
        hex_act.triggered.connect(lambda: self._open_dev_tool(MemoryViewerDialog))
        views_menu.addAction(hex_act)

        views_menu.addAction("Search memory...", lambda: self._open_dev_tool(MemorySearchDialog))
        views_menu.addAction("View I/O registers...", lambda: self._open_dev_tool(IORegisterViewerDialog))

        # 7. TRANSLATION MENU
        translation_menu = menubar.addMenu("Translation")

        ai_trans_act = QAction("⚡ Translate Screen with AI (Hotkey: T)", self)
        ai_trans_act.setShortcut(QKeySequence("T"))
        ai_trans_act.triggered.connect(self.trigger_ai_translation)
        translation_menu.addAction(ai_trans_act)

        clear_panel_act = QAction("Clear Subtitle Panel", self)
        clear_panel_act.triggered.connect(self.translation_panel.clear)
        translation_menu.addAction(clear_panel_act)

        ai_cfg_act = QAction("Configure AI Translation...", self)
        ai_cfg_act.triggered.connect(self.open_ai_translation_config)
        translation_menu.addAction(ai_cfg_act)

        translation_menu.addSeparator()

        text_inspector_act = QAction("Text Inspector & Script Auto-Repointer...", self)
        text_inspector_act.triggered.connect(self.open_translation_tools)
        translation_menu.addAction(text_inspector_act)

        font_tools_act = QAction("Font Studio & TTF Generator...", self)
        font_tools_act.triggered.connect(self.open_font_tools)
        translation_menu.addAction(font_tools_act)

        translation_menu.addSeparator()

        self.ruler_action = QAction("Screen Pixel Ruler Overlay", self, checkable=True)
        self.ruler_action.triggered.connect(self.toggle_pixel_ruler)
        translation_menu.addAction(self.ruler_action)

        # 8. TOOLS MENU
        tools_menu = menubar.addMenu("Tools")

        editor_act = QAction("In-Game Graphic Editor (YY-CHR)...", self)
        editor_act.setShortcut(QKeySequence("Ctrl+G"))
        editor_act.triggered.connect(self.open_graphic_editor)
        tools_menu.addAction(editor_act)

        hex_tool_act = QAction("Interactive Hex Editor (HxD / ImHex)...", self)
        hex_tool_act.triggered.connect(lambda: self._open_dev_tool(MemoryViewerDialog))
        tools_menu.addAction(hex_tool_act)

        tools_menu.addSeparator()
        tools_menu.addAction("Cheats Manager...", self.open_cheats_manager)
        tools_menu.addSeparator()
        tools_menu.addAction("Configure Controls & Motion Feedback...", self.open_controller_config)
        tools_menu.addSeparator()

        reset_settings_act = QAction("Reset Settings to Default...", self)
        reset_settings_act.triggered.connect(self.reset_settings_to_default)
        tools_menu.addAction(reset_settings_act)
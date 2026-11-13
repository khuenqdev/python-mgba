# shader_dialog.py
import os
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea, QFileDialog, QMessageBox, QDoubleSpinBox, QSlider,
    QGroupBox, QMenu, QSplitter
)
from PyQt6.QtCore import Qt, QTimer

BUILTIN_SHADERS = [
    ("GBA Color & LCD Grid", "gba_color"),
    ("CRT-Easymode", "crt"),
    ("Scanlines Classic", "scanlines"),
    ("Sharp Bilinear", "sharp"),
    ("Stock Passthrough", "stock")
]


class ShaderConfigDialog(QDialog):
    """RetroArch-style shader pass chain manager and real-time parameter tuner."""

    def __init__(self, shader_manager, video_widget, config, parent=None):
        super().__init__(parent)
        self.sm = shader_manager
        self.vw = video_widget
        self.config = config

        self.setWindowTitle("RetroArch Shaders & Parameter Studio")
        self.resize(880, 560)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self.sm.save_chain_to_config)

        self._init_ui()
        self.refresh_passes_table()
        self.refresh_parameters_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # Top Control Ribbon
        top_bar = QHBoxLayout()
        self.cb_enable = QCheckBox("Enable Shaders")
        self.cb_enable.setChecked(self.sm.enabled)
        self.cb_enable.toggled.connect(self.on_toggle_enabled)
        top_bar.addWidget(self.cb_enable)

        btn_load_preset = QPushButton("📂 Load Preset (.glslp)...")
        btn_load_preset.clicked.connect(self.load_preset_file)
        top_bar.addWidget(btn_load_preset)

        btn_save_preset = QPushButton("💾 Save Preset (.glslp)...")
        btn_save_preset.clicked.connect(self.save_preset_file)
        top_bar.addWidget(btn_save_preset)

        btn_reset_defaults = QPushButton("🔄 Reset Parameters")
        btn_reset_defaults.clicked.connect(self.reset_parameters)
        top_bar.addWidget(btn_reset_defaults)

        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        # Main Splitter: Passes on Left | Parameters on Right
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Widget: Passes Chain
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("<b>Shader Passes Pipeline</b>"))

        self.table_passes = QTableWidget(0, 4)
        self.table_passes.setHorizontalHeaderLabels(["Pass", "Shader Name", "Filter", "Scale"])
        self.table_passes.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_passes.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        left_layout.addWidget(self.table_passes)

        # Pass Modification Buttons
        btn_row_pass = QHBoxLayout()
        self.btn_prepend = QPushButton("⬆️ Prepend Shader...")
        self.btn_prepend.clicked.connect(lambda: self.show_add_menu(prepend=True))
        btn_row_pass.addWidget(self.btn_prepend)

        self.btn_append = QPushButton("➕ Append Shader...")
        self.btn_append.clicked.connect(lambda: self.show_add_menu(prepend=False))
        btn_row_pass.addWidget(self.btn_append)

        self.btn_remove = QPushButton("❌ Remove")
        self.btn_remove.clicked.connect(self.remove_selected_pass)
        btn_row_pass.addWidget(self.btn_remove)

        self.btn_up = QPushButton("▲ Up")
        self.btn_up.clicked.connect(lambda: self.move_pass(up=True))
        btn_row_pass.addWidget(self.btn_up)

        self.btn_down = QPushButton("▼ Down")
        self.btn_down.clicked.connect(lambda: self.move_pass(up=False))
        btn_row_pass.addWidget(self.btn_down)

        left_layout.addLayout(btn_row_pass)
        splitter.addWidget(left_widget)

        # Right Widget: Real-Time Shader Parameters
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_layout.addWidget(QLabel("<b>Live Shader Parameters (#pragma)</b>"))

        self.scroll_params = QScrollArea()
        self.scroll_params.setWidgetResizable(True)
        self.container_params = QWidget()
        self.layout_params = QVBoxLayout(self.container_params)
        self.scroll_params.setWidget(self.container_params)
        right_layout.addWidget(self.scroll_params)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        main_layout.addWidget(splitter)

        # Bottom Apply & Close
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        main_layout.addWidget(btn_close)

    def on_toggle_enabled(self, checked: bool):
        self.sm.enabled = checked
        self.config.set("shaders", "enabled", checked)
        self.config.save()
        if self.vw:
            self.vw.update()

    def refresh_passes_table(self):
        self.table_passes.setRowCount(len(self.sm.passes))

        for idx, p in enumerate(self.sm.passes):
            item_idx = QTableWidgetItem(f"Pass {idx}")
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_passes.setItem(idx, 0, item_idx)

            item_name = QTableWidgetItem(p.name)
            self.table_passes.setItem(idx, 1, item_name)

            # Filter Mode ComboBox
            cb_filter = QComboBox()
            cb_filter.addItems(["Nearest", "Linear"])
            cb_filter.setCurrentIndex(1 if p.filter_linear else 0)
            cb_filter.currentIndexChanged.connect(lambda c_idx, pass_idx=idx: self.on_filter_changed(pass_idx, c_idx == 1))
            self.table_passes.setCellWidget(idx, 2, cb_filter)

            # Scale Factor ComboBox
            cb_scale = QComboBox()
            cb_scale.addItems(["Viewport", "1.0x", "2.0x", "3.0x", "4.0x", "5.0x", "6.0x"])
            if p.scale_type == "viewport":
                cb_scale.setCurrentIndex(0)
            else:
                scale_idx = max(1, min(6, int(round(p.scale_factor))))
                cb_scale.setCurrentIndex(scale_idx)
            cb_scale.currentIndexChanged.connect(lambda s_idx, pass_idx=idx: self.on_scale_changed(pass_idx, s_idx))
            self.table_passes.setCellWidget(idx, 3, cb_scale)

    def on_filter_changed(self, pass_idx: int, is_linear: bool):
        if 0 <= pass_idx < len(self.sm.passes):
            self.sm.passes[pass_idx].filter_linear = is_linear
            self.sm.save_chain_to_config()
            if self.vw:
                self.vw.update()

    def on_scale_changed(self, pass_idx: int, combo_idx: int):
        if 0 <= pass_idx < len(self.sm.passes):
            p = self.sm.passes[pass_idx]
            if combo_idx == 0:
                p.scale_type = "viewport"
                p.scale_factor = 1.0
            else:
                p.scale_type = "source"
                p.scale_factor = float(combo_idx)
            self.sm.save_chain_to_config()
            if self.vw:
                self.vw.update()

    def refresh_parameters_ui(self):
        while self.layout_params.count():
            item = self.layout_params.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        has_params = False
        for pass_idx, p in enumerate(self.sm.passes):
            if not p.parameters:
                continue

            has_params = True
            grp = QGroupBox(f"Pass {pass_idx}: {p.name}")
            grp_layout = QVBoxLayout(grp)

            for _, param in p.parameters.items():
                row = QHBoxLayout()

                lbl = QLabel(param.desc or param.name)
                lbl.setToolTip(f"Uniform: {param.name}")
                lbl.setMinimumWidth(130)
                row.addWidget(lbl)

                steps = max(1, int(round((param.max_val - param.min_val) / param.step_val)))
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(0, steps)

                spin = QDoubleSpinBox()
                spin.setRange(param.min_val, param.max_val)
                spin.setSingleStep(param.step_val)
                spin.setValue(param.current_val)
                spin.setDecimals(3)
                spin.setFixedWidth(75)

                cur_step = int(round((param.current_val - param.min_val) / param.step_val))
                slider.setValue(max(0, min(steps, cur_step)))

                def make_handlers(target_param, s_widget, sp_widget, st_count):
                    def on_slider(val):
                        new_val = target_param.min_val + (val * target_param.step_val)
                        target_param.current_val = new_val
                        sp_widget.blockSignals(True)
                        sp_widget.setValue(new_val)
                        sp_widget.blockSignals(False)
                        self._save_timer.start()
                        if self.vw:
                            self.vw.update()

                    def on_spin(val):
                        target_param.current_val = val
                        step_idx = int(round((val - target_param.min_val) / target_param.step_val))
                        s_widget.blockSignals(True)
                        s_widget.setValue(max(0, min(st_count, step_idx)))
                        s_widget.blockSignals(False)
                        self._save_timer.start()
                        if self.vw:
                            self.vw.update()

                    return on_slider, on_spin

                h_slider, h_spin = make_handlers(param, slider, spin, steps)
                slider.valueChanged.connect(h_slider)
                spin.valueChanged.connect(h_spin)

                row.addWidget(slider)
                row.addWidget(spin)
                grp_layout.addLayout(row)

            self.layout_params.addWidget(grp)

        if not has_params:
            self.layout_params.addWidget(QLabel("<i>No adjustable parameters found in active passes.</i>"))

        self.layout_params.addStretch()

    def show_add_menu(self, prepend: bool):
        btn = self.btn_prepend if prepend else self.btn_append
        menu = QMenu(self)

        sub_builtin = menu.addMenu("Built-in RetroArch Shaders")
        for label, preset_id in BUILTIN_SHADERS:
            act = sub_builtin.addAction(label)
            act.triggered.connect(lambda _, pid=preset_id, prep=prepend: self.add_builtin_shader(pid, prep))

        act_browse = menu.addAction("Browse .glsl File...")
        act_browse.triggered.connect(lambda _, prep=prepend: self.browse_custom_glsl(prep))

        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def add_builtin_shader(self, preset_id: str, prepend: bool):
        from .shader_pipeline import BUILTIN_SHADER_REGISTRY
        name, src = BUILTIN_SHADER_REGISTRY.get(preset_id, BUILTIN_SHADER_REGISTRY["stock"])
        if prepend:
            self.sm.prepend_shader(name, src, builtin_id=preset_id)
        else:
            self.sm.append_shader(name, src, builtin_id=preset_id)

        self.refresh_passes_table()
        self.refresh_parameters_ui()
        if self.vw:
            self.vw.update()

    def browse_custom_glsl(self, prepend: bool):
        filepath, _ = QFileDialog.getOpenFileName(self, "Load RetroArch GLSL Shader", "", "GLSL Shaders (*.glsl);;All Files (*.*)")
        if filepath:
            success = self.sm.load_glsl_file(filepath, prepend=prepend)
            if success:
                self.refresh_passes_table()
                self.refresh_parameters_ui()
                if self.vw:
                    self.vw.update()
            else:
                QMessageBox.warning(self, "Shader Error", f"Failed to compile shader:\n{os.path.basename(filepath)}")

    def remove_selected_pass(self):
        rows = self.table_passes.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        self.sm.remove_pass(idx)
        self.refresh_passes_table()
        self.refresh_parameters_ui()
        if self.vw:
            self.vw.update()

    def move_pass(self, up: bool):
        rows = self.table_passes.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        target = idx - 1 if up else idx + 1
        if 0 <= target < len(self.sm.passes):
            self.sm.move_pass(idx, up)
            self.refresh_passes_table()
            self.refresh_parameters_ui()
            self.table_passes.selectRow(target)
            if self.vw:
                self.vw.update()

    def reset_parameters(self):
        for p in self.sm.passes:
            for param in p.parameters.values():
                param.current_val = param.default_val
        self.sm.save_chain_to_config()
        self.refresh_parameters_ui()
        if self.vw:
            self.vw.update()

    def load_preset_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Load RetroArch Preset", "", "RetroArch Presets (*.glslp);;All Files (*.*)")
        if filepath:
            if self.sm.load_preset_file(filepath):
                self.refresh_passes_table()
                self.refresh_parameters_ui()
                if self.vw:
                    self.vw.update()
                QMessageBox.information(self, "Preset Loaded", f"Loaded preset:\n{os.path.basename(filepath)}")
            else:
                QMessageBox.warning(self, "Preset Error", "Failed to load preset or shader files not found.")

    def save_preset_file(self):
        if not self.sm.passes:
            QMessageBox.warning(self, "Save Error", "No shader passes to save.")
            return

        filepath, _ = QFileDialog.getSaveFileName(self, "Save RetroArch Preset", "custom_preset.glslp", "RetroArch Presets (*.glslp)")
        if filepath:
            self.sm.save_preset_file(filepath)
            QMessageBox.information(self, "Preset Saved", f"Successfully exported preset to:\n{filepath}")

    def done(self, result):
        if self._save_timer.isActive():
            self._save_timer.stop()
        self.sm.save_chain_to_config()
        super().done(result)
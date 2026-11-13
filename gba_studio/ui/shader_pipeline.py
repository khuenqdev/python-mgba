# shader_manager.py
import os
import re
import ctypes
import configparser
import numpy as np
from OpenGL import GL

from PyQt6.QtOpenGL import (QOpenGLShader, QOpenGLShaderProgram, 
                            QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat)
from PyQt6.QtGui import QMatrix4x4, QOpenGLContext

from .shader_presets import *
from .shader_types import ShaderParameter, ShaderPass

class RetroArchShaderManager:
    """Multi-pass RetroArch GLSL shader pipeline & preset processor."""
    def __init__(self, config):
        self.config = config
        self.passes = []
        self.enabled = bool(self.config.get("shaders", "enabled", False))
        self.frame_count = 0
        self.widget = None

        self.stock_pass = ShaderPass("Stock Passthrough", STOCK_SHADER_GLSL)
        self.stock_pass.filter_linear = False

        # Native OpenGL integer handles (avoids PyQt6 QOpenGLBuffer SIP __len__ SystemError)
        self.vao_id = None
        self.vbo_flipped_id = None
        self.vbo_normal_id = None

        # Quad reading from CPU-uploaded texture (row 0 = top, V=0 at top, V=1 at bottom)
        self.verts_flipped = np.array([
            -1.0, -1.0,  0.0, 1.0,
             1.0, -1.0,  1.0, 1.0,
            -1.0,  1.0,  0.0, 0.0,
             1.0,  1.0,  1.0, 0.0
        ], dtype=np.float32)

        # Quad reading from OpenGL FBO texture (V=1 at top, V=0 at bottom)
        self.verts_normal = np.array([
            -1.0, -1.0,  0.0, 0.0,
             1.0, -1.0,  1.0, 0.0,
            -1.0,  1.0,  0.0, 1.0,
             1.0,  1.0,  1.0, 1.0
        ], dtype=np.float32)

        self.init_default_preset()

    def set_gl_widget(self, widget):
        self.widget = widget

    def init_gl(self):
        """Creates OpenGL VAO, VBOs, and compiles all active passes inside an active GL context."""
        if self.widget is not None:
            self.widget.makeCurrent()

        try:
            if hasattr(GL, "glGenVertexArrays") and bool(GL.glGenVertexArrays):
                self.vao_id = int(GL.glGenVertexArrays(1))
        except Exception:
            self.vao_id = None

        self.vbo_flipped_id = int(GL.glGenBuffers(1))
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo_flipped_id)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, self.verts_flipped.nbytes, self.verts_flipped, GL.GL_STATIC_DRAW)

        self.vbo_normal_id = int(GL.glGenBuffers(1))
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo_normal_id)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, self.verts_normal.nbytes, self.verts_normal, GL.GL_STATIC_DRAW)

        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)

        self.stock_pass.compile()
        self.compile_all()

    def init_default_preset(self):
        preset_name = self.config.get("shaders", "preset") or "gba_color"
        self.load_preset_by_name(preset_name, compile_now=False)

    def load_preset_by_name(self, name: str, compile_now: bool = True):
        self.passes.clear()
        if name == "gba_color":
            self.passes.append(ShaderPass("GBA Color & LCD Grid", GBA_COLOR_LCD_GLSL))
        elif name == "crt":
            self.passes.append(ShaderPass("CRT-Easymode", CRT_EASYMODE_GLSL))
        elif name == "scanlines":
            self.passes.append(ShaderPass("Scanlines Classic", SCANLINES_CLASSIC_GLSL))
        elif name == "sharp":
            self.passes.append(ShaderPass("Sharp Bilinear", SHARP_BILINEAR_GLSL))
        else:
            self.passes.append(ShaderPass("Stock Passthrough", STOCK_SHADER_GLSL))

        if compile_now:
            self.compile_all()

    def compile_all(self):
        if self.widget is not None:
            self.widget.makeCurrent()
        for p in self.passes:
            p.compile()

    def prepend_shader(self, name: str, source: str, filepath: str = ""):
        if self.widget is not None:
            self.widget.makeCurrent()
        p = ShaderPass(name, source, filepath)
        if p.compile():
            self.passes.insert(0, p)
            return True
        return False

    def append_shader(self, name: str, source: str, filepath: str = ""):
        if self.widget is not None:
            self.widget.makeCurrent()
        p = ShaderPass(name, source, filepath)
        if p.compile():
            self.passes.append(p)
            return True
        return False

    def remove_pass(self, index: int):
        if 0 <= index < len(self.passes):
            del self.passes[index]

    def move_pass(self, index: int, up: bool):
        target = index - 1 if up else index + 1
        if 0 <= target < len(self.passes):
            self.passes[index], self.passes[target] = self.passes[target], self.passes[index]

    def load_glsl_file(self, filepath: str, prepend: bool = False) -> bool:
        if not os.path.exists(filepath):
            return False
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read()

        name = os.path.splitext(os.path.basename(filepath))[0]
        if prepend:
            return self.prepend_shader(name, src, filepath)
        else:
            return self.append_shader(name, src, filepath)

    def load_preset_file(self, filepath: str) -> bool:
        if not os.path.exists(filepath):
            return False

        base_dir = os.path.dirname(os.path.abspath(filepath))
        cfg = configparser.ConfigParser(strict=False, inline_comment_prefixes=('#', ';'))
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = "[root]\n" + f.read()
            cfg.read_string(content)
        except Exception as e:
            print(f"Failed to parse preset file {filepath}: {e}")
            return False

        root = cfg["root"]
        num_shaders = int(root.get("shaders", "0"))
        if num_shaders <= 0:
            return False

        if self.widget is not None:
            self.widget.makeCurrent()

        new_passes = []
        for i in range(num_shaders):
            rel_path = root.get(f"shader{i}", "").strip('"').strip("'")
            full_path = os.path.normpath(os.path.join(base_dir, rel_path))
            if not os.path.exists(full_path):
                print(f"Shader pass file not found: {full_path}")
                continue

            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                src = f.read()

            p = ShaderPass(os.path.splitext(os.path.basename(full_path))[0], src, full_path)
            p.filter_linear = root.get(f"filter_linear{i}", "true").lower() == "true"
            p.scale_type = root.get(f"scale_type{i}", "viewport").lower()
            p.scale_factor = float(root.get(f"scale{i}", "1.0"))

            if p.compile():
                new_passes.append(p)

        for p in new_passes:
            for p_name, param in p.parameters.items():
                if p_name in root:
                    try:
                        param.current_val = float(root.get(p_name))
                    except ValueError: pass

        if new_passes:
            self.passes = new_passes
            return True
        return False

    def save_preset_file(self, filepath: str):
        lines = [f"shaders = {len(self.passes)}", ""]
        all_params = []

        for i, p in enumerate(self.passes):
            lines.append(f'shader{i} = "{p.filepath or (p.name + ".glsl")}"')
            lines.append(f"filter_linear{i} = {'true' if p.filter_linear else 'false'}")
            lines.append(f"scale_type{i} = {p.scale_type}")
            lines.append(f"scale{i} = {p.scale_factor}")
            lines.append("")
            for p_name in p.parameters:
                all_params.append(p_name)

        if all_params:
            lines.append(f'parameters = "{";".join(all_params)}"')
            for p in self.passes:
                for p_name, param in p.parameters.items():
                    lines.append(f"{p_name} = {param.current_val:.4f}")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def render(self, input_tex_id: int, in_w: int, in_h: int, out_w: int, out_h: int, default_fbo: int):
        if self.vbo_flipped_id is None or self.stock_pass.program is None:
            return

        self.frame_count += 1
        active_passes = [p for p in self.passes if p.program is not None]

        # If shaders disabled, draw using stock passthrough pass
        if not self.enabled or not active_passes:
            self._render_quad(
                input_tex_id, None, default_fbo,
                in_w, in_h, out_w, out_h,
                self.stock_pass, is_first=True
            )
            return

        current_tex_id = input_tex_id
        curr_w, curr_h = in_w, in_h

        for i, p in enumerate(active_passes):
            is_first = (i == 0)
            is_last = (i == len(active_passes) - 1)

            if is_last:
                target_fbo = None
                target_w, target_h = out_w, out_h
            else:
                if p.scale_type == "source":
                    target_w = max(1, int(curr_w * p.scale_factor))
                    target_h = max(1, int(curr_h * p.scale_factor))
                else:
                    target_w = max(1, int(out_w * p.scale_factor))
                    target_h = max(1, int(out_h * p.scale_factor))

                if p.fbo is None or p.fbo.width() != target_w or p.fbo.height() != target_h:
                    format_fbo = QOpenGLFramebufferObjectFormat()
                    p.fbo = QOpenGLFramebufferObject(target_w, target_h, format_fbo)
                target_fbo = p.fbo

            self._render_quad(
                current_tex_id, target_fbo, default_fbo,
                curr_w, curr_h, target_w, target_h,
                p, is_first=is_first
            )

            if not is_last:
                current_tex_id = target_fbo.texture()
                curr_w, curr_h = target_w, target_h

    def _render_quad(self, tex_id: int, fbo, default_fbo: int, in_w: int, in_h: int, out_w: int, out_h: int, shader_pass, is_first: bool):
        if fbo is not None:
            fbo.bind()
            GL.glViewport(0, 0, out_w, out_h)
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        else:
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, default_fbo)
            GL.glViewport(0, 0, out_w, out_h)
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)

            # Aspect-ratio-preserving 3:2 viewport on final screen output
            scale = min(out_w / 240.0, out_h / 160.0)
            vp_w = max(1, int(240.0 * scale))
            vp_h = max(1, int(160.0 * scale))
            vp_x = (out_w - vp_w) // 2
            vp_y = (out_h - vp_h) // 2
            GL.glViewport(vp_x, vp_y, vp_w, vp_h)

        prog = shader_pass.program
        if prog is None:
            if fbo is not None: fbo.release()
            return

        if self.vao_id is not None:
            GL.glBindVertexArray(self.vao_id)

        prog.bind()

        mvp = QMatrix4x4()
        prog.setUniformValue("MVPMatrix", mvp)
        prog.setUniformValue("TextureSize", float(in_w), float(in_h))
        prog.setUniformValue("InputSize", float(in_w), float(in_h))
        prog.setUniformValue("OutputSize", float(out_w), float(out_h))
        prog.setUniformValue("FrameCount", int(self.frame_count))

        for p_name, param in shader_pass.parameters.items():
            prog.setUniformValue(p_name, float(param.current_val))

        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, int(tex_id))

        filter_mode = GL.GL_LINEAR if shader_pass.filter_linear else GL.GL_NEAREST
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, filter_mode)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, filter_mode)
        prog.setUniformValue("Texture", 0)

        vbo_id = self.vbo_flipped_id if is_first else self.vbo_normal_id
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo_id)
        GL.glEnableVertexAttribArray(0)
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 16, ctypes.c_void_p(0))
        GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, GL.GL_FALSE, 16, ctypes.c_void_p(8))

        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

        GL.glDisableVertexAttribArray(0)
        GL.glDisableVertexAttribArray(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

        prog.release()

        if self.vao_id is not None:
            GL.glBindVertexArray(0)

        if fbo is not None:
            fbo.release()

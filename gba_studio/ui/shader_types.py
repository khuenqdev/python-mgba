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

class ShaderParameter:
    def __init__(self, name: str, desc: str, default_val: float, min_val: float, max_val: float, step_val: float):
        self.name = name
        self.desc = desc
        self.default_val = float(default_val)
        self.min_val = float(min_val)
        self.max_val = float(max_val)
        self.step_val = float(step_val)
        self.current_val = float(default_val)


class ShaderPass:
    def __init__(self, name: str, source: str, filepath: str = ""):
        self.name = name
        self.source = source
        self.filepath = filepath
        self.filter_linear = True
        self.scale_type = "viewport"
        self.scale_factor = 1.0
        self.parameters = {}
        self.program = None
        self.fbo = None

        self._parse_parameters()

    def _parse_parameters(self):
        pattern = r'#pragma\s+parameter\s+(\w+)\s+"([^"]+)"\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)'
        matches = re.findall(pattern, self.source)
        for m in matches:
            p = ShaderParameter(name=m[0], desc=m[1], default_val=m[2], min_val=m[3], max_val=m[4], step_val=m[5])
            self.parameters[p.name] = p

    def compile(self) -> bool:
        if QOpenGLContext.currentContext() is None:
            return False

        if self.program is not None:
            del self.program
            self.program = None

        program = QOpenGLShaderProgram()
        clean_source = re.sub(r'#pragma\s+parameter[^\n]*\n?', '', self.source)
        declared_params = set(re.findall(r'uniform\s+float\s+(\w+)\s*;', clean_source))

        v_prefix = "#version 120\n#define VERTEX 1\n#define PARAMETER_UNIFORM 1\n"
        for p in self.parameters.values():
            if p.name not in declared_params:
                v_prefix += f"uniform float {p.name};\n"

        v_shader = QOpenGLShader(QOpenGLShader.ShaderTypeBit.Vertex)
        if not v_shader.compileSourceCode(v_prefix + clean_source):
            print(f"[SHADER ERROR] Vertex compilation failed for {self.name}:\n{v_shader.log()}")
            return False

        f_prefix = "#version 120\n#define FRAGMENT 1\n#define PARAMETER_UNIFORM 1\n"
        for p in self.parameters.values():
            if p.name not in declared_params:
                f_prefix += f"uniform float {p.name};\n"

        f_shader = QOpenGLShader(QOpenGLShader.ShaderTypeBit.Fragment)
        if not f_shader.compileSourceCode(f_prefix + clean_source):
            print(f"[SHADER ERROR] Fragment compilation failed for {self.name}:\n{f_shader.log()}")
            return False

        program.addShader(v_shader)
        program.addShader(f_shader)
        program.bindAttributeLocation("VertexCoord", 0)
        program.bindAttributeLocation("TexCoord", 1)

        if not program.link():
            print(f"[SHADER ERROR] Program link failed for {self.name}:\n{program.log()}")
            return False

        self.program = program
        return True



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

# =============================================================================
# BUILT-IN RETROARCH SHADER PRESETS (Universal GLSL 1.20 Compatible)
# =============================================================================

STOCK_SHADER_GLSL = """
#if defined(VERTEX)
uniform mat4 MVPMatrix;
attribute vec4 VertexCoord;
attribute vec4 TexCoord;
varying vec4 TEX0;

void main() {
    gl_Position = MVPMatrix * VertexCoord;
    TEX0 = TexCoord;
}

#elif defined(FRAGMENT)
uniform sampler2D Texture;
varying vec4 TEX0;

void main() {
    gl_FragColor = texture2D(Texture, TEX0.xy);
}
#endif
"""

GBA_COLOR_LCD_GLSL = """
#pragma parameter target_gamma "Target Gamma" 2.2 1.0 3.0 0.1
#pragma parameter display_gamma "Display Gamma" 2.2 1.0 3.0 0.1
#pragma parameter sat "Color Saturation" 1.15 0.5 2.0 0.05
#pragma parameter grid_strength "LCD Grid Darkness" 0.18 0.0 0.5 0.02

#if defined(VERTEX)
uniform mat4 MVPMatrix;
attribute vec4 VertexCoord;
attribute vec4 TexCoord;
varying vec4 TEX0;

void main() {
    gl_Position = MVPMatrix * VertexCoord;
    TEX0 = TexCoord;
}

#elif defined(FRAGMENT)
uniform sampler2D Texture;
uniform vec2 TextureSize;
uniform vec2 OutputSize;
varying vec4 TEX0;

#ifdef PARAMETER_UNIFORM
uniform float target_gamma;
uniform float display_gamma;
uniform float sat;
uniform float grid_strength;
#else
#define target_gamma 2.2
#define display_gamma 2.2
#define sat 1.15
#define grid_strength 0.18
#endif

const mat3 gba_color_matrix = mat3(
    0.84, 0.08, 0.08,
    0.16, 0.72, 0.12,
    0.08, 0.12, 0.80
);

void main() {
    vec4 c = texture2D(Texture, TEX0.xy);
    vec3 rgb = pow(c.rgb, vec3(target_gamma));
    rgb = gba_color_matrix * rgb;

    float luma = dot(rgb, vec3(0.299, 0.587, 0.114));
    rgb = mix(vec3(luma), rgb, sat);
    rgb = pow(clamp(rgb, 0.0, 1.0), vec3(1.0 / display_gamma));

    vec2 coord = TEX0.xy * TextureSize;
    float gx = step(0.10, fract(coord.x));
    float gy = step(0.10, fract(coord.y));
    float grid_factor = 1.0 - (grid_strength * (1.0 - (gx * gy)));

    gl_FragColor = vec4(rgb * grid_factor, 1.0);
}
#endif
"""

CRT_EASYMODE_GLSL = """
#pragma parameter SCANLINE_STRENGTH "Scanline Intensity" 0.45 0.0 1.0 0.05
#pragma parameter MASK_STRENGTH "Phosphor Mask" 0.25 0.0 1.0 0.05
#pragma parameter BRIGHT_BOOST "Brightness Boost" 1.20 0.5 2.0 0.05

#if defined(VERTEX)
uniform mat4 MVPMatrix;
attribute vec4 VertexCoord;
attribute vec4 TexCoord;
varying vec4 TEX0;

void main() {
    gl_Position = MVPMatrix * VertexCoord;
    TEX0 = TexCoord;
}

#elif defined(FRAGMENT)
uniform sampler2D Texture;
uniform vec2 TextureSize;
uniform vec2 OutputSize;
varying vec4 TEX0;

#ifdef PARAMETER_UNIFORM
uniform float SCANLINE_STRENGTH;
uniform float MASK_STRENGTH;
uniform float BRIGHT_BOOST;
#else
#define SCANLINE_STRENGTH 0.45
#define MASK_STRENGTH 0.25
#define BRIGHT_BOOST 1.20
#endif

void main() {
    vec4 color = texture2D(Texture, TEX0.xy);
    
    float scanline = sin(TEX0.y * TextureSize.y * 3.14159265 * 2.0);
    scanline = (0.5 + 0.5 * scanline);
    color.rgb *= mix(1.0, scanline, SCANLINE_STRENGTH);

    float mask = mod(gl_FragCoord.x, 3.0);
    vec3 mask_weight = vec3(1.0);
    if (mask < 1.0) mask_weight = vec3(1.0, 0.7, 0.7);
    else if (mask < 2.0) mask_weight = vec3(0.7, 1.0, 0.7);
    else mask_weight = vec3(0.7, 0.7, 1.0);
    
    color.rgb = mix(color.rgb, color.rgb * mask_weight, MASK_STRENGTH);
    color.rgb *= BRIGHT_BOOST;

    gl_FragColor = clamp(color, 0.0, 1.0);
}
#endif
"""

SCANLINES_CLASSIC_GLSL = """
#pragma parameter scanline_intensity "Scanline Intensity" 0.35 0.0 1.0 0.05

#if defined(VERTEX)
uniform mat4 MVPMatrix;
attribute vec4 VertexCoord;
attribute vec4 TexCoord;
varying vec4 TEX0;

void main() {
    gl_Position = MVPMatrix * VertexCoord;
    TEX0 = TexCoord;
}

#elif defined(FRAGMENT)
uniform sampler2D Texture;
uniform vec2 TextureSize;
varying vec4 TEX0;

#ifdef PARAMETER_UNIFORM
uniform float scanline_intensity;
#else
#define scanline_intensity 0.35
#endif

void main() {
    vec4 color = texture2D(Texture, TEX0.xy);
    float pos = fract(TEX0.y * TextureSize.y);
    float weight = 1.0 - (scanline_intensity * step(0.5, pos));
    gl_FragColor = vec4(color.rgb * weight, color.a);
}
#endif
"""

SHARP_BILINEAR_GLSL = """
#if defined(VERTEX)
uniform mat4 MVPMatrix;
attribute vec4 VertexCoord;
attribute vec4 TexCoord;
varying vec4 TEX0;

void main() {
    gl_Position = MVPMatrix * VertexCoord;
    TEX0 = TexCoord;
}

#elif defined(FRAGMENT)
uniform sampler2D Texture;
uniform vec2 TextureSize;
uniform vec2 OutputSize;
varying vec4 TEX0;

void main() {
    vec2 texel = TEX0.xy * TextureSize;
    vec2 center = floor(texel) + 0.5;
    vec2 f = texel - center + 0.5;
    
    vec2 scale = OutputSize / TextureSize;
    vec2 w = f * scale;
    vec2 p = clamp(w, 0.0, 0.5) + clamp(w - scale + 0.5, 0.0, 0.5);
    vec2 uv = (center + p - 0.5) / TextureSize;

    gl_FragColor = texture2D(Texture, uv);
}
#endif
"""



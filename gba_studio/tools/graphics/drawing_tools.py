# drawing_tools.py
from collections import deque
from PIL import Image


class DrawingToolsMixin:
    def render_tileset_image(self) -> Image.Image:
        bpp_mult = 64 if self.is_8bpp else 32
        base_offset = 0 if self.selected_cbb == 6 else (self.selected_cbb * 0x4000)
        total_tiles = self.get_current_total_tiles()

        tiles_per_row = 32
        rows = max(32, (total_tiles + tiles_per_row - 1) // tiles_per_row)

        img = Image.new("RGBA", (tiles_per_row * 8, rows * 8), (20, 20, 20, 255))
        for t in range(total_tiles):
            tx = (t % tiles_per_row) * 8
            ty = (t // tiles_per_row) * 8
            t_offset = base_offset + (t * bpp_mult)
            if t_offset + bpp_mult > len(self.vram_copy):
                break
            t_data = self.vram_copy[t_offset:t_offset + bpp_mult]

            for py in range(8):
                for px in range(8):
                    if not self.is_8bpp:
                        b = t_data[py * 4 + px // 2]
                        c_idx = (b & 0xF) if (px % 2 == 0) else ((b >> 4) & 0xF)
                    else:
                        c_idx = t_data[py * 8 + px]
                    rgb = self.get_color_for_index(c_idx)
                    img.putpixel((tx + px, ty + py), (*rgb, 255))
        return img

    # ==========================================================================
    # Drawing & Pixel Editing Operations
    # ==========================================================================
    def apply_brush(self, center_x: int, center_y: int, erase: bool = False):
        color_val = 0 if erase else self.selected_color_idx
        r = self.brush_size // 2
        w, h = self.get_active_dimensions()

        for dy in range(-r, self.brush_size - r):
            for dx in range(-r, self.brush_size - r):
                nx, ny = center_x + dx, center_y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    self.set_pixel_index(nx, ny, color_val)

        self.zoom_canvas.update()
        self.tileset_grid.update()

    def pick_color(self, x: int, y: int):
        c_idx = self.get_pixel_index(x, y)
        self.select_color_index(c_idx)
        self.set_tool("pencil")

    def apply_flood_fill(self, start_x: int, start_y: int):
        target_idx = self.get_pixel_index(start_x, start_y)
        rep_idx = self.selected_color_idx
        if target_idx == rep_idx:
            return

        w, h = self.get_active_dimensions()
        queue = deque([(start_x, start_y)])
        visited = {(start_x, start_y)}

        self.begin_stroke()
        try:
            while queue:
                cx, cy = queue.popleft()
                if self.get_pixel_index(cx, cy) == target_idx:
                    self.set_pixel_index(cx, cy, rep_idx)
                    for nx, ny in [(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)]:
                        if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                            visited.add((nx, ny))
                            queue.append((nx, ny))
        finally:
            self.commit_stroke()

        self.zoom_canvas.update()
        self.tileset_grid.update()

    def apply_line(self, x0: int, y0: int, x1: int, y1: int):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy

        self.begin_stroke()
        try:
            while True:
                self.apply_brush(x0, y0)
                if x0 == x1 and y0 == y1:
                    break
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    x0 += sx
                if e2 < dx:
                    err += dx
                    y0 += sy
        finally:
            self.commit_stroke()

    def apply_rect(self, x0: int, y0: int, x1: int, y1: int, fill: bool = False):
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)

        self.begin_stroke()
        try:
            if fill:
                for y in range(ry, ry + rh + 1):
                    for x in range(rx, rx + rw + 1):
                        self.apply_brush(x, y)
            else:
                for x in range(rx, rx + rw + 1):
                    self.apply_brush(x, ry)
                    self.apply_brush(x, ry + rh)
                for y in range(ry, ry + rh + 1):
                    self.apply_brush(rx, y)
                    self.apply_brush(rx + rw, y)
        finally:
            self.commit_stroke()

    def apply_ellipse(self, x0: int, y0: int, x1: int, y1: int, fill: bool = False):
        rx, ry = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w == 0 or h == 0:
            return

        cx, cy = rx + w / 2, ry + h / 2
        rad_x, rad_y = w / 2, h / 2

        self.begin_stroke()
        try:
            for y in range(ry, ry + h + 1):
                for x in range(rx, rx + w + 1):
                    norm = ((x - cx) / rad_x) ** 2 + ((y - cy) / rad_y) ** 2
                    if fill and norm <= 1.0:
                        self.apply_brush(x, y)
                    elif not fill and abs(norm - 1.0) <= 0.15:
                        self.apply_brush(x, y)
        finally:
            self.commit_stroke()
# widgets.py
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QImage, QPainter, QColor, QPen
from PyQt6.QtCore import Qt


class ZoomedCanvas(QWidget):
    """Enlarged pixel editor for the currently active tile or sprite."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setMouseTracking(True)
        self.drag_start_pos = None
        self.temp_draw_pos = None
        self._painting_stroke = False

    def get_zoom(self):
        w, _ = self.editor.get_active_dimensions()
        return max(4, 256 // max(1, w))

    def sizeHint(self):
        w, h = self.editor.get_active_dimensions()
        z = self.get_zoom()
        self.setFixedSize(w * z, h * z)
        return super().sizeHint()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            z = self.get_zoom()
            x = int(event.position().x() // z)
            y = int(event.position().y() // z)
            w, h = self.editor.get_active_dimensions()

            if 0 <= x < w and 0 <= y < h:
                self.drag_start_pos = (x, y)
                t = self.editor.current_tool
                if t in ["pencil", "eraser"]:
                    self.editor.begin_stroke()
                    self._painting_stroke = True
                    self.editor.apply_brush(x, y, erase=(t == "eraser"))
                elif t == "eyedropper":
                    self.editor.pick_color(x, y)
                elif t == "bucket":
                    self.editor.apply_flood_fill(x, y)
                self.update()

    def mouseMoveEvent(self, event):
        z = self.get_zoom()
        x = int(event.position().x() // z)
        y = int(event.position().y() // z)
        w, h = self.editor.get_active_dimensions()

        if 0 <= x < w and 0 <= y < h:
            if event.buttons() & Qt.MouseButton.LeftButton:
                t = self.editor.current_tool
                if t in ["pencil", "eraser"]:
                    if not self._painting_stroke:
                        self.editor.begin_stroke()
                        self._painting_stroke = True
                    self.editor.apply_brush(x, y, erase=(t == "eraser"))
                elif t in ["line", "rect_stroke", "rect_fill", "ellipse_stroke", "ellipse_fill"]:
                    self.temp_draw_pos = (x, y)
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._painting_stroke:
                self._painting_stroke = False
                self.editor.commit_stroke()

            if self.drag_start_pos:
                z = self.get_zoom()
                w, h = self.editor.get_active_dimensions()
                x = max(0, min(w - 1, int(event.position().x() // z)))
                y = max(0, min(h - 1, int(event.position().y() // z)))

                t = self.editor.current_tool
                if t == "line":
                    self.editor.apply_line(self.drag_start_pos[0], self.drag_start_pos[1], x, y)
                elif t == "rect_stroke":
                    self.editor.apply_rect(self.drag_start_pos[0], self.drag_start_pos[1], x, y, fill=False)
                elif t == "rect_fill":
                    self.editor.apply_rect(self.drag_start_pos[0], self.drag_start_pos[1], x, y, fill=True)
                elif t == "ellipse_stroke":
                    self.editor.apply_ellipse(self.drag_start_pos[0], self.drag_start_pos[1], x, y, fill=False)
                elif t == "ellipse_fill":
                    self.editor.apply_ellipse(self.drag_start_pos[0], self.drag_start_pos[1], x, y, fill=True)

            self.drag_start_pos = None
            self.temp_draw_pos = None
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.editor.get_active_dimensions()
        z = self.get_zoom()

        # 1. Checkerboard pattern
        for y in range(0, self.height(), z):
            for x in range(0, self.width(), z):
                col = QColor(30, 30, 30) if ((x // z) + (y // z)) % 2 == 0 else QColor(45, 45, 45)
                painter.fillRect(x, y, z, z, col)

        # 2. Render Pixels
        for py in range(h):
            for px in range(w):
                c_idx = self.editor.get_pixel_index(px, py)
                rgb = self.editor.get_color_for_index(c_idx)
                if c_idx != 0:
                    painter.fillRect(px * z, py * z, z, z, QColor(*rgb))

        # 3. Shape Previews
        if self.drag_start_pos and self.temp_draw_pos:
            painter.setPen(QPen(QColor(255, 255, 0, 220), 2))
            x1, y1 = self.drag_start_pos[0] * z, self.drag_start_pos[1] * z
            x2, y2 = self.temp_draw_pos[0] * z, self.temp_draw_pos[1] * z
            t = self.editor.current_tool

            if t == "line":
                painter.drawLine(x1, y1, x2, y2)
            elif t in ["rect_stroke", "rect_fill"]:
                rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x2 - x1) + z, abs(y2 - y1) + z
                if t == "rect_fill":
                    painter.fillRect(rx, ry, rw, rh, QColor(255, 255, 0, 100))
                painter.drawRect(rx, ry, rw, rh)
            elif t in ["ellipse_stroke", "ellipse_fill"]:
                rx, ry, rw, rh = min(x1, x2), min(y1, y2), abs(x2 - x1) + z, abs(y2 - y1) + z
                if t == "ellipse_fill":
                    painter.setBrush(QColor(255, 255, 0, 100))
                painter.drawEllipse(rx, ry, rw, rh)

        # 4. Grids
        if self.editor.show_pixel_grid and z >= 6:
            painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
            for x in range(0, self.width(), z):
                painter.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), z):
                painter.drawLine(0, y, self.width(), y)

        if self.editor.show_tile_grid:
            painter.setPen(QPen(QColor(255, 255, 0, 90), 1))
            tz = 8 * z
            for x in range(0, self.width(), tz):
                painter.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), tz):
                painter.drawLine(0, y, self.width(), y)

        painter.end()


class TilesetGridWidget(QWidget):
    """YY-CHR Style Pattern Table with full 32-tile width to fit all VRAM glyphs."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.tiles_per_row = 32
        self.zoom = 2
        self.update_dimensions()

    def update_dimensions(self):
        total_tiles = self.editor.get_current_total_tiles()
        rows = max(32, (total_tiles + self.tiles_per_row - 1) // self.tiles_per_row)
        self.setFixedSize(self.tiles_per_row * 8 * self.zoom, rows * 8 * self.zoom)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            tx = int(event.position().x() // (8 * self.zoom))
            ty = int(event.position().y() // (8 * self.zoom))
            if 0 <= tx < self.tiles_per_row:
                tile_idx = ty * self.tiles_per_row + tx
                self.editor.select_tile(tile_idx)
                self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 20))
        img = self.editor.render_tileset_image()
        if img:
            qimg = QImage(img.tobytes(), img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
            scaled = qimg.scaled(self.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
            painter.drawImage(0, 0, scaled)

        st = self.editor.selected_tile_idx
        sx = (st % self.tiles_per_row) * 8 * self.zoom
        sy = (st // self.tiles_per_row) * 8 * self.zoom
        painter.setPen(QPen(QColor(255, 255, 0), 2))
        painter.drawRect(sx, sy, 8 * self.zoom, 8 * self.zoom)
        painter.end()
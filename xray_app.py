"""
X-Ray-lab v0.0.6
PyQt5 + editable .ui files (open in Qt Designer).
Color scheme from MolPlayer/constants.py (Laby.docx palette).
"""

import sys
import os
import json
import math
import hashlib
import tempfile
import urllib.request
import urllib.error
import zipfile
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageChops, ImageFilter

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QFileDialog, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsLineItem, QVBoxLayout, QHBoxLayout, QButtonGroup,
    QPushButton, QLabel, QDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QTextBrowser
)
from PyQt5.QtGui import (
    QPixmap, QImage, QPen, QColor, QIcon, QPainter, QDesktopServices, QPolygonF
)
from PyQt5.QtCore import Qt, QEvent, QPointF, QRectF, QTimer, pyqtSignal, pyqtSlot, QUrl
from PyQt5 import uic

from constants import (
    BG_DARK, BG_PANEL, BG_TRACK, BTN_BG, BTN_HOVER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_GLOW, ACCENT_FRAME, DEPTH_BLUE, BORDER,
    APP_NAME, APP_DISPLAY_NAME, APP_VERSION, GITHUB_REPO, get_app_stylesheet
)

def get_resource_path(relative_path: str) -> Path:
    """Robust path resolver for both development and PyInstaller frozen onedir builds.
    In frozen builds, data files (ui/, icon, resources/) live under sys._MEIPASS.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base / relative_path

# ---------------- Persistence (minimal, like MolPlayer style) ----------------
def get_app_data_dir() -> Path:
    local = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home())
    d = Path(local) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d

STATE_FILE = get_app_data_dir() / "state.json"

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(data: dict):
    try:
        STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[state] save error: {e}")

# ---------------- Image processing (Pillow) ----------------
def pil_to_pixmap(pil_img: Image.Image) -> QPixmap:
    """Convert PIL image to QPixmap. Uses .copy() so buffer is not freed early (fixes crash)."""
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    w, h = pil_img.size
    data = pil_img.tobytes("raw", "RGB")
    qimg = QImage(data, w, h, w * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())

def square_corners_from_diagonal(
    x1: float, y1: float, x2: float, y2: float
) -> Optional[List[Tuple[float, float]]]:
    """Квадрат: отрезок (x1,y1)-(x2,y2) — диагональ. Возвращает 4 угла."""
    d = math.hypot(x2 - x1, y2 - y1)
    if d < 1e-6:
        return None
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    wx, wy = -(y2 - y1) / d, (x2 - x1) / d
    # Both diagonals of a square have the same length and cross in the middle.
    off = d / 2.0
    return [
        (x1, y1),
        (cx + wx * off, cy + wy * off),
        (x2, y2),
        (cx - wx * off, cy - wy * off),
    ]


def point_in_polygon(px: float, py: float, poly: List[Tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > py) != (y2 > py)) and (
            px < (x2 - x1) * (py - y1) / (y2 - y1 + 1e-12) + x1
        ):
            inside = not inside
    return inside


def connected_component_areas(mask: bytearray, width: int, height: int) -> List[int]:
    """Return 8-connected white component areas without modifying the source mask."""
    remaining = bytearray(mask)
    result: List[int] = []

    for start in range(width * height):
        if not remaining[start]:
            continue
        remaining[start] = 0
        queue = deque([start])
        area = 0
        while queue:
            index = queue.popleft()
            area += 1
            x = index % width
            y = index // width
            for ny in range(max(0, y - 1), min(height, y + 2)):
                row = ny * width
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = row + nx
                    if remaining[neighbor]:
                        remaining[neighbor] = 0
                        queue.append(neighbor)
        result.append(area)
    return result


def filter_inclusion_components(
    mask: bytearray,
    roi_mask: bytearray,
    width: int,
    height: int,
    min_area: int = 2,
    grayscale: Optional[Image.Image] = None,
    min_surrounding_contrast: float = 8.0,
) -> Tuple[bytearray, List[int]]:
    """Keep compact dark spots surrounded by brighter pixels in every direction."""
    remaining = bytearray(mask)
    filtered = bytearray(width * height)
    areas: List[int] = []
    gray_pixels = grayscale.load() if grayscale is not None else None

    for start in range(width * height):
        if not remaining[start]:
            continue
        remaining[start] = 0
        queue = deque([start])
        component: List[int] = []
        touches_boundary = False
        while queue:
            index = queue.popleft()
            component.append(index)
            x = index % width
            y = index // width

            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or ny < 0 or nx >= width or ny >= height:
                    touches_boundary = True
                elif not roi_mask[ny * width + nx]:
                    touches_boundary = True

            for ny in range(max(0, y - 1), min(height, y + 2)):
                row = ny * width
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = row + nx
                    if remaining[neighbor]:
                        remaining[neighbor] = 0
                        queue.append(neighbor)

        surrounded = True
        if gray_pixels is not None and component:
            xs = [index % width for index in component]
            ys = [index // width for index in component]
            center_x = round(sum(xs) / len(xs))
            center_y = round(sum(ys) / len(ys))
            box_width = max(xs) - min(xs) + 1
            box_height = max(ys) - min(ys) + 1
            radius = max(5, max(box_width, box_height) + 2)
            component_values = sorted(gray_pixels[index % width, index // width] for index in component)
            dark_half = component_values[:max(1, len(component_values) // 2)]
            center_value = sum(dark_half) / len(dark_half)
            brighter_directions = 0
            for dx, dy in (
                (1, 0), (-1, 0), (0, 1), (0, -1),
                (1, 1), (1, -1), (-1, 1), (-1, -1),
            ):
                sample_x = center_x + dx * radius
                sample_y = center_y + dy * radius
                if not (0 <= sample_x < width and 0 <= sample_y < height):
                    continue
                if not roi_mask[sample_y * width + sample_x]:
                    continue
                patch = []
                for py in range(max(0, sample_y - 1), min(height, sample_y + 2)):
                    for px in range(max(0, sample_x - 1), min(width, sample_x + 2)):
                        if roi_mask[py * width + px]:
                            patch.append(gray_pixels[px, py])
                if patch and sum(patch) / len(patch) - center_value >= min_surrounding_contrast:
                    brighter_directions += 1
            surrounded = brighter_directions >= 7

        if len(component) >= min_area and not touches_boundary and surrounded:
            areas.append(len(component))
            for index in component:
                filtered[index] = 1

    return filtered, areas


def histogram_percentile(histogram: List[int], percentile: float) -> int:
    total = sum(histogram)
    if total <= 0:
        return 0
    target = max(1, math.ceil(total * percentile))
    cumulative = 0
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return value
    return len(histogram) - 1


def process_inclusions(
    original: Image.Image,
    line_points: Optional[Tuple[Tuple[int, int], Tuple[int, int]]],
    container_type: str,   # "square" or "cylinder"
    contrast_threshold: Optional[int] = None,
) -> Tuple[Image.Image, Image.Image, int, List[int]]:
    """
    Returns (original_rgb, processed_rgb, white_pixel_count_inside_mask,
    connected_component_areas_px).
    Dark inclusions are detected relative to a blurred local background. This
    suppresses broad exposure gradients and unevenly illuminated corners.
    The returned processed image keeps the original and highlights findings.
    """
    if original.mode != "L":
        gray = original.convert("L")
    else:
        gray = original
    w, h = gray.size
    orig_rgb = original.convert("RGB") if original.mode != "RGB" else original

    square_poly: Optional[List[Tuple[float, float]]] = None
    circle_params: Optional[Tuple[float, float, float]] = None  # cx, cy, r

    if line_points and len(line_points) == 2:
        (x1, y1), (x2, y2) = line_points
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 3:
            length = max(w, h) * 0.8
            cx = w / 2.0
            cy = h / 2.0
            x1, y1 = int(cx - length / 2), int(cy)
            x2, y2 = int(cx + length / 2), int(cy)

        if container_type == "square":
            square_poly = square_corners_from_diagonal(x1, y1, x2, y2)
        else:
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            circle_params = (cx, cy, length / 2.0)

    roi_mask = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            in_roi = True
            if square_poly is not None:
                in_roi = point_in_polygon(x + 0.5, y + 0.5, square_poly)
            elif circle_params is not None:
                cx, cy, r = circle_params
                in_roi = (x - cx) ** 2 + (y - cy) ** 2 <= r * r

            if in_roi:
                roi_mask[y * w + x] = 255

    spot_window = max(9, min(21, 2 * round(min(w, h) / 80) + 1))
    local_background = gray.filter(ImageFilter.MaxFilter(spot_window)).filter(
        ImageFilter.MinFilter(spot_window)
    )
    local_contrast = ImageChops.subtract(local_background, gray)
    roi_image = Image.frombytes("L", (w, h), bytes(roi_mask))
    histogram = local_contrast.histogram(mask=roi_image)
    median = histogram_percentile(histogram, 0.5)
    deviation_histogram = [0] * 256
    for value, count in enumerate(histogram):
        deviation_histogram[abs(value - median)] += count
    mad = histogram_percentile(deviation_histogram, 0.5)
    threshold = contrast_threshold or max(18, round(median + 4.5 * 1.4826 * mad))

    contrast_pixels = local_contrast.load()
    raw_mask = bytearray(w * h)
    binary_roi = bytearray(1 if value else 0 for value in roi_mask)
    for y in range(h):
        row = y * w
        for x in range(w):
            if binary_roi[row + x] and contrast_pixels[x, y] >= threshold:
                raw_mask[row + x] = 1

    inclusion_mask, component_areas = filter_inclusion_components(
        raw_mask, binary_roi, w, h, grayscale=gray
    )
    white_count = sum(component_areas)

    # Enlarge only the visual marker; volume calculations use the original mask.
    marker = Image.frombytes(
        "L", (w, h), bytes(255 if value else 0 for value in inclusion_mask)
    ).filter(ImageFilter.MaxFilter(5))
    marker = ImageChops.multiply(marker, roi_image)
    cyan = Image.new("RGB", (w, h), (0, 191, 255))
    processed = Image.composite(cyan, orig_rgb, marker)

    return orig_rgb, processed, white_count, component_areas

# ---------------- Interactive image view (line drawing via eventFilter) ----------------
# Note: We use plain QGraphicsView from the .ui file + eventFilter on the viewport
# for line drawing. No need for a promoted custom subclass in v1.

def ver_tuple(v: str) -> tuple:
    v = v.lstrip("vV")
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except Exception:
        return (0, 0, 0)


def github_request(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"{APP_NAME}-Updater/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_release_asset(asset: dict, dest_path: str):
    url = asset.get("browser_download_url")
    if not url:
        raise ValueError("У файла релиза отсутствует ссылка для скачивания")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"{APP_NAME}-Updater/{APP_VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        with open(dest_path, "wb") as out:
            shutil.copyfileobj(resp, out)

    digest = asset.get("digest", "")
    if digest.startswith("sha256:"):
        expected = digest.split(":", 1)[1].lower()
        sha256 = hashlib.sha256()
        with open(dest_path, "rb") as downloaded:
            for chunk in iter(lambda: downloaded.read(1024 * 1024), b""):
                sha256.update(chunk)
        if sha256.hexdigest().lower() != expected:
            os.remove(dest_path)
            raise ValueError("Контрольная сумма скачанного файла не совпала")


def safe_extract_zip(zip_path: str, dest_dir: str):
    """Extract a release archive while rejecting traversal and symlink entries."""
    destination = Path(dest_dir).resolve()
    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            member = Path(info.filename)
            target = (destination / member).resolve()
            if member.is_absolute() or destination not in target.parents and target != destination:
                raise ValueError(f"Недопустимый путь в архиве: {info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"Символические ссылки в обновлении запрещены: {info.filename}")
        archive.extractall(destination)


def find_portable_asset(release_data: dict) -> Optional[dict]:
    assets = release_data.get("assets", [])
    versioned = []
    generic = []
    other = []
    for asset in assets:
        name = asset.get("name", "")
        if not asset.get("browser_download_url") or not name.lower().endswith(".zip"):
            continue
        low = name.lower()
        if "portable" in low and "-v" in low:
            versioned.append(asset)
        elif low == "xray_labs-portable.zip":
            generic.append(asset)
        elif "portable" in low:
            other.append(asset)
        else:
            other.append(asset)
    for bucket in (versioned, generic, other):
        if bucket:
            return bucket[0]
    return None


def find_setup_asset(release_data: dict) -> Optional[dict]:
    for asset in release_data.get("assets", []):
        name = asset.get("name", "").lower()
        if name.endswith("-setup.exe") and asset.get("browser_download_url"):
            return asset
    return None


def get_release_install_mode() -> str:
    """Installed builds use the installer; unpacked builds update in place."""
    if not getattr(sys, "frozen", False):
        return "installer"
    app_dir = Path(get_app_install_dir())
    if any(app_dir.glob("unins*.exe")):
        return "installer"
    return "portable"


def get_app_install_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_extracted_app_dir(extract_dir: str) -> str:
    direct = os.path.join(extract_dir, "Xray_labs")
    if os.path.isfile(os.path.join(direct, "Xray_labs.exe")):
        return direct
    for root, _, files in os.walk(extract_dir):
        if "Xray_labs.exe" in files:
            return root
    return extract_dir


def calc_inclusion_volume_mm3(component_areas_mm2: List[float], incl_type: str) -> float:
    """Volume from each connected inclusion area according to Laby.docx."""
    areas = [area for area in component_areas_mm2 if area > 0]
    if not areas:
        return 0.0
    summed = sum(area ** 1.5 for area in areas)
    if incl_type == "cubic":
        return summed
    return (4.0 / (3.0 * math.sqrt(math.pi))) * summed


def make_pen(color: str = "#00bfff", width: int = 3) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidth(width)
    pen.setCosmetic(True)
    return pen


# ---------------- Lab 1 Window ----------------
class Lab1Window(QMainWindow):
    def __init__(self, main_window=None):
        super().__init__(None)
        self._main_window = main_window
        uic_path = get_resource_path("ui/lab1_window.ui")
        uic.loadUi(str(uic_path), self)

        self.setFixedSize(1000, 660)
        self.setWindowIcon(QIcon(str(get_resource_path("icon_cat.ico"))))

        self.setStyleSheet(get_app_stylesheet())

        # State
        self.original_pil: Optional[Image.Image] = None
        self.line_points: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None
        self.current_line_item: Optional[QGraphicsLineItem] = None
        self.white_px_count: int = 0
        self.component_px_areas: List[int] = []
        self._processed_signature = None
        self._dragging_line = False
        self._line_start: Optional[QPointF] = None
        self._showing_processed = False
        self._volumes_calculated = False

        self.image_scene = QGraphicsScene(self)
        self.imageView.setScene(self.image_scene)
        self.imageView.setRenderHint(QPainter.Antialiasing)
        self.imageView.viewport().installEventFilter(self)

        for layout in (self.containerRadios, self.inclRadios):
            layout.setAlignment(Qt.AlignHCenter)

        self._container_group = QButtonGroup(self)
        self._container_group.addButton(self.squareRadio)
        self._container_group.addButton(self.cylRadio)
        self._container_group.setExclusive(True)

        self._incl_group = QButtonGroup(self)
        self._incl_group.addButton(self.cubicRadio)
        self._incl_group.addButton(self.sphereRadio)
        self._incl_group.setExclusive(True)

        self.squareRadio.toggled.connect(self._on_container_changed)
        self.cylRadio.toggled.connect(self._on_container_changed)

        # Buttons
        self.openBtn.clicked.connect(self._open_file)
        self.findInclusionsBtn.clicked.connect(self._find_inclusions)
        self.calcVolumesBtn.clicked.connect(self._calculate_volumes)
        self.metodikaBtn.clicked.connect(lambda: self._open_pdf_placeholder("metodika.pdf"))
        self.protokolBtn.clicked.connect(lambda: self._open_pdf_placeholder("protokol.pdf"))

        # Initial instruction
        self._update_instruction()

        self._restore_last_values()

    def showEvent(self, event):
        super().showEvent(event)
        if self._main_window:
            geo = self._main_window.frameGeometry()
            self.move(geo.x() + 48, geo.y() + 48)
        self.raise_()
        self.activateWindow()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.raise_()

    def _get_container_type(self) -> str:
        return "square" if self.squareRadio.isChecked() else "cylinder"

    def _update_instruction(self):
        if self._get_container_type() == "square":
            self.instructionLabel.setText(
                "Укажите курсором мыши диагональ квадратного контейнера на снимке "
                "и введите её параметры"
            )
            self.diamLabel.setText("Диагональ")
        else:
            self.instructionLabel.setText(
                "Укажите курсором мыши диаметр цилиндрического контейнера на снимке "
                "и введите его параметры"
            )
            self.diamLabel.setText("Диаметр")

    def _on_container_changed(self, checked: bool):
        if not checked:
            return
        self._update_instruction()
        self.white_px_count = 0
        self.component_px_areas = []
        self._processed_signature = None
        self._reset_volume_labels()
        if self._showing_processed and self.original_pil is not None:
            self._showing_processed = False
            pix = pil_to_pixmap(self.original_pil)
            self._display_pixmap(pix, draw_line=True)

    def _restore_last_values(self):
        st = load_state()
        self.squareRadio.blockSignals(True)
        self.cylRadio.blockSignals(True)
        self.cubicRadio.blockSignals(True)
        self.sphereRadio.blockSignals(True)
        try:
            if "last_diam" in st:
                self.diamEdit.setText(str(st["last_diam"]))
            if "last_thick" in st:
                self.thickEdit.setText(str(st["last_thick"]))
            if st.get("last_container") == "square":
                self.squareRadio.setChecked(True)
            else:
                self.cylRadio.setChecked(True)
            if st.get("last_incl") == "cubic":
                self.cubicRadio.setChecked(True)
            else:
                self.sphereRadio.setChecked(True)
        finally:
            self.squareRadio.blockSignals(False)
            self.cylRadio.blockSignals(False)
            self.cubicRadio.blockSignals(False)
            self.sphereRadio.blockSignals(False)
        self._update_instruction()

    def _save_last_values(self):
        st = load_state()
        try:
            if self.diamEdit.text().strip():
                st["last_diam"] = int(self.diamEdit.text().strip())
            if self.thickEdit.text().strip():
                st["last_thick"] = int(self.thickEdit.text().strip())
            st["last_container"] = self._get_container_type()
            st["last_incl"] = "cubic" if self.cubicRadio.isChecked() else "sphere"
            save_state(st)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj is self.imageView.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if self.original_pil is None or self._showing_processed:
                    return False
                self._dragging_line = True
                self._line_start = self.imageView.mapToScene(event.pos())
                if self.current_line_item:
                    self.image_scene.removeItem(self.current_line_item)
                    self.current_line_item = None
                return True

            elif event.type() == QEvent.MouseMove and self._dragging_line and self._line_start:
                cur = self.imageView.mapToScene(event.pos())
                if self.current_line_item:
                    self.image_scene.removeItem(self.current_line_item)
                pen = make_pen(ACCENT_GLOW, 2)
                self.current_line_item = self.image_scene.addLine(
                    self._line_start.x(), self._line_start.y(),
                    cur.x(), cur.y(), pen
                )
                return True

            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._dragging_line:
                self._dragging_line = False
                end = self.imageView.mapToScene(event.pos())
                start = self._line_start
                self._line_start = None

                if self.current_line_item:
                    self.image_scene.removeItem(self.current_line_item)

                pen = make_pen("#00bfff", 3)
                self.current_line_item = self.image_scene.addLine(
                    start.x(), start.y(), end.x(), end.y(), pen
                )

                x1, y1 = int(start.x()), int(start.y())
                x2, y2 = int(end.x()), int(end.y())
                self.line_points = ((x1, y1), (x2, y2))
                self.white_px_count = 0
                self.component_px_areas = []
                self._processed_signature = None
                self._reset_volume_labels()
                return True

        return super().eventFilter(obj, event)

    def _reset_volume_labels(self):
        self._volumes_calculated = False
        self.resultPorodaLabel.setText("Объем породы:")
        self.resultInclLabel.setText("Объем включений:")

    def _display_pixmap(self, pix: QPixmap, draw_line: bool = False):
        self.image_scene.clear()
        item = QGraphicsPixmapItem(pix)
        self.image_scene.addItem(item)
        self.image_scene.setSceneRect(QRectF(0, 0, pix.width(), pix.height()))
        if draw_line and self.line_points:
            (x1, y1), (x2, y2) = self.line_points
            pen = make_pen("#00bfff", 3)
            self.current_line_item = self.image_scene.addLine(x1, y1, x2, y2, pen)
        self.imageView.fitInView(item, Qt.KeepAspectRatio)

    def _open_file(self):
        st = load_state()
        start_dir = st.get("last_open_dir", "")
        if start_dir and not os.path.isdir(start_dir):
            start_dir = ""

        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть снимок", start_dir,
            "Images (*.jpg *.jpeg *.bmp *.png);;All files (*)"
        )
        if not path:
            return

        try:
            with Image.open(path) as img:
                self.original_pil = img.convert("RGB").copy()
            self.line_points = None
            self.current_line_item = None
            self.white_px_count = 0
            self.component_px_areas = []
            self._processed_signature = None
            self._showing_processed = False
            self._reset_volume_labels()

            pix = pil_to_pixmap(self.original_pil)
            self._display_pixmap(pix, draw_line=False)

            st["last_image"] = path
            st["last_open_dir"] = str(Path(path).parent)
            save_state(st)

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть изображение:\n{e}")

    def _find_inclusions(self):
        if self.original_pil is None:
            QMessageBox.information(self, "Нет снимка", "Сначала откройте файл.")
            return False
        if self.line_points is None:
            QMessageBox.information(
                self, "Не задан контейнер",
                "Сначала проведите на снимке диагональ квадрата или диаметр цилиндра."
            )
            return False
        ctype = self._get_container_type()
        try:
            _, proc, white, component_areas = process_inclusions(
                self.original_pil, self.line_points, ctype
            )
            self.white_px_count = white
            self.component_px_areas = component_areas
            self._processed_signature = (self.line_points, ctype)
            self._showing_processed = True
            self.current_line_item = None

            ppix = pil_to_pixmap(proc)
            self.image_scene.clear()
            pitem = QGraphicsPixmapItem(ppix)
            self.image_scene.addItem(pitem)
            self.image_scene.setSceneRect(QRectF(0, 0, ppix.width(), ppix.height()))

            if self.line_points:
                (x1, y1), (x2, y2) = self.line_points
                pen = make_pen(ACCENT_GLOW, 2)
                if ctype == "square":
                    corners = square_corners_from_diagonal(x1, y1, x2, y2)
                    if corners:
                        poly = QPolygonF([QPointF(cx, cy) for cx, cy in corners])
                        self.image_scene.addPolygon(poly, pen)
                else:
                    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                    r = math.hypot(x2 - x1, y2 - y1) / 2
                    self.image_scene.addEllipse(cx - r, cy - r, r * 2, r * 2, pen)

            self.imageView.fitInView(pitem, Qt.KeepAspectRatio)
            self._reset_volume_labels()
            return True
        except Exception as e:
            QMessageBox.critical(self, "Ошибка обработки", str(e))
            return False

    def _calculate_volumes(self):
        if self.original_pil is None:
            QMessageBox.information(self, "Нет данных", "Откройте снимок.")
            return

        # Validate line + inputs
        has_line = self.line_points is not None
        diam_str = self.diamEdit.text().strip()
        thick_str = self.thickEdit.text().strip()

        try:
            real_size = float(diam_str) if diam_str else 0.0
            thick = float(thick_str) if thick_str else 0.0
        except ValueError:
            real_size = 0.0
            thick = 0.0

        if not has_line or real_size <= 0 or thick <= 0 or real_size > 99 or thick > 99:
            QMessageBox.information(
                self, "Недостаточно данных",
                "Котик, не ходи мимо лотка и введи все требуемые данные для расчета"
            )
            return

        # Scale
        (x1, y1), (x2, y2) = self.line_points
        px_len = math.hypot(x2 - x1, y2 - y1)
        if px_len < 1:
            QMessageBox.warning(self, "Линия слишком короткая", "Перерисуйте линию.")
            return

        ctype = self._get_container_type()
        # real_size: диагональ (квадрат) или диаметр (цилиндр) в мм
        scale_mm_per_px = real_size / px_len

        signature = (self.line_points, ctype)
        if self._processed_signature != signature:
            if not self._find_inclusions():
                return
        component_areas_mm2 = [
            area_px * (scale_mm_per_px ** 2) for area_px in self.component_px_areas
        ]

        if ctype == "square":
            side_mm = real_size / math.sqrt(2.0)
            container_mm3 = side_mm * side_mm * thick
        else:
            r = real_size / 2.0
            container_mm3 = math.pi * r * r * thick

        incl_type = "cubic" if self.cubicRadio.isChecked() else "sphere"
        incl_mm3 = calc_inclusion_volume_mm3(component_areas_mm2, incl_type)
        poroda_mm3 = max(0.0, container_mm3 - incl_mm3)

        poroda_cm3 = poroda_mm3 / 1000.0

        self._volumes_calculated = True
        self.resultPorodaLabel.setText(f"Объем породы: {poroda_cm3:.2f} см³")
        self.resultInclLabel.setText(f"Объем включений: {incl_mm3:.2f} мм³")
        self._save_last_values()

    def _open_pdf_placeholder(self, filename: str):
        pdf_path = get_resource_path(f"resources/{filename}")
        if pdf_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf_path)))
        else:
            QMessageBox.information(
                self, "PDF",
                f"Файл {filename} не найден.\n\n"
                "Поместите его в папку resources/ (рядом с exe или в исходниках).\n"
                "Позже можно вшить в сборку."
            )

    def closeEvent(self, event):
        self._save_last_values()
        super().closeEvent(event)


# ---------------- Version manager ----------------
class VersionManagerDialog(QDialog):
    install_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Версии X-Ray-lab")
        self.setWindowIcon(QIcon(str(get_resource_path("icon_cat.ico"))))
        self.setMinimumSize(720, 500)
        self.setStyleSheet(get_app_stylesheet())
        self._releases: List[dict] = []

        layout = QVBoxLayout(self)
        mode = "установщик" if get_release_install_mode() == "installer" else "portable-обновление"
        self.statusLabel = QLabel(
            f"Установлена версия {APP_VERSION}. Режим обновления: {mode}."
        )
        self.statusLabel.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self.statusLabel)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Версия", "Дата", "Статус", "Пакет"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        self.notes = QTextBrowser(self)
        self.notes.setMaximumHeight(150)
        self.notes.setPlaceholderText("Здесь появится описание выбранного релиза")
        layout.addWidget(self.notes)

        buttons = QHBoxLayout()
        self.openReleaseBtn = QPushButton("Открыть на GitHub")
        self.openReleaseBtn.setEnabled(False)
        self.openReleaseBtn.clicked.connect(self._open_release)
        buttons.addWidget(self.openReleaseBtn)
        buttons.addStretch(1)
        self.installBtn = QPushButton("Установить выбранную")
        self.installBtn.setEnabled(False)
        self.installBtn.clicked.connect(self._request_install)
        buttons.addWidget(self.installBtn)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def show_loading(self):
        self.statusLabel.setText("Получаю список версий с GitHub…")
        self.table.setRowCount(0)
        self.installBtn.setEnabled(False)

    def show_error(self, message: str):
        self.statusLabel.setText("Не удалось получить список версий.")
        QMessageBox.warning(self, "Версии", message)

    def set_releases(self, releases: List[dict]):
        self._releases = sorted(
            [release for release in releases if not release.get("draft")],
            key=lambda release: ver_tuple(release.get("tag_name", "")),
            reverse=True,
        )
        self.table.setRowCount(len(self._releases))
        for row, release in enumerate(self._releases):
            tag = release.get("tag_name", "?")
            version = tag.lstrip("vV")
            comparison = ver_tuple(version)
            if comparison == ver_tuple(APP_VERSION):
                state = "установлена"
            elif comparison > ver_tuple(APP_VERSION):
                state = "новее"
            else:
                state = "старее"
            assets = []
            if find_setup_asset(release):
                assets.append("установщик")
            if find_portable_asset(release):
                assets.append("portable")
            values = [version, release.get("published_at", "")[:10], state, ", ".join(assets) or "нет пакета"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row)
                self.table.setItem(row, column, item)

        self.statusLabel.setText(
            f"Установлена версия {APP_VERSION}. Доступно версий: {len(self._releases)}."
        )
        if self._releases:
            self.table.selectRow(0)

    def _selected_release(self) -> Optional[dict]:
        row = self.table.currentRow()
        if 0 <= row < len(self._releases):
            return self._releases[row]
        return None

    def _selection_changed(self):
        release = self._selected_release()
        enabled = release is not None
        self.openReleaseBtn.setEnabled(enabled and bool(release.get("html_url")))
        has_package = enabled and bool(find_setup_asset(release) or find_portable_asset(release))
        self.installBtn.setEnabled(has_package)
        if release:
            tag = release.get("tag_name", "")
            self.installBtn.setText(
                "Переустановить" if ver_tuple(tag) == ver_tuple(APP_VERSION)
                else "Перейти на выбранную версию"
            )
            self.notes.setPlainText(release.get("body") or "Описание релиза отсутствует.")

    def _open_release(self):
        release = self._selected_release()
        if release and release.get("html_url"):
            QDesktopServices.openUrl(QUrl(release["html_url"]))

    def _request_install(self):
        release = self._selected_release()
        if release:
            self.install_requested.emit(release)


# ---------------- Main selector window ----------------
class MainWindow(QMainWindow):
    ui_call = pyqtSignal(object)

    def _on_ui(self, func):
        self.ui_call.emit(func)

    @pyqtSlot(object)
    def _run_on_ui(self, callback):
        callback()

    def __init__(self):
        super().__init__()
        self.ui_call.connect(self._run_on_ui)
        uic_path = get_resource_path("ui/main_window.ui")
        uic.loadUi(str(uic_path), self)

        self._version_dialog: Optional[VersionManagerDialog] = None
        icon_path = get_resource_path("icon_cat.ico")
        try:
            self.setWindowIcon(QIcon(str(icon_path)))
            pix = QPixmap(str(icon_path)).scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.appIconLabel.setPixmap(pix)
        except Exception:
            pass

        self.setStyleSheet(get_app_stylesheet())
        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{APP_VERSION}")
        self.setFixedSize(800, 600)

        self.update_btn = QPushButton("↻", self)
        self.update_btn.setObjectName("smallUpdateBtn")
        self.update_btn.setToolTip("Версии и обновления")
        self.update_btn.setFixedSize(26, 22)
        self.update_btn.clicked.connect(self._open_version_manager)

        try:
            hl = self.headerFrame.layout()
            if hl:
                hl.addWidget(self.update_btn)
        except Exception:
            self.update_btn.move(self.width() - 40, 8)

        self._lab_windows = []
        self._populate_lab_buttons()
        QTimer.singleShot(8000, lambda: self._check_for_updates(silent=True))

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.raise_()

    def _populate_lab_buttons(self):
        container = self.labsContainer
        layout = container.layout()

        # Clear any designer placeholders
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()

        lab_btn_style = (
            f"QPushButton {{ text-align: left; padding-left: 10px; }}"
        )
        disabled_style = (
            f"QPushButton {{ background-color: {BG_TRACK}; color: {TEXT_MUTED}; "
            f"text-align: left; padding-left: 10px; }}"
        )

        btn1 = QPushButton("1. Оценка концентрации вещества рентгеноабсорбционным методом")
        btn1.setMinimumHeight(42)
        btn1.setMaximumHeight(42)
        btn1.setStyleSheet(lab_btn_style)
        btn1.clicked.connect(self._open_lab1)
        layout.addWidget(btn1)

        for i in range(2, 11):
            b = QPushButton(f"{i}. (в разработке)")
            b.setMinimumHeight(42)
            b.setMaximumHeight(42)
            b.setEnabled(False)
            b.setStyleSheet(disabled_style)
            layout.addWidget(b)

        # Small version label at bottom
        ver = QLabel(f"v{APP_VERSION}")
        ver.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 9pt;")
        ver.setAlignment(Qt.AlignRight)
        layout.addWidget(ver)

    def _open_lab1(self):
        lab = Lab1Window(main_window=self)

        def _on_lab_closed(obj=None, lw=lab):
            try:
                self._lab_windows.remove(lw)
            except ValueError:
                pass

        lab.destroyed.connect(_on_lab_closed)
        self._lab_windows.append(lab)
        lab.show()

    def _open_version_manager(self):
        if self._version_dialog and self._version_dialog.isVisible():
            self._version_dialog.raise_()
            self._version_dialog.activateWindow()
            return

        dialog = VersionManagerDialog(self)
        dialog.install_requested.connect(self._install_release)
        dialog.finished.connect(lambda _=None: setattr(self, "_version_dialog", None))
        self._version_dialog = dialog
        dialog.show_loading()
        dialog.show()

        def worker():
            try:
                api = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=100"
                releases = json.loads(github_request(api, timeout=20).decode("utf-8"))
                if not isinstance(releases, list):
                    raise ValueError("GitHub вернул неожиданный ответ")
                self._on_ui(lambda data=releases: dialog.set_releases(data))
            except Exception as error:
                self._on_ui(lambda e=error: dialog.show_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _install_release(self, release: dict):
        tag = release.get("tag_name", "неизвестная версия")
        mode = get_release_install_mode()
        asset = find_setup_asset(release) if mode == "installer" else find_portable_asset(release)
        if asset is None:
            asset = find_portable_asset(release) or find_setup_asset(release)
        if asset is None:
            QMessageBox.warning(self, "Версии", f"Для {tag} нет установочного пакета.")
            return

        relation = "переустановить"
        if ver_tuple(tag) > ver_tuple(APP_VERSION):
            relation = "обновиться до"
        elif ver_tuple(tag) < ver_tuple(APP_VERSION):
            relation = "откатиться на"
        package = "установщик" if asset.get("name", "").lower().endswith(".exe") else "portable-пакет"
        if QMessageBox.question(
            self, "Смена версии",
            f"{relation.capitalize()} {tag}?\n\n"
            f"Будет загружен {package}. Несохранённые данные в других окнах приложения "
            "следует сохранить перед продолжением."
        ) != QMessageBox.Yes:
            return
        self._perform_release_install(asset, tag)

    def _check_for_updates(self, silent: bool = False):
        def worker():
            try:
                api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
                try:
                    data = json.loads(github_request(api, timeout=20).decode("utf-8"))
                except urllib.error.HTTPError as http_err:
                    if http_err.code == 404:
                        if not silent:
                            self._on_ui(lambda: QMessageBox.information(
                                self, "Обновления",
                                "На GitHub пока нет опубликованных релизов.\n\n"
                                "Создайте Release и загрузите Xray_labs-portable.zip."
                            ))
                        return
                    if not silent:
                        self._on_ui(lambda: QMessageBox.warning(
                            self, "Обновления", f"Ошибка GitHub: {http_err}"
                        ))
                    return

                latest_tag = data.get("tag_name", "v0.0.0")
                if not (find_setup_asset(data) or find_portable_asset(data)):
                    if not silent:
                        self._on_ui(lambda: QMessageBox.information(
                            self, "Обновления", "В релизе не найден установочный пакет."
                        ))
                    return

                current = ver_tuple(APP_VERSION)
                latest = ver_tuple(latest_tag)

                if latest <= current:
                    if not silent:
                        self._on_ui(lambda: QMessageBox.information(
                            self, "Обновления", f"У вас уже последняя версия ({APP_VERSION})."
                        ))
                    return

                def ask_update(release=data, tag=latest_tag):
                    if QMessageBox.question(
                        self, "Доступно обновление",
                        f"Доступна новая версия {tag} (у вас {APP_VERSION}).\n\n"
                        "Загрузить и установить сейчас?"
                    ) == QMessageBox.Yes:
                        mode = get_release_install_mode()
                        asset = (
                            find_setup_asset(release) if mode == "installer"
                            else find_portable_asset(release)
                        )
                        asset = asset or find_setup_asset(release) or find_portable_asset(release)
                        if asset:
                            self._perform_release_install(asset, tag)

                self._on_ui(ask_update)

            except Exception as e:
                if not silent:
                    self._on_ui(lambda: QMessageBox.warning(
                        self, "Обновления", f"Не удалось проверить обновления:\n{e}"
                    ))

        threading.Thread(target=worker, daemon=True).start()

    def _perform_release_install(self, asset: dict, new_tag: str):
        progress = QMessageBox(self)
        progress.setWindowTitle("Смена версии")
        progress.setText(f"Загрузка {new_tag}...")
        progress.setStandardButtons(QMessageBox.NoButton)
        progress.show()
        QApplication.processEvents()

        def worker():
            tmp_file = None
            extract_dir = None
            try:
                suffix = ".exe" if asset.get("name", "").lower().endswith(".exe") else ".zip"
                safe_tag = "".join(ch for ch in new_tag if ch.isalnum() or ch in ".-_")
                tmp_file = os.path.join(tempfile.gettempdir(), f"xray_{safe_tag}{suffix}")
                download_release_asset(asset, tmp_file)

                if suffix == ".exe":
                    self._on_ui(progress.close)
                    self._on_ui(lambda p=tmp_file: self._launch_installer(p))
                    return

                extract_dir = tempfile.mkdtemp(prefix="xray_upd_")
                safe_extract_zip(tmp_file, extract_dir)

                src_dir = find_extracted_app_dir(extract_dir)
                app_dir = get_app_install_dir()
                exe_path = os.path.join(app_dir, "Xray_labs.exe")
                if not os.path.isfile(os.path.join(src_dir, "Xray_labs.exe")):
                    raise ValueError("В архиве не найден Xray_labs.exe")
                if not os.path.isdir(app_dir) or not os.access(app_dir, os.W_OK):
                    raise PermissionError(
                        "Нет прав на запись в папку приложения. Используйте установщик из релиза."
                    )

                bat = os.path.join(tempfile.gettempdir(), "xray_updater.bat")
                backup_dir = tempfile.mkdtemp(prefix="xray_backup_")
                src_q = src_dir.replace('"', '""')
                app_q = app_dir.replace('"', '""')
                ext_q = extract_dir.replace('"', '""')
                zip_q = tmp_file.replace('"', '""')
                backup_q = backup_dir.replace('"', '""')
                bat_content = f"""@echo off
chcp 65001 >nul
setlocal
set "SRC={src_q}"
set "DEST={app_q}"
set "EXE={exe_path}"
set "EXTRACT={ext_q}"
set "BACKUP={backup_q}"
echo Ozhidanie zakrytiya Xray_labs...
:waitloop
tasklist /FI "IMAGENAME eq Xray_labs.exe" 2>nul | find /I "Xray_labs.exe" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto waitloop
)
robocopy "%DEST%" "%BACKUP%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS >nul
echo Kopirovanie obnovleniya...
robocopy "%SRC%" "%DEST%" /E /R:8 /W:2 /NFL /NDL /NJH /NJS
if errorlevel 8 (
    echo Update copy error: %errorlevel% > "%TEMP%\\Xray_labs_update_error.txt"
    robocopy "%BACKUP%" "%DEST%" /E /R:4 /W:1 /NFL /NDL /NJH /NJS >nul
    start "" "%EXE%"
    rd /s /q "%EXTRACT%" >nul 2>&1
    rd /s /q "%BACKUP%" >nul 2>&1
    del /f /q "{zip_q}" >nul 2>&1
    del "%~f0" >nul 2>&1
    exit /b 1
)
start "" "%EXE%"
rd /s /q "%EXTRACT%" >nul 2>&1
rd /s /q "%BACKUP%" >nul 2>&1
del /f /q "{zip_q}" >nul 2>&1
del "%~f0" >nul 2>&1
"""
                with open(bat, "w", encoding="cp866") as f:
                    f.write(bat_content)

                self._on_ui(progress.close)
                self._on_ui(lambda b=bat: self._launch_updater(b))
            except Exception as ex:
                self._on_ui(progress.close)
                self._on_ui(lambda e=ex: QMessageBox.critical(
                    self, "Ошибка обновления", f"Не удалось загрузить или подготовить обновление:\n{e}"
                ))
                if tmp_file and os.path.isfile(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except OSError:
                        pass
                if extract_dir and os.path.isdir(extract_dir):
                    shutil.rmtree(extract_dir, ignore_errors=True)

        threading.Thread(target=worker, daemon=True).start()

    def _launch_installer(self, installer_path: str):
        batch_path = os.path.join(tempfile.gettempdir(), "xray_installer_launcher.bat")
        quoted = installer_path.replace('"', '""')
        batch_content = f'''@echo off
start /wait "" "{quoted}" /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS
del /f /q "{quoted}" >nul 2>&1
del "%~f0" >nul 2>&1
'''
        with open(batch_path, "w", encoding="cp866") as batch:
            batch.write(batch_content)
        self._launch_updater(batch_path)

    def _launch_updater(self, bat_path: str):
        CREATE_NO_WINDOW = 0x08000000
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", bat_path],
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
            )
        except Exception:
            os.startfile(bat_path)
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    # High DPI friendly
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

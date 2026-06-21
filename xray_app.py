"""
X-Ray-lab v0.0.2
PyQt5 + editable .ui files (open in Qt Designer).
Color scheme from MolPlayer/constants.py (Laby.docx palette).
"""

import sys
import os
import json
import math
import tempfile
import urllib.request
import urllib.error
import zipfile
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QFileDialog, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsLineItem, QGraphicsEllipseItem, QGraphicsRectItem,
    QGraphicsView, QDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QRadioButton, QLabel, QLineEdit, QFrame, QWidget
)
from PyQt5.QtGui import (
    QPixmap, QImage, QPen, QColor, QBrush, QFont, QIcon, QPainter, QDesktopServices
)
from PyQt5.QtCore import Qt, QEvent, QPointF, QRectF, QTimer, pyqtSignal, QObject, QUrl
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

def process_inclusions(
    original: Image.Image,
    line_points: Optional[Tuple[Tuple[int, int], Tuple[int, int]]],
    container_type: str,   # "square" or "cylinder"
    threshold: int = 95
) -> Tuple[Image.Image, Image.Image, int]:
    """
    Returns (original_rgb, processed_rgb, white_pixel_count_inside_mask)
    Processing per spec + user answer (show рядом):
      - Outside ROI: gray
      - Inside container shape: black base
      - Bright/dark spots inside (inclusions): white
    Simple threshold for demo. Count white pixels inside ROI for area.
    """
    if original.mode != "L":
        gray = original.convert("L")
    else:
        gray = original
    w, h = gray.size
    orig_rgb = original.convert("RGB") if original.mode != "RGB" else original

    # Default: no mask -> whole image
    mask = Image.new("L", (w, h), 0)
    draw_mask = None

    if line_points and len(line_points) == 2:
        (x1, y1), (x2, y2) = line_points
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 3:
            length = max(w, h) * 0.8

        from PIL import ImageDraw
        mask = Image.new("L", (w, h), 0)
        draw_mask = ImageDraw.Draw(mask)

        if container_type == "square":
            side = length
            left = int(cx - side / 2)
            top = int(cy - side / 2)
            right = int(cx + side / 2)
            bottom = int(cy + side / 2)
            draw_mask.rectangle([left, top, right, bottom], fill=255)
        else:
            # cylinder -> circle/ellipse
            r = length / 2.0
            left = int(cx - r)
            top = int(cy - r)
            right = int(cx + r)
            bottom = int(cy + r)
            draw_mask.ellipse([left, top, right, bottom], fill=255)

    processed = Image.new("RGB", (w, h), (128, 128, 128))  # outside = gray
    white_count = 0

    orig_pixels = orig_rgb.load()
    gray_pixels = gray.load()
    mask_pixels = mask.load() if mask else None
    proc_pixels = processed.load()

    for y in range(h):
        for x in range(w):
            in_roi = True
            if mask_pixels is not None:
                in_roi = mask_pixels[x, y] > 128

            g = gray_pixels[x, y]

            if not in_roi:
                proc_pixels[x, y] = (128, 128, 128)
            else:
                # Inside container shape: black base + white for inclusions
                if g < threshold:
                    proc_pixels[x, y] = (255, 255, 255)  # inclusion
                    white_count += 1
                else:
                    proc_pixels[x, y] = (20, 20, 20)  # container / matrix black

    return orig_rgb, processed, white_count

# ---------------- Interactive image view (line drawing via eventFilter) ----------------
# Note: We use plain QGraphicsView from the .ui file + eventFilter on the viewport
# for line drawing. No need for a promoted custom subclass in v1.

def ver_tuple(v: str) -> tuple:
    v = v.lstrip("vV")
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except Exception:
        return (0, 0, 0)


def find_portable_asset_url(release_data: dict) -> Optional[str]:
    for asset in release_data.get("assets", []):
        name = asset.get("name", "")
        if "portable" in name.lower() and name.endswith(".zip"):
            return asset.get("browser_download_url")
    for asset in release_data.get("assets", []):
        if asset.get("name", "").endswith(".zip"):
            return asset.get("browser_download_url")
    return None


def calc_inclusion_volume_mm3(area_mm2: float, thick_mm: float, incl_type: str) -> float:
    """Объём включений по типу частиц (стереологическая аппроксимация из 2D-площади)."""
    if area_mm2 <= 0 or thick_mm <= 0:
        return 0.0
    if incl_type == "cubic":
        return area_mm2 * thick_mm
    # шарообразные: V = (4/3)*pi*r^3 при S = pi*r^2 => V = (4/(3*sqrt(pi))) * S^(3/2)
    return (4.0 / (3.0 * math.sqrt(math.pi))) * (area_mm2 ** 1.5)


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

        self.setWindowOpacity(0.93)
        self.setFixedSize(1000, 600)
        self.setWindowIcon(QIcon(str(get_resource_path("icon_cat.ico"))))

        self.setStyleSheet(get_app_stylesheet())

        # State
        self.original_pil: Optional[Image.Image] = None
        self.line_points: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None
        self.current_line_item: Optional[QGraphicsLineItem] = None
        self.white_px_count: int = 0
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

        # Radios
        self.squareRadio.toggled.connect(self._update_instruction)
        self.cylRadio.toggled.connect(self._update_instruction)
        self.cubicRadio.toggled.connect(lambda: None)  # just for future
        self.sphereRadio.toggled.connect(lambda: None)

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

    def _update_instruction(self):
        if self.squareRadio.isChecked():
            self.instructionLabel.setText(
                "Укажите курсором мыши сторону контейнера на снимке и введите её истинное значение"
            )
            self.diamLabel.setText("Сторона")
        else:
            self.instructionLabel.setText("Укажите курсором мыши диаметр контейнера на снимке и введите его истинное значение")
            self.diamLabel.setText("Диаметр")

    def _restore_last_values(self):
        st = load_state()
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
        self._update_instruction()

    def _save_last_values(self):
        st = load_state()
        try:
            if self.diamEdit.text().strip():
                st["last_diam"] = int(self.diamEdit.text().strip())
            if self.thickEdit.text().strip():
                st["last_thick"] = int(self.thickEdit.text().strip())
            st["last_container"] = "square" if self.squareRadio.isChecked() else "cylinder"
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
            return
        ctype = "square" if self.squareRadio.isChecked() else "cylinder"
        try:
            _, proc, white = process_inclusions(self.original_pil, self.line_points, ctype)
            self.white_px_count = white
            self._showing_processed = True
            self.current_line_item = None

            ppix = pil_to_pixmap(proc)
            self.image_scene.clear()
            pitem = QGraphicsPixmapItem(ppix)
            self.image_scene.addItem(pitem)
            self.image_scene.setSceneRect(QRectF(0, 0, ppix.width(), ppix.height()))

            if self.line_points:
                (x1, y1), (x2, y2) = self.line_points
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                length = math.hypot(x2 - x1, y2 - y1)
                pen = make_pen(ACCENT_GLOW, 2)
                if ctype == "square":
                    side = length
                    self.image_scene.addRect(cx - side / 2, cy - side / 2, side, side, pen)
                else:
                    r = length / 2
                    self.image_scene.addEllipse(cx - r, cy - r, r * 2, r * 2, pen)

            self.imageView.fitInView(pitem, Qt.KeepAspectRatio)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка обработки", str(e))

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

        scale_mm_per_px = real_size / px_len

        # Inclusion area from last "Найти включения"
        if self.white_px_count <= 0:
            # Auto-run find if not done
            self._find_inclusions()
        area_mm2 = self.white_px_count * (scale_mm_per_px ** 2)

        # Container volume
        ctype = "square" if self.squareRadio.isChecked() else "cylinder"
        if ctype == "square":
            side_mm = real_size
            container_mm3 = side_mm * side_mm * thick
        else:
            r = real_size / 2.0
            container_mm3 = math.pi * r * r * thick

        incl_type = "cubic" if self.cubicRadio.isChecked() else "sphere"
        incl_mm3 = calc_inclusion_volume_mm3(area_mm2, thick, incl_type)
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


# ---------------- Rollback popup (PyQt version of MolPlayer popup) ----------------
class RollbackPopup(QFrame):
    version_chosen = pyqtSignal(str, str)

    def __init__(self, parent, candidates: list, anchor_widget: QWidget):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("rollbackPopup")
        self.setStyleSheet(f"""
            QFrame#rollbackPopup {{
                background-color: {BG_PANEL};
                border: 1px solid {BORDER};
                border-radius: 4px;
            }}
            QPushButton {{
                text-align: left;
                padding-left: 10px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        title = QLabel("Откат на предыдущую версию")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: bold; font-size: 11pt;")
        layout.addWidget(title)

        for tag, url in candidates[:10]:
            btn = QPushButton(f"↩ {tag}")
            btn.clicked.connect(lambda checked=False, t=tag, u=url: self._choose(t, u))
            layout.addWidget(btn)

        hint = QLabel("Выберите версию для отката")
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 9pt;")
        layout.addWidget(hint)

        self.adjustSize()
        pos = anchor_widget.mapToGlobal(anchor_widget.rect().bottomLeft())
        self.move(pos.x() - 40, pos.y() + 2)

    def _choose(self, tag: str, url: str):
        self.version_chosen.emit(url, tag)
        self.close()


# ---------------- Main selector window ----------------
class MainWindow(QMainWindow):
    def _on_ui(self, func):
        QTimer.singleShot(0, func)

    def __init__(self):
        super().__init__()
        uic_path = get_resource_path("ui/main_window.ui")
        uic.loadUi(str(uic_path), self)

        self.setWindowOpacity(0.93)
        self._rollback_popup: Optional[RollbackPopup] = None
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

        self.rollback_btn = QPushButton("↩", self)
        self.rollback_btn.setObjectName("smallUpdateBtn")
        self.rollback_btn.setToolTip("Откат на предыдущую версию")
        self.rollback_btn.setFixedSize(26, 22)
        self.rollback_btn.clicked.connect(self._show_rollback_versions)

        self.update_btn = QPushButton("↻", self)
        self.update_btn.setObjectName("smallUpdateBtn")
        self.update_btn.setToolTip("Проверить обновления")
        self.update_btn.setFixedSize(26, 22)
        self.update_btn.clicked.connect(lambda: self._check_for_updates(silent=False))

        try:
            hl = self.headerFrame.layout()
            if hl:
                hl.addWidget(self.rollback_btn)
                hl.addWidget(self.update_btn)
        except Exception:
            self.update_btn.move(self.width() - 40, 8)
            self.rollback_btn.move(self.width() - 70, 8)

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

    def _show_rollback_versions(self):
        if self._rollback_popup and self._rollback_popup.isVisible():
            self._rollback_popup.close()
            self._rollback_popup = None
            return

        def worker():
            try:
                api = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
                req = urllib.request.Request(api, headers={"User-Agent": f"{APP_NAME}-Updater/1.0"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    releases = json.loads(resp.read().decode("utf-8"))

                current = ver_tuple(APP_VERSION)
                candidates = []
                for rel in releases:
                    tag = rel.get("tag_name", "")
                    if not tag or ver_tuple(tag) >= current:
                        continue
                    url = find_portable_asset_url(rel)
                    if url:
                        candidates.append((tag, url))

                if not candidates:
                    self._on_ui(lambda: QMessageBox.information(
                        self, "Откат версии",
                        "Нет доступных предыдущих версий с portable-архивом на GitHub."
                    ))
                    return

                candidates.sort(key=lambda c: ver_tuple(c[0]), reverse=True)

                def show_popup(cands=candidates):
                    self._rollback_popup = RollbackPopup(self, cands, self.rollback_btn)
                    self._rollback_popup.version_chosen.connect(self._do_rollback)
                    self._rollback_popup.show()

                self._on_ui(show_popup)
            except Exception as e:
                self._on_ui(lambda: QMessageBox.warning(
                    self, "Ошибка отката", f"Не удалось получить список версий:\n{e}"
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _do_rollback(self, asset_url: str, tag: str):
        self._rollback_popup = None
        if QMessageBox.question(
            self, "Подтверждение отката",
            f"Откатиться на {tag}?\n\n"
            "Файлы приложения будут заменены на версию из архива.\n"
            "Приложение автоматически перезапустится."
        ) != QMessageBox.Yes:
            return
        self._perform_self_update(asset_url, tag)

    def _check_for_updates(self, silent: bool = False):
        def worker():
            try:
                api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
                req = urllib.request.Request(api, headers={"User-Agent": f"{APP_NAME}-Updater/1.0"})
                try:
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
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
                asset_url = find_portable_asset_url(data)

                if not asset_url:
                    if not silent:
                        self._on_ui(lambda: QMessageBox.information(
                            self, "Обновления", "В релизе не найден portable zip."
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

                def ask_update(url=asset_url, tag=latest_tag):
                    if QMessageBox.question(
                        self, "Доступно обновление",
                        f"Доступна новая версия {tag} (у вас {APP_VERSION}).\n\n"
                        "Загрузить и установить сейчас?"
                    ) == QMessageBox.Yes:
                        self._perform_self_update(url, tag)

                self._on_ui(ask_update)

            except Exception as e:
                if not silent:
                    self._on_ui(lambda: QMessageBox.warning(
                        self, "Обновления", f"Не удалось проверить обновления:\n{e}"
                    ))

        threading.Thread(target=worker, daemon=True).start()

    def _perform_self_update(self, download_url: str, new_tag: str):
        progress = QMessageBox(self)
        progress.setWindowTitle("Обновление")
        progress.setText(f"Загрузка {new_tag}...")
        progress.setStandardButtons(QMessageBox.NoButton)
        progress.show()

        def worker():
            try:
                tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip").name
                urllib.request.urlretrieve(download_url, tmp_zip)

                extract_dir = tempfile.mkdtemp(prefix="xray_upd_")
                with zipfile.ZipFile(tmp_zip, "r") as z:
                    z.extractall(extract_dir)

                src_dir = os.path.join(extract_dir, "Xray_labs")
                if not os.path.isdir(src_dir):
                    src_dir = extract_dir

                if getattr(sys, "frozen", False):
                    app_dir = os.path.dirname(sys.executable)
                else:
                    app_dir = os.path.dirname(os.path.abspath(__file__))

                bat = os.path.join(tempfile.gettempdir(), "xray_updater.bat")
                bat_content = f"""@echo off
chcp 65001 >nul
timeout /t 2 /nobreak >nul
robocopy "{src_dir}" "{app_dir}" /E /R:2 /W:1 /NFL /NDL /NJH /NJS
start "" "{app_dir}\\Xray_labs.exe"
rd /s /q "{extract_dir}" >nul 2>&1
del "%~f0" >nul 2>&1
"""
                with open(bat, "w", encoding="cp866") as f:
                    f.write(bat_content)

                self._on_ui(progress.close)
                self._on_ui(lambda: self._launch_updater(bat))
            except Exception as ex:
                self._on_ui(progress.close)
                self._on_ui(lambda: QMessageBox.critical(self, "Ошибка обновления", str(ex)))

        threading.Thread(target=worker, daemon=True).start()

    def _launch_updater(self, bat_path: str):
        try:
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(["cmd", "/c", bat_path], shell=True, creationflags=CREATE_NO_WINDOW)
        except Exception:
            subprocess.Popen(bat_path, shell=True)
        self.close()


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

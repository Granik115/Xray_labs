"""
X-Ray-lab v1.0.0
PyQt5 + editable .ui files (open in Qt Designer).
Color scheme 100% from MolPlayer/constants.py
Follows Laby.docx + embedded mocks exactly.
"""

import sys
import os
import json
import math
import tempfile
import urllib.request
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
    QPushButton, QRadioButton, QLabel, QLineEdit, QFrame, QVBoxLayout
)
from PyQt5.QtGui import (
    QPixmap, QImage, QPen, QColor, QBrush, QFont, QIcon, QPainter
)
from PyQt5.QtCore import Qt, QEvent, QPointF, QRectF, QTimer, pyqtSignal, QObject
from PyQt5 import uic

from constants import (
    BG_DARK, BG_PANEL, BTN_BG, BTN_HOVER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_GLOW, ACCENT_FRAME, DEPTH_BLUE, BORDER,
    APP_NAME, APP_VERSION, GITHUB_REPO, get_app_stylesheet
)

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
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    data = pil_img.tobytes("raw", "RGB")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg)

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

# ---------------- Interactive image view (line drawing) ----------------
class ImageGraphicsView(QGraphicsView):
    """Plain QGraphicsView used in .ui. Line logic is handled via eventFilter in the window."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)


def make_pen(color: str = "#00bfff", width: int = 3) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidth(width)
    pen.setCosmetic(True)
    return pen


# ---------------- Lab 1 Window ----------------
class Lab1Window(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        uic_path = Path(__file__).parent / "ui" / "lab1_window.ui"
        if not uic_path.exists():
            # Fallback for frozen
            base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent
            uic_path = base / "ui" / "lab1_window.ui"

        uic.loadUi(str(uic_path), self)

        self.setWindowOpacity(0.93)
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "icon_cat.ico")))

        self.setStyleSheet(get_app_stylesheet())

        # State
        self.original_pil: Optional[Image.Image] = None
        self.line_points: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None
        self.current_line_item: Optional[QGraphicsLineItem] = None
        self.white_px_count: int = 0
        self._dragging_line = False
        self._line_start: Optional[QPointF] = None

        # Scenes
        self.original_scene = QGraphicsScene(self)
        self.original_view.setScene(self.original_scene)
        self.original_view.setRenderHint(QPainter.Antialiasing)

        self.processed_scene = QGraphicsScene(self)
        self.processed_view.setScene(self.processed_scene)
        self.processed_view.setRenderHint(QPainter.Antialiasing)

        # Event filter for interactive line on original
        self.original_view.viewport().installEventFilter(self)

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

        # Restore last values (minimal persistence)
        self._restore_last_values()

        # Status
        self.statusbar.showMessage("Готов. Откройте снимок и укажите линию диаметра/стороны.")

    def _update_instruction(self):
        if self.squareRadio.isChecked():
            self.instructionLabel.setText("Укажите курсором мыши сторону квадрата на снимке и введите её истинное значение")
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
        if obj is self.original_view.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if self.original_pil is None:
                    return False
                self._dragging_line = True
                self._line_start = self.original_view.mapToScene(event.pos())
                # remove old line
                if self.current_line_item:
                    self.original_scene.removeItem(self.current_line_item)
                    self.current_line_item = None
                return True

            elif event.type() == QEvent.MouseMove and self._dragging_line and self._line_start:
                cur = self.original_view.mapToScene(event.pos())
                if self.current_line_item:
                    self.original_scene.removeItem(self.current_line_item)
                pen = make_pen(ACCENT_GLOW, 2)
                self.current_line_item = self.original_scene.addLine(
                    self._line_start.x(), self._line_start.y(),
                    cur.x(), cur.y(), pen
                )
                return True

            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._dragging_line:
                self._dragging_line = False
                end = self.original_view.mapToScene(event.pos())
                start = self._line_start
                self._line_start = None

                if self.current_line_item:
                    self.original_scene.removeItem(self.current_line_item)

                # commit final blue line
                pen = make_pen("#00bfff", 3)
                self.current_line_item = self.original_scene.addLine(
                    start.x(), start.y(), end.x(), end.y(), pen
                )

                # store in ORIGINAL image pixel coords (scene == image px)
                x1, y1 = int(start.x()), int(start.y())
                x2, y2 = int(end.x()), int(end.y())
                self.line_points = ((x1, y1), (x2, y2))
                self.statusbar.showMessage(f"Линия сохранена: {math.hypot(x2-x1, y2-y1):.1f} px. Введите реальный размер.")
                return True

        return super().eventFilter(obj, event)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть снимок", "", "Images (*.jpg *.jpeg *.bmp *.png);;All files (*)"
        )
        if not path:
            return

        try:
            pil = Image.open(path)
            self.original_pil = pil.convert("RGB")
            self.line_points = None
            if self.current_line_item:
                self.original_scene.removeItem(self.current_line_item)
                self.current_line_item = None
            self.white_px_count = 0

            # Clear processed
            self.processed_scene.clear()

            # Load into original view (full res in scene)
            pix = pil_to_pixmap(self.original_pil)
            self.original_scene.clear()
            item = QGraphicsPixmapItem(pix)
            self.original_scene.addItem(item)
            self.original_scene.setSceneRect(QRectF(0, 0, pix.width(), pix.height()))
            self.original_view.fitInView(item, Qt.KeepAspectRatio)

            self.statusbar.showMessage(f"Загружен: {Path(path).name}  ({pil.width}x{pil.height}) — нарисуйте линию калибровки")
            # save last image path
            st = load_state()
            st["last_image"] = path
            save_state(st)

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть изображение:\n{e}")

    def _find_inclusions(self):
        if self.original_pil is None:
            QMessageBox.information(self, "Нет снимка", "Сначала откройте файл.")
            return
        ctype = "square" if self.squareRadio.isChecked() else "cylinder"
        try:
            orig, proc, white = process_inclusions(self.original_pil, self.line_points, ctype)
            self.white_px_count = white

            # Show original (with line if any)
            self.original_scene.clear()
            opix = pil_to_pixmap(orig)
            oitem = QGraphicsPixmapItem(opix)
            self.original_scene.addItem(oitem)
            self.original_scene.setSceneRect(QRectF(0, 0, opix.width(), opix.height()))

            # Re-draw line on original if present
            if self.line_points:
                (x1, y1), (x2, y2) = self.line_points
                pen = make_pen("#00bfff", 3)
                self.current_line_item = self.original_scene.addLine(x1, y1, x2, y2, pen)

            # Processed (side-by-side)
            self.processed_scene.clear()
            ppix = pil_to_pixmap(proc)
            pitem = QGraphicsPixmapItem(ppix)
            self.processed_scene.addItem(pitem)
            self.processed_scene.setSceneRect(QRectF(0, 0, ppix.width(), ppix.height()))

            # Draw ROI outline on processed for clarity
            if self.line_points:
                (x1, y1), (x2, y2) = self.line_points
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                length = math.hypot(x2 - x1, y2 - y1)
                pen = make_pen(ACCENT_GLOW, 2)
                if ctype == "square":
                    side = length
                    self.processed_scene.addRect(
                        cx - side/2, cy - side/2, side, side, pen
                    )
                else:
                    r = length / 2
                    self.processed_scene.addEllipse(
                        cx - r, cy - r, r*2, r*2, pen
                    )

            self.processed_view.fitInView(pitem, Qt.KeepAspectRatio)
            self.original_view.fitInView(oitem, Qt.KeepAspectRatio)

            self.statusbar.showMessage(f"Включения найдены. Белых пикселей в маске: {white}. Теперь введите размеры и рассчитайте объёмы.")
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

        if not has_line or real_size <= 0 or thick <= 0:
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

        # Inclusion volume (PLACEHOLDER — user will provide exact formulas)
        # Current: simple area * thickness (2.5D). TODO: spherical 4/3 π r³ etc.
        incl_mm3 = area_mm2 * thick
        poroda_mm3 = max(0.0, container_mm3 - incl_mm3)

        poroda_cm3 = poroda_mm3 / 1000.0

        # Update UI
        self.resultPorodaLabel.setText(f"Объем породы: {poroda_cm3:.2f} см³")
        self.resultInclLabel.setText(f"Объем включений: {incl_mm3:.2f} мм³")

        self._save_last_values()

        self.statusbar.showMessage("Расчёт выполнен. (Формулы включений — заглушка, обновите по методике)")

    def _open_pdf_placeholder(self, filename: str):
        base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent
        pdf_path = base / "resources" / filename
        if pdf_path.exists():
            from PyQt5.QtGui import QDesktopServices
            from PyQt5.QtCore import QUrl
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


# ---------------- Main selector window ----------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic_path = Path(__file__).parent / "ui" / "main_window.ui"
        if not uic_path.exists():
            base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent
            uic_path = base / "ui" / "main_window.ui"

        uic.loadUi(str(uic_path), self)

        self.setWindowOpacity(0.93)
        try:
            self.setWindowIcon(QIcon(str(Path(__file__).parent / "icon_cat.ico")))
        except Exception:
            pass

        self.setStyleSheet(get_app_stylesheet())
        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{APP_VERSION}")

        # Small update button (corner, not prominent)
        self.update_btn = QPushButton("↻", self)
        self.update_btn.setObjectName("smallUpdateBtn")
        self.update_btn.setToolTip("Проверить обновления")
        self.update_btn.setFixedSize(26, 22)
        self.update_btn.clicked.connect(lambda: self._check_for_updates(silent=False))

        # Place it in header (top-right corner of headerFrame)
        try:
            hl = self.headerFrame.layout()
            if hl:
                hl.addWidget(self.update_btn)
        except Exception:
            # Fallback: bottom right-ish
            self.update_btn.move(self.width() - 40, self.height() - 30)

        # Populate lab buttons (exactly 1 functional + placeholders)
        self._populate_lab_buttons()

        # Auto silent update check (like MolPlayer)
        QTimer.singleShot(8000, lambda: self._check_for_updates(silent=True))

        self.statusbar.showMessage("Готов. Выберите лабораторную работу.")

    def _populate_lab_buttons(self):
        container = self.labsContainer
        layout = container.layout()

        # Clear any designer placeholders
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()

        # Real lab 1
        btn1 = QPushButton("1. Оценка концентрации вещества рентгеноабсорбционным методом")
        btn1.setMinimumHeight(42)
        btn1.clicked.connect(self._open_lab1)
        layout.addWidget(btn1)

        # Placeholders for future labs (as discussed)
        for i in range(2, 5):
            b = QPushButton(f"{i}. (в разработке)")
            b.setMinimumHeight(42)
            b.setEnabled(False)
            b.setStyleSheet(f"QPushButton {{ background-color: {BG_TRACK}; color: {TEXT_MUTED}; }}")
            layout.addWidget(b)

        # Small version label at bottom
        ver = QLabel(f"v{APP_VERSION}")
        ver.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 9pt;")
        ver.setAlignment(Qt.AlignRight)
        layout.addWidget(ver)

    def _open_lab1(self):
        lab = Lab1Window(self)
        lab.show()

    # ---------------- Self-update (adapted from MolPlayer) ----------------
    def _check_for_updates(self, silent: bool = False):
        def worker():
            try:
                api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
                req = urllib.request.Request(api, headers={"User-Agent": f"{APP_NAME}-Updater/1.0"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                latest_tag = data.get("tag_name", "v0.0.0")

                asset_url = None
                for asset in data.get("assets", []):
                    name = asset.get("name", "")
                    if "portable" in name.lower() and name.endswith(".zip"):
                        asset_url = asset.get("browser_download_url")
                        break
                if not asset_url:
                    for asset in data.get("assets", []):
                        if asset.get("name", "").endswith(".zip"):
                            asset_url = asset.get("browser_download_url")
                            break

                if not asset_url:
                    if not silent:
                        QMessageBox.information(self, "Обновления", "В релизе не найден подходящий portable zip.")
                    return

                def ver_tuple(v: str):
                    v = v.lstrip("vV")
                    try:
                        return tuple(int(x) for x in v.split(".")[:3])
                    except Exception:
                        return (0, 0, 0)

                current = ver_tuple(APP_VERSION)
                latest = ver_tuple(latest_tag)

                if latest <= current:
                    if not silent:
                        QMessageBox.information(self, "Обновления", f"У вас уже последняя версия ({APP_VERSION}).")
                    return

                if QMessageBox.question(
                    self, "Доступно обновление",
                    f"Доступна новая версия {latest_tag} (у вас {APP_VERSION}).\n\nЗагрузить и установить сейчас?"
                ) == QMessageBox.Yes:
                    self._perform_self_update(asset_url, latest_tag)

            except Exception as e:
                if not silent:
                    QMessageBox.warning(self, "Обновления", f"Не удалось проверить обновления:\n{e}")

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

                progress.close()
                self._launch_updater(bat)
            except Exception as ex:
                progress.close()
                QMessageBox.critical(self, "Ошибка обновления", str(ex))

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

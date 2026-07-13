import os
import tempfile
import unittest
import zipfile
import math
from pathlib import Path

from PIL import Image
from PyQt5.QtWidgets import QApplication, QPushButton

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from xray_app import (
    build_silent_installer_batch,
    calc_inclusion_volume_mm3,
    connected_component_areas,
    process_inclusions,
    safe_extract_zip,
    square_corners_from_diagonal,
    ver_tuple,
    Lab1Window,
    MainWindow,
)
from constants import APP_VERSION


class CoreLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_version_comparison(self):
        self.assertGreater(ver_tuple("v0.0.7"), ver_tuple("0.0.6"))
        self.assertEqual(APP_VERSION, "0.0.7")

    def test_installer_update_is_fully_unattended_and_restarts_app(self):
        script = build_silent_installer_batch(
            r"C:\Temp\Xray_labs-0.0.7-setup.exe",
            r"C:\Apps\X-Ray-lab\Xray_labs.exe",
        )
        for switch in (
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
            "/SP-",
        ):
            self.assertIn(switch, script)
        self.assertIn('start "" "%APP_EXE%"', script)
        self.assertIn("Installer exit code", script)

    def test_windows_are_opaque_and_version_button_is_not_duplicated(self):
        main = MainWindow()
        lab = Lab1Window(main_window=main)
        try:
            version_buttons = [
                button
                for button in main.findChildren(QPushButton)
                if button.objectName() == "smallUpdateBtn"
            ]
            self.assertEqual(len(version_buttons), 1)
            self.assertEqual(version_buttons[0].text(), "↻")
            self.assertEqual(main.windowOpacity(), 1.0)
            self.assertEqual(lab.windowOpacity(), 1.0)
        finally:
            lab.close()
            main.close()

    def test_diagonal_defines_a_true_square(self):
        corners = square_corners_from_diagonal(0, 0, 100, 80)
        sides = [
            math.dist(corners[index], corners[(index + 1) % 4])
            for index in range(4)
        ]
        self.assertLess(max(sides) - min(sides), 1e-9)
        self.assertAlmostEqual(math.dist(corners[0], corners[2]), math.dist(corners[1], corners[3]))

    def test_components_are_eight_connected(self):
        mask = bytearray([
            1, 0, 0,
            0, 1, 0,
            0, 0, 1,
        ])
        self.assertEqual(connected_component_areas(mask, 3, 3), [3])

    def test_volume_is_sum_of_individual_particles(self):
        cubic = calc_inclusion_volume_mm3([4.0, 9.0], "cubic")
        self.assertEqual(cubic, 35.0)
        spherical = calc_inclusion_volume_mm3([4.0, 9.0], "sphere")
        self.assertAlmostEqual(spherical, 4.0 * 35.0 / (3.0 * 3.141592653589793 ** 0.5))

    def test_local_contrast_ignores_smooth_exposure_gradient(self):
        width = height = 100
        image = Image.new("L", (width, height))
        pixels = image.load()
        for y in range(height):
            for x in range(width):
                pixels[x, y] = 70 + x
        for y in range(48, 52):
            for x in range(48, 52):
                pixels[x, y] = max(0, pixels[x, y] - 55)

        _, overlay, white_count, component_areas = process_inclusions(
            image, ((0, 0), (99, 99)), "square", contrast_threshold=10
        )
        self.assertGreaterEqual(white_count, 12)
        self.assertLess(white_count, 30)
        self.assertEqual(sum(component_areas), white_count)
        self.assertEqual(overlay.getpixel((50, 50)), (0, 191, 255))

    def test_safe_extract_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "bad.zip"
            destination = Path(temp) / "out"
            destination.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside.txt", "nope")
            with self.assertRaises(ValueError):
                safe_extract_zip(str(archive_path), str(destination))


if __name__ == "__main__":
    unittest.main()

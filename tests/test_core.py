import os
import hashlib
import tempfile
import time
import unittest
import zipfile
import math
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from xray_app import (
    build_silent_installer_batch,
    calc_inclusion_volume_mm3,
    container_volume_mm3,
    consume_update_error,
    connected_component_areas,
    detect_dark_inclusion_regions,
    dismiss_update_progress,
    download_release_asset,
    measurement_scale_mm_per_px,
    parse_measurement_mm,
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
        self.assertGreater(ver_tuple("v0.0.12"), ver_tuple("0.0.11"))
        self.assertEqual(APP_VERSION, "0.0.12")

    def test_three_digit_and_decimal_measurements_are_accepted(self):
        self.assertEqual(parse_measurement_mm("101"), 101.0)
        self.assertEqual(parse_measurement_mm("100,5"), 100.5)
        self.assertEqual(parse_measurement_mm("100.5"), 100.5)
        self.assertEqual(parse_measurement_mm("10000"), 0.0)
        lab = Lab1Window()
        try:
            self.assertGreaterEqual(lab.diamEdit.maxLength(), 8)
            self.assertGreaterEqual(lab.thickEdit.maxLength(), 8)
        finally:
            lab.close()

    def test_release_download_resumes_after_connection_reset(self):
        payload = b"first-half-second-half"

        class ResetResponse:
            status = 200
            headers = {"Content-Length": str(len(payload))}

            def __init__(self):
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                self.calls += 1
                if self.calls == 1:
                    return payload[:10]
                raise ConnectionResetError(10054, "connection reset")

        class ResumeResponse:
            status = 206
            headers = {"Content-Length": str(len(payload) - 10)}

            def __init__(self):
                self.remaining = payload[10:]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                result, self.remaining = self.remaining, b""
                return result

        asset = {
            "browser_download_url": "https://example.invalid/setup.exe",
            "size": len(payload),
            "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
        }
        with tempfile.TemporaryDirectory() as temp:
            destination = str(Path(temp) / "setup.exe")
            with patch(
                "urllib.request.urlopen",
                side_effect=[ResetResponse(), ResumeResponse()],
            ) as urlopen:
                download_release_asset(
                    asset,
                    destination,
                    attempts=2,
                    retry_delays=(),
                )
            self.assertEqual(Path(destination).read_bytes(), payload)
            self.assertFalse(Path(f"{destination}.part").exists())
            resume_request = urlopen.call_args_list[1].args[0]
            self.assertEqual(resume_request.get_header("Range"), "bytes=10-")

    def test_update_progress_can_always_be_dismissed(self):
        progress = MagicMock()
        dismiss_update_progress(progress)
        progress.hide.assert_called_once_with()
        progress.done.assert_called_once_with(QMessageBox.Rejected)
        progress.deleteLater.assert_called_once_with()

    def test_download_error_clears_blocking_progress_dialog(self):
        main = MainWindow()
        asset = {
            "name": "Xray_labs-0.0.10-setup.exe",
            "browser_download_url": "https://example.invalid/setup.exe",
        }
        try:
            with patch(
                "xray_app.download_release_asset",
                side_effect=ConnectionError("WinError 10054"),
            ), patch.object(QMessageBox, "show"), patch.object(
                main, "isVisible", return_value=True
            ), patch.object(QMessageBox, "critical", return_value=QMessageBox.Ok) as critical:
                main._perform_release_install(asset, "v0.0.10")
                deadline = time.monotonic() + 2.0
                while main._update_progress is not None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.app.processEvents()
                self.assertIsNone(main._update_progress)
                self.assertIsNone(main._update_cancel_event)
                critical.assert_called_once()
        finally:
            main.close()

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
            "/SP-",
        ):
            self.assertIn(switch, script)
        self.assertIn(":wait_for_app", script)
        self.assertIn('"%INSTALLER%" /VERYSILENT', script)
        self.assertNotIn("/RESTARTAPPLICATIONS", script)
        self.assertIn('start "" "%APP_EXE%"', script)
        self.assertIn("Installer exit code", script)

    def test_silent_installer_error_is_reported_only_once(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "Xray_labs_update_error.txt"
            marker.write_text("Installer exit code: 5", encoding="utf-8")
            with patch("tempfile.gettempdir", return_value=temp):
                self.assertEqual(consume_update_error(), "Installer exit code: 5")
                self.assertEqual(consume_update_error(), "")

    def test_square_input_is_side_but_drawn_line_is_diagonal(self):
        self.assertAlmostEqual(
            measurement_scale_mm_per_px(25.0, 100.0, "square"),
            25.0 * math.sqrt(2.0) / 100.0,
        )
        self.assertAlmostEqual(
            measurement_scale_mm_per_px(25.0, 100.0, "cylinder"), 0.25
        )
        self.assertAlmostEqual(container_volume_mm3(25.0, 10.0, "square"), 6250.0)
        lab = Lab1Window()
        try:
            lab.squareRadio.setChecked(True)
            lab._update_instruction()
            self.assertEqual(lab.diamLabel.text(), "Сторона")
            self.assertIn("диагональ", lab.instructionLabel.text().lower())
        finally:
            lab.close()

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

    def test_ten_half_millimetre_spheres_are_below_one_cubic_mm(self):
        diameter = 0.5
        cross_section = math.pi * (diameter / 2.0) ** 2
        actual = calc_inclusion_volume_mm3([cross_section] * 10, "sphere")
        expected = 10 * math.pi * diameter ** 3 / 6.0
        self.assertAlmostEqual(actual, expected)
        self.assertLess(actual, 1.0)

    def test_seven_to_eight_one_millimetre_spheres_are_about_four_cubic_mm(self):
        cross_section = math.pi * 0.5 ** 2
        seven = calc_inclusion_volume_mm3([cross_section] * 7, "sphere")
        eight = calc_inclusion_volume_mm3([cross_section] * 8, "sphere")
        self.assertAlmostEqual(seven, 7 * math.pi / 6.0)
        self.assertAlmostEqual(eight, 8 * math.pi / 6.0)
        self.assertLess(seven, 4.0)
        self.assertGreater(eight, 4.0)

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
        self.assertGreater(white_count, 0)
        self.assertLess(white_count, 30)
        self.assertEqual(sum(component_areas), white_count)
        self.assertEqual(overlay.getpixel((50, 50)), (0, 191, 255))
        raw_overlay = overlay.tobytes()
        cyan = bytes((0, 191, 255))
        cyan_pixels = sum(
            raw_overlay[offset:offset + 3] == cyan
            for offset in range(0, len(raw_overlay), 3)
        )
        self.assertGreater(cyan_pixels, white_count)

    def test_half_depth_detection_is_dark_only_and_rejects_size_outlier(self):
        width = height = 180
        image = Image.new("L", (width, height))
        pixels = image.load()
        spots = [
            (45, 45, 62, 2.1),
            (90, 45, 58, 2.1),
            (135, 45, 65, 2.1),
            (90, 105, 22, 2.1),  # weak but correctly sized dark point
            (45, 125, 70, 5.5),  # much too large
        ]
        for y in range(height):
            for x in range(width):
                value = 135 + x * 18 / width
                for center_x, center_y, depth, sigma in spots:
                    distance = (x - center_x) ** 2 + (y - center_y) ** 2
                    value -= depth * math.exp(-distance / (2 * sigma ** 2))
                # A bright defect must never be interpreted as a dark inclusion.
                bright_distance = (x - 135) ** 2 + (y - 125) ** 2
                value += 70 * math.exp(-bright_distance / (2 * 2.1 ** 2))
                pixels[x, y] = max(0, min(255, round(value)))

        _, overlay, _, component_areas = process_inclusions(
            image, None, "square", contrast_threshold=8
        )
        self.assertEqual(len(component_areas), 4)
        self.assertNotEqual(overlay.getpixel((45, 125)), (0, 191, 255))
        self.assertNotEqual(overlay.getpixel((135, 125)), (0, 191, 255))
        self.assertEqual(overlay.getpixel((90, 105)), (0, 191, 255))

    def test_near_boundary_particle_survives_but_smaller_noise_does_not(self):
        width = height = 180
        image = Image.new("L", (width, height))
        pixels = image.load()
        spots = [
            (45, 45, 62, 2.1),
            (90, 45, 60, 2.1),
            (135, 45, 64, 2.1),
            (90, 151, 58, 2.1),  # normal particle, 9 px from ROI border
            (120, 105, 58, 1.25),  # undersized noise with similar peak
        ]
        for y in range(height):
            for x in range(width):
                value = 140
                for center_x, center_y, depth, sigma in spots:
                    distance = (x - center_x) ** 2 + (y - center_y) ** 2
                    value -= depth * math.exp(-distance / (2 * sigma ** 2))
                pixels[x, y] = max(0, min(255, round(value)))

        roi_mask = bytearray(width * height)
        for y in range(20, 161):
            for x in range(20, 161):
                roi_mask[y * width + x] = 255
        mask, areas = detect_dark_inclusion_regions(image, roi_mask, contrast_threshold=8)
        self.assertEqual(len(areas), 4)
        self.assertTrue(mask[151 * width + 90])
        self.assertFalse(mask[105 * width + 120])

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

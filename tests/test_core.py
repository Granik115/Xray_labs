import os
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from xray_app import (
    calc_inclusion_volume_mm3,
    connected_component_areas,
    safe_extract_zip,
    ver_tuple,
)
from constants import APP_VERSION


class CoreLogicTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertGreater(ver_tuple("v0.0.4"), ver_tuple("0.0.3"))
        self.assertEqual(APP_VERSION, "0.0.4")

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

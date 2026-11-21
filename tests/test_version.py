import os
import unittest

import _support  # noqa: F401
from version import __version__


class VersionTests(unittest.TestCase):
    def test_version_file_and_runtime_constant_match(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "VERSION"), "r", encoding="utf-8") as handle:
            version = handle.read().strip()
        self.assertEqual(version, __version__)
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_uploader_build_name_is_stable(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(
            os.path.join(root, "build_all.ps1"),
            "r",
            encoding="utf-8-sig",
        ) as handle:
            script = handle.read()
        self.assertIn('"图纸上传助手"', script)
        self.assertNotIn('图纸上传助手-v{0}', script)


if __name__ == "__main__":
    unittest.main()

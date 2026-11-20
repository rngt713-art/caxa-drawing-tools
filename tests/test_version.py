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


if __name__ == "__main__":
    unittest.main()

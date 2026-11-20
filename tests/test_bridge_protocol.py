import io
import unittest
from types import SimpleNamespace

import _support  # noqa: F401
from caxa.bridge import CaxaBridge, WRITE_COMMANDS


class BridgeProtocolTests(unittest.TestCase):
    def test_stderr_is_drained_and_tail_is_bounded(self):
        bridge = CaxaBridge()
        stream = io.StringIO("first line\nsecond line\nthird line\n")
        bridge._drain_stderr(SimpleNamespace(stderr=stream))
        self.assertEqual(
            bridge.stderr_tail(2),
            "second line\nthird line",
        )

    def test_relation_unlink_is_non_idempotent(self):
        self.assertIn("unlink_product_part", WRITE_COMMANDS)
        self.assertIn("unlink_part_relation", WRITE_COMMANDS)


if __name__ == "__main__":
    unittest.main()

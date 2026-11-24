import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

import _support  # noqa: F401
from caxa.bridge import CaxaBridge, CaxaError


class BridgeProtocolTests(unittest.TestCase):
    def bridge_with_replies(self, *replies):
        bridge = CaxaBridge()
        bridge._ensure_ready = Mock()
        bridge._send_raw = Mock()
        bridge._readline = Mock(side_effect=replies)
        bridge._drop_process = Mock()
        return bridge

    def test_stderr_is_drained_and_tail_is_bounded(self):
        bridge = CaxaBridge()
        stream = io.StringIO("first line\nsecond line\nthird line\n")
        bridge._drain_stderr(SimpleNamespace(stderr=stream))
        self.assertEqual(
            bridge.stderr_tail(2),
            "second line\nthird line",
        )

    def test_lost_reply_does_not_replay_writes_or_unknown_commands(self):
        for name in ("unlink_product_part", "unlink_part_relation", "future_command"):
            with self.subTest(name=name):
                bridge = self.bridge_with_replies(None, '{"ok":true}')
                command = {"cmd": name}

                with self.assertRaises(CaxaError) as caught:
                    bridge.call(command)

                self.assertTrue(caught.exception.response_lost)
                bridge._send_raw.assert_called_once_with(command)
                bridge._drop_process.assert_called_once_with()

    def test_lost_reply_replays_known_reads_once(self):
        for name in ("query_parts", "find", "list_products"):
            with self.subTest(name=name):
                bridge = self.bridge_with_replies(None, '{"ok":true,"items":[]}')
                command = {"cmd": name}

                self.assertEqual(bridge.call(command), {"ok": True, "items": []})

                self.assertEqual(bridge._send_raw.call_args_list, [call(command)] * 2)
                self.assertEqual(bridge._ensure_ready.call_count, 2)
                bridge._drop_process.assert_called_once_with()

    def test_lost_reply_replays_auto_confirm_through_wrapper(self):
        bridge = self.bridge_with_replies(None, '{"ok":true,"enabled":true}')

        self.assertTrue(bridge.set_auto_confirm(True, mode="append_iteration")["enabled"])

        command = {
            "cmd": "set_auto_confirm",
            "enabled": True,
            "mode": "append_iteration",
            "request_id": "",
            "target_name": "",
        }
        self.assertEqual(bridge._send_raw.call_args_list, [call(command)] * 2)
        bridge._drop_process.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

import json
import os
import unittest
from unittest.mock import patch

import _support  # noqa: F401
import config


class ConfigWriteTests(unittest.TestCase):
    def test_save_replaces_config_atomically(self):
        with _support.temp_dir() as directory:
            path = os.path.join(directory, "config.json")
            with patch.object(config, "CONFIG_PATH", path):
                config.save({"caxa_server_ip": "10.0.0.1"})
                with open(path, "r", encoding="utf-8") as handle:
                    saved = json.load(handle)
                self.assertEqual(saved["caxa_server_ip"], "10.0.0.1")

    def test_failed_replace_preserves_previous_config(self):
        with _support.temp_dir() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"caxa_server_ip":"old"}')
            with patch.object(config, "CONFIG_PATH", path), patch.object(
                config.os, "replace", side_effect=OSError("busy")
            ):
                with self.assertRaises(OSError):
                    config.save({"caxa_server_ip": "new"})
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["caxa_server_ip"], "old")
            self.assertEqual(
                [name for name in os.listdir(directory) if name.endswith(".tmp")],
                [],
            )

    def test_dpapi_failure_is_not_saved_as_empty_password(self):
        with patch.object(config, "win32crypt", None), patch.object(
            config, "_dpapi_protect", side_effect=OSError("dpapi failed")
        ):
            with self.assertRaises(RuntimeError):
                config._protect("secret")


if __name__ == "__main__":
    unittest.main()

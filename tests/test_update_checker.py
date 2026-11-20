import hashlib
import io
import json
import os
import unittest
from datetime import date
from pathlib import Path

import _support  # noqa: F401
import update_checker


class BytesResponse(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class UpdateCheckerTests(unittest.TestCase):
    def test_version_comparison_uses_three_numeric_parts(self):
        self.assertGreater(
            update_checker.compare_versions("v1.10.0", "1.9.9"), 0
        )
        self.assertEqual(
            update_checker.compare_versions("v1.0.0", "1.0.0"), 0
        )
        with self.assertRaises(update_checker.UpdateError):
            update_checker.parse_version("1.0")

    def test_check_for_update_selects_versioned_exe_and_digest(self):
        content = b"new uploader"
        digest = hashlib.sha256(content).hexdigest()
        payload = {
            "tag_name": "v1.1.0",
            "html_url": "https://github.com/rngt713-art/caxa-drawing-tools/releases/tag/v1.1.0",
            "published_at": "2026-11-20T00:00:00Z",
            "assets": [
                {
                    "name": "README-v1.1.0.md",
                    "browser_download_url": "https://example.test/readme",
                    "digest": "",
                },
                {
                    "name": "CAXA-Drawing-Uploader-v1.1.0.exe",
                    "browser_download_url": "https://example.test/app.exe",
                    "digest": "sha256:%s" % digest,
                },
            ],
        }

        def opener(_request, timeout):
            self.assertEqual(timeout, 9)
            return BytesResponse(json.dumps(payload).encode("utf-8"))

        info = update_checker.check_for_update(
            "1.0.0", opener=opener, timeout=9, token=""
        )
        self.assertTrue(info.available)
        self.assertEqual(info.latest_version, "1.1.0")
        self.assertEqual(
            info.asset_name, "CAXA-Drawing-Uploader-v1.1.0.exe"
        )
        self.assertEqual(info.sha256, digest)

    def test_current_release_does_not_require_download_asset(self):
        payload = {"tag_name": "v1.0.0", "assets": []}
        info = update_checker.check_for_update(
            "1.0.0",
            opener=lambda _request, timeout: BytesResponse(
                json.dumps(payload).encode("utf-8")
            ),
            token="",
        )
        self.assertFalse(info.available)
        self.assertEqual(info.latest_version, "1.0.0")

    def test_new_release_without_github_digest_is_blocked(self):
        payload = {
            "tag_name": "v1.1.0",
            "assets": [
                {
                    "name": "CAXA-Drawing-Uploader-v1.1.0.exe",
                    "browser_download_url": "https://example.test/app.exe",
                    "digest": "",
                }
            ],
        }
        with self.assertRaisesRegex(
            update_checker.UpdateError, "SHA-256 digest"
        ):
            update_checker.check_for_update(
                "1.0.0",
                opener=lambda _request, timeout: BytesResponse(
                    json.dumps(payload).encode("utf-8")
                ),
                token="",
            )

    def test_daily_state_caches_newer_release(self):
        with _support.temp_dir("update_state_") as directory:
            path = os.path.join(directory, "update_state.json")
            store = update_checker.UpdateStateStore(path)
            self.assertFalse(store.checked_today(date(2026, 11, 20)))
            info = update_checker.UpdateInfo(
                current_version="1.0.0",
                latest_version="1.1.0",
                available=True,
                tag_name="v1.1.0",
                asset_name="CAXA-Drawing-Uploader-v1.1.0.exe",
                download_url="https://example.test/app.exe",
                sha256="a" * 64,
                release_url="https://example.test/release",
            )
            store.mark_attempt(
                success=True, info=info, today=date(2026, 11, 20)
            )

            reloaded = update_checker.UpdateStateStore(path)
            self.assertTrue(
                reloaded.checked_today(date(2026, 11, 20))
            )
            cached = reloaded.cached_update("1.0.0")
            self.assertIsNotNone(cached)
            self.assertEqual(cached.latest_version, "1.1.0")

    def test_corrupt_cached_version_does_not_break_startup(self):
        with _support.temp_dir("update_corrupt_") as directory:
            path = os.path.join(directory, "update_state.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"latest_version": "broken"}, handle)
            store = update_checker.UpdateStateStore(path)
            self.assertIsNone(store.cached_update("1.1.0"))

    def test_download_verifies_sha256_and_avoids_overwrite(self):
        content = b"verified update bytes"
        digest = hashlib.sha256(content).hexdigest()
        info = update_checker.UpdateInfo(
            current_version="1.0.0",
            latest_version="1.1.0",
            available=True,
            tag_name="v1.1.0",
            asset_name="CAXA-Drawing-Uploader-v1.1.0.exe",
            download_url="https://example.test/app.exe",
            sha256=digest,
        )
        with _support.temp_dir("update_download_") as directory:
            first = update_checker.download_update(
                info,
                target_dir=directory,
                opener=lambda _request, timeout: BytesResponse(content),
            )
            self.assertEqual(first.read_bytes(), content)
            second = update_checker.download_update(
                info,
                target_dir=directory,
                opener=lambda _request, timeout: BytesResponse(content),
            )
            self.assertEqual(second, first)

    def test_download_hash_mismatch_leaves_no_file(self):
        info = update_checker.UpdateInfo(
            current_version="1.0.0",
            latest_version="1.1.0",
            available=True,
            tag_name="v1.1.0",
            asset_name="CAXA-Drawing-Uploader-v1.1.0.exe",
            download_url="https://example.test/app.exe",
            sha256="0" * 64,
        )
        with _support.temp_dir("update_bad_hash_") as directory:
            with self.assertRaisesRegex(
                update_checker.UpdateError, "SHA-256"
            ):
                update_checker.download_update(
                    info,
                    target_dir=directory,
                    opener=lambda _request, timeout: BytesResponse(b"bad"),
                )
            self.assertEqual(os.listdir(directory), [])

    def test_launch_update_waits_for_process_to_stay_running(self):
        class Process:
            def poll(self):
                return None

        calls = []
        with _support.temp_dir("update_launch_") as directory:
            path = os.path.join(
                directory, "CAXA-Drawing-Uploader-v1.1.0.exe"
            )
            with open(path, "wb") as handle:
                handle.write(b"exe")

            def popen(args, cwd):
                calls.append((args, cwd))
                return Process()

            process = update_checker.launch_update(
                path,
                popen=popen,
                sleep=lambda seconds: calls.append(("sleep", seconds)),
            )
        self.assertIsInstance(process, Process)
        self.assertEqual(calls[0][0], [os.path.abspath(path)])
        self.assertEqual(calls[1], ("sleep", 1.2))

    def test_launch_update_rejects_immediate_exit(self):
        class Process:
            def poll(self):
                return 7

        with _support.temp_dir("update_launch_fail_") as directory:
            path = os.path.join(
                directory, "CAXA-Drawing-Uploader-v1.1.0.exe"
            )
            with open(path, "wb") as handle:
                handle.write(b"exe")
            with self.assertRaisesRegex(
                update_checker.UpdateError, "退出码 7"
            ):
                update_checker.launch_update(
                    path,
                    popen=lambda _args, cwd: Process(),
                    sleep=lambda _seconds: None,
                )

    def test_launch_update_requests_overwrite_and_relaunch_mode(self):
        class Process:
            def poll(self):
                return None

        calls = []
        with _support.temp_dir("update_overwrite_launch_") as directory:
            source = os.path.join(
                directory, "CAXA-Drawing-Uploader-v1.1.0.exe"
            )
            target = os.path.join(directory, "图纸上传助手-v1.0.0.exe")
            with open(source, "wb") as handle:
                handle.write(b"new")
            update_checker.launch_update(
                source,
                install_target=target,
                parent_pid=321,
                popen=lambda args, cwd: calls.append((args, cwd)) or Process(),
                sleep=lambda _seconds: None,
            )
        self.assertEqual(
            calls[0][0],
            [
                os.path.abspath(source),
                "--apply-update",
                os.path.abspath(target),
                "321",
            ],
        )

    def test_apply_update_waits_replaces_old_exe_and_relaunches(self):
        class Process:
            pass

        events = []
        with _support.temp_dir("update_apply_") as directory:
            source = os.path.join(directory, "new-version.exe")
            old = os.path.join(directory, "installed.exe")
            with open(source, "wb") as handle:
                handle.write(b"new-version")
            with open(old, "wb") as handle:
                handle.write(b"old-version")

            def wait_exit(pid):
                events.append(("wait", pid))

            def popen(args, cwd):
                events.append(("launch", args, cwd))
                return Process()

            process, backup = update_checker.apply_update(
                old,
                456,
                source_exe=source,
                wait_exit=wait_exit,
                popen=popen,
            )
            self.assertIsInstance(process, Process)
            self.assertEqual(Path(old).read_bytes(), b"new-version")
            self.assertEqual(backup.read_bytes(), b"old-version")
        self.assertEqual(events[0], ("wait", 456))
        self.assertEqual(events[1][0], "launch")
        self.assertEqual(events[1][1], [os.path.abspath(old)])


if __name__ == "__main__":
    unittest.main()

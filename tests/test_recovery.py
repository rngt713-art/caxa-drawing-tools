"""Persistence semantics: pending/resume/recovery, locks, phase reuse."""

import os
import json
import unittest

import _support
import journal
import recovery_verifier
import upload_core
from caxa.bridge import CaxaError
from caxa.uploader import CaxaUploader


class JournalSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.journal_file = _support.journal_path("t.sqlite3")
        self.journal = journal.TaskJournal(self.journal_file)

    def tearDown(self):
        self.journal.close()

    def test_orphan_running_is_pending(self):
        task = self.journal.create_task(digest="d")
        self.journal.record_operation(
            task["task_id"], "upload", "01.01", status=journal.OP_RUNNING
        )
        pending = self.journal.pending_operations(task["task_id"])
        self.assertEqual(len(pending), 1)

    def test_run_lock_is_exclusive(self):
        task = self.journal.create_task(digest="d")
        with self.journal.run_lock(task["task_id"]):
            with self.assertRaises(journal.JournalBusy):
                with self.journal.run_lock(task["task_id"]):
                    pass
        self.journal.release_lock(task["task_id"])
        with self.journal.run_lock(task["task_id"]):
            pass

    def test_relation_confirmations_keep_type_and_skip_is_not_success(self):
        task = self.journal.create_task(digest="d")
        self.journal.record_operation(
            task["task_id"], "relation", "01.02|01.03",
            status=journal.OP_OK, parent_code="01.02", child_code="01.03",
            link_type="borrow",
        )
        self.journal.record_operation(
            task["task_id"], "relation", "01.02|01.04",
            status=journal.OP_SKIPPED, parent_code="01.02", child_code="01.04",
            link_type="borrow",
        )
        confirmed = self.journal.confirmed_relations(task["task_id"])
        self.assertEqual(confirmed[("01.02", "01.03")]["link_type"], "borrow")
        self.assertNotIn(("01.02", "01.04"), confirmed)

    def test_workflow_detail_roundtrip(self):
        task = self.journal.create_task(digest="d")
        self.journal.record_workflow(
            task["task_id"], status=journal.OP_OK, process_id=501,
            drawing_ids=[101, 102], started=True,
        )
        restored = upload_core._workflow_from_record(
            self.journal.workflow(task["task_id"])
        )
        self.assertEqual(restored["drawing_ids"], [101, 102])
        self.assertEqual(restored["process_instance_id"], "501")

    def test_plm_com_registration_marker_is_device_scoped_and_persistent(self):
        saved = self.journal.save_plm_com_registration(
            "PLM-TEST-0001",
            dll_path=r"C:\CAXA\PlmComApi.dll",
            exit_code=0,
            detail={"registered": True},
        )
        self.assertEqual(saved["registration_id"], "PLM-TEST-0001")
        self.assertEqual(
            saved["device_id"],
            journal.current_device_id(),
        )
        self.journal.close()
        self.journal = journal.TaskJournal(self.journal_file)
        restored = self.journal.plm_com_registration()
        self.assertEqual(restored["registration_id"], "PLM-TEST-0001")
        self.assertEqual(restored["dll_path"], r"C:\CAXA\PlmComApi.dll")
        self.journal.device_id = "DEV-OTHER-DEVICE"
        self.assertIsNone(self.journal.plm_com_registration())

    def test_unselected_row_does_not_change_batch_digest(self):
        root = os.path.dirname(self.journal_file)
        selected = _support.make_item(
            root, "01.01", name="selected", kind="component"
        )
        unselected = _support.make_item(
            root,
            "01.02",
            name="waiting",
            kind="component",
            content=b"%PDF waiting",
        )
        unselected["upload_sel"] = False
        manifest = _support.make_manifest([selected, unselected])
        before = upload_core.content_digest(manifest, _support.ACCOUNT)
        unselected["name"] = "changed while unselected"
        unselected["sha256"] = "f" * 64
        after = upload_core.content_digest(manifest, _support.ACCOUNT)
        self.assertEqual(before, after)

    def test_batch_id_keeps_task_identity_when_selection_changes(self):
        root = os.path.dirname(self.journal_file)
        first = _support.make_item(root, "01.01", "first")
        second = _support.make_item(
            root,
            "01.02",
            "second",
            content=b"%PDF second",
        )
        manifest = _support.make_manifest([first, second])
        manifest["batch_id"] = "B-test-stable"
        first_only = upload_core.content_digest(manifest, _support.ACCOUNT)
        second["upload_sel"] = True
        both = upload_core.content_digest(manifest, _support.ACCOUNT)
        second["upload_sel"] = False
        second_again = upload_core.content_digest(
            manifest, _support.ACCOUNT
        )
        self.assertEqual(first_only, both)
        self.assertEqual(first_only, second_again)

    def test_confirmed_upload_hash_mismatch_requires_reupload(self):
        item = {"sha256": "a" * 64}
        matching = {
            "detail": json.dumps({"sha256": "a" * 64})
        }
        changed = {
            "detail": json.dumps({"sha256": "b" * 64})
        }
        legacy = {"detail": "old-file.pdf"}
        self.assertTrue(upload_core._upload_record_matches_item(matching, item))
        self.assertFalse(upload_core._upload_record_matches_item(changed, item))
        self.assertTrue(upload_core._upload_record_matches_item(legacy, item))

    def test_adding_selected_row_reuses_task_and_uploads_only_new_row(self):
        root = os.path.dirname(self.journal_file)
        first = _support.make_item(root, "01.01", "first")
        second = _support.make_item(
            root,
            "01.02",
            "second",
            content=b"%PDF second",
        )
        second["upload_sel"] = False
        manifest = _support.make_manifest([first, second])
        manifest["batch_id"] = "B-incremental-upload"
        first_result, _ = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(first_result["ok"])
        self.assertEqual(first_result["uploaded"], 1)

        second["upload_sel"] = True
        second_result, _ = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(second_result["ok"])
        self.assertEqual(second_result["task_id"], first_result["task_id"])
        self.assertEqual(_support.FakeUploader.instances[-1].uploads, 1)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.journal_file = _support.journal_path("r.sqlite3")
        self.root = os.path.dirname(self.journal_file)
        self.manifest = None

    def _manifest(self):
        item = _support.make_item(
            self.root, "01.01", name="test", kind="component",
            content=b"%PDF-1",
        )
        return _support.make_manifest([item])

    def test_lost_append_response_keeps_original_hash_and_prior_version(self):
        class LostAttachmentReply(_support.FakeBridge):
            def call(self, cmd, *args, **kwargs):
                if cmd.get("cmd") == "upload":
                    with journal.TaskJournal(journal_file) as state:
                        task = state.list_tasks()[0]
                        operation = state.get_operation(task["task_id"], "upload", "01.01")
                    assert operation["status"] == journal.OP_RUNNING
                    assert json.loads(operation["detail"])["attach_attempt"]["before_iterator_no"] == "b.1"
                    raise CaxaError("reply lost", stage="upload", response_lost=True)
                if cmd.get("cmd") == "query_parts":
                    return {"items": [{"identity": "01.01", "object_id": 31}]}
                if cmd.get("cmd") == "part_drawings":
                    return {"drawings": [dict(drawing)]}
                return super().call(cmd, *args, **kwargs)

        class AttachmentUploader(_support.FakeUploader):
            def __init__(self, bridge, **kwargs):
                super().__init__(bridge, **kwargs)
                self.real = CaxaUploader(
                    bridge, before_attach=kwargs["before_attach"], log=lambda *args: None,
                )

            def upload_item(self, item, *args, **kwargs):
                return self.real.attach_file(31, item["file_path"], 1, "01.01 test.pdf", "01.01")

        manifest = self._manifest()
        manifest["batch_id"] = "B-lost-append-hash"
        file_hash = manifest["items"][0]["sha256"]
        journal_file = self.journal_file
        drawing = {
            "object_id": 111, "name": "01.01 test.pdf", "class_id": 1002,
            "iterator_no": "b.1", "history_count": 3,
        }
        bridge = LostAttachmentReply()
        result, _ = _support.run_upload(
            manifest, journal_file, bridge, uploader_cls=AttachmentUploader,
        )
        task_id = result["task_id"]
        with journal.TaskJournal(journal_file) as state:
            row, = state.pending_operations(task_id)
            self.assertEqual(json.loads(row["detail"])["sha256"], file_hash)
            verifier = recovery_verifier.RecoveryVerifier(bridge, CaxaUploader(bridge))
            with self.assertRaisesRegex(recovery_verifier.RecoveryEvidenceError, "版次历史未增加"):
                verifier.verify_upload(row)
            drawing["iterator_no"], drawing["history_count"] = "b.2", 4
            state.resolve_operation(task_id, "upload", row["key"], result="success", **verifier.verify_upload(row))
            self.assertEqual(json.loads(state.confirmed_uploads(task_id)["01.01"]["detail"])["sha256"], file_hash)

        manifest["items"] = [_support.make_item(self.root, "01.01", "test", content=b"%PDF replacement")]
        renewed, _ = _support.run_upload(manifest, journal_file)
        self.assertEqual(renewed["task_id"], task_id)
        self.assertEqual(_support.FakeUploader.instances[-1].uploads, 1)

    def test_local_staging_failure_is_retryable_without_pending_recovery(self):
        class ChangedFile(_support.FakeUploader):
            def upload_item(self, item, *args, **kwargs):
                with open(item["file_path"], "ab") as stream:
                    stream.write(b" updated during preflight")
                return self.staging.stage(item)

        manifest = self._manifest()
        manifest["batch_id"] = "B-staging-no-write"
        result, _ = _support.run_upload(manifest, self.journal_file, uploader_cls=ChangedFile)
        self.assertEqual(result["failed"], 1)
        with journal.TaskJournal(self.journal_file) as state:
            self.assertEqual(state.pending_operations(result["task_id"]), [])
            row = state.get_operation(result["task_id"], "upload", "01.01")
            self.assertEqual(row["status"], journal.OP_FAILED)
            self.assertIn("指纹不一致", row["error"])
        manifest["items"] = [_support.make_item(self.root, "01.01", "test", content=b"%PDF updated")]
        retried, _ = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(retried["ok"])
        self.assertEqual(retried["task_id"], result["task_id"])

    def test_first_upload_registers_device_then_later_upload_skips(self):
        first_manifest = self._manifest()
        first, first_bridge = _support.run_upload(
            first_manifest, self.journal_file
        )
        self.assertTrue(first["ok"])
        marker = first["device_registration"]
        self.assertEqual(marker["device_id"], journal.current_device_id())
        self.assertTrue(marker["registration_id"].startswith("PLM-"))
        self.assertEqual(first_bridge.plm_registration_calls, 1)
        self.assertEqual(first_bridge.login_count, 1)

        second_manifest = self._manifest()
        second_manifest["items"][0]["content"] = "changed"
        item = second_manifest["items"][0]
        digest, size = _support.write_pdf(
            os.path.dirname(item["file_path"]),
            os.path.basename(item["file_path"]),
            b"%PDF-2",
        )[1:]
        item["sha256"] = digest
        item["size"] = size
        second, second_bridge = _support.run_upload(
            second_manifest, self.journal_file
        )
        self.assertTrue(second["ok"])
        self.assertEqual(second_bridge.plm_registration_calls, 0)
        self.assertEqual(second_bridge.login_count, 1)
        self.assertEqual(
            second["device_registration"]["registration_id"],
            marker["registration_id"],
        )

    def test_precheck_registration_is_reused_by_first_upload(self):
        manifest = self._manifest()
        with _support.patch_core() as precheck_bridge:
            info = upload_core.precheck_with_caxa(
                manifest,
                _support.ACCOUNT,
                log=lambda *args: None,
                journal_path=self.journal_file,
            )
        self.assertEqual(precheck_bridge.plm_registration_calls, 1)
        self.assertTrue(
            info["device_registration"]["registration_id"].startswith("PLM-")
        )

        result, upload_bridge = _support.run_upload(
            manifest, self.journal_file
        )
        self.assertTrue(result["ok"])
        self.assertEqual(upload_bridge.plm_registration_calls, 0)
        self.assertEqual(upload_bridge.login_count, 1)
        self.assertEqual(
            result["device_registration"]["registration_id"],
            info["device_registration"]["registration_id"],
        )

    def test_failed_device_registration_does_not_write_marker(self):
        manifest = self._manifest()

        class RegistrationFailureBridge(_support.FakeBridge):
            def ensure_plm_com_registration(self, *, timeout=120.0):
                raise CaxaError(
                    "registration cancelled",
                    stage="plm_com_registration",
                )

        with self.assertRaises(CaxaError):
            _support.run_upload(
                manifest,
                self.journal_file,
                bridge=RegistrationFailureBridge(),
            )
        with journal.TaskJournal(self.journal_file) as state:
            self.assertIsNone(state.plm_com_registration())

    def test_pending_blocks_new_task_until_resolved(self):
        manifest = self._manifest()

        def lose_reply(item, **kwargs):
            raise CaxaError("lost reply", response_lost=True)

        _support.FakeUploader.upload_behaviour = staticmethod(lose_reply)
        try:
            result, _bridge = _support.run_upload(manifest, self.journal_file)
        finally:
            _support.FakeUploader.upload_behaviour = None
        self.assertFalse(result["ok"])
        self.assertEqual(result["uncertain"], 1)

        with self.assertRaises(upload_core.PendingVerificationError):
            _support.run_upload(
                manifest, self.journal_file, force_new_task=True
            )

        task_id = result["task_id"]
        with journal.TaskJournal(self.journal_file) as j:
            record = j.resolve_operation(
                task_id, "upload", "01.01", result="no_write",
                note="测试：确认未写入",
            )
            self.assertEqual(record["status"], journal.OP_FAILED)

        retry, _bridge = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(retry["ok"])
        self.assertEqual(retry["uploaded"], 1)

    def test_unselected_edit_does_not_reupload_selected_item(self):
        first = _support.make_item(
            self.root, "01.01", name="first", kind="component"
        )
        waiting = _support.make_item(
            self.root,
            "01.02",
            name="waiting",
            kind="component",
            content=b"%PDF waiting",
        )
        waiting["upload_sel"] = False
        manifest = _support.make_manifest([first, waiting])
        result, _ = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(result["ok"])
        self.assertEqual(result["uploaded"], 1)

        waiting["name"] = "changed after first upload"
        retry, _ = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(retry["ok"])
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["uploaded"], 1)

    def test_verified_success_recovery_keeps_ids(self):
        manifest = self._manifest()

        def lose_reply(item, **kwargs):
            raise CaxaError("lost reply", response_lost=True)

        _support.FakeUploader.upload_behaviour = staticmethod(lose_reply)
        try:
            result, _ = _support.run_upload(manifest, self.journal_file)
        finally:
            _support.FakeUploader.upload_behaviour = None
        task_id = result["task_id"]
        with journal.TaskJournal(self.journal_file) as j:
            j.resolve_operation(
                task_id, "upload", "01.01", result="success",
                remote_id=777, class_id=1002, iterator_no="3",
            )
        resume, _ = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(resume["ok"])
        self.assertTrue(resume["duplicate"])
        self.assertEqual(resume["uploaded"], 1)
        self.assertIn(777, resume["drawing_ids"])

    def test_workflow_requires_state_and_document_readback(self):
        manifest = self._manifest()

        class StartedOnlyBridge(_support.FakeBridge):
            def start_workflow(self, drawing_ids, ip, port, user_id, **kwargs):
                self.workflows.append(list(drawing_ids))
                return {
                    "ok": True,
                    "process_instance_id": 501,
                    "started": True,
                }

        result, _ = _support.run_upload(
            manifest,
            self.journal_file,
            bridge=StartedOnlyBridge(),
            start_workflow=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["workflow"]["status"], "待核实")
        self.assertEqual(result["workflow"]["process_instance_id"], 501)

    def test_relation_phase_does_not_reupload(self):
        item = _support.make_item(
            os.path.dirname(self.journal_file), "01.01", name="test",
            kind="component", content=b"%PDF-1",
        )
        manifest = _support.make_manifest([item])
        first, _ = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(first["ok"])
        manifest["relations"] = [
            {
                "parent_code": "01.99",
                "child_code": "01.01",
                "link_type": "borrow",
                "manual_override": True,
            }
        ]
        second, _ = _support.run_upload(manifest, self.journal_file)
        self.assertTrue(second["ok"])
        self.assertEqual(second["uploaded"], 1)
        self.assertEqual(len(_support.FakeUploader.instances[-1].relations), 1)
        self.assertEqual(len(_support.FakeUploader.instances[-1].relations), 1)

    def test_empty_stale_lock_is_recovered_and_pid_alive_checks_processes(self):
        self.assertTrue(journal._pid_alive(os.getpid()))
        self.assertFalse(journal._pid_alive(999999999))
        with journal.TaskJournal(self.journal_file) as state:
            lock_file = state.lock_path("stale_empty")
            with open(lock_file, "w", encoding="utf-8"):
                pass
            stale_time = os.path.getmtime(lock_file) - 30.0
            os.utime(lock_file, (stale_time, stale_time))
            with state.run_lock("stale_empty"):
                self.assertTrue(os.path.isfile(lock_file))
            self.assertFalse(os.path.isfile(lock_file))


if __name__ == "__main__":
    unittest.main()

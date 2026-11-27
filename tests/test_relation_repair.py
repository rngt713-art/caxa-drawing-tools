"""Final relationship recovery and document completeness, entirely offline."""
import os
import json
import unittest
from unittest.mock import Mock

import _support as support
import journal
import upload_core
from caxa.bridge import CaxaError
from caxa.uploader import CaxaUploader


class FinalRelationRepairTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.temp_dir()
        self.root = self.directory.__enter__()
        self.journal_path = os.path.join(self.root, "journal.sqlite3")
        self.manifest = support.make_manifest(
            [support.make_item(self.root, "01.01", "test")],
            [support.relation("01.99", "01.01", "borrow", manual_override=True)],
        )

    def tearDown(self):
        self.directory.__exit__(None, None, None)

    def uploader(self, initial="失败", repair="已借用", crash=False):
        journal_path = self.journal_path

        class ScriptedUploader(support.FakeUploader):
            def link_relation(self, parent, child, link_type, **kwargs):
                self.relations.append((parent, child, link_type))
                if len(self.relations) > 1:
                    with journal.TaskJournal(journal_path) as state:
                        task = state.list_tasks()[0]
                        operation = state.operations(task["task_id"], "relation")[0]
                        if operation["status"] != journal.OP_RUNNING:
                            raise AssertionError("repair must be journaled before writing")
                    if crash:
                        raise RuntimeError("interruption while repairing")
                return {"status": initial if len(self.relations) == 1 else repair,
                        "note": "scripted result"}

            def verify_relation_pairs(self, relations):
                self.verify_calls = getattr(self, "verify_calls", 0) + 1
                missing = len(self.relations) < 2 or repair != "已借用"
                return {"verified": 0 if missing else 1,
                        "issues": [dict(relations[0], reason="missing_link")]
                        if missing else []}

        return ScriptedUploader

    def run_upload(self, cls, **kwargs):
        return support.run_upload(self.manifest, self.journal_path,
                                  uploader_cls=cls, **kwargs)

    def test_repair_success_clears_failure_and_allows_workflow(self):
        result, bridge = self.run_upload(self.uploader(), start_workflow=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["relation_failures"], 0)
        self.assertEqual(result["relation_pending"], 0)
        self.assertEqual(result["relations"][0]["status"], "已借用")
        self.assertEqual(len(bridge.workflows), 1)
        with journal.TaskJournal(self.journal_path) as state:
            self.assertEqual(state.operations(result["task_id"], "relation")[0]["status"],
                             journal.OP_OK)
        self.assertEqual(support.FakeUploader.instances[-1].verify_calls, 2)

    def test_interrupted_repair_leaves_running_and_blocks_resume(self):
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            self.run_upload(self.uploader(initial="已借用", crash=True))
        with journal.TaskJournal(self.journal_path) as state:
            task = state.list_tasks()[0]
            pending = state.pending_operations(task["task_id"])
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["status"], journal.OP_RUNNING)
            self.assertEqual(task["status"], journal.TASK_PENDING)
        with self.assertRaises(upload_core.PendingVerificationError):
            self.run_upload(self.uploader())

    def test_unknown_initial_write_is_not_repaired_automatically(self):
        result, bridge = self.run_upload(self.uploader(initial="待核实"), start_workflow=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["relation_pending"], 1)
        self.assertEqual(result["relation_failures"], 0)
        self.assertEqual(result["task_status"], journal.TASK_PENDING)
        self.assertEqual(len(support.FakeUploader.instances[-1].relations), 1)
        self.assertFalse(bridge.workflows)

    def test_unknown_repair_is_pending_even_when_readback_reports_missing(self):
        result, bridge = self.run_upload(self.uploader(repair="待核实"), start_workflow=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["relation_pending"], 1)
        self.assertEqual(result["relation_failures"], 0)
        self.assertEqual(result["task_status"], journal.TASK_PENDING)
        self.assertEqual(len(support.FakeUploader.instances[-1].relations), 2)
        self.assertFalse(bridge.workflows)
        with self.assertRaises(upload_core.PendingVerificationError):
            self.run_upload(self.uploader())

    def test_failed_repair_keeps_single_failure(self):
        result, _ = self.run_upload(self.uploader(repair="失败"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["relation_failures"], 1)
        self.assertEqual(len(result["relations"]), 1)

    def test_successful_repair_still_requires_fresh_final_evidence(self):
        class StillMissing(self.uploader()):
            def verify_relation_pairs(inner, relations):
                return {"verified": 0, "issues": [dict(relations[0], reason="missing_link")]}
        result, bridge = self.run_upload(StillMissing, start_workflow=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["relation_failures"], 1)
        self.assertFalse(bridge.workflows)

    def test_incomplete_parent_read_does_not_trigger_repair(self):
        class Unreadable(self.uploader(initial="已借用")):
            def verify_relation_pairs(inner, relations):
                return {"verified": 0,
                        "issues": [dict(relations[0], reason="unreadable_parent")]}
        result, bridge = self.run_upload(Unreadable, start_workflow=True)
        self.assertEqual(len(support.FakeUploader.instances[-1].relations), 1)
        self.assertEqual(result["relation_pending"], 1)
        self.assertEqual(result["task_status"], journal.TASK_PENDING)
        self.assertFalse(bridge.workflows)

    def test_new_success_without_id_cannot_start_incomplete_workflow(self):
        self.manifest["relations"] = []
        self.manifest["items"].append(support.make_item(self.root, "01.02", "second"))
        class MissingEvidence(support.FakeUploader):
            def upload_item(inner, item, *args, **kwargs):
                result = super().upload_item(item, *args, **kwargs)
                if item["code"] == "01.02":
                    result["object_id"] = None
                return result
        with support.patch_core(fake_uploader_cls=MissingEvidence) as bridge:
            with self.assertRaises(upload_core.PendingVerificationError):
                upload_core.upload_bundle(self.manifest, support.ACCOUNT,
                                          journal_path=self.journal_path, start_workflow=True,
                                          staging_root=os.path.join(self.root, "staging"),
                                          log=lambda *args: None)
            self.assertFalse(bridge.workflows)
        with journal.TaskJournal(self.journal_path) as state:
            task = state.list_tasks()[0]
            self.assertEqual(state.pending_operations(task["task_id"])[0]["item_code"], "01.02")

    def test_incomplete_legacy_success_blocks_whole_workflow(self):
        self.manifest["relations"] = []
        self.manifest["items"].append(support.make_item(self.root, "01.02", "second"))
        digest = upload_core.content_digest(self.manifest, support.ACCOUNT)
        with journal.TaskJournal(self.journal_path) as state:
            task = state.create_task(digest=digest, server=support.ACCOUNT["ip"],
                                     port=support.ACCOUNT["port"], user=support.ACCOUNT["user"])
            for code, remote in (("01.01", 101), ("01.02", "")):
                state.record_operation(task["task_id"], "upload", code,
                                       status=journal.OP_OK, item_code=code,
                                       remote_id=remote, class_id=1002)
        with self.assertRaises(upload_core.PendingVerificationError):
            self.run_upload(self.uploader(), start_workflow=True)
        with journal.TaskJournal(self.journal_path) as state:
            self.assertEqual(len(state.pending_operations(task["task_id"])), 1)
            self.assertEqual(state.get_operation(task["task_id"], "upload", "01.01")["status"],
                             journal.OP_OK)

    def test_explicit_invalid_or_wrong_class_cannot_hide_behind_class_name(self):
        for record in ({"remote_id": "101", "class_id": "1001", "class_name": "Drawing"},
                       {"remote_id": "101", "class_id": "1003", "class_name": "CappDocument"}):
            with self.subTest(record=record):
                with journal.TaskJournal(self.journal_path) as state:
                    task = state.create_task(digest="legacy-test")
                    state.record_operation(task["task_id"], "upload", "01.01",
                                           status=journal.OP_OK, item_code="01.01", **record)
                    with self.assertRaises(upload_core.PendingVerificationError):
                        upload_core._require_document_evidence(
                            state, task["task_id"], state.confirmed_uploads(task["task_id"]), {"01.01"})
                    self.assertEqual(len(state.pending_operations(task["task_id"])), 1)
        self.assertIsNone(upload_core._workflow_class_id(
            {"class_id": "1001", "class_name": "Drawing"}))

    def test_incomplete_legacy_workflow_cannot_be_restored_as_success(self):
        cases = [("", {"started": True, "drawing_ids": [101]}),
                 (0, {"started": True, "drawing_ids": [101]}),
                 (901, {"drawing_ids": [101]}),
                 (901, {"started": False, "drawing_ids": [101]}),
                 (901, {"started": True}),
                 (901, {"started": True, "drawing_ids": [0]}),
                 (901, "old freeform manual confirmation")]
        for remote, payload in cases:
            with self.subTest(remote=remote, payload=payload):
                with journal.TaskJournal(self.journal_path) as state:
                    task = state.create_task(digest="workflow-test")
                    detail = json.dumps(payload) if isinstance(payload, dict) else payload
                    state.record_operation(task["task_id"], "workflow", "workflow",
                                           status=journal.OP_OK, remote_id=remote, detail=detail)
                    with self.assertRaises(upload_core.PendingVerificationError):
                        upload_core._require_workflow_evidence(
                            state, task["task_id"], state.workflow(task["task_id"]))
                    row = state.workflow(task["task_id"])
                    self.assertEqual(row["status"], journal.OP_PENDING)
                    self.assertEqual(row["detail"], detail)

    def test_genuine_old_workflow_without_new_readback_flags_is_preserved(self):
        with journal.TaskJournal(self.journal_path) as state:
            task = state.create_task(digest="workflow-compat")
            state.record_workflow(task["task_id"], status=journal.OP_OK,
                                  process_id=901, drawing_ids=[101], started=True)
            upload_core._require_workflow_evidence(
                state, task["task_id"], state.workflow(task["task_id"]))
            self.assertEqual(state.workflow(task["task_id"])["status"], journal.OP_OK)

    def test_historical_class_name_still_provides_document_type(self):
        self.assertEqual(upload_core._confirmed_workflow_documents(
            {"01.01": {"remote_id": "101", "class_name": "Drawing"}}, {"01.01"}),
            [{"object_id": 101, "class_id": 1002, "class_name": "Drawing"}])
        self.assertEqual(upload_core._confirmed_workflow_documents(
            {"01.01": {"remote_id": "0", "class_name": "Drawing"}}, {"01.01"}), [])


class RelationReadbackTests(unittest.TestCase):
    def uploader(self):
        return CaxaUploader(support.FakeBridge(), log=lambda *args: None)

    def test_one_parent_read_per_pass_and_next_pass_is_fresh(self):
        uploader = self.uploader()
        reads = []
        nodes = {"01.P": 1, "01.A": 2, "01.B": 3}
        lookups = []
        def lookup(code):
            lookups.append(code)
            return {"object_id": nodes[code]}
        uploader.find_part = lookup
        present = {2, 3}
        def part_info(parent):
            reads.append(parent)
            return {"children": [{"sub_id": child, "reference": True} for child in present]}
        uploader.part_info = part_info
        relations = [support.relation("01.P", "01.A", "borrow"),
                     support.relation("01.P", "01.B", "borrow")]
        self.assertEqual(uploader.verify_relation_pairs(relations)["verified"], 2)
        self.assertEqual(reads, [1])
        self.assertEqual(lookups.count("01.P"), 1)
        present.remove(2)
        result = uploader.verify_relation_pairs(relations)
        self.assertEqual(reads, [1, 1])
        self.assertEqual(result["verified"], 1)
        self.assertEqual(result["issues"][0]["reason"], "missing_link")

    def test_partial_children_read_is_unknown_but_known_endpoint_is_valid(self):
        uploader = self.uploader()
        uploader.find_part = lambda code: {"object_id": {"01.P": 1, "01.A": 2, "01.B": 3}[code]}
        uploader.part_info = lambda parent: {
            "children_error": "access/read error", "children": [],
            "link_endpoints": [{"roleA": {"object_id": 1}, "roleB": {"object_id": 2},
                                "reference": True}],
        }
        result = uploader.verify_relation_pairs([
            support.relation("01.P", "01.A", "borrow"),
            support.relation("01.P", "01.B", "borrow")])
        self.assertEqual(result["verified"], 1)
        self.assertEqual(result["issues"][0]["reason"], "unreadable_parent")

    def test_incomplete_read_never_proves_a_missing_link(self):
        uploader = self.uploader()
        uploader.find_part = lambda code: {"object_id": {"01.P": 1, "01.A": 2}[code]}
        expected = [support.relation("01.P", "01.A", "borrow")]
        for info in ({"link_endpoints": []}, {"refresh_error": "stale parent", "children": []},
                     {"children": [{"sub_id": None}], "link_endpoints": [{"link_error": "stale link"}]}):
            with self.subTest(info=info):
                uploader.part_info = lambda _parent: info
                self.assertEqual(
                    uploader.verify_relation_pairs(expected)["issues"][0]["reason"],
                    "unreadable_parent",
                )
                with self.assertRaises(CaxaError):
                    uploader._child_link_state(1, 2)

    def test_positive_low_level_relation_remains_valid_when_child_pointer_is_missing(self):
        uploader = self.uploader()
        uploader.part_info = lambda _parent: {
            "children": [{"sub_id": None, "reference": True}],
            "link_endpoints": [{"roleA": {"object_id": 1}, "roleB": {"object_id": 2}, "reference": True}],
        }
        self.assertEqual(uploader._child_link_state(1, 2), (True, True))

    def test_read_error_after_link_is_pending_not_safe_to_replay(self):
        uploader = self.uploader()
        uploader.find_part = lambda code: {"object_id": 1 if code == "01.P" else 2}
        uploader.link_part = Mock()
        uploader._child_link_state = Mock(side_effect=[
            (False, None), CaxaError("read failed", stage="part_info")])
        result = uploader.link_relation("01.P", "01.A", "borrow")
        self.assertEqual(result["status"], "待核实")
        uploader.link_part.assert_called_once()

    def test_missing_nodes_queried_once_per_pass(self):
        uploader = self.uploader()
        lookups = []
        def lookup(code):
            lookups.append(code)
            return None
        uploader.find_part = lookup
        relations = [support.relation("01.P", "01.A", "borrow"),
                     support.relation("01.P", "01.B", "borrow")]
        uploader.verify_relation_pairs(relations)
        self.assertEqual(lookups.count("01.P"), 1)
        uploader.verify_relation_pairs(relations)
        self.assertEqual(lookups.count("01.P"), 2)

    def test_upload_bundle_cleans_staging_dir_on_success_and_failure(self):
        with support.temp_dir("staging_cleanup_") as root:
            staging_root = os.path.join(root, "staging")
            journal_path = os.path.join(root, "journal.sqlite3")
            item = support.make_item(root, "01.01", "first")
            manifest = support.make_manifest([item])
            result, _ = support.run_upload(
                manifest, journal_path, staging_root=staging_root
            )
            self.assertTrue(result["ok"])
            self.assertEqual(os.listdir(staging_root), [])

            class FailingUploader(support.FakeUploader):
                def upload_item(inner, item, *args, **kwargs):
                    raise upload_core.CaxaError("boom", stage="attach")

            item2 = support.make_item(root, "01.02", "second")
            manifest2 = support.make_manifest([item2])
            failed_result, _ = support.run_upload(
                manifest2,
                journal_path,
                uploader_cls=FailingUploader,
                staging_root=staging_root,
            )
            self.assertFalse(failed_result["ok"])
            self.assertEqual(os.listdir(staging_root), [])

            class CrashingUploader(support.FakeUploader):
                def upload_item(inner, item, *args, **kwargs):
                    raise RuntimeError("unexpected crash")

            item3 = support.make_item(root, "01.03", "third")
            manifest3 = support.make_manifest([item3])
            with self.assertRaises(RuntimeError):
                support.run_upload(
                    manifest3,
                    journal_path,
                    uploader_cls=CrashingUploader,
                    staging_root=staging_root,
                )
            self.assertEqual(os.listdir(staging_root), [])

    def test_half_product_target_state_blocks_when_part_info_fails(self):
        class BrokenPartInfoUploader:
            def find_product(self, identity):
                return {"object_id": 2, "identity": identity}

            def find_part_candidates(self, code):
                if code == "02.A36.02.AR02":
                    return [{"object_id": 20, "identity": code}]
                if code == "02.A36.02":
                    return [{"object_id": 10, "identity": code}]
                return []

            def part_info(self, part_id):
                raise RuntimeError("rpc timeout")

        state = upload_core.half_product_target_state(
            BrokenPartInfoUploader(), "02.A36.02.AR02"
        )
        self.assertIn("读取半成品零件关系失败", state["blocked_reason"])
        self.assertIn("rpc timeout", state["blocked_reason"])


if __name__ == "__main__":
    unittest.main()

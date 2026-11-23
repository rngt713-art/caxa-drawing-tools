"""A workflow-only retry uses confirmed uploads and never reuploads PDFs."""

import json
import os
import unittest

import _support as support
import journal
import upload_core
from caxa.bridge import CaxaError


class WorkflowOnlyTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.temp_dir()
        self.root = self.directory.__enter__()
        self.journal_path = os.path.join(self.root, "journal.sqlite3")
        self.item = support.make_item(self.root, "01.01", "test")
        self.manifest = support.make_manifest([self.item], [])

    def tearDown(self):
        self.directory.__exit__(None, None, None)

    def test_confirmed_batch_can_start_workflow_after_local_pdf_is_gone(self):
        first, _bridge = support.run_upload(
            self.manifest, self.journal_path, start_workflow=False
        )
        self.assertTrue(first["ok"])
        os.remove(self.item["file_path"])

        second, bridge = support.run_upload(
            self.manifest,
            self.journal_path,
            start_workflow=True,
            workflow_only=True,
        )
        self.assertTrue(second["ok"])
        self.assertEqual(len(bridge.workflows), 1)
        self.assertEqual(
            support.FakeUploader.instances[-1].uploads,
            0,
        )
        self.assertEqual(second["workflow"]["process_instance_id"], 901)

    def test_workflow_only_subset_reuses_full_batch_task(self):
        second_item = support.make_item(
            self.root,
            "01.02",
            "second",
            content=b"%PDF second",
        )
        self.manifest["items"].append(second_item)
        self.manifest["batch_id"] = "B-workflow-subset"
        first, _bridge = support.run_upload(
            self.manifest, self.journal_path, start_workflow=False
        )
        self.assertTrue(first["ok"])
        self.assertEqual(first["uploaded"], 2)

        second_item["upload_sel"] = False
        workflow, bridge = support.run_upload(
            self.manifest,
            self.journal_path,
            start_workflow=True,
            workflow_only=True,
        )
        self.assertTrue(workflow["ok"])
        self.assertEqual(workflow["task_id"], first["task_id"])
        self.assertEqual(support.FakeUploader.instances[-1].uploads, 0)
        self.assertEqual(len(bridge.workflows), 1)

    def test_additional_workflow_uses_only_documents_without_an_existing_process(self):
        bridge = support.FakeBridge()
        second = support.make_item(self.root, "01.02", "second")
        self.manifest["items"].append(second)
        self.manifest["batch_id"] = "B-workflow-coverage"
        uploaded, _ = support.run_upload(self.manifest, self.journal_path)

        second["upload_sel"] = False
        first_flow, _ = support.run_upload(
            self.manifest, self.journal_path, bridge, start_workflow=True, workflow_only=True,
        )
        self.assertEqual(first_flow["workflow"]["drawing_ids"], [101])

        second["upload_sel"] = True
        second_flow, _ = support.run_upload(
            self.manifest, self.journal_path, bridge, start_workflow=True, workflow_only=True,
        )
        self.assertEqual(second_flow["workflow"]["drawing_ids"], [102])
        self.assertEqual(bridge.workflows, [[101], [102]])

        repeated, _ = support.run_upload(
            self.manifest, self.journal_path, bridge, start_workflow=True, workflow_only=True,
        )
        self.assertTrue(repeated["ok"])
        self.assertTrue(repeated["workflow"]["duplicate"])
        self.assertEqual(repeated["workflow"]["process_instance_ids"], ["901", "902"])
        self.assertEqual(bridge.workflows, [[101], [102]])
        with support.open_journal(self.journal_path) as state:
            records = state.operations(uploaded["task_id"], "workflow")
        self.assertEqual([json.loads(row["detail"])["drawing_ids"] for row in records], [[101], [102]])
        self.assertEqual(records[0]["key"], "workflow")
        self.assertNotEqual(records[0]["key"], records[1]["key"])

    def test_upload_and_workflow_with_later_new_drawing_keeps_old_process(self):
        class IncrementalUploader(support.FakeUploader):
            def upload_item(self, item, *args, **kwargs):
                result = super().upload_item(item, *args, **kwargs)
                result["object_id"] = {"01.01": 101, "01.02": 102}[item["code"]]
                return result

        self.manifest["batch_id"] = "B-incremental-workflow"
        bridge = support.FakeBridge()
        first, _ = support.run_upload(
            self.manifest, self.journal_path, bridge,
            uploader_cls=IncrementalUploader, start_workflow=True,
        )
        self.manifest["items"].append(support.make_item(self.root, "01.02", "second"))
        second, _ = support.run_upload(
            self.manifest, self.journal_path, bridge,
            uploader_cls=IncrementalUploader, start_workflow=True,
        )
        self.assertEqual(second["task_id"], first["task_id"])
        self.assertTrue(second["ok"])
        self.assertEqual(support.FakeUploader.instances[-1].uploads, 1)
        self.assertEqual(bridge.workflows, [[101], [102]])
        self.assertEqual(second["workflow"]["drawing_ids"], [102])

    def test_recovering_a_later_workflow_keeps_the_first_workflow_evidence(self):
        class MissingWorkflowReply(support.FakeBridge):
            def start_workflow(self, drawing_ids, *args, **kwargs):
                self.workflows.append(drawing_ids)
                raise CaxaError("reply lost", response_lost=True)

        second = support.make_item(self.root, "01.02", "second")
        self.manifest["items"].append(second)
        self.manifest["batch_id"] = "B-workflow-recovery"
        uploaded, _ = support.run_upload(self.manifest, self.journal_path)
        second["upload_sel"] = False
        support.run_upload(self.manifest, self.journal_path, start_workflow=True, workflow_only=True)
        second["upload_sel"] = True

        pending, bridge = support.run_upload(
            self.manifest, self.journal_path, MissingWorkflowReply(),
            start_workflow=True, workflow_only=True,
        )
        self.assertEqual(pending["workflow"]["status"], upload_core.WORKFLOW_PENDING)
        self.assertEqual(bridge.workflows, [[102]])
        with support.open_journal(self.journal_path) as state:
            row, = state.pending_operations(uploaded["task_id"])
            self.assertNotEqual(row["key"], "workflow")
            self.assertEqual(json.loads(row["detail"])["drawing_ids"], [102])
            state.resolve_operation(
                uploaded["task_id"], "workflow", row["key"], result="success",
                remote_id=999, process_state=2, linked_document_ids=[102],
            )
            self.assertEqual(state.workflow(uploaded["task_id"])["status"], journal.OP_OK)
        repeated, bridge = support.run_upload(
            self.manifest, self.journal_path, start_workflow=True, workflow_only=True,
        )
        self.assertEqual(repeated["workflow"]["process_instance_ids"], ["901", "999"])
        self.assertEqual(bridge.workflows, [])

    def test_missing_previously_confirmed_relation_blocks_without_repair(self):
        class MissingRemoteRelation(support.FakeUploader):
            def verify_relation_pairs(self, expected):
                return {"verified": 0, "issues": [dict(expected[0], reason="missing_link")]}

        self.manifest["relations"] = [
            support.relation("01.99", "01.01", "borrow", manual_override=True)
        ]
        first, _ = support.run_upload(self.manifest, self.journal_path)
        self.assertTrue(first["ok"])
        result, bridge = support.run_upload(
            self.manifest, self.journal_path, start_workflow=True, workflow_only=True,
            uploader_cls=MissingRemoteRelation,
        )
        self.assertFalse(result["ok"])
        self.assertIn("01.99 ← 01.01", result["workflow_blocked"])
        self.assertEqual(bridge.workflows, [])
        self.assertEqual(support.FakeUploader.instances[-1].relations, [])

    def test_unconfirmed_batch_is_blocked_before_upload_or_workflow(self):
        with self.assertRaisesRegex(CaxaError, "没有找到这批"):
            support.run_upload(
                self.manifest,
                self.journal_path,
                start_workflow=True,
                workflow_only=True,
            )
        with support.open_journal(self.journal_path) as state:
            self.assertEqual(state.list_tasks(), [])

    def test_unconfirmed_relation_blocks_workflow_only(self):
        first, _bridge = support.run_upload(
            self.manifest, self.journal_path, start_workflow=False
        )
        self.assertTrue(first["ok"])
        self.manifest["relations"] = [
            support.relation("03.HW.001", "01.01", "borrow")
        ]
        with self.assertRaisesRegex(CaxaError, "仍有未确认成功"):
            support.run_upload(
                self.manifest,
                self.journal_path,
                start_workflow=True,
                workflow_only=True,
            )


if __name__ == "__main__":
    unittest.main()

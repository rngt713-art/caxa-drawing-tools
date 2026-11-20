"""A workflow-only retry uses confirmed uploads and never reuploads PDFs."""

import os
import unittest

import _support as support
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

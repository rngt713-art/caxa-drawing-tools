import unittest

import _support
import upload_core
from caxa.bridge import CaxaError


class ManualBorrowTests(unittest.TestCase):
    def manifest(self, relations):
        return _support.make_manifest([], relations=relations)

    def run_manual(self, relation, behaviour=None):
        journal_path = _support.journal_path("manual.sqlite3")
        with _support.patch_core() as bridge:
            uploader_cls = _support.FakeUploader
            original = uploader_cls.link_relation
            if behaviour is not None:
                uploader_cls.link_relation = behaviour
            try:
                result = upload_core.apply_manual_borrows(
                    self.manifest([relation]),
                    _support.ACCOUNT,
                    log=lambda *args: None,
                    journal_path=journal_path,
                )
            finally:
                uploader_cls.link_relation = original
        return result, bridge

    def test_successful_manual_borrow(self):
        result, _ = self.run_manual(
            _support.relation("03.HW.001", "04.001", "borrow")
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["borrowed"], 1)
        self.assertEqual(result["rows"][0]["status"], "已借用")

    def test_missing_parent_is_failure(self):
        def missing(self, parent, child, link_type, **kwargs):
            return {
                "parent_code": parent,
                "child_code": child,
                "link_type": link_type,
                "status": "缺父",
                "note": "parent missing",
            }

        result, _ = self.run_manual(
            _support.relation("03.HW.001", "04.001", "borrow"),
            missing,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["missing"], 1)

    def test_uncertain_write_remains_pending(self):
        def lost(self, parent, child, link_type, **kwargs):
            raise CaxaError("lost reply", response_lost=True)

        result, _ = self.run_manual(
            _support.relation("03.HW.001", "04.001", "borrow"),
            lost,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["rows"][0]["status"], "待核实")

    def test_second_relation_failure_keeps_first_result(self):
        calls = {"count": 0}

        def second_fails(self, parent, child, link_type, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return {
                    "parent_code": parent,
                    "child_code": child,
                    "link_type": link_type,
                    "status": "已借用",
                    "note": "",
                }
            raise CaxaError("lost second reply", response_lost=True)

        relations = [
            _support.relation("03.HW.001", "04.001", "borrow"),
            _support.relation("03.HW.001", "04.002", "borrow"),
        ]
        journal_path = _support.journal_path("manual_two.sqlite3")
        with _support.patch_core():
            original = _support.FakeUploader.link_relation
            _support.FakeUploader.link_relation = second_fails
            try:
                result = upload_core.apply_manual_borrows(
                    self.manifest(relations),
                    _support.ACCOUNT,
                    log=lambda *args: None,
                    journal_path=journal_path,
                )
            finally:
                _support.FakeUploader.link_relation = original
        self.assertEqual(result["borrowed"], 1)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(
            [row["status"] for row in result["rows"]],
            ["已借用", "待核实"],
        )

    def test_pending_manual_borrow_preserves_error_note_and_exception_marks_task_failed(self):
        import journal as journal_module

        def lost(self, parent, child, link_type, **kwargs):
            raise CaxaError("lost reply", response_lost=True)

        journal_path = _support.journal_path("manual_note.sqlite3")
        with _support.patch_core():
            original = _support.FakeUploader.link_relation
            _support.FakeUploader.link_relation = lost
            try:
                result = upload_core.apply_manual_borrows(
                    self.manifest([_support.relation("03.HW.001", "04.001", "borrow")]),
                    _support.ACCOUNT,
                    log=lambda *args: None,
                    journal_path=journal_path,
                )
            finally:
                _support.FakeUploader.link_relation = original
        with journal_module.TaskJournal(journal_path) as state:
            op = state.get_operation(result["task_id"], "relation", "03.HW.001|04.001")
            self.assertIn("lost reply", op["error"])

        fail_journal = _support.journal_path("manual_fail.sqlite3")
        with _support.patch_core() as bridge:
            bridge.login = lambda *args, **kwargs: (_ for _ in ()).throw(CaxaError("login down"))
            with self.assertRaises(CaxaError):
                upload_core.apply_manual_borrows(
                    self.manifest([_support.relation("03.HW.001", "04.001", "borrow")]),
                    _support.ACCOUNT,
                    log=lambda *args: None,
                    journal_path=fail_journal,
                )
        with journal_module.TaskJournal(fail_journal) as state:
            tasks = state.list_tasks()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["status"], journal_module.TASK_FAILED)


if __name__ == "__main__":
    unittest.main()

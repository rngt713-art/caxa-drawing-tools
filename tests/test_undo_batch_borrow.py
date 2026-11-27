"""Offline regression tests for batch-scoped borrow link undo."""

import json
import os
import unittest

import _support
import journal as journal_module
import recovery_verifier
import upload_core
from caxa.bridge import CaxaError
from caxa.uploader import CaxaUploader


class _BatchBorrowFakeBridge(_support.FakeBridge):
    def __init__(self):
        super().__init__()
        self.part_ids = {"03.HW.001": 100, "04.001": 200, "01.01": 10, "01.01-01": 11}
        self.links_by_parent = {}
        self.next_link_id = 5000
        self.unlink_calls = []
        self.fail_unlink_uncertain = False

    def unlink_part_relation(
        self,
        parent_id: int,
        child_id: int,
        link_id: int,
        link_class_id: int = 1037,
        *,
        require_reference: bool = True,
        timeout: float = 120.0,
    ) -> dict:
        return self.call(
            {
                "cmd": "unlink_part_relation",
                "parent_id": int(parent_id),
                "child_id": int(child_id),
                "link_id": int(link_id),
                "link_class_id": int(link_class_id),
                "require_reference": bool(require_reference),
            },
            timeout=timeout,
            idempotent=False,
        )

    def call(self, cmd, timeout=240.0, idempotent=None):
        self.calls.append((cmd.get("cmd"), dict(cmd)))
        name = cmd.get("cmd")
        if name == "query_parts":
            keyword = str(cmd.get("keyword") or "").upper()
            if keyword in self.part_ids:
                return {
                    "ok": True,
                    "items": [
                        {
                            "object_id": self.part_ids[keyword],
                            "identity": keyword,
                            "name": keyword,
                            "material_no": keyword,
                        }
                    ],
                }
            return {"ok": True, "items": []}
        if name == "create_part":
            ident = str(cmd.get("identity_no") or "").upper()
            oid = self.part_ids.setdefault(ident, 800 + len(self.part_ids))
            return {"ok": True, "object_id": oid, "identity": ident}
        if name == "ensure_part_material_code":
            return {"ok": True}
        if name == "upload":
            return {
                "ok": True,
                "attached": True,
                "object_id": 700 + len(self.calls),
                "class_id": 1002,
                "class_name": "Drawing",
                "iterator_no": "a.1",
            }
        if name == "part_drawings":
            return {"ok": True, "drawings": []}
        if name == "part_info":
            pid = int(cmd.get("part_id") or 0)
            links = list(self.links_by_parent.get(pid) or [])
            return {
                "ok": True,
                "children": [
                    {
                        "sub_id": ep["roleB"]["object_id"],
                        "reference": ep["reference"],
                    }
                    for ep in links
                ],
                "link_endpoints": links,
                "parents": [],
                "products": [],
            }
        if name == "link_part":
            if cmd.get("product_link"):
                return {"ok": True, "link": "product_part", "link_id": 999}
            pid = int(cmd.get("parent_id") or 0)
            cid = int(cmd.get("child_id") or 0)
            ref = bool(cmd.get("reference"))
            self.next_link_id += 1
            lid = self.next_link_id
            self.links_by_parent.setdefault(pid, []).append(
                {
                    "link_id": lid,
                    "link_class": 1037,
                    "reference": ref,
                    "roleA": {"object_id": pid},
                    "roleB": {"object_id": cid},
                }
            )
            return {
                "ok": True,
                "link": "part_part_low" if ref else "part_part",
                "link_class_id": 1037,
                "link_id": lid,
                "reference": ref,
            }
        if name == "unlink_part_relation":
            self.unlink_calls.append(dict(cmd))
            if self.fail_unlink_uncertain:
                raise CaxaError("lost reply during unlink", response_lost=True)
            pid = int(cmd.get("parent_id") or 0)
            lid = int(cmd.get("link_id") or 0)
            self.links_by_parent[pid] = [
                ep
                for ep in self.links_by_parent.get(pid) or []
                if int(ep.get("link_id") or 0) != lid
            ]
            return {"ok": True, "deleted_relation_only": True}
        if name == "find_product":
            ident = str(cmd.get("keyword") or cmd.get("identity") or "")
            if ident in ("01", "通用件"):
                return {
                    "ok": True,
                    "found": [{"object_id": 1, "identity": "01", "name": "通用件"}],
                }
            return {
                "ok": True,
                "found": [{"object_id": 2, "identity": ident, "name": ident}],
            }
        if name == "list_products":
            return {
                "ok": True,
                "products": [{"object_id": 1, "identity": "01", "name": "通用件"}],
                "complete": True,
                "errors": [],
            }
        if name == "find_product_folder":
            kw = str(cmd.get("keyword") or "")
            return {"ok": True, "found": [{"object_id": 401, "path": kw}]}
        if name == "product_folder_index":
            return {"ok": True, "items": [], "complete": True, "errors": []}
        return super().call(cmd, timeout=timeout, idempotent=idempotent)


class UndoBatchBorrowTests(unittest.TestCase):
    def setUp(self):
        self._dir = _support.temp_dir("undo_borrow_")
        self.root = self._dir.__enter__()
        self.journal_path = os.path.join(self.root, "journal.sqlite3")
        self.staging_root = os.path.join(self.root, "staging")

    def tearDown(self):
        self._dir.__exit__(None, None, None)

    def _run_real_uploader(self, manifest, bridge, **kwargs):
        with _support.patch_core(fake_bridge=bridge, fake_uploader_cls=CaxaUploader):
            return upload_core.upload_bundle(
                manifest,
                _support.ACCOUNT,
                journal_path=self.journal_path,
                staging_root=self.staging_root,
                log=lambda *a: None,
                **kwargs,
            )

    def test_new_borrow_records_link_evidence_and_can_be_undone_and_relinked(self):
        bridge = _BatchBorrowFakeBridge()
        item_p = _support.make_item(
            self.root, "03.HW.001", "整机", kind="machine_drawing", machine="03.HW.001"
        )
        item_c = _support.make_item(self.root, "04.001", "组件", kind="component")
        manifest = _support.make_manifest(
            [item_p, item_c],
            relations=[
                _support.relation("03.HW.001", "04.001", "borrow", manual_override=True)
            ],
        )

        result = self._run_real_uploader(manifest, bridge)
        self.assertTrue(result["ok"])
        rel = manifest["relations"][0]
        self.assertTrue(rel["created_by_batch"])
        self.assertGreater(rel["link_id"], 0)
        self.assertEqual(rel["link_class_id"], 1037)
        self.assertFalse(rel["workflow_locked"])

        with _support.patch_core(fake_bridge=bridge, fake_uploader_cls=CaxaUploader):
            undo_res = upload_core.undo_batch_borrow(
                manifest,
                _support.ACCOUNT,
                "03.HW.001",
                "04.001",
                journal_path=self.journal_path,
                log=lambda *a: None,
            )
        self.assertTrue(undo_res["ok"])
        self.assertEqual(len(bridge.unlink_calls), 1)
        self.assertTrue(bridge.unlink_calls[0]["require_reference"])
        self.assertEqual(bridge.links_by_parent.get(100), [])

        with journal_module.TaskJournal(self.journal_path) as state:
            op = state.get_operation(result["task_id"], "relation", "03.HW.001|04.001")
            self.assertEqual(op["status"], journal_module.OP_UNLINKED)
            self.assertEqual(state.confirmed_relations(result["task_id"]), {})

        undoable_after = upload_core.batch_undoable_borrows(
            manifest, _support.ACCOUNT, journal_path=self.journal_path
        )
        self.assertEqual(undoable_after, {})

        # Re-uploading with the corrected plan cleanly recreates the borrow link.
        re_result = self._run_real_uploader(manifest, bridge)
        self.assertTrue(re_result["ok"])
        self.assertEqual(len(bridge.links_by_parent.get(100) or []), 1)

    def test_existing_borrow_and_own_relations_are_refused(self):
        bridge = _BatchBorrowFakeBridge()
        # Only 03.HW.001 and 04.001 pre-exist; 01.01 and 01.01-01 are new own parts.
        bridge.part_ids = {"03.HW.001": 100, "04.001": 200}
        # Pre-existing borrow link in CAXA before this batch runs.
        bridge.links_by_parent[100] = [
            {
                "link_id": 4444,
                "link_class": 1037,
                "reference": True,
                "roleA": {"object_id": 100},
                "roleB": {"object_id": 200},
            }
        ]
        item_p = _support.make_item(
            self.root, "03.HW.001", "整机", kind="machine_drawing", machine="03.HW.001"
        )
        item_c = _support.make_item(self.root, "04.001", "组件", kind="component")
        item_o1 = _support.make_item(self.root, "01.01", "父件", kind="component")
        item_o2 = _support.make_item(self.root, "01.01-01", "子件", kind="component")
        manifest = _support.make_manifest(
            [item_p, item_c, item_o1, item_o2],
            relations=[
                _support.relation("03.HW.001", "04.001", "borrow", manual_override=True),
                _support.relation("01.01", "01.01-01", "own"),
            ],
        )

        result = self._run_real_uploader(manifest, bridge)
        self.assertTrue(result["ok"])
        self.assertFalse(manifest["relations"][0].get("created_by_batch"))
        self.assertFalse(manifest["relations"][1].get("created_by_batch"))

        for parent, child in (("03.HW.001", "04.001"), ("01.01", "01.01-01")):
            with self.subTest(parent=parent, child=child):
                with _support.patch_core(fake_bridge=bridge, fake_uploader_cls=CaxaUploader):
                    with self.assertRaises(CaxaError) as caught:
                        upload_core.undo_batch_borrow(
                            manifest,
                            _support.ACCOUNT,
                            parent,
                            child,
                            journal_path=self.journal_path,
                            log=lambda *a: None,
                        )
                self.assertEqual(caught.exception.stage, "undo_borrow_guard")
        self.assertEqual(bridge.unlink_calls, [])

    def test_confirmed_workflow_locks_batch_borrow_undo(self):
        bridge = _BatchBorrowFakeBridge()
        item_p = _support.make_item(
            self.root, "03.HW.001", "整机", kind="machine_drawing", machine="03.HW.001"
        )
        item_c = _support.make_item(self.root, "04.001", "组件", kind="component")
        manifest = _support.make_manifest(
            [item_p, item_c],
            relations=[
                _support.relation("03.HW.001", "04.001", "borrow", manual_override=True)
            ],
        )
        result = self._run_real_uploader(manifest, bridge, start_workflow=True)
        self.assertTrue(result["ok"])
        self.assertTrue(manifest["relations"][0]["workflow_locked"])

        with _support.patch_core(fake_bridge=bridge, fake_uploader_cls=CaxaUploader):
            with self.assertRaises(CaxaError) as caught:
                upload_core.undo_batch_borrow(
                    manifest,
                    _support.ACCOUNT,
                    "03.HW.001",
                    "04.001",
                    journal_path=self.journal_path,
                    log=lambda *a: None,
                )
        self.assertEqual(caught.exception.stage, "undo_borrow_workflow_locked")
        self.assertEqual(bridge.unlink_calls, [])

    def test_uploader_unlink_precheck_aborts_when_remote_is_own(self):
        bridge = _BatchBorrowFakeBridge()
        bridge.links_by_parent[100] = [
            {
                "link_id": 5001,
                "link_class": 1037,
                "reference": False,
                "roleA": {"object_id": 100},
                "roleB": {"object_id": 200},
            }
        ]
        uploader = CaxaUploader(bridge, log=lambda *a: None)
        with self.assertRaises(CaxaError) as caught:
            uploader.unlink_batch_borrow(
                "03.HW.001",
                "04.001",
                parent_id=100,
                child_id=200,
                link_id=5001,
                link_class_id=1037,
            )
        self.assertEqual(caught.exception.stage, "unlink_borrow_guard")
        self.assertEqual(bridge.unlink_calls, [])

    def test_uncertain_unlink_marks_pending_and_blocks_verify_as_success(self):
        bridge = _BatchBorrowFakeBridge()
        item_p = _support.make_item(
            self.root, "03.HW.001", "整机", kind="machine_drawing", machine="03.HW.001"
        )
        item_c = _support.make_item(self.root, "04.001", "组件", kind="component")
        manifest = _support.make_manifest(
            [item_p, item_c],
            relations=[
                _support.relation("03.HW.001", "04.001", "borrow", manual_override=True)
            ],
        )
        result = self._run_real_uploader(manifest, bridge)
        bridge.fail_unlink_uncertain = True

        with _support.patch_core(fake_bridge=bridge, fake_uploader_cls=CaxaUploader):
            with self.assertRaises(upload_core.PendingVerificationError):
                upload_core.undo_batch_borrow(
                    manifest,
                    _support.ACCOUNT,
                    "03.HW.001",
                    "04.001",
                    journal_path=self.journal_path,
                    log=lambda *a: None,
                )

        with journal_module.TaskJournal(self.journal_path) as state:
            op = state.get_operation(result["task_id"], "relation", "03.HW.001|04.001")
            self.assertEqual(op["status"], journal_module.OP_PENDING)
            self.assertEqual(op["phase"], "relation_unlink_uncertain")
            verifier = recovery_verifier.RecoveryVerifier(
                bridge, CaxaUploader(bridge, log=lambda *a: None)
            )
            with self.assertRaises(recovery_verifier.RecoveryEvidenceError):
                verifier.verify_relation(op)


if __name__ == "__main__":
    unittest.main()

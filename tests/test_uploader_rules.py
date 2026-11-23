"""Upload/link rules: explicit parent, own vs borrow, partial completion, class."""

import os
import unittest
import uuid
from unittest.mock import patch

import _support
import drawing_bundle
import staging
import upload_core
from caxa.bridge import CaxaError
from caxa.uploader import DRAWING_2D, CaxaUploader, UnverifiedResult


class RuleBridge:
    def __init__(self, upload_result=None, children=None, fail_product_link=False):
        self.calls = []
        self.link_calls = []
        self.upload_result = upload_result or {
            "ok": True,
            "attached": True,
            "object_id": 50,
            "class_name": "Drawing",
            "class_id": 1002,
            "method": "attach",
        }
        self.children = children if children is not None else []
        self.fail_product_link = fail_product_link
        # Simulate the read-back after a successful link.
        self.record_links = True
        self.reference_override = None

    def call(self, cmd, timeout=240.0, idempotent=None):
        self.calls.append(cmd)
        name = cmd.get("cmd")
        if name == "upload":
            return dict(self.upload_result)
        if name == "part_info":
            return {"ok": True, "children": list(self.children), "link_endpoints": []}
        if name == "link_part":
            self.link_calls.append(dict(cmd))
            if cmd.get("product_link") and self.fail_product_link:
                raise CaxaError("product link denied")
            if self.record_links and not cmd.get("product_link"):
                reference = cmd.get("reference")
                if self.reference_override is not None:
                    reference = self.reference_override
                self.children.append(
                    {"sub_id": cmd.get("child_id"), "reference": reference}
                )
            return {"ok": True}
        if name == "part_drawings":
            return {"ok": True, "drawings": []}
        if name == "query_parts":
            return {"ok": True, "items": []}
        if name in ("find_product", "find_product_in_folder"):
            return {"ok": True, "found": []}
        if name == "find_product_folder":
            keyword = cmd.get("keyword")
            return {
                "ok": True,
                "found": [
                    {
                        "object_id": 401,
                        "identity": keyword,
                        "name": keyword.rsplit("/", 1)[-1],
                        "path": keyword,
                    }
                ],
            }
        if name == "ensure_product_folder":
            keyword = cmd.get("keyword")
            return {
                "ok": True,
                "object_id": 401,
                "identity": keyword,
                "name": keyword.rsplit("/", 1)[-1],
                "path": keyword,
                "existing": True,
                "created": False,
                "created_paths": [],
            }
        if name == "create_product":
            return {
                "ok": True,
                "object_id": 501,
                "identity": cmd.get("identity_no"),
                "name": cmd.get("name"),
                "product_folder": cmd.get("product_folder"),
                "product_folder_id": 401,
            }
        if name == "link_product_to_folder":
            return {
                "ok": True,
                "product_id": cmd.get("product_id"),
                "product_folder": cmd.get("product_folder"),
                "product_folder_id": 401,
                "already_linked": False,
            }
        return {"ok": True}


class RuleUploader(CaxaUploader):
    def __init__(self, bridge, parts=None, log=lambda *a: None,
                 staging_root=None, **kwargs):
        if staging_root is None:
            staging_root = os.path.join(
                _support.BASE_TMP, "staging_" + uuid.uuid4().hex[:8]
            )
        kwargs["staging_dir"] = staging.TaskStaging(root=staging_root, log=log)
        super().__init__(bridge, log=log, **kwargs)
        self.parts = parts or {}
        self.created = []
        self.created_products = []
        self.links = []

    def find_part(self, code):
        value = self.parts.get(str(code).upper())
        if value is None:
            return None
        return {"object_id": value, "identity": str(code)}

    def find_product(self, identity):
        return {"object_id": 1, "identity": str(identity), "name": "product"}

    def create_part(self, identity, name=""):
        self.created.append(str(identity).upper())
        return {"object_id": 70, "identity": identity}

    def create_product(self, identity, name, folder_name=""):
        self.created_products.append((str(identity).upper(), name))
        return {
            "object_id": 1,
            "identity": str(identity),
            "name": name,
            "created_new_product": True,
        }

    def link_part(self, parent, child, reference, product_link=False):
        self.links.append((parent, child, reference, product_link))
        return self.bridge.call(
            {
                "cmd": "link_part",
                "parent_id": parent,
                "child_id": child,
                "reference": reference,
                "product_link": product_link,
            }
        )


def component(code="01.01", name="零件", machine="", **extra):
    item = {"code": code, "name": name, "kind": "component", "machine": machine}
    item.update(extra)
    return item


class OwnBorrowTests(unittest.TestCase):
    def setUp(self):
        self.td = _support.temp_dir()
        self.dir = self.td.__enter__()

    def tearDown(self):
        self.td.__exit__(None, None, None)

    def make_file(self, name="01.01 零件.pdf", content=b"PDF"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def test_borrow_missing_child_never_creates_a_part(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={"01.02": 12})
        item = component(file_path=self.make_file("01.01 零件.pdf"))
        with self.assertRaises(CaxaError) as caught:
            uploader.upload_item(
                item, {"01.01"}, None, explicit_parent="01.02",
                relation_type="borrow",
            )
        self.assertIn("借用", str(caught.exception))
        self.assertEqual(uploader.created, [])
        self.assertEqual(bridge.link_calls, [])

    def test_new_own_child_keeps_selected_parent(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={"01.02": 12})
        item = component(
            code="01.02-01",
            file_path=self.make_file("01.02-01 零件.pdf"),
        )
        uploader.upload_item(
            item, {"01.02-01"}, None, explicit_parent="01.02", relation_type="own"
        )
        self.assertEqual(uploader.created, ["01.02-01"])
        self.assertEqual(uploader.links, [(12, 70, False, False)])
        material_calls = [
            call for call in bridge.calls
            if call.get("cmd") == "ensure_part_material_code"
        ]
        self.assertEqual(
            [
                (call.get("part_id"), call.get("identity"))
                for call in material_calls
            ],
            [(70, "01.02-01")],
        )
        self.assertEqual(bridge.link_calls[0]["reference"], False)

    def test_same_01_series_cross_code_owner_is_allowed(self):
        bridge = RuleBridge()
        uploader = RuleUploader(
            bridge,
            parts={"01.22.01.V202.Z3": 12},
        )
        item = component(
            code="01.22.01.V202.1001",
            file_path=self.make_file("01.22.01.V202.1001 电机底座.pdf"),
        )
        uploader.upload_item(
            item,
            {"01.22.01.V202.1001"},
            None,
            explicit_parent="01.22.01.V202.Z3",
            relation_type="own",
        )
        self.assertEqual(
            uploader.created,
            ["01.22.01.V202.1001"],
        )
        self.assertEqual(uploader.links, [(12, 70, False, False)])

    def test_unmatched_code_part_is_created_under_general_product(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={})
        item = component(
            code="04.06",
            file_path=self.make_file("04.06 分离器组件.pdf"),
        )
        uploader.upload_item(item, {"04.06"}, None, relation_type="own")
        self.assertEqual(uploader.created, ["04.06"])
        self.assertEqual(uploader.links, [(1, 70, False, True)])

    def test_04_part_uses_nearest_existing_code_prefix_as_owner(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={"04.06": 8496})
        item = component(
            code="04.06.OV855-01",
            file_path=self.make_file("04.06.OV855-01 组件.pdf"),
        )
        uploader.upload_item(
            item, {"04.06.OV855-01"}, None, relation_type=""
        )
        self.assertEqual(uploader.created, ["04.06.OV855-01"])
        self.assertEqual(uploader.links, [(8496, 70, False, False)])

    def test_explicit_missing_parent_is_not_replaced(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={"01.01": 11})
        item = component(code="01.01-02", file_path=self.make_file("01.01-02 零件.pdf"))
        with self.assertRaises(CaxaError) as caught:
            uploader.upload_item(item, {"01.01-02"}, None, explicit_parent="01.09")
        self.assertIn("01.09", str(caught.exception))
        self.assertEqual(uploader.links, [])

    def test_without_explicit_parent_prefix_candidate_is_used(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={"01.01": 11})
        item = component(code="01.01-02", file_path=self.make_file("01.01-02 零件.pdf"))
        uploader.upload_item(item, {"01.01-02"}, None)
        self.assertEqual(uploader.links[0][:3], (11, 70, False))

    def test_link_relation_missing_child_is_reported_not_created(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={"01.02": 12})
        row = uploader.link_relation("01.02", "01.99", "borrow")
        self.assertEqual(row["status"], "缺子")
        self.assertEqual(bridge.link_calls, [])
        self.assertEqual(uploader.created, [])

    def test_link_relation_missing_parent_is_reported(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={})
        row = uploader.link_relation("01.02", "01.03", "borrow")
        self.assertEqual(row["status"], "缺父")

    def test_link_relation_verifies_reference_flag(self):
        bridge = RuleBridge(children=[])
        uploader = RuleUploader(bridge, parts={"01.02": 12, "01.03": 13})
        row = uploader.link_relation("01.02", "01.03", "borrow")
        self.assertEqual(row["status"], "已借用")
        self.assertEqual(bridge.link_calls[0]["reference"], True)

    def test_link_relation_reports_unconfirmed_link(self):
        # Write succeeds but the read-back cannot see the link.
        bridge = RuleBridge(children=[])
        bridge.record_links = False
        uploader = RuleUploader(bridge, parts={"01.02": 12, "01.03": 13})
        row = uploader.link_relation("01.02", "01.03", "own")
        self.assertEqual(row["status"], "待核实")
        self.assertIn("回读", row["note"])

    def test_link_relation_reports_reference_mismatch(self):
        bridge = RuleBridge(children=[])
        bridge.reference_override = True
        uploader = RuleUploader(bridge, parts={"01.02": 12, "01.03": 13})
        row = uploader.link_relation("01.02", "01.03", "own")
        self.assertEqual(row["status"], "待核实")
        self.assertIn("回读为借用，计划为归属", row["note"])

    def test_link_relation_existing_wrong_type_is_conflict(self):
        bridge = RuleBridge(children=[{"sub_id": 13, "reference": False}])
        uploader = RuleUploader(bridge, parts={"01.02": 12, "01.03": 13})
        row = uploader.link_relation("01.02", "01.03", "borrow")
        self.assertEqual(row["status"], "冲突")
        self.assertEqual(bridge.link_calls, [])

    def test_link_relation_unknown_existing_type_is_pending(self):
        bridge = RuleBridge(children=[{"sub_id": 13, "reference": None}])
        uploader = RuleUploader(bridge, parts={"01.02": 12, "01.03": 13})
        row = uploader.link_relation("01.02", "01.03", "borrow")
        self.assertEqual(row["status"], "待核实")
        self.assertEqual(bridge.link_calls, [])

    def test_part_info_error_blocks_relation_write(self):
        class ErrorBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                if cmd.get("cmd") == "part_info":
                    self.calls.append(cmd)
                    return {"ok": True, "children": [], "children_error": "SDK error"}
                return super().call(cmd, timeout, idempotent)

        bridge = ErrorBridge()
        uploader = RuleUploader(bridge, parts={"01.02": 12, "01.03": 13})
        with self.assertRaises(CaxaError):
            uploader.link_relation("01.02", "01.03", "borrow")
        self.assertEqual(bridge.link_calls, [])

    def test_newly_created_product_is_reused_without_requery(self):
        bridge = RuleBridge()
        uploader = CaxaUploader(
            bridge,
            staging_dir=staging.TaskStaging(
                root=os.path.join(_support.BASE_TMP, "product_" + uuid.uuid4().hex[:8])
            ),
        )
        uploader.machine_part = lambda product_id, identity: {"object_id": 700}
        first = uploader.ensure_machine(
            "03.HW.015350-46P01",
            part_name="空气压缩机",
            product_name="HW1.5-35(直连/水冷)",
        )
        second = uploader.ensure_machine(
            "03.HW.015350-46P01",
            part_name="",
            create_part=False,
        )
        self.assertEqual(first[0]["object_id"], second[0]["object_id"])
        self.assertEqual(
            sum(1 for call in bridge.calls if call.get("cmd") == "query_objects"),
            1,
        )
        self.assertEqual(
            sum(1 for call in bridge.calls if call.get("cmd") == "create_product"),
            1,
        )
        create_call = next(
            call for call in bridge.calls if call.get("cmd") == "create_product"
        )
        self.assertEqual(create_call["product_folder"], "HW系列")

    def test_product_is_found_by_exact_identity_before_tree_scan(self):
        class HiddenProductBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                if cmd.get("cmd") == "find_product":
                    self.calls.append(cmd)
                    return {"ok": True, "found": []}
                if cmd.get("cmd") == "query_objects":
                    self.calls.append(cmd)
                    return {
                        "ok": True,
                        "items": [
                            {
                                "object_id": 2033,
                                "IdentityNo": cmd.get("where_value"),
                                "name": "HW1.5-35(直连/水冷)",
                            }
                        ],
                    }
                if cmd.get("cmd") == "dump_product_by_id":
                    self.calls.append(cmd)
                    return {
                        "ok": True,
                        "product": {
                            "object_id": 2033,
                            "identity": "03.HW.015350-46P01",
                            "name": "HW1.5-35(直连/水冷)",
                        },
                    }
                return super().call(cmd, timeout, idempotent)

        bridge = HiddenProductBridge()
        uploader = CaxaUploader(
            bridge,
            staging_dir=staging.TaskStaging(
                root=os.path.join(_support.BASE_TMP, "hidden_" + uuid.uuid4().hex[:8])
            ),
        )
        product = uploader.find_product("03.HW.015350-46P01")
        self.assertEqual(product["object_id"], 2033)
        self.assertTrue(product["found_by_identity"])
        self.assertFalse(
            any(call.get("cmd") == "find_product" for call in bridge.calls)
        )

    def test_exact_query_miss_uses_folder_scoped_product_search(self):
        class NestedProductBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                if cmd.get("cmd") == "query_objects":
                    self.calls.append(cmd)
                    return {"ok": True, "items": []}
                if cmd.get("cmd") == "find_product_in_folder":
                    self.calls.append(cmd)
                    return {
                        "ok": True,
                        "found": [
                            {
                                "object_id": 90350,
                                "identity": "03.HT.HGB01-06350G",
                                "name": "氩气压缩机",
                            }
                        ],
                    }
                return super().call(cmd, timeout, idempotent)

        bridge = NestedProductBridge()
        uploader = CaxaUploader(
            bridge,
            staging_dir=staging.TaskStaging(
                root=os.path.join(_support.BASE_TMP, "nested_" + uuid.uuid4().hex[:8])
            ),
        )
        product = uploader.find_product_for_machine(
            "03.HT.HGB01-06350G",
            product_folder="HT合同特殊/HGB系列",
        )
        self.assertEqual(product["object_id"], 90350)
        self.assertTrue(product["found_in_folder"])
        self.assertIn(
            {
                "cmd": "find_product_in_folder",
                "identity": "03.HT.HGB01-06350G",
                "folder_path": "HT合同特殊/HGB系列",
            },
            bridge.calls,
        )
        self.assertFalse(any(call.get("cmd") == "find_product" for call in bridge.calls))

    def test_nested_product_incomplete_read_cannot_trigger_creation(self):
        class NestedProductBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                if cmd["cmd"] == "find_product_in_folder":
                    self.calls.append(cmd)
                    return {"ok": True}  # invalid reply, not an empty result
                return super().call(cmd, timeout, idempotent)

        bridge = NestedProductBridge()
        uploader = CaxaUploader(bridge)
        with self.assertRaises(CaxaError) as error:
            uploader.ensure_machine(
                "03.HT.HGB01-06350G", product_name="有效产品名",
                product_folder="HT合同特殊/HGB系列",
            )
        self.assertEqual(error.exception.stage, "product_folder_read")
        self.assertFalse(any(call["cmd"] == "create_product" for call in bridge.calls))

    def test_missing_product_folder_blocks_product_creation(self):
        class MissingFolderBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                if cmd.get("cmd") == "find_product_folder":
                    self.calls.append(cmd)
                    return {"ok": True, "found": []}
                return super().call(cmd, timeout, idempotent)

        bridge = MissingFolderBridge()
        uploader = CaxaUploader(
            bridge,
            staging_dir=staging.TaskStaging(
                root=os.path.join(_support.BASE_TMP, "folder_" + uuid.uuid4().hex[:8])
            ),
        )
        uploader.machine_part = lambda product_id, identity: {"object_id": 700}
        with self.assertRaises(CaxaError) as caught:
            uploader.ensure_machine(
                "03.HT.HGB.001",
                part_name="HGB压缩机",
                product_name="HT 1.5-35",
            )
        self.assertIn("不允许创建系列目录", str(caught.exception))
        self.assertFalse(
            any(call.get("cmd") == "create_product" for call in bridge.calls)
        )
        self.assertFalse(
            any(call.get("cmd") == "ensure_product_folder" for call in bridge.calls)
        )

    def test_existing_product_is_relinked_to_expected_folder_once(self):
        class ExistingProductBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                if cmd.get("cmd") == "query_objects":
                    self.calls.append(cmd)
                    return {
                        "ok": True,
                        "items": [
                            {
                                "object_id": 501,
                                "IdentityNo": cmd.get("where_value"),
                                "name": "HW1.5-35",
                            }
                        ],
                    }
                return super().call(cmd, timeout, idempotent)

        bridge = ExistingProductBridge()
        uploader = CaxaUploader(
            bridge,
            staging_dir=staging.TaskStaging(
                root=os.path.join(_support.BASE_TMP, "existing_" + uuid.uuid4().hex[:8])
            ),
        )
        uploader.machine_part = lambda product_id, identity: {"object_id": 700}
        product, part = uploader.ensure_machine(
            "03.HW.015350-46P01",
            part_name="空气压缩机",
        )
        self.assertEqual(product["object_id"], 501)
        self.assertEqual(part["object_id"], 700)
        uploader.ensure_machine(
            "03.HW.015350-46P01",
            part_name="空气压缩机",
        )
        link_calls = [
            call for call in bridge.calls
            if call.get("cmd") == "link_product_to_folder"
        ]
        self.assertEqual(len(link_calls), 1)
        self.assertEqual(link_calls[0]["product_folder"], "HW系列")


class MachineAndClassTests(unittest.TestCase):
    def test_append_iteration_retries_after_delay_when_entity_is_stale(self):
        class StaleSessionBridge(RuleBridge):
            def __init__(self):
                super().__init__(
                    upload_result={
                        "ok": True,
                        "attached": True,
                        "object_id": 17,
                        "class_id": 1002,
                        "class_name": "Drawing",
                        "method": "attach",
                        "diag": {"append_iteration_requested": True},
                    }
                )
                self.read_count = 0

            def call(self, cmd, timeout=240.0, idempotent=None):
                if cmd.get("cmd") == "part_drawings":
                    self.calls.append(cmd)
                    self.read_count += 1
                    fresh = self.read_count >= 3
                    iterator = "b.2" if fresh else "b.1"
                    count = 3 if fresh else 2
                    return {
                        "ok": True,
                        "drawings": [
                            {
                                "object_id": 17,
                                "name": "drawing.pdf",
                                "draw_no": "01.01",
                                "iterator_no": iterator,
                                "history_count": count,
                            }
                        ],
                    }
                return super().call(cmd, timeout, idempotent)

        uploader = RuleUploader(StaleSessionBridge(), parts={})
        item = self.make_file("drawing.pdf")
        with patch("caxa.uploader.time.sleep") as sleep:
            result = uploader.attach_file(
                20,
                item,
                DRAWING_2D,
                "drawing.pdf",
                draw_no="01.01",
                kind="component",
            )
        self.assertTrue(result["new_version"])
        self.assertEqual(result["iterator_no"], "b.2")
        sleep.assert_called_once_with(3.0)
        self.assertEqual(result["diag"]["readback_retry_delay_seconds"], 3.0)

    def test_collect_machine_targets_accepts_02_and_03_process_documents(self):
        items = [
            {
                "code": "J03.HW.025400-11T01",
                "kind": "process_doc",
                "machine": "03.HW.025400-11T01",
                "series_product_name": "",
            },
            {
                "code": "M03.HW.025400-11T01",
                "kind": "process_doc",
                "machine": "03.HW.025400-11T01",
                "series_product_name": "HW2.5-40",
            },
            {
                "code": "Z03.HW.025400-11T01",
                "kind": "process_doc",
                "machine": "03.HW.025400-11T01",
            },
            {
                "code": "Z02.A36.02.ARF25C",
                "kind": "process_doc",
                "machine": "02.A36.02.ARF25C",
            },
        ]
        targets = upload_core.collect_machine_targets(items)
        self.assertEqual(
            [target["machine"] for target in targets],
            ["03.HW.025400-11T01", "02.A36.02.ARF25C"],
        )
        machine = targets[0]
        self.assertEqual(machine["family"], "03")
        self.assertFalse(machine["has_machine_drawing"])
        self.assertEqual(machine["series_product_name"], "HW2.5-40")
        self.assertEqual(machine["series_product_folder"], "HW系列")
        self.assertEqual(
            machine["item_codes"],
            [
                "J03.HW.025400-11T01",
                "M03.HW.025400-11T01",
                "Z03.HW.025400-11T01",
            ],
        )
        self.assertTrue(all(
            item.get("series_product_name") == "HW2.5-40"
            for item in items[:3]
        ))
        half = targets[1]
        self.assertEqual(half["family"], "02")
        self.assertEqual(half["series_product_name"], "")

    def test_precheck_finds_03_product_and_part_from_process_document_only(self):
        class ProcessPrecheckUploader:
            def __init__(self, bridge, log=print, **kwargs):
                self.bridge = bridge

            def find_product(self, identity):
                return None

            def find_product_folder(self, name):
                return {"object_id": 401, "identity": name}

            def machine_part(self, product_id, identity):
                return None

            def find_part(self, identity):
                return None

            def close(self):
                return None

        manifest = {
            "items": [
                {
                    "code": "J03.HW.025400-11T01",
                    "kind": "process_doc",
                    "machine": "03.HW.025400-11T01",
                    "upload_sel": True,
                },
                {
                    "code": "M03.HW.025400-11T01",
                    "kind": "process_doc",
                    "machine": "03.HW.025400-11T01",
                    "upload_sel": True,
                },
                {
                    "code": "Z03.HW.025400-11T01",
                    "kind": "process_doc",
                    "machine": "03.HW.025400-11T01",
                    "upload_sel": True,
                    "series_product_name": "HW2.5-40",
                },
            ]
        }
        with _support.patch_core(fake_uploader_cls=ProcessPrecheckUploader):
            info = upload_core.precheck_with_caxa(
                manifest,
                _support.ACCOUNT,
                log=lambda *args: None,
                journal_path=_support.journal_path("precheck_com.sqlite3"),
            )
        self.assertEqual(len(info["machine_products"]), 1)
        row = info["machine_products"][0]
        self.assertEqual(row["code"], "03.HW.025400-11T01")
        self.assertFalse(row["product_exists"])
        self.assertFalse(row["part_exists"])
        self.assertTrue(row["requires_product_name"])
        self.assertFalse(row["has_machine_drawing"])
        self.assertEqual(row["requested_name"], "HW2.5-40")
        self.assertEqual(info["missing_product_parts"], [])
        self.assertEqual(
            row["item_codes"],
            [
                "J03.HW.025400-11T01",
                "M03.HW.025400-11T01",
                "Z03.HW.025400-11T01",
            ],
        )

    def test_precheck_02_leaf_is_creatable_only_when_group_exists(self):
        class HalfPrecheckUploader:
            def __init__(self, bridge, log=print, **kwargs):
                self.bridge = bridge
                self.parts = {"02.A36": 12}

            def find_product(self, identity):
                return {"object_id": 1, "identity": identity, "name": "半成品"}

            def find_part(self, identity):
                object_id = self.parts.get(str(identity).upper())
                if object_id is None:
                    return None
                return {"object_id": object_id, "identity": identity}

            def find_part_candidates(self, identity):
                part = self.find_part(identity)
                return [part] if part is not None else []

            def close(self):
                return None

        class HalfMissingGroupUploader(HalfPrecheckUploader):
            def __init__(self, bridge, log=print, **kwargs):
                super().__init__(bridge, log=log, **kwargs)
                self.parts = {}

        class HalfDuplicateLinksUploader(HalfPrecheckUploader):
            def __init__(self, bridge, log=print, **kwargs):
                super().__init__(bridge, log=log, **kwargs)
                self.parts = {
                    "02.A36.02": 11,
                    "02.A36.02.AR02": 13,
                }

            def part_info(self, part_id):
                return {
                    "part_id": part_id,
                    "parents": [
                        {"parent_id": 10, "reference": False},
                        {"parent_id": 11, "reference": False},
                    ],
                    "products": [],
                }

        class HalfBorrowProductLinksUploader(HalfPrecheckUploader):
            def __init__(self, bridge, log=print, **kwargs):
                super().__init__(bridge, log=log, **kwargs)
                self.parts = {
                    "02.A36.02": 11,
                    "02.A36.02.AR02": 13,
                }

            def part_info(self, part_id):
                return {
                    "part_id": part_id,
                    "parents": [
                        {"parent_id": 11, "reference": False},
                    ],
                    "products": [
                        {"product_id": 1, "reference": True},
                        {"product_id": 1, "reference": True},
                    ],
                }

        manifest = {
            "items": [
                {
                    "code": "Z02.A36.02.AR02",
                    "kind": "process_doc",
                    "machine": "02.A36.02.AR02",
                    "upload_sel": True,
                }
            ]
        }
        with _support.patch_core(fake_uploader_cls=HalfPrecheckUploader):
            info = upload_core.precheck_with_caxa(
                manifest,
                _support.ACCOUNT,
                log=lambda *args: None,
                journal_path=_support.journal_path("half_precheck.sqlite3"),
            )
        self.assertEqual(info["missing_process_parts"], [])
        self.assertEqual(
            [row["code"] for row in info["process_parts_will_create"]],
            ["02.A36.02.AR02"],
        )

        with _support.patch_core(fake_uploader_cls=HalfMissingGroupUploader):
            blocked = upload_core.precheck_with_caxa(
                manifest,
                _support.ACCOUNT,
                log=lambda *args: None,
                journal_path=_support.journal_path("half_precheck_missing.sqlite3"),
            )
        self.assertEqual(blocked["process_parts_will_create"], [])
        self.assertEqual(len(blocked["missing_process_parts"]), 1)
        self.assertIn("02.A36", blocked["missing_process_parts"][0]["blocked_reason"])

        with _support.patch_core(fake_uploader_cls=HalfDuplicateLinksUploader):
            duplicates = upload_core.precheck_with_caxa(
                manifest,
                _support.ACCOUNT,
                log=lambda *args: None,
                journal_path=_support.journal_path(
                    "half_precheck_duplicate_links.sqlite3"
                ),
            )
        self.assertEqual(len(duplicates["missing_process_parts"]), 1)
        self.assertIn(
            "多个非借用归属",
            duplicates["missing_process_parts"][0]["blocked_reason"],
        )

        with _support.patch_core(
            fake_uploader_cls=HalfBorrowProductLinksUploader
        ):
            borrowed = upload_core.precheck_with_caxa(
                manifest,
                _support.ACCOUNT,
                log=lambda *args: None,
                journal_path=_support.journal_path(
                    "half_precheck_borrow_product_links.sqlite3"
                ),
            )
        self.assertEqual(borrowed["missing_process_parts"], [])

    def test_workflow_includes_drawings_and_capp_documents(self):
        documents = upload_core._confirmed_workflow_documents(
            {
                "03.HW.025400-11T01": {
                    "remote_id": "101",
                    "class_id": "1002",
                    "class_name": "Drawing",
                },
                "J03.HW.025400-11T01": {
                    "remote_id": "201",
                    "class_id": "1003",
                    "class_name": "CappDocument",
                },
                "Z03.HW.025400-11T01": {
                    "remote_id": "202",
                    "class_name": "CappDocument",
                },
            },
            {
                "03.HW.025400-11T01",
                "J03.HW.025400-11T01",
                "Z03.HW.025400-11T01",
            },
        )
        self.assertEqual(
            documents,
            [
                {"object_id": 101, "class_id": 1002, "class_name": "Drawing"},
                {"object_id": 201, "class_id": 1003, "class_name": "CappDocument"},
                {"object_id": 202, "class_id": 1003, "class_name": "CappDocument"},
            ],
        )

    def setUp(self):
        self.td = _support.temp_dir()
        self.dir = self.td.__enter__()

    def tearDown(self):
        self.td.__exit__(None, None, None)

    def make_file(self, name, content=b"PDF"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def test_machine_part_link_failure_is_partial_not_success(self):
        bridge = RuleBridge(fail_product_link=True)
        uploader = RuleUploader(bridge, parts={})
        uploader.machine_part = lambda *a, **k: None
        uploader.create_part = lambda identity, name="": {"object_id": 77}
        item = {
            "code": "03.MACH",
            "name": "整机",
            "kind": "machine_drawing",
            "machine": "03.MACH",
            "file_path": self.make_file("03.MACH 整机.pdf"),
        }
        with self.assertRaises(CaxaError) as caught:
            uploader.upload_item(item, {"03.MACH"}, None)
        self.assertTrue(getattr(caught.exception, "partial", False))
        self.assertIn("未能挂接", str(caught.exception))

    def test_missing_series_product_is_created_with_machine_code(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={})
        uploader.find_product = lambda identity: None
        uploader.machine_part = lambda *a, **k: None
        item = {
            "code": "03.HE.002035A-21B01",
            "name": "氦气压缩机",
            "kind": "machine_drawing",
            "machine": "03.HE.002035A-21B01",
            "series_product_name": "HEM0.2-35(标准型)",
            "file_path": self.make_file("03.HE.002035A-21B01 氦气压缩机.pdf"),
        }
        uploader.upload_item(
            item,
            {item["code"]},
            None,
            series_product_name=item["series_product_name"],
        )
        self.assertEqual(
            uploader.created_products,
            [("03.HE.002035A-21B01", "HEM0.2-35(标准型)")],
        )
        self.assertEqual(uploader.created, ["03.HE.002035A-21B01"])
        self.assertEqual(
            uploader.links,
            [(1, 70, False, True)],
        )

    def test_series_product_name_reuses_product_in_expected_folder(self):
        class SeriesProductBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                name = cmd.get("cmd")
                if name == "find_product":
                    self.calls.append(cmd)
                    return {
                        "ok": True,
                        "found": [
                            {
                                "object_id": 1467,
                                "identity": "OT312100",
                                "name": "OFT312",
                            },
                            {
                                "object_id": 1693,
                                "identity": "",
                                "name": "OFT312",
                            },
                        ],
                    }
                if name == "find_product_folder":
                    self.calls.append(cmd)
                    return {
                        "ok": True,
                        "found": [
                            {
                                "object_id": 96,
                                "identity": "",
                                "name": "OF系列",
                                "path": "OF系列",
                            }
                        ],
                    }
                if name == "dump_product_by_id":
                    self.calls.append(cmd)
                    product_id = int(cmd["product_id"])
                    folder_id = 96 if product_id == 1693 else 137
                    return {
                        "ok": True,
                        "product": {
                            "object_id": product_id,
                            "children": [
                                {
                                    "kind": "product_folder",
                                    "object_id": folder_id,
                                }
                            ],
                        },
                    }
                return super().call(cmd, timeout, idempotent)

        uploader = CaxaUploader(
            SeriesProductBridge(),
            staging_dir=staging.TaskStaging(
                root=os.path.join(
                    _support.BASE_TMP,
                    "series_name_" + uuid.uuid4().hex[:8],
                )
            ),
        )
        uploader.find_product = lambda identity: None
        product = uploader.find_product_for_machine(
            "03.OF.045-06016S",
            product_name="OFT312",
            product_folder="OF系列",
        )
        self.assertEqual(product["object_id"], 1693)
        self.assertEqual(product["name"], "OFT312")

    def test_ambiguous_series_name_in_same_folder_is_blocked(self):
        class DuplicateSeriesBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                name = cmd.get("cmd")
                if name == "find_product":
                    return {
                        "ok": True,
                        "found": [
                            {"object_id": 1, "identity": "", "name": "OFT312"},
                            {"object_id": 2, "identity": "", "name": "OFT312"},
                        ],
                    }
                if name == "find_product_folder":
                    return {
                        "ok": True,
                        "found": [{"object_id": 96, "name": "OF系列"}],
                    }
                if name == "dump_product_by_id":
                    return {
                        "ok": True,
                        "product": {
                            "children": [
                                {"kind": "product_folder", "object_id": 96}
                            ]
                        },
                    }
                return super().call(cmd, timeout, idempotent)

        uploader = CaxaUploader(
            DuplicateSeriesBridge(),
            staging_dir=staging.TaskStaging(
                root=os.path.join(
                    _support.BASE_TMP,
                    "series_duplicate_" + uuid.uuid4().hex[:8],
                )
            ),
        )
        uploader.find_product = lambda identity: None
        with self.assertRaises(CaxaError) as caught:
            uploader.find_product_for_machine(
                "03.OF.045-06016S",
                product_name="OFT312",
                product_folder="OF系列",
            )
        self.assertEqual(
            caught.exception.stage,
            "product_series_duplicate",
        )

    def test_process_document_keeps_machine_material_code_without_z(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={})
        uploader.machine_part = lambda *args, **kwargs: {"object_id": 77}
        uploader.attach_file = lambda *args, **kwargs: {
            "attached": True,
            "object_id": 88,
            "class_id": 1003,
            "class_name": "CappDocument",
        }
        item = {
            "code": "Z03.HG.020100A-11P02",
            "name": "",
            "kind": "process_doc",
            "machine": "03.HG.020100A-11P02",
            "file_path": self.make_file("Z03.HG.020100A-11P02.pdf"),
        }
        uploader.upload_item(item, {item["code"]}, None)
        material_calls = [
            call for call in bridge.calls
            if call.get("cmd") == "ensure_part_material_code"
        ]
        self.assertEqual(
            [
                (call.get("part_id"), call.get("identity"))
                for call in material_calls
            ],
            [(77, "03.HG.020100A-11P02")],
        )

    def test_03_process_document_creates_missing_product_and_machine_part(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={})
        uploader.find_product = lambda identity: None
        uploader.machine_part = lambda *args, **kwargs: None
        attached = {}

        def attach(part_id, *args, **kwargs):
            attached["part_id"] = part_id
            attached["args"] = args
            attached["kwargs"] = kwargs
            return {
                "attached": True,
                "object_id": 88,
                "class_id": 1003,
                "class_name": "CappDocument",
            }

        uploader.attach_file = attach
        item = {
            "code": "J03.HW.025400-11T01",
            "name": "",
            "kind": "process_doc",
            "machine": "03.HW.025400-11T01",
            "file_path": self.make_file("J03.HW.025400-11T01.pdf"),
        }
        uploader.upload_item(
            item,
            {item["code"]},
            None,
            series_product_name="HW2.5-40",
            series_product_folder="HW系列",
        )
        self.assertEqual(
            uploader.created_products,
            [("03.HW.025400-11T01", "HW2.5-40")],
        )
        self.assertEqual(uploader.created, ["03.HW.025400-11T01"])
        self.assertEqual(uploader.links, [(1, 70, False, True)])
        self.assertEqual(attached["part_id"], 70)

    def test_03_process_document_reuses_existing_product_and_creates_only_part(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={})
        uploader.find_product = lambda identity: {
            "object_id": 501,
            "identity": identity,
            "name": "existing",
        }
        uploader.machine_part = lambda *args, **kwargs: None
        uploader.attach_file = lambda *args, **kwargs: {
            "attached": True,
            "object_id": 89,
            "class_id": 1003,
            "class_name": "CappDocument",
        }
        item = {
            "code": "Z03.HW.025400-11T01",
            "kind": "process_doc",
            "machine": "03.HW.025400-11T01",
            "file_path": self.make_file("Z03.HW.025400-11T01.pdf"),
        }
        uploader.upload_item(item, {item["code"]}, None)
        self.assertEqual(uploader.created_products, [])
        self.assertEqual(uploader.created, ["03.HW.025400-11T01"])
        self.assertEqual(uploader.links, [(501, 70, False, True)])

    def test_03_process_document_without_product_name_blocks_creation(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={})
        uploader.find_product = lambda identity: None
        uploader.machine_part = lambda *args, **kwargs: None
        item = {
            "code": "M03.HW.025400-11T01",
            "kind": "process_doc",
            "machine": "03.HW.025400-11T01",
            "file_path": self.make_file("M03.HW.025400-11T01.pdf"),
        }
        with self.assertRaises(upload_core.MachineMissing):
            uploader.upload_item(item, {item["code"]}, None)
        self.assertEqual(uploader.created_products, [])
        self.assertEqual(uploader.created, [])

    def test_02_process_document_attaches_to_existing_half_product_part(self):
        bridge = RuleBridge()
        uploader = RuleUploader(
            bridge,
            parts={
                "02.A36.02": 11,
                "02.A36.02.ARF25C": 12,
            },
        )
        uploader.machine_part = lambda *args, **kwargs: {
            "object_id": 12,
            "identity": "02.A36.02.ARF25C",
        }
        uploader.part_info = lambda part_id: {
            "part_id": part_id,
            "products": [],
            "parents": [
                {"parent_id": 11, "reference": False},
            ],
        }
        attached = {}

        def attach(part_id, *args, **kwargs):
            attached["part_id"] = part_id
            attached["args"] = args
            attached["kwargs"] = kwargs
            return {
                "attached": True,
                "object_id": 88,
                "class_id": 1003,
                "class_name": "CappDocument",
            }

        uploader.attach_file = attach
        item = {
            "code": "Z02.A36.02.ARF25C",
            "name": "",
            "kind": "process_doc",
            "machine": "02.A36.02.ARF25C",
            "file_path": self.make_file("Z02.A36.02.ARF25C.pdf"),
        }
        uploader.upload_item(item, {item["code"]}, None)
        self.assertEqual(attached["part_id"], 12)
        self.assertEqual(
            uploader.links,
            [],
        )
        material_calls = [
            call for call in bridge.calls
            if call.get("cmd") == "ensure_part_material_code"
        ]
        self.assertEqual(
            [
                (call.get("part_id"), call.get("identity"))
                for call in material_calls
            ],
            [(12, "02.A36.02.ARF25C")],
        )

    def test_02_process_document_creates_missing_leaf_under_existing_group(self):
        bridge = RuleBridge()
        uploader = RuleUploader(
            bridge,
            parts={"02.A36": 12},
        )
        uploader.part_info = lambda part_id: {
            "part_id": part_id,
            "products": [],
        }
        attached = {}

        def attach(part_id, *args, **kwargs):
            attached["part_id"] = part_id
            return {
                "attached": True,
                "object_id": 88,
                "class_id": 1003,
                "class_name": "CappDocument",
            }

        uploader.attach_file = attach
        item = {
            "code": "Z02.A36.02.AR02",
            "kind": "process_doc",
            "machine": "02.A36.02.AR02",
            "file_path": self.make_file("Z02.A36.02.AR02.pdf"),
        }
        result = uploader.upload_item(item, {item["code"]}, None)
        self.assertEqual(uploader.created, ["02.A36.02.AR02"])
        self.assertEqual(uploader.links, [(12, 70, False, False)])
        self.assertEqual(attached["part_id"], 70)
        self.assertTrue(result["created_new_part"])

    def test_02_process_document_never_creates_missing_group(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={})
        item = {
            "code": "Z02.A36.02.AR02",
            "kind": "process_doc",
            "machine": "02.A36.02.AR02",
            "file_path": self.make_file("Z02.A36.02.AR02.pdf"),
        }
        with self.assertRaises(CaxaError) as caught:
            uploader.upload_item(item, {item["code"]}, None)
        self.assertIn("02.A36", str(caught.exception))
        self.assertEqual(uploader.created, [])
        self.assertEqual(uploader.links, [])

    def test_02_component_drawing_creates_only_missing_leaf(self):
        bridge = RuleBridge()
        uploader = RuleUploader(
            bridge,
            parts={"02.A36": 12},
        )
        uploader.part_info = lambda part_id: {
            "part_id": part_id,
            "products": [],
        }
        uploader.attach_file = lambda *args, **kwargs: {
            "attached": True,
            "object_id": 90,
            "class_id": 1002,
            "class_name": "Drawing",
        }
        item = {
            "code": "02.A36.02.AR02",
            "name": "氩气过滤干燥撬",
            "kind": "component",
            "machine": "",
            "file_path": self.make_file("02.A36.02.AR02 氩气过滤干燥撬.pdf"),
        }
        result = uploader.upload_item(item, {item["code"]}, None)
        self.assertEqual(uploader.created, ["02.A36.02.AR02"])
        self.assertEqual(uploader.links, [(12, 70, False, False)])
        self.assertTrue(result["created_new_part"])

    def test_02_multiple_non_borrow_links_block_before_upload(self):
        bridge = RuleBridge()
        uploader = RuleUploader(
            bridge,
            parts={"02.A36.02": 11, "02.A36.02.AR02": 13},
        )
        uploader.part_info = lambda part_id: {
            "part_id": part_id,
            "parents": [
                {"parent_id": 10, "reference": False},
                {"parent_id": 11, "reference": False},
            ],
            "products": [],
        }
        uploader.attach_file = lambda *args, **kwargs: self.fail(
            "multiple non-borrow links must block before attach"
        )
        item = {
            "code": "Z02.A36.02.AR02",
            "kind": "process_doc",
            "machine": "02.A36.02.AR02",
            "file_path": self.make_file("Z02.A36.02.AR02.pdf"),
        }
        with self.assertRaises(CaxaError) as caught:
            uploader.upload_item(item, {item["code"]}, None)
        self.assertIn("多个非借用归属", str(caught.exception))
        self.assertEqual(uploader.links, [])

    def test_02_multiple_borrow_product_links_are_legal(self):
        bridge = RuleBridge()
        uploader = RuleUploader(
            bridge,
            parts={"02.A36.02": 11, "02.A36.02.AR02": 13},
        )
        uploader.part_info = lambda part_id: {
            "part_id": part_id,
            "parents": [
                {"parent_id": 11, "reference": False},
            ],
            "products": [
                {"product_id": 1, "reference": True},
                {"product_id": 2, "reference": True},
            ],
        }
        uploader.attach_file = lambda *args, **kwargs: {
            "attached": True,
            "object_id": 91,
            "class_id": 1003,
            "class_name": "CappDocument",
        }
        item = {
            "code": "Z02.A36.02.AR02",
            "kind": "process_doc",
            "machine": "02.A36.02.AR02",
            "file_path": self.make_file("Z02.A36.02.AR02.pdf"),
        }
        uploader.upload_item(item, {item["code"]}, None)
        self.assertEqual(uploader.links, [])

    def test_final_verification_detects_missing_shared_child_borrow(self):
        bridge = RuleBridge()
        uploader = RuleUploader(
            bridge,
            parts={
                "03.PARENT-A": 11,
                "03.PARENT-B": 12,
                "01.SHARED": 21,
            },
        )
        links_by_parent = {
            11: [
                {
                    "reference": True,
                    "roleA": {"object_id": 11},
                    "roleB": {"object_id": 21},
                }
            ],
            12: [],
        }
        uploader.part_info = lambda parent_id: {
            "part_id": parent_id,
            "children": [],
            "link_endpoints": links_by_parent[parent_id],
        }
        relations = [
            {
                "parent_code": "03.PARENT-A",
                "child_code": "01.SHARED",
                "link_type": "borrow",
            },
            {
                "parent_code": "03.PARENT-B",
                "child_code": "01.SHARED",
                "link_type": "borrow",
            },
        ]
        result = uploader.verify_relation_pairs(relations)
        self.assertEqual(result["verified"], 1)
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["parent_code"], "03.PARENT-B")
        self.assertEqual(result["issues"][0]["reason"], "missing_link")

        links_by_parent[12] = [
            {
                "reference": True,
                "roleA": {"object_id": 12},
                "roleB": {"object_id": 21},
            }
        ]
        result = uploader.verify_relation_pairs(relations)
        self.assertEqual(result["verified"], 2)
        self.assertEqual(result["issues"], [])

    def test_wrong_document_class_is_unverified(self):
        bridge = RuleBridge(
            upload_result={
                "ok": True,
                "attached": True,
                "object_id": 60,
                "class_name": "CappDocument",
                "class_id": 1003,
                "method": "low:CappDocument",
            }
        )
        uploader = RuleUploader(bridge, parts={"01.02": 12})
        item = component(
            code="01.02-01",
            file_path=self.make_file("01.02-01 零件.pdf"),
        )
        with self.assertRaises(UnverifiedResult):
            uploader.upload_item(
                item,
                {"01.02-01"},
                None,
                explicit_parent="01.02",
                relation_type="own",
            )

    def test_helper_attached_false_is_never_treated_as_success(self):
        class ExistingDrawingBridge(RuleBridge):
            def call(self, cmd, timeout=240.0, idempotent=None):
                if cmd.get("cmd") == "part_drawings":
                    return {
                        "ok": True,
                        "drawings": [
                            {
                                "object_id": 60,
                                "class_id": 1002,
                                "class_name": "Drawing",
                                "name": "01.02-01 零件.pdf",
                                "draw_no": "01.02-01",
                                "iterator_no": "b.1",
                                "history_count": 2,
                            }
                        ],
                    }
                return super().call(cmd, timeout, idempotent)

        bridge = ExistingDrawingBridge(
            upload_result={
                "ok": True,
                "attached": False,
                "object_id": 60,
                "class_name": "Drawing",
                "class_id": 1002,
                "method": "append_iteration_unverified",
                "stage": "upload_verify",
                "partial": True,
                "uncertain": True,
                "diag": {
                    "append_iteration_requested": True,
                    "before_iterator_no": "b.1",
                    "before_history_count": 2,
                    "attach": "null",
                    "attach_as_child": "null",
                    "attach_doc": "null",
                },
            }
        )
        uploader = RuleUploader(bridge, parts={"01.02": 12})
        item = component(
            code="01.02-01",
            file_path=self.make_file("01.02-01 零件.pdf"),
        )
        with self.assertRaisesRegex(
            UnverifiedResult,
            "CAXA 未确认文档挂接结果",
        ) as caught:
            uploader.upload_item(
                item,
                {"01.02-01"},
                None,
                explicit_parent="01.02",
                relation_type="own",
            )
        self.assertEqual(caught.exception.result["object_id"], 60)
        self.assertTrue(caught.exception.result["uncertain"])

    def test_matching_class_is_verified(self):
        bridge = RuleBridge()
        uploader = RuleUploader(bridge, parts={"01.02": 12})
        item = component(
            code="01.02-01",
            file_path=self.make_file("01.02-01 零件.pdf"),
        )
        result = uploader.upload_item(
            item,
            {"01.02-01"},
            None,
            explicit_parent="01.02",
            relation_type="own",
        )
        self.assertTrue(result["class_verified"])
        self.assertEqual(result["class_id"], 1002)


if __name__ == "__main__":
    unittest.main()

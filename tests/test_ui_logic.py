"""User-facing labels must not leak implementation policy names."""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401
import upload_ui_logic


class DuplicatePolicyLabelTests(unittest.TestCase):
    def test_policy_labels_are_chinese(self):
        self.assertEqual(
            upload_ui_logic.duplicate_policy_label("append_iteration"),
            "升版",
        )
        self.assertEqual(
            upload_ui_logic.duplicate_policy_label("reuse"),
            "复用",
        )

    def test_legacy_labels_load_with_the_new_wording(self):
        self.assertEqual(upload_ui_logic.duplicate_policy_label("追加新版次"), "升版")
        self.assertEqual(upload_ui_logic.duplicate_policy_label("已有则复用"), "复用")

    def test_chinese_label_maps_back_to_internal_policy(self):
        self.assertEqual(
            upload_ui_logic.duplicate_policy_value("追加新版次"),
            "append_iteration",
        )
        settings = upload_ui_logic.snapshot_settings(
            ip="127.0.0.1",
            port=8900,
            user="tester",
            password="unused",
            duplicate_policy="升版",
            start_workflow=False,
        )
        self.assertEqual(settings["duplicate_policy"], "append_iteration")

    def test_empty_caxa_password_is_allowed(self):
        settings = upload_ui_logic.snapshot_settings(
            ip="127.0.0.1",
            port=8900,
            user="tester",
            password="",
            duplicate_policy="追加新版次",
            start_workflow=False,
        )
        self.assertEqual(settings["password"], "")
        self.assertEqual(
            upload_ui_logic.account_from(settings)["password"],
            "",
        )


class PdfPathCollectionTests(unittest.TestCase):
    def test_folder_scan_is_one_level_and_pdf_only(self):
        with tempfile.TemporaryDirectory() as root:
            top_pdf = os.path.join(root, "A.PDF")
            ignored_text = os.path.join(root, "readme.txt")
            nested = os.path.join(root, "nested")
            os.mkdir(nested)
            nested_pdf = os.path.join(nested, "nested.pdf")
            for path in (top_pdf, ignored_text, nested_pdf):
                with open(path, "wb") as stream:
                    stream.write(b"test")

            result = upload_ui_logic.collect_pdf_paths([root])

            self.assertEqual(result["files"], [os.path.abspath(top_pdf)])
            self.assertEqual(result["ignored"], 2)
            self.assertEqual(result["errors"], [])

    def test_direct_pdf_and_duplicate_folder_entry_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as root:
            pdf = os.path.join(root, "A.pdf")
            with open(pdf, "wb") as stream:
                stream.write(b"test")

            result = upload_ui_logic.collect_pdf_paths([pdf, root, pdf])

            self.assertEqual(result["files"], [os.path.abspath(pdf)])
            self.assertEqual(result["ignored"], 0)


class RecoveryOperationLabelTests(unittest.TestCase):
    def test_upload_uses_business_label_and_drawing_code(self):
        text = upload_ui_logic.recovery_operation_text(
            {
                "kind": "upload",
                "key": "01.02",
                "item_code": "01.02",
                "phase": "upload:attach",
                "status": "执行中",
            }
        )
        self.assertIn("操作：图纸上传", text)
        self.assertIn("对象：01.02", text)
        self.assertIn("阶段：图纸挂接确认", text)
        self.assertNotIn("upload", text)
        self.assertNotIn("upload:attach", text)

    def test_relation_uses_business_label_and_endpoints(self):
        text = upload_ui_logic.recovery_operation_text(
            {
                "kind": "relation",
                "key": "03.HW.001|04.001",
                "parent_code": "03.HW.001",
                "child_code": "04.001",
                "phase": "relation_final_verify",
                "status": "待核实",
            }
        )
        self.assertIn("操作：次级/借用关系", text)
        self.assertIn("对象：03.HW.001 ← 04.001", text)
        self.assertIn("阶段：核实归属或借用关系", text)
        self.assertNotIn("relation", text)

    def test_workflow_prefers_instance_status_and_document_count(self):
        text = upload_ui_logic.recovery_operation_text(
            {
                "kind": "workflow",
                "key": "workflow",
                "remote_id": "16318",
                "phase": "started_unconfirmed",
                "status": "待核实",
                "class_name": "workflow",
                "detail": json.dumps(
                    {
                        "drawing_ids": [1001, 1002, 1003, 1004],
                        "stage": "started_unconfirmed",
                    }
                ),
            }
        )
        self.assertIn("操作：审核打印流程", text)
        self.assertIn("流程实例 ID：16318", text)
        self.assertIn("流程状态：待核实", text)
        self.assertIn("关联文档：4 项", text)
        self.assertIn("阶段：流程启动结果确认", text)
        self.assertNotIn("workflow/workflow", text)


class PlannedDrawingRemovalTests(unittest.TestCase):
    def test_removes_items_and_every_dangling_relation_source(self):
        items = [
            {"code": "03.HW.001", "kind": "machine_drawing"},
            {"code": "04.001", "kind": "component"},
            {"code": "01.001", "kind": "component"},
        ]
        relations = [
            {"parent_code": "03.HW.001", "child_code": "04.001"},
            {"parent_code": "04.001", "child_code": "01.001"},
            {"parent_code": "01.001", "child_code": "04.001"},
            {"parent_code": "04.999", "child_code": "01.001"},
        ]
        manual_borrows = [
            {"machine": "03.HW.001", "codes": ["04.001", "04.999"]},
            {"machine": "04.001", "codes": ["01.001"]},
            {"machine": "04.999", "codes": ["04.001", "01.001"]},
        ]
        result = upload_ui_logic.remove_planned_drawings(
            items,
            relations,
            manual_borrows,
            {"03.HW.001", "04.001"},
        )
        self.assertEqual([item["code"] for item in result["items"]], ["01.001"])
        self.assertEqual(
            result["relations"],
            [{"parent_code": "04.999", "child_code": "01.001"}],
        )
        self.assertEqual(
            result["manual_borrows"],
            [
                {"machine": "04.999", "codes": ["01.001"]},
            ],
        )

    def test_product_codes_only_cover_remaining_03_documents(self):
        self.assertEqual(
            upload_ui_logic.machine_product_codes(
                [
                    {"code": "Z03.HW.001", "kind": "process_doc", "machine": "03.HW.001"},
                    {"code": "03.OF.002", "kind": "machine_drawing"},
                    {"code": "Z02.A.001", "kind": "process_doc", "machine": "02.A.001"},
                    {"code": "04.001", "kind": "component", "machine": "03.HW.001"},
                ]
            ),
            {"03.HW.001", "03.OF.002"},
        )


class FilenameNormalizationPlanTests(unittest.TestCase):
    def test_process_document_with_chinese_name_is_canonicalized(self):
        fields, errors = upload_ui_logic.normalized_filename_fields(
            {"code": "", "kind": "unknown", "machine": ""},
            "Z03.HG.010250A-11P03 空气压缩机.pdf",
        )
        self.assertEqual(errors, [])
        self.assertEqual(fields["code"], "Z03.HG.010250A-11P03")
        self.assertEqual(fields["name"], "")
        self.assertEqual(fields["kind"], "process_doc")
        self.assertEqual(fields["machine"], "03.HG.010250A-11P03")

    def test_normalization_replaces_stale_process_machine(self):
        fields, errors = upload_ui_logic.normalized_filename_fields(
            {
                "code": "Z02.A36.02.AR02",
                "kind": "process_doc",
                "machine": "03.WRONG.001",
            },
            "Z02.A36.02.AR02.pdf",
        )
        self.assertEqual(errors, [])
        self.assertEqual(fields["code"], "Z02.A36.02.AR02")
        self.assertEqual(fields["kind"], "process_doc")
        self.assertEqual(fields["machine"], "02.A36.02.AR02")
        self.assertEqual(fields["name"], "")

    def test_rekey_migrates_items_relations_and_manual_borrows(self):
        items = [
            {"code": "OLD.001", "parent_code": "OLD.PARENT"},
            {"code": "OLD.PARENT", "borrow_to": "OLD.001"},
        ]
        relations = [
            {
                "parent_code": "OLD.PARENT",
                "child_code": "OLD.001",
                "link_type": "own",
            },
            {
                "parent_code": "NEW.PARENT",
                "child_code": "NEW.001",
                "link_type": "own",
            },
        ]
        manual_borrows = [
            {
                "machine": "OLD.PARENT",
                "codes": ["OLD.001", "NEW.001"],
            }
        ]
        changed = upload_ui_logic.rekey_planned_codes(
            items,
            relations,
            manual_borrows,
            {
                "OLD.001": "NEW.001",
                "OLD.PARENT": "NEW.PARENT",
            },
        )
        self.assertGreater(changed, 0)
        self.assertEqual(items[0]["code"], "NEW.001")
        self.assertEqual(items[0]["parent_code"], "NEW.PARENT")
        self.assertEqual(items[1]["code"], "NEW.PARENT")
        self.assertEqual(items[1]["borrow_to"], "NEW.001")
        self.assertEqual(
            relations,
            [
                {
                    "parent_code": "NEW.PARENT",
                    "child_code": "NEW.001",
                    "link_type": "own",
                }
            ],
        )
        self.assertEqual(
            manual_borrows,
            [{"machine": "NEW.PARENT", "codes": ["NEW.001"]}],
        )

    def test_no_write_upload_becomes_visible_retry(self):
        item = {
            "code": "01.01",
            "status": "待核实",
            "drawing_id": 9,
            "class_id": 1002,
        }
        changed = upload_ui_logic.mark_no_write_in_plan(
            [item],
            [],
            {"kind": "upload", "key": "01.01", "item_code": "01.01"},
        )
        self.assertTrue(changed)
        self.assertEqual(item["status"], "待上传")
        self.assertIn("可重新上传", item["note"])
        self.assertNotIn("drawing_id", item)
        self.assertNotIn("class_id", item)


class PrecheckItemMergeTests(unittest.TestCase):
    def test_existing_original_fields_are_copied_to_live_plan(self):
        items = [{"code": "01.01-01", "kind": "component"}]
        changed = upload_ui_logic.apply_precheck_items(
            items,
            {
                "items": [
                    {
                        "code": "01.01-01",
                        "existing_original": True,
                        "existing_original_part_id": 501,
                        "upgrade_only": True,
                        "series_product_name": "HX21 2.0",
                        "series_product_folder": "HX系列",
                        "series_product_folder_requested": "HX21系列",
                        "series_product_exists": True,
                        "status": "不应复制",
                    }
                ]
            },
        )
        self.assertEqual(changed, 1)
        self.assertTrue(items[0]["existing_original"])
        self.assertEqual(items[0]["existing_original_part_id"], 501)
        self.assertTrue(items[0]["upgrade_only"])
        self.assertEqual(items[0]["series_product_name"], "HX21 2.0")
        self.assertEqual(items[0]["series_product_folder"], "HX系列")
        self.assertEqual(
            items[0]["series_product_folder_requested"], "HX21系列"
        )
        self.assertTrue(items[0]["series_product_exists"])
        self.assertNotIn("status", items[0])


if __name__ == "__main__":
    unittest.main()

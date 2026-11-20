"""Existing CAXA Parts are upgraded and borrowed, never re-owned."""

import os
import unittest

import _support as support
import upload_core
from caxa.uploader import CaxaUploader, UnverifiedResult


class ExistingPartUploader(support.FakeUploader):
    existing_codes = set()

    def find_part(self, code):
        if str(code).upper() in self.existing_codes:
            return {"object_id": 500, "identity": code}
        return None


class ExistingOriginalRuleTests(unittest.TestCase):
    def test_legacy_drawing_name_is_repaired_before_upload(self):
        class Bridge:
            def __init__(self):
                self.calls = []

            def call(self, command, **kwargs):
                self.calls.append(command)
                return {
                    "ok": True,
                    "updated": True,
                    "name_after": "01.01-01 child.pdf",
                }

        bridge = Bridge()
        uploader = CaxaUploader(bridge, log=lambda *args: None)
        refreshed = [
            {
                "object_id": 501,
                "name": "01.01-01 child.pdf",
                "draw_no": "",
            }
        ]
        uploader.part_drawings = lambda *args, **kwargs: refreshed
        drawings = [
            {
                "object_id": 501,
                "name": "01.01-01 child",
                "draw_no": "",
            }
        ]
        result = uploader._repair_legacy_pdf_name(
            10, drawings, "01.01-01 child.pdf"
        )
        self.assertEqual(result, refreshed)
        self.assertEqual(bridge.calls[0]["cmd"], "set_drawing")
        self.assertEqual(bridge.calls[0]["name"], "01.01-01 child.pdf")

    def test_legacy_name_repair_requires_readback_confirmation(self):
        class Bridge:
            def call(self, command, **kwargs):
                return {"ok": True, "name_after": "different.pdf"}

        uploader = CaxaUploader(Bridge(), log=lambda *args: None)
        with self.assertRaises(UnverifiedResult):
            uploader._repair_legacy_pdf_name(
                10,
                [{"object_id": 501, "name": "01.01-01 child"}],
                "01.01-01 child.pdf",
            )

    def test_rule_defaults_and_relation_suppression(self):
        items = [
            {"code": "01.01-01", "kind": "component"},
            {"code": "01.01-02", "kind": "component"},
            {"code": "01.01-03", "kind": "component"},
        ]
        relations = [
            {
                "parent_code": "01.01",
                "child_code": "01.01-02",
                "link_type": "own",
            },
            {
                "parent_code": "01.99",
                "child_code": "01.01-03",
                "link_type": "borrow",
            },
        ]
        existing = {
            "01.01-01": {"object_id": 501},
            "01.01-02": {"object_id": 502},
        }
        result = upload_core.apply_existing_original_rules(
            items, relations, existing
        )
        self.assertTrue(items[0]["existing_original"])
        self.assertTrue(items[0]["upgrade_only"])
        self.assertFalse(items[1]["upgrade_only"])
        self.assertFalse(items[2]["existing_original"])
        self.assertEqual(result["only_upgrade_count"], 1)
        self.assertEqual(result["borrow_after_upgrade_count"], 1)

        active = upload_core.active_relations_for_upload(items, relations)
        self.assertEqual(
            active,
            [
                {
                    "parent_code": "01.01",
                    "child_code": "01.01-02",
                    "link_type": "own",
                },
                {
                    "parent_code": "01.99",
                    "child_code": "01.01-03",
                    "link_type": "borrow",
                },
            ],
        )

        class Lookup:
            def find_part(self, code):
                return {"object_id": 1}

        upload_core.refine_relations_from_tree(
            Lookup(),
            relations,
            {"01.01-02": True},
            force=True,
            existing_original_codes={"01.01-02"},
        )
        self.assertEqual(relations[0]["link_type"], "borrow")
        self.assertTrue(relations[0]["existing_original"])

    def test_unchecked_existing_original_without_parent_is_blocked(self):
        items = [
            {
                "code": "01.01-01",
                "kind": "component",
                "upgrade_only": False,
            }
        ]
        result = upload_core.apply_existing_original_rules(
            items, [], {"01.01-01": {"object_id": 501}}
        )
        self.assertEqual(
            result["missing_borrow_positions"], ["01.01-01"]
        )

    def test_multiple_existing_parts_are_reported_as_violation(self):
        class DuplicateLookup:
            def find_part_candidates(self, code):
                return [
                    {"object_id": 501, "identity": code},
                    {"object_id": 502, "identity": code},
                ]

        duplicate_codes = []
        found = upload_core.discover_existing_original_parts(
            DuplicateLookup(),
            [{"code": "01.01-01", "kind": "component"}],
            duplicate_codes=duplicate_codes,
        )
        self.assertEqual(found["01.01-01"]["object_id"], 501)
        self.assertEqual(duplicate_codes, ["01.01-01"])

    def test_upload_turns_existing_own_into_borrow(self):
        with support.temp_dir() as root:
            item = support.make_item(root, "01.01-01", "child")
            manifest = support.make_manifest(
                [item],
                [
                    support.relation(
                        "01.01",
                        "01.01-01",
                        "own",
                        manual_override=True,
                    )
                ],
            )
            ExistingPartUploader.existing_codes = {"01.01-01"}
            result, _bridge = support.run_upload(
                manifest,
                os.path.join(root, "journal.sqlite3"),
                uploader_cls=ExistingPartUploader,
            )
            uploader = ExistingPartUploader.instances[-1]
            self.assertTrue(result["ok"])
            self.assertEqual(
                uploader.borrow_actions[0]["link_type"], "borrow"
            )
            self.assertTrue(item["existing_original"])
            self.assertFalse(item["upgrade_only"])

    def test_upgrade_only_skips_all_relation_writes(self):
        with support.temp_dir() as root:
            item = support.make_item(root, "01.01-01", "child")
            item["upgrade_only"] = True
            manifest = support.make_manifest(
                [item],
                [
                    support.relation(
                        "01.01",
                        "01.01-01",
                        "borrow",
                        manual_override=True,
                    )
                ],
            )
            ExistingPartUploader.existing_codes = {"01.01-01"}
            result, bridge = support.run_upload(
                manifest,
                os.path.join(root, "journal.sqlite3"),
                uploader_cls=ExistingPartUploader,
                start_workflow=True,
            )
            uploader = ExistingPartUploader.instances[-1]
            self.assertTrue(result["ok"])
            self.assertEqual(uploader.borrow_actions, [])
            self.assertEqual(len(bridge.workflows), 1)


if __name__ == "__main__":
    unittest.main()

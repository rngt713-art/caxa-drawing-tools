"""Relation rules: single source, own/borrow by tree, stability, conflicts."""

import os
import unittest

import _support  # noqa: F401  (sets sys.path)
import relations
import drawing_bundle
import upload_core


class FakeTree:
    def __init__(self, existing, relation_types=None):
        self.existing = {code.upper() for code in existing}
        self.relation_types = {
            (str(parent).upper(), str(child).upper()): link_type
            for (parent, child), link_type in (relation_types or {}).items()
        }

    def find_part(self, code):
        return {
            "object_id": abs(hash(str(code).upper())) % 100000 + 1
        } if str(code).upper() in self.existing else None

    def existing_relation_type(self, parent, child):
        return self.relation_types.get(
            (str(parent).upper(), str(child).upper()),
            "",
        )


def pairs(manifest):
    return {
        (rel["parent_code"], rel["child_code"]) for rel in manifest.get("relations") or []
    }


class MigrationTests(unittest.TestCase):
    def test_legacy_collections_are_migrated_once(self):
        manifest = {
            "items": [],
            "assemblies": [{"machine": "03.TEST", "codes": ["01.01"]}],
            "manual_borrows": [{"machine": "01.02", "codes": ["01.03"]}],
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(
            pairs(manifest),
            {("03.TEST", "01.01"), ("01.02", "01.03")},
        )
        self.assertEqual(manifest["relations_migrated"], 1)

    def test_deleted_relation_does_not_come_back(self):
        manifest = {
            "items": [],
            "assemblies": [{"machine": "03.TEST", "codes": ["01.01"]}],
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        manifest["relations"] = []  # what delete_selected_relation leaves behind
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(manifest["relations"], [])

    def test_item_parent_is_kept_as_source(self):
        manifest = {
            "items": [
                {"code": "01.02", "kind": "component", "parent_code": "01.01"},
            ]
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(pairs(manifest), {("01.01", "01.02")})
        self.assertEqual(manifest["relations"][0]["source"], "drag")

    def test_new_item_after_migration_is_added_once(self):
        manifest = {"items": [{"code": "01.01", "kind": "component"}]}
        drawing_bundle.normalize_manifest_relations(manifest)
        manifest["items"].append(
            {"code": "01.02", "kind": "component", "parent_code": "01.01"}
        )
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(pairs(manifest), {("01.01", "01.02")})
        # Deleting it again must be permanent.
        manifest["relations"] = []
        manifest["items"][1]["parent_code"] = ""
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(pairs(manifest), set())

    def test_auto_prefix_uses_immediate_existing_parent(self):
        codes = [
            "01.21.01.SFD2-17",
            "01.21.01.SFD2-17-01",
            "01.21.01.SFD2-17-01-01",
            "01.21.01.SFD2-17-01-02",
            "01.21.01.SFD2-17-01-03",
            "01.21.01.SFD2-17-01-03-01",
            "01.21.01.SFD2-17-01-03-02",
        ]
        manifest = {
            "items": [{"code": code, "kind": "component"} for code in codes]
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(
            pairs(manifest),
            {
                ("01.21.01.SFD2-17", "01.21.01.SFD2-17-01"),
                ("01.21.01.SFD2-17-01", "01.21.01.SFD2-17-01-01"),
                ("01.21.01.SFD2-17-01", "01.21.01.SFD2-17-01-02"),
                ("01.21.01.SFD2-17-01", "01.21.01.SFD2-17-01-03"),
                (
                    "01.21.01.SFD2-17-01-03",
                    "01.21.01.SFD2-17-01-03-01",
                ),
                (
                    "01.21.01.SFD2-17-01-03",
                    "01.21.01.SFD2-17-01-03-02",
                ),
            },
        )
        self.assertEqual(manifest["auto_prefix_relation_count"], 6)

    def test_auto_prefix_adds_owner_alongside_borrow_parent(self):
        manifest = {
            "items": [
                {"code": "01.01", "kind": "component"},
                {"code": "01.01-01", "kind": "component"},
                {"code": "01.02", "kind": "component"},
            ],
            "relations": [
                {
                    "parent_code": "01.02",
                    "child_code": "01.01-01",
                    "link_type": "borrow",
                    "manual_override": True,
                }
            ],
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(
            pairs(manifest),
            {("01.02", "01.01-01"), ("01.01", "01.01-01")},
        )
        own = [
            rel for rel in manifest["relations"]
            if rel["child_code"] == "01.01-01" and rel["link_type"] == "own"
        ]
        self.assertEqual([rel["parent_code"] for rel in own], ["01.01"])

    def test_deleted_auto_prefix_relation_is_not_recreated(self):
        manifest = {
            "items": [
                {"code": "01.01", "kind": "component"},
                {"code": "01.01-01", "kind": "component"},
            ]
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(pairs(manifest), {("01.01", "01.01-01")})
        manifest["relations"] = []
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(pairs(manifest), set())

    def test_manual_import_auto_relation_stays_deleted(self):
        manifest = {
            "items": [
                {"code": "01.01", "kind": "component"},
                {"code": "01.01-01", "kind": "component"},
            ],
            "relations": [],
        }
        added = drawing_bundle.infer_code_relations(
            manifest, child_codes={"01.01-01"}
        )
        self.assertEqual(len(added), 1)
        self.assertTrue(manifest[relations.AUTO_PREFIX_FLAG])
        manifest["relations"] = []
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(pairs(manifest), set())

    def test_import_keeps_all_borrows_but_only_last_own_parent(self):
        manifest = {
            "items": [],
            "relations": [
                {"parent_code": "01.01", "child_code": "01.03", "link_type": "own"},
                {"parent_code": "01.02", "child_code": "01.03", "link_type": "own"},
                {"parent_code": "03.01", "child_code": "01.03", "link_type": "borrow"},
                {"parent_code": "03.02", "child_code": "01.03", "link_type": "borrow"},
            ],
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        owner = {
            (rel["parent_code"], rel["child_code"])
            for rel in manifest["relations"]
            if rel["link_type"] == "own"
        }
        borrows = {
            (rel["parent_code"], rel["child_code"])
            for rel in manifest["relations"]
            if rel["link_type"] == "borrow"
        }
        self.assertEqual(owner, {("01.02", "01.03")})
        self.assertEqual(
            borrows, {("03.01", "01.03"), ("03.02", "01.03")}
        )

    def test_borrow_with_multiple_parents_is_allowed(self):
        manifest = {
            "items": [],
            "relations": [
                {"parent_code": "01.01", "child_code": "01.03", "link_type": "borrow"},
                {"parent_code": "01.02", "child_code": "01.03", "link_type": "borrow"},
            ],
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertEqual(
            pairs(manifest),
            {("01.01", "01.03"), ("01.02", "01.03")},
        )

    def test_manual_link_only_flag_survives_normalization(self):
        manifest = {
            "items": [],
            "relations": [
                {
                    "parent_code": "03.MACH",
                    "child_code": "04.99",
                    "link_type": "borrow",
                    "manual_override": True,
                    "link_only": True,
                }
            ],
        }
        drawing_bundle.normalize_manifest_relations(manifest)
        self.assertTrue(manifest["relations"][0]["link_only"])


class TreeDecisionTests(unittest.TestCase):
    def test_ht_product_folders_use_special_nested_path(self):
        self.assertEqual(
            relations.series_product_folder("03.HT.HGB.001"),
            "HT合同特殊/HGB系列",
        )
        self.assertEqual(
            relations.series_product_folder("03.HT.HGB01-06350G"),
            "HT合同特殊/HGB系列",
        )
        self.assertEqual(
            relations.series_product_folder("03.HT.ABC-01"),
            "HT合同特殊",
        )
        self.assertEqual(
            relations.series_product_folder("03.HT.HG025-350W"),
            "HT合同特殊",
        )
        self.assertEqual(
            relations.series_product_folder("03.HW.015350-46P01"),
            "HW系列",
        )
        self.assertEqual(
            relations.series_product_folder("03.GPZ.L34040-T01"),
            "GPZ钢瓶组",
        )
        self.assertEqual(
            relations.series_product_folder("03.HX21.E20H-03L"),
            "HX系列",
        )
        self.assertEqual(
            relations.series_product_folder("03.HGB01-06350G"),
            "HGB系列",
        )
        self.assertEqual(
            relations.series_product_folder("03.HW21.001"),
            "HW系列",
        )
        self.assertEqual(
            relations.series_product_folder("03.HE300.001"),
            "HE系列",
        )
        self.assertEqual(
            relations.series_product_folder("03.GPZ1.L34040-T01"),
            "GPZ钢瓶组",
        )
        for code in ("03.OM.001", "03.OV.001", "03.OW.001"):
            self.assertEqual(
                relations.series_product_folder(code),
                "OW系列",
            )
        self.assertEqual(
            relations.series_product_folder("03.W6.001"),
            "",
        )

    def test_live_series_folder_alias_matching(self):
        folders = [
            {
                "path": "LSYZ两室一站",
                "path_parts": ["LSYZ两室一站"],
            },
            {
                "path": "GPZ钢瓶组",
                "path_parts": ["GPZ钢瓶组"],
            },
            {
                "path": "QT其他成品",
                "path_parts": ["QT其他成品"],
            },
            {
                "name": "NS",
                "path": "NS",
                "path_parts": ["NS"],
            },
            {
                "name": "NS防爆箱",
                "path": "NS防爆箱",
                "path_parts": ["NS防爆箱"],
            },
            {
                "path": "SFC防爆箱",
                "path_parts": ["SFC防爆箱"],
            },
            {
                "path": "SFD防爆箱",
                "path_parts": ["SFD防爆箱"],
            },
            {
                "path": "OW系列",
                "path_parts": ["OW系列"],
            },
            {
                "path": "HT合同特殊",
                "path_parts": ["HT合同特殊"],
            },
            {
                "path": "HT合同特殊/HGB系列",
                "path_parts": ["HT合同特殊", "HGB系列"],
            },
        ]

        def matched(code):
            row = relations.match_series_product_folder(code, folders)
            return row["path"] if row else ""

        self.assertEqual(matched("03.LSYZ.001"), "LSYZ两室一站")
        self.assertEqual(matched("03.GPZ.L34040-T01"), "GPZ钢瓶组")
        self.assertEqual(matched("03.QT.001"), "QT其他成品")
        self.assertEqual(matched("03.NS.13.ETB20-01"), "NS")
        self.assertEqual(matched("03.SFC.001"), "SFC防爆箱")
        self.assertEqual(matched("03.SFD.001"), "SFD防爆箱")
        self.assertEqual(matched("03.CFS.2NG-01"), "")
        self.assertEqual(matched("03.OM.001"), "OW系列")
        self.assertEqual(matched("03.OV.001"), "OW系列")
        self.assertEqual(matched("03.OW.001"), "OW系列")
        self.assertEqual(
            matched("03.HT.HGB01-06350G"),
            "HT合同特殊/HGB系列",
        )
        self.assertEqual(matched("03.W6.001"), "")

    def test_same_01_series_is_own_without_textual_prefix(self):
        tree = FakeTree([])
        rels = [
            {
                "parent_code": "01.22.01.V202.Z3",
                "child_code": "01.22.01.V202.1001",
            }
        ]
        upload_core.refine_relations_from_tree(tree, rels, log=lambda *a: None)
        self.assertEqual(rels[0]["link_type"], "own")

    def test_different_01_series_is_borrow(self):
        tree = FakeTree([])
        rels = [
            {
                "parent_code": "01.22.01.V202.Z3",
                "child_code": "01.23.01.V202.1001",
            }
        ]
        upload_core.refine_relations_from_tree(tree, rels, log=lambda *a: None)
        self.assertEqual(rels[0]["link_type"], "borrow")

    def test_cross_code_child_defaults_to_borrow(self):
        tree = FakeTree([])
        rels = [{"parent_code": "01.01", "child_code": "04.02"}]
        upload_core.refine_relations_from_tree(tree, rels, log=lambda *a: None)
        self.assertEqual(rels[0]["link_type"], "borrow")

    def test_existing_child_defaults_to_borrow(self):
        tree = FakeTree(["01.02"])
        rels = [{"parent_code": "01.01", "child_code": "04.02"}]
        upload_core.refine_relations_from_tree(tree, rels, log=lambda *a: None)
        self.assertEqual(rels[0]["link_type"], "borrow")

    def test_machine_prefix_is_borrow(self):
        tree = FakeTree([])
        rels = [{"parent_code": "03.MACH", "child_code": "01.02"}]
        upload_core.refine_relations_from_tree(tree, rels, log=lambda *a: None)
        self.assertEqual(rels[0]["link_type"], "borrow")

    def test_prefix_child_is_own_even_when_part_exists(self):
        tree = FakeTree(["01.02"])
        rels = [{"parent_code": "01.02", "child_code": "01.02-01"}]
        upload_core.refine_relations_from_tree(tree, rels, log=lambda *a: None)
        self.assertEqual(rels[0]["link_type"], "own")

    def test_manual_override_wins_over_tree(self):
        tree = FakeTree(["01.02"])
        rels = [
            {
                "parent_code": "01.01",
                "child_code": "01.02",
                "link_type": "own",
                "manual_override": True,
            }
        ]
        upload_core.refine_relations_from_tree(tree, rels, log=lambda *a: None)
        self.assertEqual(rels[0]["link_type"], "own")
        self.assertTrue(rels[0]["manual_override"])

    def test_retry_does_not_flip_own_to_borrow(self):
        rels = [{"parent_code": "01.01", "child_code": "01.01-01"}]
        upload_core.refine_relations_from_tree(
            FakeTree([]), rels, log=lambda *a: None
        )
        self.assertEqual(rels[0]["link_type"], "own")

    def test_existing_own_relation_is_preserved_and_only_upgraded(self):
        parent = "01.21.01.V1-01"
        child = "01.21.01.V1-01-07"
        tree = FakeTree(
            [parent, child],
            relation_types={(parent, child): "own"},
        )
        items = [
            {"code": parent, "kind": "component"},
            {"code": child, "kind": "component"},
        ]
        rels = [{"parent_code": parent, "child_code": child}]
        upload_core.refine_relations_from_tree(
            tree,
            rels,
            existing_original_codes={parent, child},
            log=lambda *_args: None,
        )
        rules = upload_core.apply_existing_original_rules(
            items,
            rels,
            {
                parent: {"object_id": 11},
                child: {"object_id": 12},
            },
        )
        self.assertEqual(rels[0]["link_type"], "own")
        self.assertTrue(rels[0]["existing_relation"])
        self.assertTrue(items[1]["existing_relation"])
        self.assertEqual(items[1]["existing_relation_type"], "own")
        self.assertTrue(items[1]["upgrade_only"])
        self.assertEqual(rules["borrow_conversion_codes"], [])
        self.assertEqual(
            upload_core.active_relations_for_upload(items, rels),
            [],
        )
        # The code-prefix owner is stable regardless of whether the part exists.
        upload_core.refine_relations_from_tree(
            FakeTree(["01.02"]), rels, log=lambda *a: None
        )
        self.assertEqual(rels[0]["link_type"], "own")
        # An explicit re-check keeps the ownership rule as well.
        upload_core.refine_relations_from_tree(
            FakeTree(["01.02"]), rels, log=lambda *a: None, force=True
        )
        self.assertEqual(rels[0]["link_type"], "own")


class ConflictTests(unittest.TestCase):
    def test_cycle_is_detected(self):
        analysis = relations.analyze_relations(
            [
                {"parent_code": "01.01", "child_code": "01.02", "link_type": "own"},
                {"parent_code": "01.02", "child_code": "01.01", "link_type": "own"},
            ]
        )
        self.assertFalse(analysis["ok"])
        self.assertEqual(analysis["issues"][0]["type"], "cycle")

    def test_own_child_with_two_parents_is_detected(self):
        analysis = relations.analyze_relations(
            [
                {"parent_code": "01.01", "child_code": "01.03", "link_type": "own"},
                {"parent_code": "01.02", "child_code": "01.03", "link_type": "own"},
            ]
        )
        self.assertFalse(analysis["ok"])
        types = {issue["type"] for issue in analysis["issues"]}
        self.assertIn("multi_own_parent", types)

    def test_borrow_with_two_parents_is_allowed(self):
        analysis = relations.analyze_relations(
            [
                {"parent_code": "01.01", "child_code": "01.03", "link_type": "borrow"},
                {"parent_code": "01.02", "child_code": "01.03", "link_type": "borrow"},
            ]
        )
        self.assertTrue(analysis["ok"])


if __name__ == "__main__":
    unittest.main()

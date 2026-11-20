"""Automatic recovery evidence from business-visible CAXA identifiers."""

import unittest
import json

import _support  # noqa: F401
import recovery_verifier


class FakeUploader:
    def __init__(self, parts=None, drawings=None, relation_result=None):
        self.parts = parts or {}
        self.drawings = drawings or []
        self.relation_result = relation_result or {"issues": []}
        self.lookups = []

    def find_part(self, code):
        self.lookups.append(code)
        return self.parts.get(code)

    def part_drawings(self, part_id, strict=False, refresh=False):
        return list(self.drawings)

    def verify_relation_pairs(self, relations):
        self.verified_relations = list(relations)
        return self.relation_result

    def close(self):
        pass


class FakeBridge:
    def __init__(self, workflow=None):
        self.workflow = workflow or {}
        self.calls = []

    def workflow_info(self, process_id):
        self.calls.append(process_id)
        return dict(self.workflow)

    def stop(self):
        pass


class RecoveryVerifierTests(unittest.TestCase):
    def append_row(self, before_iterator, before_history):
        return {
            "kind": "upload",
            "item_code": "01.61.02.OV-11",
            "detail": json.dumps(
                {
                    "result": {
                        "file_name": "01.61.02.OV-11 一级缓冲罐.pdf",
                        "method": "append_iteration_unverified",
                        "diag": {
                            "append_iteration_requested": True,
                            "before_iterator_no": before_iterator,
                            "before_history_count": before_history,
                        },
                    }
                },
                ensure_ascii=False,
            ),
        }

    def test_append_recovery_requires_history_growth(self):
        verifier = recovery_verifier.RecoveryVerifier(
            FakeBridge(),
            FakeUploader(
                parts={"01.61.02.OV-11": {"object_id": 10}},
                drawings=[
                    {
                        "object_id": 31039,
                        "class_id": 1002,
                        "class_name": "Drawing",
                        "name": "01.61.02.OV-11 一级缓冲罐.pdf",
                        "iterator_no": "c.1",
                        "history_count": 4,
                    }
                ],
            ),
        )
        with self.assertRaisesRegex(
            recovery_verifier.RecoveryEvidenceError,
            "版次历史未增加",
        ):
            verifier.verify_upload(self.append_row("c.1", 4))

    def test_append_recovery_accepts_confirmed_history_growth(self):
        verifier = recovery_verifier.RecoveryVerifier(
            FakeBridge(),
            FakeUploader(
                parts={"01.61.02.OV-11": {"object_id": 10}},
                drawings=[
                    {
                        "object_id": 31039,
                        "class_id": 1002,
                        "class_name": "Drawing",
                        "name": "01.61.02.OV-11 一级缓冲罐.pdf",
                        "iterator_no": "c.2",
                        "history_count": 5,
                    }
                ],
            ),
        )
        evidence = verifier.verify_upload(self.append_row("c.1", 4))
        self.assertEqual(
            evidence,
            {
                "remote_id": 31039,
                "class_id": 1002,
                "iterator_no": "c.2",
            },
        )

    def test_append_recovery_without_before_evidence_is_blocked(self):
        verifier = recovery_verifier.RecoveryVerifier(
            FakeBridge(),
            FakeUploader(
                parts={"01.61.02.OV-11": {"object_id": 10}},
                drawings=[
                    {
                        "object_id": 31039,
                        "class_id": 1002,
                        "class_name": "Drawing",
                        "name": "01.61.02.OV-11 一级缓冲罐.pdf",
                        "iterator_no": "c.1",
                        "history_count": 4,
                    }
                ],
            ),
        )
        with self.assertRaisesRegex(
            recovery_verifier.RecoveryEvidenceError,
            "缺少升版前版次证据",
        ):
            verifier.verify_upload(
                {
                    "kind": "upload",
                    "item_code": "01.61.02.OV-11",
                    "detail": json.dumps(
                        {
                            "result": {
                                "file_name": "01.61.02.OV-11 一级缓冲罐.pdf",
                                "method": "append_iteration_unverified",
                            }
                        },
                        ensure_ascii=False,
                    ),
                }
            )

    def test_upload_uses_code_and_target_name_to_fill_hidden_evidence(self):
        uploader = FakeUploader(
            parts={"01.01": {"object_id": 10}},
            drawings=[
                {
                    "object_id": 101,
                    "class_id": 1002,
                    "class_name": "Drawing",
                    "draw_no": "01.01",
                    "file_name": "01.01 旧版本.pdf",
                    "iterator_no": "a.1",
                },
                {
                    "object_id": 102,
                    "class_id": 1002,
                    "class_name": "Drawing",
                    "draw_no": "01.01",
                    "file_name": "01.01 新版本.pdf",
                    "iterator_no": "b.2",
                },
            ],
        )
        verifier = recovery_verifier.RecoveryVerifier(
            FakeBridge(), uploader
        )
        evidence = verifier.verify_upload(
            {
                "kind": "upload",
                "item_code": "01.01",
                "detail": '{"result":{"file_name":"01.01 新版本.pdf"}}',
            }
        )
        self.assertEqual(
            evidence,
            {"remote_id": 102, "class_id": 1002, "iterator_no": "b.2"},
        )
        self.assertEqual(uploader.lookups, ["01.01"])

    def test_process_document_reads_machine_part_and_requires_capp_class(self):
        uploader = FakeUploader(
            parts={"03.HW.001": {"object_id": 20}},
            drawings=[
                {
                    "object_id": 201,
                    "class_id": 1003,
                    "class_name": "CappDocument",
                    "draw_no": "Z03.HW.001",
                    "file_name": "Z03.HW.001.pdf",
                    "iterator_no": "a.1",
                }
            ],
        )
        verifier = recovery_verifier.RecoveryVerifier(FakeBridge(), uploader)
        evidence = verifier.verify_upload(
            {"kind": "upload", "item_code": "Z03.HW.001"}
        )
        self.assertEqual(evidence["class_id"], 1003)
        self.assertEqual(uploader.lookups, ["03.HW.001"])

    def test_part_drawings_name_field_is_used_when_file_name_is_empty(self):
        uploader = FakeUploader(
            parts={"01.01": {"object_id": 10}},
            drawings=[
                {
                    "object_id": 103,
                    "class_id": 1002,
                    "class_name": "drawing",
                    "draw_no": "",
                    "name": "01.01 新版图纸.pdf",
                    "file_name": "",
                    "iterator_no": "c.2",
                }
            ],
        )
        verifier = recovery_verifier.RecoveryVerifier(FakeBridge(), uploader)
        evidence = verifier.verify_upload(
            {
                "kind": "upload",
                "item_code": "01.01",
                "detail": '{"result":{"file_name":"01.01 新版图纸.pdf"}}',
            }
        )
        self.assertEqual(
            evidence,
            {"remote_id": 103, "class_id": 1002, "iterator_no": "c.2"},
        )

    def test_wrong_document_class_or_ambiguous_current_rows_stay_pending(self):
        verifier = recovery_verifier.RecoveryVerifier(
            FakeBridge(),
            FakeUploader(
                parts={"01.01": {"object_id": 10}},
                drawings=[
                    {
                        "object_id": 101,
                        "class_id": 1003,
                        "draw_no": "01.01",
                        "file_name": "01.01 图纸.pdf",
                        "iterator_no": "a.1",
                    }
                ],
            ),
        )
        with self.assertRaisesRegex(
            recovery_verifier.RecoveryEvidenceError, "类型不匹配"
        ):
            verifier.verify_upload(
                {"kind": "upload", "item_code": "01.01"}
            )

        ambiguous = recovery_verifier.RecoveryVerifier(
            FakeBridge(),
            FakeUploader(
                parts={"01.01": {"object_id": 10}},
                drawings=[
                    {
                        "object_id": 101,
                        "class_id": 1002,
                        "draw_no": "01.01",
                        "file_name": "01.01 A.pdf",
                        "iterator_no": "a.1",
                    },
                    {
                        "object_id": 102,
                        "class_id": 1002,
                        "draw_no": "01.01",
                        "file_name": "01.01 B.pdf",
                        "iterator_no": "b.2",
                    },
                ],
            ),
        )
        with self.assertRaisesRegex(
            recovery_verifier.RecoveryEvidenceError, "多条"
        ):
            ambiguous.verify_upload(
                {"kind": "upload", "item_code": "01.01"}
            )

    def test_relation_is_checked_from_planned_endpoints_and_type(self):
        uploader = FakeUploader(relation_result={"issues": []})
        verifier = recovery_verifier.RecoveryVerifier(FakeBridge(), uploader)
        row = {
            "kind": "relation",
            "parent_code": "03.HW.001",
            "child_code": "04.001",
            "link_type": "borrow",
        }
        self.assertEqual(
            verifier.verify_relation(row), {"link_type": "borrow"}
        )
        self.assertEqual(
            uploader.verified_relations,
            [{
                "parent_code": "03.HW.001",
                "child_code": "04.001",
                "link_type": "borrow",
            }],
        )

    def test_workflow_reads_state_and_documents_from_visible_instance_id(self):
        bridge = FakeBridge(
            workflow={
                "process_instance_id": 501,
                "process_state": "2",
                "linked_document_ids": [12, 13],
            }
        )
        verifier = recovery_verifier.RecoveryVerifier(
            bridge, FakeUploader()
        )
        evidence = verifier.verify_workflow(
            {
                "kind": "workflow",
                "detail": '{"drawing_ids":[13,12]}',
            },
            501,
        )
        self.assertEqual(
            evidence,
            {
                "process_state": "2",
                "linked_document_ids": [12, 13],
            },
        )
        self.assertEqual(bridge.calls, [501])


if __name__ == "__main__":
    unittest.main()

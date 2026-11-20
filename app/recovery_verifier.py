"""Read CAXA evidence for a pending operation using business-visible data.

Users see drawing codes, target file names, versions, relation endpoints and
workflow instance IDs.  Internal object ids and document class ids remain in
CAXA and are collected here instead of being typed by the operator.
"""

from __future__ import annotations

import json
import os
import re

import drawing_bundle
from caxa.bridge import CaxaBridge, CaxaError
from caxa.uploader import CaxaUploader


CLASS_ID_DRAWING = 1002
CLASS_ID_CAPP = 1003


class RecoveryEvidenceError(RuntimeError):
    pass


def _detail(row: dict) -> dict:
    raw = row.get("detail") or ""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _positive_int(value, label: str) -> int:
    text = str(value or "").strip()
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        raise RecoveryEvidenceError("%s不是有效正整数" % label)
    return int(text)


def _optional_nonnegative_int(value):
    text = str(value if value is not None else "").strip()
    if not text.isascii() or not text.isdecimal():
        return None
    return int(text)


def _iteration_key(value):
    match = re.fullmatch(r"([A-Za-z]+)\.(\d+)", str(value or "").strip())
    if not match:
        return None
    return match.group(1).upper(), int(match.group(2))


def _append_baseline(row: dict):
    """Return before-upgrade evidence for an append_iteration operation."""
    payload = _detail(row)
    result = payload.get("result")
    if not isinstance(result, dict):
        result = payload
    diag = result.get("diag")
    if not isinstance(diag, dict):
        diag = {}

    method = str(result.get("method") or "")
    append_requested = bool(diag.get("append_iteration_requested"))
    if (
        not append_requested
        and method not in ("append_iteration", "append_iteration_unverified")
    ):
        return None
    return {
        "before_history_count": _optional_nonnegative_int(
            diag.get("before_history_count")
        ),
        "before_iterator_no": str(
            diag.get("before_iterator_no") or ""
        ).strip(),
    }


def _verify_append_growth(
    row: dict,
    candidate: dict,
    iterator_no: str,
) -> None:
    baseline = _append_baseline(row)
    if baseline is None:
        return

    before_count = baseline["before_history_count"]
    before_iterator = baseline["before_iterator_no"]
    if before_count is None and not before_iterator:
        raise RecoveryEvidenceError(
            "追加版次记录缺少升版前版次证据，不能确认本次上传成功"
        )

    current_count = _optional_nonnegative_int(candidate.get("history_count"))
    if before_count is not None:
        if current_count is None:
            raise RecoveryEvidenceError(
                "CAXA 未返回当前历史数，无法确认版次已增加"
            )
        if current_count <= before_count:
            raise RecoveryEvidenceError(
                "版次历史未增加：升版前 %d，当前 %d"
                % (before_count, current_count)
            )
        return

    before_key = _iteration_key(before_iterator)
    current_key = _iteration_key(iterator_no)
    if before_key is None or current_key is None:
        raise RecoveryEvidenceError(
            "无法比较升版前版次 %s 和当前版次 %s"
            % (before_iterator, iterator_no)
        )
    if current_key <= before_key:
        raise RecoveryEvidenceError(
            "当前版次没有增长：升版前 %s，当前 %s"
            % (before_iterator, iterator_no)
        )


def expected_document_class(code: str, item: dict | None = None) -> int:
    del item
    kind, _machine = drawing_bundle.infer_kind(code)
    return CLASS_ID_CAPP if kind == "process_doc" else CLASS_ID_DRAWING


def upload_part_code(code: str, item: dict | None = None) -> str:
    code = drawing_bundle.normalize_code(code)
    kind, _machine = drawing_bundle.infer_kind(code)
    if kind == "process_doc":
        return code[1:]
    return code


def expected_target_filename(row: dict, item: dict | None = None) -> str:
    payload = _detail(row)
    result = payload.get("result")
    if isinstance(result, dict):
        file_name = str(result.get("file_name") or "").strip()
        if file_name:
            return os.path.basename(file_name)
    for key in ("file_name", "target_filename"):
        file_name = str(payload.get(key) or "").strip()
        if file_name:
            return os.path.basename(file_name)
    if item is not None:
        code = drawing_bundle.normalize_code(item.get("code"))
        return drawing_bundle.expected_drawing_filename(
            code, str(item.get("name") or "")
        )
    return ""


def _filename_code(file_name: str) -> str:
    stem = os.path.splitext(os.path.basename(str(file_name or "")))[0]
    return drawing_bundle.normalize_code(stem.split(maxsplit=1)[0] if stem else "")


def _candidate_filename(candidate: dict) -> str:
    """CAXA may expose the uploaded PDF name through either field."""
    return str(candidate.get("file_name") or candidate.get("name") or "").strip()


def _candidate_matches(candidate: dict, code: str) -> bool:
    draw_no = drawing_bundle.normalize_code(candidate.get("draw_no"))
    file_code = _filename_code(_candidate_filename(candidate))
    return draw_no == code or file_code == code


def _candidate_label(candidate: dict) -> str:
    return "%s（%s）" % (
        _candidate_filename(candidate)
        or candidate.get("draw_no")
        or candidate.get("object_id")
        or "未命名图纸",
        candidate.get("iterator_no") or "版次未知",
    )


class RecoveryVerifier:
    def __init__(self, bridge, uploader, *, log=print):
        self.bridge = bridge
        self.uploader = uploader
        self.log = log

    @classmethod
    def connect(cls, account: dict, journal, *, log=print):
        # Import lazily so unit tests and non-CAXA UI startup do not require the
        # upload orchestrator's full import graph.
        from upload_core import prepare_device_plm_com_registration

        bridge = CaxaBridge()
        prepare_device_plm_com_registration(bridge, journal, log=log)
        bridge.login(
            account["ip"],
            int(account["port"]),
            account["user"],
            account["password"],
        )
        uploader = CaxaUploader(bridge, log=log)
        return cls(bridge, uploader, log=log)

    def close(self):
        try:
            self.uploader.close()
        finally:
            self.bridge.stop()

    def verify_upload(self, row: dict, item: dict | None = None) -> dict:
        """Find the uploaded document by code/file name and read its evidence."""
        code = drawing_bundle.normalize_code(
            row.get("item_code") or row.get("key")
        )
        if not drawing_bundle.is_valid_code(code):
            raise RecoveryEvidenceError("待核实记录缺少有效图号")
        part_code = upload_part_code(code, item)
        expected_class = expected_document_class(code, item)
        part = self.uploader.find_part(part_code)
        if not part or not part.get("object_id"):
            raise RecoveryEvidenceError(
                "CAXA 中未找到宿主零件 %s，不能确认图纸已上传" % part_code
            )
        drawings = self.uploader.part_drawings(
            int(part["object_id"]), strict=True, refresh=True
        )
        if drawings is None:
            raise RecoveryEvidenceError("CAXA 图纸列表回读失败，请保持待核实")

        matching_code = [
            row for row in drawings
            if _candidate_matches(row, code)
        ]
        if not matching_code:
            raise RecoveryEvidenceError(
                "CAXA 的零件 %s 下未找到图号 %s 的图纸"
                % (part_code, code)
            )
        missing_class = [
            row for row in matching_code
            if not str(row.get("class_id") or "").strip()
        ]
        if missing_class:
            raise RecoveryEvidenceError(
                "CAXA helper 未返回图纸类型，请重建 helper 后再核实"
            )
        classified = [
            row for row in matching_code
            if int(row["class_id"]) == expected_class
        ]
        if not classified:
            actual = sorted({str(row.get("class_name") or row.get("class_id")) for row in matching_code})
            raise RecoveryEvidenceError(
                "图号 %s 找到的文档类型不匹配：%s" % (code, "、".join(actual))
            )

        target_name = expected_target_filename(row, item)
        exact_name = [
            candidate for candidate in classified
            if target_name
            and os.path.basename(_candidate_filename(candidate)).casefold()
            == target_name.casefold()
        ]
        candidates = exact_name or classified
        if len(candidates) != 1:
            raise RecoveryEvidenceError(
                "CAXA 中找到多条同一图号的当前文档，无法自动确定版本：%s"
                % "；".join(_candidate_label(candidate) for candidate in candidates[:8])
            )
        candidate = candidates[0]
        object_id = _positive_int(candidate.get("object_id"), "图纸对象 ID")
        iterator_no = str(candidate.get("iterator_no") or "").strip()
        if not iterator_no:
            raise RecoveryEvidenceError("CAXA 中该图纸没有可回读的版次")
        _verify_append_growth(row, candidate, iterator_no)
        return {
            "remote_id": object_id,
            "class_id": expected_class,
            "iterator_no": iterator_no,
        }

    def verify_relation(self, row: dict) -> dict:
        parent = drawing_bundle.normalize_code(row.get("parent_code"))
        child = drawing_bundle.normalize_code(row.get("child_code"))
        link_type = str(row.get("link_type") or "").strip()
        if not parent or not child or link_type not in ("own", "borrow"):
            raise RecoveryEvidenceError(
                "待核实关系缺少父级、子级或归属/借用类型"
            )
        result = self.uploader.verify_relation_pairs([
            {
                "parent_code": parent,
                "child_code": child,
                "link_type": link_type,
            }
        ])
        issues = result.get("issues") or []
        if issues:
            issue = issues[0]
            raise RecoveryEvidenceError(
                "CAXA 关系回读未通过：%s"
                % (issue.get("note") or issue.get("reason") or "未知原因")
            )
        return {"link_type": link_type}

    def verify_workflow(self, row: dict, process_instance_id=None) -> dict:
        payload = _detail(row)
        expected_ids = payload.get("drawing_ids")
        if not isinstance(expected_ids, list) or not expected_ids:
            raise RecoveryEvidenceError(
                "待核实流程记录缺少本批文档集合，无法自动核实"
            )
        expected = {
            _positive_int(value, "计划文档 ID") for value in expected_ids
        }
        process_id = _positive_int(
            process_instance_id or row.get("remote_id"),
            "流程实例 ID",
        )
        info = self.bridge.workflow_info(process_id)
        state = str(info.get("process_state") or "").strip()
        observed_ids = info.get("linked_document_ids")
        if not isinstance(observed_ids, list):
            raise RecoveryEvidenceError("流程关联文档回读失败，请保持待核实")
        observed = {
            _positive_int(value, "流程关联文档 ID") for value in observed_ids
        }
        if state != "2":
            raise RecoveryEvidenceError(
                "流程当前状态为 %s，不是运行中（2）" % (state or "未知")
            )
        if observed != expected:
            raise RecoveryEvidenceError(
                "流程实际关联 %d 项文档，与本批应关联 %d 项不一致"
                % (len(observed), len(expected))
            )
        return {
            "process_state": state,
            "linked_document_ids": sorted(observed),
        }


def recovery_evidence_summary(row: dict, evidence: dict) -> str:
    kind = str(row.get("kind") or "")
    if kind == "upload":
        return "CAXA 已回读：图号 %s，版次 %s，文档类型 %s" % (
            drawing_bundle.normalize_code(row.get("item_code") or row.get("key")),
            evidence.get("iterator_no"),
            "CappDocument" if int(evidence.get("class_id") or 0) == CLASS_ID_CAPP else "Drawing",
        )
    if kind == "relation":
        return "CAXA 已回读：%s ← %s（%s）" % (
            drawing_bundle.normalize_code(row.get("parent_code")),
            drawing_bundle.normalize_code(row.get("child_code")),
            "归属" if evidence.get("link_type") == "own" else "借用",
        )
    if kind == "workflow":
        return "CAXA 已回读：流程实例 %s，运行中，关联文档 %d 项" % (
            row.get("remote_id") or "",
            len(evidence.get("linked_document_ids") or []),
        )
    return "CAXA 已完成回读"

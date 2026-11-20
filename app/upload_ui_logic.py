"""Pure helpers for the Tk uploader.

The GUI itself is thin: these functions build the frozen snapshot that a worker
thread may read, merge results back, and decide what may be clicked while a task
runs.  Keeping them free of Tk objects makes the busy/snapshot rules testable.
"""

from __future__ import annotations

import copy
import json

import drawing_bundle

DEFAULT_DUPLICATE_POLICY = "append_iteration"
DUPLICATE_POLICIES = ("append_iteration", "reuse")
DUPLICATE_POLICY_LABELS = {
    "append_iteration": "追加新版次",
    "reuse": "已有则复用",
}
_DUPLICATE_LABEL_TO_POLICY = {
    label: policy for policy, label in DUPLICATE_POLICY_LABELS.items()
}

ITEM_DONE = "已上传"
ITEM_PENDING = "待上传"

_RECOVERY_KIND_LABELS = {
    "upload": "图纸上传",
    "relation": "次级/借用关系",
    "workflow": "审核打印流程",
    "decision": "关系类型确认",
}

_RECOVERY_PHASE_LABELS = {
    "upload": "上传确认",
    "upload:attach": "图纸挂接确认",
    "upload:existing_duplicate": "重复对象确认",
    "upload:verify_iteration": "新版次确认",
    "upload:create_part": "图纸宿主结构创建",
    "relation": "关系建链",
    "relation_final_verify": "关系建链回读",
    "workflow": "流程启动",
    "workflow:create": "流程启动",
    "create": "流程启动",
    "started_unconfirmed": "流程启动结果确认",
    "workflow:started_unconfirmed": "流程启动结果确认",
}


class SettingsError(ValueError):
    pass


def duplicate_policy_label(value: str | None) -> str:
    policy = str(value or DEFAULT_DUPLICATE_POLICY)
    return DUPLICATE_POLICY_LABELS.get(policy, policy)


def duplicate_policy_value(label_or_value: str | None) -> str:
    text = str(label_or_value or DEFAULT_DUPLICATE_POLICY).strip()
    return _DUPLICATE_LABEL_TO_POLICY.get(text, text)


def snapshot_settings(
    *,
    ip: str,
    port,
    user: str,
    password: str,
    duplicate_policy: str,
    start_workflow: bool,
    performer: str = "",
) -> dict:
    """Validate GUI values once, on the UI thread, then freeze them."""
    ip_text = str(ip or "").strip()
    user_text = str(user or "").strip()
    if not ip_text:
        raise SettingsError("请填写 CAXA 服务器 IP")
    if not user_text:
        raise SettingsError("请填写 CAXA 用户名")
    try:
        port_int = int(str(port).strip())
    except (TypeError, ValueError):
        raise SettingsError("CAXA 端口必须是数字")
    if not 0 < port_int < 65536:
        raise SettingsError("CAXA 端口超出范围")
    policy = duplicate_policy_value(duplicate_policy)
    if policy not in DUPLICATE_POLICIES:
        raise SettingsError("未知的重复图号策略：%s" % policy)
    return {
        "ip": ip_text,
        "port": port_int,
        "user": user_text,
        "password": password,
        "duplicate_policy": policy,
        "start_workflow": bool(start_workflow),
        "performer": str(performer or "").strip(),
    }


def account_from(settings: dict) -> dict:
    return {
        "ip": settings["ip"],
        "port": int(settings["port"]),
        "user": settings["user"],
        "password": settings["password"],
    }


def identity_key(settings: dict) -> tuple[str, int, str]:
    return (str(settings["ip"]), int(settings["port"]), str(settings["user"]))


def snapshot_manifest(manifest: dict | None) -> dict:
    """Deep copy so the worker never touches objects the UI can still edit."""
    if not manifest:
        return {}
    return copy.deepcopy(json.loads(json.dumps(manifest, ensure_ascii=False)))


def apply_upload_result(items: list[dict], result_manifest: dict) -> int:
    """Copy per-item results from the frozen manifest back into the live list."""
    by_code = {
        drawing_bundle.normalize_code(item.get("code")): item
        for item in (result_manifest or {}).get("items") or []
    }
    merged = 0
    for item in items:
        code = drawing_bundle.normalize_code(item.get("code"))
        source = by_code.get(code)
        if source is None:
            continue
        for key in ("status", "note", "target", "caxa_path", "drawing_id", "class_name", "class_id"):
            if key in source:
                item[key] = source[key]
        merged += 1
    return merged


def apply_relation_result(relations: list[dict], result_manifest: dict) -> int:
    by_pair = {
        (
            drawing_bundle.normalize_code(rel.get("parent_code")),
            drawing_bundle.normalize_code(rel.get("child_code")),
        ): rel
        for rel in (result_manifest or {}).get("relations") or []
    }
    merged = 0
    for relation in relations:
        key = (
            drawing_bundle.normalize_code(relation.get("parent_code")),
            drawing_bundle.normalize_code(relation.get("child_code")),
        )
        source = by_pair.get(key)
        if source is None:
            continue
        for field in ("link_type", "link_status", "note", "manual_override", "auto_decided"):
            if field in source:
                relation[field] = source[field]
        merged += 1
    return merged


def apply_precheck_items(items: list[dict], result_manifest: dict) -> int:
    """Copy precheck-only fields from a frozen snapshot into the live plan."""
    by_code = {
        drawing_bundle.normalize_code(item.get("code")): item
        for item in (result_manifest or {}).get("items") or []
        if drawing_bundle.normalize_code(item.get("code"))
    }
    merged = 0
    for item in items:
        code = drawing_bundle.normalize_code(item.get("code"))
        source = by_code.get(code)
        if source is None:
            continue
        for key in (
            "existing_original",
            "existing_original_part_id",
            "upgrade_only",
            "upgrade_only_user_set",
            "upgrade_only_defaulted",
        ):
            if key in source:
                item[key] = source[key]
        merged += 1
    return merged


def invalidate_successes(items: list[dict], reason: str) -> int:
    """Another account/server or an edited item must not keep a success state."""
    changed = 0
    for item in items:
        if item.get("status") == ITEM_DONE or item.get("drawing_id"):
            item["status"] = ITEM_PENDING
            item["note"] = reason
            for key in ("drawing_id", "class_name", "class_id", "caxa_path", "target"):
                item.pop(key, None)
            changed += 1
    return changed


def invalidate_item(item: dict, reason: str = "条目已修改，需重新上传") -> bool:
    if item.get("status") == ITEM_DONE or item.get("drawing_id"):
        item["status"] = ITEM_PENDING
        item["note"] = reason
        for key in ("drawing_id", "class_name", "class_id", "caxa_path", "target"):
            item.pop(key, None)
        return True
    return False


def rekey_planned_codes(
    items: list[dict],
    relations: list[dict],
    manual_borrows: list[dict],
    mapping: dict[str, str],
) -> int:
    """Follow a normalized drawing code through every persisted plan edge.

    The filename normalizer may replace a code after relations were inferred.
    Leaving those edges on the old code makes the next CAXA precheck fail with
    missing parents or children even though every file is valid.
    """
    changes = {
        drawing_bundle.normalize_code(old): drawing_bundle.normalize_code(new)
        for old, new in (mapping or {}).items()
        if drawing_bundle.normalize_code(old)
        and drawing_bundle.normalize_code(new)
        and drawing_bundle.normalize_code(old) != drawing_bundle.normalize_code(new)
    }
    if not changes:
        return 0

    changed = 0

    def replace(value) -> str:
        code = drawing_bundle.normalize_code(value)
        return changes.get(code, code)

    for item in items:
        for field in ("code", "parent_code", "borrow_to"):
            old = drawing_bundle.normalize_code(item.get(field))
            new = replace(old)
            if old and old != new:
                item[field] = new
                changed += 1

    for relation in relations:
        for field in ("parent_code", "child_code"):
            old = drawing_bundle.normalize_code(relation.get(field))
            new = replace(old)
            if old and old != new:
                relation[field] = new
                changed += 1

    for entry in manual_borrows:
        for field in ("parent_code", "machine"):
            old = drawing_bundle.normalize_code(entry.get(field))
            new = replace(old)
            if old and old != new:
                entry[field] = new
                changed += 1
        codes = []
        for value in entry.get("codes") or ():
            old = drawing_bundle.normalize_code(value)
            new = replace(old)
            if new and new not in codes:
                codes.append(new)
            if old and old != new:
                changed += 1
        if "codes" in entry:
            entry["codes"] = codes

    # A rename can make two inferred edges equivalent.  Keep one canonical
    # edge instead of presenting duplicates to the precheck.
    seen = set()
    canonical = []
    for relation in relations:
        key = (
            drawing_bundle.normalize_code(relation.get("parent_code")),
            drawing_bundle.normalize_code(relation.get("child_code")),
            str(relation.get("link_type") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        canonical.append(relation)
    relations[:] = canonical
    return changed


def normalized_filename_fields(item: dict, file_path: str):
    """Return canonical fields parsed from a file, or a list of blocking errors."""
    code, name, errors = drawing_bundle.parse_drawing_filename(file_path)
    kind, inferred_machine = drawing_bundle.infer_kind(code)
    if kind == "unknown":
        return None, errors or ["无法从文件名确定类型，请双击修正图号"]
    if kind == "component" and not name:
        return None, errors or ["数字开头图纸缺少中文名称"]
    if inferred_machine:
        machine = inferred_machine
    elif kind == "component":
        machine = drawing_bundle.normalize_code(item.get("machine"))
    else:
        machine = ""
    return {
        "code": code,
        "name": "" if drawing_bundle.is_single_letter_process_code(code) else name,
        "kind": kind,
        "machine": machine,
    }, []


def mark_no_write_in_plan(
    items: list[dict],
    relations: list[dict],
    operation: dict,
) -> bool:
    """Make a human-confirmed no-write operation visibly retryable."""
    kind = str(operation.get("kind") or "")
    changed = False
    if kind == "upload":
        code = drawing_bundle.normalize_code(
            operation.get("item_code") or operation.get("key")
        )
        item = next(
            (
                row for row in items
                if drawing_bundle.normalize_code(row.get("code")) == code
            ),
            None,
        )
        if item is not None:
            item["status"] = ITEM_PENDING
            item["note"] = "已确认上次未写入，可重新上传"
            for field in (
                "drawing_id", "class_name", "class_id", "iterator_no",
                "caxa_path", "target",
            ):
                item.pop(field, None)
            changed = True
    elif kind == "relation":
        parent = drawing_bundle.normalize_code(operation.get("parent_code"))
        child = drawing_bundle.normalize_code(operation.get("child_code"))
        for relation in relations:
            if (
                drawing_bundle.normalize_code(relation.get("parent_code")) == parent
                and drawing_bundle.normalize_code(relation.get("child_code")) == child
            ):
                relation["link_status"] = ""
                relation["note"] = "已确认上次未写入，可重新执行"
                changed = True
    return changed


def should_auto_confirm(policy: str) -> str:
    """Auto-confirm is only ever enabled for append_iteration during one request."""
    return "append_iteration" if str(policy or "") == "append_iteration" else ""


def can_start_upload(*, busy: bool, has_bundle: bool, selected: int, settings_ok: bool) -> tuple[bool, str]:
    if busy:
        return False, "当前任务尚未结束，请等待完成"
    if not has_bundle:
        return False, "请先打开上传包或添加 PDF"
    if not selected:
        return False, "没有勾选待上传的图纸"
    if not settings_ok:
        return False, "请先填写完整的 CAXA 账号信息"
    return True, ""


def recovery_kind_label(kind: str | None) -> str:
    text = str(kind or "").strip()
    return _RECOVERY_KIND_LABELS.get(text, "待核实操作")


def _recovery_detail(row: dict) -> dict:
    raw = row.get("detail") or ""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def recovery_phase_label(kind: str | None, phase: str | None) -> str:
    kind_text = str(kind or "").strip()
    phase_text = str(phase or "").strip()
    if phase_text in _RECOVERY_PHASE_LABELS:
        return _RECOVERY_PHASE_LABELS[phase_text]
    if kind_text == "upload":
        return "图纸写入结果确认"
    if kind_text == "relation":
        return "次级/借用关系确认"
    if kind_text == "workflow":
        return "审核打印流程启动结果确认"
    return "操作结果确认"


def recovery_operation_subject(row: dict) -> str:
    """Return the user-facing business object without internal operation ids."""
    kind = str(row.get("kind") or "").strip()
    if kind == "workflow":
        process_id = str(row.get("remote_id") or "").strip()
        return "流程实例 %s" % process_id if process_id else "审核打印流程"
    if kind == "relation":
        parent = drawing_bundle.normalize_code(row.get("parent_code"))
        child = drawing_bundle.normalize_code(row.get("child_code"))
        if parent and child:
            return "%s ← %s" % (parent, child)
    code = drawing_bundle.normalize_code(
        row.get("item_code")
        or row.get("child_code")
        or row.get("parent_code")
    )
    if code:
        return code
    key = str(row.get("key") or "").strip()
    if drawing_bundle.is_valid_code(key):
        return drawing_bundle.normalize_code(key)
    return "未命名业务对象"


def recovery_operation_text(row: dict) -> str:
    """Format one pending operation using business labels and business data."""
    kind = str(row.get("kind") or "").strip()
    lines = [
        "操作：%s" % recovery_kind_label(kind),
        "对象：%s" % recovery_operation_subject(row),
        "阶段：%s" % recovery_phase_label(kind, row.get("phase")),
        "记录状态：%s" % (str(row.get("status") or "").strip() or "待核实"),
    ]
    if kind == "workflow":
        process_id = str(row.get("remote_id") or "").strip()
        lines[1] = "流程实例 ID：%s" % (process_id or "未返回")
        lines[3] = "流程状态：%s" % (
            str(row.get("status") or "").strip() or "待核实"
        )
        detail = _recovery_detail(row)
        document_ids = detail.get("drawing_ids")
        if not isinstance(document_ids, list):
            document_ids = detail.get("linked_document_ids")
        if isinstance(document_ids, list):
            lines.append("关联文档：%d 项" % len(document_ids))
        else:
            lines.append("关联文档：待回读")
    return "\n".join(lines)


def remove_planned_drawings(
    items: list[dict],
    relations: list[dict],
    manual_borrows: list[dict],
    removed_codes,
) -> dict:
    """Remove drawing rows and every edge that would otherwise become dangling.

    This only changes upload-plan data.  Source PDFs and CAXA objects are
    intentionally outside this function's reach.
    """
    removed = {
        drawing_bundle.normalize_code(code)
        for code in removed_codes or ()
        if drawing_bundle.normalize_code(code)
    }
    new_items = [
        item for item in items
        if drawing_bundle.normalize_code(item.get("code")) not in removed
    ]
    new_relations = [
        relation for relation in relations
        if drawing_bundle.normalize_code(relation.get("parent_code")) not in removed
        and drawing_bundle.normalize_code(relation.get("child_code")) not in removed
    ]
    new_manual_borrows: list[dict] = []
    for entry in manual_borrows:
        parent = drawing_bundle.normalize_code(
            entry.get("parent_code") or entry.get("machine")
        )
        if parent in removed:
            continue
        child_codes = [
            child
            for child in entry.get("codes") or ()
            if drawing_bundle.normalize_code(child) not in removed
        ]
        if not child_codes:
            continue
        cleaned = dict(entry)
        cleaned["codes"] = child_codes
        new_manual_borrows.append(cleaned)
    return {
        "items": new_items,
        "relations": new_relations,
        "manual_borrows": new_manual_borrows,
        "removed_codes": removed,
    }


def machine_product_codes(items: list[dict]) -> set[str]:
    """Return 03 product codes still represented by the current upload plan."""
    result: set[str] = set()
    for item in items:
        kind = str(item.get("kind") or "")
        if kind == "machine_drawing":
            code = drawing_bundle.normalize_code(item.get("code"))
        elif kind == "process_doc":
            code = drawing_bundle.normalize_code(item.get("machine"))
        else:
            code = ""
        if code.startswith("03."):
            result.add(code)
    return result


def summary_message(result: dict) -> str:
    """One readable summary: files, relations, workflow - including failures."""
    if not result:
        return "没有结果"
    if result.get("duplicate"):
        workflow = result.get("workflow") or {}
        text = result.get("message") or "相同批次已完成，未重复执行"
        if workflow.get("process_instance_id"):
            text += "；原流程 %s" % workflow["process_instance_id"]
        return text

    lines = []
    uploaded = int(result.get("uploaded") or 0)
    failed = int(result.get("failed") or 0)
    uncertain = int(result.get("uncertain") or 0)
    lines.append("图纸：确认成功 %d 项，失败 %d 项，待核实 %d 项" % (uploaded, failed, uncertain))

    rows = result.get("relations") or []
    if rows:
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.get("status") or "未知"] = counts.get(row.get("status") or "未知", 0) + 1
        lines.append(
            "关系："
            + "，".join("%s %d 条" % (status, count) for status, count in sorted(counts.items()))
        )
        failed_rows = [row for row in rows if row.get("status") not in ("已借用", "已归属", "已存在", "跳过")]
        for row in failed_rows[:6]:
            lines.append(
                "  · %s ← %s：%s %s"
                % (
                    row.get("parent_code"),
                    row.get("child_code"),
                    row.get("status"),
                    row.get("note") or "",
                )
            )
        if len(failed_rows) > 6:
            lines.append("  · 其余 %d 条见日志" % (len(failed_rows) - 6))

    workflow = result.get("workflow")
    if workflow:
        status = workflow.get("status") or ""
        process_id = workflow.get("process_instance_id")
        if process_id:
            lines.append("流程：%s（流程ID %s）" % (status or "已发起", process_id))
        else:
            lines.append("流程：%s %s" % (status, workflow.get("reason") or ""))
    elif result.get("workflow_blocked"):
        lines.append("流程：" + str(result["workflow_blocked"]))

    failures = [item for item in result.get("items") or [] if item.get("note") and item.get("status") in ("失败", "整机不存在", "失败：图纸已发布，无法追加版次", "部分完成", "待核实")]
    for item in failures[:6]:
        lines.append("  · %s：%s %s" % (item.get("code"), item.get("status"), item.get("note")))
    if len(failures) > 6:
        lines.append("  · 其余 %d 项见日志" % (len(failures) - 6))
    return "\n".join(lines)

"""Run a trusted local CAXA uploader after the MCP client has approved a plan."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace


class PlanError(ValueError):
    """A business-facing refusal safe to return through MCP."""


_POLICY_LABELS = {"append_iteration": "升版", "reuse": "复用"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_DRAWING_KINDS = {"component": "组件图纸", "machine_drawing": "整机图纸", "process_doc": "工艺文档"}
_ITEM_FIELDS = ("code", "name", "kind", "machine", "file", "file_path", "sha256", "size")
_EXPECTED_INPUT = frozenset({"manifest", "duplicate_policy", "expected_review_digest"})


def _runtime():
    path = os.environ.get("CAXA_APP_DIR", "").strip()
    if not path:
        raise PlanError("请在本机配置 CAXA_APP_DIR，指向图纸上传助手的 app 目录。")
    app_dir = Path(path).expanduser().resolve()
    if not (app_dir / "upload_core.py").is_file():
        raise PlanError("CAXA_APP_DIR 中找不到上传助手的 Python 运行文件。")

    import config
    import drawing_bundle
    import relations
    import upload_core
    import upload_ui_logic
    from caxa.bridge import HELPER_PATH

    if not Path(HELPER_PATH).is_file():
        raise PlanError("找不到已编译的 CAXA 辅助程序，请先按上传助手的说明安装运行组件。")
    config_path = os.environ.get("CAXA_CONFIG_FILE", "").strip()
    if config_path:
        config_path = Path(config_path).expanduser().resolve()
        if not config_path.is_file():
            raise PlanError("找不到 CAXA_CONFIG_FILE 指定的本机配置。")
        config.CONFIG_PATH = str(config_path)

    return SimpleNamespace(
        config=config, drawing_bundle=drawing_bundle, relations=relations,
        upload_core=upload_core, ui_logic=upload_ui_logic,
    )


def _account(runtime, duplicate_policy):
    cfg = runtime.config.load()
    password = runtime.config.get_password(cfg)
    if cfg.get("caxa_password_enc") and not password:
        raise PlanError("无法读取本机保存的 CAXA 密码，请用当前 Windows 账号重新保存。")
    try:
        settings = runtime.ui_logic.snapshot_settings(
            ip=cfg.get("caxa_server_ip"),
            port=cfg.get("caxa_server_port"),
            user=cfg.get("caxa_user"),
            password=password,
            duplicate_policy=duplicate_policy,
            start_workflow=False,
        )
    except runtime.ui_logic.SettingsError as exc:
        raise PlanError(str(exc)) from None
    return runtime.ui_logic.account_from(settings)


def _plan(runtime, proposal):
    bundle = runtime.drawing_bundle
    relations = runtime.relations
    if (
        not isinstance(proposal, dict)
        or proposal.get("schema") != bundle.SCHEMA
        or proposal.get("schema_version") != bundle.SCHEMA_VERSION
        or not isinstance(proposal.get("batch_id"), str)
        or not proposal["batch_id"].strip()
        or not isinstance(proposal.get("items"), list)
        or not isinstance(proposal.get("relations"), list)
    ):
        raise PlanError("上传计划格式不正确，请从本地复核包重新生成。")

    items = []
    seen = set()
    selected = set()
    for source in proposal["items"]:
        if not isinstance(source, dict):
            raise PlanError("上传清单中的图纸条目格式不正确。")
        item = {key: source.get(key) for key in _ITEM_FIELDS}
        if not all(isinstance(item[key], str) for key in _ITEM_FIELDS if key != "size"):
            raise PlanError("上传清单有图号、文件或指纹字段格式不正确。")
        code = bundle.normalize_code(item["code"])
        if not bundle.is_valid_code(code) or code in seen:
            raise PlanError("上传清单有无效或重复图号。")
        seen.add(code)
        file_path = Path(item["file_path"])
        if not file_path.is_absolute() or file_path.suffix.lower() != ".pdf":
            raise PlanError(f"图纸 {code} 必须使用本机 PDF 的绝对路径。")
        file_hash = item["sha256"].lower()
        if not _DIGEST.fullmatch(file_hash) or type(item["size"]) is not int or item["size"] <= 0:
            raise PlanError(f"图纸 {code} 缺少有效文件指纹。")
        selected_flag = source.get("upload_sel")
        product_name = source.get("series_product_name", "")
        if type(selected_flag) is not bool or not isinstance(product_name, str):
            raise PlanError(f"图纸 {code} 的选择或产品名称格式不正确。")
        item.update(
            code=code, file_path=str(file_path.resolve()), sha256=file_hash,
            upload_sel=selected_flag, status="待人工复核",
        )
        if selected_flag:
            selected.add(code)
            if product_name.strip():
                item["series_product_name"] = product_name.strip()
        items.append(item)
    if not selected:
        raise PlanError("请至少选择一项图纸再预检。")

    edges = []
    pairs = set()
    for row in proposal["relations"]:
        if not isinstance(row, dict):
            raise PlanError("归属或借用关系格式不正确。")
        parent = bundle.normalize_code(row.get("parent_code"))
        child = bundle.normalize_code(row.get("child_code"))
        link_type = row.get("link_type")
        if (
            link_type not in ("own", "borrow")
            or not bundle.is_digit_code(parent)
            or not bundle.is_digit_code(child)
            or child not in selected
            or parent not in selected
            or parent == child
            or (parent, child) in pairs
        ):
            raise PlanError("每条归属或借用关系须使用本次明确选中的上级和子件图号，且不得重复。")
        pairs.add((parent, child))
        edges.append({
            "parent_code": parent, "child_code": child, "link_type": link_type,
            "source": "manual", "manual_override": True,
        })
    issues = relations.analyze_relations(edges)["issues"]
    if issues:
        raise PlanError("\n".join(item["message"] for item in issues))
    return {
        "schema": bundle.SCHEMA,
        "schema_version": bundle.SCHEMA_VERSION,
        "batch_id": proposal["batch_id"].strip(),
        "items": items,
        "relations": edges,
        "assemblies": [],
        "manual_borrows": [],
        relations.MIGRATION_FLAG: relations.MIGRATION_VERSION,
        relations.AUTO_PREFIX_FLAG: True,
    }


def _discard(*_args, **_kwargs):
    pass


def _fields(rows, fields):
    return [{key: row.get(key) for key in fields} for row in rows]


def _review(runtime, plan, account, policy):
    core = runtime.upload_core
    errors = runtime.drawing_bundle.validate_bundle(plan)
    if errors:
        raise PlanError("\n".join(errors))
    preview = core.preview_bundle(plan)
    local_reasons = preview["invalid_details"] + preview["fingerprint_details"]
    local_reasons.extend(row["message"] for row in preview["relation_issues"])
    if not preview["ready"] or local_reasons:
        return None, {}, local_reasons or ["本地图纸预检未通过。"]
    initial_plan = runtime.ui_logic.snapshot_manifest(plan)
    try:
        report = core.precheck_with_caxa(plan, account, log=_discard)
    except Exception:
        raise PlanError("CAXA 预检未完成。请检查本机上传助手的连接配置，必要时用图纸上传助手预检查看详情。") from None

    selected = [item for item in plan["items"] if item["upload_sel"]]
    relations = core.active_relations_for_upload(selected, plan["relations"])
    edges = _fields(relations, ("parent_code", "child_code", "link_type"))
    originals = {row["code"] for row in report["existing_original_items"]}
    drawings = [
        {
            "code": item["code"], "name": item["name"],
            "kind": _DRAWING_KINDS[item["kind"]],
            "machine": item["machine"], "sha256": item["sha256"], "size": item["size"],
            "filename": runtime.drawing_bundle.expected_drawing_filename(item["code"], item["name"]),
            "existing_original": item["code"] in originals,
            "borrow": (
                not item.get("upgrade_only", False)
                if item["code"] in originals else None
            ),
        }
        for item in selected
    ]
    products = _fields(
        report["machine_products"],
        ("code", "product_exists", "product_name", "part_exists",
         "folder", "folder_exists", "requested_name", "requires_product_name"),
    )
    half_products = _fields(
        report["process_parents"],
        ("code", "product_exists", "part_exists", "parent_code", "parent_exists", "part_will_create"),
    )
    counts = {
        "selected": len(selected),
        "unselected": len(plan["items"]) - len(selected),
        "ownership": sum(row["link_type"] == "own" for row in edges),
        "borrow": sum(row["link_type"] == "borrow" for row in edges),
    }
    summary = {
        "drawings": drawings,
        "relationships": edges,
        "products": products,
        "half_products": half_products,
        "duplicate_policy": _POLICY_LABELS[policy],
        "account": {key: account[key] for key in ("ip", "port", "user")},
        "counts": counts,
    }

    reasons = [row["message"] for row in report["issues"]]
    reasons.extend(
        f"图号 {code} 的借用处理缺少借用父级。"
        for code in report["missing_borrow_positions"]
    )
    reasons.extend(
        f"图号 {code} 的借用子件不存在。"
        for code in report["missing_link_children"]
    )
    reasons.extend(
        f"图号 {code} 对应多个原件。"
        for code in report["duplicate_existing_parts"]
    )
    reasons.extend(
        f"整机 {row['code']} 的系列目录不存在：{row['folder']}。"
        for row in report["missing_product_folders"]
    )
    reasons.extend(
        f"整机 {row['code']} 在 CAXA 中没有对应产品，请填写计划产品名称。"
        for row in products if row["requires_product_name"] and not row["requested_name"]
    )
    reasons.extend(
        f"半成品 {row['code']}：{row['blocked_reason']}"
        for row in report["missing_process_parts"]
    )
    if reasons:
        return None, summary, reasons

    # Bind both the submitted and effective plans. Server object IDs and
    # connection settings influence the digest, never the MCP response.
    evidence = {
        "plan": initial_plan,
        "summary": summary,
        "duplicate_policy": policy,
        "products": report["machine_products"],
        "half_products": report["process_parents"],
        "originals": report["existing_original_items"],
    }
    encoded = json.dumps(evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), summary, []


def _blocked(mode, reasons, summary=None):
    if mode == "precheck":
        return {"ready": False, "review_digest": None, "summary": summary or {}, "reasons": reasons}
    return {"ok": False, "status": "blocked", "summary": {}, "reasons": reasons}


def _upload_summary(result):
    uncertain = result["uncertain"]
    relation_pending = result["relation_pending"]
    if result["ok"]:
        status = "completed"
    elif uncertain or relation_pending:
        status = "needs_verification"
    else:
        status = "incomplete"
    return {
        "ok": result["ok"],
        "status": status,
        "summary": {
            "uploaded": result["uploaded"],
            "failed": result["failed"],
            "uncertain": uncertain,
            "relation_failed": result["relation_failures"],
            "relation_pending": relation_pending,
            "drawings": _fields(
                [item for item in result["items"] if item["upload_sel"]],
                ("code", "status"),
            ),
            "relationships": _fields(
                result["relations"], ("parent_code", "child_code", "link_type", "status"),
            ),
        },
        "reasons": [],
    }


def run_caxa(mode: str, payload, runtime=None) -> dict:
    if mode not in ("precheck", "upload"):
        raise ValueError("unsupported runner command")
    if not isinstance(payload, dict) or set(payload) - _EXPECTED_INPUT:
        return _blocked(mode, ["上传计划包含不支持的请求字段。"])
    policy = payload.get("duplicate_policy")
    if not isinstance(policy, str) or policy not in _POLICY_LABELS:
        return _blocked(mode, ["请明确选择重复图号的处理方式。"])
    expected = payload.get("expected_review_digest")
    if mode == "upload" and (not isinstance(expected, str) or not _DIGEST.fullmatch(expected)):
        return _blocked(mode, ["没有可核对的已审核计划，请重新预检和审批。"])
    try:
        runtime = runtime or _runtime()
        plan = _plan(runtime, payload.get("manifest"))
        account = _account(runtime, policy)
        review_digest, summary, reasons = _review(runtime, plan, account, policy)
    except PlanError as exc:
        return _blocked(mode, [str(exc)])
    except Exception:
        return _blocked(mode, ["无法读取本机上传计划或运行配置，请检查文件和上传助手配置。"])

    if not review_digest:
        return _blocked(mode, reasons, summary)
    if mode == "precheck":
        return {"ready": True, "review_digest": review_digest, "summary": summary, "reasons": []}
    if not hmac.compare_digest(expected, review_digest):
        return _blocked(mode, ["图纸、运行账号或实际处理计划自审核后已有变化，请重新预检和审批。"])
    try:
        result = runtime.upload_core.upload_bundle(
            plan, account, on_duplicate=policy, start_workflow=False,
            auto_confirm=runtime.ui_logic.should_auto_confirm(policy), log=_discard,
        )
        return _upload_summary(result)
    except Exception:
        return {
            "ok": False,
            "status": "needs_verification",
            "summary": {},
            "reasons": ["未能确认本次上传结果。请先在图纸上传助手中核实，避免重复上传。"],
        }

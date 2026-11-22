"""Validate a local drawing and relationship proposal for human review."""

from __future__ import annotations

import hashlib
import json

import drawing_bundle
import relations


_RELATION_TYPES = {"own": "own", "borrow": "borrow", "归属": "own", "借用": "borrow"}
_RELATION_NAMES = {"own": "归属", "borrow": "借用"}
_DOCUMENT_TYPES = {"component": "组件图纸", "machine_drawing": "整机图纸", "process_doc": "工艺文档"}
_ITEM_FIELDS = (
    "code", "name", "kind", "machine", "file", "file_path", "sha256", "size",
    "source", "page", "source_pages", "source_sha256",
)


def _build_relationships(relationships: list[dict], selected: set[str]) -> tuple[list[dict], set[str]]:
    proposed = []
    external_parents = set()
    seen_pairs = set()
    for row in relationships:
        parent = drawing_bundle.normalize_code(row.get("parent_code"))
        child = drawing_bundle.normalize_code(row.get("child_code"))
        link_type = _RELATION_TYPES.get(row.get("link_type") or row.get("kind"))
        if not link_type:
            raise ValueError("每条关系必须明确选择归属或借用。")
        if not drawing_bundle.is_digit_code(parent) or not drawing_bundle.is_digit_code(child):
            raise ValueError(f"{parent} → {child} 必须使用零件图号。工艺文档图号不能作为关系端点。")
        if parent == child:
            raise ValueError(f"图纸 {child} 不能挂接到自身。")
        if child not in selected:
            raise ValueError(f"关系子件 {child} 未选入本次上传。")
        if parent not in selected:
            if link_type == "own":
                raise ValueError(f"归属关系的上级 {parent} 须选入本次上传。")
            external_parents.add(parent)
        pair = (parent, child)
        if pair in seen_pairs:
            raise ValueError(f"关系重复或冲突：{parent} → {child}。")
        seen_pairs.add(pair)
        proposed.append({
            "parent_code": parent,
            "child_code": child,
            "link_type": link_type,
            "source": "manual",
            "manual_override": True,
        })
    issues = relations.analyze_relations(proposed)["issues"]
    if issues:
        raise ValueError("\n".join(issue["message"] for issue in issues))
    return proposed, external_parents


def _product_names(names: dict[str, str], selected_items: list[dict]) -> tuple[dict[str, str], set[str]]:
    machines = {item["machine"] for item in selected_items if item.get("machine", "").startswith("03.")}
    products = {}
    for code, raw_name in names.items():
        machine = drawing_bundle.normalize_code(code)
        if machine not in machines:
            raise ValueError(f"产品名必须对应已选图纸的 03 整机图号：{code}")
        name = raw_name.strip()
        if not name:
            raise ValueError(f"整机 {machine} 的产品名称为空。")
        if machine in products:
            raise ValueError(f"整机 {machine} 的产品名称重复填写。")
        products[machine] = name
    return products, machines


def build_review_plan(
    manifest_path: str,
    selected_codes: list[str],
    relationships: list[dict],
    product_names: dict[str, str] | None = None,
) -> tuple[dict, dict]:
    """Return a detached proposal with enough business information to review it."""
    path, original = drawing_bundle.load_bundle(manifest_path)
    batch_id = str(original.get("batch_id") or "").strip()
    if not batch_id:
        raise ValueError("上传包缺少批次标识。请先通过 MCP 生成新的复核包。")

    normalized = [drawing_bundle.normalize_code(code) for code in selected_codes]
    selected = set(normalized)
    if not selected or "" in selected or len(selected) != len(normalized):
        raise ValueError("请明确选择至少一个图号，每个图号只填写一次。")
    items = []
    seen_codes = set()
    for row in original["items"]:
        code = drawing_bundle.normalize_code(row.get("code"))
        if code in seen_codes:
            raise ValueError(f"上传包内有重复图号：{code}")
        seen_codes.add(code)
        item = {key: row[key] for key in _ITEM_FIELDS if key in row}
        item.update(code=code, upload_sel=code in selected, status="待人工复核")
        items.append(item)
    unknown = sorted(selected - seen_codes)
    if unknown:
        raise ValueError(f"以下图号不在上传包中：{'、'.join(unknown)}")
    selected_items = [item for item in items if item["upload_sel"]]
    proposed, external_parents = _build_relationships(relationships, selected)
    products, machines = _product_names(product_names or {}, selected_items)
    for item in selected_items:
        machine = item.get("machine")
        if machine in products:
            item["series_product_name"] = products[machine]

    warnings = list(original.get("warnings") or [])
    if external_parents:
        warnings.append("以下借用上级不在本次上传清单，需在 CAXA 预检中核实：" + "、".join(sorted(external_parents)))
    manifest = {
        "schema": drawing_bundle.SCHEMA,
        "schema_version": drawing_bundle.SCHEMA_VERSION,
        "batch_id": batch_id,
        "source_files": list(original.get("source_files") or []),
        "items": items,
        "assemblies": [],
        "manual_borrows": [],
        "relations": proposed,
        relations.MIGRATION_FLAG: relations.MIGRATION_VERSION,
        relations.AUTO_PREFIX_FLAG: True,
        "warnings": warnings,
    }
    problems = drawing_bundle.validate_bundle(manifest, base_dir=path.parent)
    if problems:
        raise ValueError("\n".join(problems))

    review_bytes = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    summary = {
        "manifest_path": str(path),
        "batch_id": batch_id,
        "review_digest": hashlib.sha256(review_bytes).hexdigest(),
        "caxa_preflight_required": True,
        "selected_drawings": [
            {
                "drawing_code": item["code"],
                "drawing_name": item.get("name", ""),
                "document_type": _DOCUMENT_TYPES[item["kind"]],
                "machine_code": item.get("machine", ""),
                "filename": drawing_bundle.expected_drawing_filename(item["code"], item.get("name", "")),
                "source": item.get("source", ""),
                "source_pages": item.get("source_pages", []),
                "sha256": item["sha256"],
            }
            for item in selected_items
        ],
        "unselected_count": len(items) - len(selected_items),
        "relationships": [
            {"parent_code": row["parent_code"], "child_code": row["child_code"],
             "relationship": _RELATION_NAMES[row["link_type"]]}
            for row in proposed
        ],
        "series_products": [
            {"machine_code": machine, "product_name": products.get(machine, ""),
             "series_folder": drawing_bundle.series_product_folder(machine)}
            for machine in sorted(machines)
        ],
        "external_parent_codes": sorted(external_parents),
        "warnings": warnings,
    }
    return manifest, summary

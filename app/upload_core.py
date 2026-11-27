"""CAXA-only upload orchestration used by the distributable uploader.

One journal task = one content batch (server + login + the exact PDFs).  The
phases (upload / classify+relate / workflow) are planned separately, so adding
a relation or sending the workflow later never re-uploads confirmed PDFs.

Order of operations is deliberate:

1. validate the rows that will be executed (content hash first);
2. find/restore the task and refuse to continue while any operation is
   uncertain (orphan running rows included);
3. take the single-run lock;
4. classify own/borrow from the live tree (or restore recorded decisions) and
   validate the *effective* graph before any write;
5. persist those decisions;
6. upload only unconfirmed items; then execute only unconfirmed relations;
7. start the workflow only when every required upload/relation is confirmed.
"""

from __future__ import annotations

import hashlib
import json
import os

import drawing_bundle
import journal as journal_module
import relations as relations_module
import staging as staging_module
from upload_ui_logic import recovery_kind_label, recovery_operation_subject
from caxa.bridge import CaxaBridge, CaxaError
from caxa.uploader import (
    CLASS_ID_CAPP,
    CLASS_ID_DRAWING,
    CaxaUploader,
    MachineMissing,
    PublishedDrawingError,
    UnverifiedResult,
    own_part_parent_ids,
)

ITEM_PENDING = "待上传"
ITEM_DONE = "已上传"
ITEM_FAILED = "失败"
ITEM_UNCERTAIN = "待核实"
ITEM_PARTIAL = "部分完成"
ITEM_SKIPPED = "已跳过"

WORKFLOW_STARTED = "已发起"
WORKFLOW_PENDING = "待核实"
WORKFLOW_BLOCKED = "已阻止"
WORKFLOW_FAILED = "失败"


class PendingVerificationError(CaxaError):
    """A previous attempt has an unknown outcome; automatic replay is blocked."""

    def __init__(self, message, pending=None):
        super().__init__(message, stage="pending_verification", response_lost=True)
        self.pending = pending or []


def _is_executed(item: dict) -> bool:
    if not item.get("upload_sel", True):
        return False
    return str(item.get("status") or "") not in (ITEM_SKIPPED,)


def _selected_items(manifest: dict) -> list[dict]:
    return [item for item in manifest.get("items") or [] if isinstance(item, dict) and _is_executed(item)]


def collect_machine_targets(items: list[dict]) -> list[dict]:
    """Collect deduplicated 02/03 process-document parents.

    A 03 target can come from either the whole-machine drawing or any J03/M03/
    P03/T03/Z03 process document.  A 02 target can only be an existing half-
    product Part referenced by its process document.  The first whole-machine
    drawing is preferred as the source of the upload-plan product name/folder;
    if it has no name, the first named process document is used.  The chosen
    name/folder is copied to every item aimed at the same 03 machine so upload
    order cannot change the result.
    """
    groups: dict[str, dict] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        code = drawing_bundle.normalize_code(item.get("code"))
        machine = drawing_bundle.normalize_code(item.get("machine"))
        if kind == "machine_drawing" and (machine or code).startswith("03."):
            machine = machine or code
            priority = 0
        elif kind == "process_doc" and machine.startswith(("02.", "03.")):
            priority = 1
        else:
            continue
        entry = groups.setdefault(
            machine,
            {
                "machine": machine,
                "family": machine[:2],
                "members": [],
            },
        )
        entry["members"].append((priority, index, item))

    targets: list[dict] = []
    for machine, entry in groups.items():
        members = sorted(entry["members"], key=lambda row: (row[0], row[1]))
        plan_items = [row[2] for row in members]
        target = {
            "machine": machine,
            "family": entry["family"],
            "members": plan_items,
            "item_codes": [
                drawing_bundle.normalize_code(item.get("code"))
                for item in plan_items
            ],
            "has_machine_drawing": any(row[0] == 0 for row in members),
        }
        if entry["family"] == "03":
            requested_name = next(
                (
                    str(item.get("series_product_name") or "").strip()
                    for item in plan_items
                    if str(item.get("series_product_name") or "").strip()
                ),
                "",
            )
            requested_folder = next(
                (
                    str(item.get("series_product_folder") or "").strip()
                    for item in plan_items
                    if str(item.get("series_product_folder") or "").strip()
                ),
                "",
            ) or drawing_bundle.series_product_folder(machine)
            target["series_product_name"] = requested_name
            target["series_product_folder"] = requested_folder
            for item in plan_items:
                if requested_name:
                    item["series_product_name"] = requested_name
                item["series_product_folder"] = requested_folder
        else:
            target["series_product_name"] = ""
            target["series_product_folder"] = ""
        targets.append(target)
    return targets


def find_machine_product(
    uploader: CaxaUploader,
    machine: str,
    product_name: str = "",
    product_folder: str = "",
):
    """Resolve a 03 Product with the same identity/name rules as uploads."""
    resolver = getattr(uploader, "find_product_for_machine", None)
    if callable(resolver):
        return resolver(
            machine,
            product_name=product_name,
            product_folder=product_folder,
        )
    return uploader.find_product(machine)


def resolve_series_product_folder(
    uploader: CaxaUploader,
    machine: str,
    requested_folder: str = "",
) -> str:
    """Resolve a 03 series folder from the live helper index when available."""
    resolver = getattr(uploader, "resolve_series_product_folder", None)
    if not callable(resolver):
        return str(requested_folder or "").strip()
    result = resolver(machine, requested_folder=requested_folder)
    return str((result or {}).get("path") or "").strip()


def find_existing_machine_part(uploader: CaxaUploader, machine: str):
    """Return a unique existing 03 machine Part without needing its Product."""
    resolver = getattr(uploader, "existing_machine_part", None)
    if callable(resolver):
        return resolver(machine)
    finder = getattr(uploader, "find_part_candidates", None)
    if not callable(finder):
        return uploader.find_part(machine)
    candidates = finder(machine)
    if len(candidates) > 1:
        raise CaxaError(
            "整机图号 %s 对应 %d 个 Part，请先人工清理重复零件"
            % (machine, len(candidates)),
            stage="machine_part_duplicate",
        )
    return dict(candidates[0]) if candidates else None


def _upload_order(items: list[dict], relations: list[dict] | None = None) -> list[dict]:
    """Place a PDF that is a parent of another in-batch PDF first."""
    remaining = list(items)
    ordered = []
    rel_parents: dict[str, set[str]] = {}
    for relation in relations or []:
        parent = drawing_bundle.normalize_code(relation.get("parent_code"))
        child = drawing_bundle.normalize_code(relation.get("child_code"))
        if parent and child:
            rel_parents.setdefault(child, set()).add(parent)
    while remaining:
        pending_codes = {drawing_bundle.normalize_code(item.get("code")) for item in remaining}
        ready = []
        for item in remaining:
            code = drawing_bundle.normalize_code(item.get("code"))
            parents = set(rel_parents.get(code, set()))
            legacy_parent = drawing_bundle.normalize_code(item.get("parent_code"))
            if legacy_parent:
                parents.add(legacy_parent)
            if not (parents & pending_codes) or all(parent == code for parent in parents):
                ready.append(item)
        if not ready:
            ordered.extend(remaining)
            break
        ordered.extend(ready)
        ready_ids = {id(item) for item in ready}
        remaining = [item for item in remaining if id(item) not in ready_ids]
    return ordered


def find_person_id(bridge: CaxaBridge, name: str) -> int:
    result = bridge.call(
        {
            "cmd": "query_objects",
            "class_id": 1008,
            "limit": 20,
            "where_property": "name",
            "where_value": name,
            "where_operator": "equal",
            "properties": ["name"],
        },
        timeout=120,
    )
    items = [item for item in result.get("items") or [] if (item.get("name") or "") == name]
    if len(items) != 1:
        raise CaxaError("人员 %s 精确匹配数为 %d" % (name, len(items)))
    return int(items[0]["object_id"])


def prepare_device_plm_com_registration(
    bridge: CaxaBridge,
    journal: journal_module.TaskJournal,
    *,
    log=print,
) -> dict | None:
    """Register PlmComApi once per physical device."""
    existing = journal.plm_com_registration()
    force = os.environ.get("CAXA_FORCE_PLM_REGISTRATION", "").strip() == "1"
    if existing is not None and not force:
        log(
            "本设备已注册：%s"
            % existing.get("registration_id")
        )
        return existing
    if not hasattr(bridge, "ensure_plm_com_registration"):
        # Test doubles and older bridge implementations may not expose the
        # device-registration command.
        return existing
    log("本设备首次上传：注册 32 位 CAXA PlmComApi（需要时会弹出 UAC）")
    prepared = bridge.ensure_plm_com_registration()
    plm_com = dict(prepared.get("plm_com") or {})
    registration_id = "PLM-%s-%s" % (
        journal.device_id[-8:],
        os.urandom(4).hex().upper(),
    )
    saved = journal.save_plm_com_registration(
        registration_id,
        dll_path=str(plm_com.get("dll") or ""),
        exit_code=plm_com.get("exit_code"),
        detail=plm_com,
    )
    log(
        "本设备注册完成：%s；后续上传直接登录 CAXA"
        % registration_id
    )
    return saved


def refine_relations_from_tree(
    uploader: CaxaUploader,
    relations: list[dict],
    part_exists_cache: dict[str, bool] | None = None,
    log=print,
    *,
    force: bool = False,
    existing_original_codes: set[str] | None = None,
) -> dict[str, bool]:
    """Decide ownership versus borrow from the drawing-code relationship.

    A code-prefix parent or a same ``01.x`` series parent is the single
    ownership edge (Reference=False).  A ``03.*`` machine or another
    cross-series parent is a borrow edge (Reference=True), regardless of
    whether the child part already exists.
    The live tree is still queried for display/provenance, not for deciding
    the relation type.  Manual choices win.
    """
    cache = part_exists_cache if part_exists_cache is not None else {}
    existing_codes = {
        drawing_bundle.normalize_code(code)
        for code in (existing_original_codes or set())
        if drawing_bundle.normalize_code(code)
    }
    relation_reader = getattr(uploader, "existing_relation_type", None)
    for rel in relations:
        parent = drawing_bundle.normalize_code(rel.get("parent_code"))
        child = drawing_bundle.normalize_code(rel.get("child_code"))
        if not parent or not child:
            continue
        if child in existing_codes:
            rel["tree_exists"] = True
            rel["existing_original"] = True
            actual_type = ""
            if callable(relation_reader):
                try:
                    actual_type = relation_reader(parent, child)
                except CaxaError as exc:
                    log(
                        "关系预检：无法读取 %s ← %s 的现有关系：%s"
                        % (parent, child, exc)
                    )
            if actual_type in ("own", "borrow"):
                rel["link_type"] = actual_type
                rel["existing_relation"] = True
                rel["existing_relation_type"] = actual_type
                rel["auto_decided"] = True
                log(
                    "关系预检：%s ← %s 已有%s关系，保持现状"
                    % (
                        parent,
                        child,
                        "归属" if actual_type == "own" else "借用",
                    )
                )
                continue
            if rel.get("link_type") != "borrow":
                rel["link_type"] = "borrow"
                log(
                    "关系预检：%s 已在 CAXA 中作为原件存在，%s ← %s 强制改为借用"
                    % (child, parent, child)
                )
            rel["auto_decided"] = True
            continue
        if rel.get("manual_override"):
            if rel.get("link_only") and child not in cache:
                cache[child] = uploader.find_part(child) is not None
                rel["tree_exists"] = cache[child]
            continue
        if rel.get("auto_decided") and not force:
            continue
        new_type = relations_module.default_link_type(parent, child)
        if rel.get("link_type") != new_type:
            rel["link_type"] = new_type
            log(
                "关系预检：%s ← %s 判定为【%s】"
                % (
                    parent,
                    child,
                    "借用" if new_type == "borrow" else "归属子件",
                )
            )
        if rel.get("link_only") and child not in cache:
            cache[child] = uploader.find_part(child) is not None
            rel["tree_exists"] = cache[child]
        rel["auto_decided"] = True
    return cache


def _part_candidates_for_code(uploader, code: str) -> list[dict]:
    finder = getattr(uploader, "find_part_candidates", None)
    if callable(finder):
        candidates = list(finder(code) or [])
        if candidates:
            return candidates
    part = uploader.find_part(code)
    return [part] if part is not None else []


def half_product_target_state(uploader, machine: str) -> dict:
    """Describe whether a ``02.x.*`` target may be reused or created."""
    machine = drawing_bundle.normalize_code(machine)
    group = drawing_bundle.half_product_group_code(machine)
    product = uploader.find_product("02")
    candidates = _part_candidates_for_code(uploader, machine)
    part = candidates[0] if len(candidates) == 1 else None
    parent_code = ""
    parent_candidates = []
    for code in drawing_bundle.half_product_parent_codes(machine):
        found_parents = _part_candidates_for_code(uploader, code)
        if found_parents:
            parent_code = code
            parent_candidates = found_parents
            break
    product_link_count = 0
    own_link_count = 0
    own_parent_matches = False
    if part is not None and product is not None:
        reader = getattr(uploader, "part_info", None)
        if callable(reader):
            try:
                info = reader(int(part.get("object_id") or part.get("part_id")))
                product_link_count = sum(
                    1
                    for row in info.get("products") or []
                    if int(row.get("product_id") or 0) == int(product["object_id"])
                )
                target_own_parents = [
                    row for row in info.get("parents") or []
                    if not bool(row.get("reference"))
                ]
                own_parent_ids = own_part_parent_ids(
                    int(part.get("object_id") or part.get("part_id")),
                    info,
                )
                own_products = [
                    row for row in info.get("products") or []
                    if not bool(row.get("reference"))
                ]
                own_parent_count = max(
                    len(target_own_parents),
                    len(own_parent_ids),
                )
                own_link_count = own_parent_count + len(own_products)
                parent_link_matches = False
                if parent_candidates and callable(reader):
                    parent_id = int(
                        parent_candidates[0].get("object_id")
                        or parent_candidates[0].get("part_id")
                        or 0
                    )
                    parent_info = reader(parent_id)
                    for link in parent_info.get("link_endpoints") or []:
                        if bool(link.get("reference")):
                            continue
                        role_a = link.get("roleA") or {}
                        role_b = link.get("roleB") or {}
                        ids = {
                            int(role_a.get("object_id") or 0),
                            int(role_b.get("object_id") or 0),
                        }
                        if parent_id in ids and int(part.get("object_id") or part.get("part_id")) in ids:
                            parent_link_matches = True
                            break
                own_parent_matches = bool(
                    parent_candidates
                    and (
                        parent_link_matches
                        or int(
                            parent_candidates[0].get("object_id")
                            or parent_candidates[0].get("part_id")
                            or 0
                        ) in own_parent_ids
                    )
                )
            except Exception:
                product_link_count = -1
                own_link_count = -1
    part_will_create = (
        product is not None
        and not candidates
        and group != machine
        and len(parent_candidates) == 1
    )
    blocked_reason = ""
    if product is None:
        blocked_reason = "产品「02 半成品」不存在"
    elif len(candidates) > 1:
        blocked_reason = "目标半成品存在多个原件"
    elif len(parent_candidates) > 1:
        blocked_reason = "02 半成品上级 %s 存在多个原件" % parent_code
    elif own_link_count > 1:
        blocked_reason = "目标半成品存在多个非借用归属关系"
    elif part is not None and group != machine and not parent_code:
        blocked_reason = "目标半成品找不到已存在的点号上级"
    elif (
        part is not None
        and parent_code
        and own_link_count == 1
        and not own_parent_matches
    ):
        blocked_reason = "目标半成品的归属上级不是最近的 %s" % parent_code
    elif part is None and group != machine and not parent_candidates:
        blocked_reason = (
            "目标半成品找不到已存在的点号上级（%s 分组必须存在）" % group
        )
    elif part is None and not part_will_create:
        blocked_reason = "02 半成品目标不能自动建立"
    return {
        "code": machine,
        "family": "02",
        "product_exists": product is not None,
        "product_id": (product or {}).get("object_id"),
        "part_exists": part is not None,
        "part_id": (part or {}).get("object_id"),
        "product_link_count": product_link_count,
        "own_link_count": own_link_count,
        "part_candidate_count": len(candidates),
        "group_code": group,
        "parent_code": parent_code,
        "parent_exists": bool(parent_candidates),
        "parent_candidate_count": len(parent_candidates),
        "part_will_create": part_will_create,
        "blocked_reason": blocked_reason,
    }


def discover_existing_original_parts(
    uploader: CaxaUploader,
    items: list[dict],
    *,
    duplicate_codes: list[str] | None = None,
) -> dict[str, dict]:
    """Return selected component codes that already exist as CAXA Parts."""
    found: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") != "component":
            continue
        code = drawing_bundle.normalize_code(item.get("code"))
        if not code:
            continue
        if hasattr(uploader, "find_part_candidates"):
            candidates = uploader.find_part_candidates(code)
        else:
            part = uploader.find_part(code)
            candidates = [part] if part is not None else []
        if len(candidates) > 1 and duplicate_codes is not None:
            duplicate_codes.append(code)
        if candidates:
            found[code] = dict(candidates[0])
    return found


def apply_existing_original_rules(
    items: list[dict],
    relations: list[dict],
    existing_parts: dict[str, dict],
) -> dict:
    """Persist existing-original defaults and validate borrow positions.

    ``upgrade_only`` is a user-editable plan flag.  It is initialized from
    whether the plan already contains a relation for that code:

    * no configured relation -> only upgrade, no borrow;
    * configured relation -> upgrade first, then borrow to that position.
    """
    child_relations: dict[str, list[dict]] = {}
    existing_relation_by_child: dict[str, dict] = {}
    for relation in relations:
        child = drawing_bundle.normalize_code(relation.get("child_code"))
        if child:
            child_relations.setdefault(child, []).append(relation)
            if relation.get("existing_relation"):
                existing_relation_by_child[child] = relation

    existing_codes = {
        drawing_bundle.normalize_code(code)
        for code, part in (existing_parts or {}).items()
        if code and part
    }
    only_upgrade = 0
    borrow_after_upgrade = 0
    missing_positions = []
    borrow_conversions = []
    for item in items:
        code = drawing_bundle.normalize_code(item.get("code"))
        if code not in existing_codes:
            item["existing_original"] = False
            item.pop("existing_relation", None)
            item.pop("existing_relation_type", None)
            item.pop("existing_original_part_id", None)
            item.pop("upgrade_only", None)
            continue
        item["existing_original"] = True
        item["existing_original_part_id"] = (
            existing_parts.get(code, {}).get("object_id")
            or existing_parts.get(code, {}).get("part_id")
        )
        existing_relation = existing_relation_by_child.get(code)
        if existing_relation:
            item["existing_relation"] = True
            item["existing_relation_type"] = (
                existing_relation.get("existing_relation_type") or "own"
            )
        else:
            item.pop("existing_relation", None)
            item.pop("existing_relation_type", None)
        if item.get("upgrade_only_user_set"):
            pass
        elif "upgrade_only" in item and not item.get("upgrade_only_defaulted"):
            item["upgrade_only_user_set"] = True
        else:
            item["upgrade_only"] = bool(
                existing_relation or code not in child_relations
            )
            item["upgrade_only_defaulted"] = True
        if item.get("upgrade_only"):
            only_upgrade += 1
            continue
        if not child_relations.get(code):
            missing_positions.append(code)
        else:
            borrow_after_upgrade += 1
            if not existing_relation:
                borrow_conversions.append(code)
    return {
        "existing_codes": existing_codes,
        "only_upgrade_count": only_upgrade,
        "borrow_after_upgrade_count": borrow_after_upgrade,
        "existing_relation_codes": sorted(existing_relation_by_child),
        "borrow_conversion_codes": borrow_conversions,
        "missing_borrow_positions": missing_positions,
    }


def active_relations_for_upload(
    items: list[dict], relations: list[dict]
) -> list[dict]:
    """Suppress every relation for items marked only-upgrade."""
    upgrade_only = {
        drawing_bundle.normalize_code(item.get("code"))
        for item in items
        if item.get("upgrade_only")
    }
    return [
        relation
        for relation in relations
        if drawing_bundle.normalize_code(relation.get("child_code"))
        not in upgrade_only
    ]


# ---------------------------------------------------------------------------
# plan identity
# ---------------------------------------------------------------------------


def _item_plan_entry(item: dict) -> dict:
    """Fields that define *content*; status/selection/workflow are not part."""
    return {
        "code": drawing_bundle.normalize_code(item.get("code")),
        "name": str(item.get("name") or ""),
        "kind": str(item.get("kind") or ""),
        "machine": drawing_bundle.normalize_code(item.get("machine")),
        "series_product_name": str(item.get("series_product_name") or "").strip(),
        "series_product_folder": str(item.get("series_product_folder") or "").strip(),
        "sha256": str(item.get("sha256") or ""),
        "size": item.get("size"),
    }


def content_digest(manifest: dict, account: dict) -> str:
    batch_id = str(manifest.get("batch_id") or "").strip()
    payload = {
        "server": str(account.get("ip") or ""),
        "port": int(account.get("port") or 0),
        "user": str(account.get("user") or ""),
    }
    if batch_id:
        # A GUI upload list keeps one stable identity while the user adds,
        # removes, or re-selects rows.  Per-item content changes are detected
        # against the confirmed upload evidence below.
        payload["batch_id"] = batch_id
    else:
        # Backwards compatibility for callers that do not provide a batch id.
        selected = _selected_items(manifest)
        payload["items"] = sorted(
            (_item_plan_entry(item) for item in selected),
            key=lambda entry: (entry["code"], entry["sha256"]),
        )
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_digest(manifest: dict, account: dict, **_ignored) -> str:
    """Backwards-compatible name: the batch identity is the content only."""
    return content_digest(manifest, account)


def _upload_record_matches_item(record: dict, item: dict) -> bool:
    """Compare a confirmed upload with the current item content.

    New records include the source SHA-256 in their JSON detail.  Legacy
    records without a hash remain compatible and are treated as matching.
    """
    raw = record.get("detail") or ""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return True
    if not isinstance(payload, dict) or not payload.get("sha256"):
        return True
    expected = str(item.get("sha256") or "").strip().lower()
    recorded = str(payload.get("sha256") or "").strip().lower()
    return bool(expected and recorded and expected == recorded)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _result_row(item: dict, status: str, detail: str = "", **extra) -> dict:
    row = {
        "code": drawing_bundle.normalize_code(item.get("code")),
        "name": str(item.get("name") or ""),
        "file": os.path.basename(str(item.get("file_path") or "")),
        "status": status,
        "detail": detail,
    }
    row.update(extra)
    return row


def _record_value(record: dict | None, key: str, default=""):
    if not record:
        return default
    value = record.get(key)
    return default if value is None else value


def _workflow_class_id(record: dict | None) -> int | None:
    if not record:
        return None
    explicit_class = str(record.get("class_id") or "").strip()
    if explicit_class:
        try:
            class_id = int(explicit_class)
        except (TypeError, ValueError):
            return None
        return class_id if class_id in (CLASS_ID_DRAWING, CLASS_ID_CAPP) else None
    name = "".join(ch for ch in str(record.get("class_name") or "").lower() if ch.isalnum())
    if name == "drawing":
        return CLASS_ID_DRAWING
    if name == "cappdocument":
        return CLASS_ID_CAPP
    return None


def _confirmed_workflow_documents(
    confirmed: dict[str, dict], codes: set[str]
) -> list[dict]:
    documents: dict[int, dict] = {}
    for code, record in (confirmed or {}).items():
        if code not in codes:
            continue
        class_id = _workflow_class_id(record)
        if class_id is None:
            continue
        remote = record.get("remote_id")
        try:
            object_id = int(remote)
        except (TypeError, ValueError):
            continue
        if object_id <= 0:
            continue
        documents[object_id] = {
            "object_id": object_id,
            "class_id": class_id,
            "class_name": (
                "CappDocument" if class_id == CLASS_ID_CAPP else "Drawing"
            ),
        }
    return [documents[object_id] for object_id in sorted(documents)]


def _require_document_evidence(journal, task_id, confirmed, codes):
    """Keep incomplete successful upload evidence recoverable, never omit it."""
    invalid_confirmations = []
    for code in codes & confirmed.keys():
        record = confirmed[code]
        try:
            valid_id = int(record.get("remote_id") or 0) > 0
        except (TypeError, ValueError):
            valid_id = False
        class_id = _workflow_class_id(record)
        kind, _machine = drawing_bundle.infer_kind(code)
        expected_class = CLASS_ID_CAPP if kind == "process_doc" else CLASS_ID_DRAWING
        if valid_id and class_id == expected_class:
            continue
        journal.record_operation(
            task_id, "upload", code, status=journal_module.OP_PENDING,
            phase="upload:evidence", item_code=code,
            remote_id=record.get("remote_id") or "",
            class_id=record.get("class_id") or "",
            class_name=record.get("class_name") or "",
            iterator_no=record.get("iterator_no") or "",
            detail=record.get("detail") or "",
            error="已完成记录中的文档资料缺失或类型不符，请重新核实",
        )
        invalid_confirmations.append(journal.get_operation(task_id, "upload", code))
    if invalid_confirmations:
        raise _pending_error(invalid_confirmations)


def _require_workflow_evidence(journal, task_id, record):
    """Reject legacy manual confirmations without evidence of a real start."""
    if not record or record["status"] != journal_module.OP_OK:
        return
    try:
        payload = json.loads(record.get("detail") or "{}")
        if not isinstance(payload, dict):
            payload = {}
        documents = payload.get("drawing_ids")
        valid = (int(record.get("remote_id") or 0) > 0
                 and payload.get("started") is True
                 and isinstance(documents, list) and bool(documents)
                 and all(int(value) > 0 for value in documents))
        # These fields are optional in genuine old successful records, but an
        # explicit contrary observation is not evidence of a successful start.
        if "process_state" in payload:
            valid = valid and str(payload["process_state"]) == "2"
    except (TypeError, ValueError):
        valid = False
    if valid:
        return
    key = record["key"]
    journal.record_operation(
        task_id, "workflow", key, status=journal_module.OP_PENDING,
        phase=record.get("phase") or "workflow",
        remote_id=record.get("remote_id") or "",
        class_name=record.get("class_name") or "workflow",
        detail=record.get("detail") or "",
        error=("历史流程成功证据不完整，请人工核实真实流程及关联文档清单；"
               "原计划集合缺失时须重建证据，不能仅因日志缺失确认未写入"),
    )
    raise _pending_error([journal.get_operation(task_id, "workflow", key)])


def _workflow_from_record(record: dict) -> dict:
    """Restore the workflow result from the stored JSON detail."""
    payload = {}
    detail = record.get("detail") or ""
    try:
        payload = json.loads(detail) if detail else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    drawing_ids = []
    for value in payload.get("drawing_ids") or []:
        try:
            drawing_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return {
        "status": WORKFLOW_STARTED,
        "process_instance_id": record.get("remote_id") or None,
        "drawing_ids": drawing_ids,
        "started": bool(payload.get("started", True)),
        "restored": True,
    }


def _workflow_key(records: list[dict], document_ids: list[int]) -> str:
    if not records:
        return "workflow"
    document_set = set(document_ids)
    for row in records:
        if row["status"] == journal_module.OP_FAILED:
            try:
                recorded = json.loads(row["detail"])["drawing_ids"]
                if set(int(value) for value in recorded) == document_set:
                    return row["key"]
            except (KeyError, TypeError, ValueError):
                continue
    encoded = ",".join(str(value) for value in sorted(document_set))
    return "workflow-" + hashlib.sha256(encoded.encode("ascii")).hexdigest()


def content_lock_key(digest: str, account: dict) -> str:
    """One lock per batch content + login, covering lookup/create/writes."""
    seed = "%s|%s|%s|%s" % (
        digest,
        account.get("ip") or "",
        int(account.get("port") or 0),
        account.get("user") or "",
    )
    return "content-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _pending_error(pending: list[dict]) -> PendingVerificationError:
    operations = []
    for row in pending[:8]:
        label = recovery_kind_label(row.get("kind"))
        subject = recovery_operation_subject(row)
        name = label if subject == label else "%s %s" % (label, subject)
        operations.append("%s（%s）" % (name, row["status"]))
    return PendingVerificationError(
        "本批次还有待核实或中断的操作，请先核实这些结果：%s" % "；".join(operations),
        pending=pending,
    )


def _phase_blockers(failures, pending_codes, relation_failures, relation_pending,
                    drawing_ids) -> list[str]:
    blockers = []
    if failures:
        blockers.append("%d 项图纸上传失败" % len(failures))
    if pending_codes:
        blockers.append("%d 项上传结果待核实" % len(pending_codes))
    if relation_failures:
        blockers.append("%d 条必需关系未成功" % relation_failures)
    if relation_pending:
        blockers.append("%d 条关系结果待核实" % relation_pending)
    if not drawing_ids:
        blockers.append("没有可关联的已确认图纸或工艺文档")
    return blockers


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


def upload_bundle(manifest: dict, account: dict, *, on_duplicate: str = "append_iteration",
                  borrow_missing: bool = True, start_workflow: bool = False,
                  workflow_only: bool = False,
                  workflow_template_id: int = 1,
                  workflow_template_instance_id: int = 31,
                  workflow_process_def_id: str = "2",
                  workflow_activity_def_id: str = "4",
                  performer_name: str = "",
                  log=print, progress=None, journal=None, journal_path: str | None = None,
                  force_new_task: bool = False, auto_confirm: str = "",
                  staging_root: str | None = None, recheck_tree: bool = False,
                  bundle_path: str = "") -> dict:
    items = list(manifest.get("items") or [])
    relations = drawing_bundle.normalize_manifest_relations(manifest, log=log)
    selected = _selected_items(manifest)
    selected_codes = {drawing_bundle.normalize_code(item.get("code")) for item in selected}
    machine_targets = collect_machine_targets(selected)

    # 1) validate only the rows that will be executed (structure is checked by
    #    the bundle validator for every row).
    if workflow_only and not selected:
        raise CaxaError("请选中已上传成功的图纸后再发起流程", stage="workflow_only")
    for item in selected:
        problems = drawing_bundle.item_problems(
            item, check_content=not workflow_only
        )
        if problems:
            raise CaxaError(
                "第 %s 项校验失败：%s" % (item.get("code") or "?", "；".join(problems)),
                stage="plan_validate",
            )
    # A follow-up workflow uses the persisted upload evidence.  Local PDFs may
    # already have been moved, so only validate the business plan in that mode.
    bundle_errors = [] if workflow_only else drawing_bundle.validate_bundle(manifest)
    if bundle_errors:
        raise CaxaError(
            "上传包校验失败：\n" + "\n".join(bundle_errors[:20]), stage="plan_validate"
        )

    digest = content_digest(manifest, account)
    owns_journal = journal is None
    journal = journal or journal_module.TaskJournal(journal_path)
    bridge = None
    uploader = None
    task = None
    lock = None
    content_lock = None
    device_registration = journal.plm_com_registration()
    try:
        # Single-run guard must cover lookup/create as well as the writes,
        # otherwise two processes can create two tasks for the same batch.
        try:
            content_lock = journal.run_lock(content_lock_key(digest, account))
            content_lock.__enter__()
        except journal_module.JournalBusy as exc:
            raise PendingVerificationError(str(exc)) from exc
        existing = journal.find_task(
            digest=digest,
            server=str(account.get("ip") or ""),
            port=int(account.get("port") or 0),
            user=str(account.get("user") or ""),
        )
        # Any attempt of this batch with an unknown outcome blocks replay,
        # including older attempts and even an explicit "new task".
        for previous in journal.tasks_for_digest(
            digest=digest,
            server=str(account.get("ip") or ""),
            port=int(account.get("port") or 0),
            user=str(account.get("user") or ""),
        ):
            pending = journal.pending_operations(previous["task_id"])
            if pending:
                raise _pending_error(pending)
        if existing and not force_new_task:
            task = existing
            log("继续本批次任务：%s（状态 %s）" % (task["task_id"], task["status"]))
        else:
            if workflow_only:
                raise CaxaError(
                    "没有找到这批图纸的上传任务，请先完成上传再发起流程",
                    stage="workflow_only",
                )
            task = journal.create_task(
                digest=digest,
                server=str(account.get("ip") or ""),
                port=int(account.get("port") or 0),
                user=str(account.get("user") or ""),
                bundle_path=bundle_path,
                note="force_new_task" if force_new_task else "",
            )
        task_id = task["task_id"]
        try:
            lock = journal.run_lock(task_id)
            lock.__enter__()
        except journal_module.JournalBusy as exc:
            raise PendingVerificationError(str(exc)) from exc

        confirmed = journal.confirmed_uploads(task_id)
        selected_by_code = {
            drawing_bundle.normalize_code(item.get("code")): item
            for item in selected
            if drawing_bundle.normalize_code(item.get("code"))
        }
        for code, record in list(confirmed.items()):
            item = selected_by_code.get(code)
            if item is not None and not _upload_record_matches_item(record, item):
                confirmed.pop(code, None)
                log("图纸 %s 的内容已变化，本任务将重新上传当前版本" % code)
        decisions = journal.relation_decisions(task_id)
        confirmed_relations = journal.confirmed_relations(task_id)
        workflow_records = journal.operations(task_id, "workflow")

        # Historical recovery records must provide usable document evidence.
        _require_document_evidence(journal, task_id, confirmed, selected_codes)
        for record in workflow_records:
            _require_workflow_evidence(journal, task_id, record)
        previous_workflows = [
            _workflow_from_record(record) for record in workflow_records
            if record["status"] == journal_module.OP_OK
        ]
        covered_document_ids = {
            object_id for result in previous_workflows
            for object_id in result["drawing_ids"]
        }

        # Reconcile "已上传" claims against this machine's journal only.
        for item in items:
            if not isinstance(item, dict):
                continue
            code = drawing_bundle.normalize_code(item.get("code"))
            if item.get("status") == ITEM_DONE and code not in confirmed:
                item["status"] = ITEM_PENDING
                item.pop("drawing_id", None)
                item.pop("class_name", None)
                item.pop("class_id", None)
                item["note"] = "包内的“已上传”状态缺少本机记录，已重新纳入上传"

        todo = [
            item for item in selected
            if drawing_bundle.normalize_code(item.get("code")) not in confirmed
        ]
        if workflow_only:
            relations = active_relations_for_upload(selected, relations)
        relations_pending: list[dict] = []
        for rel in relations:
            key = (
                drawing_bundle.normalize_code(rel.get("parent_code")),
                drawing_bundle.normalize_code(rel.get("child_code")),
            )
            record = confirmed_relations.get(key)
            if record and str(record.get("link_type") or "") == str(rel.get("link_type") or ""):
                rel["link_status"] = "已存在"
                continue
            relations_pending.append(rel)

        if workflow_only:
            unconfirmed = [
                drawing_bundle.normalize_code(item.get("code"))
                for item in todo
            ]
            if unconfirmed:
                raise CaxaError(
                    "以下选中文档还没有本机确认成功的上传记录，"
                    "不能只发起流程：%s" % "、".join(unconfirmed[:20]),
                    stage="workflow_only",
                )
            if relations_pending:
                raise CaxaError(
                    "当前批次仍有未确认成功的归属/借用关系；"
                    "请先完成上传与关系核实，再单独发起流程",
                    stage="workflow_only",
                )

        confirmed_selected = _confirmed_workflow_documents(confirmed, selected_codes)
        workflow_missing = start_workflow and (
            todo or any(row["object_id"] not in covered_document_ids for row in confirmed_selected)
        )
        needs_connection = bool(todo or relations_pending or workflow_missing or (workflow_only and relations))

        def record_attach_attempt(code, baseline):
            row = journal.get_operation(task_id, "upload", code)
            detail = json.loads(row["detail"])
            detail["attach_attempt"] = baseline
            journal.record_operation(
                task_id, "upload", code, status=journal_module.OP_RUNNING,
                phase="upload", item_code=code, detail=json.dumps(detail, ensure_ascii=False),
            )

        # 2) connect only when there is work (or classification needs the tree)
        if needs_connection:
            bridge = CaxaBridge()
            device_registration = prepare_device_plm_com_registration(
                bridge, journal, log=log
            )
            log("登录 CAXA：%s@%s:%s" % (account["user"], account["ip"], account["port"]))
            bridge.login(
                account["ip"],
                int(account["port"]),
                account["user"],
                account["password"],
            )
            uploader = CaxaUploader(
                bridge,
                log=log,
                on_duplicate=on_duplicate,
                staging_dir=staging_module.TaskStaging(task_id, root=staging_root, log=log),
                auto_confirm=auto_confirm,
                before_attach=record_attach_attempt,
            )
            if not workflow_only:
                duplicate_parts = []
                existing_parts = discover_existing_original_parts(
                    uploader, selected, duplicate_codes=duplicate_parts
                )
                if duplicate_parts:
                    raise CaxaError(
                        "CAXA 中同一图号存在多个原件，请先清理重复对象：%s"
                        % "、".join(duplicate_parts[:20]),
                        stage="duplicate_existing_parts",
                    )
                original_rules = apply_existing_original_rules(
                    selected, relations, existing_parts
                )
                if original_rules["missing_borrow_positions"]:
                    raise CaxaError(
                        "以下已有原件的「借用处理」选了「按计划借用」，请设置借用父级：%s"
                        % "、".join(original_rules["missing_borrow_positions"][:20]),
                        stage="existing_original_relation",
                    )
                refine_relations_from_tree(
                    uploader,
                    relations,
                    {code: True for code in existing_parts},
                    log=log,
                    force=True,
                    existing_original_codes=set(existing_parts),
                )
                relations = active_relations_for_upload(selected, relations)
                relations_pending = []
                for rel in relations:
                    key = (
                        drawing_bundle.normalize_code(rel.get("parent_code")),
                        drawing_bundle.normalize_code(rel.get("child_code")),
                    )
                    record = confirmed_relations.get(key)
                    if (
                        record
                        and str(record.get("link_type") or "")
                        == str(rel.get("link_type") or "")
                    ):
                        rel["link_status"] = "已存在"
                        continue
                    relations_pending.append(rel)
            user_id = None
            if start_workflow and workflow_missing:
                user_id = find_person_id(bridge, account["user"])
                log("已识别 CAXA 登录人员：%s" % account["user"])
            half_product_codes = [
                target["machine"]
                for target in machine_targets
                if target["family"] == "02"
            ]
            half_product_codes.extend(
                drawing_bundle.normalize_code(item.get("code"))
                for item in selected
                if item.get("kind") == "component"
                and drawing_bundle.normalize_code(item.get("code")).startswith("02.")
            )
            for machine_code in dict.fromkeys(half_product_codes):
                state = half_product_target_state(uploader, machine_code)
                if state["blocked_reason"]:
                    raise CaxaError(
                        "02半成品 %s：%s；上传已阻止"
                        % (machine_code, state["blocked_reason"]),
                        stage="process_parent",
                    )

            for target in machine_targets:
                machine_code = target["machine"]
                if target["family"] == "02":
                    continue

                requested_folder = (
                    str(target.get("series_product_folder") or "").strip()
                    or drawing_bundle.series_product_folder(machine_code)
                )
                requested_folder = resolve_series_product_folder(
                    uploader,
                    machine_code,
                    requested_folder=requested_folder,
                )
                existing_part = find_existing_machine_part(
                    uploader, machine_code
                )
                if existing_part is not None:
                    for item in target["members"]:
                        item["series_product_folder"] = requested_folder
                        item["series_product_folder_requested"] = requested_folder
                        item["series_product_exists"] = False
                        item["series_machine_exists"] = True
                        item["series_product_part_exists"] = True
                        item["series_product_part_id"] = existing_part["object_id"]
                    continue

                requested_name = str(
                    target.get("series_product_name") or ""
                ).strip()
                product = (
                    find_machine_product(
                        uploader,
                        machine_code,
                        product_name=requested_name,
                        product_folder=requested_folder,
                    )
                    if requested_name
                    else None
                )
                folder_name = (
                    str((product or {}).get("folder_path") or "").strip()
                    or requested_folder
                )
                folder = (
                    uploader.find_product_folder(folder_name)
                    if folder_name
                    else None
                )
                part = (
                    uploader.machine_part(product["object_id"], machine_code)
                    if product is not None
                    else None
                )
                for item in target["members"]:
                    item["series_product_folder"] = folder_name
                    item["series_product_folder_requested"] = requested_folder
                    if product is not None and str(product.get("name") or "").strip():
                        item["series_product_name"] = str(product.get("name") or "").strip()
                    item["series_product_exists"] = product is not None
                    item["series_product_part_exists"] = part is not None
                    item["series_product_folder_exists"] = folder is not None
                    item["series_product_folder_will_create"] = False
                if product is None and not requested_name:
                    raise CaxaError(
                        "03整机 %s 的整机零件不存在，"
                        "请在上传清单中填写对应的系列产品名称"
                        % machine_code,
                        stage="series_product_name",
                    )
                if folder_name and folder is None:
                    raise CaxaError(
                        "03整机 %s 的系列产品目录 %s 在 CAXA 中不存在；"
                        "上传助手不允许创建系列目录，请先在 CAXA 设计树中"
                        "建立或修正系列目录"
                        % (machine_code, folder_name),
                        stage="series_product_folder",
                    )
        else:
            user_id = None

        # 3) classify AFTER the task is known, BEFORE any write; restore the
        #    classification recorded by this task if present.
        pending_keys = {
            (
                drawing_bundle.normalize_code(rel.get("parent_code")),
                drawing_bundle.normalize_code(rel.get("child_code")),
            )
            for rel in relations_pending
        }
        if relations:
            cache: dict[str, bool] = {}
            for rel in relations:
                key = (
                    drawing_bundle.normalize_code(rel.get("parent_code")),
                    drawing_bundle.normalize_code(rel.get("child_code")),
                )
                if key not in pending_keys:
                    continue
                if rel.get("existing_original"):
                    # Existing CAXA Parts keep their original ownership; this
                    # batch may only borrow them under the selected parent.
                    rel["link_type"] = "borrow"
                    rel["auto_decided"] = True
                    continue
                if rel.get("manual_override"):
                    # The human choice is authoritative; no tree query needed.
                    rel["auto_decided"] = False
                    continue
                if key in decisions and not recheck_tree:
                    expected = relations_module.default_link_type(key[0], key[1])
                    if decisions[key] == expected:
                        rel["link_type"] = decisions[key]
                        rel["auto_decided"] = True
                        continue
                    # Classification rules can change between app versions.
                    # Recompute stale non-manual decisions instead of carrying
                    # an old borrow/own result forever in the task journal.
                    log(
                        "关系分类规则已更新：%s ← %s，%s → %s"
                        % (
                            key[0], key[1],
                            "借用" if decisions[key] == "borrow" else "归属",
                            "借用" if expected == "borrow" else "归属",
                        )
                    )
                # Relation type is determined by the drawing-code structure.
                # Only manual link-only rows require a live existence probe.
                rel["auto_decided"] = False
                if uploader is None:
                    raise CaxaError(
                        "需要连接 CAXA，以确定关系为归属或借用", stage="plan_relations"
                    )
                if rel.get("link_only") and key[1] not in cache:
                    cache[key[1]] = uploader.find_part(key[1]) is not None
                    rel["tree_exists"] = cache[key[1]]
                rel["link_type"] = relations_module.default_link_type(
                    key[0], key[1]
                )
                rel["auto_decided"] = True

            analysis = relations_module.analyze_relations(relations)
            if analysis["issues"]:
                raise CaxaError(
                    "关系存在冲突，请先修正：\n"
                    + "\n".join(issue["message"] for issue in analysis["issues"]),
                    stage="plan_relations",
                )
            # 4) persist decisions before ANY remote write
            for rel in relations:
                if rel.get("manual_override"):
                    continue
                journal.record_operation(
                    task_id,
                    "decision",
                    journal_module.relation_key(
                        rel.get("parent_code"), rel.get("child_code")
                    ),
                    status=journal_module.OP_OK,
                    phase="classify",
                    parent_code=drawing_bundle.normalize_code(rel.get("parent_code")),
                    child_code=drawing_bundle.normalize_code(rel.get("child_code")),
                    link_type=str(rel.get("link_type") or ""),
                )

        batch_codes = {
            drawing_bundle.normalize_code(item.get("code")) for item in items if item.get("code")
        }
        own_parent_map: dict[str, str] = {}
        own_relation_by_child: dict[str, dict] = {}
        for rel in relations:
            child_code = drawing_bundle.normalize_code(rel.get("child_code"))
            if rel.get("link_type") == "own":
                own_parent_map[child_code] = (
                    drawing_bundle.normalize_code(rel.get("parent_code"))
                )
                own_relation_by_child.setdefault(child_code, rel)

        journal.set_task_stage(task_id, "upload")
        upload_results: list[dict] = []
        failures: list[dict] = []
        uncertain: list[dict] = []
        pending_upload_codes: set[str] = set()

        for item in selected:
            code = drawing_bundle.normalize_code(item.get("code"))
            record = confirmed.get(code)
            if record:
                item["status"] = ITEM_DONE
                item["drawing_id"] = int(record["remote_id"]) if record.get("remote_id") else None
                item["class_name"] = record.get("class_name") or ""
                item["class_id"] = record.get("class_id") or ""
                item["iterator_no"] = record.get("iterator_no") or ""
                item["note"] = "本任务已确认上传，未重复写入"
                upload_results.append(
                    _result_row(
                        item, ITEM_DONE, "本任务已确认上传",
                        drawing_id=item.get("drawing_id"),
                        class_name=item.get("class_name"),
                    )
                )

        ordered = sorted(
            todo, key=lambda item: 0 if item.get("kind") == "machine_drawing" else 1
        )
        ordered = _upload_order(ordered, relations)
        total = len(ordered)
        for index, item in enumerate(ordered):
            code = drawing_bundle.normalize_code(item.get("code"))
            if progress:
                progress(index, total, "正在上传 %s" % (code or "未命名"))
            journal.set_task_stage(task_id, "upload:%s" % code)
            journal.record_operation(
                task_id, "upload", code, status=journal_module.OP_RUNNING,
                phase="upload", item_code=code,
                detail=json.dumps({
                    "sha256": str(item.get("sha256") or "").lower(),
                    "size": item.get("size"),
                    "attach_attempt": None,
                }, ensure_ascii=False),
            )
            try:
                explicit_parent = own_parent_map.get(code) or drawing_bundle.normalize_code(
                    item.get("parent_code")
                )
                rel = own_relation_by_child.get(code) or {}
                result = uploader.upload_item(
                    item,
                    batch_codes,
                    on_machine_missing=None,
                    explicit_parent=explicit_parent,
                    relation_type=str(rel.get("link_type") or ""),
                    series_product_name=str(
                        item.get("series_product_name") or ""
                    ).strip(),
                    series_product_folder=str(
                        item.get("series_product_folder") or ""
                    ).strip(),
                )
                class_id = result.get("class_id")
                item["status"] = ITEM_DONE
                item["target"] = result.get("target", "")
                item["note"] = result.get("file_name", "")
                item["caxa_path"] = result.get("caxa_path", "")
                item["drawing_id"] = result.get("object_id")
                item["class_name"] = result.get("class_name")
                item["class_id"] = class_id
                item["iterator_no"] = result.get("iterator_no") or ""
                journal.record_operation(
                    task_id, "upload", code, status=journal_module.OP_OK,
                    phase="upload", item_code=code,
                    remote_id=result.get("object_id"),
                    class_id=class_id if class_id is not None else "",
                    iterator_no=str(result.get("iterator_no") or ""),
                    class_name=result.get("class_name") or "",
                    detail=json.dumps(
                        {
                            "file_name": str(result.get("file_name") or ""),
                            "target": str(result.get("target") or ""),
                            "sha256": str(result.get("staged_sha256") or item.get("sha256") or "").lower(),
                            "size": item.get("size"),
                        },
                        ensure_ascii=False,
                    ),
                )
                upload_results.append(
                    _result_row(
                        item, ITEM_DONE, item.get("caxa_path", ""),
                        drawing_id=result.get("object_id"),
                        class_name=result.get("class_name"),
                    )
                )
                if result.get("created_new_part") and result.get("linked_parent_identity"):
                    linked_parent = drawing_bundle.normalize_code(
                        result.get("linked_parent_identity")
                    )
                    for rel2 in relations:
                        if (
                            drawing_bundle.normalize_code(rel2.get("parent_code")) == linked_parent
                            and drawing_bundle.normalize_code(rel2.get("child_code")) == code
                        ):
                            rel2["link_status"] = "已归属"
                            journal.record_operation(
                                task_id, "relation",
                                journal_module.relation_key(linked_parent, code),
                                status=journal_module.OP_OK,
                                phase="relation", parent_code=linked_parent,
                                child_code=code, link_type="own",
                                detail="新建归属子件时已挂接",
                            )
                log("已上传 %s -> %s" % (code, item.get("target") or code))
            except PublishedDrawingError as exc:
                item["status"] = "失败：图纸已发布，无法追加版次"
                item["note"] = str(exc)
                failures.append(item)
                journal.record_operation(
                    task_id, "upload", code, status=journal_module.OP_FAILED,
                    phase="upload", item_code=code, error=str(exc),
                )
                log("上传阻止 %s：%s" % (code, exc))
            except MachineMissing as exc:
                item["status"] = "整机不存在"
                item["note"] = str(exc)
                failures.append(item)
                journal.record_operation(
                    task_id, "upload", code, status=journal_module.OP_FAILED,
                    phase="upload", item_code=code, error=str(exc),
                )
                log("整机不存在：%s" % exc)
            except (CaxaError, OSError, ValueError, staging_module.StagingError) as exc:
                uncertain_flag = bool(getattr(exc, "uncertain", False))
                partial = bool(getattr(exc, "partial", False))
                stage = getattr(exc, "stage", "") or ""
                error_result = getattr(exc, "result", {}) or {}
                if partial:
                    item["status"] = ITEM_PARTIAL
                elif uncertain_flag:
                    item["status"] = ITEM_UNCERTAIN
                else:
                    item["status"] = ITEM_FAILED
                item["note"] = str(exc)
                if uncertain_flag or partial:
                    uncertain.append(item)
                    pending_upload_codes.add(code)
                    operation = journal.get_operation(task_id, "upload", code)
                    attempt = json.loads(operation["detail"])
                    attempt.update(partial=partial, stage=stage, result=error_result)
                    journal.record_operation(
                        task_id, "upload", code, status=journal_module.OP_PENDING,
                        phase="upload:%s" % stage, item_code=code, error=str(exc),
                        remote_id=error_result.get("object_id")
                        or error_result.get("created_part_id") or "",
                        class_id=error_result.get("class_id") or "",
                        iterator_no=str(error_result.get("iterator_no") or ""),
                        detail=json.dumps(attempt, ensure_ascii=False),
                    )
                else:
                    failures.append(item)
                    journal.record_operation(
                        task_id, "upload", code, status=journal_module.OP_FAILED,
                        phase="upload:%s" % stage, item_code=code, error=str(exc),
                    )
                log("上传未完成 %s（%s）：%s" % (code, item["status"], exc))

        # ---- relations ----
        if progress:
            progress(total, total, "正在处理次级/借用关系")
        relation_rows: list[dict] = []
        relation_failures = 0
        relation_pending = 0
        relation_executed = 0
        missing_workflow_relations = []
        for rel in relations:
            parent = drawing_bundle.normalize_code(rel.get("parent_code"))
            child = drawing_bundle.normalize_code(rel.get("child_code"))
            if not parent or not child:
                continue
            key_tuple = (parent, child)
            key = journal_module.relation_key(parent, child)
            link_type = str(rel.get("link_type") or "borrow")
            record = confirmed_relations.get(key_tuple)
            if record and str(record.get("link_type") or "") == link_type:
                rel["link_status"] = "已存在"
                continue
            if str(rel.get("link_status") or "") in relations_module.PENDING_STATUSES:
                relation_pending += 1
                relation_rows.append(
                    {
                        "parent_code": parent, "child_code": child,
                        "link_type": link_type, "status": "待核实",
                        "note": rel.get("note") or "上次结果待核实",
                    }
                )
                continue
            if child in pending_upload_codes:
                rel["link_status"] = "缺子"
                relation_failures += 1
                relation_rows.append(
                    {
                        "parent_code": parent, "child_code": child,
                        "link_type": link_type, "status": "缺子",
                        "note": "子件上传结果待核实，关系未执行",
                    }
                )
                journal.record_operation(
                    task_id, "relation", key, status=journal_module.OP_PENDING,
                    phase="relation", parent_code=parent, child_code=child,
                    link_type=link_type, error="子件上传结果待核实",
                )
                continue
            child_item = next(
                (it for it in items
                 if drawing_bundle.normalize_code(it.get("code")) == child),
                None,
            )
            child_failed = child_item is not None and child_item.get("status") in (
                ITEM_FAILED, ITEM_PARTIAL, ITEM_UNCERTAIN, "整机不存在",
                "失败：图纸已发布，无法追加版次", ITEM_SKIPPED,
            )
            if child_failed:
                rel["link_status"] = "缺子"
                relation_failures += 1
                relation_rows.append(
                    {
                        "parent_code": parent, "child_code": child,
                        "link_type": link_type, "status": "缺子",
                        "note": "子件上传失败，关系未执行",
                    }
                )
                journal.record_operation(
                    task_id, "relation", key, status=journal_module.OP_FAILED,
                    phase="relation", parent_code=parent, child_code=child,
                    link_type=link_type, error="子件上传失败",
                )
                continue
            if not borrow_missing:
                rel["link_status"] = "未执行"
                relation_failures += 1
                relation_rows.append(
                    {
                        "parent_code": parent, "child_code": child,
                        "link_type": link_type, "status": "未执行",
                        "note": "本次未执行归属或借用，计划中的关系尚未完成",
                    }
                )
                log("必须执行的关系未执行：%s ← %s" % (parent, child))
                continue
            journal.set_task_stage(task_id, "relation:%s" % key)
            journal.record_operation(
                task_id, "relation", key, status=journal_module.OP_RUNNING,
                phase="relation", parent_code=parent, child_code=child,
                link_type=link_type,
            )
            try:
                row = uploader.link_relation(parent, child, link_type)
            except CaxaError as exc:
                row = {
                    "parent_code": parent,
                    "child_code": child,
                    "link_type": link_type,
                    "status": "待核实" if getattr(exc, "uncertain", False) else "失败",
                    "note": str(exc),
                }
            row["parent_code"] = parent
            row["child_code"] = child
            row["link_type"] = link_type
            relation_rows.append(row)
            rel["link_status"] = row["status"]
            rel["note"] = row.get("note") or ""
            relation_executed += 1
            if row["status"] in ("已借用", "已归属", "已存在"):
                journal.record_operation(
                    task_id, "relation", key, status=journal_module.OP_OK,
                    phase="relation", parent_code=parent, child_code=child,
                    link_type=link_type, detail=row.get("note") or row["status"],
                )
            elif row["status"] == "待核实":
                relation_pending += 1
                journal.record_operation(
                    task_id, "relation", key, status=journal_module.OP_PENDING,
                    phase="relation", parent_code=parent, child_code=child,
                    link_type=link_type, error=row.get("note") or "",
                )
            elif row["status"] == "冲突":
                relation_failures += 1
                journal.record_operation(
                    task_id, "relation", key, status=journal_module.OP_FAILED,
                    phase="relation", parent_code=parent, child_code=child,
                    link_type=link_type, error=row.get("note") or "已有链接类型冲突",
                )
            else:
                relation_failures += 1
                journal.record_operation(
                    task_id, "relation", key, status=journal_module.OP_FAILED,
                    phase="relation", parent_code=parent, child_code=child,
                    link_type=link_type, error=row.get("note") or "",
                )

        # Verify the complete relation set again.  A later link write can
        # invalidate an earlier one when the same child is borrowed under
        # multiple machines; a per-link immediate readback alone cannot detect
        # that cross-link interaction.
        verify_relations = (
            getattr(uploader, "verify_relation_pairs", None)
            if uploader is not None else None
        )
        if callable(verify_relations) and relations and borrow_missing:
            def relation_identity(row):
                return (row["parent_code"], row["child_code"])

            rows_by_pair = {relation_identity(row): row for row in relation_rows}
            items_by_code = {drawing_bundle.normalize_code(item.get("code")): item
                             for item in items}

            def save_relation_result(rel, status, note, phase):
                parent, child = relation_identity(rel)
                row = rows_by_pair.get((parent, child))
                if row is None:
                    row = {"parent_code": parent, "child_code": child,
                           "link_type": rel["link_type"]}
                    relation_rows.append(row)
                    rows_by_pair[(parent, child)] = row
                row.update(status=status, note=note)
                rel.update(link_status=status, note=note)
                state = (journal_module.OP_OK
                         if status in ("已借用", "已归属", "已存在")
                         else journal_module.OP_PENDING if status == "待核实"
                         else journal_module.OP_FAILED)
                journal.record_operation(
                    task_id, "relation", journal_module.relation_key(parent, child),
                    status=state, phase=phase, parent_code=parent,
                    child_code=child, link_type=rel["link_type"],
                    detail=note if state == journal_module.OP_OK else "",
                    error=note if state != journal_module.OP_OK else "",
                )

            final_check = verify_relations(relations)
            by_pair = {relation_identity(rel): rel for rel in relations}
            repairs_attempted = False
            for issue in final_check["issues"]:
                if issue.get("reason") != "missing_link":
                    continue
                parent, child = relation_identity(issue)
                if workflow_only:
                    missing_workflow_relations.append("%s ← %s" % (parent, child))
                    continue
                record = journal.get_operation(
                    task_id, "relation", journal_module.relation_key(parent, child)
                )
                # A missing readback is insufficient evidence to replay a write
                # whose outcome is unknown.  Keep explicit human recovery.
                if record and record["status"] in journal_module.PENDING_STATUSES:
                    continue
                child_item = items_by_code.get(child)
                if child in pending_upload_codes or (child_item is not None
                        and child_item.get("status") != ITEM_DONE):
                    continue
                rel = by_pair[(parent, child)]
                journal.record_operation(
                    task_id, "relation", journal_module.relation_key(parent, child),
                    status=journal_module.OP_RUNNING, phase="relation_final_repair",
                    parent_code=parent, child_code=child, link_type=rel["link_type"],
                )
                repairs_attempted = True
                try:
                    repair = uploader.link_relation(parent, child, rel["link_type"])
                except CaxaError as exc:
                    repair = {"status": "待核实" if getattr(exc, "uncertain", False)
                              else "失败", "note": str(exc)}
                # Unexpected interruptions intentionally leave OP_RUNNING behind.
                save_relation_result(rel, repair.get("status") or "失败",
                                     repair.get("note") or "", "relation_final_repair")
                relation_executed += 1
                log("最终回读补齐关系：%s ← %s（%s）"
                    % (parent, child, repair.get("status") or "失败"))
            if repairs_attempted:
                # This is a fresh call, never the parent snapshots used above.
                final_check = verify_relations(relations)
            issues_by_pair = {relation_identity(issue): issue
                              for issue in final_check["issues"]}
            for rel in relations:
                parent, child = relation_identity(rel)
                record = journal.get_operation(
                    task_id, "relation", journal_module.relation_key(parent, child)
                )
                if record and record["status"] in journal_module.PENDING_STATUSES:
                    save_relation_result(rel, "待核实", record.get("error")
                                         or "关系写入结果待人工核实", "relation_final_verify")
                    continue
                issue = issues_by_pair.get((parent, child))
                if issue:
                    reason = issue.get("reason") or "unverified"
                    status = ("待核实" if reason in
                              ("unreadable_parent", "unknown_reference", "unverified")
                              else "失败")
                    save_relation_result(rel, status, issue.get("note") or reason,
                                         "relation_final_verify")
                else:
                    row = rows_by_pair.get((parent, child)) or {}
                    status = row.get("status")
                    if status not in ("已借用", "已归属", "已存在"):
                        status = "已存在"
                    save_relation_result(rel, status, "关系最终回读通过",
                                         "relation_final_verify")
            # Final rows are authoritative; repaired failures must not leave
            # stale counters blocking the workflow or stale failed journal rows.
            relation_pending = sum(row["status"] == "待核实" for row in relation_rows)
            relation_failures = sum(row["status"] not in
                                    ("已借用", "已归属", "已存在", "待核实")
                                    for row in relation_rows)
            if not final_check["issues"]:
                log("关系最终回读通过：%d 条" % final_check["verified"])

        # ---- workflow ----
        final_confirmed = journal.confirmed_uploads(task_id)
        _require_document_evidence(journal, task_id, final_confirmed, selected_codes)
        workflow_documents = _confirmed_workflow_documents(final_confirmed, selected_codes)
        missing_document_codes = selected_codes - final_confirmed.keys()
        drawing_ids = [row["object_id"] for row in workflow_documents]
        uncovered_documents = [
            row for row in workflow_documents if row["object_id"] not in covered_document_ids
        ]
        uncovered_ids = [row["object_id"] for row in uncovered_documents]
        document_class_ids = [row["class_id"] for row in uncovered_documents]
        workflow_result: dict | None = None
        workflow_blocked = ""
        workflow_started_now = False
        if start_workflow:
            blockers = _phase_blockers(
                failures, pending_upload_codes, relation_failures,
                relation_pending, drawing_ids,
            )
            if missing_document_codes:
                blockers.append("%d 项选中文档缺少已确认上传记录" % len(missing_document_codes))
            if missing_workflow_relations:
                blockers.append(
                    "CAXA 中缺少关系 %s，请先完成关系补建，再单独发起流程"
                    % "、".join(missing_workflow_relations[:8])
                )
            if blockers:
                workflow_blocked = "已阻止发起流程：" + "、".join(blockers)
                workflow_result = {
                    "status": WORKFLOW_BLOCKED,
                    "reason": workflow_blocked,
                    "drawing_ids": drawing_ids,
                }
                log(workflow_blocked)
            elif not uncovered_ids:
                selected_ids = set(drawing_ids)
                matching = [
                    result for result in previous_workflows
                    if selected_ids.intersection(result["drawing_ids"])
                ]
                workflow_result = {
                    **matching[-1],
                    "drawing_ids": drawing_ids,
                    "process_instance_ids": [result["process_instance_id"] for result in matching],
                    "duplicate": True,
                    "note": "所有选中文档已有本批审核打印流程，未重复发起",
                }
                log(workflow_result["note"])
            else:
                workflow_key = _workflow_key(workflow_records, uncovered_ids)
                if progress:
                    progress(total, total, "正在发起审核打印流程")
                journal.set_task_stage(task_id, "workflow")
                journal.record_workflow(
                    task_id, key=workflow_key, status=journal_module.OP_RUNNING, process_id=None,
                    stage="create", drawing_ids=uncovered_ids,
                )
                try:
                    performer_id = find_person_id(bridge, performer_name)
                    raw = bridge.start_workflow(
                        uncovered_ids,
                        account["ip"],
                        int(account["port"]),
                        user_id,
                        document_class_ids=document_class_ids,
                        performer_id=performer_id,
                        template_id=workflow_template_id,
                        template_instance_id=workflow_template_instance_id,
                        process_def_id=workflow_process_def_id,
                        activity_def_id=workflow_activity_def_id,
                        execute=True,
                    )
                    process_id = raw.get("process_instance_id")
                    started = raw.get("started") is True
                    process_state = str(raw.get("process_state") or "")
                    state_verified = (
                        raw.get("state_verified") is True
                        and process_state == "2"
                    )
                    try:
                        positive = int(process_id or 0) > 0
                    except (TypeError, ValueError):
                        positive = False
                    # A successful start call is insufficient. Require a
                    # live state read (2 OPEN__RUNNING) and the exact
                    # document set read back from the process object.
                    link_ids = raw.get("linked_document_ids")
                    linkage_ok = False
                    if (
                        raw.get("documents_verified") is True
                        and isinstance(link_ids, list)
                    ):
                        try:
                            linkage_ok = (
                                set(int(v) for v in link_ids)
                                == set(uncovered_ids)
                            )
                        except (TypeError, ValueError):
                            linkage_ok = False
                    if not started or not positive or not state_verified or not linkage_ok:
                        workflow_result = {
                            "status": WORKFLOW_PENDING,
                            "process_instance_id": process_id,
                            "reason": (
                                "流程启动结果待核实。流程实例 ID %s，启动%s，运行状态%s，关联文档已回读 %s 项（计划 %d 项）"
                                % (
                                    process_id if positive else "未返回",
                                    "已确认" if started else "未确认",
                                    "已确认" if state_verified else "未确认",
                                    len(link_ids) if isinstance(link_ids, list) else "未知",
                                    len(uncovered_ids),
                                )
                            ),
                            "drawing_ids": uncovered_ids,
                        }
                        journal.record_workflow(
                            task_id, key=workflow_key, status=journal_module.OP_PENDING,
                            process_id=process_id, stage="started_unconfirmed",
                            drawing_ids=uncovered_ids, started=False,
                            error=workflow_result["reason"],
                        )
                        log("流程启动未确认：%s" % workflow_result["reason"])
                    else:
                        workflow_result = {
                            "status": WORKFLOW_STARTED,
                            "process_instance_id": process_id,
                            "drawing_ids": uncovered_ids,
                            "started": True,
                            "process_state": process_state,
                            "start_time": raw.get("process_start_time") or "",
                            "linked_document_ids": [int(v) for v in link_ids],
                        }
                        workflow_started_now = True
                        journal.record_workflow(
                            task_id, key=workflow_key, status=journal_module.OP_OK,
                            process_id=process_id, stage="started",
                            drawing_ids=uncovered_ids, started=True,
                            detail="state=%s, linked_documents=%s"
                            % (process_state, len(link_ids)),
                        )
                        log("流程已发起，流程实例 ID %s" % process_id)
                except CaxaError as exc:
                    process_id = getattr(exc, "created_process_instance_id", None)
                    uncertain_flag = bool(getattr(exc, "uncertain", False)) or bool(process_id)
                    workflow_result = {
                        "status": WORKFLOW_PENDING if uncertain_flag else WORKFLOW_FAILED,
                        "process_instance_id": process_id,
                        "reason": str(exc),
                        "stage": getattr(exc, "stage", ""),
                        "drawing_ids": uncovered_ids,
                    }
                    journal.record_workflow(
                        task_id, key=workflow_key,
                        status=journal_module.OP_PENDING if uncertain_flag
                        else journal_module.OP_FAILED,
                        process_id=process_id,
                        stage=getattr(exc, "stage", "") or "workflow",
                        drawing_ids=uncovered_ids,
                        error=str(exc),
                    )
                    log("流程未确认完成：%s（流程实例 ID %s）" % (exc, process_id))

        uploaded_total = sum(1 for item in items if item.get("status") == ITEM_DONE)
        ok = (
            not failures
            and not pending_upload_codes
            and relation_failures == 0
            and relation_pending == 0
            and not workflow_blocked
            and (not start_workflow or (workflow_result or {}).get("status") == WORKFLOW_STARTED)
        )
        if pending_upload_codes or relation_pending or (
            workflow_result and workflow_result.get("status") == WORKFLOW_PENDING
        ):
            task_status = journal_module.TASK_PENDING
        elif ok:
            task_status = journal_module.TASK_DONE
        else:
            task_status = journal_module.TASK_FAILED
        journal.set_task_status(task_id, task_status, stage="finished")

        duplicate = not todo and not relation_executed and not workflow_started_now
        if duplicate and not ok:
            duplicate = False
        return {
            "ok": ok,
            "task_id": task_id,
            "duplicate": duplicate,
            "items": items,
            "results": upload_results,
            "uploaded": uploaded_total,
            "failed": len(failures),
            "uncertain": len(pending_upload_codes),
            "pending": [
                {"code": item.get("code"), "note": item.get("note")}
                for item in uncertain
            ],
            "relations": relation_rows,
            "relation_failures": relation_failures,
            "relation_pending": relation_pending,
            "borrow_rows": relation_rows,
            "borrow_total": sum(
                1 for row in relation_rows if row["status"] in ("已借用", "已归属")
            ),
            "borrow_skipped": sum(1 for row in relation_rows if row["status"] == "已存在"),
            "workflow": workflow_result,
            "workflow_blocked": workflow_blocked,
            "task_status": task_status,
            "drawing_ids": drawing_ids,
            "workflow_documents": workflow_documents,
            "device_registration": device_registration or {},
            "warnings": [
                warning
                for item in items
                for warning in drawing_bundle.filename_warnings(item)
            ],
        }
    except Exception as exc:
        if task is not None:
            try:
                pending_now = journal.pending_operations(task["task_id"])
            except Exception:
                pending_now = []
            status = (
                journal_module.TASK_PENDING
                if (pending_now or getattr(exc, "uncertain", False))
                else journal_module.TASK_FAILED
            )
            try:
                journal.set_task_status(task["task_id"], status, stage="aborted", note=str(exc))
            except Exception:
                pass
        raise
    finally:
        if lock is not None:
            try:
                lock.__exit__(None, None, None)
            except Exception:
                pass
        if content_lock is not None:
            try:
                content_lock.__exit__(None, None, None)
            except Exception:
                pass
        if uploader is not None:
            try:
                uploader.close()
            except Exception:
                pass
        if bridge is not None:
            bridge.stop()
        if owns_journal:
            journal.close()


# ---------------------------------------------------------------------------
# preview / pre-check / manual relations
# ---------------------------------------------------------------------------


def preview_bundle(manifest: dict) -> dict:
    selected = _selected_items(manifest)
    missing = [
        item for item in selected if not os.path.isfile(str(item.get("file_path") or ""))
    ]
    invalid = []
    fingerprints = []
    for item in selected:
        problems = drawing_bundle.item_problems(item, check_content=False)
        if problems:
            invalid.append((item, problems))
        fingerprints.extend(
            (item, problem) for problem in drawing_bundle.verify_fingerprint(item)
        )
    relations = drawing_bundle.normalize_manifest_relations(manifest)
    issues = relations_module.analyze_relations(relations)["issues"]
    return {
        "items": len(manifest.get("items") or []),
        "selected": len(selected),
        "missing_files": len(missing),
        "invalid_names": len(invalid),
        "invalid_details": [
            "%s：%s" % (item.get("code"), "；".join(problems)) for item, problems in invalid
        ],
        "fingerprint_errors": len(fingerprints),
        "fingerprint_details": [
            "%s：%s" % (item.get("code"), problem) for item, problem in fingerprints
        ],
        "relation_issues": issues,
        "ready": not missing and not invalid and not fingerprints and bool(selected),
    }


def precheck_with_caxa(
    manifest: dict,
    account: dict,
    log=print,
    journal_path: str | None = None,
) -> dict:
    """Calibrate own/borrow against the live tree (explicit user action)."""
    relations = drawing_bundle.normalize_manifest_relations(manifest)
    bridge = None
    uploader = None
    state_journal = None
    device_registration = None
    try:
        bridge = CaxaBridge()
        state_journal = journal_module.TaskJournal(journal_path)
        device_registration = prepare_device_plm_com_registration(
            bridge, state_journal, log=log
        )
        log("预检连接 CAXA：%s@%s:%s" % (account["user"], account["ip"], account["port"]))
        bridge.login(
            account["ip"],
            int(account["port"]),
            account["user"],
            account["password"],
        )
        uploader = CaxaUploader(bridge, log=log)
        selected = _selected_items(manifest)
        duplicate_parts = []
        existing_parts = discover_existing_original_parts(
            uploader, selected, duplicate_codes=duplicate_parts
        )
        existing_codes = set(existing_parts)
        cache = refine_relations_from_tree(
            uploader,
            relations,
            {code: True for code in existing_codes},
            log=log,
            force=True,
            existing_original_codes=existing_codes,
        )
        original_rules = apply_existing_original_rules(
            selected, relations, existing_parts
        )
        analysis = relations_module.analyze_relations(relations)
        machine_targets = collect_machine_targets(selected)
        machine_products = []
        process_parents = []

        half_product_codes = []
        for target in machine_targets:
            if target["family"] == "02":
                half_product_codes.append(target["machine"])
        for item in selected:
            code = drawing_bundle.normalize_code(item.get("code"))
            if item.get("kind") == "component" and code.startswith("02."):
                half_product_codes.append(code)
        half_product_codes = list(dict.fromkeys(half_product_codes))

        for machine_code in half_product_codes:
            row = half_product_target_state(uploader, machine_code)
            row["item_codes"] = [
                drawing_bundle.normalize_code(item.get("code"))
                for item in selected
                if (
                    item.get("kind") == "component"
                    and drawing_bundle.normalize_code(item.get("code")) == machine_code
                )
                or (
                    item.get("kind") == "process_doc"
                    and drawing_bundle.normalize_code(item.get("machine")) == machine_code
                )
            ]
            process_parents.append(row)

        for target in machine_targets:
            machine_code = target["machine"]
            if target["family"] == "02":
                continue
            requested_folder = (
                str(target.get("series_product_folder") or "").strip()
                or drawing_bundle.series_product_folder(machine_code)
            )
            requested_folder = resolve_series_product_folder(
                uploader,
                machine_code,
                requested_folder=requested_folder,
            )
            existing_part = find_existing_machine_part(
                uploader, machine_code
            )
            if existing_part is not None:
                for item in target["members"]:
                    item["series_product_folder"] = requested_folder
                    item["series_product_folder_requested"] = requested_folder
                    item["series_product_exists"] = False
                    item["series_machine_exists"] = True
                    item["series_product_part_exists"] = True
                    item["series_product_part_id"] = existing_part["object_id"]
                machine_products.append(
                    {
                        "code": machine_code,
                        "exists": True,
                        "product_exists": False,
                        "product_id": None,
                        "product_name": "",
                        "part_exists": True,
                        "part_id": existing_part["object_id"],
                        "folder": requested_folder,
                        "requested_folder": requested_folder,
                        "product_folder_paths": [],
                        "folder_exists": True,
                        "folder_will_create": False,
                        "folder_id": None,
                        "requested_name": str(
                            target.get("series_product_name") or ""
                        ).strip(),
                        "requires_product_name": False,
                        "reused_existing_part": True,
                        "has_machine_drawing": bool(
                            target.get("has_machine_drawing")
                        ),
                        "item_codes": list(target["item_codes"]),
                    }
                )
                continue

            requested_name = str(
                target.get("series_product_name") or ""
            ).strip()
            product = (
                find_machine_product(
                    uploader,
                    machine_code,
                    product_name=requested_name,
                    product_folder=requested_folder,
                )
                if requested_name
                else None
            )
            folder_name = (
                str((product or {}).get("folder_path") or "").strip()
                or requested_folder
            )
            folder = (
                uploader.find_product_folder(folder_name)
                if folder_name
                else None
            )
            part = (
                uploader.machine_part(product["object_id"], machine_code)
                if product is not None
                else None
            )
            for item in target["members"]:
                item["series_product_folder"] = folder_name
                item["series_product_folder_requested"] = requested_folder
                if product is not None and str(product.get("name") or "").strip():
                    item["series_product_name"] = str(
                        product.get("name") or ""
                    ).strip()
            machine_products.append(
                {
                    "code": machine_code,
                    "exists": product is not None,
                    "product_exists": product is not None,
                    "product_id": (product or {}).get("object_id"),
                    "product_name": (product or {}).get("name", ""),
                    "part_exists": part is not None,
                    "part_id": (part or {}).get("object_id"),
                    "folder": folder_name,
                    "requested_folder": requested_folder,
                    "product_folder_paths": list(
                        (product or {}).get("folder_paths") or []
                    ),
                    "folder_exists": folder is not None if folder_name else True,
                    "folder_will_create": False,
                    "folder_id": (folder or {}).get("object_id"),
                    "requested_name": str(
                        target.get("series_product_name") or ""
                    ).strip(),
                    "requires_product_name": product is None,
                    "has_machine_drawing": bool(
                        target.get("has_machine_drawing")
                    ),
                    "item_codes": list(target["item_codes"]),
                }
            )
        missing_link_children = [
            drawing_bundle.normalize_code(rel.get("child_code"))
            for rel in relations
            if rel.get("link_only") and not cache.get(
                drawing_bundle.normalize_code(rel.get("child_code"))
            )
        ]
        own_count = sum(1 for rel in relations if rel.get("link_type") == "own")
        borrow_count = sum(1 for rel in relations if rel.get("link_type") != "own")
        return {
            "checked_relations": len(relations),
            "own_count": own_count,
            "borrow_count": borrow_count,
            "tree_cache": cache,
            "machine_products": machine_products,
            "process_parents": process_parents,
            "missing_process_parts": [
                row
                for row in process_parents
                if row.get("blocked_reason")
            ],
            "process_parts_will_create": [
                row
                for row in process_parents
                if row.get("part_will_create")
            ],
            "duplicate_process_parts": [
                row
                for row in process_parents
                if int(row.get("part_candidate_count") or 0) > 1
                or int(row.get("parent_candidate_count") or 0) > 1
                or int(row.get("own_link_count") or 0) > 1
            ],
            "missing_product_folders": [
                row for row in machine_products if not row["folder_exists"]
            ],
            "missing_product_parts": [
                row
                for row in machine_products
                if row["product_exists"] and not row["part_exists"]
            ],
            "missing_link_children": missing_link_children,
            "issues": analysis["issues"],
            "existing_original_items": [
                {
                    "code": code,
                    "part_id": (part or {}).get("object_id"),
                }
                for code, part in sorted(existing_parts.items())
            ],
            "existing_relation_items": [
                {
                    "code": drawing_bundle.normalize_code(
                        rel.get("child_code")
                    ),
                    "parent_code": drawing_bundle.normalize_code(
                        rel.get("parent_code")
                    ),
                    "link_type": rel.get("link_type"),
                }
                for rel in relations
                if rel.get("existing_relation")
            ],
            "duplicate_existing_parts": duplicate_parts,
            **{
                key: value
                for key, value in original_rules.items()
                if key != "existing_codes"
            },
            "device_registration": device_registration or {},
        }
    finally:
        if uploader is not None:
            uploader.close()
        if bridge is not None:
            bridge.stop()
        if state_journal is not None:
            state_journal.close()


def apply_manual_borrows(manifest: dict, account: dict, log=print,
                         journal=None, journal_path: str | None = None) -> dict:
    """Execute the selected relations only: read-only lookups, no new parts."""
    relations = drawing_bundle.normalize_manifest_relations(manifest)
    batch_codes = {
        drawing_bundle.normalize_code(item.get("code"))
        for item in (manifest.get("items") or [])
        if item.get("code")
    }
    target_relations = [
        rel for rel in relations
        if drawing_bundle.normalize_code(rel.get("child_code")) not in batch_codes
        or rel.get("source") == "manual"
    ]
    if not target_relations:
        raise CaxaError("没有可执行的次级/借用条目")

    digest = hashlib.sha256(
        json.dumps(
            {
                "server": str(account.get("ip") or ""),
                "port": int(account.get("port") or 0),
                "user": str(account.get("user") or ""),
                "relations": sorted(
                    [
                        {
                            "parent": drawing_bundle.normalize_code(rel.get("parent_code")),
                            "child": drawing_bundle.normalize_code(rel.get("child_code")),
                            "link_type": (
                                rel.get("link_type") if rel.get("manual_override") else "auto"
                            ),
                        }
                        for rel in target_relations
                    ],
                    key=lambda entry: (entry["parent"], entry["child"]),
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    owns_journal = journal is None
    journal = journal or journal_module.TaskJournal(journal_path)
    bridge = None
    uploader = None
    task = None
    lock = None
    content_lock = None
    rows: list[dict] = []
    try:
        try:
            content_lock = journal.run_lock(content_lock_key(digest, account))
            content_lock.__enter__()
        except journal_module.JournalBusy as exc:
            raise PendingVerificationError(str(exc)) from exc
        existing = journal.find_task(
            digest=digest,
            server=str(account.get("ip") or ""),
            port=int(account.get("port") or 0),
            user=str(account.get("user") or ""),
        )
        for previous in journal.tasks_for_digest(
            digest=digest,
            server=str(account.get("ip") or ""),
            port=int(account.get("port") or 0),
            user=str(account.get("user") or ""),
        ):
            pending_previous = journal.pending_operations(previous["task_id"])
            if pending_previous:
                raise _pending_error(pending_previous)
        task = existing or journal.create_task(
            digest=digest,
            server=str(account.get("ip") or ""),
            port=int(account.get("port") or 0),
            user=str(account.get("user") or ""),
            note="manual_borrow",
        )
        try:
            lock = journal.run_lock(task["task_id"])
            lock.__enter__()
        except journal_module.JournalBusy as exc:
            raise PendingVerificationError(str(exc)) from exc

        bridge = CaxaBridge()
        device_registration = prepare_device_plm_com_registration(
            bridge, journal, log=log
        )
        log("登录 CAXA：%s@%s:%s" % (account["user"], account["ip"], account["port"]))
        bridge.login(
            account["ip"],
            int(account["port"]),
            account["user"],
            account["password"],
        )
        uploader = CaxaUploader(bridge, log=log, on_duplicate="reuse")
        refine_relations_from_tree(uploader, target_relations, log=log)
        analysis = relations_module.analyze_relations(target_relations)
        if analysis["issues"]:
            raise CaxaError(
                "关系存在冲突，请先修正：\n"
                + "\n".join(issue["message"] for issue in analysis["issues"]),
                stage="plan_relations",
            )
        confirmed = journal.confirmed_relations(task["task_id"])
        failures = 0
        pending = 0
        for rel in target_relations:
            parent = drawing_bundle.normalize_code(rel.get("parent_code"))
            child = drawing_bundle.normalize_code(rel.get("child_code"))
            if not parent or not child:
                continue
            link_type = str(rel.get("link_type") or "borrow")
            key_tuple = (parent, child)
            key = journal_module.relation_key(parent, child)
            record = confirmed.get(key_tuple)
            if record and str(record.get("link_type") or "") == link_type:
                rows.append(
                    {
                        "parent_code": parent, "child_code": child,
                        "link_type": link_type, "status": "已存在",
                        "note": "本任务已确认",
                    }
                )
                continue
            journal.record_operation(
                task["task_id"], "relation", key, status=journal_module.OP_RUNNING,
                phase="relation", parent_code=parent, child_code=child,
                link_type=link_type,
            )
            try:
                row = uploader.link_relation(parent, child, link_type)
            except CaxaError as exc:
                row = {
                    "parent_code": parent, "child_code": child,
                    "link_type": link_type,
                    "status": "待核实" if getattr(exc, "uncertain", False) else "失败",
                    "note": str(exc),
                }
            row["parent_code"] = parent
            row["child_code"] = child
            row["link_type"] = link_type
            rows.append(row)
            rel["link_status"] = row["status"]
            rel["note"] = row.get("note") or ""
            if row["status"] in ("已借用", "已归属", "已存在"):
                status = journal_module.OP_OK
            elif row["status"] == "待核实":
                status = journal_module.OP_PENDING
                pending += 1
            elif row["status"] in ("缺父", "缺子"):
                status = journal_module.OP_FAILED
                failures += 1
            else:
                status = journal_module.OP_FAILED
                failures += 1
            journal.record_operation(
                task["task_id"], "relation", key, status=status, phase="relation",
                parent_code=parent, child_code=child, link_type=link_type,
                detail=row.get("note") or row["status"],
                error=row.get("note") if status == journal_module.OP_FAILED else "",
            )
        journal.set_task_status(
            task["task_id"],
            journal_module.TASK_PENDING if pending
            else journal_module.TASK_FAILED if failures
            else journal_module.TASK_DONE,
            stage="finished",
        )
        return {
            "borrowed": sum(1 for row in rows if row["status"] in ("已借用", "已归属")),
            "skipped": sum(1 for row in rows if row["status"] == "已存在"),
            "missing": sum(1 for row in rows if row["status"] in ("缺父", "缺子")),
            "failed": failures,
            "pending": pending,
            "rows": rows,
            "task_id": task["task_id"],
            "ok": not failures and not pending,
            "device_registration": device_registration or {},
        }
    finally:
        if lock is not None:
            try:
                lock.__exit__(None, None, None)
            except Exception:
                pass
        if content_lock is not None:
            try:
                content_lock.__exit__(None, None, None)
            except Exception:
                pass
        if uploader is not None:
            uploader.close()
        if bridge is not None:
            bridge.stop()
        if owns_journal:
            journal.close()

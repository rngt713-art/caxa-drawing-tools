"""Unified relation (次级条目) model.

Business rules implemented here (confirmed by the user, 2026-11-05):

* The drawing code decides the default relation type.  A code-prefix parent,
  or a parent and child in the same ``01.x.*`` series, is ``own``
  (归属子件, Reference=False).  A ``03.*`` machine and cross-series parents are
  ``borrow`` (借用, Reference=True).
* A manual choice always wins (``manual_override=True``) and the automatic
  decision is stable within one task: retrying must not turn an ``own`` entry
  into ``borrow`` merely because the previous attempt created the part.
* ``relations`` is the only executable source.  ``assemblies``,
  ``manual_borrows`` and ``item.parent_code`` are legacy inputs migrated once
  on import; afterwards a deleted relation stays deleted.
* Product folders prefer the live ProductFolder index loaded during pre-check.
  Folder names are matched by their Latin-letter aliases; the static fallback
  is only for building the local plan before a CAXA session exists.

This module deliberately has no dependency on the rest of the application so it
can be imported from the CAXA layer without cycles.  Callers that need code
syntax validation pass ``is_valid_code``.
"""

from __future__ import annotations

import re

VALID_LINK_TYPES = ("own", "borrow")

#: Link statuses that mean "this relation is finished, do not execute it again".
DONE_STATUSES = ("已借用", "已归属", "已存在")

#: Statuses that mean "uncertain outcome, do not silently replay".
PENDING_STATUSES = ("待核实", "结果待核实")

MIGRATION_FLAG = "relations_migrated"
MIGRATION_VERSION = 1
MIGRATED_CHILDREN_FLAG = "relations_migrated_children"
AUTO_PREFIX_FLAG = "relations_auto_prefix_v1"
_GENERAL_SERIES_RE = re.compile(r"^01\.([A-Z0-9]+)(?:[.\-]|$)")
_SERIES_ALIAS_GROUPS = {"OM": "OW", "OV": "OW", "OW": "OW"}
_LATIN_RUN_RE = re.compile(r"[A-Z]+")


def normalize_code(value: str) -> str:
    """Uppercase/canonicalise a drawing code the same way the GUI does."""
    text = str(value or "").strip()
    if not text:
        return ""
    return (
        text.replace("，", ",")
        .replace("：", ":")
        .replace("。", ".")
        .replace("（", "(")
        .replace("）", ")")
    ).upper()


def is_done(relation: dict) -> bool:
    return str(relation.get("link_status") or "") in DONE_STATUSES


def is_pending(relation: dict) -> bool:
    return str(relation.get("link_status") or "") in PENDING_STATUSES


def normalize_link_type(value: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in VALID_LINK_TYPES else ""


def is_code_prefix_parent(parent_code: str, child_code: str) -> bool:
    parent = normalize_code(parent_code)
    child = normalize_code(child_code)
    return bool(
        parent
        and child
        and parent != child
        and (child.startswith(parent + ".") or child.startswith(parent + "-"))
    )


def same_general_part_series(parent_code: str, child_code: str) -> bool:
    """Whether both codes belong to the same ``01.x.*`` series.

    Within one general-parts series, a cross-code parent still describes
    ownership.  For example, ``01.22.01.V202.Z3`` owns
    ``01.22.01.V202.1001`` even though one code is not a textual prefix of the
    other.
    """
    parent = normalize_code(parent_code)
    child = normalize_code(child_code)
    parent_match = _GENERAL_SERIES_RE.match(parent)
    child_match = _GENERAL_SERIES_RE.match(child)
    return bool(
        parent_match
        and child_match
        and parent_match.group(1) == child_match.group(1)
    )


def series_product_folder(machine_code: str) -> str:
    """Return the CAXA product-folder path for a ``03.<series>.*`` code.

    ``03.HT.HGB.*`` is nested under ``HT合同特殊/HGB系列``.  Other
    ``03.HT.*`` products live directly under ``HT合同特殊``.  Ordinary
    series folder names contain letters only, so model digits are removed
    from tokens such as ``HX21`` and ``HGB01``.  ``W6`` is a design-drawing
    tree, not an automatically inferred machine-product folder.
    """
    machine = normalize_code(machine_code)
    match = re.match(r"^03\.([A-Z0-9]+)(?:[.\-]|$)", machine)
    if not match:
        return ""
    series = match.group(1)
    if series == "W6":
        return ""
    series_code = re.sub(r"\d+", "", series)
    if not series_code:
        return ""
    series_code = _SERIES_ALIAS_GROUPS.get(series_code, series_code)
    if series_code == "HT":
        if machine.startswith("03.HT.HGB"):
            return "HT合同特殊/HGB系列"
        return "HT合同特殊"
    if series_code == "GPZ":
        return "GPZ钢瓶组"
    return "%s系列" % series_code


def machine_series_aliases(machine_code: str) -> list[str]:
    """Return ordered Latin aliases used to match an actual ProductFolder.

    Numeric model suffixes are ignored, so ``HX21`` contributes ``HX`` and
    ``HGB01`` contributes ``HGB``. ``OM/OV/OW`` are the explicitly confirmed
    equivalent group stored in the ``OW系列`` folder. ``W6`` is a design-tree
    exception and intentionally produces no automatic aliases.
    """
    machine = normalize_code(machine_code)
    match = re.match(r"^03\.(.+)$", machine)
    if not match:
        return []
    parts = [
        part for part in re.split(r"[.\-]", match.group(1))
        if part
    ]
    if parts and parts[0] == "W6":
        return []
    aliases = []
    for index, part in enumerate(parts):
        letters = "".join(_LATIN_RUN_RE.findall(part))
        if len(letters) < 2:
            continue
        if index == 0:
            letters = _SERIES_ALIAS_GROUPS.get(letters, letters)
        if letters not in aliases:
            aliases.append(letters)
    return aliases


def _folder_name_aliases(value: str) -> list[str]:
    aliases = []
    for letters in _LATIN_RUN_RE.findall(normalize_code(value)):
        if len(letters) >= 2 and letters not in aliases:
            aliases.append(letters)
    return aliases


def match_series_product_folder(
    machine_code: str,
    folders: list[dict],
) -> dict | None:
    """Match one actual ProductFolder path by its Latin-letter aliases.

    The first code alias must occur in the first path level. Deeper path levels
    must consume later aliases in order, so ``HT + HGB`` can select
    ``HT合同特殊/HGB系列`` without matching unrelated sibling folders.
    Ambiguous equal-score matches return ``None`` and remain manual.
    """
    code_aliases = machine_series_aliases(machine_code)
    if not code_aliases:
        return None
    matches = []
    for row in folders or []:
        if not isinstance(row, dict):
            continue
        parts = row.get("path_parts")
        if not isinstance(parts, (list, tuple)) or not parts:
            parts = [
                part.strip()
                for part in str(row.get("path") or "").replace("\\", "/").split("/")
                if part.strip()
            ]
        if not parts:
            continue
        first_aliases = _folder_name_aliases(str(parts[0]))
        if code_aliases[0] not in first_aliases:
            continue
        cursor = 1
        matched = 1
        valid = True
        for part in parts[1:]:
            aliases = _folder_name_aliases(str(part))
            found = next(
                (
                    index for index in range(cursor, len(code_aliases))
                    if code_aliases[index] in aliases
                ),
                None,
            )
            if found is None:
                valid = False
                break
            cursor = found + 1
            matched += 1
        if valid:
            result = dict(row)
            result["matched_aliases"] = code_aliases[:cursor]
            exact_name = int(
                normalize_code(row.get("name")).strip()
                == code_aliases[0]
            )
            matches.append((matched, exact_name, result))
    if not matches:
        return None
    best_score = max(
        (matched, exact_name)
        for matched, exact_name, _row in matches
    )
    best = [
        row for matched, exact_name, row in matches
        if (matched, exact_name) == best_score
    ]
    if len(best) != 1:
        return None
    return best[0]


def prefix_parent_candidates(child_code: str) -> list[str]:
    """Return code ancestors from nearest to broadest."""
    current = normalize_code(child_code)
    parents: list[str] = []
    while current:
        if "-" in current:
            current = current.rsplit("-", 1)[0]
        elif "." in current:
            current = current.rsplit(".", 1)[0]
        else:
            break
        if current and current not in parents:
            parents.append(current)
    return parents


def default_link_type(
    parent_code: str,
    child_code: str,
    batch_codes: set[str] | None = None,
    child_exists_in_tree: bool | None = None,
) -> str:
    """Return the default relation type.

    The owning part tree is determined by the drawing code.  A direct or
    nearest existing code-prefix parent is ``own``.  Codes under the same
    ``01.x.*`` general-parts series are also ``own`` even when the rest of the
    code differs.  A whole-machine parent (``03.*``) or any other cross-series
    parent is a borrow reference.  Existing-child state no longer changes this
    rule.
    """
    del batch_codes, child_exists_in_tree
    parent = normalize_code(parent_code)
    child = normalize_code(child_code)
    if parent.startswith("03."):
        return "borrow"
    if same_general_part_series(parent, child) or is_code_prefix_parent(parent, child):
        return "own"
    return "borrow"


def infer_prefix_relations(
    items: list[dict],
    relations: list[dict],
    *,
    child_codes: set[str] | None = None,
    is_valid_code=None,
) -> list[dict]:
    """Infer immediate component parents from drawing-code suffixes.

    A code such as ``01.21.01.SFD2-17-01-03`` maps to the nearest existing
    prefix in the current batch.  Only components with no ownership parent are
    inferred.  ``child_codes`` limits the rule to newly imported rows so a
    relation deleted by the user is not recreated by a later import.
    """
    is_valid = is_valid_code or (lambda code: bool(code))
    batch_items = {}
    for item in items or []:
        if str(item.get("kind") or "") != "component":
            continue
        code = normalize_code(item.get("code"))
        if code and is_valid(code):
            batch_items.setdefault(code, item)
    batch_codes = set(batch_items)
    existing_own_children = {
        normalize_code(relation.get("child_code"))
        for relation in relations or []
        if normalize_code(relation.get("child_code"))
        and normalize_link_type(relation.get("link_type")) == "own"
    }
    targets = (
        {normalize_code(code) for code in child_codes if normalize_code(code)}
        if child_codes is not None
        else set(batch_items)
    )

    added: list[dict] = []
    for item in items or []:
        child = normalize_code(item.get("code"))
        if (
            child not in targets
            or child not in batch_items
            or child in existing_own_children
        ):
            continue
        parent = next(
            (
                candidate
                for candidate in prefix_parent_candidates(child)
                if candidate in batch_codes
            ),
            "",
        )
        if not parent:
            continue
        entry = _clean_relation(
            {
                "parent_code": parent,
                "child_code": child,
                "link_type": default_link_type(parent, child, batch_codes),
                "source": "code_prefix",
                "auto_decided": True,
            },
            is_valid,
        )
        if entry is None:
            continue
        relations.append(entry)
        existing_own_children.add(child)
        added.append(entry)
    return added


def _dedupe_key(relation: dict) -> tuple[str, str]:
    return (
        normalize_code(relation.get("parent_code")),
        normalize_code(relation.get("child_code")),
    )


def _clean_relation(relation: dict, is_valid) -> dict | None:
    parent = normalize_code(relation.get("parent_code"))
    child = normalize_code(relation.get("child_code"))
    if not parent or not child or parent == child:
        return None
    if not is_valid(parent) or not is_valid(child):
        return None
    link_type = normalize_link_type(relation.get("link_type"))
    if not link_type:
        link_type = default_link_type(parent, child)
    return {
        "parent_code": parent,
        "child_code": child,
        "link_type": link_type,
        "source": str(relation.get("source") or "manual"),
        "manual_override": bool(relation.get("manual_override")),
        "link_status": str(relation.get("link_status") or ""),
        "note": str(relation.get("note") or ""),
        # Classification provenance must survive normalisation: a retry must
        # not silently reinterpret an own child as borrow.
        "auto_decided": bool(relation.get("auto_decided")),
        "tree_exists": relation.get("tree_exists"),
        "link_only": bool(relation.get("link_only")),
    }


def _merge_into(index: dict, order: list, entry: dict, *, force: bool = False) -> None:
    pair = (entry["parent_code"], entry["child_code"])
    existing = index.get(pair)
    if existing is None:
        index[pair] = entry
        order.append(entry)
        return
    if entry["manual_override"] and not existing.get("manual_override"):
        existing["link_type"] = entry["link_type"]
        existing["manual_override"] = True
    elif not existing.get("manual_override") and force:
        existing["link_type"] = entry["link_type"]
    if entry.get("link_status") and not existing.get("link_status"):
        existing["link_status"] = entry["link_status"]
    if entry.get("note") and not existing.get("note"):
        existing["note"] = entry["note"]
    if entry.get("link_only"):
        existing["link_only"] = True
    # Keep every origin so the UI can explain where a relation came from.
    sources = existing.setdefault("sources", [])
    for source in [existing.get("source"), entry.get("source")]:
        if source and source not in sources:
            sources.append(source)


def migrate_manifest_relations(manifest: dict, is_valid_code=None, log=None) -> list[dict]:
    """One-time migration of legacy inputs into ``manifest['relations']``.

    ``assemblies`` / ``manual_borrows`` / ``item.parent_code`` stay in the file
    as provenance only.  After this call they are never merged again, so a
    relation deleted in the UI cannot come back on the next upload.
    """
    is_valid = is_valid_code or (lambda code: bool(code))
    items = manifest.get("items") or []
    batch_codes = {
        normalize_code(item.get("code")) for item in items if item.get("code")
    }

    index: dict[tuple[str, str], dict] = {}
    order: list[dict] = []
    migrated_children: set[str] = set()

    def upsert(parent, child, *, link_type="", source="manual",
               manual_override=False, link_status="", link_only=False):
        entry = _clean_relation(
            {
                "parent_code": parent,
                "child_code": child,
                "link_type": link_type or default_link_type(parent, child, batch_codes),
                "source": source,
                "manual_override": manual_override,
                "link_status": link_status,
                "link_only": link_only,
            },
            is_valid,
        )
        if entry is None:
            return
        _merge_into(index, order, entry, force=bool(link_type))

    # Existing canonical entries win (an uploaded package may already have them).
    for relation in manifest.get("relations") or []:
        upsert(
            relation.get("parent_code"),
            relation.get("child_code"),
            link_type=normalize_link_type(relation.get("link_type")),
            source=str(relation.get("source") or "manual"),
            manual_override=bool(relation.get("manual_override")),
            link_status=str(relation.get("link_status") or ""),
            link_only=bool(relation.get("link_only")),
        )

    for item in items:
        parent = normalize_code(item.get("parent_code") or item.get("borrow_to"))
        child = normalize_code(item.get("code"))
        if parent and child and child in batch_codes:
            upsert(
                parent,
                child,
                link_type=normalize_link_type(item.get("link_type")),
                source="drag",
            )

    for entry in manifest.get("manual_borrows") or []:
        parent = normalize_code(entry.get("parent_code") or entry.get("machine"))
        for child in entry.get("codes") or []:
            upsert(parent, child, link_type="borrow", source="manual")

    for assembly in manifest.get("assemblies") or []:
        parent = normalize_code(assembly.get("machine"))
        if parent.startswith("03.GPZ."):
            continue
        for child in assembly.get("codes") or []:
            upsert(parent, child, link_type="borrow", source="z03_bom")

    for entry in order:
        migrated_children.add(entry["child_code"])

    manifest["relations"] = order
    manifest[MIGRATION_FLAG] = MIGRATION_VERSION
    manifest[MIGRATED_CHILDREN_FLAG] = sorted(migrated_children)
    manifest["legacy_relation_counts"] = {
        "relations": len(order),
        "assemblies": len(manifest.get("assemblies") or []),
        "manual_borrows": len(manifest.get("manual_borrows") or []),
    }
    if log and order:
        log("已导入 %d 条次级关系（旧包 assemblies/manual_borrows 仅保留为来源记录）" % len(order))
    return order


def _migrate_new_items(manifest: dict, is_valid, index, order) -> None:
    """Migrate items added after the first import (e.g. dropped into the GUI)."""
    migrated = set(manifest.get(MIGRATED_CHILDREN_FLAG) or [])
    batch_codes = {
        normalize_code(item.get("code")) for item in manifest.get("items") or []
    }
    changed = False
    for item in manifest.get("items") or []:
        parent = normalize_code(item.get("parent_code") or item.get("borrow_to"))
        child = normalize_code(item.get("code"))
        if not parent or not child or child not in batch_codes:
            continue
        if child in migrated:
            continue
        entry = _clean_relation(
            {
                "parent_code": parent,
                "child_code": child,
                "link_type": normalize_link_type(item.get("link_type"))
                or default_link_type(parent, child, batch_codes),
                "source": "drag",
            },
            is_valid,
        )
        if entry is not None:
            _merge_into(index, order, entry)
            migrated.add(child)
            changed = True
    if changed:
        manifest[MIGRATED_CHILDREN_FLAG] = sorted(migrated)


def normalize_manifest_relations(manifest: dict, is_valid_code=None, log=None) -> list[dict]:
    """Normalise ``manifest['relations']`` without reviving deleted entries."""
    if manifest.get(MIGRATION_FLAG) != MIGRATION_VERSION:
        migrate_manifest_relations(manifest, is_valid_code, log=log)

    is_valid = is_valid_code or (lambda code: bool(code))
    index: dict[tuple[str, str], dict] = {}
    order: list[dict] = []
    for relation in manifest.get("relations") or []:
        entry = _clean_relation(relation, is_valid)
        if entry is None:
            continue
        entry["sources"] = list(relation.get("sources") or [])
        _merge_into(index, order, entry, force=False)

    _migrate_new_items(manifest, is_valid, index, order)

    inferred_count = 0
    if not manifest.get(AUTO_PREFIX_FLAG):
        inferred = infer_prefix_relations(
            manifest.get("items") or [],
            order,
            is_valid_code=is_valid,
        )
        inferred_count = len(inferred)
        manifest[AUTO_PREFIX_FLAG] = True
        manifest["auto_prefix_relation_count"] = inferred_count
        if log and inferred_count:
            log("已按图号自动匹配 %d 条次级关系" % inferred_count)

    # A drawing has at most one ownership parent (reference=False).  Borrow
    # references (reference=True) may exist under several parents.
    last_own_by_child = {
        normalize_code(relation.get("child_code")): relation
        for relation in order
        if normalize_link_type(relation.get("link_type")) == "own"
    }
    kept = [
        relation
        for relation in order
        if normalize_link_type(relation.get("link_type")) != "own"
        or last_own_by_child.get(normalize_code(relation.get("child_code")))
        is relation
    ]
    removed_count = len(order) - len(kept)
    if removed_count and log:
        log("已合并 %d 条重复归属关系（每个子件只保留一个 reference=False 父级）"
            % removed_count)

    manifest["relations"] = kept
    return kept


def analyze_relations(relations: list[dict]) -> dict:
    """Detect explicit conflicts: cycles and a child with two ownership parents."""
    edges: dict[str, set[str]] = {}
    own_parents_by_child: dict[str, list[str]] = {}
    for relation in relations or []:
        parent = normalize_code(relation.get("parent_code"))
        child = normalize_code(relation.get("child_code"))
        if not parent or not child or parent == child:
            continue
        edges.setdefault(parent, set()).add(child)
        if normalize_link_type(relation.get("link_type")) == "own":
            own_parents_by_child.setdefault(child, [])
            if parent not in own_parents_by_child[child]:
                own_parents_by_child[child].append(parent)

    cycles: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for neighbour in sorted(edges.get(node, ())):
            if state.get(neighbour, 0) == 0:
                visit(neighbour)
            elif state.get(neighbour) == 1:
                start = stack.index(neighbour)
                cycles.append(stack[start:] + [neighbour])
        stack.pop()
        state[node] = 2

    for node in sorted(edges):
        if state.get(node, 0) == 0:
            visit(node)

    multi_parent = {
        child: parents
        for child, parents in own_parents_by_child.items()
        if len(parents) > 1
    }
    issues: list[dict] = []
    for cycle in cycles:
        issues.append(
            {
                "type": "cycle",
                "message": "关系存在循环：%s" % " → ".join(cycle),
                "codes": cycle,
            }
        )
    for child, parents in sorted(multi_parent.items()):
        issues.append(
            {
                "type": "multi_own_parent",
                "message": "子件 %s 有多个归属父级：%s" % (
                    child,
                    "、".join(parents),
                ),
                "codes": [child] + parents,
            }
        )
    return {
        "issues": issues,
        "cycles": cycles,
        "multi_own_parent": multi_parent,
        # Backwards-compatible key retained for existing callers.
        "multi_parent": multi_parent,
        "own_multi_parent": multi_parent,
        "ok": not issues,
    }


def relation_label(relation: dict) -> str:
    parent = relation.get("parent_code") or ""
    child = relation.get("child_code") or ""
    kind = "归属" if normalize_link_type(relation.get("link_type")) == "own" else "借用"
    return "%s ← %s（%s）" % (parent, child, kind)

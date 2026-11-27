"""CAXA operations: upload a PDF, create/link parts, read results back.

Ground rules (user requirements, 2026-11-05):

* the PDF that is uploaded is always the copy staged from the file the user
  selected - never a file that merely happens to have the target name;
* ``borrow`` only links parts that already exist; it never creates them;
* a new ``own`` child keeps the parent the user chose; an explicitly requested
  parent is never silently replaced by another candidate;
* a Part that was created but could not be linked is *not* a normal success;
* the document class reported by CAXA is checked; a fallback that produced a
  different class ends as "待核实/部分完成" and is not sent to the workflow.
"""

from __future__ import annotations

import os
import time

import drawing_bundle
import relations
import staging
from .bridge import CaxaBridge, CaxaError

DRAWING_2D = 0
DRAWING_CAPP = 2

#: Verified class IDs: 1002 Drawing (2D), 1003 CappDocument (process doc).
CLASS_ID_DRAWING = 1002
CLASS_ID_CAPP = 1003
CLASS_NAMES_DRAWING = ("drawing",)
CLASS_NAMES_CAPP = ("cappdocument",)

CLASS_ID_BY_KIND = {
    "component": CLASS_ID_DRAWING,
    "machine_drawing": CLASS_ID_DRAWING,
    "process_doc": CLASS_ID_CAPP,
}

STATUS_UPLOADED = "已上传"
STATUS_PARTIAL = "部分完成"
STATUS_PENDING = "待核实"
STATUS_FAILED = "失败"

GENERAL_PARTS_PRODUCT_IDENTITY = "01"
GENERAL_PARTS_PRODUCT_NAME = "通用件"


class MachineMissing(Exception):
    def __init__(self, machine):
        super().__init__(
            "整机 %s 所属产品在 CAXA 中不存在；"
            "若是03整机，请双击该03整机行填写系列产品名称后重试" % machine
        )
        self.machine = machine


class PublishedDrawingError(CaxaError):
    def __init__(self, draw_name: str):
        super().__init__("失败：图纸已发布，无法追加版次（%s）" % draw_name)
        self.draw_name = draw_name


class UploadStop(Exception):
    pass


class UnverifiedResult(CaxaError):
    """The write was attempted but its result could not be confirmed."""

    def __init__(self, message, **kwargs):
        super().__init__(message, **kwargs)
        self.response_lost = True


def _candidate_parent_codes(code: str) -> list[str]:
    normalized = str(code or "").strip().upper()
    parents = []
    current = normalized
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


def own_part_parent_ids(part_id: int, info: dict) -> list[int]:
    """Read non-borrow Part parent IDs, preferring verified link endpoints."""
    part_id = int(part_id)
    endpoint_ids = []
    for link in info.get("link_endpoints") or []:
        if bool(link.get("reference")):
            continue
        role_a = link.get("roleA") or {}
        role_b = link.get("roleB") or {}
        a_id = int(role_a.get("object_id") or 0)
        b_id = int(role_b.get("object_id") or 0)
        if a_id == part_id and b_id:
            endpoint_ids.append(b_id)
        elif b_id == part_id and a_id:
            endpoint_ids.append(a_id)
    if endpoint_ids:
        return list(dict.fromkeys(endpoint_ids))
    return [
        int(row.get("parent_id"))
        for row in info.get("parents") or []
        if not bool(row.get("reference")) and row.get("parent_id") is not None
    ]


def normalize_class_name(value) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def classify_document(result: dict) -> tuple[int | None, bool]:
    """Return (class_id, verified) from a helper upload result."""
    class_id = result.get("class_id")
    try:
        class_id = int(class_id) if class_id is not None else None
    except (TypeError, ValueError):
        class_id = None
    name = normalize_class_name(result.get("class_name") or result.get("low_class"))
    if class_id is None:
        if name in CLASS_NAMES_DRAWING:
            class_id = CLASS_ID_DRAWING
        elif name in CLASS_NAMES_CAPP:
            class_id = CLASS_ID_CAPP
    return class_id, class_id is not None


def document_type_label(class_id: int | None) -> str:
    return {CLASS_ID_DRAWING: "图纸", CLASS_ID_CAPP: "工艺文档"}.get(class_id, "未知")


def expected_class_id(kind: str, drawing_type: int) -> int:
    if drawing_type == DRAWING_CAPP or kind == "process_doc":
        return CLASS_ID_CAPP
    return CLASS_ID_DRAWING


class CaxaUploader:
    def __init__(self, bridge: CaxaBridge, log=print, on_duplicate: str = "append_iteration",
                 staging_dir: staging.TaskStaging | None = None, auto_confirm: str = "",
                 before_attach=None):
        self.bridge = bridge
        self.log = log
        self.on_duplicate = on_duplicate
        self.staging = staging_dir
        self.auto_confirm = auto_confirm or ""
        self.before_attach = before_attach
        self.borrow_actions = []  # [{machine, code, link_type, status, note}]
        self._owns_staging = False
        self._general_product = None
        self._product_cache: dict[str, dict] = {}
        self._product_index: list[dict] | None = None
        self._product_index_complete = False
        self._root_part_index_complete = False
        self._product_index_errors: list[dict] = []
        self._product_root_parts: dict[int, list[dict]] = {}
        self._product_folder_paths_cache: dict[int, dict] = {}
        self._series_folder_index_cache: dict | None = None
        self._part_cache: dict[str, dict] = {}
        self._machine_part_cache: dict[tuple[int, str], dict] = {}
        self._product_folder_links: set[tuple[int, str]] = set()

    # ---------- basic queries ----------
    @staticmethod
    def _normalize_product_folder_path(value) -> str:
        return "/".join(
            part.strip()
            for part in str(value or "").replace("\\", "/").split("/")
            if part.strip()
        )

    @classmethod
    def _product_folder_paths(cls, item: dict) -> list[str]:
        paths = []
        values = item.get("folder_paths")
        if not isinstance(values, (list, tuple)):
            values = []
        for value in list(values) + [item.get("folder_path")]:
            path = cls._normalize_product_folder_path(value)
            if path and path.casefold() not in {
                existing.casefold() for existing in paths
            }:
                paths.append(path)
        return paths

    @classmethod
    def _product_with_folder(cls, item: dict, requested_folder: str = "") -> dict:
        product = dict(item)
        paths = cls._product_folder_paths(product)
        requested = cls._normalize_product_folder_path(requested_folder)
        selected = ""
        if requested:
            for path in paths:
                if (
                    path.casefold() == requested.casefold()
                    or (
                        "/" not in requested
                        and path.casefold().endswith("/" + requested.casefold())
                    )
                ):
                    selected = path
                    break
        if not selected and paths:
            selected = paths[0]
        if not selected:
            selected = requested
        product["folder_path"] = selected
        product["folder_paths"] = paths
        return product

    def load_product_index(self, force: bool = False) -> dict:
        """Load every Product with its actual ProductFolder path once.

        ``complete`` is false when an SDK subtree could not be read.  Callers
        may still reuse a positive identity/name hit from a partial index, but
        they must not treat a partial miss as proof that a Product is absent.
        """
        if self._product_index is not None and not force:
            return {
                "products": [dict(item) for item in self._product_index],
                "complete": self._product_index_complete,
                "products_complete": self._product_index_complete,
                "parts_complete": self._root_part_index_complete,
                "errors": [dict(item) for item in self._product_index_errors],
            }

        result = self.bridge.call(
            {"cmd": "list_products", "include_root_parts": False},
            timeout=240.0,
        )
        raw_products = result.get("products")
        if not isinstance(raw_products, list):
            raise CaxaError(
                "CAXA 未返回完整的产品索引",
                stage="product_index_read",
            )

        products = []
        seen = set()
        for raw in raw_products:
            if not isinstance(raw, dict):
                continue
            try:
                object_id = int(raw["object_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if object_id in seen:
                continue
            seen.add(object_id)
            item = {
                "object_id": object_id,
                "class_id": raw.get("class_id"),
                "identity": str(raw.get("identity") or "").strip().upper(),
                "name": str(raw.get("name") or "").strip(),
                "folder_path": self._normalize_product_folder_path(
                    raw.get("folder_path")
                ),
                "folder_paths": self._product_folder_paths(raw),
                "found_in_product_index": True,
                "root_parts": list(
                    self._product_root_parts.get(object_id, [])
                ),
            }
            for part in raw.get("root_parts") or []:
                if not isinstance(part, dict):
                    continue
                try:
                    part_id = int(part["object_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                item["root_parts"].append(
                    {
                        "object_id": part_id,
                        "identity": str(part.get("identity") or "").strip().upper(),
                        "name": str(part.get("name") or "").strip(),
                        "reference": bool(part.get("reference")),
                    }
                )
            products.append(item)

        errors = result.get("errors")
        if not isinstance(errors, list):
            errors = []
        self._product_index = products
        fallback_complete = result.get("complete") is True and not errors
        self._product_index_complete = bool(
            result.get("products_complete", fallback_complete)
        )
        root_parts_included = bool(result.get("root_parts_included"))
        self._root_part_index_complete = bool(
            root_parts_included
            and result.get("parts_complete", fallback_complete)
            and all(
                int(item["object_id"]) in self._product_root_parts
                for item in products
            )
        )
        self._product_index_errors = [
            dict(item) for item in errors if isinstance(item, dict)
        ]
        self.log(
            "已读取 CAXA 产品索引：%d 个产品，%d 个产品总装件%s"
            % (
                len(products),
                sum(len(item.get("root_parts") or []) for item in products),
                "" if self._product_index_complete else
                "（部分目录读取失败，不能据此判定产品不存在）",
            )
        )
        return {
            "products": [dict(item) for item in products],
            "complete": self._product_index_complete,
            "products_complete": self._product_index_complete,
            "parts_complete": self._root_part_index_complete,
            "errors": [dict(item) for item in self._product_index_errors],
        }

    def load_product_root_parts(self, product_ids) -> dict:
        """Load root machine Parts only for selected Products."""
        ids = list(
            dict.fromkeys(
                int(value)
                for value in product_ids or ()
                if value is not None
            )
        )
        missing = [
            product_id for product_id in ids
            if product_id not in self._product_root_parts
        ]
        errors = []
        for start in range(0, len(missing), 200):
            batch = missing[start:start + 200]
            result = self.bridge.call(
                {"cmd": "product_root_parts", "product_ids": batch},
                timeout=240.0,
            )
            items = result.get("items")
            if not isinstance(items, list):
                raise CaxaError(
                    "CAXA 未返回所选产品的总装件索引",
                    stage="product_root_part_read",
                )
            returned = set()
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    product_id = int(item["product_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                returned.add(product_id)
                roots = []
                for part in item.get("root_parts") or []:
                    if not isinstance(part, dict):
                        continue
                    try:
                        part_id = int(part["object_id"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    roots.append(
                        {
                            "object_id": part_id,
                            "identity": str(
                                part.get("identity") or ""
                            ).strip().upper(),
                            "name": str(part.get("name") or "").strip(),
                            "reference": bool(part.get("reference")),
                        }
                    )
                if item.get("root_parts_error"):
                    errors.append(dict(item))
                else:
                    self._product_root_parts[product_id] = roots
            for product_id in batch:
                if product_id not in returned:
                    errors.append(
                        {
                            "product_id": product_id,
                            "root_parts_error": "产品总装件未返回",
                        }
                    )
        if self._product_index is not None:
            for item in self._product_index:
                roots = self._product_root_parts.get(item["object_id"])
                if roots is not None:
                    item["root_parts"] = list(roots)
            self._root_part_index_complete = bool(
                all(
                    int(item["object_id"]) in self._product_root_parts
                    for item in self._product_index
                )
            )
        return {
            "loaded": sum(
                product_id in self._product_root_parts
                for product_id in ids
            ),
            "requested": len(ids),
            "errors": errors,
            "complete": self._root_part_index_complete,
        }

    def _enrich_product_from_index(
        self,
        product: dict,
        requested_folder: str = "",
    ) -> dict:
        if self._product_index is None:
            return self._product_with_folder(product, requested_folder)
        object_id = product.get("object_id")
        match = None
        for item in self._product_index:
            if object_id is not None and item["object_id"] == int(object_id):
                match = item
                break
        if match is None:
            identity = str(product.get("identity") or "").strip().upper()
            matches = [
                item for item in self._product_index
                if identity and item["identity"] == identity
            ]
            if len(matches) == 1:
                match = matches[0]
        if match is not None:
            product = dict(product)
            product.update(
                folder_path=match.get("folder_path") or "",
                folder_paths=list(match.get("folder_paths") or []),
            )
        return self._product_with_folder(product, requested_folder)

    def find_product(self, identity: str):
        identity = str(identity or "").strip().upper()
        if not identity:
            return None
        cached = self._product_cache.get(identity)
        if cached is not None:
            return self._enrich_product_from_index(cached)

        # Product identity is an indexed object-store property.  Query it
        # first: recursively scanning every Product/ProductFolder can take tens
        # of seconds, while the exact query is typically sub-second.
        low = self.bridge.call(
            {
                "cmd": "query_objects",
                "class_id": 1012,
                "limit": 20,
                "where_property": "IdentityNo",
                "where_value": identity,
                "where_operator": "equal",
                "properties": ["IdentityNo", "name"],
            }
        )
        low_items = [
            item for item in low.get("items") or []
            if str(item.get("IdentityNo") or "").strip().upper() == identity
        ]
        if len(low_items) == 1:
            product = {
                "object_id": int(low_items[0]["object_id"]),
                "identity": identity,
                "name": low_items[0].get("name") or "",
                "found_by_identity": True,
            }
            product = self._enrich_product_from_index(product)
            self._product_cache[identity] = dict(product)
            return product
        if len(low_items) > 1:
            raise CaxaError(
                "图号 %s 对应 %d 个产品，请先人工清理重复产品"
                % (identity, len(low_items)),
                stage="product_identity_duplicate",
            )

        # Numeric/drawing identities stay on the indexed path.  A nested
        # ProductFolder lookup is handled by find_product_in_folder() only when
        # the caller knows the target folder.
        if identity[0].isdigit():
            return None

        r = self.bridge.call(
            {"cmd": "find_product", "keyword": identity, "limit": 20}
        )
        found = r.get("found", [])
        for p in found:
            if (p.get("identity") or "").upper() == identity:
                p = self._enrich_product_from_index(p)
                self._product_cache[identity] = dict(p)
                return p
        for p in found:
            if (p.get("name") or "").strip().upper() == identity:
                p = self._enrich_product_from_index(p)
                self._product_cache[identity] = dict(p)
                return p
        for p in found:
            if identity in (p.get("name") or "").upper():
                p = self._enrich_product_from_index(p)
                self._product_cache[identity] = dict(p)
                return p
        self.log("未找到整机产品 %s，候选：%s" % (
            identity, [(x.get("identity"), x.get("name")) for x in found[:10]]))
        return None

    def find_product_in_folder(self, identity: str, folder_path: str):
        """Find an exact Product inside one ProductFolder subtree.

        This is the bounded fallback for products nested under folders: it
        scans only the requested folder tree instead of all Products.
        """
        identity = str(identity or "").strip().upper()
        folder_path = str(folder_path or "").strip().replace("\\", "/").strip("/")
        if not identity or not folder_path:
            return None
        result = self.bridge.call(
            {
                "cmd": "find_product_in_folder",
                "identity": identity,
                "folder_path": folder_path,
            }
        )
        found = result.get("found")
        if not isinstance(found, list):
            raise CaxaError(
                "CAXA 未返回完整的系列目录产品查询结果：%s" % folder_path,
                stage="product_folder_read",
            )
        if len(found) > 1:
            raise CaxaError(
                "图号 %s 在系列目录 %s 下对应 %d 个产品，请先人工清理重复产品"
                % (identity, folder_path, len(found)),
                stage="product_identity_duplicate",
            )
        if len(found) == 1:
            product = {
                "object_id": int(found[0]["object_id"]),
                "identity": identity,
                "name": found[0].get("name") or "",
                "found_in_folder": True,
                "folder_path": self._normalize_product_folder_path(
                    found[0].get("folder_path") or folder_path
                ),
                "folder_paths": [
                    self._normalize_product_folder_path(
                        found[0].get("folder_path") or folder_path
                    )
                ],
            }
            self._product_cache[identity] = dict(product)
            return product
        return None

    def find_products_by_name(self, name: str) -> list[dict]:
        """Return Products whose Name is exactly ``name``.

        CAXA Product names are not guaranteed to be unique, so callers must
        disambiguate by the expected ProductFolder when multiple rows exist.
        """
        name = str(name or "").strip()
        if not name:
            return []
        result = self.bridge.call(
            {"cmd": "query_product", "keyword": name}
        )
        products = []
        seen = set()
        for item in result.get("items") or []:
            if str(item.get("name") or "").strip().casefold() != name.casefold():
                continue
            try:
                product_id = int(item["object_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if product_id in seen:
                continue
            seen.add(product_id)
            product = {
                "object_id": product_id,
                "identity": str(item.get("identity") or "").strip().upper(),
                "name": str(item.get("name") or "").strip(),
                "found_by_name_query": True,
            }
            try:
                folders = self.product_folder_paths(product_id)
            except CaxaError:
                folders = {"paths": [], "complete": False}
            product["folder_paths"] = list(folders.get("paths") or [])
            product["folder_path"] = (
                product["folder_paths"][0]
                if product["folder_paths"]
                else ""
            )
            products.append(product)
        return products

    def _product_in_folder(self, product: dict, folder_id) -> bool:
        try:
            target_id = int(folder_id)
        except (TypeError, ValueError):
            return False
        result = self.bridge.call(
            {
                "cmd": "dump_product_by_id",
                "product_id": int(product["object_id"]),
            }
        )
        product_payload = result.get("product") or {}
        for child in product_payload.get("children") or []:
            if str(child.get("kind") or "") != "product_folder":
                continue
            try:
                if int(child.get("object_id")) == target_id:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _root_part_matches(
        self,
        machine: str,
        folder_name: str,
        product_ids=None,
    ) -> list[dict]:
        allowed = None if product_ids is None else {
            int(value) for value in product_ids
        }
        matches = []
        for item in self._product_index or []:
            if allowed is not None and item["object_id"] not in allowed:
                continue
            for part in item.get("root_parts") or []:
                if part.get("identity") != machine:
                    continue
                product = self._product_with_folder(item, folder_name)
                product["found_by_root_part"] = True
                product["matched_product_part"] = dict(part)
                matches.append(product)
                break
        return matches

    def _candidate_product_ids(
        self,
        machine: str,
        product_name: str,
        folder_name: str,
    ) -> list[int]:
        machine_stem = (
            machine.rsplit("-", 1)[0] if "-" in machine else machine
        )
        candidates = []
        for item in self._product_index or []:
            identity = str(item.get("identity") or "")
            identity_stem = (
                identity.rsplit("-", 1)[0] if "-" in identity else identity
            )
            if (
                (identity and machine_stem.startswith(identity))
                or (identity and identity.startswith(machine_stem))
                or (
                    identity_stem
                    and machine.startswith(identity_stem)
                )
                or (
                    product_name
                    and str(item.get("name") or "").strip().casefold()
                    == product_name.casefold()
                )
                or (
                    folder_name
                    and any(
                        path.casefold() == folder_name.casefold()
                        or (
                            "/" not in folder_name
                            and path.casefold().endswith(
                                "/" + folder_name.casefold()
                            )
                        )
                        for path in item.get("folder_paths") or []
                    )
                )
            ):
                candidates.append(int(item["object_id"]))
        return list(dict.fromkeys(candidates))

    def product_folder_paths(self, product_id: int) -> dict:
        """Read actual ProductFolder paths for one Product."""
        product_id = int(product_id)
        cached = self._product_folder_paths_cache.get(product_id)
        if cached is not None:
            return dict(cached)
        result = self.bridge.call(
            {"cmd": "product_folder_paths", "product_id": product_id}
        )
        paths = []
        for value in result.get("paths") or []:
            path = self._normalize_product_folder_path(value)
            if path and path.casefold() not in {
                existing.casefold() for existing in paths
            }:
                paths.append(path)
        normalized = {
            "product_id": product_id,
            "paths": paths,
            "complete": result.get("complete") is True,
            "errors": list(result.get("errors") or []),
        }
        self._product_folder_paths_cache[product_id] = normalized
        return dict(normalized)

    def load_series_folder_index(self, force: bool = False) -> dict:
        """Load the fixed ProductFolder tree once for each helper session."""
        if not force and self._series_folder_index_cache is not None:
            return dict(self._series_folder_index_cache)
        if self.bridge is None:
            raise CaxaError(
                "没有 CAXA 会话，无法读取系列产品目录",
                stage="series_folder_index",
            )
        result = self.bridge.call({"cmd": "product_folder_index"})
        if result.get("complete") is not True:
            errors = list(result.get("errors") or [])
            detail = "; ".join(
                "%s: %s"
                % (
                    row.get("folder_path") or "未知目录",
                    row.get("detail") or row.get("area") or "",
                )
                for row in errors[:20]
                if isinstance(row, dict)
            )
            raise CaxaError(
                "CAXA 系列产品目录读取不完整，不能安全自动匹配",
                stage="series_folder_index",
                detail=detail,
            )
        items = [
            dict(row) for row in result.get("items") or []
            if isinstance(row, dict)
        ]
        self._series_folder_index_cache = {
            "items": items,
            "complete": True,
            "errors": [],
        }
        self.log("已读取 CAXA 系列产品目录：%d 项（会话内缓存）" % len(items))
        return dict(self._series_folder_index_cache)

    def resolve_series_product_folder(
        self,
        machine: str,
        requested_folder: str = "",
    ) -> dict:
        """Resolve a machine series from the live ProductFolder index.

        A user-entered path that differs from the local inferred fallback is
        authoritative. Otherwise the helper index is matched by letter aliases.
        """
        machine = drawing_bundle.normalize_code(machine)
        requested = self._normalize_product_folder_path(requested_folder)
        fallback = relations.series_product_folder(machine)
        manual = bool(
            requested
            and requested.casefold() not in {
                fallback.casefold(),
                "未指定系列".casefold(),
            }
        )
        if manual:
            return {
                "path": requested,
                "source": "manual",
                "machine": machine,
            }
        index = self.load_series_folder_index()
        matched = relations.match_series_product_folder(
            machine, list(index.get("items") or [])
        )
        if matched is not None:
            return {
                **matched,
                "path": str(matched.get("path") or ""),
                "source": "caxa_index",
                "machine": machine,
            }
        return {
            "path": fallback,
            "source": "fallback" if fallback else "manual_required",
            "machine": machine,
        }

    def products_by_machine_part(self, machine: str) -> list[dict]:
        """Use CAXA's ProductPartLink advanced query for an existing Part."""
        machine = str(machine or "").strip().upper()
        if not machine:
            return []
        result = self.bridge.call(
            {
                "cmd": "find_product_by_part",
                "identity": machine,
                "limit": 20,
            }
        )
        products = []
        seen = set()
        for item in result.get("items") or []:
            if str(item.get("class_name") or "").casefold() != "product":
                continue
            try:
                product_id = int(item["object_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if product_id in seen:
                continue
            seen.add(product_id)
            product = {
                "object_id": product_id,
                "identity": str(item.get("IdentityNo") or "").strip().upper(),
                "name": str(item.get("name") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "found_by_part_query": True,
            }
            try:
                folders = self.product_folder_paths(product_id)
            except CaxaError:
                folders = {"paths": [], "complete": False, "errors": []}
            product["folder_paths"] = list(folders.get("paths") or [])
            product["folder_path"] = (
                product["folder_paths"][0]
                if product["folder_paths"]
                else ""
            )
            product["folder_paths_complete"] = bool(
                folders.get("complete")
            )
            products.append(product)
        return products

    def find_product_for_machine(
        self,
        machine: str,
        product_name: str = "",
        product_folder: str = "",
    ):
        """Resolve a 03 Product by machine code, then exact series name.

        The machine-code identity remains the preferred key.  For legacy CAXA
        data whose Product has no IdentityNo and is identified by a shared
        series name such as ``OFT312``, reuse the exact-name Product linked to
        the expected ProductFolder.  Ambiguous exact-name matches are blocked.
        """
        machine = str(machine or "").strip().upper()

        # CAXA's own object-store query is indexed and is much cheaper than
        # walking Product/ProductFolder relations.  The machine code identifies
        # the real root Part, so use that as the first existence check.
        part_candidates = self.find_part_candidates(machine)
        if len(part_candidates) > 1:
            raise CaxaError(
                "整机图号 %s 对应 %d 个 Part，请先人工清理重复零件"
                % (machine, len(part_candidates)),
                stage="machine_part_duplicate",
            )
        if len(part_candidates) == 1:
            part = dict(part_candidates[0])
            self._part_cache[machine] = dict(part)
            self.log(
                "已按 CAXA 零部件索引确认整机零件：%s"
                % machine
            )

        if len(part_candidates) == 1:
            try:
                part_products = self.products_by_machine_part(machine)
            except CaxaError as exc:
                self.log(
                    "高级 Product 反查暂不可用，改用产品目录兜底：%s"
                    % exc
                )
            else:
                if len(part_products) > 1:
                    raise CaxaError(
                        "整机零件 %s 同时关联 %d 个 Product，"
                        "无法唯一确定所属产品"
                        % (machine, len(part_products)),
                        stage="product_root_part_duplicate",
                    )
                if len(part_products) == 1:
                    product = self._product_with_folder(
                        part_products[0], product_folder
                    )
                    product["found_machine_part"] = dict(
                        part_candidates[0]
                    )
                    self._product_cache[machine] = dict(product)
                    self.log(
                        "已用 CAXA 高级查询匹配整机：%s → %s → %s"
                        % (
                            machine,
                            product.get("folder_path") or "根目录",
                            product.get("identity")
                            or product.get("name")
                            or "未命名产品",
                        )
                    )
                    return product

        product = self.find_product(machine)
        if product is not None:
            product = self._enrich_product_from_index(product, product_folder)
            if self.bridge is not None and not product.get("folder_paths"):
                try:
                    folders = self.product_folder_paths(
                        int(product["object_id"])
                    )
                except CaxaError:
                    folders = {}
                product["folder_paths"] = list(
                    folders.get("paths") or []
                )
                if product["folder_paths"]:
                    product["folder_path"] = product["folder_paths"][0]
                    product = self._product_with_folder(
                        product, product_folder
                    )
            if len(part_candidates) == 1:
                product["found_machine_part"] = dict(part_candidates[0])
            return product

        name = str(product_name or "").strip()
        folder_name = self._normalize_product_folder_path(product_folder)
        if name:
            fast_products = self.find_products_by_name(name)
            if folder_name:
                fast_products = [
                    product for product in fast_products
                    if any(
                        path.casefold() == folder_name.casefold()
                        or (
                            "/" not in folder_name
                            and path.casefold().endswith(
                                "/" + folder_name.casefold()
                            )
                        )
                        for path in product.get("folder_paths") or []
                    )
                ]
            if len(fast_products) > 1:
                raise CaxaError(
                    "产品名称 %s 在目录 %s 中对应 %d 个 Product；"
                    "请先人工清理重复对象"
                    % (name, folder_name or "未指定", len(fast_products)),
                    stage="product_series_duplicate",
                )
            if len(fast_products) == 1:
                product = self._product_with_folder(
                    fast_products[0], folder_name
                )
                self._product_cache[machine] = dict(product)
                self._product_cache[name.upper()] = dict(product)
                self.log(
                    "已按产品名称快速匹配整机：%s → %s → %s"
                    % (
                        machine,
                        product.get("folder_path") or "根目录",
                        name,
                    )
                )
                return product

        index = self.load_product_index()
        identity_matches = [
            item for item in index.get("products") or []
            if item.get("identity") == machine
        ]
        if len(identity_matches) > 1:
            raise CaxaError(
                "图号 %s 在产品索引中对应 %d 个产品，请先人工清理重复产品"
                % (machine, len(identity_matches)),
                stage="product_identity_duplicate",
            )
        if len(identity_matches) == 1:
            product = self._product_with_folder(
                identity_matches[0], folder_name
            )
            if len(part_candidates) == 1:
                product["found_machine_part"] = dict(part_candidates[0])
            self._product_cache[machine] = dict(product)
            self.log(
                "已从产品索引匹配整机 %s → %s"
                % (machine, product.get("folder_path") or "根目录")
            )
            return product

        candidate_ids = self._candidate_product_ids(
            machine, name, folder_name
        )
        if candidate_ids:
            self.load_product_root_parts(candidate_ids)
        root_part_matches = self._root_part_matches(
            machine, folder_name, candidate_ids
        )
        if len(root_part_matches) > 1:
            own_matches = [
                item for item in root_part_matches
                if not (item.get("matched_product_part") or {}).get("reference")
            ]
            if len(own_matches) == 1:
                root_part_matches = own_matches
        if len(root_part_matches) > 1:
            raise CaxaError(
                "整机零件 %s 同时挂在 %d 个产品下，无法唯一确定所属产品；"
                "请先人工清理重复总装关系"
                % (machine, len(root_part_matches)),
                stage="product_root_part_duplicate",
            )
        if len(root_part_matches) == 1:
            product = root_part_matches[0]
            if len(part_candidates) == 1:
                product["found_machine_part"] = dict(part_candidates[0])
            self._product_cache[machine] = dict(product)
            self.log(
                "已按整机零件匹配所属产品：%s → %s → %s"
                % (
                    machine,
                    product.get("folder_path") or "根目录",
                    product.get("identity") or product.get("name") or "未命名产品",
                )
            )
            return product

        if name:
            candidates = [
                item for item in index.get("products") or []
                if str(item.get("name") or "").strip().casefold()
                == name.casefold()
            ]
            if folder_name:
                in_folder = []
                for candidate in candidates:
                    product = self._product_with_folder(
                        candidate, folder_name
                    )
                    paths = product.get("folder_paths") or []
                    if any(
                        path.casefold() == folder_name.casefold()
                        or (
                            "/" not in folder_name
                            and path.casefold().endswith(
                                "/" + folder_name.casefold()
                            )
                        )
                        for path in paths
                    ):
                        in_folder.append(product)
                if len(in_folder) > 1:
                    raise CaxaError(
                        "系列目录 %s 有 %d 个产品使用名称 %s；"
                        "请先人工清理重复对象"
                        % (folder_name, len(in_folder), name),
                        stage="product_series_duplicate",
                    )
                if len(in_folder) == 1:
                    product = in_folder[0]
                    if len(part_candidates) == 1:
                        product["found_machine_part"] = dict(part_candidates[0])
                    self._product_cache[machine] = dict(product)
                    self._product_cache[name.upper()] = dict(product)
                    return product
            if len(candidates) == 1:
                product = self._product_with_folder(
                    candidates[0], folder_name
                )
                if len(part_candidates) == 1:
                    product["found_machine_part"] = dict(part_candidates[0])
                self._product_cache[machine] = dict(product)
                self._product_cache[name.upper()] = dict(product)
                return product
            if len(candidates) > 1:
                raise CaxaError(
                    "有 %d 个产品使用名称 %s，无法从系列目录 %s 唯一确定；"
                    "请先人工清理重复对象"
                    % (len(candidates), name, folder_name or "未指定"),
                    stage="product_series_duplicate",
                )

        if folder_name:
            product = self.find_product_in_folder(machine, folder_name)
            if product is not None:
                if len(part_candidates) == 1:
                    product["found_machine_part"] = dict(part_candidates[0])
                return product

        if not self._root_part_index_complete:
            self.load_product_root_parts(
                item["object_id"] for item in index.get("products") or []
            )
            root_part_matches = self._root_part_matches(
                machine, folder_name
            )
            if len(root_part_matches) > 1:
                own_matches = [
                    item for item in root_part_matches
                    if not (
                        item.get("matched_product_part") or {}
                    ).get("reference")
                ]
                if len(own_matches) == 1:
                    root_part_matches = own_matches
            if len(root_part_matches) > 1:
                raise CaxaError(
                    "整机零件 %s 同时挂在 %d 个产品下，无法唯一确定所属产品；"
                    "请先人工清理重复总装关系"
                    % (machine, len(root_part_matches)),
                    stage="product_root_part_duplicate",
                )
            if len(root_part_matches) == 1:
                product = root_part_matches[0]
                if len(part_candidates) == 1:
                    product["found_machine_part"] = dict(part_candidates[0])
                self._product_cache[machine] = dict(product)
                self.log(
                    "已按整机零件匹配所属产品：%s → %s → %s"
                    % (
                        machine,
                        product.get("folder_path") or "根目录",
                        product.get("identity")
                        or product.get("name")
                        or "未命名产品",
                    )
                )
                return product

        if not (
            self._product_index_complete
            and self._root_part_index_complete
        ):
            raise CaxaError(
                "CAXA 产品/总装件索引读取不完整，无法确认整机 %s 是否已存在；"
                "请恢复目录权限后重新预检" % machine,
                stage="product_index_read",
                detail="; ".join(
                    "%s: %s"
                    % (
                        item.get("folder_path") or "未知目录",
                        item.get("detail") or item.get("area") or "",
                    )
                    for item in self._product_index_errors
                ),
            )
        self.log("未找到整机产品 %s" % machine)
        return None

    def find_product_folder(self, name: str):
        name = str(name or "").strip()
        if not name:
            return None
        r = self.bridge.call(
            {"cmd": "find_product_folder", "keyword": name, "limit": 20}
        )
        for folder in r.get("found") or []:
            if (folder.get("name") or "").strip().casefold() == name.casefold():
                return folder
        for folder in r.get("found") or []:
            if (folder.get("identity") or "").strip().casefold() == name.casefold():
                return folder
        normalized = name.replace("\\", "/").strip("/").casefold()
        for folder in r.get("found") or []:
            path = str(folder.get("path") or "").replace("\\", "/").strip("/")
            if path.casefold() == normalized:
                return folder
        return None

    def require_product_folder_path(self, name: str):
        name = str(name or "").strip().replace("\\", "/").strip("/")
        if not name:
            raise CaxaError(
                "03整机的系列产品目录不能为空",
                stage="product_folder",
            )
        folder = self.find_product_folder(name)
        if folder is None:
            raise CaxaError(
                "CAXA 中不存在系列产品目录 %s；上传助手不允许创建系列目录，"
                "请先在 CAXA 设计树中建立或修正系列目录" % name,
                stage="product_folder",
            )
        return folder

    def find_part_candidates(self, identity: str) -> list[dict]:
        identity = str(identity or "").strip().upper()
        if not identity or self.bridge is None:
            return []
        r = self.bridge.call({"cmd": "query_parts", "keyword": identity})
        return [
            dict(it)
            for it in r.get("items") or []
            if (it.get("identity") or "").strip().upper() == identity
        ]

    def find_part(self, identity: str):
        identity = str(identity or "").strip().upper()
        if not identity:
            return None
        cached = self._part_cache.get(identity)
        if cached is not None:
            return dict(cached)
        candidates = self.find_part_candidates(identity)
        if candidates:
            self._part_cache[identity] = dict(candidates[0])
            return candidates[0]
        return None

    def existing_machine_part(self, machine: str):
        """Return one existing 03 machine Part without resolving its Product."""
        machine = str(machine or "").strip().upper()
        if not machine:
            return None
        cached = self._part_cache.get(machine)
        if cached is not None:
            return dict(cached)
        candidates = self.find_part_candidates(machine)
        if len(candidates) > 1:
            raise CaxaError(
                "整机图号 %s 对应 %d 个 Part，请先人工清理重复零件"
                % (machine, len(candidates)),
                stage="machine_part_duplicate",
            )
        if not candidates:
            return None
        part = dict(candidates[0])
        self._part_cache[machine] = dict(part)
        return part

    def part_drawings(self, part_id: int, strict: bool = False,
                      refresh: bool = False):
        """Read the drawing list.  ``None`` = unknown (query failed)."""
        try:
            r = self.bridge.call({
                "cmd": "part_drawings",
                "part_id": int(part_id),
                "refresh": bool(refresh),
            })
        except CaxaError:
            if strict:
                raise
            return None
        drawings = r.get("drawings")
        if drawings is None:
            return [] if not strict else None
        return list(drawings)

    def part_info(self, part_id: int) -> dict:
        """Raw part_info; raises when the helper itself failed."""
        return self.bridge.call({"cmd": "part_info", "part_id": int(part_id)})

    def part_children(self, part_id: int) -> list[dict]:
        """Read child links (id, reference); raises when this is unknown."""
        return list(self.part_info(part_id).get("children") or [])

    def machine_part(self, product_id, identity: str):
        identity = str(identity or "").strip().upper()
        key = (int(product_id), identity)
        cached = self._machine_part_cache.get(key)
        if cached is not None:
            return dict(cached)
        try:
            part = self.bridge.call(
                {"cmd": "product_root_part", "product_id": product_id, "identity": identity}
            )
        except CaxaError:
            return None
        self._machine_part_cache[key] = dict(part)
        return part

    def create_part(self, identity: str, name: str = ""):
        r = self.bridge.call({"cmd": "create_part", "identity_no": identity, "name": name})
        part = {"object_id": r["object_id"], "identity": r["identity"]}
        self._part_cache[str(identity or "").strip().upper()] = dict(part)
        return part

    def ensure_part_material_code(self, part_id: int, identity: str):
        if self.bridge is None:
            # Unit/acceptance doubles may exercise upload orchestration without
            # a live helper; production always supplies a bridge.
            return {
                "part_id": int(part_id),
                "identity": str(identity or "").strip(),
                "material_no": str(identity or "").strip(),
                "changed": False,
                "skipped": True,
            }
        return self.bridge.call(
            {
                "cmd": "ensure_part_material_code",
                "part_id": int(part_id),
                "identity": str(identity or "").strip(),
            }
        )

    def create_product(self, identity: str, name: str, folder_name: str = ""):
        r = self.bridge.call(
            {
                "cmd": "create_product",
                "identity_no": identity,
                "name": name,
                "product_folder": str(folder_name or "").strip(),
            }
        )
        product = {
            "object_id": r["object_id"],
            "identity": r.get("identity") or identity,
            "name": r.get("name") or name,
            "created_new_product": True,
            "product_folder": r.get("product_folder") or folder_name,
            "product_folder_id": r.get("product_folder_id"),
            "product_folder_created": bool(r.get("product_folder_created")),
            "created_product_folders": list(
                r.get("created_product_folders") or []
            ),
        }
        # A just-inserted CAXA product may not appear in a subsequent
        # GetRootProducts/tree scan within the same task.  Reuse the returned
        # product for dependent 03 process documents and borrow links.
        self._product_cache[str(product["identity"]).upper()] = dict(product)
        product_name = str(product.get("name") or "").strip()
        if product_name:
            self._product_cache[product_name.upper()] = dict(product)
        return product

    def ensure_product_folder(self, product: dict, folder_name: str) -> dict:
        """Ensure an existing product is linked into the expected folder."""
        folder_name = str(folder_name or "").strip().replace("\\", "/")
        if not folder_name:
            return {}
        product_id = int(product["object_id"])
        key = (product_id, folder_name.casefold())
        if key in self._product_folder_links:
            return {
                "product_id": product_id,
                "product_folder": folder_name,
                "already_linked": True,
            }
        self.require_product_folder_path(folder_name)
        result = self.bridge.call(
            {
                "cmd": "link_product_to_folder",
                "product_id": product_id,
                "product_folder": folder_name,
            }
        )
        self._product_folder_links.add(key)
        return result

    def general_parts_product(self):
        """Return Product 01 通用件, the single ownership home for components."""
        if self._general_product is None:
            product = self.find_product(GENERAL_PARTS_PRODUCT_IDENTITY)
            if product is None:
                product = self.find_product(GENERAL_PARTS_PRODUCT_NAME)
            if product is None:
                raise CaxaError(
                    "CAXA 中找不到通用件产品（代号 %s，名称 %s）"
                    % (
                        GENERAL_PARTS_PRODUCT_IDENTITY,
                        GENERAL_PARTS_PRODUCT_NAME,
                    ),
                    stage="general_product",
                )
            self._general_product = product
        return self._general_product

    def ensure_half_product_part(self, machine: str, part_name: str = ""):
        """Reuse or create a 02 leaf using its nearest dot-number parent."""
        machine = drawing_bundle.normalize_code(machine)
        group = drawing_bundle.half_product_group_code(machine)
        if not machine.startswith("02.") or not group:
            raise CaxaError(
                "无效的 02 半成品图号：%s" % (machine or "空"),
                stage="half_product_group",
            )
        product = self.find_product("02")
        if product is None:
            raise CaxaError(
                "CAXA 中找不到产品「02 半成品」，未创建 02 层级",
                stage="half_product_group",
            )

        candidates = self.find_part_candidates(machine)
        if not candidates:
            existing = self.find_part(machine)
            if existing is not None:
                candidates = [existing]
        if len(candidates) > 1:
            raise CaxaError(
                "02 半成品 %s 在 CAXA 中存在 %d 个原件，请先人工清理"
                % (machine, len(candidates)),
                stage="half_product_duplicate",
            )

        part = candidates[0] if candidates else None
        parent_identity = ""
        parent_part = None
        for parent_code in drawing_bundle.half_product_parent_codes(machine):
            parent_candidates = self.find_part_candidates(parent_code)
            if not parent_candidates:
                existing_parent = self.find_part(parent_code)
                if existing_parent is not None:
                    parent_candidates = [existing_parent]
            if not parent_candidates:
                continue
            if len(parent_candidates) > 1:
                raise CaxaError(
                    "02 半成品上级 %s 存在多个原件，请先人工清理"
                    % parent_code,
                    stage="half_product_duplicate",
                )
            parent_identity = parent_code
            parent_part = dict(parent_candidates[0])
            break

        if part is None:
            if group == machine:
                raise CaxaError(
                    "02 半成品分组 %s 不存在；只能自动建立其下的 .* 层级"
                    % group,
                    stage="half_product_group",
                )
            if parent_part is None:
                raise CaxaError(
                    "02 半成品 %s 找不到图号层级中已存在的上级零件；"
                    "请先建立上级层级，助手只自动建立末级 %s"
                    % (machine, machine),
                    stage="half_product_group",
                )
            part = self.create_part(machine, part_name or machine)
            part = dict(part)
            part["created_new_part"] = True
            self.link_part(
                parent_part["object_id"],
                part["object_id"],
                reference=False,
            )
        elif group != machine and parent_part is None:
            raise CaxaError(
                "02 半成品 %s 找不到图号层级中已存在的上级零件"
                % machine,
                stage="half_product_group",
            )
        elif parent_part is not None:
            part = dict(part)
            part_id = int(part.get("object_id") or part.get("part_id"))
            info = self.part_info(part_id)
            target_own_parents = [
                row for row in info.get("parents") or []
                if not bool(row.get("reference"))
            ]
            own_parent_ids = own_part_parent_ids(part_id, info)
            own_products = [
                row for row in info.get("products") or []
                if not bool(row.get("reference"))
            ]
            own_parent_count = max(
                len(target_own_parents),
                len(own_parent_ids),
            )
            if own_parent_count + len(own_products) > 1:
                raise CaxaError(
                    "02 半成品 %s 存在多个非借用归属关系，请先人工清理"
                    % machine,
                    stage="half_product_duplicate",
                )
            matching_parent = int(parent_part["object_id"]) in own_parent_ids
            if not matching_parent:
                parent_info = self.part_info(int(parent_part["object_id"]))
                for link in parent_info.get("link_endpoints") or []:
                    if bool(link.get("reference")):
                        continue
                    role_a = link.get("roleA") or {}
                    role_b = link.get("roleB") or {}
                    ids = {
                        int(role_a.get("object_id") or 0),
                        int(role_b.get("object_id") or 0),
                    }
                    if int(parent_part["object_id"]) in ids and part_id in ids:
                        matching_parent = True
                        break
            if own_parent_count and not matching_parent:
                raise CaxaError(
                    "02 半成品 %s 的归属上级不是最近的 %s，请先人工修正"
                    % (machine, parent_identity),
                    stage="half_product_parent",
                )
            if own_products:
                raise CaxaError(
                    "02 半成品 %s 直接归属产品，不是 %s 下的"
                    "点号层级，请先人工修正"
                    % (machine, parent_identity),
                    stage="half_product_parent",
                )
            if not own_parent_count:
                self.link_part(
                    parent_part["object_id"],
                    part_id,
                    reference=False,
                )

        part = dict(part)
        part["linked_parent_identity"] = parent_identity
        return product, part

    def link_part(self, parent_id, child_id, reference: bool, product_link: bool = False):
        return self.bridge.call({
            "cmd": "link_part",
            "parent_id": parent_id,
            "child_id": child_id,
            "reference": reference,
            "product_link": product_link,
        })

    def _find_matching_drawing(self, drawings: list[dict], name: str, draw_no: str) -> dict | None:
        target_name = (name or "").strip().upper()
        target_no = (draw_no or "").strip().upper()
        for dr in drawings:
            dr_name = str(dr.get("name") or "").strip().upper()
            dr_no = str(dr.get("draw_no") or "").strip().upper()
            if (target_name and dr_name == target_name) or (target_no and dr_no == target_no):
                return dr
        return None

    def _repair_legacy_pdf_name(
        self, part_id: int, drawings: list[dict], target_name: str
    ) -> list[dict]:
        """Append the missing .pdf suffix through CAXA property editing.

        This is only done when the existing name equals the target name after
        removing exactly one trailing ``.pdf``.  Any other name difference is
        left untouched.  The rename is read back before upload continues.
        """
        target = str(target_name or "").strip()
        if not target.lower().endswith(".pdf"):
            return drawings
        expected_without_pdf = target[:-4].strip().upper()
        repaired = 0
        for drawing in drawings:
            current = str(drawing.get("name") or "").strip()
            if not current or current.lower().endswith(".pdf"):
                continue
            if current.upper() != expected_without_pdf:
                continue
            object_id = int(drawing.get("object_id") or 0)
            if object_id <= 0:
                continue
            try:
                result = self.bridge.call({
                    "cmd": "set_drawing",
                    "object_id": object_id,
                    "name": target,
                })
            except Exception as exc:
                raise UnverifiedResult(
                    "检测到历史图纸名称缺少 .pdf，但自动补名未确认：%s" % exc,
                    stage="repair_legacy_pdf_name",
                    result={"object_id": object_id, "name": current},
                    partial=True,
                ) from exc
            actual = str(
                result.get("name_after")
                or result.get("name")
                or ""
            ).strip()
            if actual.upper() != target.upper():
                raise UnverifiedResult(
                    "历史图纸自动补 .pdf 后回读名称不一致",
                    stage="repair_legacy_pdf_name",
                    result={"object_id": object_id, "name_after": actual},
                    partial=True,
                )
            drawing["name"] = actual
            repaired += 1
            self.log(
                "历史图纸 %s 名称已自动补全为 %s"
                % (object_id, actual)
            )
        if not repaired:
            return drawings
        refreshed = self.part_drawings(part_id, strict=True, refresh=True)
        if refreshed is None:
            raise UnverifiedResult(
                "历史图纸补 .pdf 后无法刷新图纸列表",
                stage="repair_legacy_pdf_name",
                partial=True,
            )
        return refreshed

    def attach_file(self, part_id, file_path, drawing_type, name, draw_no="",
                    on_duplicate: str = "", kind: str = ""):
        effective_dup = on_duplicate or self.on_duplicate
        before_drawings = self.part_drawings(part_id)
        if before_drawings is None:
            raise UnverifiedResult(
                "上传前无法读取该零件的图纸列表，无法确认是否会重复/追加",
                stage="pre_upload_query",
            )
        before_drawings = self._repair_legacy_pdf_name(
            part_id, before_drawings, name
        )
        existing_dr = self._find_matching_drawing(before_drawings, name, draw_no)
        if existing_dr is not None and effective_dup == "append_iteration":
            state_text = str(existing_dr.get("state") or "").strip()
            if "发布" in state_text:
                raise PublishedDrawingError(existing_dr.get("name") or name or draw_no)

        # Persist the old version before sending the write. A timeout (or
        # process exit) cannot return this evidence from the helper.
        if self.before_attach is not None:
            baseline = {
                "target_filename": name,
                "append_iteration": (
                    existing_dr is not None and effective_dup == "append_iteration"
                ),
            }
            if baseline["append_iteration"]:
                baseline["before_iterator_no"] = existing_dr.get("iterator_no")
                baseline["before_history_count"] = existing_dr.get("history_count")
            self.before_attach(draw_no, baseline)

        # Auto-confirm is enabled for exactly this upload command only, and the
        # helper further restricts it to windows owned by its own process while
        # the upload call is active.
        auto_enabled = False
        if (
            self.auto_confirm == "append_iteration"
            and effective_dup == "append_iteration"
            and hasattr(self.bridge, "set_auto_confirm")
        ):
            try:
                self.bridge.set_auto_confirm(
                    True,
                    mode="append_iteration",
                    request_id=getattr(self.staging, "task_id", "") or "",
                    target_name=name,
                )
                auto_enabled = True
            except Exception as ex:
                self.log("未能启用重复对话框自动确认（改为人工确认）：%s" % ex)
        try:
            r = self.bridge.call({
                "cmd": "upload",
                "parent_kind": "part",
                "parent_id": part_id,
                "file_path": file_path,
                "draw_no": draw_no,
                "name": name,
                "drawing_type": drawing_type,
                "on_duplicate": effective_dup,
            })
        finally:
            if auto_enabled:
                try:
                    self.bridge.set_auto_confirm(False, mode="append_iteration")
                except Exception:
                    pass
        if not r.get("attached"):
            # The helper may have written before returning attached=false.
            raise UnverifiedResult(
                "CAXA 未确认文档挂接结果，可能已写入，请核实",
                stage="upload_verify",
                result=dict(r),
                partial=bool(r.get("object_id")),
            )

        method = str(r.get("method") or "")
        # 1) new_version must prove that the file was really replaced.
        diag = r.get("diag") or {}
        if method == "new_version" and diag.get("new_version_copy_failed"):
            raise UnverifiedResult(
                "新增版次未确认：覆盖检出文件失败（未检入）",
                stage="new_version",
                result=dict(r),
                partial=True,
            )
        if method == "append_iteration_fallback_reuse":
            # Policy asked for a new iteration; CAXA silently reused.  Verify.
            pass

        # 2) Every append path (not only fallback_reuse) needs real evidence
        #    that the iteration/history increased for an existing drawing.
        if existing_dr is not None and effective_dup == "append_iteration":
            try:
                after_drawings = self.part_drawings(part_id, refresh=True)
            except TypeError:
                # Compatibility with lightweight test doubles that predate
                # the refresh flag.
                after_drawings = self.part_drawings(part_id)
            if after_drawings is None:
                raise UnverifiedResult(
                    "追加新版次结果无法确认：回读图纸列表失败",
                    stage="upload_verify",
                    result=dict(r),
                    partial=True,
                )
            after_dr = self._find_matching_drawing(after_drawings, name, draw_no)
            if after_dr is None:
                raise UnverifiedResult(
                    "追加新版次结果无法确认：回读不到该图纸",
                    stage="upload_verify",
                    result=dict(r),
                    partial=True,
                )
            before_cnt = int(existing_dr.get("history_count") or 0)
            after_cnt = int(after_dr.get("history_count") or 0)
            before_itr = str(existing_dr.get("iterator_no") or "")
            after_itr = str(after_dr.get("iterator_no") or "")
            diag["after_iterator_no"] = after_itr
            diag["after_history_count"] = after_cnt
            if after_cnt <= before_cnt and after_itr == before_itr:
                # CAXA may expose the new history row before advancing the
                # current iterator in the existing EAP session.  A short delay
                # and a second refresh is sufficient; restarting the helper
                # costs another CAXA login and is unnecessary.
                time.sleep(3.0)
                try:
                    refreshed = self.part_drawings(part_id, refresh=True)
                except TypeError:
                    refreshed = self.part_drawings(part_id)
                if refreshed is not None:
                    refreshed_dr = self._find_matching_drawing(
                        refreshed, name, draw_no
                    )
                    if refreshed_dr is not None:
                        after_cnt = int(
                            refreshed_dr.get("history_count") or 0
                        )
                        after_itr = str(
                            refreshed_dr.get("iterator_no") or ""
                        )
                        diag["after_iterator_no"] = after_itr
                        diag["after_history_count"] = after_cnt
                        diag["readback_retry_delay_seconds"] = 3.0
            if after_cnt <= before_cnt and after_itr == before_itr:
                r["diag"] = diag
                raise UnverifiedResult(
                    "追加新版次结果无法确认：版次未增加（当前版次 %s）"
                    % (after_itr or "未知"),
                    stage="upload_verify",
                    result=dict(r),
                    partial=True,
                )
            r["reused"] = False
            r["new_version"] = True
            r["method"] = "append_iteration"
            r["iterator_no"] = after_itr
            r["diag"] = diag
        elif method == "reuse" and effective_dup == "append_iteration":
            raise UnverifiedResult(
                "重复策略要求追加版次，但 CAXA 返回复用旧文档，未获版次证据",
                stage="upload_verify",
                result=dict(r),
            )

        class_id, verified = classify_document(r)
        expected = expected_class_id(kind, drawing_type)
        r["class_id"] = class_id
        r["class_verified"] = bool(verified and class_id == expected)
        if not r["class_verified"]:
            raise UnverifiedResult(
                "上传返回的文档类型不是预期类型（实际 %s / 期望 %s）"
                % (document_type_label(class_id), document_type_label(expected)),
                stage="upload_verify",
                result=r,
            )

        extra = []
        if r.get("reused"):
            extra.append("复用已有文档")
        if r.get("new_version"):
            extra.append("追加新版次")
        if (r.get("diag") or {}).get("append_iteration_requested") and not r.get("reused"):
            extra.append("重复框确认追加新版次")
        self.log("  %s挂接成功%s" % (
            document_type_label(class_id),
            ("（" + "，".join(extra) + "）") if extra else "",
        ))
        return r

    # ---------- business orchestration ----------
    def ensure_machine(
        self,
        machine: str,
        part_name: str = "",
        create_part: bool = True,
        product_name: str = "",
        product_folder: str = "",
    ):
        """Ensure the whole-machine Product and Part.

        A missing 03 Product is created only when ``product_name`` is supplied
        and its series folder already exists.  A created-but-unlinked machine
        part carries ``link_failed`` so callers do not treat it as a normal
        success.
        """
        existing_part = self.existing_machine_part(machine)
        if existing_part is not None:
            existing_part["created_new_part"] = False
            existing_part["link_failed"] = False
            self.log(
                "复用已有整机零件：%s（不解析或移动 Product）"
                % machine
            )
            return {}, existing_part

        product_folder = (
            self.resolve_series_product_folder(
                machine,
                requested_folder=str(product_folder or "").strip(),
            ).get("path") or ""
        )
        product_name = str(product_name or "").strip()
        if not product_name:
            raise MachineMissing(machine)

        product = self.find_product_for_machine(
            machine,
            product_name=product_name,
            product_folder=product_folder,
        )
        if product is None:
            folder_result = {}
            if product_folder:
                folder_result = self.require_product_folder_path(product_folder)
            product = self.create_product(
                machine, product_name, folder_name=product_folder
            )
            self.log(
                "已创建系列产品：%s（产品代号 %s，目录 %s）"
                % (product_name, machine, product_folder or "根目录")
            )
        else:
            product = self._enrich_product_from_index(product, product_folder)
            actual_folder = str(product.get("folder_path") or "").strip()
            if actual_folder:
                self.log(
                    "复用已有整机产品：%s → %s（保持原目录）"
                    % (machine, actual_folder)
                )
        part = self.machine_part(product["object_id"], machine)
        if part is None:
            if not create_part:
                raise CaxaError("整机 %s 存在但缺少整机零件节点" % machine)
            part = self.create_part(machine, part_name)
            part = dict(part)
            part["created_new_part"] = True
            try:
                self.link_part(
                    product["object_id"], part["object_id"],
                    reference=False, product_link=True,
                )
                part["link_failed"] = False
            except CaxaError as ex:
                part["link_failed"] = True
                part["link_error"] = str(ex)
                self.log("整机零件已建但挂接失败：%s" % ex)
        else:
            part = dict(part)
            part["link_failed"] = False
        self._machine_part_cache[
            (int(product["object_id"]), str(machine).strip().upper())
        ] = dict(part)
        self._part_cache[str(machine).strip().upper()] = dict(part)
        return product, part

    def resolve_parent_part(self, code: str, batch_codes: set, explicit_parent: str = ""):
        """Return (parent_id, parent_in_batch, parent_identity).

        An explicitly requested parent is authoritative: when it is missing the
        caller must fail - guessing another parent is forbidden.
        """
        code = drawing_bundle.normalize_code(code)
        if explicit_parent:
            ep = drawing_bundle.normalize_code(explicit_parent)
            if ep and ep != code:
                found = self.find_part(ep)
                if found is not None:
                    return found["object_id"], False, ep
                return None, ep in batch_codes, ep
            return None, False, ""
        for parent in _candidate_parent_codes(code):
            found = self.find_part(parent)
            if found is not None:
                return found["object_id"], False, parent
            if parent in batch_codes:
                return None, True, parent
        return None, False, ""

    def _stage_item(self, item, file_name: str | None):
        if self.staging is None:
            self.staging = staging.TaskStaging(log=self.log)
            self._owns_staging = True
        staged = self.staging.stage(item, file_name=file_name)
        self.staging.verify(staged)
        return staged

    def upload_item(self, item, batch_codes: set, on_machine_missing,
                    explicit_parent: str = "", relation_type: str = "",
                    staged: staging.StagedFile | None = None,
                    series_product_name: str = "",
                    series_product_folder: str = ""):
        """Upload one item; the payload always comes from a staged copy."""
        code = drawing_bundle.normalize_code(item["code"])
        name = str(item.get("name") or "").strip()
        machine = drawing_bundle.normalize_code(item.get("machine"))
        kind = str(item.get("kind") or "")
        relation_type = relations.normalize_link_type(relation_type)
        if not os.path.exists(item.get("file_path") or item.get("page_pdf") or ""):
            raise CaxaError("文件不存在: %s" % (item.get("file_path") or item.get("page_pdf")))

        drawing_type = DRAWING_CAPP if kind == "process_doc" else DRAWING_2D
        file_name = drawing_bundle.expected_drawing_filename(code, name)
        if kind in ("machine_drawing", "process_doc", "component"):
            file_name = os.path.basename(file_name)
        if staged is None:
            staged = self._stage_item(item, file_name)
        else:
            file_name = staged.file_name

        product = None
        machine_part = None
        if machine and not (
            kind == "process_doc" and not machine.startswith("03.")
        ):
            # Borrow never creates a Part (neither machine root nor child).
            # A 03 process document is enough to establish the whole-machine
            # Product/Part hierarchy.  02 process documents still require the
            # existing half-product Part handled by the branch below.
            may_create = relation_type != "borrow" and (
                kind != "process_doc" or machine.startswith("03.")
            )
            try:
                product, machine_part = self.ensure_machine(
                    machine,
                    part_name=name if kind == "machine_drawing" else "",
                    create_part=may_create,
                    product_name=series_product_name,
                    product_folder=series_product_folder,
                )
            except MachineMissing:
                if kind in ("process_doc", "machine_drawing"):
                    raise
                self.log("整机 %s 不存在，组件照常上传、跳过整机借用" % machine)
                product, machine_part = None, None
            except CaxaError as ex:
                if kind in ("process_doc", "machine_drawing"):
                    raise
                self.log("整机零件不可用（%s），组件照常上传、跳过整机借用" % ex)
                product, machine_part = None, None

        if kind == "process_doc":
            if machine and not machine.startswith("03."):
                product, part = self.ensure_half_product_part(machine)
                part_id = int(
                    part.get("object_id") or part.get("part_id")
                )
                self.ensure_part_material_code(part_id, machine)
                result = self.attach_file(
                    part_id,
                    staged.path,
                    drawing_type,
                    file_name,
                    draw_no=code,
                    kind=kind,
                )
                caxa_path = "产品「02 半成品」→ 零件 %s → 工艺文档 %s" % (
                    machine,
                    file_name,
                )
                return {
                    "target": "半成品 %s 工艺文档" % machine,
                    "file_name": file_name,
                    "caxa_path": caxa_path,
                    "created_new_part": bool(part.get("created_new_part")),
                    "staged_sha256": staged.sha256,
                    "status": STATUS_UPLOADED,
                    **result,
                }
            if machine_part is None:
                raise CaxaError("工艺文档缺少整机代码或整机零件")
            if machine_part.get("link_failed"):
                raise CaxaError(
                    "整机零件已创建但未能挂接到产品 %s（%s），本次不上传工艺文档"
                    % (machine, machine_part.get("link_error") or "未知原因"),
                    stage="ensure_machine",
                    partial=True,
                )
            part_id = machine_part.get("object_id") or machine_part.get("part_id")
            # A process document such as Z03.* is attached to the machine
            # Part, but the Part's material code must remain the machine
            # drawing code (03.*), never the process-document code.
            self.ensure_part_material_code(part_id, machine)
            result = self.attach_file(
                part_id, staged.path, drawing_type, file_name, draw_no=code, kind=kind
            )
            product_folder = str((product or {}).get("folder_path") or "")
            caxa_path = "%s产品 %s（%s）→ 整机零件 %s → 文档 %s" % (
                ("目录 %s → " % product_folder) if product_folder else "",
                machine,
                (product or {}).get("name", ""),
                machine,
                file_name,
            )
            return {
                "target": "整机 %s 工艺文档" % machine,
                "file_name": file_name,
                "caxa_path": caxa_path,
                "staged_sha256": staged.sha256,
                "status": STATUS_UPLOADED,
                **result,
            }

        if kind == "machine_drawing":
            if machine_part is None:
                raise CaxaError(
                    "整机 %s 存在但缺少整机零件节点，未上传整机图纸" % machine,
                    stage="ensure_machine",
                )
            if machine_part.get("link_failed"):
                raise CaxaError(
                    "整机零件已创建但未能挂接到产品 %s（%s），本次不上传图纸"
                    % (machine, machine_part.get("link_error") or "未知原因"),
                    stage="ensure_machine",
                    partial=True,
                    result={
                        "created_part_id": machine_part.get("object_id"),
                        "part_id": machine_part.get("object_id"),
                    },
                )
            part_id = machine_part.get("object_id") or machine_part.get("part_id")
            self.ensure_part_material_code(part_id, code)
            result = self.attach_file(
                part_id, staged.path, drawing_type, file_name, draw_no=code, kind=kind
            )
            product_folder = str((product or {}).get("folder_path") or "")
            caxa_path = "%s产品 %s（%s）→ 整机零件 %s → 文档 %s" % (
                ("目录 %s → " % product_folder) if product_folder else "",
                machine,
                (product or {}).get("name", ""),
                machine,
                file_name,
            )
            return {
                "target": "整机 %s 图纸" % machine,
                "file_name": file_name,
                "caxa_path": caxa_path,
                "staged_sha256": staged.sha256,
                "status": STATUS_UPLOADED,
                **result,
            }

        if kind == "component" and code.startswith("02."):
            if relation_type == "borrow" and self.find_part(code) is None:
                raise CaxaError(
                    "借用只能链接已存在的 02 半成品：%s 在 CAXA 中不存在"
                    % code,
                    stage="borrow_missing_child",
                )
            product, part = self.ensure_half_product_part(code, name)
            part_id = int(part.get("object_id") or part.get("part_id"))
            self.ensure_part_material_code(part_id, code)
            result = self.attach_file(
                part_id,
                staged.path,
                drawing_type,
                file_name,
                draw_no=code,
                kind=kind,
            )
            return {
                "target": "半成品 %s 图纸" % code,
                "file_name": file_name,
                "caxa_path": (
                    "产品「02 半成品」→ 半成品 %s → 图纸 %s"
                    % (code, file_name)
                ),
                "created_new_part": bool(part.get("created_new_part")),
                "staged_sha256": staged.sha256,
                "status": STATUS_UPLOADED,
                **result,
            }

        # component
        explicit_parent = drawing_bundle.normalize_code(explicit_parent)
        valid_own_parent = (
            relations.is_code_prefix_parent(explicit_parent, code)
            or relations.same_general_part_series(explicit_parent, code)
        )
        if explicit_parent and not valid_own_parent:
            if relation_type == "own":
                raise CaxaError(
                    "归属父级 %s 不是图号 %s 的前缀，"
                    "也不属于同一 01.x 系列；请改用借用关系"
                    % (explicit_parent, code),
                    stage="resolve_parent",
                )
            if relation_type == "borrow":
                raise CaxaError(
                    "借用只能链接已存在的零件；应先在图号归属层级创建 %s"
                    % code,
                    stage="borrow_missing_child",
                )
            raise CaxaError(
                "指定的归属父级 %s 不是图号 %s 的前缀，"
                "也不属于同一 01.x 系列"
                % (explicit_parent, code),
                stage="resolve_parent",
            )
        parent_id, parent_in_batch, parent_identity = self.resolve_parent_part(
            code, batch_codes, explicit_parent=explicit_parent
        )
        if parent_id is None and parent_identity:
            raise CaxaError(
                "指定的父级 %s 在 CAXA 中不存在（不会改用其他父级）" % parent_identity,
                stage="resolve_parent",
            )
        if parent_id is None and parent_in_batch:
            raise CaxaError("父级 %s 在本批图纸中，请先上传父级" % parent_identity)

        part = self.find_part(code)
        created_new_part = False
        link_reference = None
        if part is None:
            if relation_type == "borrow":
                raise CaxaError(
                    "借用只能链接已存在的零件：%s 在 CAXA 中不存在，未创建" % code,
                    stage="borrow_missing_child",
                )
            part = self.create_part(code, name)
            created_new_part = True
            try:
                if parent_id is not None:
                    self.link_part(parent_id, part["object_id"], reference=False)
                    linked_parent = parent_identity
                else:
                    product = (
                        self.find_product("02")
                        if code.startswith("02.")
                        else self.general_parts_product()
                    )
                    if product is None:
                        raise CaxaError(
                            "CAXA 中找不到产品「02 半成品」",
                            stage="product_owner",
                        )
                    self.link_part(
                        product["object_id"],
                        part["object_id"],
                        reference=False,
                        product_link=True,
                    )
                    linked_parent = (
                        "02" if code.startswith("02.")
                        else GENERAL_PARTS_PRODUCT_IDENTITY
                    )
                link_reference = False
            except CaxaError as ex:
                raise CaxaError(
                    "零件 %s 已创建但归属挂接失败（%s），需要人工处理" % (code, ex),
                    stage="link_new_part",
                    partial=True,
                    result={
                        "created_part_id": part.get("object_id"),
                        "part_id": part.get("object_id"),
                        "parent_id": parent_id
                        or (self._general_product or {}).get("object_id"),
                    },
                ) from ex
        if code.startswith("02."):
            self.ensure_product_owner(part, code)
        self.ensure_part_material_code(part["object_id"], code)
        result = self.attach_file(
            part["object_id"], staged.path, drawing_type, file_name, draw_no=code, kind=kind
        )
        # Everything below is display-only: a failed lookup must not turn a
        # confirmed upload into a replayable failure.
        parent_name = ""
        prod = None
        try:
            if parent_identity:
                pp = self.find_part(parent_identity)
                parent_name = (pp or {}).get("name", "") or ""
            prod = self.find_product(
                parent_identity[:2] if len(parent_identity) >= 2 else "04"
            )
        except Exception as ex:
            self.log("上传已成功，但补充显示信息查询失败（不影响结果）：%s" % ex)
        caxa_path = "产品 %s（%s）→ 零件 %s（%s）→ 零件 %s（%s）→ 文档 %s" % (
            (prod or {}).get("identity", ""), (prod or {}).get("name", ""),
            linked_parent if created_new_part else parent_identity,
            parent_name, code, name, file_name)
        return {
            "target": "零件 %s 图纸" % code,
            "file_name": file_name,
            "caxa_path": caxa_path,
            "created_new_part": created_new_part,
            "linked_parent_identity": linked_parent if created_new_part else "",
            "link_reference": link_reference,
            "staged_sha256": staged.sha256,
            "status": STATUS_UPLOADED,
            **result,
        }

    # ---------- relations ----------
    @staticmethod
    def _as_bool(value):
        """Never interpret the string 'false' as True."""
        if value is None or value == "":
            return None
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("true", "1", "yes"):
                return True
            if text in ("false", "0", "no"):
                return False
            return None
        return bool(value)

    def _relation_children(self, parent_id: int, info: dict) -> tuple[dict[int, bool | None], str]:
        """Read positive links from either API and use complete lists for absence."""
        endpoint_states = {}
        endpoints = info.get("link_endpoints")
        endpoints_complete = (
            isinstance(endpoints, list)
            and not info.get("link_endpoints_error")
            and not info.get("link_endpoints_session")
        )
        for endpoint in endpoints if isinstance(endpoints, list) else ():
            try:
                source_id = int(endpoint["roleA"]["object_id"])
                child_id = int(endpoint["roleB"]["object_id"])
                if source_id != parent_id or child_id <= 0 or endpoint.get("link_error"):
                    raise ValueError("unresolved link")
            except (KeyError, TypeError, ValueError):
                endpoints_complete = False
                continue
            endpoint_states[child_id] = self._as_bool(endpoint.get("reference"))

        high_states = {}
        children = info.get("children")
        children_complete = isinstance(children, list) and not info.get("children_error")
        for child in children if isinstance(children, list) else ():
            try:
                candidate = child.get("sub_id") or child.get("sub_object_id")
                if not candidate:
                    candidate = child["roleB"]["object_id"]
                child_id = int(candidate)
                if child_id <= 0:
                    raise ValueError("unresolved child")
            except (AttributeError, KeyError, TypeError, ValueError):
                children_complete = False
                continue
            high_states[child_id] = self._as_bool(child.get("reference"))

        states = dict(endpoint_states)
        for child_id, reference in high_states.items():
            if child_id not in states or states[child_id] is None:
                states[child_id] = reference
            elif reference is not None and states[child_id] != reference:
                states[child_id] = None

        # The low-level pass can account for rows that had no RefSubPart
        # pointer. The complete count from the high-level pass proves coverage.
        low_covers_children = (
            endpoints_complete and isinstance(children, list) and not info.get("children_error")
            and len(endpoints) == len(children)
        )
        if info.get("refresh_error"):
            return states, str(info["refresh_error"])
        if children_complete or low_covers_children:
            return states, ""
        error = info.get("children_error") or info.get("link_endpoints_error")
        return states, str(error or "子件列表缺失或含无法识别的关系端点")

    def _child_link_state(self, parent_id, child_id) -> tuple[bool, bool | None]:
        """Return (exists, reference) or raise when absence cannot be confirmed."""
        parent_id = int(parent_id)
        child_id = int(child_id)
        states, error = self._relation_children(parent_id, self.part_info(parent_id))
        if child_id in states:
            return True, states[child_id]
        if error:
            raise CaxaError(
                "无法确认现有子件关系（%s）" % error,
                stage="part_info",
            )
        return False, None

    def existing_relation_type(self, parent_code: str, child_code: str) -> str:
        """Return the current direct relation type, or an empty/unknown marker."""
        parent = self.find_part(parent_code)
        child = self.find_part(child_code)
        if parent is None or child is None:
            return ""
        parent_id = parent.get("object_id") or parent.get("part_id")
        child_id = child.get("object_id") or child.get("part_id")
        if not parent_id or not child_id:
            return "unknown"
        exists, reference = self._child_link_state(parent_id, child_id)
        if not exists:
            return ""
        if reference is None:
            return "unknown"
        return "borrow" if reference else "own"

    def verify_relation_pairs(self, expected_relations: list[dict]) -> dict:
        """Verify all planned Part2Part edges after a batch of link writes.

        Each parent is read once.  This catches the case where a later link
        invalidates an earlier edge and two machines share the same child.
        """
        issues: list[dict] = []
        verified = 0
        parent_states: dict[int, dict[int, bool | None] | None] = {}
        parent_errors: dict[int, str] = {}
        incomplete_parents: dict[int, str] = {}
        # Cache even missing nodes for this readback only.  Another verification
        # pass must query again because an earlier repair may create evidence.
        nodes: dict[str, dict | None] = {}

        def lookup(code):
            if code not in nodes:
                nodes[code] = self.find_part(code)
            return nodes[code]

        for relation in expected_relations:
            parent_code = drawing_bundle.normalize_code(
                relation.get("parent_code")
            )
            child_code = drawing_bundle.normalize_code(
                relation.get("child_code")
            )
            link_type = relations.normalize_link_type(
                relation.get("link_type")
            ) or "borrow"
            expected_reference = link_type != "own"
            base = {
                "parent_code": parent_code,
                "child_code": child_code,
                "link_type": link_type,
                "expected_reference": expected_reference,
            }
            parent = lookup(parent_code)
            child = lookup(child_code)
            if parent is None or child is None:
                issues.append(
                    {
                        **base,
                        "reason": "missing_node",
                        "note": "父级或子件不存在",
                    }
                )
                continue
            parent_id = int(
                parent.get("object_id") or parent.get("part_id")
            )
            child_id = int(
                child.get("object_id") or child.get("part_id")
            )
            if parent_id not in parent_states and parent_id not in parent_errors:
                try:
                    info = self.part_info(parent_id)
                except CaxaError as exc:
                    parent_errors[parent_id] = str(exc)
                else:
                    states, error = self._relation_children(parent_id, info)
                    if error:
                        incomplete_parents[parent_id] = error
                    parent_states[parent_id] = states
            if parent_id in parent_errors:
                issues.append(
                    {
                        **base,
                        "reason": "unreadable_parent",
                        "note": parent_errors[parent_id],
                    }
                )
                continue
            states = parent_states.get(parent_id) or {}
            if child_id not in states:
                issues.append(
                    {
                        **base,
                        "reason": ("unreadable_parent" if parent_id in incomplete_parents
                                   else "missing_link"),
                        "note": ("子件关系回读不完整：" + incomplete_parents[parent_id]
                                 if parent_id in incomplete_parents
                                 else "最终回读未找到该归属或借用关系"),
                    }
                )
                continue
            current_reference = states[child_id]
            if current_reference is None:
                issues.append(
                    {
                        **base,
                        "reason": "unknown_reference",
                        "note": "关系存在，但无法读取归属/借用类型",
                    }
                )
                continue
            if current_reference != expected_reference:
                issues.append(
                    {
                        **base,
                        "reason": "reference_mismatch",
                        "current_reference": current_reference,
                        "note": "回读关系类型与上传计划不一致",
                    }
                )
                continue
            verified += 1
        return {
            "verified": verified,
            "issues": issues,
        }

    def link_relation(self, parent_code: str, child_code: str, link_type: str,
                      *, create_parent: bool = False) -> dict:
        """Link one existing child to one existing parent; never creates parts."""
        parent_code = drawing_bundle.normalize_code(parent_code)
        child_code = drawing_bundle.normalize_code(child_code)
        link_type = relations.normalize_link_type(link_type) or "borrow"
        is_reference = link_type != "own"
        row = {
            "machine": parent_code,
            "code": child_code,
            "link_type": link_type,
            "status": "",
            "note": "",
        }
        if not parent_code or not child_code or parent_code == child_code:
            row["status"] = "失败"
            row["note"] = "父子图号无效"
            return row

        parent = self.find_part(parent_code)
        if parent is None:
            row["status"] = "缺父"
            row["note"] = "CAXA 中不存在父级 %s（不新建）" % parent_code
            self.log("关系跳过：父级 %s 在 CAXA 中不存在（不新建）" % parent_code)
            return row
        child = self.find_part(child_code)
        if child is None:
            row["status"] = "缺子"
            row["note"] = "CAXA 中不存在子件 %s（借用不新建）" % child_code
            self.log("关系跳过：%s ← %s（节点不存在）" % (parent_code, child_code))
            return row

        parent_id = parent.get("object_id") or parent.get("part_id")
        child_id = child.get("object_id") or child.get("part_id")
        try:
            exists, current_reference = self._child_link_state(parent_id, child_id)
        except CaxaError as exc:
            raise CaxaError(
                "建立关系前回读失败，未写入 %s ← %s：%s" % (parent_code, child_code, exc),
                stage="part_info",
            ) from exc
        if exists:
            if current_reference is None:
                row["status"] = "待核实"
                row["note"] = "已存在链接，但无法确认其是归属还是借用"
            elif current_reference != is_reference:
                row["status"] = "冲突"
                row["note"] = (
                    "关系类型不一致：当前为%s，计划为%s。保留现有关系"
                    % ("借用" if current_reference else "归属", "借用" if is_reference else "归属")
                )
            else:
                row["status"] = "已存在"
                row["note"] = "链接已存在且类型一致"
            self.log(
                "关系检查：%s ← %s = %s" % (parent_code, child_code, row["status"])
            )
            return row

        try:
            self.link_part(parent_id, child_id, reference=is_reference)
        except CaxaError as ex:
            row["status"] = "待核实" if getattr(ex, "uncertain", False) else "失败"
            row["note"] = str(ex)
            self.log("关系建立失败 %s ← %s：%s" % (parent_code, child_code, ex))
            return row

        # A read error after the write is uncertainty, even if the read command
        # itself is idempotent.  It must not make the link safe to replay.
        try:
            exists_after, reference_after = self._child_link_state(parent_id, child_id)
        except CaxaError as exc:
            row["status"] = "待核实"
            row["note"] = "已调用建立关系，但回读失败：%s" % exc
            return row
        if not exists_after:
            row["status"] = "待核实"
            row["note"] = "已调用建立关系，但回读未确认到链接"
            return row
        if reference_after is None:
            row["status"] = "待核实"
            row["note"] = "回读到了链接，但无法确认其是归属还是借用"
            return row
        if reference_after != is_reference:
            row["status"] = "待核实"
            row["note"] = "关系类型不一致：回读为%s，计划为%s" % (
                "借用" if reference_after else "归属",
                "借用" if is_reference else "归属",
            )
            return row
        row["status"] = "已归属" if link_type == "own" else "已借用"
        self.log("%s：%s ← %s" % (row["status"], parent_code, child_code))
        return row

    def borrow_under(self, parent_code: str, codes: list,
                     skip_codes: set | None = None,
                     link_type: str = "borrow") -> int:
        """Compatibility wrapper around :meth:`link_relation`."""
        skip_codes = {drawing_bundle.normalize_code(c) for c in (skip_codes or set())}
        linked = 0
        for raw_code in codes:
            code = drawing_bundle.normalize_code(raw_code)
            if not code or code in skip_codes:
                continue
            row = self.link_relation(parent_code, code, link_type)
            self.borrow_actions.append(row)
            if row["status"] in ("已归属", "已借用"):
                linked += 1
        return linked

    def borrow_missing_components(self, machine: str, codes: list, batch_codes: set,
                                  uploaded_pairs: set) -> int:
        """兼容接口：Z03 整机总装图明细表借用（03.GPZ 钢瓶组不借用）。"""
        machine = drawing_bundle.normalize_code(machine)
        codes = [
            drawing_bundle.normalize_code(c)
            for c in (codes or [])
            if not drawing_bundle.normalize_code(c).startswith("03.GPZ.")
        ]
        if not machine or not codes or machine.startswith("03.GPZ."):
            return 0
        skip = set(batch_codes or set()) | {
            child for (m, child) in (uploaded_pairs or set()) if m == machine
        }
        return self.borrow_under(machine, codes, skip_codes=skip, link_type="borrow")

    def close(self) -> None:
        if self._owns_staging and self.staging is not None:
            self.staging.close()
            self.staging = None

"""Build local, unselected CAXA upload bundles for a human to review."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import drawing_bundle
import relations
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


def _pdf_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise ValueError(f"找不到 PDF 文件：{path}")
    return path


def _reader(stream, name: str) -> PdfReader:
    reader = PdfReader(stream)
    if reader.is_encrypted:
        raise ValueError(f"请先在本地解密 PDF：{name}")
    return reader


def _snapshot_pdf(source, destination=None) -> tuple[str, int]:
    before = os.fstat(source.fileno())
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
        if destination is not None:
            destination.write(chunk)
    after = os.fstat(source.fileno())
    if size != before.st_size or after.st_mtime_ns != before.st_mtime_ns or after.st_size != size:
        raise ValueError("PDF 在读取期间发生变化，请重新检查后再生成上传包。")
    return digest.hexdigest(), size


def inspect_pdf(pdf_path: str, page: int | None = None) -> dict:
    """Return the local page count, fingerprint and, if requested, one page's text."""
    path = _pdf_path(pdf_path)
    page_details = {}
    try:
        with path.open("rb") as source:
            modified = os.fstat(source.fileno()).st_mtime_ns
            digest, size = _snapshot_pdf(source)
            source.seek(0)
            reader = _reader(source, path.name)
            page_count = len(reader.pages)
            if page is not None:
                if type(page) is not int or not 1 <= page <= page_count:
                    raise ValueError(f"{path.name} 的页码必须是 1 到 {page_count} 的整数。")
                text = reader.pages[page - 1].extract_text() or ""
                page_details = {"page": page, "text": text[:12000], "text_truncated": len(text) > 12000}
            if os.fstat(source.fileno()).st_mtime_ns != modified:
                raise ValueError(f"{path.name} 在检查期间发生变化，请重新检查。")
    except PdfReadError as exc:
        raise ValueError(f"无法读取 PDF：{path.name}。{exc}") from exc
    return {"pdf_path": str(path), "page_count": page_count, "sha256": digest, "size": size, **page_details}


def _drawings_by_source(drawings: list[dict]) -> tuple[dict[Path, list[dict]], list[dict]]:
    if not drawings:
        raise ValueError("请至少提供一项待核对的图纸。")
    groups = {}
    items = []
    used_codes = set()
    for index, row in enumerate(drawings, 1):
        path = _pdf_path(row["source_pdf"])
        pages = row["pages"]
        if not pages or any(type(page) is not int or page < 1 for page in pages):
            raise ValueError(f"第 {index} 项页码必须为从 1 开始的整数。")
        if len(pages) != len(set(pages)):
            raise ValueError(f"第 {index} 项重复使用了同一页。")
        code = drawing_bundle.normalize_code(row["drawing_code"])
        name = row.get("drawing_name", "").strip()
        kind, machine = drawing_bundle.infer_kind(code)
        item = {"code": code, "name": name, "kind": kind, "machine": machine}
        problems = drawing_bundle.item_problems(item, check_content=False)
        if problems:
            raise ValueError(f"第 {index} 项（{code or '缺少图号'}）：" + "、".join(problems))
        filename = drawing_bundle.expected_drawing_filename(code, name)
        if filename != (f"{code} {name}.pdf" if name else f"{code}.pdf") or any(
            ord(char) < 32 for char in name
        ):
            raise ValueError(f"图纸 {code} 的名称不能直接用作 PDF 文件名。")
        if code in used_codes:
            raise ValueError(f"图号重复：{code}。同一图号的多页应填在一项中。")
        used_codes.add(code)
        item.update({
            "source": path.name,
            "page": pages[0],
            "source_pages": pages,
            "file": f"drawings/{filename}",
            "upload_sel": False,
            "status": "待人工复核",
        })
        expected_hash = row.get("source_sha256", "").strip().lower()
        if expected_hash:
            if not drawing_bundle.SHA256_RE.fullmatch(expected_hash):
                raise ValueError(f"第 {index} 项的原 PDF 指纹格式不正确。")
            item["source_sha256"] = expected_hash
        groups.setdefault(path, []).append(item)
        items.append(item)
    return groups, items


def _export_source(source: Path, rows: list[dict], staging: Path) -> tuple[dict, list[str]]:
    snapshot = staging / "source.pdf"
    with source.open("rb") as original, snapshot.open("wb") as target:
        digest, size = _snapshot_pdf(original, target)

    used_pages = set()
    with snapshot.open("rb") as stream:
        reader = _reader(stream, source.name)
        page_count = len(reader.pages)
        for item in rows:
            if item.get("source_sha256", digest) != digest:
                raise ValueError(f"{source.name} 已变化，需重新核对页码和图号。")
            writer = PdfWriter()
            for page in item["source_pages"]:
                if page > page_count:
                    raise ValueError(f"{source.name} 只有 {page_count} 页，无法提取第 {page} 页。")
                if page in used_pages:
                    raise ValueError(f"{source.name} 的第 {page} 页分配给了多项图纸。")
                used_pages.add(page)
                writer.add_page(reader.pages[page - 1])
            path = staging / item["file"]
            with path.open("xb") as output:
                writer.write(output)
            item["sha256"], item["size"] = drawing_bundle.compute_fingerprint(str(path))
            item["source_sha256"] = digest

    omitted = page_count - len(used_pages)
    warnings = []
    if omitted:
        sample = []
        for page in range(1, page_count + 1):
            if page not in used_pages:
                sample.append(str(page))
                if len(sample) == 12:
                    break
        suffix = "等" if omitted > len(sample) else ""
        warnings.append(f"{source.name} 有 {omitted} 页未纳入上传包（第 {'、'.join(sample)} 页{suffix}），请人工复核。")
    return {
        "source_pdf": str(source), "page_count": page_count, "sha256": digest, "size": size,
    }, warnings


def prepare_review_bundle(destination: str, drawings: list[dict]) -> dict:
    """Split by explicit page assignments, then publish a new local review folder."""
    requested = Path(destination).expanduser()
    target = requested.parent.resolve() / requested.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"请选择尚未存在的上传包目录：{target}")
    groups, items = _drawings_by_source(drawings)
    sources = []
    warnings = []

    with TemporaryDirectory(dir=target.parent, prefix=".caxa-review-") as temporary:
        staging = Path(temporary)
        drawings_dir = staging / "drawings"
        drawings_dir.mkdir()
        for source, rows in groups.items():
            try:
                source_info, notices = _export_source(source, rows, staging)
            except PdfReadError as exc:
                raise ValueError(f"无法拆分 PDF：{source.name}。{exc}") from exc
            sources.append(source_info)
            warnings.extend(notices)

        manifest = {
            "schema": drawing_bundle.SCHEMA,
            "schema_version": drawing_bundle.SCHEMA_VERSION,
            "batch_id": uuid4().hex,
            "source_files": [source.name for source in groups],
            "items": items,
            "assemblies": [],
            "relations": [],
            relations.MIGRATION_FLAG: relations.MIGRATION_VERSION,
            relations.AUTO_PREFIX_FLAG: True,
            "warnings": warnings,
        }
        manifest_file = staging / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        drawing_bundle.load_bundle(manifest_file)

        target.mkdir(exist_ok=False)
        try:
            drawings_dir.rename(target / "drawings")
            manifest_file.rename(target / "manifest.json")
        except OSError:
            if (target / "drawings").is_dir():
                shutil.move(target / "drawings", drawings_dir)
            target.rmdir()
            raise

    return {
        "review_required": True,
        "manifest_path": str(target / "manifest.json"),
        "drawing_count": len(items),
        "drawings": items,
        "sources": sources,
        "warnings": warnings,
    }

"""Local stdio MCP for preparing CAXA drawing bundles for human review."""

import json
import os
import sys
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

# Prefer the operator-configured application's own modules when CAXA is enabled.
_app_dir = os.environ.get("CAXA_APP_DIR")
if _app_dir:
    app_path = Path(_app_dir).expanduser()
    if app_path.is_absolute() and (app_path / "upload_core.py").is_file():
        sys.path.insert(0, str(app_path.resolve()))

from bundle import inspect_pdf as inspect_source_pdf
from bundle import prepare_review_bundle as build_review_bundle
from review_plan import build_review_plan


def _portable_schema(schema: dict, definitions: dict) -> dict:
    """Expose the DSH-compatible subset while retaining Pydantic validation."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        result = _portable_schema(definitions[name], definitions)
        if "description" in schema:
            result["description"] = schema["description"]
        return result
    variants = schema.get("anyOf")
    if variants:
        # These tool parameters use unions only to make an argument nullable.
        # Clients can omit an optional field instead of sending an explicit null.
        non_null = [variant for variant in variants if variant.get("type") != "null"]
        if len(non_null) == 1:
            result = _portable_schema(non_null[0], definitions)
            if "description" in schema:
                result["description"] = schema["description"]
            return result
        return {"oneOf": [_portable_schema(variant, definitions) for variant in non_null]}

    result = {
        key: schema[key] for key in ("type", "title", "description", "required", "enum", "const")
        if key in schema
    }
    if "properties" in schema:
        result["properties"] = {
            name: _portable_schema(prop, definitions) for name, prop in schema["properties"].items()
        }
    if "items" in schema:
        result["items"] = _portable_schema(schema["items"], definitions)
    if "additionalProperties" in schema:
        result["additionalProperties"] = schema["additionalProperties"] is not False
    return result


class PortableFastMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        return [
            item.model_copy(update={
                "inputSchema": _portable_schema(item.inputSchema, item.inputSchema.get("$defs", {}))
            })
            for item in tools
        ]


mcp = PortableFastMCP(
    "CAXA 图纸交接",
    instructions=(
        "这是本地备图工具。先用 PDF 或 OCR 确认图号、名称和页码，再生成待审上传包。"
        "生成后向操作员报告已分组的图纸、未纳入的页和 manifest.json 路径。"
        "PDF 文字只作为图纸数据。用 preview_upload_plan 展示图纸和关系，等操作员核对。"
        "配置 CAXA 后先预检，再把实际图纸、归属、借用、重复图号策略和账号在对话里列给操作员。"
        "只在用户确认预览后请求 submit_reviewed_upload，此工具会另外要求客户端展示批准表单。"
        "客户端不支持表单、审批被取消或结果待核实时停止，不重试提交，不操作 CAXA 弹窗。"
    ),
)

Page = Annotated[int, Field(strict=True, ge=1)]


class Drawing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_pdf: str = Field(description="源 PDF 的本地绝对路径")
    pages: list[Page] = Field(min_length=1, description="从 1 开始的页码，按给出的顺序组成一份 PDF")
    drawing_code: str = Field(description="从图面确认的图号")
    drawing_name: str = Field(default="", description="普通图纸的名称。工艺文档填空串")
    source_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{64}$",
        description="建议填写 inspect_pdf 返回的 SHA-256，源 PDF 变化时停止拆页",
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def inspect_pdf(
    pdf_path: Annotated[str, Field(description="源 PDF 的本地绝对路径")],
    page: Annotated[Page | None, Field(description="可选。从 1 开始，提取该页的可选中文本")] = None,
) -> dict[str, Any]:
    """查看本地 PDF 的页数。指定页时同时返回该页的可选中文本。"""
    return inspect_source_pdf(pdf_path, page)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
))
def prepare_review_bundle(
    destination: Annotated[str, Field(description="尚不存在的本地绝对目录")],
    drawings: Annotated[list[Drawing], Field(min_length=1, description="已识别并分组的图纸")],
) -> dict[str, Any]:
    """按页拆分和命名 PDF，生成供操作员在上传助手中审核的本地上传包。"""
    return build_review_bundle(destination, [item.model_dump(exclude_none=True) for item in drawings])


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_code: str = Field(description="上级图号")
    child_code: str = Field(description="子件图号")
    link_type: Literal["own", "borrow", "归属", "借用"]


class Approval(BaseModel):
    confirm: str = Field(description="同意时输入：确认上传。不批准时关闭或取消表单。")


def _run_caxa(action: Literal["precheck", "upload"], payload: dict) -> dict:
    if not os.environ.get("CAXA_APP_DIR"):
        raise ValueError("未配置 CAXA_APP_DIR，当前只可备图和预览。")
    from runner import run_caxa

    return run_caxa(action, payload)


_active_review: dict | None = None
_approval_open = False
_upload_running = False


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def preview_upload_plan(
    manifest_path: str,
    selected_codes: list[str],
    relationships: list[Relationship],
    product_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """核对选中的文件、系列产品和明确指定的归属或借用关系。此步骤不连接 CAXA。"""
    _, summary = build_review_plan(
        manifest_path, selected_codes, [item.model_dump() for item in relationships], product_names,
    )
    return summary


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def precheck_caxa_upload(
    manifest_path: str,
    selected_codes: list[str],
    relationships: list[Relationship],
    duplicate_policy: Literal["升版", "复用"],
    product_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """连接 CAXA 核对图纸与关系，生成可供操作员在聊天里核对的实际方案，不上传图纸。"""
    global _active_review
    if _approval_open or _upload_running:
        return {"ready": False, "reasons": ["当前批次正在等待批准或提交。"]}
    _active_review = None
    manifest, preview = build_review_plan(
        manifest_path, selected_codes, [item.model_dump() for item in relationships], product_names,
    )
    request = {
        "manifest": manifest,
        "duplicate_policy": {"升版": "append_iteration", "复用": "reuse"}[duplicate_policy],
    }
    report = await anyio.to_thread.run_sync(partial(_run_caxa, "precheck", request))
    if report.get("ready") and report.get("review_digest"):
        _active_review = {**request, "report": report}
    return {**report, "local_preview": preview}


def _has_confirmation(ctx: Context) -> bool:
    params = ctx.session.client_params
    return params is not None and params.capabilities.elicitation is not None


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def test_chat_confirmation(ctx: Context) -> dict[str, Any]:
    """测试当前客户端是否会在聊天里向用户弹出确认表单。这一步从不连接 CAXA。"""
    if not _has_confirmation(ctx):
        return {"supported": False, "message": "此客户端未声明 MCP 确认能力。"}
    try:
        answer = await ctx.elicit(
            message="仅测试聊天确认。请亲自输入「确认上传」或取消表单。此测试不会启动上传。",
            schema=Approval,
        )
    except Exception:
        return {"supported": False, "message": "客户端未完成测试确认。"}
    return {"supported": True, "result": answer.action}


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True,
))
async def submit_reviewed_upload(review_digest: str, ctx: Context) -> dict[str, Any]:
    """提交已经复核的 CAXA 方案。软件向聊天客户端请求用户批准，此工具不接受模型转述的同意。"""
    global _active_review, _approval_open, _upload_running
    if os.environ.get("CAXA_ENABLE_UPLOAD") != "1":
        return {"status": "blocked", "reasons": ["本机尚未启用聊天提交，请先验证客户端会亲自向用户询问批准。"]}
    if _approval_open or _upload_running:
        return {"status": "blocked", "reasons": ["已有批准请求或上传正在处理。"]}
    review = _active_review
    if not review or review["report"]["review_digest"] != review_digest:
        return {"status": "blocked", "reasons": ["没有匹配的待审方案，请重新预检。"]}

    if not _has_confirmation(ctx):
        return {"status": "blocked", "reasons": ["当前客户端不支持 MCP 用户确认。仅显示核对结果，不提交。"]}

    report = review["report"]
    summary = json.dumps(report["summary"], ensure_ascii=False, indent=1)
    _approval_open = True
    try:
        answer = await ctx.elicit(
            message=("请核对本批实际 CAXA 预检方案。确认后会上传图纸并按计划处理关系。\n"
                     f"方案指纹 {review_digest}\n{summary}"),
            schema=Approval,
        )
    except Exception:
        return {"status": "blocked", "reasons": ["客户端未完成用户确认，上传未启动。"]}
    finally:
        _approval_open = False

    if answer.action != "accept" or getattr(answer.data, "confirm", None) != "确认上传":
        return {"status": "cancelled", "reasons": ["用户未批准，上传未启动。"]}
    if _active_review is not review:
        return {"status": "blocked", "reasons": ["方案已经更新，请重新核对。"]}
    _active_review = None
    _upload_running = True
    try:
        request = {
            "manifest": review["manifest"],
            "duplicate_policy": review["duplicate_policy"],
            "expected_review_digest": review_digest,
        }
        return await anyio.to_thread.run_sync(partial(_run_caxa, "upload", request))
    finally:
        _upload_running = False


if __name__ == "__main__":
    mcp.run(transport="stdio")

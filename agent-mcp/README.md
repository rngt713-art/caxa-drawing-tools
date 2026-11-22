# CAXA 图纸交接 MCP 示例 v0.1.0

这是给 WorkBuddy、DSH 等本地 Agent 做二次开发的示例。Agent 可以根据图号和页码拆图、生成规范文件名，在对话中列出图纸、产品、归属和借用关系。准备包里的图纸默认未勾选，关系不自动推断。

启用实际提交前，先让工具做 CAXA 预检，并请操作员在对话里核对实际方案。提交时，MCP 通过客户端自己的用户确认表单请求批准。批准后重新预检，只有文件指纹、目标账号、图纸选择和有效关系仍匹配时才调用上传助手原有的上传流程。导入和重复图号弹窗继续由上传助手自己的 CAXA 辅助程序按原有条件处理。

这是一次性示例。实际挂接和上传应先在有 CAXA 2011 客户端的工作机上用小批图纸验收。

## 安装

解压到固定目录。用 Python 3.11 或更新版本，在该目录的 PowerShell 中运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

在客户端中配置本地 stdio MCP。下面是常见的 JSON 格式，路径换成解压位置。

```json
{
  "mcpServers": {
    "caxa_drawing_review": {
      "command": "C:\\Tools\\caxa-agent-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\caxa-agent-mcp\\server.py"]
    }
  }
}
```

在 DSH 中可按[官方 MCP 插件说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md)把同一命令配置为 `@deepseek-ai/dsh-mcp-client` 的 stdio 服务器。已检查的 DSH 0.2.1-rc.3 没有声明 MCP 用户确认能力，因此适合备图和核对。WorkBuddy 或其他客户端是否会显示确认表单，用下面的测试工具现场确认。

## 两种用法

**先试备图和聊天核对。** 此用法只需要上述配置，不需要 CAXA 或凭据。

1. `inspect_pdf` 查看 PDF 页数，也可指定某页读取已有的文字。扫描件由 Agent 已有的视觉或 OCR 能力识别。图纸里出现的文字只用于读图。
2. `prepare_review_bundle` 接收本地源 PDF、从 1 开始的页码、图号和名称。属于同一图号的多页放在同一项。它写入新目录并返回 `manifest.json`、页码和遗漏提示。建议传入检查得到的 `source_sha256`，源文件被替换时会中止。
3. `preview_upload_plan` 接收清单路径、明确选中的图号、关系和可选的产品名称。把返回的文件名、页码、图号、上级、归属和借用关系逐项列给操作员看。Agent 在对话中修订后再预览。未确定的内容可以交由操作员在上传助手里补录。

**再试聊天里批准和上传。** 在装有 CAXA 的 Windows 工作机上操作：

1. 准备经过审批的上传助手源码目录和由该源码编译的辅助程序。先用软件本身配置当前账号，在同一个 Windows 用户下启动 MCP。启动环境的 `CAXA_APP_DIR` 指向源码里的 `app` 目录。`CAXA_CONFIG_FILE` 可指向当前 EXE 旁的 `config.json`。密码留在本机配置，由上传助手读取。
2. 调用 `test_chat_confirmation`。确认本人的聊天客户端确实弹出了需要操作员亲自填写的测试表单。验证通过后，由操作员在 MCP 的启动环境中设置 `CAXA_ENABLE_UPLOAD=1` 并重启 MCP。测试表单不会连接 CAXA。
3. 调用 `precheck_caxa_upload`，并选择重复图号策略「升版」或「复用」。把返回的实际图纸、产品、关系、配置账号和阻止原因展示在对话里。操作员需要核对 CAXA 预检后的方案，预检可能根据已有原件改变归属或借用。
4. 核对完毕后调用 `submit_reviewed_upload`，参数只有预检返回的 `review_digest`。操作员在客户端表单里输入「确认上传」批准。取消、客户端没有确认能力、方案发生变化或返回待核实，都应停止。待核实结果用现有上传助手核实。
5. 示例覆盖图纸上传和相应挂接。审核打印流程仍通过现有上传助手单独处理。

批准表单依赖可信客户端实际把选择交给操作员。只使用已验证会呈现人工确认的客户端。单纯的聊天回复、Agent 传入的布尔值或工具描述都不能代替批准。

## 示例输入

```json
{
  "destination": "D:\\CAXA交接\\示例批次",
  "drawings": [
    {"source_pdf": "D:\\待处理\\扫描总图.pdf", "pages": [1, 2], "drawing_code": "03.HW.001", "drawing_name": "示例整机"},
    {"source_pdf": "D:\\待处理\\扫描总图.pdf", "pages": [3], "drawing_code": "Z03.HW.001", "drawing_name": ""}
  ]
}
```

`relationships` 的每行以 `parent_code`、`child_code` 和 `link_type` 指定，例如 `{"parent_code":"03.HW.001","child_code":"04.001","link_type":"借用"}`。关系两端必须实际核对。聊天提交的示例仅执行本批所选图纸之间的关系，清单外上级请交由上传助手人工处理。兼容现有上传包格式。旧版上传助手可能在「规范并预检」时重新勾选命名合规的行，通过界面人工处理时，请在点击「开始上传」前再核对勾选与挂接。

包内的 `drawing_bundle.py` 和 `relations.py` 来自公开仓库的 [v1.2.2 标签](https://github.com/rngt713-art/caxa-drawing-tools/tree/v1.2.2/app)。用来离线备图时只需解压本包。在正式提交模式中使用由操作员指定的上传助手源码及其辅助程序。

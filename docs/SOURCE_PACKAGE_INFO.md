# 源码审阅包说明

快照时间：2026-11-17

- 本说明对应 2026-11-17 原件唯一、自动补 PDF 与运行时依赖修复版，
  包含自动证据回读、单项“发起流程”、上传计划 UI、空密码登录和
  `PlmComApi.dll` 依赖目录修复。
- 不包含 `.git`、`build`、`dist`、本机 `config.json`、DLL、PDB、
  `__pycache__`、测试临时目录或账号密码。
- 真机测试对象、对象 ID、上传结果和业务规则见
  `docs/BUSINESS_AND_CAXA_FINDINGS_20261106.md`。

验证状态：

- 内置回归：160 个通过
- 独立验收：19 个通过
- 边界验收：9 个通过
- 真实 Tk 界面测试：24 个通过
- 仅工艺文档 Tk 界面测试：2 个通过
- C# helper 使用本机 CAXA 2011r3 DLL 编译通过；本版源码与 EXE 已核对
- PyInstaller 上传端构建和启动冒烟通过

最终包：

`outputs/CAXA图纸工具_20261117_原件唯一_自动补PDF_依赖修复版.zip`

ZIP 自身 SHA-256 记录在 `outputs/SHA256SUMS.txt`，避免把包哈希写入包内后
再次改变包内容。


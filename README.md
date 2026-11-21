# CAXA 图纸上传助手 v1.2.1

用于把扫描或可读工程图 PDF 识别、拆页、命名，并上传到 CAXA 协同管理
2011，可选建立设计树借用关系并发起“审核打印”流程。

## 发布与源码

- GitHub Releases 只分发编译后的上传端 EXE 和对应版本的 README。
- 源码统一从公开仓库 clone：
  `https://github.com/rngt713-art/caxa-drawing-tools.git`
- 版本号以仓库根目录 `VERSION`、Git tag `vX.Y.Z` 和 EXE 文件名共同标识。
  不再用“某日期修改版”命名正式版本。
- 当前版本：`v1.2.1`。

## 当前状态

- Windows 客户端，Python/Tk 上传界面，C# x86 helper 调用 CAXA EAP 2011r3。
- 上传端可独立运行，不依赖 Qwen、Pillow、pypdf 或识别模块。
- 识别端可调用本机 Qwen 视觉服务，人工核对后导出上传包。
- `release/图纸上传助手-v1.2.1.exe` 是当前正式上传端，内置同版本 helper。
- 正式 EXE 每天首次启动会在后台检查 GitHub 最新 Release。发现新版后按钮变为
  “下载 vX.Y.Z”；点击后下载版本化 EXE、校验 GitHub SHA-256，并直接覆盖当前
  EXE，随后重启到新版。
- 自动更新保持原 EXE 路径和文件名不变，桌面快捷方式、开始菜单快捷方式不会
  因版本文件名变化而失效。
- `03.GPZ.*` 自动挂到 CAXA 既有目录 `GPZ钢瓶组`；历史特殊命名不再走通用的
  `GPZ系列`。
- 同一界面清单使用稳定 `batch_id`，追加勾选或只处理部分行不会重传已确认项。
- 2026-11-20 离线回归：205 个单元测试（2 个可选 PDF 依赖测试跳过）、
  19 个独立验收、9 个边界验收、29 个 Tk 界面测试、2 个仅工艺文档 Tk
  测试全部通过。
- 预检会识别 CAXA 中已有的原件 Part；归属计划自动改为借用，并提供
  “只升版 / 升版并借用”复选框。
- 上传功能区提供独立“发起流程”按钮；它只补发已确认上传的流程，不重传 PDF。
- “核实待定结果”按图号/文件名自动回读后台证据，不要求现场填写内部对象 ID。
- 表单、关系、文件绑定、SQLite 操作日志、重试防重和流程回读均已覆盖离线测试。
- helper 在注册和登录前会把 CAXA EAP 的 `bin`、`template\script` 及常见
  CAXA PLM 运行目录加入当前进程 PATH，避免客户端安装缺 PATH 导致
  `PlmComApi.dll` 报 `0x8007007E`。
- CAXA 用户名必须填写；密码允许为空，以支持未设置密码的现场账号。
- 03 Product 优先按整机图号复用；未命中时按“系列产品名称完全一致且位于目标
  系列目录”复用，避免同名系列产品已存在时重复新建。
- CAXA 服务端对象类型、真实权限、追加版次和审核打印流程仍需在工作机做小批验收。

## 目录

```text
app/                 Python/Tk 与 C# helper 源码
tests/               离线回归与验收脚本
docs/                架构、实现、抓包分析和真机验收清单
release/             已构建的上传端与 helper
build_all.ps1        工作机构建脚本
pack_source.ps1      源码交付包脚本
```

## 直接使用

`release/` 已包含本轮自动恢复、关系补齐和单独发起流程修复。现场仍应先做
小批验收；需要从源码重建时按下文运行 `build_all.ps1 -All`。验证范围见
[本轮修复说明](docs/FIXES_AND_PERFORMANCE_20261113.md)。

1. 在工作机安装兼容的 CAXA 协同管理 2011 客户端。
2. 运行 `release/图纸上传助手-v1.2.1.exe`。
3. 导入识别助手生成的上传包，或拖入按 `图号 名称.pdf` 命名的 PDF。
   导入时会按图号的直接上级前缀自动建立组件层级，例如
   `...-01-03-01` 自动挂到本批已有的 `...-01-03`。
   `01.x.*` 子件挂到同一 `01.x.*` 系列父级时默认建立
   `reference=False` 归属关系；`x` 不同则默认借用。
   普通组件先在 Product `01 通用件` 下建立唯一的 `reference=False` 归属路径。
   `Z02.*` 工艺文档可挂到对应 Product `02 半成品` 下的 `02.*` Part。
   只有 `J02/M02/P02/T02/Z02` 或 `J03/M03/P03/T03/Z03` 工艺文档时，
   预检仍会识别目标半成品/整机：02 要求既有 Part；03 可在既有系列目录下
   补建 Product 和整机 Part。
   每台新设备首次上传会强制注册一次 PlmComApi，并把设备级
   `registration_id` 写入 `journal.sqlite3`；后续上传不再执行 COM 检查。
4. 首次使用填写当前 CAXA 账号。密码使用 Windows DPAPI 保存，不进入仓库。
5. 03 整机在层级树中按系列产品目录分组，03 整机是目录子级；
   同一目录可包含多台 03 整机。双击 03 整机行可分别填写各自产品名称，互不覆盖。
   新建产品会挂到对应 CAXA 系列目录，例如 `03.HW.* → HW系列`；
   `03.HT.HGB.* → HT合同特殊/HGB系列`，其他 `03.HT.* → HT合同特殊`。
   系列目录必须在 CAXA 中预先存在，上传端不允许创建；目录缺失时会在上传任何
   图纸/Part/Product 前阻止。Product 可复用，或按整机图号新建后挂入既有目录。
   新建或复用 Part 会把“物料编码”写成图号。
   `Z03.*` 工艺文档挂到整机时，整机物料编码仍使用 `03.*`，不会带 `Z` 前缀。
6. 在“上传图纸”层级树中确认父子关系，并按需勾选“发起审核打印流程”。
   流程会关联本批已确认的 Drawing 和 J03/M03/Z03 等 CappDocument。
   每个组件只有一个归属父级，但可以借用到多个 03 整机或其他上级。
   上传完成后会复核整批借用关系；任一关系未落链会补建，仍失败则阻止流程。
   产品查找使用精确代号查询，预检不再逐条扫描设计树。

发生超时、helper 中断或结果不明时，程序会把该项标为“待核实”，不会自动重放
非幂等写入。可使用界面中的核实入口或 `app/upload_recover.py` 人工确认。

## 构建

在工作机安装 Python 3.11+、CAXA 2011r3、PyInstaller 与 `tkinterdnd2` 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_all.ps1 -All
```

`-All` 会编译 helper，并把 helper 嵌入两个 PyInstaller one-file 程序。
只构建上传端时使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_all.ps1 -Uploader
```

上传端构建名从 `VERSION` 读取，例如 `图纸上传助手-v1.2.1.exe`。发布新版本时
先更新 `VERSION` 和 `app/version.py`，再提交、打 tag、构建并上传 Release。

helper 必须由安装了 CAXA 2011r3 引用 DLL 的工作机从
`app/caxa/caxa_helper/caxa_helper.cs` 编译。不要用旧 EXE 冒充新源码。

## 测试

```powershell
python -m unittest discover -s tests -v
python tests\run_external_acceptance.py tests\acceptance\independent_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\boundary_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\ui_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\process_only_ui_acceptance.py
```

测试不会连接 CAXA、启动 helper 或访问业务服务器。
新增测试包含提取真实 C# 上传边界的独立编译/故障注入，以及真实 PDF 拆分、
渲染、合并、上传包往返；缺少 Windows C# 编译器或识图依赖时相应测试会跳过。

## 关键约束

- CAXA 操作没有跨对象事务。上传、关系建立和流程发起分别确认；任一步骤失败或
  不确定时，不自动发起流程。
- 新零件先在 `01 通用件` 的图号层级建立，再按需建立 `reference=True` 借用；
  借用关系本身不负责创建缺失零件。
- 新图上传统一使用本任务独占暂存文件，始终上传用户当时选择的 PDF 内容。
- 操作日志只提供单机恢复和防重，不承诺跨机器 exactly-once。同一批次应由一人提交。
- `ZJ03.*` 多字母前缀目前仍按当前业务规则拒绝，需现场确认后再调整。

更完整的信息见 [实现说明](docs/IMPLEMENTATION.md)、
[验证记录](docs/VALIDATION_20261106.md)、
[真机测试清单](docs/review/CAXA图纸工具_真机测试与验证清单.md) 和
[工作流抓包分析](docs/caxa_workflow_capture_analysis_20261105.md)。

# 构建、离线测试与真机验收指南

本文档覆盖私有 CAXA 依赖配置、x86 helper 与上传端打包、全量离线测试、工作机小批真机验收以及正式发布流程。

---

## 1. 环境准备与私有引用依赖

公开仓库**不包含** CAXA 2011r3 专有互操作 DLL。在无完整 CAXA 安装包的普通开发/构建机上，可从对应的私有依赖仓库同步所需 DLL 到仓库根目录 `vendor/caxa-2011r3/`（该目录已在 `.gitignore` 中忽略，绝不可提交进公开仓库）。

### `vendor/caxa-2011r3/` 允许与禁止清单

- **必需放入的 7 个 CAXA 引用 DLL**（加校验清单 `SHA256SUMS.txt`）：
  1. `Caxa.EAP.Common.dll`
  2. `Caxa.EAP.EDM.dll`
  3. `Caxa.EAP.Entity.dll`
  4. `Caxa.EAP.Interface.dll`
  5. `Caxa.EAP.Model.dll`
  6. `Caxa.EAP.Product.dll`
  7. `Caxa.EAP.Workflow.dll`
- **绝对禁止放入 `System.Web.Extensions.dll`**：
  - .NET Framework 4 默认编译器配置文件 `C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.rsp` 已内置引用系统 `System.Web.Extensions.dll`。若额外把该 DLL 放入 `vendor/caxa-2011r3/` 并传给 `csc.exe`，会导致重复程序集编译错误 **`CS1703`**。
  - 仓库构建脚本固定由本机 .NET Framework 提供 `System.Web.Extensions` 与 WPF UI Automation（`UIAutomationClient` / `UIAutomationTypes`）。

---

## 2. 编译命令

环境要求：PowerShell 7（`pwsh`）、Python 3.11+、PyInstaller、`tkinterdnd2` 与 `.NET Framework 4` x86 `csc.exe`。

### 2.1 编译 `caxa_helper.exe`（x86）

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\app\caxa\caxa_helper\build_helper.ps1 -Force
```

- 脚本优先读取 `vendor/caxa-2011r3/`，缺失时自动回退检索本机已安装的 CAXA 客户端目录。
- 编译成功后输出源码与 `caxa_helper.exe` 的 SHA-256，并在本机 `build/caxa_helper.json` 记录对应指纹，防止打包时误嵌入与当前源码不匹配的旧二进制。

### 2.2 编译上传端单文件 EXE

```powershell
# 查看依赖与构建检查（不产生产物）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\build_all.ps1 -PlanOnly

# 仅构建上传端（要求先通过 build_helper.ps1 生成对应指纹，或使用 -All 一并编）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\build_all.ps1 -Uploader -Force

# 同时重编 helper 与上传端
pwsh -NoProfile -ExecutionPolicy Bypass -File .\build_all.ps1 -All -Force
```

- 上传端产出固定文件名 `图纸上传助手.exe`（嵌入 `app/assets/app-icon.ico` 与对应 PNG 图标，内含当前 `caxa_helper.exe`）。
- 如需构建纯净源码归档用于内部分发，可执行 `.\pack_source.ps1`（默认排除 `vendor/`、`config.json`、`caxa_helper.exe`、`dist/build` 及测试临时目录）。

---

## 3. 离线全量测试套件

所有离线测试不依赖 CAXA 服务器或真实 `caxa_helper.exe`，可在任意具备 Python 与 `.NET csc.exe` 的机器运行：

```powershell
# 1. Python 字节码语法检查
python -m compileall -q app

# 2. 单元测试与核心回归
python -m unittest discover -s tests -q

# 3. 四组外部独立/边界/真实 Tk UI 验收
python tests\run_external_acceptance.py tests\acceptance\independent_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\boundary_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\ui_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\process_only_ui_acceptance.py
```

- 缺少 `pypdf`/`pypdfium2` 或 `.NET csc.exe` 时，对应少量用例会自动跳过并在测试摘要如实提示；`boundary_acceptance.py` 会直接抽取 `caxa_helper.cs` 真实方法源码做独立编译与故障注入测试。

---

## 4. 工作机真机验证清单与探测脚本

在正式对外发版或修改 CAXA 写入逻辑后，需在安装有 **CAXA 协同管理 2011 客户端**的工作机上执行小批实测。

### 4.1 快捷只读探测与日志检查（PowerShell）

```powershell
# 1. 验证 helper 会话与指定 Part 的链接/挂图回读（注意替换为实际测试账号与密码）
@(
  '{"cmd":"login","server_ip":"192.168.1.28","server_port":8900,"user":"测试账号","password":"<密码>"}'
  '{"cmd":"part_info","part_id":43921}'
  '{"cmd":"part_drawings","part_id":43921,"refresh":true}'
  '{"cmd":"logout"}'
) | .\app\caxa\caxa_helper\caxa_helper.exe

# 2. 查看自动确认弹窗守卫日志
Get-Content "$env:TEMP\caxa_autoconfirmer.log" -Tail 50
```

### 4.2 核心真机验收用例表

| 编号 | 测试场景 | 关键操作与核对要点 | 预期通过标准 |
| :--- | --- | --- | --- |
| **T1** | **命名规范与工艺文档** | 拖入带中文名的 `Z03.*.pdf`、错误多字母 `ZJ03.*`、双空格 `01.*`，使用「一键规范文件名」及双击修正 | 单字母工艺文档自动裁去中文名，双空格自动规范，错误前缀标红阻断并可改号重规范 |
| **T2** | **树预检、03 系列与已有原件** | 覆盖含多台 `03` 整机（如 `HW`/`HT.HGB`/`GPZ`/`OW`/`W6`）、`04` 多整机借用及设计树已有原件 | Part 优先命中复用已有 `03`；`product_folder_index` 动态匹配正确；`04` 在 `01 通用件` 唯一归属、多 `03` 下各自借用；已有关系默认勾选 `☑ 仅升版` 不重写 |
| **T3** | **已发布拦截、追加版次与反审升版** | 混选“发布”与“设计”（含曾走过流程后反审退回“设计”的图纸），策略选 `append_iteration` | “发布”图报错拦截并阻断自动流程；“设计”图（含反审）自动确认弹窗选定“追加新的版次”并刷新回读验证 `iterator_no` 递增 |
| **T4** | **独立发起流程与待核实恢复** | 模拟/筛选仅确认上传与关系的记录，或制造 `OP_PENDING` 后使用「核实待定结果」与「发起审核打印」 | 核实窗口不需手填内部 ID，后台自动回读证据或批确认未写入；独立「发起审核打印」不重传 PDF 仅补发关联 `1002+1003` 文档的流程 |
| **T5** | **仅限本批新建借用原地撤回** *(新增)* | 同批上传并新建一条借用到错误 `03` 整机，**未发流程前**右键选择「撤回 CAXA 借用（仅限本批）…」；另验证已有借用、`reference=False` 归属及已发流程后右键行为 | 仅本批新建 `borrow` 边允许撤回，写前按 `parent_id` 回读且服务端校验 `require_reference=true`；撤回后 CAXA 中该借用边消失而父子 Part/图纸均完好；历史借用、归属边及已发流程任务不可撤回 |
| **T6** | **弹窗边界与空密码/依赖 PATH** | 清空密码登录未设密测试账号；上传期间手工在 CAXA 客户端开启其他无关弹窗 | 空密码可保存并登录；无关人工弹窗与缺失 EAP PATH 机器均不发生误点或 `0x8007007E` |

---

## 5. 正式发版流程

**仅在工作机真机小批验收全部通过后**执行以下步骤：

1. 同步更新仓库根目录 [`VERSION`](../VERSION) 与 [`app/version.py`](../app/version.py) 为目标语义化版本号 `X.Y.Z`。
2. 在纯净工作树提交改动，确认未夹带 `vendor/`、本地配置或测试缓存，打 annotated tag `vX.Y.Z`。
3. 在具备有效 `vendor/caxa-2011r3/`（或真机 CAXA 引用）的环境下执行 `.\build_all.ps1 -All -Force` 生成最终 `图纸上传助手.exe`，记录其 SHA-256。
4. 推送 commit 与 tag 到远端公开仓库，创建 GitHub Release，上传核心资产 `图纸上传助手.exe`（或与兼容规则匹配的最新稳定命名）与版本说明，确保 GitHub API 返回资产的 `sha256:` digest 以供现场端自动覆盖更新使用。

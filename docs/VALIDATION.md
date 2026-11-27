# 验证状态面板

本文记录当前主干代码与发布基线的最新离线验证结果、构建指纹及工作机真机验收矩阵。早期按时间记录的阶段性全量验证日志已归档至 [`docs/archive/VALIDATION_20261106.md`](archive/VALIDATION_20261106.md)。

---

## 1. 当前验证基线与构建指纹

- **已正式发布版本**：`v1.2.6`
- **当前主干功能基线**：提交 `79e5c28`（包含 P0/P1/P3 缺陷修复、仅限本批新建借用原地撤回及本轮重新编译的 helper；**尚未打 tag / 发 Release，待工作机真机测试通过后发版**）
- **最新构建产物指纹**（无代码修改之下的对应构建）：

| 工件路径 | SHA-256 | 说明 |
| --- | --- | --- |
| `app/caxa/caxa_helper/caxa_helper.cs` | `73950d7eb8add5713ddf6dbf0615d63130c4edec13497c5fd4e8083a4e9d9edc` | helper 源码（含本批借用撤回 `require_reference` 与 P0/P1 修复） |
| `app/caxa/caxa_helper/caxa_helper.exe` | `5a439f23bb775c037229ec367f58edff654e2482f94609a894a91c9032d5e3d2` | 本地基于私有 CAXA 2011r3 7-DLL 引用编通的 x86 二进制 |

---

## 2. 最新离线全量验证（2026-11-27，基于提交 `79e5c28`）

以下套件全部在隔离离线环境中运行通过（**合计 334 项用例通过**，未连接实际 CAXA 服务）：

| 套件 | 执行命令 | 结果 | 覆盖说明 |
| --- | --- | --- | --- |
| 字节码编译检查 | `python -m compileall -q app` | **通过** | 全部 Python 模块语法无误 |
| 单元测试与回归 | `python -m unittest discover -s tests -q` | **263 通过**（`skipped=2`） | 覆盖文件名、归属/借用、系列目录索引、SQLite 日志/防重、本批借用撤回护栏、DPAPI 与更新器校验（2 个跳过为本机未装可选 `pypdf`/`pypdfium2` 依赖） |
| 独立验收 | `python tests\run_external_acceptance.py tests\acceptance\independent_acceptance.py` | **19 通过** | 核心上传业务契约与故障路径 |
| 边界与 C# 源码提取 | `python tests\run_external_acceptance.py tests\acceptance\boundary_acceptance.py` | **9 通过** | 提取真实 `caxa_helper.cs` 方法独立编译及故障注入校验 |
| 真实 Tk UI 验收 | `python tests\run_external_acceptance.py tests\acceptance\ui_acceptance.py` | **41 通过** | 层级树编辑、拖拽、已有原件切换、核实窗口、仅限本批借用撤回菜单与 busy 冻结 |
| 仅工艺文档 Tk UI | `python tests\run_external_acceptance.py tests\acceptance\process_only_ui_acceptance.py` | **2 通过** | 纯 `Z02`/`Z03` 工艺文档批次界面与补建逻辑 |

> 注：以上测试用例不连接真实 CAXA 服务、不启动 `caxa_helper.exe` 连库，也未在本地构建完整单文件 `图纸上传助手.exe`。

---

## 3. 真机验证状态矩阵

下表区分**历史版本已在工作机/抓包验证过的功能**与**在 `79e5c28` 修改后仍待工作机小批验证的内容**：

| 模块 / 功能点 | 历史真机验证情况 | `79e5c28` 后当前状态 | 说明 |
| --- | --- | --- | --- |
| 基础登录、`PlmComApi` 设备注册与 EAP PATH 注入 | 已于 `v1.1.x` 在多台现场机验证 | **待回归确认** | `79e5c28` 加固了空密码、弹窗锁 `_autoLock` 与退出清洁注销 |
| 整机 Part 优先查询 & `product_folder_index` | 已在 `v1.2.6` 现场只读实测（76 目录/39 顶层，4 台整机约 0.25s） | **待回归确认** | 本轮将 `product_folder_index` 补充归入 `READ_COMMANDS` |
| 归属 / `NewOneLevelLink(1037)` 借用与 `LinkSubPart` 回读回退 | 已在 `v1.2.1+` 多批真机实测并核对 `link_endpoints` | **待回归确认** | `79e5c28` 新增 `link_class_id` 返回与空引用防御 |
| 自动追加版次弹窗（UIA）与 `Refresh()` 版次刷新 | 已部分验证，部分老旧复现依赖客户端状态 | **待真机验证** | 本轮修复了 `SendMessageTimeoutPtr` EntryPoint、`ClickChildContaining` 按钮类判定与防死锁，须真机实际触发一次重复框回归 |
| 「核实待定结果」自动回读与独立「发起审核打印」 | 线下全覆盖 | **待真机验证** | 需工作机现场实测写中断恢复与补发流程 |
| **仅限本批新建借用的原地撤回** | *新功能* | **待真机首次验证** | 已完成单测与 UI 离线全覆盖，须按 [`BUILD_AND_TEST.md`](BUILD_AND_TEST.md) §4.2 `T5` 完成工作机实测 |
| 自动覆盖更新器（`update_checker.py`） | 已在 `v1.2.0+` Release 链验证思路 | **待回归确认** | 本轮加固了 HTTPS 校验与替换失败回滚；待下一正式 Release 实测 |

---

## 4. 历史验证与归档链接

- 全量按日期追加的历史验证明细（`v1.0.0` ~ `v1.2.6` 各阶段测试项、真机任务号及对象 ID 核对）：[`docs/archive/VALIDATION_20261106.md`](archive/VALIDATION_20261106.md)。
- 阶段性能测量数据与历史现场修复记录：[`docs/archive/FIXES_AND_PERFORMANCE_20261113.md`](archive/FIXES_AND_PERFORMANCE_20261113.md)。

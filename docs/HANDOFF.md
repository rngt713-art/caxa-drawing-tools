# 项目交接记录（HANDOFF）

**状态**：正式已发布版本为 **`v1.2.6`**；`main` 分支最新开发基线为 **`79e5c28`**（**尚未发 Release**，待工作机真机小批测试通过后打 tag 发版）。

> 继续改代码、重构或处理外部评审前，请先读 [`BUSINESS_REQUIREMENTS.md`](BUSINESS_REQUIREMENTS.md)、[`IMPLEMENTATION.md`](IMPLEMENTATION.md)，涉及 helper/CAXA 接口时加读 [`CAXA_INTERNALS.md`](CAXA_INTERNALS.md)。

---

## 1. 本轮已完成工作（`v1.2.6` → `main`）

1. **P0 / P1 核心缺陷全量修复并编通 `caxa_helper.exe`**：
   - 同步私有仓库 CAXA 2011r3 7-DLL 引用至 `.gitignore` 忽略的 `vendor/caxa-2011r3/`（未混入 `System.Web.Extensions.dll` 以规避 `CS1703`），重新编译并通过 `caxa_helper.exe`（SHA-256 `5a439f23bb775c037229ec367f58edff654e2482f94609a894a91c9032d5e3d2`）。
   - 修复 C# 侧 `SendMessageTimeoutW` P/Invoke 入口缺失、Win32 控件点击类校验、弹窗守卫 `_autoLock` + `FindOwnedWindowByTitle` 死锁风险、`JavaScriptSerializer.MaxJsonLength` 溢出、序列化异常处理、空引用防护及流程/图纸 ClassID 兜底。
   - 修复 Python 侧 `TaskStaging` 泄漏、`product_folder_index` 归入 `READ_COMMANDS`、`CaxaBridge` 关闭与状态锁定、`half_product_target_state` 异常阻断、`apply_manual_borrows` 待核实边界、64-bit DPAPI ctypes 签名、0 字节陈旧锁清理、更新器 HTTPS 校验及 Tk UI 队列异常/销毁防护与 `borrow_to` 命名清理。
2. **新增「仅限本批新建借用的原地撤回」**：
   - 在四道硬护栏保护下支持右键原地撤回同批误挂的 CAXA 借用边（`OP_UNLINKED` 记录 + 服务端 `require_reference=true` 二道校验，绝不删 Part、归属边或历史借用）。
3. **P3 仓库卫生与文档按主题整合**：
   - 清理仓库历史残留大二进制与重复审查文件，对齐 `pack_source.ps1` / `.bat` / `config.example.json` / `smoke.cs`。
   - 将原 16 份交叉重复文档整合重写为 7 份专题活跃文档，并将历史过程文档移入 [`docs/archive/`](archive/) 保留追溯。

---

## 2. 当前待办：工作机真机验收

`79e5c28` 已完成 **334 项离线测试全绿**（263 单测 + 71 验收），下一步请在安装有 CAXA 客户端的工作机按 [`BUILD_AND_TEST.md`](BUILD_AND_TEST.md) §4 与 [`VALIDATION.md`](VALIDATION.md) §3 完成小批真机验证：

1. 重点验证 **T5（仅限本批新建借用原地撤回）**、**T3（重复图号 UIA 弹窗与版次刷新回读）** 与 **T4（待核实自动恢复及独立发起流程）**。
2. 确认真机小批无误后，再按 [`BUILD_AND_TEST.md`](BUILD_AND_TEST.md) §5 升级 `VERSION`、`app/version.py`、打包构建最终 EXE 并发布 GitHub Release。

---

## 3. 明确边界与已知限制

- **无 Authenticode 签名**：打包出的 EXE 仍无商业代码签名证书，现场需允许运行（GitHub SHA-256 仅作传输完整性校验，不作为独立发布者身份根）。
- **单机日志与并发范围**：SQLite 操作日志与 PID 文件锁只覆盖单机防重与重启恢复，不承诺跨机器并发提交去重。
- **已弃用 / 冻结模块**：`recognize_*` 视觉识图模块处于弃用状态；`agent-mcp/` 仅为一次性示例，不随正式上传端迭代维护。
- **多字母前缀**：`ZJ03.*` 等多字母前缀仍按业务规则拒绝，需明确业务变更确认后才会放宽。

---

## 4. 下一阶段规划

- 待工作机真机验收完成并发布下一稳定版本后，按需评估将 CAXA 装配关系与图纸只读查询（以及既有轻量查图体验）抽取或整合为独立工具 / 只读视图，不与上传写操作事务耦合。

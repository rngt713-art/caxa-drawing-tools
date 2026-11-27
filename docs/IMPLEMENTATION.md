# 架构与实现说明

本文面向研发与代码评审人员，说明 CAXA 图纸上传助手的当前源码架构、关键数据流、容错状态机及重点修复设计。

---

## 1. 总体架构与模块地图

上传端保持 **Tkinter 桌面界面 + Python 业务编排 + C# x86 子进程（`caxa_helper.exe`）** 三层结构，不依赖数据库直连或云端服务：

```text
app/
├── upload_main.py            # Tk 层级树界面、事件循环、busy 冻结、拖放与右键菜单
├── upload_ui_logic.py        # 冻结快照、层级树展示模型、图号迁移、待核实业务文案格式化
├── upload_core.py            # 核心编排：预检、分类落盘、三阶段执行、补链回读、本批借用撤回
├── relations.py              # canonical relations 建模、成环/多归属检查、动态系列目录匹配
├── drawing_bundle.py         # manifest.json 校验、图号解析（01/02/03/04 与单字母工艺文档）
├── staging.py                # TaskStaging 任务独占 PDF 暂存、校验与原子提交
├── journal.py                # SQLite（WAL）持久化日志、批次防重、PID 任务锁与设备状态
├── recovery_verifier.py      # 面向业务证据的只读自动回读核实（上传/关系/流程）
├── upload_recover.py         # 命令行恢复工具（list / confirm / no-write / release-lock）
├── config.py                 # 原子配置保存与 Windows 64-bit DPAPI 凭据加密
├── update_checker.py         # GitHub Releases 每日后台检查、SHA-256 校验与原子覆盖更新
├── version.py                # 版本读取与比较（对应仓库根 VERSION）
└── caxa/
    ├── bridge.py             # 单锁子进程管线、stderr 300 行排空、非幂等写超时不重放
    ├── uploader.py           # 面向 CAXA 业务的 Part/Product/Drawing/Relation/Workflow 操作
    └── caxa_helper/
        └── caxa_helper.cs    # .NET Framework 4 / x86 COM 调用 CAXA EAP SDK 与弹窗守卫
```

> **边界提示**：`app/recognize_*` 等视觉识别模块已弃用；`agent-mcp/` 仅为冻结的一次性接入示例，与正式上传端核心演进保持隔离。

---

## 2. 文件暂存与内容绑定（`staging.py`）

- `TaskStaging` 为每次任务建立独占暂存目录，将用户选中的本地源文件复制到规范目标文件名（由 `expected_drawing_filename()` 生成），写入过程采用「先写临时文件 + 校验 SHA-256/文件大小 + Windows 大小写不敏感重名检查 + 原子替换」。
- 上传阶段 `uploader.upload_item()` **只读取暂存目录内的副本字节**，在写入前再次复核指纹，确保用户编辑图号或移动本地原文件后不会误传或污染源文件。
- 异常路径（如 `prepare` 阶段冲突、校验失败）自动清理临时文件并保障句柄/目录 `close()` 不泄漏。

---

## 3. 关系模型、已有原件与目录索引（`relations.py`、`upload_core.py`）

### 3.1 Canonical 关系来源

- 导入旧格式包（`assemblies` / `manual_borrows` / `item.parent_code`）时一次性归一化迁移为单一 `relations` 集合，后续增删改全在 `relations` 上进行，界面删除的边不会因刷新而复活。
- 分类决策在任何远端写操作之前写入 SQLite（`kind="decision"`），重启或重试时优先读取已记录决策。
- 归属/借用确定后先检查**有向成环**与**多 `reference=False` 归属**冲突。

### 3.2 `03` 整机查找与系列目录索引

- **整机 Part 优先**：`find_existing_machine_part()` 在预检和上传前先按 `03.*` 图号精确查询 Part；唯一命中直接复用，多条命中以 `machine_part_duplicate` 阻止；仅未命中时才走 `find_product_for_machine()`（先 `ProductPartLink` 反查，再回退精确 `IdentityNo` 与 `名称 + ProductFolder` 消歧）。
- **会话缓存系列目录**：helper 只读命令 `product_folder_index` 在会话内递归一次 ProductFolder 树（不扫描目录下 Product），Python 端 `load_series_folder_index()` 与 `resolve_series_product_folder()` 动态匹配实际目录名与别名组（`OM/OV/OW → OW系列`、`LSYZ`、`GPZ`、`QT`、精确 `NS`、`SFC/SFD`、`HT合同特殊/HGB系列` 等），`03.W6.*` 留白交人工指定。

### 3.3 已有原件与真实关系预检

- `discover_existing_original_parts()` 按组件图号查询 CAXA 设计树已有 Part；`existing_relation_type()` 读取父件当前真实子关系：
  - 若父子已存在同类关系（归属或借用），预检置 `existing_relation=True` 并默认 `upgrade_only=True`，在 `active_relations_for_upload()` 中剔除该边避免重复挂接；
  - 若父子之间尚无同一条关系，且子件已在设计树存在原件，则把计划的 `own` 关系强制转为 `borrow` 并受 `upgrade_only` / `upgrade_only_user_set` 控制。

---

## 4. SQLite 日志、防重与恢复状态机（`journal.py`、`recovery_verifier.py`）

### 4.1 持久化结构与锁保护

- SQLite 启用 `WAL` 模式与 `busy_timeout=5000`，核心表包含：
  - `tasks`：以 `batch_id + server + port + user`（缺 `batch_id` 时兼容回退选中项内容摘要）确定任务身份；
  - `operations`：按 `upload`、`relation`、`workflow` 分阶段记录 `status`、远端 `object_id`、`class_id`、`class_name`、`iterator_no`、`link_type`、`detail` JSON（含源文件 SHA-256、流程覆盖 `drawing_ids` 等）；
  - `device_state`：按主机 `MachineGuid + 计算机名` 哈希保存本机 `PlmComApi.dll` 注册标记。
- **任务级独占锁**：`run_lock` 通过 PID 文件锁保护“查建任务 + 远端写操作”，并集成 Win32 `_pid_alive()` 存活性检测与 0 字节陈旧异常锁清理，避免多开并发写同时防止死锁卡死。

### 4.2 状态流转与后台自动核实

- **写前落盘**：任意非幂等写入前写入 `OP_RUNNING`；进程崩溃或下次加载任务时，`OP_RUNNING` 自动视为 `OP_PENDING`（待核实），**绝不自动重放**。
- **只读后台核实**：UI 点击「核实待定结果」只做列表多选，随后由 `_recovery_worker()` 在后台线程调用 `recovery_verifier.py`：
  - 上传：按宿主 Part + 目标文件名/后缀刷新回读 `part_drawings(refresh=True)` 验证 `object_id`、`class_id`、`iterator_no`；
  - 关系：调用 `verify_relation_pairs()` 按父件 `link_endpoints` 校验证实关系已存在；
  - 流程：调用只读 `workflow_info` 严格比对 `state == 2` 与关联文档 ID 集合。
- 人工确认“未写入”时，通过 `mark_no_write_in_plan()` 清除行内旧 ID/版次恢复为“待上传”，并在 `batch_id` 与当前 CAXA 账号一致时支持立即重试。

---

## 5. 仅限本批新建借用的原地撤回实现

为在不引入全量删改风险的前提下支持同批误挂借用的原地修正，撤回链路贯通以下四层：

1. **范围筛选（`journal.py` / `upload_core.py::batch_undoable_borrows`）**：仅筛选当前任务 `batch_id` 下满足 `stage="relation"`、`status="OP_OK"`、`link_type="borrow"` 且 `detail` 携带 `created_by_batch=True` 与 `parent_id/child_id/link_id/link_class_id` 的边；若任务存在 `OP_PENDING` 或已确认/待核实的流程记录，则整体拒绝撤回。
2. **写前真实回读与锁检查（`upload_core.py::undo_batch_borrow` & `uploader.py::unlink_batch_borrow`）**：获取任务级 `run_lock` 后按 `parent_id` 回读 `part_info(refresh=True)` 的 `link_endpoints`，确认目标 `link_id` 依然存在且 `reference=True`。
3. **C# 二道硬防线（`caxa_helper.cs::CmdUnlinkPartRelation`）**：接收参数 `require_reference=true`，实际调用删除前再次查验目标 `Part2Part` 链接属性确实为借用引用关系（`Reference == true`），仅删除链接关系自身，绝不删除任何 `Part` 或图纸实体，且删后复核确认已从父子列表消失。
4. **日志与界面同步（`upload_main.py`）**：
   - 结果不确定时写回 `OP_PENDING`（`relation_unlink_uncertain`）；
   - 撤回成功后把 `operations` 记录更新为 `OP_UNLINKED`，`upload_main.py` 右键菜单「**撤回 CAXA 借用（仅限本批）…**」在受 `set_busy()` 保护的状态下完成二次确认对话框后清理本地对应关系边，不引发重新挂接复活。

---

## 6. `CaxaBridge` 与 `caxa_helper.exe`（`bridge.py`、`caxa_helper.cs`）

- **子进程管线**：`CaxaBridge` 使用同一把锁串行管理子进程启动、`login` 状态与标准输入/输出收发；启动后立即启动独立后台线程持续排空 `stderr` 并保留最近 300 行，避免缓冲区写满挂死并在超时/崩溃时作为诊断附加到 `CaxaError.detail`。
- **读写命令分界**：
  - `READ_COMMANDS`（包含 `product_folder_index`、`part_info`、`part_drawings`、`find_product_by_part`、`workflow_info` 等）查询超时可安全报错并按规则清理会话；
  - `WRITE_COMMANDS`（包含 `upload`、`create_part`、`link_part`、`unlink_part_relation`、`start_workflow` 等）发生 `timed_out` 或 `response_lost` 一律标记 `uncertain=True` 交上层待核实，**禁止重试**。
- **C# 健壮性加固**：
  - 每次序列化前捕获异常、扩大 `JavaScriptSerializer.MaxJsonLength = int.MaxValue` 防大图纸树超限，并在异常退出路径确保 clean logout；
  - 写命令在 `Insert()`、`LinkSubPart`、物料更新或流程建链发生局部异常时统一输出结构化 `WriteError` 证据（`partial`/`uncertain`/已知对象 ID）；
  - 弹窗自动确认器使用 `_autoLock` 与受限查找 `FindOwnedWindowByTitle` 避免锁内死锁，严格校验 `Button` 控件类型、Win32 返回码与 UIA `SelectionItem`/`Toggle` 状态。

---

## 7. UI 并发模型、配置安全与自动更新

- **UI 并发隔离**：点击执行上传、预检、核实、撤回借用时由 `set_busy(True)` 冻结全部输入控件与菜单操作；后台工作线程只读取 `snapshot_settings()` / `snapshot_manifest()` 产出的只读字典副本，所有 UI 变更通过事件队列回到主线程；`_poll_events()` 在 `finally` 中安全重调度（附带窗口 `winfo_exists()` 销毁防护），日志文本框硬上限 5,000 行。
- **配置与 DPAPI**：`config.py` 通过同目录临时文件 + `fsync` + `os.replace` 原子保存设置，避免写入中途截断；Windows DPAPI 调用显式声明 64 位指针/结构体 ctypes 参数与返回签名（`argtypes`/`restype`），防止 64 位地址截断导致解密异常。
- **自动更新器（`update_checker.py`）**：严格校验 GitHub Release API 与资产下载地址 HTTPS 合法性，强制逐块核对 `sha256:` digest；替换旧 EXE 时先写出临时新文件与 `.old-时间戳` 备份，再以原子替换更新原安装路径，若替换或新版首次拉起失败则自动回滚旧文件并记录 `%LOCALAPPDATA%\caxa_upload_journal\update_error.log`。

---

## 8. 性能优化要点

1. **Part 优先免扫全库**：已有 `03` 整机不再加载全库上千个 Product 索引，直接精确查 Part。
2. **会话内索引与回读缓存**：helper 在单次连接会话中缓存 `product_folder_index` 与 `product_folder_paths`；每轮关系补齐回读内缓存节点查重但不跨轮缓存关系证据（保障每次回读真实有效）。
3. **单次长连接复用**：同批上传与后续回读尽量复用同一登录进程，只在出现 `EAP` 会话不可逆故障或显式超时时才重建 helper 进程。

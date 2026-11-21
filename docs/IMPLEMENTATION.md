# 实现说明（图纸上传助手修复）

日期：2026-11-05。实现模型：DeepSeek V4.1 Flash。验收方：Codex。
工作目录：`E:\codex目录\2026-11-05\b\work\dsh_fix`；`app/` 为可修改副本，原始代码未改。

## 1. 范围与约束

- 保持 Tk + Python 编排 + C# helper 子进程架构；不引入云/服务/复杂安全体系。
- 本机具备 CAXA 2011r3 引用 DLL；修订后的 helper 已用 x86 `csc` 编译通过。全部业务
  测试仍为离线 unittest / 桩对象，**未连接业务服务器、未上传图纸、未发起真实流程**。
- 不改工作流 `user_id` 身份绑定策略；不取消“高层失败→低层兼容回退”的冗余。
- ZJ03.* 规则保持现状（拒绝多字母前缀），需业务确认，文档不擅自放宽。
- 只修改本工作区；未改 Codex 的外部验收脚本。

## 2. 主要修改

### 2.1 文件内容绑定（TASK 1）

- 新增 `app/staging.py`：每个任务独占暂存目录，把**用户选中的源文件**复制为规范名，
  复制后校验 SHA-256/大小；先写临时文件并校验，再检查同名冲突（Windows 大小写不敏感），
  最后原子提交。冲突/校验失败不会覆盖已暂存字节，更不会碰源文件。
- `app/caxa/uploader.py::upload_item` 只上传暂存副本；编辑图号/名称后按
  `expected_drawing_filename()` 生成目标名，界面显示实际目标（`filename_warnings()`），
  源文件不改名、不被替换。
- 上传前再次核对指纹；缺失/冲突/不符都显式报错，不假成功。

### 2.2 关系唯一来源与业务规则（TASK 2、11）

- 新增 `app/relations.py`：`assemblies`、`manual_borrows`、`item.parent_code` 在导入时
  **一次性迁移**为 canonical `relations`，之后不再合并；GUI 删除的边不会复活。
- 默认规则：图号上一级前缀→`own`（Reference=False）；03 整机及跨图号父级→
  `borrow`（Reference=True）。`manual_override` 永远优先。
- 分类决策在**任何写操作之前**写入 SQLite 日志（kind=`decision`）；同一任务重试/重启
  恢复该决策，不会因为上次已创建 Part 就把 own 改成 borrow；新任务第一次一定现场查询
  设计树，不信任上传包里的 `auto_decided`。
- 分类完成后再校验有效图：检测环和多个 `own` 父级；借用父级允许有多个。
- 导入时按图号的直接上级前缀自动补全组件层级，已有显式关系优先；删除的自动关系
  不会在再次归一化时复活。
- 普通新零件先在 Product `01 通用件` 下按最近图号前缀建立归属；没有可用前缀时
  作为该 Product 的根 Part 建立。
- 03 整机在各自系列 Product 下建立；产品缺失时可用手动输入的系列名称和整机图号
  新建 Product，但目标系列 ProductFolder 必须已经存在。上传端不创建系列目录。
- borrow 只读查找后链接：父/子缺失分别记“缺父/缺子”；借用步骤本身不新建 Part。

### 2.3 关系失败显式统计、阻止自动发流程（TASK 3）

- 每条关系都有逐条结果（已借用/已归属/已存在/冲突/缺父/缺子/未执行/待核实/失败）。
- 已有链接类型不符→“冲突”，类型无法确认→“待核实”，查询失败（`children_error` 且无
  可用 endpoint 证据）直接报错且**不写**；不会把 `bool('false')` 当成 True。
- 文件失败、关系失败、关系待核实、必需关系未执行（`borrow_missing=false`）、没有已验证
  Drawing、流程未确认启动，任一存在即 `ok=False` 且不自动发流程；UI 汇总文件/关系/流程结果。

### 2.4 轻量持久化与防重（TASK 4）

- 新增 `app/journal.py`（标准库 SQLite）：
  - `tasks`：任务、服务器/端口/登录账号、摘要、状态、阶段；
  - `operations`：阶段、状态、远端对象 ID、ClassID/类名、版次、link_type、错误与证据。
- `OP_RUNNING` 视为“待核实”（进程崩溃/断电留下的执行中记录不会被自动重放）；任务级
  `run_lock` 独占锁覆盖“查找/创建任务 + 远端操作”，防止双开并发写。
- 任务身份 = 服务器 + 账号 + PDF 内容（含 SHA-256/大小），**不含**关系与流程选项：
  - 仅改关系、仅补发流程不会重传 PDF；
  - 内容或账号/服务器变化才形成新任务；
  - 相同批次再次点击返回 duplicate 结果，不再创建版次/流程；
  - “新建任务”是显式操作，但**不能绕过待核实**。
- 待核实恢复：`app/upload_recover.py`（`list` / `confirm` / `no-write` / `release-lock`）
  与 UI“核实待定结果”按钮；人工只更新指定操作（成功需给 ID/类型/版次证据；确认未写入
  才允许重试），其它已确认成功保留，不需要删库或整批重传。
- 不做跨机器 exactly-once 的承诺：本地日志是恢复辅助；同批图纸建议一人提交。

### 2.5 bridge 与 helper（TASK 5）

- `app/caxa/bridge.py`：进程句柄与登录状态同锁管理；**任何新进程先登录**再收业务命令；
  登录返回 false/`logged_in=false` 判失败；超时/丢响应标记 `timed_out`/`response_lost`；
  非幂等写（upload/create_part/link_part/reference/start_workflow/...）不自动重试。
- `CaxaError` 结构化保留 `stage/detail/created_process_instance_id/partial/uncertain/result`，
  `as_dict()` 不含凭据。
- `caxa_helper.cs`：
  - 登录检查返回值与 `HasLogin`，换账号先 Logout；
  - 流程 create 成功后所有失败路径带 `created_process_instance_id` 与 `stage`；
  - 上传返回**实际** ClassID（append 成功路径改用 `existingDuplicate.ClassID`，修掉空引用）；
  - `CmdUpload` 外层 try/finally 维护“上传命令执行中”，未预期异常返回
    `partial/uncertain`（附已知 object_id），不再当普通失败重放；
  - `new_version` 覆盖文件失败时放弃检入并返回结构化错误，不再假成功；
  - 自动确认只在 `upload` 命令执行期间，按精确标题、正文、当前目标文件名
    和唯一窗口处理；当前实现允许该对话框由 CAXA 自有进程创建；
    “导入文件确认对话框”按 Button 类、归一化文本和 IDOK/`WM_COMMAND` 逐级点击确定；
    重复对象框先唯一选中“追加新的版次”，读 `BM_GETCHECK` 确认后才点“确定”，
    无法确认则不点。

### 2.6 UI 冻结、快照与并发（TASK 9）

- `set_busy()` 统一禁用所有操作按钮、账号/服务器/策略/流程控件与清单选择；所有编辑、
  拖拽、勾选、增删关系、预检、手工借用、加载上传包入口都有 busy 拦截。
- 工作线程只读 `upload_ui_logic.snapshot_settings()` / `snapshot_manifest()` 生成的冻结
  快照，不再读 Tk 变量；日志/状态/结果统一经事件队列回 UI 线程。
- 完成/失败/异常都恢复交互；若上传期间账号/服务器被改动，结果不再合并成功状态；条目
  修改（图号/名称/类型/成品代码/父级）会失效原成功状态并重传。
- UI 编辑原子化：先校验全部字段再写；改图号同步迁移入/出边并同步 `item.parent_code`，
  删除父级组会删除其下所有边；刷新不再从旧集合恢复关系。

### 2.7 文档类型、版次与流程（TASK 7、10）

- 上传返回实际 ClassID：2D 图纸要求 1002 Drawing，工艺文档要求 1003 CappDocument；
  回退到其他类型→“待核实”，不进入流程集合、不自动删除。
- `append_iteration` 的**所有**成功路径都回读版次/历史并比较，无增长→待核实（保留
  object_id）；`reuse` 明确表示复用旧文档；`part_drawings` 查询失败视为“未知”而非“无”。
- 流程要求 `started=true` 且 process_id>0；否则“待核实”并保留已有流程 ID。工作流
  启动后必须回读 `WFProcessInst.state=2` 和完整 Drawing 集合；回读失败同样记为
  “待核实”。工作流 Drawing 集合由日志中全部已确认上传重建（旧成功 + 新成功）。
- 整机 Part 已建但挂接失败→部分完成/待核实（带 created_part_id），不记正常成功；
  `Login` 返回 false 不会报 ok。

### 2.8 上传包校验（TASK 8）

- `load_bundle` / `validate_bundle`：schema、版本（未知拒绝）、字段类型、kind 枚举、
  必填 machine、重复图号、路径穿越、文件存在性、导入时 SHA-256/大小核对；手工 PDF
  首次登记指纹，提交前复核。
- 仅对**选中执行**的行做严格字段校验：未勾选的异常行可保留供人工修改，不阻塞有效批次。

## 3. 测试与结果

离线（无 CAXA/无网络/无 EXE）：

| 套件 | 命令 | 结果 |
| --- | --- | --- |
| 本工作区回归 | `python -m unittest discover -s tests -v` | **88 tests OK** |
| 独立验收 | `python tests\run_external_acceptance.py ..\independent_acceptance.py` | **19 tests OK** |
| 边界验收 | `python tests\run_external_acceptance.py ..\boundary_acceptance.py` | **9 tests OK** |
| 真实 Tk UI | `python tests\run_external_acceptance.py ..\ui_acceptance.py` | **18 tests OK** |
| 仅工艺文档 Tk UI | `python tests\run_external_acceptance.py .\acceptance\process_only_ui_acceptance.py` | **2 tests OK** |

说明：外部脚本要求把临时目录建在其自身目录旁（本工作区外）。本机受限令牌无法在那些
位置创建 0o700 目录，因此 `tests/run_external_acceptance.py` **只读加载**外部脚本，仅把
`tempfile.TemporaryDirectory` 重定向到 `tests/_tmp`（默认权限），并为本机缺失的
`tkinterdnd2` 提供 `tests/_stub_deps` 桩；未修改外部脚本。Codex 侧可直接运行原脚本。

覆盖点包括：8 项旧复现的正确预期、own/borrow 默认与人工优先、借用缺对象不建、重试语义
稳定、删除关系不复活、重复提交与重启恢复、部分流程错误 ID、超时/丢响应待核实、关系失败
不发流程、文件哈希/版本/路径校验、UI 忙时冻结、待核实恢复命令。

## 4. 构建与分发

- `build_all.ps1`（工作区根）：`-All` / `-Helper` / `-Uploader` / `-Recognizer`；
  `-PlanOnly` 只检查环境与依赖；上传端、识别端分别 PyInstaller 打包到
  `dist\uploader`、`dist\recognizer`。
- `app\caxa\caxa_helper\build_helper.ps1`：需 CAXA 2011 引用 DLL；找不到会明确失败并
  拒绝在无源码对应关系时覆盖旧 exe（`-Force` 才覆盖且自动备份）；成功打印 exe SHA-256。
- `pack_source.ps1`：生成源码交付 zip + `SHA256SUMS.txt`，默认排除
  `caxa_helper.exe`、`__pycache__`、`config.json`、`dist/build`、测试临时目录。
- 仓库已删除旧的 `app\caxa\caxa_helper\caxa_helper.exe`（旧二进制，避免冒充新版）。
  发布前必须：工作设备编译 helper → 小批现场验收 → 记录 exe/源码 SHA-256。

## 5. 未实现 / 现场待验（明确限制）

1. helper 已编译；真实 ClassID、版次回读、关系 endpoint 字段、自动确认窗口归属仍需在
   业务客户端现场验收。
2. 权限/身份策略未改：`user_id` 仍由调用方传入；未新增权限拒绝分类，高层→低层回退冗余
   按用户要求保留（底层是否真正鉴权只能由受限账号现场验证）。
3. 不做跨机器 exactly-once；本地日志 + 独占锁只覆盖单机并发与重启恢复。
4. ZJ03.* 规则未放宽，需业务确认；工艺/图纸 ClassID 白名单以现场实测为准。
5. 自动确认仅在 upload 作用域内，且标题、正文、目标文件名和窗口唯一性全部
   匹配时执行；不满足任一条件仍留给人工选择。
6. 旧 `new_version` 路径仅做“复制失败不检入/不假成功”的保守修复；默认 UI 仍为
   `append_iteration`/`reuse`。
7. 本机存在少量早期实验/旧版测试临时目录（0o700 ACL 无法删除），打包脚本已排除，
   不影响源码交付与测试（`tests/` 下正常测试用例不使用它们）。

## 6. 现场最小验收表

| 场景 | 预期 |
| --- | --- |
| 正常账号上传图纸/工艺文档/追加版次 | 类型分别为 Drawing/CappDocument，版次实际增长 |
| 已发布/他人检出 | 明确失败或待核实，不靠 fallback 改写 |
| 新建 own 子件挂到所选父级 | Reference=False，父级不更换 |
| 借用缺失零件 | 不新建任何 Part，逐条报缺父/缺子 |
| 关系类型冲突 | 报冲突，不改写，不自动发流程 |
| 流程 create 后中断 | 保留流程 ID，状态待核实，可用 `upload_recover.py` 处理 |
| 同批重复点击 / 重启 | 不重复版次/流程；改关系或补发流程不重传 PDF |
| 上传时手工打开其他 CAXA 对话框 | 不会被自动点击 |

## 7. 2026-11-11 仅工艺文档目标

- `upload_core.collect_machine_targets()` 是 02/03 半成品/整机目标的统一来源。
  它从整机图号或工艺文档 `machine` 收集目标，按目标图号去重；03 的系列产品
  名称和目录同步到同一目标下的全部工艺文档。
- 预检和实际上传前都会检查目标系列目录、Product 和整机 Part。03 Product 缺失
  要求产品名称并可新建；Product 存在而 Part 缺失时只补 Part。
- 02 工艺文档按点号上级查找既有 Part；只允许创建缺失末级，并保持一个
  `reference=False` 归属。历史阶段“只接受既有 02 Part”的规则已被后续
  2026-11-18/19 规则取代。
- UI 在没有整机图时由工艺文档生成产品分组，并将所有同类 CappDocument 挂在
  产品节点下；Z02 仍作为半成品工艺文档显示。

## 8. 2026-11-11 设备首次注册

- `journal.sqlite3` 新增 `device_state` 表；设备标识由 Windows MachineGuid
  和计算机名哈希得到，不保存原始 GUID。
- 首次上传先执行 helper
  `ensure_plm_com_registered(force=true)`。helper 不再先探测，而是直接调用
  32 位 `regsvr32` 注册 `PlmComApi.dll`，再回读 COM 类。
- Python 仅在注册成功后写入 `plm_com_registration` 状态；记录包含
  `registration_id`、DLL 路径、注册返回和更新时间。
- 后续登录不再携带 COM 模式，也不包含 COM 检查代码。由于仍每进程重新登录
  CAXA，业务命令的登录保障不变。
- `CAXA_FORCE_PLM_REGISTRATION=1` 可忽略既有标记并重新注册。

## 9. 2026-11-12 自动追加版次修复

- 重复对象框必须属于当前 helper PID；在此之上还要匹配精确标题、正文、
  当前目标文件名、唯一窗口和正在执行的 upload 命令，不跨进程点击。
- 旧实现按文本匹配后用`BM_CLICK`，命中的可能只是标签，导致选项未选中。
  新实现通过 UI Automation 查找 RadioButton、CheckBox、Button，并回读
  SelectionItem/Toggle 状态。
- 不能用旧的`DocumentOfWFInstance`关系判断当前是否禁止修改。CAXA 反审会将
  当前状态改回“设计”并先生成一个版本；此时仍可能保留历史流程关系，但继续
  追加版次是合法的。
- 追加版次后的首次回读带`refresh=true`，helper先`Retrieve()`图纸实体；
  若仍与升版前相同，等待3秒后再`Retrieve()`回读一次。不重启helper，
  也不重试`upload`写入；已知远端对象ID始终保留。

## 10. 2026-11-13 上传计划 UI 收尾

- `upload_ui_logic.py` 新增待核实操作格式化：
  - `upload / relation / workflow` 分别显示“图纸上传 / 次级/借用关系 /
    审核打印流程”；
  - 上传和关系显示实际图号，关系显示 `父级 ← 子级`；
  - 阶段通过固定映射转为“上传确认、关系建链、流程启动结果确认”等文案，
    未知内部阶段也使用业务兜底文案；
  - 流程显示实例 ID、状态和从日志 `detail.drawing_ids` 读取的关联文档数。
- `upload_main.py` 新增 `PlaceholderEntry`。控件内部使用独立显示变量，
  空值且未聚焦时显示灰字；聚焦或输入后清除。绑定的真实变量始终保持空值，
  因此占位示例不会保存到 manifest。
- 完整编辑对话框增加：
  - 系列产品名称：`HW1.5-35(直连/水冷)`
  - 系列产品目录：`HW系列`
- 右键菜单增加“从列表中删除所选图纸”。右键已选行时保留多选；菜单命令
  只收集实际图纸行。删除经过纯函数
  `remove_planned_drawings()` 同步清理：
  - `items` 与 bundle `items`；
  - 任一端命中删除图号的 `relations`；
  - `manual_borrows` 中已删除父级、已删除子项及变空的记录；
  - 产品名称必填集合与失效的当前产品选择。
- 删除前明确确认“仅从列表移除，不改本地 PDF，也不删除 CAXA 远端对象”。
  系列目录、产品计划节点和外部 CAXA 节点因为不存在对应 item，不会被当作
  图纸删除。

## 11. 2026-11-16 自动恢复证据与单独发起流程

- 新增 `app/recovery_verifier.py`。恢复窗口不再把后台 ID 暴露成人工表单：
  - `verify_upload()` 根据 `item_code`、目标文件名和业务类型推导宿主 Part，
    通过 `part_drawings(refresh=true)` 回读当前图纸的 Object ID、ClassID、
    ClassName、IteratorNo 和 FileName；要求唯一匹配且类型正确。
  - `verify_relation()` 使用已知父级、子级和计划关系类型调用整批回读中的
    `verify_relation_pairs()`；不完整或错误关系保持待核实。
  - `verify_workflow()` 使用日志或界面可见的流程实例 ID 调用新增只读命令
    `workflow_info`，严格比较状态 `2` 和本批关联文档 ID 集合。
- `caxa_helper.cs` 的 `part_drawings` 增加每份文档的 `class_id` 和
  `class_name`；新增只读 `workflow_info`，返回流程名称、状态、开始时间和
  `WFInstanceWithDocument` 关联文档 ID。
- UI 的“核实待定结果”只在用户先确认业务事实后连接 CAXA 自动回读；任何
  隐藏 ID 都不再通过 `simpledialog` 询问。查询失败、结果不唯一或证据不匹配
  时保持待核实。
- `upload_bundle(..., workflow_only=True)` 增加补发流程模式：
  - 只检查业务计划，不校验本地 PDF 存在性；
  - 所选图纸必须全部存在于该任务已确认上传记录；
  - 所有计划关系必须已经有同类型确认记录；
  - 任一条件不满足时在连接写操作前阻止，绝不顺带上传或补建关系。
- 功能区新增“发起流程”按钮，复制当前勾选清单，强制
  `start_workflow=True` 和 `workflow_only=True` 后复用现有后台任务与 UI
  完成事件。

## 12. 2026-11-17 已有原件只升版/升版后借用

- `.pdf` 为当前规范行为；上传前 `CaxaUploader._repair_legacy_pdf_name()`
  只对“去掉 `.pdf` 后名称完全一致”的历史 Drawing 自动执行属性改名，
  `set_drawing` 回读 `name_after` 一致后继续；其他差异不碰，失败则阻止。
- `upload_core.discover_existing_original_parts()` 在预检和实际上传连接后
  按选中组件图号查询 CAXA Part。
- `CaxaUploader.find_part_candidates()` 返回精确图号的全部 Part；查到多个时
  预检上报 `duplicate_existing_parts`，实际上传在写操作前直接阻止。
- `apply_existing_original_rules()`：
  - 设置 `existing_original`、`existing_original_part_id`；
  - 首次默认 `upgrade_only = 没有计划关系`；
  - 用户手动切换后通过 `upgrade_only_user_set` 保持，不被重试覆盖；
  - “升版并借用”但没有父级时返回阻止项。
- `refine_relations_from_tree(..., existing_original_codes=...)` 将已有原件的
  own 关系强制改成 borrow；后续分类阶段也优先保留该规则。
- `active_relations_for_upload()` 会移除 `upgrade_only` 图纸的全部关系，
  保证只升版时不会写借用/归属边。
- UI 树增加“只升版”列：
  - 组件行显示 `☑ 不借用` / `☐ 升版并借用`；
  - 类型列显示“已有原件”；
  - 单元格点击切换，并写入 `upgrade_only_user_set`。
- “增加次级条目”创建后调用 `_center_dialog_on_parent()`，与双击编辑框一致。

## 13. 2026-11-17 CAXA 依赖 PATH 与空密码

- `caxa_helper.cs::EnsureCaxaDependencyPath()` 从 `PlmComApi.dll` 所在目录
  推导 `CAXA EAP\1.0\bin`，并补充常见 CAXA PLM 的 `CAXABOM\bin`、
  `CAXAEDM\bin`。存在的目录会按去重顺序前置到 helper 进程 PATH。
- 设备注册的 `regsvr32` 和每次登录前都调用该函数，因此设备标记已存在时
  登录仍具备正确依赖搜索路径。
- 现场验证：无 EAP PATH 时 32 位 `LoadLibraryW` 返回 126；加入两个 EAP
  目录后加载和 COM 实例化成功。新 helper 直接注册返回
  `registered=true`。
- `upload_ui_logic.snapshot_settings()` 只校验 IP、用户名和端口，空密码
  作为合法字符串继续传递；`_save_settings()` 始终写入密码字段，因此用户
  清空旧密码后不会继续复用旧 DPAPI 密文。
- `TryActivateExistingPlmCom()` 在设备标记不存在时先验证 CLSID 是否已可
  实例化；成功时返回 `registered=true`、`registration_attempted=false`，
  不重复弹出 UAC，也不执行 `regsvr32`。
- 需要新注册时，`RegisterPlmCom()` 生成临时 `.cmd` 并请求 UAC。临时脚本
  在提升上下文内部显式设置 EAP `bin`、`template\script` 和常见 CAXA PLM
  目录，再执行 32 位 `regsvr32`，把真实退出码写入结果文件供 helper 读取。
- `CaxaUploader.find_product_for_machine()` 先用整机图号查 Product；未命中时
  用 `find_products_by_name()` 做名称精确搜索。存在同名候选时通过
  `_product_in_folder()` 读取 `dump_product_by_id` 的直属 ProductFolder
  对象 ID，只接受目标目录中的唯一 Product。
- `CmdPartDrawings(refresh=true)` 和 `CmdDrawingHistory` 优先调用 CAXA 实体的
  `Refresh()`，仅在 Refresh 失败时回退 `Retrieve()`。`Retrieve()` 只刷新当前
  低层对象，不能保证父 Part 的图纸集合和版次历史失效重查。

## 14. 2026-11-18 02 半成品叶子、规范化迁移与批量核实

- `drawing_bundle.half_product_group_code()` / `half_product_parent_codes()`
  从 `02.A36.02.AR02` 依次得到最近上级 `02.A36.02`、再上一级 `02.A36`。
- `precheck_with_caxa()` 对 02 目标返回：
  - `part_will_create=true`：Product `02`、最近点号上级存在、目标叶子缺失；
  - `missing_process_parts`：根 Product、点号上级或唯一性条件不满足；
  - `duplicate_process_parts`：目标/上级多个原件或多个非借用归属。
- `upload_ui_logic.normalized_filename_fields()` 在“一键规范文件名”时重新推导
  kind 和成品代码，旧错误成品代码不再保留。
- `upload_ui_logic.rekey_planned_codes()` 把图号变化同步到 items、relations 和
  manual_borrows，并去重规范化后重合的关系边。
- “核实待定结果”改为单个 Tk 列表窗口：
  - 多选后自动回读成功；
  - 多选后确认未写入；
  - 全部确认未写入并直接重新上传。
- `upload_ui_logic.mark_no_write_in_plan()` 将上传行恢复为“待上传”并清除旧
  Drawing ID、ClassID、版次和路径，使界面与日志都可立即重试。
- `CaxaUploader.ensure_half_product_part()` 与 `half_product_target_state()`
  从最近点号向上寻找唯一存在的 Part 上级。缺失末级 Part 链接到该上级，
  不再直接挂到 Product `02`。
- 多个产品/上级挂接允许存在，但只允许一个 `reference=False` 非借用归属；
  多个非借用归属在写入前阻止。`part_info.link_endpoints` 优先于 EAP
  `ParentParts()` 的空 ID 回读。
- helper 新增只读刷新后的 `part_info` 以及 `unlink_part_relation`。后者只按
  parent/child 验证并删除指定低层 Part2Part 关系对象，不删除端点 Part。
- helper 自身删除产品关系或 Part 关系时启用删除确认自动点“是”的作用域；
  窗口必须属于当前 helper PID，不处理用户手工删除产生的其他窗口。

## 15. 2026-11-19 上传端评审整改

- `content_digest()` 只序列化 `_selected_items()`。未勾选或已跳过行不再改变
  批次摘要；新增“未勾选行修改后仍命中原任务”的回归。
- `CaxaBridge` 启动 helper 后立即排空 stderr，保留最近 300 行，并在进程异常、
  超时或响应无法解析时附到 `CaxaError.detail`。
- `WRITE_COMMANDS` 补齐 `unlink_part_relation`，产品/Part 关系删除不会按幂等
  命令自动重试。
- 待核实窗口只在 UI 线程完成列表选择；CAXA 登录、连接和逐项回读改由
  `_recovery_worker()` 后台执行，结果通过 `recovery_done/recovery_error`
  事件返回。
- `_poll_events()` 在 `finally` 中重新注册；单次事件异常写入 UI 日志后继续
  下一轮。UI 日志限制为最近 5,000 行。
- `config.save()` 使用同目录临时文件、flush/fsync 和 `os.replace`；替换失败
  保留旧配置并删除临时文件。
- `TaskJournal` 增加 `busy_timeout=5000`。
- C# 新增统一 `WriteError()` 证据结构，并加固：
  - `create_part` / `create_product` Insert 后异常；
  - Product 目录挂接失败/回读失败；
  - `link_product_to_folder`；
  - `ensure_part_material_code` 更新后异常/回读不一致；
  - `link_part` 未处理异常和低层建链异常。
- 同一账号提示自动确认增加 `_loginInProgress` 和当前 helper PID 双重约束。
- 新增手动借用成功、缺父、写后不确定、连续两条第二条失败测试；新增配置
  原子写、stderr、非幂等关系删除和 UI 事件继续轮询测试。
- 识图端未修改；旧一体化上传入口未删除，留待识图端整改。

## 16. 2026-11-20 版本号与 GitHub Release

- 根目录 `VERSION` 和 `app/version.py` 固定当前正式版本 `1.0.0`。
- UI 标题显示版本号；`build_all.ps1 -Uploader` 读取 `VERSION`，输出
  `图纸上传助手-v1.0.0.exe`。
- GitHub Release 资产固定为：
  - `CAXA图纸助手-v1.0.0.exe`
  - `README-v1.0.0.md`
- 源码使用公开仓库，不再制作日期式源码/分发包。
- 公开树中的真实账号姓名替换为 `上传账号A`、`审核账号`、`测试账号`、
  `历史作者`、`操作账号` 等角色占位。
- 当前私有 Git 历史保留作归档；公开仓库使用无父提交的 `v1.0.0` 根提交，
  避免历史文档中的旧姓名被公开。

## 17. 2026-11-20 每日检查与自动覆盖更新

- 新增 `app/update_checker.py`：
  - 查询 `rngt713-art/caxa-drawing-tools` 的 `/releases/latest`；
  - 只接受 `vX.Y.Z`，按三段数字比较版本；
  - 选择 `CAXA-Drawing-Uploader-vX.Y.Z.exe`，要求 GitHub 返回
    `sha256:` digest；
  - 下载到临时文件，逐块计算 SHA-256，校验通过后原子改名；
  - 更新状态写入
    `%LOCALAPPDATA%\caxa_upload_journal\update_state.json`，记录当天是否已
    检查以及最近一次 Release 信息。
- `UploadApp` 在正式打包 EXE 中每天首次启动 700 ms 后后台检查；检查结果经
  现有事件队列回到 Tk 主线程。检查或下载期间只禁用更新按钮，不冻结上传表单。
- 发现新版后按钮变为“下载 vX.Y.Z”。下载完成并校验后：
  1. 启动下载的新版 EXE，参数为
     `--apply-update <旧 EXE 路径> <旧进程 PID>`；
  2. 当前窗口关闭；
  3. 新版更新模式等待旧 PID 退出；
  4. 旧 EXE 改名为同目录 `.old-YYYYMMDD-HHMMSS` 备份；
  5. 新版复制到旧路径并校验 SHA-256；
  6. 启动旧路径的新内容后退出更新模式。
- 覆盖或重启失败时恢复旧 EXE，并写入
  `%LOCALAPPDATA%\caxa_upload_journal\update_error.log`；窗口版同时显示错误框。
- 源码运行不执行自动每日检查；可用 `CAXA_ENABLE_UPDATE_CHECK=1` 强制测试，
  `CAXA_DISABLE_UPDATE_CHECK=1` 禁用。源码模式下载后只启动新版，不覆盖
  Python 解释器路径。

## 18. 2026-11-20 GPZ 特殊目录、批次身份与复审整改

- `relations.series_product_folder()` 对 `03.GPZ.*` 返回 `GPZ钢瓶组`；
  其他系列仍按 `<系列>系列`、HT/HGB 等既有规则处理。
- `UploadApp` 在打开上传包、手工拖入或创建关系计划时生成稳定 `batch_id`，
  并随上传包保存。`content_digest()` 优先使用
  `batch_id + server + port + user`，勾选变化不再改变任务身份；没有
  `batch_id` 的旧调用继续使用原选中内容摘要。
- 成功上传记录新增 JSON detail，保存 `file_name/target/sha256/size`。
  续传时当前 item 的 SHA-256 与确认记录不一致，会从本次 confirmed 集合移除并
  重新上传；旧的非 JSON detail 继续按兼容记录处理。
- 更新下载期间 `_blocked_by_update()` 阻止上传、手工借用和单独发起流程。
  下载完成但 UI 仍 busy 时保存 `_pending_update`，由 `set_busy(False)` 在任务
  结束后启动覆盖重启。
- `apply_update()` 改为先复制新 EXE 到同目录临时文件并 fsync、复制旧 EXE
  到 `.old-时间戳` 备份、校验两者，再 `os.replace(temp, old)` 原子替换。
- 更新器启动新版前清理 `_PYI_*` 和 `_MEIPASS*` 环境变量。最终进程仍启动
  `old` 的原路径，因此桌面/开始菜单快捷方式不会因版本文件名变化而失效。
- `UpdateStateStore.cached_update()` 对非法版本/损坏缓存返回 `None`，避免
  UI 初始化被坏状态文件阻断。
- helper 部分写入保护补充：
  - `CmdCreateProduct()` 的 ProductFolder 挂接异常返回带 product ID 的
    `partial/uncertain`；
  - `CmdLinkPartCore()` 高层 `LinkSubPart()` 抛异常后不再自动走低层，避免重复边；
  - `CmdSetDrawing()` 写后异常统一返回 `WriteError`；
  - `CmdUnlinkPartRelation()` 删除异常或删除后仍存在关系时返回关系 ID 证据。

## 19. 2026-11-21 LinkSubPart 安全回读与低层回退

- 新增 `FindPart2PartChild(parent, childId)`：刷新父 Part 并读取直接子关系。
- `CmdLinkPartCore()`：
  - 高层调用前已有同子件关系时直接返回现有关系；
  - `LinkSubPart()` 抛异常后先回读；
  - 回读已存在则返回现有 link ID；
  - 回读确认不存在时才执行低层 `NewOneLevelLink(1037, ...)`；
  - 回读异常仍由低层结果/上层最终验证决定，不盲目确认成功。
- 事件与已创建 Part ID 清单见 `docs/INCIDENT_20261121_LINKSUBPART.md`。

## 11. 2026-11-13 恢复一致性与性能修复

本节为当前源码；上文的旧测试数量和已编译产物描述属于各历史阶段。

- `journal.resolve_operation` 与 UI/CLI 共用操作类型证据校验；恢复不擦除已有
  ID/类型/版次和结构化 detail。流程需要运行状态 2 与完全匹配的文档 ID 集合。
- `upload_core` 对旧成功上传记录增加完整性防线；显式非法类型不能被类名覆盖，
  类型与图号必须一致。旧流程成功记录须有正实例 ID、明确 started 和计划文档集。
- 关系补齐与最终回读同步 journal、结果行、状态计数；补齐前写 RUNNING，未知结果
  保持待核实；回读不完整不能当成缺链后重放。每轮读取父关系不复用旧快照。
- helper 上传入口清空本条 `_uploadCurrentObjectId`。其真实 `CmdUpload` 方法
  已在独立 C# 测试中编译，注入“上一张成功、下一张提前异常”及“当前部分写入异常”。
- 识图共用 `drawing_bundle` 分类/命名，02/03 工艺文档均支持。导出在副本上规范
  `name`，原 OCR 名称保留到 `display_name`，并在写出前做统一字段验证。
- `matcher` 从 basename/stem 提取整机编号；`recognize_core` 复用首次拆分结果；
  `LocalVision` 对成功健康检查保留 5 秒 monotonic 缓存，失败立即失效且不重试问答。
- 验证：142 单元、19 独立、9 边界、21 Tk、2 工艺专用 Tk，193 项全部通过，
  compileall 通过。真实 PDF 回归使用 pypdf/pypdfium2，仅模型响应为桩。
- 2026-11-16 已在具备 CAXA 2011r3 引用 DLL 的本机完整编译 helper，并重编
  PyInstaller 上传端；发布目录中的 EXE 已与本轮源码对应。仍需真实 CAXA
  客户端做自动恢复和补发流程的小批验收。

用户确认的默认升版和图号唯一边界见 `BUSINESS_REQUIREMENTS.md` §13；
具体性能测量及使用说明见 `FIXES_AND_PERFORMANCE_20261113.md`。

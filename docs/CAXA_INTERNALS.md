# CAXA 2011r3 内部接口、抓包与踩坑手册

本文汇总针对 **CAXA 协同管理 2011（EAP 2011r3）** 客户端的实测抓包证据、对象模型关系、SDK 异常行为与现场故障排查方案，主要供修改 `caxa_helper.cs`、`uploader.py` 与排查线上问题使用。

---

## 1. 核心对象模型与 ClassID 速查表

| ClassID | 类名 / 内部名称 | 业务含义与角色说明 |
| ---: | --- | --- |
| `1002` | `Drawing` / `drawing` | 二维工程图纸（CAD/PDF），上传后必须校验返回真实 ClassID 为 `1002` |
| `1003` | `CappDocument` | 工艺文档（`J/M/P/T/Z` 单字母前缀文档），必须校验返回真实 ClassID 为 `1003` |
| `1008` | `Person` | CAXA 人员对象（例如 `审核账号` Person `66`、DCC Person `8`） |
| `1012` | `Product` | 产品根节点，关键业务产品：`01` 通用件、`02` 半成品、`04` 虚拟件（归通用件组件管理体系），以及各 `03` 系列产品 |
| `1013` | `EAPProductFolderEntity` | 产品目录（ProductFolder），顶层与二级目录（如 `HT合同特殊/HGB系列`）均为此类 |
| `1019` | `Part` | 零部件主对象，拥有唯一图号 `IdentityNo` 与物料编码 `MaterialNo` |
| `1036` | `ProductPartLink` | 产品总装件关联：Product 侧角色 **`ProdWithTopAssembly`**，Part 侧角色 **`TopAssemblyInProd`** |
| `1037` | `Part2Part` | 零件父子关系对象：`Reference=false` 为唯一归属、`Reference=true` 为可多父的借用 |
| `2466` | `WFProcessInst` | 审核流程实例（正常启动运行中状态 `state = 2`，`OPEN__RUNNING`） |
| `2467` | `WFActivityInst` | 流程活动实例 |
| `2468` | `WFWorkitem` | 流程待办工作项（审核通过后 `state = 3`，`submit_mode = "通过"`） |
| `2593` | `WFWorkitemPerform` | 工作项执行人关系：工作项侧 `AssignmentOfPerson`，人员侧 `AssignmentPerform` |
| `3064` / `3066` | `File` / `vaultfile` | 上传创建的文件对象与库内存储对象 |
| `3221` / `3224` / `3225` | `drawingItr` / `drawingRevcmp` / `drawingItrfile` | 图纸版次及与版本/文件的关联；追加版次复用了 `1002` master 与 `drawingRev`，只追加新版次链 |
| `3305` | `WFInstObjDocument` | 流程「关联对象」底层关系（非普通 `WFAssociatedObject`）：流程侧 `WFInstanceWithDocument`，文档侧 `DocumentOfWFInstance` |

> **关键业务认知**：CAXA `Part.MaterialNo` 对应界面上的“物料编码”；`part_info.parents` 中部分字段（如 `parent_identity`/`parent_name`）在 EAP 读取时可能为空，判断真实父子链接必须优先以 `link_endpoints[].roleA / roleB` 为准。

---

## 2. 审核打印工作流抓包与调用链

通过抓取客户端往 `8900/tcp`（文件与对象）及 `8903/tcp`（Workflow Server）的二进制 RPC 通信，确认「审核打印」流程的标准参数与调用链如下：

- **固定流程参数**：`WFTemplate = 1`、`WFProcessTemplate = 31`、`process_def_id = "2"`、`activity_def_id = "4"`、审核人 `Person ID = 66`、`DCC Person ID = 8`。
- **上传端发流程链路（`start_workflow`）**：
  1. `CxDalWfConnection.connect(user_id)`；
  2. `CxDalWfProcess.create(1, 31, "2", name, description, notify)`（创建成功即锁定并回传 `created_process_instance_id`）；
  3. 逐条创建 `WFInstObjDocument(3305)` 关联本批全部确认的 Drawing(`1002`) 与 CappDocument(`1003`)；
  4. `designate_dynamic_performer(process_id, "2", "4", 1, "66")` 指定审核人；
  5. `CxDalWfProcess.start(process_id)`；
  6. 回读 `WFProcessInst.state == 2` 及 `WFInstanceWithDocument` 集合校验无遗漏。
- **主管审核端（独立工具）工作项动作**：`CWfWorkItemStub.Pick(workitem_id)` 接收 → 发布全部关联图纸 → `DesignateNextPerformer(workitem_id, "2", "5", 1, "8")` 转 DCC → `Submit(workitem_id, "通过")`。

---

## 3. 高级查询抓包与 SDK 反查路径

2026-11-26 抓包证实 CAXA 客户端「高级查询」绕过 `8900/tcp` EAP RPC，直接连接 SQL Server `1433/tcp` 执行 `T_Product` + `T_ProductPartLink` + `T_Part` 联表过滤 `IdentityNo LIKE @P1`。

为恪守**严禁直连数据库与保存 DB 凭据**的安全红线，helper 通过已登录的 EAP `ObjectStore.NewQuery()` + `QueryCondition` 实现等价反查（`find_product_by_part`）：

1. 查询主类设定为 `Product`，起始范围可设对应 `ProductClassification`（如按实际结构检索）；
2. 通过 `ProductPartLink(1036)` 固定双侧角色：`ProdWithTopAssembly`（Product）与 `TopAssemblyInProd`（Part）；
3. 过滤条件匹配目标整机 `Part.IdentityNo`；
4. 再结合会话内缓存的 `product_folder_paths` / `product_folder_index` 读取 Product 真实所在目录。

**现场实证案例**（说明为何不能按图号前缀猜测 Product 编号或目录）：
- 整机 Part `03.HT.HGB05-05025B`（ID `44135`）挂接在 Product `687`（`03.HT.HGB05-05025`，位于 `HT合同特殊/HGB系列`）；
- 整机 Part `03.HX21.E20H-03L`（ID `44510`）挂接在 Product `1377`（`03.HX21.E20H-01`，位于 `HX系列`）。

---

## 4. `LinkSubPart` 事故与低层安全回退

- **事故背景（2026-11-21）**：部分客户端调用高层 `LinkSubPart()` 时会直接在**未建链状态**下抛出“未将对象引用设置到对象的实例”；而若高层调用后异常但不经检查就直接强行重试低层建链，则又存在**部分写入后重复建链或串父级**的风险；并且同一子件借用到第二台整机时，高层 `LinkSubPart` 可能误移动或复用首条归属边。
- **最终安全策略**：
  1. **只走独立低层借用**：多父级借用（`reference=True`）固定调用低层 `NewOneLevelLink(1037, parent, child)` 插入独立 `Part2Part` 关系，禁止替换为 `LinkSubPart`。
  2. **新零件归属建链（`CmdLinkPartCore`）**：
     - 调用 `LinkSubPart` 前先刷新父 Part 并通过 `FindPart2PartChild()` 检查是否已存在目标子关系，存在则直接复用返回；
     - `LinkSubPart` 抛异常时**必须先刷新并回读**父子关系：确认已存在则返回现有 link ID；确认不存在才允许安全走 `NewOneLevelLink(1037, ...)` 回退；回读仍异常则保留 `WriteError`（`uncertain`）交上层人工核实。
- **撤回边界（`CmdUnlinkPartRelation`）**：仅凭经核实的 `parent_id`/`child_id`/`link_id` 在服务端复核后删除指定 `Part2Part` 链接，批内原地撤回场景强制传入 `require_reference=true` 验证为借用边。

---

## 5. `PlmComApi.dll` 注册、运行 PATH 与 EAP 缓存机制

### 5.1 Win32 Error 126 / `0x8007007E` 与依赖 PATH

- 现场排查发现即使 32 位 CLSID `{F7950E30-7112-4D20-8610-2196CBDE2511}` 与 `PlmComApi.dll` 存在，若系统 `PATH` 缺少 EAP 运行目录，32 位 `LoadLibraryW` 仍会报 `126`（找不到指定模块）。
- 因此 helper 在设备注册和每次 `login` 前均调用 `EnsureCaxaDependencyPath()`，按去重顺序将以下存在目录前置到当前进程 `PATH`：
  - `...\CAXA EAP\1.0\bin`
  - `...\CAXA EAP\1.0\template\script`（缺此目录仍报 126）
  - 常见 `CAXABOM\bin`、`CAXAEDM\bin`
- **提权 UAC 注册**：通过 `ShellExecute + runas` 启动高权限子进程时不一定会继承父进程临时修改的 `PATH`，因此首次注册改为生成临时 `.cmd`，在提权脚本内部显式注入完整 `PATH` 后再调用 32 位 `SysWOW64\regsvr32.exe` 并把真实退出码回写到结果文件；在此之前先尝试 `TryActivateExistingPlmCom()` 实例化验证，若本机 COM 已可用则直接写入 `device_state` 标记、跳过不必要的 UAC 弹窗（设置 `CAXA_FORCE_PLM_REGISTRATION=1` 可强制重注）。

### 5.2 EAP 缓存刷新与字段兼容

- **必须优先 `Refresh()`**：CAXA SDK 反射与现场实测（如 `Drawing 20475` 实际已升到 `l.2`、回读仍卡在 `l.1`）证明低层 `Retrieve()` 无法使父级图纸列表及历史版本缓存失效。`CmdPartDrawings(refresh=true)` 与 `CmdDrawingHistory` 必须优先调用实体的 `Refresh()`，失败时再回退 `Retrieve()`。
- **文件名与版次字段兼容**：部分环境下 `part_drawings` 的实际文件名保存在 `name` 字段而 `file_name` 为空，恢复核对逻辑必须同时匹配两者；同时不能用旧 `DocumentOfWFInstance` 历史流程关联判定禁止升版，反审回到「设计」态的图纸可正常追加版次。
- **导入/追加区分**：上传弹窗中的“在现有版本上追加新的版次”底层是在同一 `Drawing(1002)` 与同一 `drawingRev` 下追加 `drawingItr(3221) + File(3064)`；不能将其与 `EAPDrawingEntity.NewRelease()`（`new_release`）混淆。

---

## 6. 弹窗自动确认与客户端阻塞排查

- **严格守卫矩阵**：
  1. **导入文件确认对话框**：精确标题、正文及 `Button` 类归一化匹配（兼容 `确定(O)` / `&确定` / `IDOK`），在 `upload` 命令作用域且窗口归属约束下处理；
  2. **对象已经存在提示框**：必须限定当前 helper PID 与当前目标文件名，且通过 WPF UI Automation（`AutomationElement`）选中「追加新的版次」单选框并回读 `SelectionItemPattern` 验证选中状态后才允许点击「确定」（避免过去 `BM_CLICK` 仅点到文本标签导致误操作）；
  3. **同一用户已经登陆提示框**：精确校验标题“提示框”、正文“同一个用户已经登”与错误码 `0x83060607`，且绑定 `_loginInProgress` 与当前 helper PID；
  4. **关系删除确认框**：仅在 helper 自身执行关系删除且窗口归属当前 PID 时点击“是”。
- **客户端模态阻塞**：若本机桌面上的 CAXA 客户端停留在“确定退出系统吗？”或授权模态提示框，后台 helper `login` 通信会长时间无响应；排障时需先检查并关掉客户端阻塞模态框。

---

## 7. 原始抓包与事件归档追溯

- 工作流抓包原始分析：[caxa_workflow_capture_analysis_20261105.md](archive/caxa_workflow_capture_analysis_20261105.md)
- 高级查询 SQL 抓包原文：[caxa_advanced_search_capture_20261126.md](archive/caxa_advanced_search_capture_20261126.md)
- `LinkSubPart` 空引用事故日志：[INCIDENT_20261121_LINKSUBPART.md](archive/INCIDENT_20261121_LINKSUBPART.md)
- 早期业务与真机发现原文：[BUSINESS_AND_CAXA_FINDINGS_20261106.md](archive/BUSINESS_AND_CAXA_FINDINGS_20261106.md)

# HANDOFF — 图纸上传助手修复（dsh_fix）

更新时间：2026-11-06。状态：**完成（status=complete）**，已按 REVIEW_FEEDBACK.md 全部 23 项整改，并补充流程启动后的状态与图纸集合回读。
实现模型：DeepSeek V4.1 Flash；验收方：Codex。

> 跨窗口继续工作前必须先读 `docs/BUSINESS_REQUIREMENTS.md`。用户确认的业务规则和
> 刻意保留的高层→低层兼容回退不是待优化项。

## 2026-11-20 v1.2.0 GPZ 特殊目录与复审整改

- 当前版本升级为 `v1.2.0`。
- `03.GPZ.*` 固定映射到 CAXA 既有 ProductFolder `GPZ钢瓶组`，不再生成
  `GPZ系列`；GPZ 产品显示名中的 `/` 不影响图号识别。
- 上传清单增加稳定 `batch_id`。勾选变化、追加行和 workflow-only 子集不再
  创建新任务或重传已确认项。
- 成功上传 detail 保存 SHA-256；同图号内容变化会重新上传当前内容。
- 更新下载期间阻止上传/关系/流程写入；下载完成但仍有任务时延迟到 busy
  解除后覆盖重启。
- 覆盖更新改为“临时新文件 + 旧备份 + 一次原子替换”，损坏 update state
  不再阻止启动。
- helper 补齐 Product 目录挂接、LinkSubPart、set_drawing 和关系删除的
  部分写入证据。
- Authenticode 仍未签名，已在
  `docs/REVIEW_RESPONSE_20261120.md` 明确列为外部证书限制。
- 验证：204 内置（2 个可选 PDF 依赖跳过）、19 独立、9 边界、29 Tk、
  2 工艺 Tk；`compileall`、helper x86、上传端 one-file 和真实覆盖重启冒烟
  通过。
- 当前上传端：
  `f01b20a17abc3c4a2eff7e4accbe1a37f32091b881bd11a6cae9499e9fb5cccd`
- 当前 helper：
  `ceac64b89da36bc79675248af40f1a54fee8b1d5fb3b313c961cb7f51e9cf566`
- 下一步：提交、发布 `v1.2.0` Release。

## 2026-11-20 v1.1.0 每日检查与自动覆盖更新

- 当前版本升级为 `v1.1.0`。
- 正式 EXE 每天首次启动后台检查 GitHub 最新 Release；当天已检查时不重复联网。
- 发现新版后按钮显示“下载 vX.Y.Z”，点击一次下载并校验 GitHub SHA-256。
- 下载的新版 EXE 以 `--apply-update` 模式启动，等待旧 PID 退出后：
  - 旧 EXE 改名为 `.old-YYYYMMDD-HHMMSS`；
  - 新版覆盖原 EXE 路径并再次校验；
  - 启动原路径的新版，更新进程退出。
- 覆盖或启动失败会恢复旧文件并写
  `%LOCALAPPDATA%\caxa_upload_journal\update_error.log`。
- 当前验证：199 内置（2 个可选 PDF 依赖跳过）、19 独立、9 边界、27 Tk、
  2 工艺 Tk；`compileall` 通过。
- 上传端已重建，真实更新模式冒烟通过：覆盖后哈希一致、`.old-时间戳` 备份
  存在、新版成功重启且测试进程已清理。
- 当前上传端：
  `848e13f2bcf95206b09b95a3f4ace72c6cb41e19313a8b41df616788b2ddfbfb`
- GitHub Release `v1.1.0` 已发布：
  `https://github.com/rngt713-art/caxa-drawing-tools/releases/tag/v1.1.0`
- Release 远端提交为 `da10fed7e47b042d9acf90dd6530dc754769d8ce`，内容树与
  本地发布提交一致。
- Release 只包含：
  - `CAXA-Drawing-Uploader-v1.1.0.exe`
  - `README-v1.1.0.md`

## 2026-11-20 v1.0.0 版本化与 GitHub 分发

- 正式版本改为 `vX.Y.Z`，当前 `v1.0.0`。
- `VERSION`、`app/version.py`、UI 标题、构建文件名和 Git tag 使用同一版本号。
- GitHub Release 只提供版本化上传端 EXE 和 README。
- 源码只通过公开 GitHub 仓库 clone。
- 当前树已移除真实账号姓名；本地 IP 按用户确认保留。
- 旧日期式 ZIP 和旧固定文件名 EXE 已移入回收站。
- 当前内置回归：188 个通过，2 个可选 PDF 依赖测试跳过。
- 版本化 EXE：
  `dc47c3a604df3f05e52738b32bfa9a3d2f2d371800aff6c145a6b703a6b8ecef`

## 2026-11-19 上传端评审整改（识图端未改）

- 批次摘要改为只由当前勾选执行行组成；未勾选行修改不会创建新任务或重传。
- helper stderr 持续排空并保留最近 300 行；异常 detail 附带尾部。
- `unlink_part_relation` 已加入非幂等写命令集合。
- 待核实 CAXA 回读移至后台线程，Tk 主线程不再等待登录/查询。
- 事件轮询在 `finally` 重新注册，单次异常后继续运行；UI 日志限制 5,000 行。
- `config.json` 改为原子替换；SQLite 增加 5 秒 busy timeout。
- C# Part/Product/目录/物料/关系写入补统一 `WriteError` 证据；同账号提示
  自动点击限制在当前 login 作用域和 helper PID。
- 手动借用补成功、缺父、写后不确定和连续两条第二条失败回归。
- 验证：187 内置（2 个可选 PDF 依赖跳过）、19 独立、9 边界、26 Tk、
  2 工艺 Tk；helper x86、上传端重建和 8 秒冒烟通过。
- 当前产物：
  - uploader `92e95fc0d718e04ee35cad2ea4ef9197a196927b89a4538be08e2deaec11cddc`
  - helper `d584788a04d857bd5cfaa3cb656af06058138fd2fe659d3922a6ba764dfb81ec`
- 识图端与旧一体化上传入口按用户要求本轮未改。

## 2026-11-18 当前补充：文件名规范化、02 叶子与批量核实

- “一键规范文件名”已修复：
  - `Z03... 中文名.pdf` 这类可修复工艺文档名会裁成规范目标名；
  - 导入、原地改名和一键规范化都会重新登记 SHA-256/大小；
  - 图号变化同步迁移 relations、manual_borrows 和旧 parent 字段。
- 02 半成品规则已按现场截图确认：
  - 从最近点号 `02.A36.02` 向上寻找既有 Part；
  - 只创建缺失末级并挂到最近上级；
  - 同一图号一个原件，多个挂接中只允许一个非借用归属。
- 待核实窗口已合并为单张列表：
  - 可多选自动回读成功；
  - 可确认未写入并回到主界面重试；
  - 可全部确认未写入并直接重新上传。
- 真实样例 `Z03.HG.010250A-11P03 空气压缩机.pdf` 已通过：
  - 规范目标 `Z03.HG.010250A-11P03.pdf`
  - SHA-256 `a3568ca076e8ce8f8c033fd7996309252e7a9a121bbacf77e85e593030ccf657`
  - 大小 `734274`
  - 本地预检 `ready=true`
- 验证：173 内置（2 个可选 PDF 依赖跳过）、19 独立、9 边界、25 Tk、
  2 工艺 Tk；compileall、上传端重建和 8 秒冒烟通过。
- 当前产物：
  - uploader `3d28850f1db2c06f1454a17397d309ab30edd1c879fe141308bc98104a176da4`
  - helper `07abf7f495ca6235d4cf958a6ed8ba76c3386becc640bf235c3263b27400c8e8`

### 2026-11-19 02 重复产品挂接现场修复

- `journal(9)` 任务 `T20261118162825-3dc17c` 只有一次上传成功记录
  Drawing `36023`；没有两次创建 Part 的日志证据。
- CAXA 中两个 `[02.A36.02.AR02]氩气过滤干燥橇` 实际是同一 Part `44594`
  的两条 Product `02` 产品挂接关系，不是两个 Part。
- 已用审核账号账号完成关系修复：
  - Part `44594` 保留到 `21117 / 02.A36.02` 的归属关系 `89441`；
  - Product `02` 下重复产品挂接已清为 0；
  - Drawing `36023`、CappDocument `15391/15392/15393` 均保留。
- 旧版 `02.A36.02.ARF25C` 也已清理：
  - Part `43911` 保留正确父级关系 `87493 / 21117 / 02.A36.02`；
  - 错误 Product `02` 直属关系已解除；
  - Drawing `35831`、CappDocument `15230/15231/15357` 保留。
- 新版本已改为最近点号上级寻址，只允许一个非借用归属，并新增只删除关系
  对象的 `unlink_part_relation`。
- 当前产物：
  - uploader `2694f42005d524d9a6142722574978454bed305b8c04ff3afabb3195fa73e68d`
  - helper `af4ec2ddfcf720fa8c9763b50dc446bed7e285a5f6d3c32add2ac7b0d6dbf182`

## 2026-11-18 前序状态（历史）：升版失败不得返回成功

- 已在同事机器 `DESKTOP-RGC9IBT / 192.168.1.39` 真机复现
  `T20261118130949-79b768`：helper 主线程默认 MTA，高层 `AttachFile` 在
  导入框出现前直接返回空，两张图均未新增版次。
- 两台机器上的 32 位 CAXA COM 类均注册为
  `ThreadingModel=Apartment`；MTA 在多数机器上可用，但这台机器稳定走
  STA 依赖路径，属于确定性的环境差异，并非随机偶发。
- 失败机器已有有效设备注册标记和 COM 激活验证，不是免注册部署；
  `regsvr32` 注册功能没有失效。
- `E_NOINTERFACE` 已排除为根因；正常机器和失败机器上的低层回退表现一致。
- helper 已修复：所有 attach 路径为空且版次/历史未增长时返回
  `attached=false`、`partial=true`、`uncertain=true`，不再返回
  `attached=true`。
- 恢复器同时要求升版前历史数或版次严格增长，缺基线或未增长保持待核实。
- 当前验证：164 内置、19 独立、9 边界、24 Tk、2 工艺 Tk；compileall、
  helper x86 编译、上传端 one-file 重建与启动冒烟通过。
- 同一失败机器加 `[STAThread]` 后双图对照成功：
  `31039 c.2→c.3`、`31035 d.2→d.3`，确认 STA/MTA 是根因。
- 当时产物（已被上方最新构建取代）：
  - `release/图纸上传助手.exe`
    `27fdc4f3fe8622f24af25357d5282c5fcc9eb57ec27076fbd9e4fb3901c04c56`
  - `release/caxa_helper.exe`
    `07abf7f495ca6235d4cf958a6ed8ba76c3386becc640bf235c3263b27400c8e8`
- 新 STA helper 已在失败机器临时目录完成双图实测；尚未替换同事机器的正式
  部署目录。

## 最终结果

| 套件 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 78 tests OK |
| `../independent_acceptance.py`（19） | OK |
| `../boundary_acceptance.py`（9） | OK |
| `../ui_acceptance.py`（真实 Tk，18） | OK |

外部脚本未修改，经 `tests/run_external_acceptance.py` 只读加载运行：仅把其
`tempfile.TemporaryDirectory` 重定向到 `tests/_tmp`（本机受限令牌无法在工作区外
创建 0o700 目录），并在本机缺包时提供 `tests/_stub_deps/tkinterdnd2.py`；真实包
可导入时优先使用真实包。

（另：`python -m compileall -q app` 通过；helper 已在具备 CAXA 2011r3 引用 DLL
的机器上以 x86 csc 编译通过。构建产物已嵌入上传端 one-file EXE。）

## 交付内容

- 关系：`app/relations.py` 唯一来源、一次性迁移、稳定重试、人工优先、
  单 `own` 父级/多 `borrow` 父级和图号前缀自动层级。
- 上传：普通 Part 先在 Product `01 通用件` 下建立归属；03 整机在各系列 Product 下建立，
  产品缺失可按手动填写的系列名称和整机图号创建。
- 内容绑定：`app/staging.py` 独占暂存、先验证后原子提交、大小写冲突保护。
- 恢复/防重：`app/journal.py`（RUNNING=待核实、内容级独占锁、关系类型确认、
  remote_id/class_id/iterator 持久化）+ `app/upload_recover.py`（list/confirm/
  no-write/release-lock）+ UI“核实待定结果”。
- 编排：`app/upload_core.py` 内容任务身份与阶段分离；分类先于图校验；仅执行未确认
  操作；流程要求 started/正 ID/链接证据；关系失败或缺父缺子或未执行都阻止流程。
- CAXA 层：`app/caxa/bridge.py` 结构化错误、每进程重新登录、非幂等写不重试；
  `app/caxa/uploader.py` 实际 ClassID 校验、版次回读、显示查询失败不影响成功、
  部分完成带对象 ID。
- helper：`app/caxa/caxa_helper/caxa_helper.cs` 登录判错、流程 ID/阶段结构化、
  append 空引用修复（用 existingDuplicate.ClassID）、上传外层 try/finally、
  历史旧版自动确认曾严格限定本 helper 进程且仅 upload 期间；该 PID 限制已
  被 2026-11-12 的精确对话框指纹方案取代。
- UI：`app/upload_main.py` 忙时全量冻结（含账号控件）、冻结快照、结果回 UI 线程、
  编辑原子化并迁移关系边、删除父级组、换账号/服务器不合并旧成功。
- 构建/分发：`build_all.ps1`（上传端+识别端 PyInstaller、helper 可选、`-PlanOnly`
  已验证）、`app/caxa/caxa_helper/build_helper.ps1`（缺 DLL 明确失败、覆盖需
  `-Force` 且备份、输出 SHA-256）、`pack_source.ps1`（默认排除旧 exe/pycache/
  config/dist/测试临时目录，已验证 0 个 exe、0 个 pycache）。
- 文档：`IMPLEMENTATION.md`、`app/README_上传助手.md`、`STATUS.json`、本文件。

## 现场待验（明确限制）

1. 工作设备编译 helper（CAXA 2011 DLL）并核对 exe/源码 SHA-256。
2. 真实 ClassID、追加版次回读、part_info children/link_endpoints 字段、自动确认
   窗口归属需现场小批验收。
3. `user_id` 身份绑定与高层→低层回退冗余按用户要求**未改**；受限账号鉴权需现场验证。
4. 跨机器 exactly-once 不承诺；同批建议一人提交。
5. ZJ03.* 规则保持拒绝，待业务确认。
6. 本机残留两个早期实验目录（tests/probe_*、tests/tmp*，0o700 ACL 无法删除）；
   `pack_source.ps1` 已排除，不影响交付。

## 常用命令

```
python -m unittest discover -s tests -v
python tests\run_external_acceptance.py ..\independent_acceptance.py
python tests\run_external_acceptance.py ..\boundary_acceptance.py
python tests\run_external_acceptance.py ..\ui_acceptance.py
python app\upload_recover.py list
powershell -ExecutionPolicy Bypass -File .\build_all.ps1 -All -PlanOnly
powershell -ExecutionPolicy Bypass -File .\pack_source.ps1
```

## 2026-11-06 产品目录与 01.x 归属修订

- 同一 `01.x` 系列的跨图号父子默认 `reference=False` 归属；不同 `x` 才借用。
- 03 整机在树中按系列产品目录分组，多台 03 可位于同一目录下。
- 新建 Product 会挂入已存在的系列目录并回读：
  - `03.HW.* → HW系列`
  - `03.HT.HGB.* → HT合同特殊/HGB系列`
  - 其他 `03.HT.* → HT合同特殊`
- 同一任务缓存新建 Product，后续工艺文档和借用关系不再因产品树扫描延迟报
  “整机不存在”。
- 最新验证：71 内置、19 独立、9 边界、14 Tk 界面测试通过；C# helper 与
  PyInstaller 上传端均已重建。

## 2026-11-06 上传计划与物料编码修订

- 系列行和产品行右键编辑只修改本地上传计划，不再登录 CAXA 或改写已有对象。
- 上传端不允许创建 ProductFolder。目标系列目录必须已存在；缺失时预检提示，
  上传在任何图纸/Part/Product 写入前阻止。Product 缺失时只能挂入已有目录。
- 新建 Product 时使用编辑后的计划名称；已有 Product 直接复用，不改其名称。
- 新建或复用 Part 时把 `MaterialNo` 写成图号，对应 CAXA 界面“物料编码”；
  已有值不一致会更新，写入后回读确认。
- 已用 Part `44342` 真机验证：物料编码由空值写为
  `03.HW.015350-46P01`，第二次执行返回 `changed=false`。
- 已用 `HT合同特殊/HGB系列` 真机验证嵌套目录查找，返回 folder `84`。
- 验证：73 内置、19 独立、9 边界、18 Tk 界面测试通过；上传端启动冒烟通过。

## 2026-11-06 整机物料编码 Z 前缀修正

- 原因：上传 `Z03.*` 工艺文档时，程序把工艺文档图号写到了所挂载整机 Part
  的物料编码，导致整机出现 `Z03.*`，而整机代号仍是 `03.*`。
- 修正：工艺文档分支改为使用 `machine`（`03.*` 整机图号）写入整机 Part 的
  `MaterialNo`；普通图纸和整机图纸路径不变。
- 真机修复：Part `44365` 物料编码由 `Z03.HG.020100A-11P02` 回写为
  `03.HG.020100A-11P02`，回读一致。
- 验证：74 个内置回归通过；新增“工艺文档不得把 Z 前缀写入整机物料编码”用例。

## 2026-11-09 共享借用关系与性能修正

- 两台整机共用同一压缩机组时，借用关系改为低层直接插入独立 Part2Part
  关系，避免高层 `LinkSubPart` 复用/移动先前的借用边。
- 上传完成后对整批全部计划关系做最终回读；缺失边自动补建，补建后仍缺失则
  阻止发起流程。
- 零件和整机零件查询增加任务内缓存；自动关系分类不再逐条查询设计树。
- Product 查找改为按 `IdentityNo` 精确查询优先，避免递归扫描整个产品树。
- 只有当本次确实要发起流程时才查询审核人 Person ID。
- 性能实测：Product 查找约 55 秒降至约 0.03 秒；本批两台整机、6 条关系的
  真实预检总耗时约 2.2 秒。
- 真机回读：两条“传动组件 ← 压缩机组”和四条整机借用均存在，6/6 通过。
- 验证：75 个内置回归、19 个独立验收、9 个边界验收、18 个 Tk 界面测试通过。

## 2026-11-10 Z02 半成品工艺文档支持

- 文件名规则放宽为接受单字母 `02` / `03` 工艺文档，因此 `Z02.*` 可正常导入。
- `Z02.*` 挂到 Product `02 半成品` 下对应的 `02.*` Part；不会进入 03 整机流程。
- 真实样例 `02.A36.02.ARF25C / 氩气过滤撬` 已上传 Drawing 与
  `Z02.A36.02.ARF25C.pdf` CappDocument。
- 验证：77 个内置回归通过；新增 Z02 命名与共享借用最终回读回归。

## 2026-11-11 仅工艺文档建整机层级

- 已修复审核打印流程漏关联 CappDocument：流程集合同时处理 `1002` 和 `1003`。
- 历史阶段：helper 登录曾增加 PlmComApi COM 检测和 UAC 提权注册；当前已
  改为设备级一次性注册，登录阶段不再包含 COM 检查。
- 已用审核账号账号验证 Part `42843` 下四张文档及 helper dry-run。
- 已实现仅工艺文档批次：统一从 `machine_drawing.code` 和 02/03
  `process_doc.machine` 收集目标；同一 03 整机去重并同步产品名称。
- 03 Product/Part 缺失时，可在既有系列目录下创建
  `Product → 整机 Part` 后挂 CappDocument；Product 已存在时只补 Part；
  Product 缺失但没有产品名称时阻止上传。
- 02 工艺文档继续挂 Product `02 半成品` 下既有 `02.*` Part；缺少 Product 或
  Part 时预检和写入前检查都会阻止，不套用 03 系列产品规则。
- UI 在没有 03 整机图时仍显示系列产品节点，J/M/P/T/Z03 文档挂在产品节点下；
  产品名称可从产品节点或任一相关工艺文档行编辑。
- 验证：该阶段为 83 个内置回归、19 个独立验收、9 个边界验收、20 个真实
  Tk 界面测试；当前设备注册版已增至 87 个内置回归。
- 当前发布文件：
  - `release/图纸上传助手.exe`
    SHA256 `48475efec7b0ab5817b7bb288163e5ab84991ac6092a32f4376604a8626d3b36`
  - `release/caxa_helper.exe`
    SHA256 `14cd7b18a9a3e1042417bb41ebc9d20723a55d25e84c0fd19f447f4a414e0655`
  - 最终 ZIP 名见设备注册版交接章节。

## 2026-11-11 设备首次注册与持久化标识

- 不再每次登录探测 PlmComApi；每台设备首次上传先强制做一次 32 位 COM 注册。
- 成功后在 `journal.sqlite3::device_state` 保存设备哈希标识和
  `registration_id`，上传结果返回同一记录。
- 后续上传直接执行 CAXA 登录，不再包含 COM 检查；注册失败或 UAC 取消
  不写标记。
- 复制数据库到其他设备不会误用标记；可通过 `python upload_recover.py device`
  查看，设置 `CAXA_FORCE_PLM_REGISTRATION=1` 可强制重注册。
- 当前构建：88 内置、19 独立、9 边界、20 Tk 全部通过。
- 当前发布文件：
  - `release/图纸上传助手.exe`
    SHA256 `48475efec7b0ab5817b7bb288163e5ab84991ac6092a32f4376604a8626d3b36`
  - `release/caxa_helper.exe`
    SHA256 `14cd7b18a9a3e1042417bb41ebc9d20723a55d25e84c0fd19f447f4a414e0655`

## 2026-11-12 自动升版修复

- 重复对象框按标题、正文、当前目标文件名和 upload 作用域四重匹配。
- 重复框必须属于 helper PID，不跨进程点击。
- 使用 UI Automation 操作实际 RadioButton、CheckBox 和 Button，操作后回读。
- 升版确认先`Retrieve()`刷新；仍读到旧版次则等待3秒后再次刷新回读，不重启
  helper 会话。
- 版本历史确认 16:34:49 已生成 `b.2`；工具在 16:34:50 回读旧会话仍得到
  `b.1`，因此误判。16:35:57 另一次人工上传生成 `b.3`。不能依据旧流程关系
  阻止反审后的合法追加。
- 最终分发包：`outputs/CAXA图纸工具_20261113_自动升版回读修复版.zip`。

## 2026-11-13 上传计划 UI 收尾

- 状态：已完成。
- “核实待定结果”改为业务文案：
  - 图纸上传显示实际图号；
  - 次级/借用关系显示父级与子级；
  - 审核打印流程优先显示流程实例 ID、流程状态和关联文档数；
  - 不再直接显示 `upload`、`relation`、`workflow` 或内部 `phase`。
- 完整编辑对话框的系列产品名称/目录增加灰色 placeholder，示例分别为
  `HW1.5-35(直连/水冷)`、`HW系列`；placeholder 不进入真实字段或 manifest。
- 右键菜单新增“从列表中删除所选图纸”，支持保留当前多选。删除仅作用于本地
  上传计划，并同步清理 items、relations、manual_borrows、bundle 和产品名称
  必填/选择引用；本地 PDF 与 CAXA 远端对象不变。
- 当前发布文件：
  - `release/图纸上传助手.exe`
    SHA256 `db287975dff6cb24a38fc6903968bf0ea85aade05ebcfa0737b70674b2207b93`
  - `release/caxa_helper.exe`
    SHA256 `14cd7b18a9a3e1042417bb41ebc9d20723a55d25e84c0fd19f447f4a414e0655`
- 验证：93 内置、19 独立、9 边界、21 Tk、2 工艺专用 Tk 全部通过；
  `compileall`、上传端 one-file 重建和 8 秒启动冒烟通过。
- 最终分发包：`outputs/CAXA图纸工具_20261113_上传计划UI版.zip`；ZIP 自身
  SHA-256 记录在包外 `outputs/SHA256SUMS.txt`。

## 2026-11-16 PR 后续：可操作恢复与单独发起流程

- 用户确认：待核实记录已有图号/目标文件名，内部 Object ID、ClassID
  `1002/1003` 和流程关联文档 ID 不应要求现场填写。
- 新增 `app/recovery_verifier.py`，由程序按业务对象自动回读 CAXA 证据。
- helper 的 `part_drawings` 返回 ClassID/ClassName；新增只读
  `workflow_info`，自动读取流程状态和关联文档。
- 上传功能区新增“发起流程”。`workflow_only=True` 只对已确认上传和已确认
  关系发起流程，不重传 PDF；本地文件被移动也可补发。
- 现场任务 `T20261116164347-2b13b7` 证实 CAXA `part_drawings.file_name`
  为空，当前文件名在 `name`；自动恢复已兼容，`35877 c.2`、`35892 b.2`
  回读确认成功，日志已解除待核实。
- 验证：149 内置、19 独立、9 边界、22 Tk、2 工艺专用 Tk，共 201 项通过；
  `compileall` 通过。
- 当前分支为 `codex/fix-recovery-and-performance-20261113`，更新 PR #1。
  helper x86 和上传端 EXE 已重建，上传端启动冒烟通过；仍需现场小批验收。
- 发布哈希：
  - 上传端 `94f85229281bb5afd88280185ad20df8c2a1dab53a9592a80afdeb3b7d62f97f`
  - helper `6725155c7cf36522a52927f4ec246e207f6f6b6259b5115084631f0db28eb008`

## 2026-11-17 已有原件只升版规则

- CAXA 中同一图号的原件 Part 只能有一个；其他上级只能借用。
- 预检发现已有原件后，归属关系强制改为借用，并写入 `existing_original`。
- 树增加“只升版”列：
  - 无计划关系默认只升版不借用；
  - 有计划关系默认升版后借用；
  - 用户切换状态在预检/重试间保持；
  - 只升版不执行任何关系边。
- “增加次级条目”窗口已居中。
- 2026-11-04 原图 `35759 a.1` 已由 helper 日志和 CAXA 历史确认是 helper 上传；
  无 `.pdf` 是旧版本保存行为，当前版本保留 `.pdf`。
- 历史 Drawing 仅缺 `.pdf` 且其余名称一致时，上传前自动通过属性编辑补后缀，
  回读确认后再升版；补名失败则阻止。
- 验证：160 内置、19 独立、9 边界、24 Tk、2 工艺专用 Tk，共 214 项通过。

## 2026-11-17 PlmComApi 依赖 PATH 与空密码

- `PlmComApi.dll` 本身和 32 位 CLSID 均正常，但目标设备 PATH 缺少：
  - `CAXA EAP\1.0\bin`
  - `CAXA EAP\1.0\template\script`
- 无 PATH 时 32 位加载返回 `126`；只加 `bin` 仍失败；两个目录都加入后
  `LoadLibraryW` 和 COM 实例化成功。
- helper 新增 `EnsureCaxaDependencyPath()`，在注册与登录前前置 EAP 目录
  及常见 `CAXABOM\bin`、`CAXAEDM\bin`。
- 上传端允许 CAXA 密码为空；用户名、IP 和端口仍必填。
- 新 helper 在目标机现场返回 `registered=true`；新上传端已替换并完成启动
  冒烟，旧文件备份在目标部署目录 `_backup_20261117_145246`。
- 第二台设备 `192.168.1.85` 同样因 PATH 缺项报 126，修正后注册成功。
  helper 新增已有 COM 验证，可在设备标记缺失但注册已成功时跳过 UAC；
  现场实测 `registration_attempted=false`。新版已替换到
  `D:\CAXA图纸工具_20261117_原件唯一_自动补PDF_依赖修复版`，备份为
  `_backup_20261117_153351`。
- `journal.sqlite3` 已确认该机任务 `T20261117151828-6eebfc` 在设备注册阶段
  以 `regsvr32 exit code 5` 结束，`device_state` 无记录。最终 helper 将提升后
  的注册命令写入临时 `.cmd`，在脚本内部显式设置 EAP PATH 和真实退出码回传。
- 系列产品 `OFT312` 已存在时重复新建的 bug 已修复。Product `1693` 位于
  `OF系列` 并挂多台 `03.OF.*` 整机；旧对象 `1467` 位于 `OF`。新逻辑按
  “整机图号 → 系列名称精确匹配 → 目标目录对象 ID”解析，真机唯一命中 `1693`。
- `01.75.V-001` 的 `l.2` 于 `11:32:44` 已写入 CAXA，程序 `11:32:48`
  仍回读到 `l.1`。SDK 提供 `Refresh()`，helper 已改用实体 Refresh 优先、
  Retrieve 回退，解决旧 EAP 快照未刷新的误判。

## 2026-11-13 代码评审后修复与性能优化（当前源码）

- 用户确认：“追加新的版次”是重复框默认项；图号唯一，同批不会同时处理
  不同来源的同名/同图号图纸。本轮保留弹框逻辑与拆页/合并命名，详见业务基线 §13。
- 恢复入口按操作类型验证 ID、类型、版次、关系类型或流程状态/文档集合。
  取消任意输入保持原记录；空 ID 等沿用已有证据；显式空流程集合/状态不能复用
  旧值冒充本次核实。恢复备注保留原结构化 detail。
- 历史上传/流程的成功记录若缺少关键证据，会转为待核实，不能静默漏图或重建流程。
  历史日志丢失原流程计划文档集合时，须针对真实流程重建证据，不能仅因日志
  缺失就确认“没有写入”。正常历史成功记录的类名推导和流程复用仍兼容。
- 最终关系补齐记录写前 RUNNING、写后结果，修复后的最终行、日志、计数一致；
  不完整回读与写后读取失败保持待核实。未知写结果不自动补写。
- C# 每条上传开始清空本条对象证据，避免异常携带上一张图纸 ID。
- 工艺文档分类/命名共用上传端规则，支持 02/03；OCR 名称以 display_name 保留，
  工艺文档 name 和文件名按原业务规范导出。总装图路径解析修正。
- 性能：识别核心拆分每来源 2 次降为 1 次；GUI 共用的视觉服务健康探测缓存
  5 秒，失败/换端点失效，不重试 POST；关系验证每次调用内缓存节点查询，
  最终父关系每轮仍新鲜读取，结果对齐改为索引。
- 验证：142 单元 + 19 独立 + 9 边界 + 21 Tk + 2 工艺专用 Tk，193 项全通过，
  compileall 通过；包含实际 C# 上传边界编译和真实 PDF 回归。
- 本机无 CAXA 引用 DLL，**未重建完整 helper 或发布 EXE**；release 中仍是历史
  构建，不含本轮修复。下一步在工作机从当前源码构建并小批验收。
- 本轮细节和性能数据见 `FIXES_AND_PERFORMANCE_20261113.md`。

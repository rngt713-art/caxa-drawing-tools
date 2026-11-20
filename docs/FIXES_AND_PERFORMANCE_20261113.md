# 2026-11-13 修复与性能说明

本轮基于 `b807689b96ce055da93d374cf5e2dc1ce9b91267` 修复源码。默认追加版次行为、图号唯一规则、拆分/合并文件命名及 CAXA 兼容回退保持原样。

## 修复内容

- 人工恢复按上传、关系、流程分别验证证据。上传要求正对象 ID、匹配的文档类型和实际版次；关系要求计划类型一致；流程要求正实例 ID、运行状态 2 与完整匹配的实际文档集合。
- 取消任意恢复输入时原记录不变；空 ID/类型/版次保留已有值，备注不破坏结构化证据。显式空流程文档集合或无效状态不能被旧值覆盖。UI 与命令行共用验证。
- 旧成功上传若缺有效对象/类型证据，或旧成功流程缺有效实例 ID、明确启动证据或文档集合，会转为待核实。不会静默跳过图纸后发流程，也不会因旧日志不完整而重复发流程。
- 最终关系补齐增加写前日志；中断留下待核实标记。补齐成功并最终回读通过后，同步修正结果、日志和失败计数。写后读取失败与不完整读取保持未知，不能据此重放。
- helper 每条上传开始清空当前对象证据，异常不会继承上一张图纸的对象 ID。
- 识图与上传端共用图号分类/命名，支持 02/03 工艺文档。导出时工艺中文名留在 `display_name`，规范 `name` 与文件名，导出前统一校验。
- 总装图解析先取文件名、去扩展名，再规范整机编号。

## 性能改动与测量

| 改动 | 证据 | 收益范围 |
| --- | --- | --- |
| 识别核心复用第一次拆页结果 | 真实 2页+4页 PDF，拆分调用 4→2 | 减少重复解析/写盘，GUI 原来已只拆一次 |
| 视觉健康结果缓存 5 秒 | 连续4次问答，健康GET 4→1 | GUI 与识别核心共用；成功响应后续期，到期/换端点/失败失效，POST不重试 |
| 关系回读内缓存节点查询、结果按键索引 | 重复缺失节点每轮只查一次；同父关系每轮读取一次，下一轮仍重查 | 减少重复查找和结果对齐循环；最终关系证据不缓存跨轮 |

真实 PDF 各版本运行 5 轮：拆分时间中位 **7.218→3.974 ms**；整条路径 **113.806→109.983 ms**。该样本仅模拟模型响应，不代表真实模型推理、网络或 CAXA 业务的整体加速比例。可靠收益是调用与 I/O 次数减少。

## 验证

全部 **193 项**通过：142 单元、19 独立验收、9 边界验收、21 Tk 界面、2 仅工艺文档 Tk；`compileall` 通过。

包含真实 PDF 解析/拆分/渲染/合并、上传包往返、实际 UI 恢复入口取消、关系写入中断恢复，以及从生产源码提取真实 `CmdUpload` 方法的 C# 编译/故障注入。没有启动真实 helper 或连接 CAXA/视觉服务。

```powershell
python -m unittest discover -s tests -v
python tests\run_external_acceptance.py tests\acceptance\independent_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\boundary_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\ui_acceptance.py
python tests\run_external_acceptance.py tests\acceptance\process_only_ui_acceptance.py
python -m compileall -q app tests
```

真实 PDF 回归需要识图依赖，C# 边界测试需要 Windows .NET C# 编译器；缺少时对应测试会跳过。本次验证这些依赖齐全，没有跳过。

## 构建与使用

**2026-11-17 已在本机完成 helper x86 编译和上传端 one-file 重建。**
本机具备 CAXA 2011r3 引用 DLL；发布文件：

```text
SHA256  release/图纸上传助手.exe
94f85229281bb5afd88280185ad20df8c2a1dab53a9592a80afdeb3b7d62f97f

SHA256  release/caxa_helper.exe
6725155c7cf36522a52927f4ec246e207f6f6b6259b5115084631f0db28eb008

SHA256  app/caxa/caxa_helper/caxa_helper.cs
ddda2b038da50029d1047e8c8f6b521d1bd23796a7e9ba193cdc32bb90bcc3cc
```

在装有 CAXA 2011r3、Python、识图依赖与 PyInstaller 的工作机，从当前源码执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_all.ps1 -All
# 已有目标或helper、需要覆盖重建时：
powershell -ExecutionPolicy Bypass -File .\build_all.ps1 -All -Force
```

使用新构建的 EXE，先小批验收恢复、追加版次和审核打印。无需删除原操作日志。历史误确认流程若丢失原计划文档集合，须针对真实流程重建证据；不能仅因日志缺失认定远端没有写入。

## 2026-11-16 可操作性补充

- 人工恢复不再要求现场填写 Object ID、`1002/1003` 和流程关联文档 ID。
  这些证据均由新增只读回读取得：
  - 上传按图号和目标文件名定位宿主 Part、当前文档、类型和版次；
  - 关系按已知父级/子级和计划类型回读；
  - 流程按日志或可见实例 ID 回读运行状态和文档集合。
- `part_drawings` 现在返回文档 ClassID；新增 `workflow_info` 只读命令。
- 实测 CAXA `part_drawings` 的当前文件名位于 `name` 字段，`file_name` 可能为空。
  恢复匹配同时读取两者，修复“CAXA 已升到 c.2/b.2、界面仍无法确认”的误判。
- 功能区新增“发起流程”按钮。它只使用本机已经确认成功的上传与关系记录，
  本地 PDF 已移动或删除也可发起；未确认项会直接阻止，不会偷偷重传。
- 回归增至 149 单元、19 独立、9 边界、22 Tk、2 工艺专用 Tk，共 201 项。

## 2026-11-17 已有原件与只升版

- `.pdf` 后缀为当前正常行为；2026-11-04 的原图无后缀属于旧版本保存行为。
- 预检按组件图号查询 CAXA Part；已有原件即使计划为归属，也强制变成借用。
- 树中已有原件显示“只升版”复选框：
  - 无计划关系：默认 `☑ 不借用`；
  - 有计划关系：默认 `☐ 升版并借用`；
  - 只升版会抑制该图号全部关系写入；
  - 升版并借用但没有父级时上传前阻止。
- “增加次级条目”窗口已居中。
- 回归：160 单元、19 独立、9 边界、24 Tk、2 工艺专用 Tk，共 214 项。

## 2026-11-17 PlmComApi 依赖目录与空密码

- 现场设备 `DESKTOP-IRQJ4FT` 的 `PlmComApi.dll` 存在且哈希与本机一致，
  32 位 COM CLSID 注册表项也存在，但直接加载返回 Win32 错误 `126`。
- 系统 PATH 缺少：
  - `C:\Program Files (x86)\Common Files\CAXA Shared\CAXA EAP\1.0\bin`
  - `C:\Program Files (x86)\Common Files\CAXA Shared\CAXA EAP\1.0\template\script`
- 只加入 `bin` 仍返回 126；同时加入这两个目录后，32 位
  `LoadLibraryW` 成功、错误码变为 0，COM 类
  `{F7950E30-7112-4D20-8610-2196CBDE2511}` 可实例化。
- `caxa_helper.cs` 新增 `EnsureCaxaDependencyPath()`，在设备注册和登录前
  将 EAP `bin`、`template\script`、常见 `CAXABOM\bin`、`CAXAEDM\bin`
  前置到 helper 进程 PATH。新 helper 在目标机现场命令返回
  `registered=true`、退出码 0。
- 上传端不再强制要求 CAXA 密码非空；用户名和服务器仍必填。空白密码会按
  空字符串保存并用于登录。
- 第二台现场设备 `192.168.1.85` 同样缺少 EAP PATH；手动注册成功后，
  新 helper 增加“已有注册验证”：设备标记不存在时先尝试实例化 COM，
  成功则跳过 UAC 和 `regsvr32`。目标机实测返回
  `registration_attempted=false`、`existing_registration_verified=true`。
- 目标机 journal 任务 `T20261117151828-6eebfc` 显示第一次最新版失败点是
  `regsvr32 returned exit code 5`，且 `device_state` 为空，说明尚未登录或上传。
  原因是 `ShellExecute + runas` 启动的提升进程不能保证继承 helper 临时设置的
  PATH。新实现改为生成临时 `.cmd`，在提升后的脚本内部显式设置完整 PATH，再
  调用 32 位 `regsvr32`，并从结果文件读取真实退出码。

## 2026-11-17 系列产品同名复用

- 现场 `OFT312` 已存在两个 Product：`1693` 位于 `OF系列`，下挂多台
  `03.OF.*` 整机；`1467` 位于 `OF`，是另一个旧对象。
- 旧逻辑只按整机图号查 Product，`OFT312` 又不是整机图号，因此漏掉已有
  `1693` 并新建 Product。
- 新逻辑先按整机图号复用；未命中时按系列产品名称精确匹配，并用目标
  ProductFolder 对象 ID 消歧。`03.OF.045-06016S + OFT312 + OF系列`
  实测解析到 Product `1693`。
- 同一目标目录内仍有多个同名 Product 时阻止上传并提示人工清理。

## 2026-11-18 EAP Refresh 缓存修复

- CAXA 2011 SDK 反射确认 `EAPPartEntity`、`EAPFolderEntity`、绘图实体及
  `ICaxObjectBase` 提供独立的 `Refresh()`；旧代码只调用 `Retrieve()`。
- `part_drawings(refresh=true)` 和 `drawing_history` 改为优先调用
  `Refresh()`，失败时才回退 `Retrieve()`。
- 现场 `01.75.V-001 / Drawing 20475` 实际历史已到 `l.2`（历史数 24），
  journal 最终回读仍为 `l.1`（23），证明等待不是根因，旧 EAP 快照未刷新。

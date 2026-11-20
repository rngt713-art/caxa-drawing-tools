# 验证记录（持续更新）

当前核对日期：2026-11-19。

## 已通过

- Python 语法检查：`python -m compileall -q app`
- C# helper：使用当前机器安装的 CAXA 2011r3 引用 DLL，采用 x86 `csc` 编译通过
- 内置回归：88 个测试全部通过
- 独立验收：19 个测试全部通过
- 边界验收：9 个测试全部通过
- 真实 Tk 界面测试：20 个测试全部通过（原 18 个 + 仅工艺文档新增 2 个）
- PyInstaller one-file 上传端构建成功，归档中包含
  `caxa/caxa_helper/caxa_helper.exe`
- 上传端启动冒烟：进程保持运行 5 秒后由测试脚本关闭，没有出现模块导入异常

## 关键反向用例

- 修改图号或名称后，只上传本任务暂存的用户原始 PDF 内容。
- 同一内容和账号重复提交不会再次上传或重复发起流程。
- 删除旧 BOM/借用关系后，后续归一化不会恢复该关系。
- 图号上一级前缀关系为 `reference=False` 归属；
  `01.x.*` 子件与同一 `01.x.*` 系列父级之间也是归属，`x` 不同才是借用；
  03 整机父级固定为 `reference=True` 借用。
- 每个零件最多一个归属父级，但允许借用到多个 03 整机；人工选择优先。
- 新零件先在 Product `01 通用件` 下创建归属路径，再由借用关系链接到整机。
- 一批可包含多台 03 整机；树按系列目录分组，每台 03 使用独立产品名称。
- Product 按系列目录挂载：`03.HW.* → HW系列`；
  `03.HT.HGB.* → HT合同特殊/HGB系列`，其他 `03.HT.* → HT合同特殊`。
- 上传时系列目录必须已存在；不存在时上传端阻止，不创建目录。
- 新建或复用 Part 时写入 `MaterialNo=图号`，写入后回读确认；重复执行返回
  `changed=false`。
- 真机回读：Part `44342` 的物料编码从空值改为
  `03.HW.015350-46P01`；嵌套目录 `HT合同特殊/HGB系列` 返回现有
  ProductFolder `84`。
- 修复 `Z03.*` 工艺文档把 `Z` 前缀写进整机 Part 物料编码的问题。整机 Part
  `44365` 由 `Z03.HG.020100A-11P02` 校正为
  `03.HG.020100A-11P02`，并已回读确认。
- 同一子件借用到多个上级时，借用关系改为独立插入，不再依赖高层
  `LinkSubPart` 复用关系。
- 关系写入后按整批计划再次回读全部边；缺失关系自动补建，仍缺失时阻止流程。
- 两台整机共用压缩机组、共 6 条借用关系的真实回读结果为 6/6 通过。
- 性能实测：Product 精确查询由约 55 秒降至约 0.03 秒；同一测试包真实预检
  总耗时约 2.2 秒。
- `Z02.*` 工艺文档已加入命名规则，并挂到 Product `02 半成品` 下的对应
  `02.*` Part；`ZJ03.*` 仍拒绝。
- 新建 03 Product 后，同一任务内后续工艺文档复用已创建对象，不重复扫描或误报缺失。
- 上传返回的文档 ClassID 与预期不符时，不记为成功，也不进入流程集合。
- 关系失败、缺父、缺子或结果待核实时，不允许自动发起流程。
- 流程启动后必须回读 `WFProcessInst.state=2` 和完整 Drawing 集合；回读失败只记为待核实。
- 自动确认只处理本次 upload 命令期间、helper 自己进程中的窗口，并额外匹配
  精确标题、正文和当前目标文件名；不会跨进程点击手动弹框。
- “导入文件确认对话框”在上传请求作用域内自动点“确定(O)”；按钮匹配失败时不会
  点击其他窗口，并记录完整子控件信息。
- “同一个用户已经登陆 / 0x83060607”提示框已增加严格文本匹配的自动确定；
  自测日志确认“确定点击=True”，未匹配其他错误框。
- 已用审核账号账号修复孤立整机：保留原 Product `2033`
  （`03.HW.015350-46P01 / HW1.5-35(直连/水冷)`）并挂到 `HW系列`；
  Part `44342` 保留在其下。误建 Product `2034` 已解除 Part 关系并从
  `HW系列` 剪切移出，随后由用户手动删除；产品树只显示原有正确层级。
- 层级树右键菜单新增“原地重命名 / 修改文件名…”，可直接覆盖编辑树中的
  图号和名称；完整编辑对话框居中于上传助手主窗口。
- 系列行和产品行的右键编辑只修改本地上传计划，不修改 CAXA 已有对象；
  上传时系列目录必须已存在；缺失时阻止上传。Product 可复用，或在既有系列
  目录下按整机图号新建。
- 已确认 `HT合同特殊`、`HGB系列` 均为 ProductFolder class `1013`；
  HGB 只是二级嵌套目录，无需独立对象类型。
- 重复图号下拉框显示“追加新版次 / 已有则复用”，界面不再展示内部策略名。
- 上传图纸与次级条目合并为同一棵可展开层级树。
- 多选多个子图后一次拖到父级；新的归属父级会替换旧归属，借用父级可保留多个。
- 导入时按图号直接上级前缀自动建立完整组件层级，已有显式关系优先。
- 双击行首 `+`/`-` 只展开或折叠，双击文字区域才打开修改上传信息。
- 手工“增加次级条目”固定为借用已有设计树零件；缺失子项在预检和执行结果中报错，
  不自动创建 Part。
- 03 产品名称在对应 03 整机行单独填写，产品代号使用整机图号。
- 行内拖拽显示 PDF 拖动标签，经过上级行时高亮，松开后清除高亮。

## 历史构建校验值（2026-11-06，仅用于追溯）

```text
SHA256  release/图纸上传助手.exe
f1334ef10507aa2610dc931d80044f6008eacdccc6c41eafbb4d101c454fa52c

SHA256  release/caxa_helper.exe
eef4feb72f283c7e78aa97322925063af048af8c6eb686a991db058969c1e608

SHA256  app/caxa/caxa_helper/caxa_helper.cs
3d3b67299d215c2e3c80d7da8fc09b9affaade66db59a5ebac2e3d435c0b7da4
```

历史整机物料编码修正版：

```text
SHA256  release/图纸上传助手.exe
b18cfb01e389bf5712931c9f2b9602d1f866f6564386544bc39487f22f85ef7f
```

## 历史构建校验值（2026-11-13 升版流程锁修复版，已被 UI 版取代）

```text
SHA256  release/图纸上传助手.exe
48475efec7b0ab5817b7bb288163e5ab84991ac6092a32f4376604a8626d3b36

SHA256  release/caxa_helper.exe
14cd7b18a9a3e1042417bb41ebc9d20723a55d25e84c0fd19f447f4a414e0655

SHA256  app/caxa/caxa_helper/caxa_helper.cs
3d44f920d090d0af225aa1bdda7cf31eebba7798a30d6641a1348c5b1ce1d1fa
```

## 历史构建校验值（2026-11-13 上传计划 UI 版，已被自动恢复版取代）

```text
SHA256  release/图纸上传助手.exe
db287975dff6cb24a38fc6903968bf0ea85aade05ebcfa0737b70674b2207b93

SHA256  release/caxa_helper.exe
14cd7b18a9a3e1042417bb41ebc9d20723a55d25e84c0fd19f447f4a414e0655

SHA256  app/caxa/caxa_helper/caxa_helper.cs
3d44f920d090d0af225aa1bdda7cf31eebba7798a30d6641a1348c5b1ce1d1fa
```

## 当前构建校验值（2026-11-16 自动恢复与补发流程版）

```text
SHA256  release/图纸上传助手.exe
ceb38c714552e8463bcdb48f745c6ed968993c5c191fd6a779b94998dda05537

SHA256  release/caxa_helper.exe
b7b2da674f2978e3d2eff12862cccf5039b34cb442ea1b76b845a6cdd5d744f2

SHA256  app/caxa/caxa_helper/caxa_helper.cs
865b65085a7afedee64e30581b6003249cc58ccadb4275f51f1228fa63695086
```

### 2026-11-13 上传计划 UI 需求

- 需求：待核实界面不得显示内部操作/阶段代号；完整编辑对话框增加灰色示例；
  右键菜单支持从上传列表删除一条或多条图纸，只改本地计划。
- 实现状态：已完成。
  - “核实待定结果”使用“图纸上传、次级/借用关系、审核打印流程”等业务对象；
    图号原样显示，关系显示父级与子级，流程显示实例 ID、状态和关联文档数。
  - 系列产品名称示例 `HW1.5-35(直连/水冷)`、目录示例 `HW系列` 仅在空值且
    未聚焦时显示；真实绑定值为空，不会进入 manifest。
  - “从列表中删除所选图纸”保留右键前的多选状态，删除后同步清理
    `items`、canonical `relations`、`manual_borrows`、bundle 对应字段和产品
    名称必填/选择引用。
  - 删除父级或子级时，任一端命中图号的归属/借用关系均被清除；本地 PDF、
    CAXA 远端对象不在删除范围内。
  - 系列目录、产品节点和外部 CAXA 节点没有图纸 item，不会被当作图纸删除。
- 验证证据：
  - 93 个内置回归全部通过；新增待核实业务文案、悬空关系清理、03 产品
    必填集合回归。
  - 19 个独立验收、9 个边界验收全部通过。
  - 21 个真实 Tk 界面测试全部通过；新增多选右键删除、实际列表/关系清理、
    源 PDF 保留、placeholder 不写入绑定值回归。
  - 仅工艺文档真实 Tk 验收 2 个全部通过。
  - `python -m compileall -q app` 通过。
  - 上传端 one-file 重建完成，8 秒启动冒烟通过；PyInstaller 父子进程均已
    按构建产物路径结束。
- 本项是本地 UI 与上传计划变更，不需要新增 CAXA 真机写操作；现场首次使用
  仍建议先用非生产小批确认列表删除后的预检内容和关系树符合预期。
- 最终分发包：`outputs/CAXA图纸工具_20261113_上传计划UI版.zip`；ZIP 自身
  SHA-256 记录在包外 `outputs/SHA256SUMS.txt`。

### 2026-11-16 自动恢复证据与单独发起流程（PR 后续源码）

- 需求：待核实文件的图号或目标文件名已知，不应要求人工填写 CAXA 后台
  Object ID、Drawing/CappDocument ClassID 或流程关联文档 ID；上传后忘记
  自动发流程时应可在功能区单独补发。
- 实现状态：已完成，helper 和上传端已在具备 CAXA 2011r3 引用 DLL 的本机重建。
  - 上传恢复按图号、目标文件名和业务类型自动定位宿主 Part 与当前文档，
    helper 回读 Object ID、ClassID、ClassName、版次和文件名；要求唯一匹配。
  - 关系恢复按已知父级、子级和计划类型自动回读。
  - 流程恢复使用日志或可见流程实例 ID，新增 `workflow_info` 自动读取状态
    和全部关联文档。
  - 真机日志 `T20261116164347-2b13b7` 实测 `part_drawings.file_name` 为空，
    当前文件名在 `name`；恢复匹配已兼容两字段，`35877` 回读 `c.2`、
    `35892` 回读 `b.2` 后均成功确认。
  - 自动证据不唯一、不完整、类型/版次不符时保持待核实。
  - 新增“发起流程”按钮，`workflow_only` 模式只使用本机确认记录，不重传
    已移动/删除的本地 PDF；未确认上传或关系时阻止。
- 验证证据：
  - 149 个内置回归通过；新增自动恢复、隐藏字段不提示、PDF 删除后补发流程、
    未确认批次阻止和流程按钮存在性测试。
  - 19 个独立验收、9 个边界验收、22 个真实 Tk 测试、2 个工艺专用 Tk
    测试全部通过，共 201 项。
  - `python -m compileall -q app tests` 通过。
  - helper x86 完整编译通过；上传端 one-file 重建和 8 秒启动冒烟通过。
  - 当前上传端 SHA256
    `94f85229281bb5afd88280185ad20df8c2a1dab53a9592a80afdeb3b7d62f97f`；
    helper SHA256
    `6725155c7cf36522a52927f4ec246e207f6f6b6259b5115084631f0db28eb008`；
    helper 源码 SHA256
    `ddda2b038da50029d1047e8c8f6b521d1bd23796a7e9ba193cdc32bb90bcc3cc`。
  - 仍需在真实 CAXA 客户端做小批恢复和补发流程验收。

### 2026-11-17 已有原件、只升版与对话框位置

- 现场案例：`01.21.01.SFD1-01-07`、`01.21.01.SFD1-01-08` 已作为原件存在于
  其他上级下；错误批次在 `01.21.01.SFD2-17` 下再次创建了同图号原件。
- 规则：同图号原件只能有一个；预检发现已有 Part 时，归属计划强制改为借用。
- 已有原件行提供“只升版”复选框：
  - 无任何计划关系时默认“只升版不借用”；
  - 有计划关系时默认“升版并借用”；
  - 用户切换后状态保持；
  - “升版并借用”但无父级时上传前阻止。
- 2026-11-04 原 Drawing `35759 a.1` 的历史 Creator 为历史作者，
  `caxa_autoconfirmer.log` 在 `14:53:27` 启动并在 `14:53:31` 自动确认导入，
  与 `a.1` 的 `14:53:33` 创建时间吻合，证明原图由 helper 上传。
  当时旧版本保存名称不带 `.pdf`；当前行为保留 `.pdf`，属于正常修复。
- 用户确认 CAXA Drawing 的右键“属性编辑”是合法操作，因此上传前若检测到
  “仅缺 `.pdf`、其他名称完全一致”的历史对象，程序必须自动补后缀并回读确认，
  再继续重复检测/升版；补名失败不得继续创建独立 Drawing。
- 验证：160 内置、19 独立、9 边界、24 Tk、2 工艺专用 Tk；
  新增测试覆盖已有原件默认值、own→borrow、只升版抑制关系、
  无父级阻止、重复原件检测、树单元格切换和对话框居中。

### 2026-11-17 PlmComApi PATH 与空密码现场验证

- 目标设备 `DESKTOP-IRQJ4FT` 的 `PlmComApi.dll` 文件存在，
  SHA256 与本机一致；32 位 CLSID 注册表项也存在。
- 32 位 `LoadLibraryW` 在缺少 EAP PATH 时返回 Win32 错误 `126`；
  只加入 `CAXA EAP\1.0\bin` 仍失败。
- 同时加入以下目录后，`LoadLibraryW` 成功、错误码为 `0`，COM 类
  `{F7950E30-7112-4D20-8610-2196CBDE2511}` 可实例化：
  - `C:\Program Files (x86)\Common Files\CAXA Shared\CAXA EAP\1.0\bin`
  - `C:\Program Files (x86)\Common Files\CAXA Shared\CAXA EAP\1.0\template\script`
- helper 新增进程级 PATH 补齐后，新 helper 在目标机现场执行注册返回
  `registered=true`、退出码 `0`。
- 空密码校验已移除；用户名仍必填。空密码保存时会显式覆盖旧密码。
- 验证：160 内置、19 独立、9 边界、24 Tk、2 工艺专用 Tk，共 214 项；
  compileall、helper x86 编译、上传端 one-file 重建和本地/远程启动冒烟通过。
- 第二台现场设备 `192.168.1.85` 的 CLSID 初始不存在；在修正 PATH 后
  32 位注册成功。新 helper 再次运行时先验证已有 COM，返回
  `registered=true`、`registration_attempted=false`，无需再次 UAC。
- 该设备 journal `T20261117151828-6eebfc` 的原始失败备注为
  `CAXA PlmComApi.dll 注册失败：regsvr32 returned exit code 5`；
  `device_state` 为空，`operations` 为空，证明失败发生在首次设备注册，
  没有进入登录或上传。最终 helper 改为在提升后的临时脚本内部显式设置
  EAP PATH，避免 UAC 子进程丢失父进程 PATH。
- 系列产品复用真机回读：`find_product("OFT312")` 返回 Product `1467`
  （目录 `OF`）和 `1693`（目录 `OF系列`）。新解析器以
  `03.OF.045-06016S + OFT312 + OF系列` 唯一选择 `1693`，不再新建 Product。
- `01.75.V-001` 真机回读确认 Drawing `20475` 已是 `l.2`、历史数 24；
  journal 最终回读仍为 `l.1`、23。SDK 存在 `Refresh()`，helper 已改为在
  `part_drawings(refresh=true)` 和 `drawing_history` 中优先刷新。

### 2026-11-12 自动追加版次修复

- 现象：CAXA 弹出“对象已经存在提示框”，自动线程能识别窗口，但
  “追加新的版次”回读为未选中，随后“确定”也未点击，任务进入待核实。
- 日志证据：`选中=False`，随后连续多次`确定点击=False`。人工在 CAXA 中导入
  后，新 helper 会话读到版次增加，证明文件和升版路径本身有效。
- 原因：旧代码按文本匹配到标签控件，向其发送`BM_CLICK`不会改变实际单选框。
- 修复：使用 UI Automation 定位真正的 RadioButton、CheckBox 和 Button；
  自动确认增加当前目标文件名；升版回读先`Retrieve()`，仍陈旧时等待3秒后
  再次刷新回读，不重启 helper 会话。
- 版本历史最终确认：
  - `a.1`：15:49:33，工具首次上传；
  - `b.1`：16:32:04，DCC 反审生成；
  - `b.2`：16:34:49，重复框中的追加版次实际成功；
  - `b.3`：16:35:57，随后人工上传生成。
  程序在 16:34:50 仅晚 1 秒回读，仍在旧会话中读到 `b.1`，所以这是
  回读时序/缓存误判，不是升版失败，也不是流程锁。
- 验证：88 个内置回归、19 个独立验收、9 个边界验收、20 个真实 Tk 界面
  测试通过；helper 编译和上传端启动冒烟通过。

### 2026-11-11 设备首次注册与持久化标识

- 新设备首次上传不再先做 COM 探测，而是直接调用
  `ensure_plm_com_registered()`，由 helper 使用 32 位
  `SysWOW64\regsvr32.exe /s` 注册 `PlmComApi.dll`。
- 注册成功后生成本机 `registration_id`，与哈希后的设备标识、DLL 路径、
  注册返回一起写入 `journal.sqlite3` 的 `device_state` 表；上传结果也返回
  `device_registration`。
- 同一设备后续登录不再包含任何 COM 检查或注册代码。
- 数据库内设备标识不匹配时不会复用标记；UAC 取消或注册失败也不会写标记。
- 查询命令：`python upload_recover.py device`。
- 强制重新注册：设置 `CAXA_FORCE_PLM_REGISTRATION=1` 后再次上传。
- 验证：该阶段为 87 个内置回归、19 个独立验收、9 个边界验收、20 个真实 Tk 界面
  测试全部通过；helper 使用 CAXA 2011r3 DLL 的 x86 编译通过，上传端重新
  构建并通过启动冒烟。

## 2026-11-10 新要求：上传端不创建系列目录

- 新增要求：自动识别或人工指定的系列 ProductFolder 必须已经存在于 CAXA，
  上传端不允许创建。
- 预检会列出缺失目录并阻止上传；实际上传在写任何 Drawing、Part、Product
  前再次校验。
- Product 仍可在既有系列目录下复用或按整机图号新建；系列目录本身不创建。
- helper 的 `ensure_product_folder(create=true)` 明确拒绝创建，
  `create_product` 也拒绝空目录或缺失目录。
- 已增加“缺失 ProductFolder 不创建 Product”回归测试。
- 验证：78 个内置回归、19 个独立验收、9 个边界验收、18 个 Tk 界面测试、
  helper 编译和上传端启动冒烟全部通过。

## 2026-11-11 现场待核实批次诊断

- 同事账号：纪和津。
- 任务：`T20261111102204-c9a29f`。
- 图纸：`03.HW.025400-11T01`、`J03.HW.025400-11T01`、
  `M03.HW.025400-11T01`、`Z03.HW.025400-11T01`。
- 四条记录均停在 `upload:upload_verify`，状态“待核实”；helper 返回
  `ok=true, attached=false`，没有远端对象 ID。
- 诊断：`part.AttachFile`、`AttachFileAsChild` 和 Document 回退都返回
  `attached=false`；低层回退在创建
  `{F7950E30-7112-4D20-8610-2196CBDE2511}`（PlmComApi 的
  CxBclProductStructure）时返回 `0x8007007E 找不到指定的模块`。
- 结论：该电脑 CAXA COM/组件注册或依赖不完整。日志不能证明远端未写入，
  因此程序阻止自动重放是正确的；必须先在该电脑的 CAXA 中核对四张图，
  再通过“核实待定结果”分别标记“已确认成功”或“确认未写入”。
- 修复方向：用 32 位 `regsvr32` 重新注册
  `PlmComApi.dll`；失败则修复/重装 CAXA 2011 客户端。修复后先用单张图纸
  做小批验证。

### 后续上传结果与流程漏关联修正

- COM 注册修复后，四张文档均成功挂到 Part `42843`：
  - Drawing `35872`：`03.HW.025400-11T01 空气压缩机.pdf`
  - CappDocument `15358`：`J03.HW.025400-11T01.pdf`
  - CappDocument `15359`：`M03.HW.025400-11T01.pdf`
  - CappDocument `15360`：`Z03.HW.025400-11T01.pdf`
- 首次自动流程只关联了 Drawing `35872`。原因：Python 侧只收集
  `class_id=1002`，helper 关联时也硬编码检索 `1002`。
- 已修正：流程关联集合同时接受 `1002 Drawing` 和 `1003 CappDocument`；
  桥接层传递每张文档的实际 `class_id`；helper 按对应 class ID 检索和建立
  `WFInstanceWithDocument` 关系。
- 已用审核账号账号完成只读实探并执行 helper dry-run：总图 `35872`、
  J03 `15358`、M03 `15359`、Z03 `15360` 均按
  `1002/1003/1003/1003` 被正确识别。
- 历史旧实现：helper 登录时会检测 PlmComApi COM 类；缺失时自动通过
  `SysWOW64\regsvr32.exe /s` 请求 UAC 注册，注册后重新检测。取消提权或注册
  失败时登录会提前报错，不再等到上传后才留下“可能已写入”。
- 该问题只修改上传 helper/上传端；审核助手按要求不修改，由现场使用其自身
  agent 处理。

### 2026-11-11 已实现：仅工艺文档时自动创建整机层级

- 新要求：当批次没有 `03.*` 整机图，只有 J03/M03/P03/T03/Z03 等工艺文档时，
  预检必须根据工艺文档的 `machine` 检测目标整机。
- 目标系列 ProductFolder 仍遵守既有规则：必须预先存在，上传端不创建。
- Product 或整机 Part 缺失时，应生成
  `既有系列目录 → Product → 整机 Part`，然后将 CappDocument 挂到整机 Part。
- Product 缺失时必须为对应 03 整机填写系列产品名称；Product 已存在但 Part
  缺失时不要求重复填写。
- 实现：`upload_core.collect_machine_targets()` 同时从整机图和 J02/M02/P02/
  T02/Z02、J03/M03/P03/T03/Z03 的 `machine` 收集目标并按图号去重；同一 03
  整机的产品名称/目录复制到全部关联工艺文档，上传顺序不会改变计划。
- 预检返回值增加 `product_exists`、`part_exists`、`part_id`、
  `requires_product_name`、`has_machine_drawing`；实际上传前再次检查全部目标，
  任一系列目录或 02 Part 缺失时都在任何 Drawing/Part/Product 写入前阻止。
- `process_doc + 03.` 已允许创建 Product/整机 Part；已有 Product 时只补整机
  Part；缺少 Product 且无产品名称时阻止。`02.` 工艺文档保持只挂既有 Part。
- UI 在只有 03 工艺文档时仍显示 `系列目录 → Product → J/M/P/T/Z 文档`，
  产品节点和工艺文档行均可编辑并同步产品名称；`Z02` 仍作为半成品工艺文档显示。
- 默认目录仍采用 `03.HW.* → HW系列`、
  `03.HT.HGB.* → HT合同特殊/HGB系列`、其他 `03.HT.* → HT合同特殊`，
  ProductFolder 仍只能复用既有目录。
- 当阶段验证：83 个内置回归、19 个独立验收、9 个边界验收、20 个真实 Tk 界面
  测试全部通过。新增覆盖包括：02/03 同时识别、工艺文档去重、仅工艺文档预检、
  新建 Product+Part、已有 Product 只建 Part、无产品名称阻止，以及仅工艺文档
  UI 产品分组和名称同步。

## 仍需真机验证

- 新版“导入文件确认对话框”自动点击已编译，但修复后的写入回归尚未重跑。
- 重复对象提示框在实际客户端中的窗口归属；无法唯一识别时程序不会点击。
- 受限账号下上传、关系建立和流程发起是否完全服从 CAXA 服务端权限。
- 流程结束后各活动和工作项的完整状态流转。

## 2026-11-06 真实小批上传

- 账号：测试账号（Person 55）
- 测试目录：`C:\Users\user\Desktop\新建文件夹`
- 任务：`T20261106123504-1365e7`
- 8 张图纸全部上传成功：0 失败、0 待核实。
- 03 整机：`03.HE.050250W-21P04`，产品
  `HE300-250WS 氦气压缩机(静音)`，整机 Part `44274`。
- Z03 作为 `CappDocument` 成功挂到整机 Part。
- 6 个 04 组件均成功建立 Part、上传 Drawing，并以 `reference=True`
  借用到整机；整机节点回读共 7 个借用子件（含原有电控板）。
- 新 Drawing ID：`35771`-`35777`；Z03 CappDocument ID：`15346`。
- 新 Part ID：`44335`-`44340`。
- 回读确认每个新 04 Part 同时有一个 `reference=False` 归属父级和
  `03.HE.050250W-21P04` 的 `reference=True` 借用父级。
- 本批首次真实上传时“导入文件确认对话框”仍需人工点“确定”；日志显示
  `ClickUniqueChild(dlg, "确定")` 返回 False。已改为按 Button 类、归一化
  “确定(O)”文本、IDOK 和 `WM_COMMAND` 多级回退，并重新编译。
- 用户逐项检查确认本批图纸和层级全部正确，无需清理。

## 2026-11-13 评审边界确认、恢复修复与性能验证

本节为本轮源码验证；前文的默认选项假设、测试数量及 EXE 状态属于历史记录。

| 用户确认/要求 | 当前实现状态 | 验证证据 |
| --- | --- | --- |
| “追加新的版次”为默认项，其他选项才额外确认 | 保留现有弹框行为、PID/目标文件/作用域限制 | 用户现场确认；本轮 C# 弹框代码无修改 |
| 图号唯一，同名或同图号表示同一零件或升版，不同来源不会同批同时出现 | 保留现有拆分/合并命名和重复图号校验 | 用户确认；pdf_tools.py 与合并命名代码无修改 |
| 修复其余评审问题 | 恢复证据与流程完整性、关系补齐日志、helper 对象证据隔离、工艺包往返/02分类、路径解析已修 | test_recovery_validation.py、test_relation_repair.py、test_helper_upload_context.py、test_recognition_rules.py |
| 优化性能但保留关键校验与最终回读 | 去重复拆分、5秒健康缓存、验证内节点缓存与结果索引 | 真实PDF基准、HTTP探测计数、跨轮part_info新鲜读取回归 |

- 总回归：142 单元 + 19 独立 + 9 边界 + 21 Tk + 2 工艺专用 Tk = **193 项全部通过**；
  compileall 通过。识图依赖已在隔离的工作区安装，真实 PDF 测试未跳过；
  Windows C# 编译器可用，上传边界故障注入测试未跳过。
- 实际 PDF 基准：2 页 + 4 页，各版本 5 轮。拆分调用 4→2，拆分时间中位
  7.218→3.974 ms；总识别路径（视觉响应模拟）113.806→109.983 ms，
  该微型样本不足以推断真实模型/服务器上的整体加速比例。
- 连续 4 次问答、5秒窗口内健康 GET 为 4→1；TTL到期、换端点、POST失败后均重查。
  关系验证同父2条每轮part_info一次，下一轮仍重新读取；缺失节点每轮仅查询一次。
- 未连接 CAXA、未执行真实 helper/上传/流程。CAXA引用DLL本机不存在，
  仅真实CmdUpload方法在独立测试中编译，不代表完整helper已重编或现场验证。
- 发布状态：本轮交付源码，仓库已有EXE仍为历史构建；须在工作机重建后小批验收。
- 同一任务补发“审核打印”流程成功：`WFProcessInst 16292`，开始时间
  `2026/11/6 12:59:03`，状态 `2 OPEN__RUNNING`，关联 Drawing
  `35771`-`35777` 共 7 张。

## 2026-11-18 升版失败被误恢复为成功

- 同事机器 `DESKTOP-RGC9IBT` / `192.168.1.39` 的任务
  `T20261118130949-79b768` 已真机复现：
  `01.61.02.OV-11 / Drawing 31039` 与
  `01.36.02.OV-11B / Drawing 31035` 均未新增版次。
- 自动确认日志显示两次上传都在约 1 秒内返回，未出现
  `导入文件确认对话框` 或 `对象已经存在提示框`。因此自动确认线程没有
  匹配失败，问题发生在 CAXA 进入导入 UI 之前。
- 高层 `part.AttachFile(...)`、`AttachFileAsChild(...)` 和
  `AttachFile(Document, ...)` 均返回 `null`。低层回退中的
  `0x80004002 E_NOINTERFACE` 不是根因：在正常本机和失败机器上，
  `IEAPPartEntity` 对该接口的查询同样返回该结果，而独立
  `CxBclProductStructure` 对象的查询返回 `S_OK`。
- 根因定位为 helper 的 `Main` 没有 `[STAThread]`，控制台入口默认运行在 MTA；
  CAXA 的文件导入/重复对象处理依赖交互式 STA COM 上下文，因此高层
  `AttachFile` 在导入框创建前直接返回空对象。旧 helper 随后仍以已知旧
  Drawing ID 组装 `attached=true` 的 `append_iteration_unverified`，导致
  恢复器把未升版误判为成功。
- 两台机器的 32 位 CLSID
  `{F7950E30-7112-4D20-8610-2196CBDE2511}` 均注册为
  `ThreadingModel=Apartment`。多数同事机器上的 MTA 路径可用，但该机器
  稳定走 STA 依赖路径；两次上传均失败、切到 STA 后双图均成功，说明这是
  确定性的环境差异，不是随机偶发。
- 失败机器不是免注册部署：journal 记录
  `existing_registration_verified=true`、`registration_attempted=false`，
  helper 登录返回 `plm_com.managed_by_device_registration=true`；32 位
  CLSID 指向与正常机器相同哈希的 `PlmComApi.dll`。设备强制注册标记和
  COM 激活均正常，问题不在注册步骤失效。
- `ICxBclProductStructure` 的 `ProxyStubClsid32` 使用 Universal Marshaler
  `{00020424-0000-0000-C000-000000000046}`。代理/存根只负责跨 apartment
  封送，不能把 MTA 调用转换成满足 CAXA 交互式导入所需的 STA 执行上下文。
- 修复：所有挂接路径均为空且版次/历史未增长时，helper 返回
  `attached=false`、`method=append_iteration_unverified`、
  `stage=upload_verify`、`partial=true`、`uncertain=true`，保留旧对象 ID
  供人工核实，不再伪装成功。
- 新增回归 `test_helper_attached_false_is_never_treated_as_success`；恢复器
  继续要求历史数严格增长或版次增长。
- 验证：164 个内置测试通过（2 个可选 PDF 依赖测试跳过）、19 个独立验收、
  9 个边界验收、24 个真实 Tk、2 个工艺专用 Tk；`compileall`、x86 helper
  编译、上传端 one-file 重建和启动冒烟通过。
- 用新 helper 临时部署到失败机器复测同一路径：返回
  `attached=false`、`partial=true`、`uncertain=true`，版次保持
  `c.2 / history 5`，未再误报成功。
- 进一步给 helper 入口增加 `[System.STAThread]` 后，在同一失败机器、同一
  账号、同一 staging PDF 上双图对照成功：
  - `31039`：`attach=ok`，`c.2 → c.3`，历史 `5 → 6`
  - `31035`：`attach=ok`，`d.2 → d.3`，历史 `7 → 8`
- 该对照证明 MTA/STA 是成功与失败的决定性差异；`E_NOINTERFACE` 只是 MTA
  失败后低层回退的伴随现象。
- 当前本地发布产物：
  - `图纸上传助手.exe` SHA256
    `27fdc4f3fe8622f24af25357d5282c5fcc9eb57ec27076fbd9e4fb3901c04c56`
  - `caxa_helper.exe` SHA256
    `07abf7f495ca6235d4cf958a6ed8ba76c3386becc640bf235c3263b27400c8e8`
  - `caxa_helper.cs` SHA256
    `46f792ad489762eb712d3fdef07276bbe065e7f988f9d7c8ec496eedb3091efe`

## 2026-11-18 文件名规范化、02 半成品叶子与批量核实

| 用户确认/要求 | 当前实现状态 | 验证证据 |
| --- | --- | --- |
| 点击“一键规范文件名”后应能继续预检，不能丢 SHA-256/大小 | 已修；导入、原地改名和一键规范化都会重新登记指纹；可修复的格式提示不再阻断规范化 | 真实 PDF 与真实 Tk 回归 |
| 02 按点号层级管理，只创建缺失末级，且只能有一个非借用归属 | 已实现预检和执行；缺上级、根 Product、多个原件或多个非借用归属时阻止 | 上传器与预检回归 |
| 确认未写入后可回到主界面重试，并在待核实窗口直接重传 | 已实现；上传行恢复“待上传”，批量窗口提供“全部确认未写入并重新上传” | 恢复状态与 Tk/逻辑回归 |
| 多个待核实操作不要逐张弹窗 | 已改为单个合并列表窗口，支持多选处理 | 真实 Tk 窗口代码、恢复回归 |

真实样例：

- 文件：`C:\Users\user\Desktop\Z03.HG.010250A-11P03 空气压缩机.pdf`
- 规范化后：
  - 图号 `Z03.HG.010250A-11P03`
  - 类型 `process_doc`
  - 成品代码 `03.HG.010250A-11P03`
  - 上传目标名 `Z03.HG.010250A-11P03.pdf`
- 内容指纹：
  - SHA-256 `a3568ca076e8ce8f8c033fd7996309252e7a9a121bbacf77e85e593030ccf657`
  - 大小 `734274`
- 本地预检：`ready=true`、文件缺失 `0`、命名/字段异常 `0`、内容校验失败 `0`。

回归结果：

- 内置测试：173 个通过，2 个可选 PDF 依赖测试跳过。
- 独立验收：19 个通过。
- 边界验收：9 个通过。
- 真实 Tk 界面测试：25 个通过。
- 工艺专用 Tk：2 个通过。
- `compileall` 通过；上传端 one-file 重建和 8 秒启动冒烟通过。

当前产物：

- `release/图纸上传助手.exe` SHA256
  `3d28850f1db2c06f1454a17397d309ab30edd1c879fe141308bc98104a176da4`
- `release/caxa_helper.exe` SHA256
  `07abf7f495ca6235d4cf958a6ed8ba76c3386becc640bf235c3263b27400c8e8`

## 2026-11-19 02 半成品重复产品挂接排查与现场修复

- 现场 `journal(9).sqlite3` 的任务 `T20261118162825-3dc17c` 只记录了一次
  `02.A36.02.AR02` 上传，Drawing `36023` 成功；没有两次创建 Part 的证据。
- 用审核账号账号只读回读后确认，树中的两个
  `[02.A36.02.AR02]氩气过滤干燥橇` 不是两个 Part 对象，而是同一个
  Part `44594` 在 Product `02 半成品` 下存在两条产品挂接关系：
  - `part_info.products` 返回两条 Product `02`；
  - Product `02` 树的根零件列表返回两行 Part `44594`；
  - Part `44594` 下有 Drawing `36023`、CappDocument `15391/15392/15393`。
- 正确父级已确认为 Part `21117 / 02.A36.02 / 排气过滤器总成`。
- 修复操作只改变关系，不删除对象：
  - 保留 Part `44594` 到 Part `21117` 的归属关系 `89441`；
  - 解除 Product `02` 下两条重复产品挂接；
  - Part `44594` 和 4 个文档保持存在。
- 删除产品关系时 CAXA 弹出“确认删除当前对象”，helper 已补上删除关系作用域
  自动确认，窗口必须属于当前 helper PID；不会处理用户手工删除产生的窗口。
- 最终用审核账号账号重新登录回读：
  - `target_id=44594`
  - Product `02` 下目标行数 `0`
  - 正确父级关系数 `1`（link `89441`，`reference=false`）
  - Drawing/CappDocument 仍为 `36023/15391/15392/15393`
- 新版本按用户确认的 02 模型修正：
  - `02.A36.02.AR02` 从最近点号 `02.A36.02` 向上寻找已存在 Part；
  - 缺失末级 Part 只链接到最近上级，不再直接挂 Product `02`；
  - 同一图号多个 Part 对象阻止；
  - 多个产品/上级挂接合法，但只允许一个 `reference=False` 非借用归属；
  - `part_info.link_endpoints` 用于补足 EAP `ParentParts()` 空 ID 的兼容回读；
  - 新增 `unlink_part_relation`，只删除经端点验证的 Part2Part 关系对象。
- 验证：175 个内置测试通过（2 个可选 PDF 依赖测试跳过）；helper x86 编译、
  上传端 one-file 重建通过。
- 修复后再次以审核账号账号运行新版本状态判断：
  - `part_id=44594`
  - `parent_code=02.A36.02`
  - `own_link_count=1`
  - `product_link_count=0`
  - `blocked_reason=""`

### 2026-11-19 旧版 `02.A36.02.ARF25C` 清理

- CAXA“借用信息”页显示两条原件路径：
  - `半成品\氩气过滤撬`
  - `半成品\处理净化类总成\排气过滤器总成\氩气过滤撬`
- 低层回读确认这是同一 Part `43911`：
  - 正确父级关系 `87493 / 21117 / 02.A36.02` 已存在；
  - Product `02` 下另有一条错误直属原件关系；
  - 下挂 Drawing `35831`、CappDocument `15230/15231/15357`。
- 已用审核账号账号仅解除 Product `02` 直属关系：
  - Product `02` 下目标行数 `1 → 0`
  - 正确父级关系仍为 `87493`
  - 4 个文档 ID 未变化
- 新版本再次状态判断：
  - `part_id=43911`
  - `parent_code=02.A36.02`
  - `own_link_count=1`
  - `product_link_count=0`
  - `blocked_reason=""`

## 2026-11-19 上传端评审整改

范围仅限上传端；识图端代码、构建入口和旧一体化识图界面未修改。

| 评审项 | 当前处理 | 验证 |
| --- | --- | --- |
| 未勾选行变化改变批次摘要 | 摘要只使用选中执行行 | `test_unselected_row_does_not_change_batch_digest`、未勾选行修改后不重传 |
| helper stderr 不排空 | 启动排空线程，保留最近 300 行并附到异常 detail | `test_bridge_protocol` |
| 关系删除命令漏出非幂等集合 | 已加入 `unlink_part_relation` | 非幂等命令表回归 |
| 待核实窗口阻塞 Tk | CAXA 回读移至 `_recovery_worker()` | 后台 worker 与 UI 立即返回回归 |
| 事件异常后停止轮询 | `finally` 重新注册，异常写日志 | 真实 Tk `test_event_handler_error_still_reschedules_polling` |
| config 直接覆盖 | 临时文件 + fsync + `os.replace` | 原子写成功/替换失败保留旧文件 |
| SQLite 锁等待 | `busy_timeout=5000` | Journal 初始化路径 |
| C# 写后异常无统一证据 | 新增 `WriteError`，加固 Part/Product/目录/物料/关系写入 | helper x86 编译 |
| 同一账号提示范围过宽 | 仅当前 login 命令 + 当前 helper PID | helper 编译 |
| 手动借用缺少核心测试 | 成功、缺父、写后不确定、连续两条第二条失败 | `test_manual_borrow` |
| 旧一体化上传入口 | 属于识图界面，本轮按要求未改 | 范围确认 |

回归结果：

- 内置：187 个通过，2 个可选 PDF 依赖测试跳过。
- 独立验收：19 个通过。
- 边界验收：9 个通过。
- 真实 Tk：26 个通过。
- 工艺专用 Tk：2 个通过。
- helper x86 编译通过；上传端 one-file 重建和 8 秒启动冒烟通过。

当前产物：

- uploader `92e95fc0d718e04ee35cad2ea4ef9197a196927b89a4538be08e2deaec11cddc`
- helper `d584788a04d857bd5cfaa3cb656af06058138fd2fe659d3922a6ba764dfb81ec`
- helper source `635a626fa14761d7bed7cbaa7463e78acaf4fba6de39281ff0e32abd11cf7c51`

## 2026-11-20 v1.0.0 版本化与公开分发

- 根目录 `VERSION` 和 `app/version.py` 均为 `1.0.0`，新增一致性回归。
- UI 标题显示 `CAXA 图纸上传助手 v1.0.0`。
- 上传端构建输出改为 `图纸上传助手-v1.0.0.exe`。
- 内置测试：188 个通过，2 个可选 PDF 依赖测试跳过。
- 独立验收：19 个通过；边界验收：9 个通过。
- 真实 Tk：26 个通过；工艺专用 Tk：2 个通过。
- 上传端 one-file 重建和 8 秒启动冒烟通过。
- 当前版本化 EXE：
  `dc47c3a604df3f05e52738b32bfa9a3d2f2d371800aff6c145a6b703a6b8ecef`
- 公开树敏感项审计：
  - 未跟踪 `config.json`、SQLite、PDF、Office 文档、私钥或凭据文件；
  - 全历史令牌/私钥模式无命中；
  - 当前树真实账号姓名已替换为角色占位；
  - 本地 IP 按用户确认保留。
- 本地旧日期式 ZIP 和旧固定文件名 EXE 已移入回收站，仅保留：
  - `CAXA图纸助手-v1.0.0.exe`
  - `README-v1.0.0.md`
- 当前产物：
  - uploader `2694f42005d524d9a6142722574978454bed305b8c04ff3afabb3195fa73e68d`
  - helper `af4ec2ddfcf720fa8c9763b50dc446bed7e285a5f6d3c32add2ac7b0d6dbf182`
  - helper source `9d388e503a8c7b8d1d1a0e8e59842dfa4526a9822bd87802a3d7cf8f1106abfd`

## 2026-11-20 v1.1.0 每日检查与一键覆盖更新

用户要求：

- 上传端增加更新按钮；
- 每天第一次打开时联网检查 GitHub 最新版本；
- 发现新版后点击一次即下载版本化 EXE；
- 不要求现场手工替换文件，下载校验后直接覆盖旧 EXE 并重启。

实现：

- 正式 EXE 每天首次启动后台检查 GitHub `/releases/latest`，结果经 Tk 事件队列
  回主线程；当天已检查时不重复联网。
- Release 必须包含 `CAXA-Drawing-Uploader-vX.Y.Z.exe` 和 `sha256:` digest。
- 下载先写临时文件并校验 SHA-256；失败时不覆盖、不执行。
- 校验通过后，新版 EXE 以 `--apply-update` 更新模式启动；旧进程退出后，旧
  EXE 保留为 `.old-时间戳` 备份，新版覆盖原路径并重新启动。
- 覆盖失败恢复旧文件，写入 `%LOCALAPPDATA%\caxa_upload_journal\update_error.log`
  并显示错误框。
- `VERSION`、`app/version.py`、UI 标题和构建名同步升级为 `1.1.0`。

验证：

- 内置：199 个通过，2 个可选 PDF 依赖测试跳过。
- 独立验收：19 个通过。
- 边界验收：9 个通过。
- 真实 Tk：27 个通过。
- 工艺专用 Tk：2 个通过。
- `compileall` 通过。
- 更新专项覆盖：版本比较、Release 资产/digest 选择、缺 digest 阻止、每日状态
  缓存、下载 SHA-256 校验、哈希错误清理、更新模式参数、等待旧进程退出、覆盖
  原路径、旧文件备份和重新启动。
- 上传端 one-file 重建和真实更新模式冒烟通过。冒烟结果为：
  - 覆盖后的原路径 SHA-256
    `848e13f2bcf95206b09b95a3f4ace72c6cb41e19313a8b41df616788b2ddfbfb`；
  - `.old-时间戳` 备份保留旧内容；
  - 原路径成功重新启动新版；
  - 测试结束无残留上传端进程。
- `v1.1.0` 已发布：
  `https://github.com/rngt713-art/caxa-drawing-tools/releases/tag/v1.1.0`
- Release 目标远端提交：
  `da10fed7e47b042d9acf90dd6530dc754769d8ce`。
- Release 资产回读：
  - `CAXA-Drawing-Uploader-v1.1.0.exe`，
    `sha256:848e13f2bcf95206b09b95a3f4ace72c6cb41e19313a8b41df616788b2ddfbfb`；
  - `README-v1.1.0.md`，
    `sha256:fa9054da32bdc9472ab32793d2eace06aa1ae219ff1ba8c35973a47e753ed145`。

## 2026-11-20 v1.2.0 GPZ 特殊目录与复审整改

用户确认：

- 特殊项目 GPZ 在 CAXA 中没有 `GPZ系列`，既有目录名为 `GPZ钢瓶组`。
- 希望自动处理历史目录差异，不再要求现场手改。
- 外部 v1.1.0 上传端复审意见需要逐项处理。

实现：

- `03.GPZ.* → GPZ钢瓶组`；其他系列目录规则不变。
- 上传清单增加稳定 `batch_id`，选择变化不再创建新任务。
- 上传成功记录保存内容 SHA-256，同图号内容变化时重新上传。
- 更新下载期间阻止 CAXA 写入；busy 时下载完成后延迟覆盖重启。
- 更新替换改为临时文件 + 备份 + 原子替换。
- 损坏 update state 安全降级。
- helper 补齐 CreateProduct 目录挂接、LinkSubPart、set_drawing、
  unlink_part_relation 的部分写入证据。
- 复审逐项处理记录见 `docs/REVIEW_RESPONSE_20261120.md`。

验证：

- 内置：205 个通过，2 个可选 PDF 依赖测试跳过。
- 独立验收：19 个通过。
- 边界验收：9 个通过。
- 真实 Tk：29 个通过。
- 工艺专用 Tk：2 个通过。
- `compileall`、helper x86 编译通过。
- 上传端 one-file 重建与真实 PyInstaller 父子进程覆盖/重启冒烟通过。
- `.lnk` 快捷方式冒烟通过：快捷方式目标为安装 EXE，Start in 指向桌面；
  更新后目标路径和文件名不变，新版重新启动成功。
- 当前产物：
  - uploader
    `933deba87845cd2b89ba427317cf199105631c184e1d4c60f0c9cafc04ba07e0`
  - helper
    `ceac64b89da36bc79675248af40f1a54fee8b1d5fb3b313c961cb7f51e9cf566`
  - helper source
    `701824893594e8d9ef02584fad52b6b0d24db5274c24d17e2a717ab2aa388ba9`

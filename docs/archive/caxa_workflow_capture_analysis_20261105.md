# CAXA 升版、上传与流程发起抓包分析

> 范围说明：本文只记录升版、文件上传和流程发起的 EAP RPC，目标端口为
> `8900/tcp`。CAXA 客户端“高级查询”是另一条路径，抓包确认它直接访问
> SQL Server `1433/tcp`；本工具仍通过 EAP SDK 执行等价查询，不直连数据库。
> 详见 `docs/BUSINESS_AND_CAXA_FINDINGS_20261106.md` §8。

## 时间范围

- 抓包文件：`caxa_wf_capture_20261105.pcapng`
- 全程范围：`2026-11-05 13:55:07` 至 `15:33:13`
- 本次分析窗口：`2026-11-05 15:28:00` 至 `15:33:15`
- 目标连接：`192.168.1.251:随机端口 -> 192.168.1.28:8900`

## 前端动作对应的时间与数据

捕获到 6 轮独立的 PDF 上传。每轮都包含 `CUnifyIDStub/CreateID`、
`CVaultFileStub/BeginPutBuffer` 和两次 `CTransmitFileStub`。

| 时间 | 服务端临时文件 | 客户端发送字节 | 对应拆分件 |
|---|---|---:|---|
| 15:29:40 | `file_211691.dat` | 404,048 | `01.59.OV.03 焊接管路包` |
| 15:29:55 | `file_211692.dat` | 453,204 | `01.59.OV.03-03 二级缸进气管` |
| 15:30:04 | `file_211693.dat` | 444,422 | `01.59.OV.03-04 一级缸排气管` |
| 15:30:16 | `file_211694.dat` | 959,108 | `01.59.OV.03-08 气缸进排水管道` |
| 15:31:10 | `file_211695.dat` | 845,189 | `01.61.02.OV-11 一级缓冲罐` |
| 15:31:25 | `file_211696.dat` | 938,610 | `01.36.02.OV-11B 一级气水分离器` |

上传字节数比本地 PDF 文件大约 1.8–1.9 KB，差额与协议元数据、文件名和
对象头长度一致，因此可以按大小精确对应。

## 升版与普通新增上传的对象差异

本次 6 次上传都复用了原有 `drawing(1002)` 对象 ID，没有新建图纸对象。
每轮上传创建的对象类型一致：

- `drawingItr(3221)`：新的“图纸版次”
- `drawingRevcmp(3224)`：现有“图纸版本”到新版次的关联
- `File(3064)`：新的文件对象
- `vaultfile(3066)`：库内文件内容
- `drawingItrfile(3225)`：新版次到文件对象的关联

因此界面的“在现有版本上追加新的版次”实际是在同一个图纸 master 和同一个
`drawingRev` 下追加新的 `drawingItr + File`，保留旧版次历史。普通新图纸导入
才会新建 `drawing(1002)` 本身及其初始版本/版次链。

本次使用的不是“使用新的版本替换库中已存在版本”，也不是“版本合并”。
helper 的 `new_release` 是另一条 `EAPDrawingEntity.NewRelease()` 路径，
不能与导入对话框中的“追加新的版次”混为一谈。

## 流程发起后的服务端对象

- 流程实例：`WFProcessInst(16289)`
- 创建/发起人：`上传账号A`，Person ID `108`
- 模板对象：`WFTemplate(1) 审核打印`
- 流程模板实例：`WFProcessTemplate(31)`
- 流程定义 ID：`2`
- 审核动态活动定义 ID：`4`
- 模板内部审核活动实例：`WFActivityInst(79123)`
- 审核工作项：`WFWorkitem(48227)`
- 审核参与者：`Person(66) 审核账号`
- 流程状态：`2`，`OPEN__RUNNING`
- 开始时间：`2026/11/5 15:32:37`

流程关联的 6 个文档是：

| 图纸 | Drawing ID |
|---|---:|
| `01.36.02.OV-11B 一级气水分离器.pdf` | `31035` |
| `01.61.02.OV-11 一级缓冲罐.pdf` | `31039` |
| `01.59.OV.03 焊接管路包.pdf` | `35441` |
| `01.59.OV.03-03 二级缸进气管.pdf` | `35444` |
| `01.59.OV.03-04 一级缸排气管.pdf` | `35445` |
| `01.59.OV.03-08 气缸进排水管道.pdf` | `35451` |

## 关联对象与参与者底层关系

“关联对象”页签最终写入 `WFInstObjDocument`，不是普通
`WFAssociatedObject`：

- 关系类：`WFInstObjDocument`
- Class ID：`3305`
- 流程端角色：`WFInstanceWithDocument`
- 文档端角色：`DocumentOfWFInstance`

抓包在 `15:32:38.918–15:32:39.021` 连续出现 6 次
`CreateID`，请求中的 class ID 是 `3305`，响应 ID 依次为：

`448958`、`448959`、`448960`、`448961`、`448962`、`448963`

数量与 6 张关联图纸完全一致。

审核人底层关系是 `WFWorkitemPerform`：

- Class ID：`2593`
- 工作项端角色：`AssignmentOfPerson`
- 人员端角色：`AssignmentPerform`
- 人员类：`Person(1008)`
- 当前目标：`Person(66) 审核账号`

界面上显示的“审核账号”是属性解析后的文字，持久化关系使用的是
Person ID `66`。

## 对 helper 接口的结构化映射

实际协议是 CAXA 自定义二进制 RPC，不是直接发送普通 JSON 文本；其业务字段
对应如下：

```json
{
  "template_object_id": 1,
  "template_instance_id": 31,
  "process_def_id": "2",
  "activity_def_id": "4",
  "performer_ids": [66],
  "drawing_ids": [31035, 31039, 35441, 35444, 35445, 35451]
}
```

调用链为：

1. `CxDalWfConnection.connect(user_id)`，当前 `上传账号A` 对应 `108`。
2. `CxDalWfProcess.create(1, 31, "2", name, description, notify)`。
3. 建立 6 条 `WFInstObjDocument(3305)`：流程实例 -> Drawing。
4. `designate_dynamic_performer(process_id, "2", "4", 1, "66")`。
5. `CxDalWfProcess.start(process_id)`。
6. 回读流程实例、6 个文档、审核活动和 `WFWorkitemPerform`。

## helper 状态

已新增：

- `release_drawing`
- `start_workflow`

`start_workflow` 默认 `execute=false`，只校验模板、图纸和人员参数；
只有显式传入 `execute=true` 才创建真实流程。helper 已编译成功。

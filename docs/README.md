# 文档索引

`docs/` 按主题维护当前有效文档；历史按日期追加的记录、抓包原始报告与阶段性评审文件已统一归档到 [`docs/archive/`](archive/)，仅作追溯参考。

## 活跃文档导航

| 文档 | 目标读者 | 涵盖内容 |
| --- | --- | --- |
| [BUSINESS_REQUIREMENTS.md](BUSINESS_REQUIREMENTS.md) | 全部开发、测试、AI Agent | 业务规则基线：规则优先级、命名分类、归属/借用、03 整机与系列目录、02 半成品、物料编码、弹窗与流程、恢复与本批借用原地撤回、分发与外部边界 |
| [IMPLEMENTATION.md](IMPLEMENTATION.md) | 研发与代码评审 | 软件架构与实现说明：模块划分、文件暂存、关系建模、SQLite 日志与状态机、原地撤回借用实现、`CaxaBridge` / `caxa_helper`、UI 并发快照、自动更新与性能要点 |
| [CAXA_INTERNALS.md](CAXA_INTERNALS.md) | 修改 helper 或排查 CAXA 问题的研发 | CAXA 2011r3 逆向与 SDK 手册：ClassID 速查、工作流 DAL 抓包、高级查询 SQL 与 SDK 反查、`LinkSubPart` 回读回退、`PlmComApi` 注册与 PATH、EAP 缓存刷新、弹窗守卫 |
| [BUILD_AND_TEST.md](BUILD_AND_TEST.md) | 构建、发版与现场验收人员 | 构建与测试指南：私有 CAXA 引用目录配置（含 `CS1703` 防坑）、x86 helper 与上传端编译、离线单测/验收命令、工作机真机验收清单与探测脚本、正式发版流程 |
| [VALIDATION.md](VALIDATION.md) | 研发、测试、项目跟进 | 当前验证状态面板：最新离线单测/验收结果、已验证与待真机小批验收矩阵、历史验证日志归档索引 |
| [HANDOFF.md](HANDOFF.md) | 跨窗口接力开发者 / AI Agent | 当前状态交接：已发布版本与主干最新提交、本轮完成项、待办真机验收项、已知限制与下一阶段规划 |

## 推荐阅读顺序

1. **任何改代码或评审前必读**：[BUSINESS_REQUIREMENTS.md](BUSINESS_REQUIREMENTS.md) → [HANDOFF.md](HANDOFF.md) → [IMPLEMENTATION.md](IMPLEMENTATION.md)。
2. **涉及 `caxa_helper.cs` 或 CAXA 客户端行为时加读**：[CAXA_INTERNALS.md](CAXA_INTERNALS.md)。
3. **准备编译、离线回归、真机测试或发版时阅读**：[BUILD_AND_TEST.md](BUILD_AND_TEST.md) → [VALIDATION.md](VALIDATION.md)。

## 文档维护约定

- 新增业务规则、兼容回退、测试记录或实现变化时，**直接更新对应的主题文档**，避免再新增带日期后缀的零散文档导致规则分散或前后矛盾。
- 已被替代的大段过程记录，按需归入 [`docs/archive/`](archive/) 保留追溯，不在活跃文档中混写“历史临时口径”和“当前最终口径”。
- 公开仓库文档禁止出现真实员工姓名、密码、Token、DPAPI 密文、生产库账号或个人本地私有路径；账户统一使用角色占位（如 `上传账号A`、`审核账号`、`测试账号`）。

## 历史归档清单

[`docs/archive/`](archive/) 中保存了早期按时间记录的原始文档：

- 早期架构与评审：[ARCHITECTURE_REVIEW.md](archive/ARCHITECTURE_REVIEW.md)、[REVIEW_RESPONSE_20261120.md](archive/REVIEW_RESPONSE_20261120.md)、[CAXA图纸工具_架构评审报告.md](archive/review/CAXA图纸工具_架构评审报告.md)、[REVIEW_FEEDBACK.md](archive/review/REVIEW_FEEDBACK.md)
- 历史抓包与事件原文：[caxa_workflow_capture_analysis_20261105.md](archive/caxa_workflow_capture_analysis_20261105.md)、[caxa_advanced_search_capture_20261126.md](archive/caxa_advanced_search_capture_20261126.md)、[INCIDENT_20261121_LINKSUBPART.md](archive/INCIDENT_20261121_LINKSUBPART.md)、[BUSINESS_AND_CAXA_FINDINGS_20261106.md](archive/BUSINESS_AND_CAXA_FINDINGS_20261106.md)
- 历史修复、构建与测试原文：[FIXES_AND_PERFORMANCE_20261113.md](archive/FIXES_AND_PERFORMANCE_20261113.md)、[PRIVATE_BUILD.md](archive/PRIVATE_BUILD.md)、[SOURCE_PACKAGE_INFO.md](archive/SOURCE_PACKAGE_INFO.md)、[VALIDATION_20261106.md](archive/VALIDATION_20261106.md)、[CAXA图纸工具_真机测试与验证清单.md](archive/review/CAXA图纸工具_真机测试与验证清单.md)

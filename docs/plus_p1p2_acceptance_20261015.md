# PLUS P1/P2 工程交付与分层裁决 — 2026-10-15

结论：P1/P2并行工程补强已实现；历史研究为REVISE，前瞻优势及经济验收BLOCKED。P0不在本次验收范围，仍由原工作线核验10月16日发行与10月17日到期证据。

基线main：`59dab912fd08bc3033355dc76696b13be37a4dd7`。分支：`upgrade/plus-p1p2-shadow-20261015`。仅新增文件；所有原config、system、data、research、冻结代码和既有Actions均保持原始Git字节。克隆造成的CRLF经逐文件证明只有换行差异后还原，原文件内容差异为0。

| 层次 | 裁决 | 已完成及限制 |
|---|---|---|
| P1数据审计工程 | PASS | DOGE/BTC/ETH全资产缺失门禁；实际日/小时数据及链上、鲸鱼、衍生品、宏观、增强源逐项审计；不确定分母保持未知 |
| P1研究 | REVISE | 实际六折phase1和1056次phase2拟合、分组消融、概率校准/状态比较；保留7项STOP，不凭历史改善激活或删除因子 |
| P1生产PIT准入 | BLOCKED | 历史receipt-time可用锚点0；宏观vintage、实体归属、历史first-seen及独立前目标见证不足 |
| P2诊断工程 | PASS | 同事件双向召回/Brier/固定校准桶、完整成熟分母、canary/未成熟排除；原固定look和多重校正沿用 |
| P2新协议准备 | REVISE | 新增明确数值晋级/回退草案与缺身份/见证阻断校验；状态DRAFT_NOT_REGISTERED，没有运行时激活入口 |
| P2前瞻优势/经济验收 | BLOCKED | 当前R2只有1个canary、0个成熟正式事件；其既有正式窗口10月20日开始。纸面经济报告仍WAIT，不能证明胜出、70%或盈利 |
| P0 | 未由本轮裁决 | 无发行/评分工作流调用，无调度、触发器或正式指针变更 |

## 验证

本地全仓：`python -m unittest discover -q` → `Ran 967 tests in 58.043s`、`OK (skipped=5)`。5项为Windows符号链接权限相关跳过；完整输出在`audit/plus_p1p2_20261015/tests.log`。最初编码与CRLF检查失败保留为本地排障日志，最终验证采用UTF-8和原始Git字节。

`forward_validation_v2.py --integrity-only`验证原始已提交预测/评分历史及协议；`phase3_runtime.guard()`验证独立注册；`rolling_audit()`核验原归档1056模型。独立代码审查发现并修复未成熟事件分母及错误资格推断，最终复查无阻断问题，并增加“未成熟但已有分数不能替代缺失成熟槽”回归。

新增CI为只读push/PR验证，无schedule、发行、交易、部署或写仓库权限。CI与本地测试分别记录，不把本地成功冒称云端成功。

## 复核入口

- `docs/p1_plus_upgrade_20261015.md`：来源截止、全部36项去留、消融和实际外部数据缺陷。
- `docs/p2_plus_upgrade_20261015.md`：原模块复用、阈值、分母、统计与经济限制。
- `docs/plus_official_framework_assessment_20261015.md`：Qlib/FreqAI/R&D-Agent官方来源和不新增依赖的适配判断。
- `audit/plus_p1p2_20261015/integration_receipt.json`：原始身份检查、数据摘要和证据SHA256。
- `audit/plus_p1p2_20261015/p1/phase2_v2_summary.json`：实际拟合及压缩完整结果的双重SHA256。
- `audit/plus_p1p2_20261015/p2/future_shadow_protocol_draft.json`：未注册/未激活的新草案。

未完成：新的精确候选/特征绑定与前瞻公开注册、独立完整公开见证、足够正式样本、连续经济路径与成本验证、锁定外部留存、P0验收。没有更改DOGE定投规则或实盘，不自动晋级、不事后补预测。

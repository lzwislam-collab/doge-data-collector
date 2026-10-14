# 第三层独立评审实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 冻结独立统计评审、同事件竞争及纸面回滚。
**Architecture:** 新模块只读R2，独立注册，日常诊断和完整固定审阅分离。
**Tech Stack:** Python、现有NumPy、GitHub Actions。
**Spec:** ../specs/2026-10-14-phase3-independent-design.md

## Global Constraints
- R2已注册文件、原18模型与生效时间不变；无新cron/Apps Script触发器；无实盘订单。
- 每次64命题、两次固定审阅、10000家族、原始完整槽分母，不用回测晋升。

## Review Focus
- 缺失事件及未知发行行情不得缩小分母。
- 局部网络审计不能当全样本审计。
- 随机行情子样本不得直接使用固定样本量置信界。
- 经济获胜频率不能冒充原始收益均值置信区间。
- 伪造统计批准或版本/回退包不匹配不得切换。

### Task 1: 固定统计与竞争
Files: phase3_independent.py, test_phase3_independent.py, config/phase3_independent_v1.json。
Interfaces: lower_bound(values,lo,hi,alpha); evaluate(policy,events,scores,qualified,now,full_audit,registered)→只读诊断。
- [ ] 写测试并观察缺模块/接口失败。
- [ ] 实现完整预定槽、最坏缺失、固定审阅、方向/Brier/经济与可预测行情掩码。
- [ ] 运行专项测试通过，提交。

### Task 2: 纸面生命周期
Files: phase3_lifecycle.py, test_phase3_lifecycle.py, system/phase3_candidates_v1.json。
Interfaces: genesis(registry); transition(state,command,registry,at,evidence)→哈希链状态，不写真实生产。
- [ ] 写拒绝提前批准、缺存储、错误回退包、覆盖/重复及到期回滚测试，观察失败。
- [ ] 实现并执行synthetic独立演练，专项测试通过，提交。

### Task 3: 独立注册与云端回读
Files: phase3_runtime.py, test_phase3_runtime.py, config/phase3_registration_v1.json, .github/workflows/phase3_independent.yml, reports/DOGE_PHASE3_INDEPENDENT.md。
Interfaces: guard/registration_witness、readonly runtime routine/full；精确manifest与Git历史保护。
- [ ] 写局部见证/晚注册/篡改测试，观察失败。
- [ ] 实现新工作流及不可变注册，运行原R2审计与全部测试。
- [ ] 独立审查、修复Important/Critical，推送PR并验证分支/合并main运行及产物字节。

## Pre-flight ledger
Task1→Task3: evaluate接收实际重放且network-audited的全事件与qualified；routine永不输出准入。
Task2→Task3: 演练输出只为synthetic临时状态；真实候选初始REGISTERED不覆盖原生产。
Ruling: 按用户已明确立即实施与按计划推进的授权执行，不重复等待设计/推送批准；新统计输出仍不能批准生产。
Final: fixed Important full audit rate limit — test_1080_events_have_bounded_per_batch_run_requests RED→GREEN; 新增20事件预算批次与实际成功main产物汇集，冻结R2不变。
Final: minor deferred paper self-sealed state authenticity — 文档明确仅simulation_only意外损坏检测，真实批准未实现，不能充当授权。
Final: fixed Minor missed review status — test_late_run_identifies_missed_fixed_review RED→GREEN。
Ruling: 初次本地基线遗漏PYTHONUTF8导致12项编码错误；显式UTF8后相关测试46项及完整874项通过（4个平台跳过），不修改旧系统代码。
Post-delivery Ruling: 原全样本成本胜率同时胜cash/hold过半不可达，且旧fixture不现实。正式样本0时新建独立assessment/schema V2重注册；保留旧提交及工程记录、拒绝旧批次混用。test_realistic_long_cash_cost_gate_uses_issue_time_exposure_mask RED→GREEN，预测时不同敞口掩码条件检验，全分母/原始成本/其他准入门槛不变。
Cost review: fixed Important stress fixture — test_stress_costs_cannot_improve_same_long_or_cash_payoff RED→GREEN；上涨压力.01<=名义.02，下跌压力-.03<=名义-.02，cash均0。

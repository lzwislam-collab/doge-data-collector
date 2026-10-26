# DOGE Predict PLUS — One-pass scoped remediation, 2026-10-15 (JST)

## Decision and stop rule

Status: **REVISE; prediction edge, 70% accuracy, and cost-adjusted profit NOT PROVEN.**

This is **one bounded repair**, not a perpetual audit/redesign loop. Baseline main commit: `fa4618666ec041575953514df461dfc288ac792c`. Source-audit snapshot checked through 2026-10-15 08:55 UTC. Do not alter frozen forecast/model versions, histories, labels, target scoring, collector schedules, Google Sheets, Apps Script triggers, account/strategy data, or production-trading permissions.

**Stop modification after:** the scoped regression passes, pull request is merged, exact main SHA and CI completion are read back. Thereafter **collect future evidence only**. Reopen engineering only for reproducible new faults that corrupt issuance, qualified target data, source chronology, model comparison, or score integrity; do not reopen solely because actual accuracy is low or future sample thresholds are unmet.

## Defect and implemented correction

Lean-v1 removed the schedule for an unproven supplemental confirmed-whale feed. The pre-existing comprehensive source auditor still marks its scanner as required. Actual `data/quality/latest.json` (2026-10-15 07:56 UTC) returned `status=blocked`, `critical_failures=['whale_confirmed_blocks']`, while the market source status was `ok`. A real auxiliary-source failure thus made the **entire market research report** look invalid despite healthy core market data.

The new **read-only** `report_evidence.evidence_state` market-scope classification applies only if:
- The sole comprehensive critical failure is `whale_confirmed_blocks` and its source shows the actual failure.
- The current independent market source reports `state=ok`, all other required sources report valid states, the audit has a valid recent timezone-aware timestamp, and the history cutoff matches the direction record.
- Full quality status remains literally `blocked`; critical failure and supplemental missing/old coverage remain visible. The narrowed status is `RESEARCH_ONLY_MARKET_SCOPE_AUX_BLOCKED_NO_VALIDATED_EDGE`, **not** P0/P1/P2 PASS, an active trading direction, or forecast release authorization.

Any other required source fault, malformed source row, stale quality, inconsistent daily cutoff, or unavailable market input keeps `DEGRADED_NO_VALIDATED_EDGE`. Existing `feed_freshness_audit.py` and `data/quality/latest.json` are unchanged. Tests cover the exception and refusal cases; the existing full four-layer regression workflow now also runs when these files are edited. No schedule was added.

## Audited recommendations: reuse existing solutions instead of inventing new ones

| Objective | Verified existing control | What is **not** established |
|---|---|---|
| Isolated formal forecast version | `lean_version_gate.py`, `plus_shadow.py`, separate original18/R2/legacy namespaces | No formal newly validated direction may be improvised from conflicting V4.2 opinions |
| Pre-issue data/PIT | `factor_eligibility_v1.py`, `research_artifact_audit.py`; availability clock and raw witness guards | Old ETF/macro revisions, unverified whale attribution or incomplete liquidation windows are not eligible predictors |
| Model improvement/ablation | Frozen original18 groups, `feature_ablation.py`, `factor_evaluation_v1.py`, `phase3_independent_v1` | Independent improvement over frozen, constant-direction and BTC baselines not proven |
| Scoring/calibration | Frozen same-event baseline, Brier and preregistered fixed-look tests | A small overlapping high hit rate does not equal calibrated independent 70% performance |
| Net economics | Existing P2 3.1 / `p2_economic_acceptance_v1` paper ledger with fees/slippage, cash and hold comparisons | No robust positive net advantage, full-path drawdown or live fills proven |
| Evidence custody | GitHub issue-time record receipts/hash chain; OSS export exploration | Public GitHub history is not third-party immutable WORM; independent retention remains unverified |

Reusable external background: scikit-learn TimeSeriesSplit with chronological gap (https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) and held-out probability calibration (https://scikit-learn.org/stable/modules/calibration.html). Existing frozen study's stricter PIT and multiplicity rules prevail; **do not** substitute library defaults into the live model.

## Finite follow-up milestones, no automatic model redesign

1. **Engineering-only merge gate:** full existing unit/regression suite, version-isolation/frozen-integrity checks, GitHub success and main code readback. A push-run green check proves tests, **not** production schedule stability.
2. **Original18** starts no earlier than 2026-10-16 00:00 UTC (09:00 JST). Require actual post-activation scheduled issuance, matching public witness, target receipt after its own 1h or 24h horizon, and score readback. No backfilled missing forecasts.
3. **R2** starts no earlier than 2026-10-20 00:00 UTC (09:00 JST). Exclude engineering canary and other versions. Its first preregistered fixed review is 2027-01-18 00:25 UTC, **not** 2026-10-17.
4. **P0**, independently of model significance, keeps its genuine rolling seven-day issuance AND matured quote denominators, at least 90% each, as defined by existing protocol; past misses cannot be erased. Initial timestamps are inspection opportunities, not a promise of PASS.
5. **P1/P2** retain their original sample/fixed-look/market-regime/statistical/fee/cash/hold/drawdown gates; no retroactive changes to hit-rate thresholds or cohort definitions.
6. **External immutable evidence** remains a separately disclosed infrastructure gap. Do not call the system independently WORM-verified without actual remote storage setup and receipt.

## Closure declaration

This one-pass change is limited to **report classification, tests, one existing CI path trigger, and this document**. If all regression gates succeed, make no more unsolicited engineering changes and switch to genuine prospective observation. If CI fails, fix only the reproducible regression in the same bounded change. If the first real scheduled run fails, a **single narrowly scoped incident repair** may be opened, with immutable original events preserved. No new monitor, cron, or duplicate project governance is authorized by this note.

**Source of truth:** Existing canonical CURRENT Library + Google Sheets Authority Registry; this document is a supplemental GitHub audit note, not a competing authority or new prediction model.


<!-- DOGE-CONVERGENCE-EXECUTION-20261026-V1 -->
## 2026-10-26 执行规范补充：沿用原规划，不重开治理或冻结窗口

本节为用户授权的工程收敛规范，正式保存于独立草稿分支，尚未合并主线；不创建第二个 CURRENT、注册表、模型、PR、任务或触发器。唯一行动总控仍为 [10月26日原规划 A–K](https://github.com/lzwislam-collab/doge-data-collector/issues/5#issuecomment-6406584258)，维护证据沿用 Issue #5；研究准入沿用 Issue #10，E1异常沿用 Issue #70。原规划的 ID、负责人、依赖、期限和既有验收阈值不变；本节只明确执行和关闭方式。

### 权威核对与兼容

本次实读唯一 Library CURRENT `libfile_a418bf13c4548191ad5765d68d0f3abe`、生产 Sheet 的 Authority Registry!A1:Q30、Review Split v1.0!A1:F30，以及仓库版本隔离、一次整改、有限整改、horizon 冻结协议与 current 指针。CURRENT 最后指针明确委托 Report PLUS Integration Audit 与 Review Split；没有复制或改写 CURRENT/Registry。机器 current 文件是各自日期与命名空间的快照，不得把10月13日局部工程通过当作今日总体通过。

CURRENT/Registry 记载10月21日定投已作废，而本地项目说明仍引用5.4 FINAL；本次仅登记差异，不据此修改、恢复或执行任何定投规则，也不让历史定投许可成为独立研究前置条件。Collector 的静态登记为v1.2.5，本地说明为v1.2；本次不改采集器，真实自动采集结论仍须实际代码、触发器、执行记录与同次新增数据回读，不能用静态版本号替代。

原“冻结修复循环原因”助手正文读取仍为 content-reference 占位符，无法声称逐句全文审计完成。本节依据本次用户明确列项、实物治理和原规划核对；没有补造缺失讨论。

### 根因去重、P0分级与有限退出

每个已确认根因只在原规划所属项下维护一份记录：稳定根因键、首个源run/attempt/实际checkout、影响命名空间/步骤、原始证据、最小修复SHA、验收项、当前状态、负责人和下一复核点。以“受影响组件＋稳定失败原因＋冻结协议身份”归并；新run/新artifact是新实例证据，不因ID变化另立一轮修复。同一根因的源失败、下游拒绝和观察告警保留父子关联，不相加成三个独立代码缺陷。不同bundle不互相覆盖，旧失败不删除。

- P0完整性阻断：可复现的历史/模型/协议漂移、未来信息、重复/错绑发行、评分/证据不一致，阻断受影响写入或PASS；保留现有fail-closed和单写入锁，禁止全局停掉无关自然采样。
- P0运行阻断：关键采集/发行/目标/发布尾链或证据回读失败，受影响链保持FAIL或NOT_VERIFIED；先定位源输入、条件、步骤和实际版本，再做同根因最小修复。缺来源不能以放宽资格、忽略drain或伪造marker消除。
- 等待类：未到期、尚未自然触发、样本不足、正确的NO_TRADE及非必需辅助源缺失，按原规则记WAIT/N/A/PARTIAL；不是修模型理由。历史错过截止仍FAIL，不将其降成等待。原规划已列P0的C/D时区/中国网络事项不因本轮“P1运维”称谓降级。
- 同SHA、同证据、无新故障不再机械复审/复测。重开须给出新的可复现故障或改变既有结论的新证据；低命中率、绿色图标变化或尚无样本不构成代码修复依据。

关闭分两层：代码/离线范围完成后即停止该范围改动；生产验收保留独立待验项。自然证据足够时按原标准关闭；自然条件未发生就记录NOT_VERIFIED和下一原规划复核点，不人工补跑、不延长冻结截止、不增加新指标或新系统来救绿。

### PR验收证据与职责收敛

每次验收必须绑定PR head、实际受测checkout/合并检出、原始日志/产物SHA256、执行环境、唯一用例身份、发现/执行/通过/失败/错误/跳过数、冻结及历史保护检查、远端精确文件回读。历史1319等汇总保留来源，定向复跑不增加分母；必要路径SKIPPED不计PASS。旧PR描述中“测试仍在跑”与已合并事实分开，不能仅按旧正文推断现状或编造后续通过。

| 所属原规划/对象 | 本次核实与唯一职责 | 退出条件/仍待验 |
|---|---|---|
| B，PR75 | open Draft，head 1ce33cca350c34f704f8b7a239df8d8c4f1499ce。只完成40248370100根因、最小预算模块、已有离线回归及证据同步；不移植main整套运行服务，不重复PR83 | 已有42项定向离线PASS及原14项回执日志为既有证据，本轮未重跑。旧head生产接入未完成，生产恢复未验证；保持Draft、不合并/部署/补跑/上传OSS/建触发器 |
| E，PR81 | 已合并300912566b6922ce775c9b4c65556a2e0fcfb4d6；有界一次fresh-source恢复，保留90秒门槛 | 须早期可选源失败且单写入服务安全结束后，真实恢复条件执行、原件校验、确认K线刷新、原子回执和源精确远端回读，完整tail及下一自然交接；条件跳过不能关闭 |
| E，PR82 | 已合并e22dab441bf30e69cff87bb7fc59c295031a43ae；独立临时索引仅发布有效marker，普通非强推 | 须同run有效prep、artifact成功、安全drain、marker实际发布并精确回读、runner原字节/索引/HEAD保护、完整尾链及下一自然交接；上传成功或marker跳过均不够 |
| B/E，PR83 | 已合并6da5ee2c767e226a6d4da98df0446d0bad68be61；head 5a1111aa7a9eb3ce5fee60f5d8abeb34324f4539，预留1800秒 | 须自然运行记录真实预算计划，正式预约目标前合格盘口、qualified stop、上传及完整tail；时间回放不算自然通过，不改预约/截止 |
| B，已有PR84 | 本轮开始前已存在，open Draft，head fe18314569664d33c8dbd6483762546c6acdf3f0；资格失败仍fatal=false/尝试最终发布为独立传播缺陷 | 仅纳入去重，不另新建PR或追加实现。CI通过范围与16跳过分列；真实生产恢复未验证，保持草稿，不由本轮合并部署 |

“审计项目讨论并更新规划”负责原规划/证据归口与新根因增量；“修复 PR 归档失败”只负责PR75上述已授权交付。已向两个原对话发出职责边界，不取消自然任务、不关闭对话；消息送达不等于对方已执行。已有PR84保持独立历史，不把它算作本轮新增PR。

### 冻结与自然采样

正式模型、权重、协议、预约/选样、身份、有效期、训练截止、成本、原始历史发行/评分及已错过目标窗口均冻结。既有正常自然任务按其原权限持续采集、发行、取目标和评分；受影响事件按原门禁拒绝，不补造历史、不改分母、不把影子/研究/分钟/canary计入正式1H/24H/7D。1H/24H/7D独立protocol不与original18/R2/Sheet旧轨混算；NO_TRADE和未校准概率按原协议如实保留。

P0滚动7日发行及到期目标覆盖分别沿用原≥90%门槛，列expected/issued/matured/scored/missing、UTC截止和逐事件来源，未到期N/A、到期缺失不删除。工程稳定和预测有优势是两项独立结论。10月20日工程截止已过的结论不得延期改判，后续维护只追加证据。

### P1运维验收映射（仍沿用C/D/H/F/K原优先级与期限）

云端执行与本地访问分别验证：实际运行环境、run/attempt、完整tail与下一交接；固定版本OSS对象/保留终点/完整GET哈希和隔离恢复/独立审计；不得用日本本机可用推断中国实网或云端登录恢复。UTC是协议时钟，JST=UTC+9、中国时间=UTC+8；同UTC输入须槽ID/目标/截止/分母一致，跨日、边界和错误偏移拒绝。Sheet当前metadata为Asia/Shanghai，仅证明表格时区，不证明GAS/客户端任务时区。GAS与既有任务后端日程仍须实读；不改设备/生产时区，不平移发行或冻结截止。返程日期未知，出发前48小时/抵达24小时复核仍依赖真实环境，不虚构日程已自动安排。日线手动测试如确需只运行runOkxDaily，不创建触发器。

### P2独立前瞻与每周统一报告

J仍为准入未通过。沿用已冻结的固定look、样本、非重叠/训练隔离、同事件独立评分、多重比较及regime要求；分别比较Always Up/Down、BTC momentum、协议指定冻结模型、现金/持有基准。成本含原冻结手续费、价差/滑点及压力场景，报告成本后净收益、基准超额和回撤；缺盘口/成本或不成熟结果为null/N/A/WAIT。1H无合格阈值时FROZEN_NO_SIGNAL/NO_TRADE保持，不能事后调参。工程测试绿、小样本命中、回测或现金收益不证明70%准确率/盈利。无新模型、因子、训练或晋升授权。

每周复用Registry已登记周学习任务6aaeadad489c81919eebd42eda66efd7与原Model Learning Log，遵守Review Split的UTC ISO周幂等键，不新建定时器。统一报告按原A–K项列：来源及截止、数据缺失/重复/时间戳质量、根因键及新增证据、代码/生产两层状态、各轨样本/固定分母/缺失、相同事件基准及成本后结果、P1待验、下一原截止与所需人工输入。证据追加到Issue5原规划；没有变化仅保留状态，不重启整改。该规范已写入草稿不表示现有周任务提示词已更新或自动运行验收通过；未实读周任务后台配置及新版自然写表回读，运行态仍NOT_VERIFIED。

### 本轮实读事实、截止与验收裁决

证据读取范围截止（UTC）：2026-10-26 03:13:12 UTC（JST加9小时，中国时间加8小时）；仓库文档基线固定e124279d03727be572595889c3151dcae397157c。查询为有界最新运行及指定run/jobs/logs，不是全历史或全数据QA。

- [源run40248370100/job121342440960](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/40248370100/job/121342440960)：schedule、最终cancelled；53步drain失败，54步marker与20步PR81恢复均skipped。日志01:48:57 UTC确认artifact12623035164上传成功，大小1511705224；01:54:21报archive capture service failed。不能称“库存上传失败”。冻结预约空档/归档过晚及fatal传播缺陷的固定来源调查见[原规划增量](https://github.com/lzwislam-collab/doge-data-collector/issues/5#issuecomment-6407034976)。PR75离线原包结论限固定包，本轮未重下载1.51GB原包。
- 最新schedule查询返回的小时历史源仍为40248370100，早于PR82/83合并；在该有界查询内未取得合并后新自然源的完整验收。PR81真实条件跳过，PR82 marker跳过，PR83后续自然运行未证实，三者生产项均NOT_VERIFIED。
- [OSS run40261473117](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/40261473117)：retain job121376064367成功，但原日志明确“Unchanged inventory; no fresh readback claimed”，不能据此关闭新包固定版本回读。forecast-retain job121376064371于03:02:12 UTC仍BLOCKED_PRE_TARGET_WORM、receipts=[]，三条正式错过截止保留；canary另列。故不把重复历史截止告警变成新修复，也不把retain绿色当整体WORM通过。
- 最新20条运行元数据中终态审计40261916436、40261626948仍failure；仅元数据，本轮未对这两次重新归因，不凭名称认定与旧根因相同。workflow_dispatch测试绿不计自然生产样本；本轮没有调用dispatch。
- CURRENT与Registry路由核对完成；旧冻结文件和current JSON仅读取。本分支仅追加本文件原文末尾；不修改config/system/data/research/代码/workflow、CURRENT/Sheet，原文保留。合并生效、生产稳定、Sheet/GAS自动采集、云端独立恢复、中国实网、前瞻优势及成本后盈利均未在本轮完成验收。

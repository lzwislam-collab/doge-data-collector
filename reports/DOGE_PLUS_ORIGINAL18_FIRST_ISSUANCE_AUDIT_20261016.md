# DOGE Predict PLUS｜original18 首次发行与评分证据核验（2026-10-16）

> 性质：补充只读审计。此文件不是新预测，不改变原始事件或评分，不升级模型。时区统一给出 UTC 和 JST（UTC+09:00）；历史旧审计按当时快照保留。
>
> 裁决：original18 首次 1H/24H 事前 GitHub 公开见证链 **PASS（限公开来源证据）**；首次 1H 目标报价及独立目标见证 **PASS（限此事件）**；首次 24H 目标评分 **WAIT**；逐笔外部不可改写存证 **NOT_VERIFIED**；自动化跨系统及预测优势、净收益验收 **NOT_VERIFIED/FAIL_TO_ESTABLISH_EDGE**。

## 1. 原始发行（禁止事后改写）

- 冻结协议：`DOGE_LEARNED_SHADOW_V1_20261016`；冻结模型包 SHA256：`c93004aadc669c074f51a4cb6411769c3d84d388b31773d1251823b1d45ccad3`。
- 计划槽：2026-10-16 00:00:00 UTC = 09:00:00 JST。
- 1H 事件：`1792108800_1`；24H 事件：`1792108800_24`。两条原始发行均为 2026-10-16 00:41:06.846667 UTC（09:41:06.846667 JST），在原 45 分钟发行窗口内。
- 参考价：DOGE-USDT **0.08127 USDT**。1H full 原始上涨概率 **0.514453044881**；24H full 原始上涨概率 **0.506111712277**。两者均判断 UP；均 **未经未来真实样本校准**。
- 1H目标：2026-10-16 01:41:06.846667 UTC（10:41:06.846667 JST）；24H目标：2026-10-17 00:41:06.846667 UTC（09:41:06.846667 JST）。
- 两条预测在 GitHub 独立见证工作流 `38830881793` 的 `head_sha=8131dd4f471fe297ada57e992c2daf9b60f091b8` 事前公开存在。该工作流创建于 00:41:11 UTC，成功完成于 00:42:26 UTC，均早于上述目标时间。
- 原始事件内的 `DRAFT_UNWITNESSED`、`public_eligibility_verified=false` 是**发行当时未完成见证**的不可改写初始状态；稍后 `witness_bindings` 的成功证明另行追加，不能以改写旧事件字段来伪装其出生时就完成见证。
- 原始文件：
  - [1H](../data/learned_shadow_v1/events/1792108800_1.json)
  - [24H](../data/learned_shadow_v1/events/1792108800_24.json)
  - [1H事前见证](../data/learned_shadow_v1/witness_bindings/1792108800_1_prediction.json)
  - [24H事前见证](../data/learned_shadow_v1/witness_bindings/1792108800_24_prediction.json)

## 2. 首次1H真实到期评分

- [原始评分](../data/learned_shadow_v1/scores/1792108800_1.json)：目标之后在 2026-10-16 01:50:19.581353 UTC（10:50:19.581353 JST）记录观察；原始 ticker 价格 0.08128 USDT。
- 该 ticker 的时间戳 `1792115418471`（毫秒 UTC）比目标晚，位于目标后25分钟窗口内；目标见证工作流 `38837263345` 创建于 01:50:23 UTC，完成于 01:51:18 UTC。见[独立目标见证](../data/learned_shadow_v1/witness_bindings/1792108800_1_target.json)。
- 真实方向 `y=1`（上涨）；冻结 full 候选方向 UP，**本事件方向命中1/1**。这是单个非独立总体业绩结论；不宣称70%或统计显著优势。
- 名义成本纸面净收益 `cash_net=-0.003118153109`（约 **-0.311815%**）；双倍成本压力净收益 `cash_stress_net=-0.006104322382`（约 **-0.610432%**）。这是模型中该事件的成本记账差额，**不是实盘盈亏，也不是R2正式样本**。
- 方向对，但扣成本收益为负；报告必须同时如实显示，禁止以单次正确冒充盈利。

## 3. 统一只读成绩表与版本隔离

- [plus_scorecard.py](../plus_scorecard.py) 已实现原冻结计划槽完整分母；[plus_shadow.py](../plus_shadow.py) 将 original18 的1H/24H和R2的1H分轨；[docs/finite_remediation_20261016.md](../docs/finite_remediation_20261016.md) 规定工程封口和固定验收终点。
- 报告展示必须区别最新事件单次结果和完整资格成绩；遗漏发行、目标缺失、公开见证缺失保留，不隐匿；缺成本写 null。
- original18、R2、旧 V4.2、Google Sheet 历史模型、canary及研究轨道不混合统计；1H和24H不可混合。
- [当前发布选择事件](../system/prediction_releases_v1/events/00000000.json) 仍为 `FROZEN_REFERENCE` 初始化；首次 original18 前瞻影子发行**不等于**生产模型晋升或独立统计优势通过。
- 同步登记 Google Sheet `Report PLUS Integration Audit`，但并不替代 GitHub 原始事件账本。

## 4. 未完成事项与固定核验

- original18 首次24H真实目标：2026-10-17 **09:41:06 JST**；目标捕获至冻结规定窗口期限。10月17日12:00 JST的既有日报节点审查到期评分。
- R2 正式前瞻窗口：2026-10-20 09:00 JST起；截止当日12:00 JST对本轮工程状态按 PASS/FAIL/NOT_VERIFIED结案，不能延期、补造漏发或调整统计门槛。
- original18统计冻结review日期：2027-06-01/12-01 09:00 JST；R2冻结review日期：2027-01-18 09:25及2027-02-11 17:00 JST。样本量、覆盖率、概率校准、原始报价及净收益必须沿用冻结政策。
- 本轮核验未确认逐次预测的外部不可改写事前留存。发行回执中 `external_immutable_retention_verified=false`；不能把事后公开GitHub或每日WORM库存自动等同逐笔事前锁定。
- Google Sheets 1H 最新时间、时间戳、OHLC、完整性及定时采集运行闭环需**独立按现行OKX规范实读**；本文件不报告这部分自动成功。
- 未改冻结模型/协议/既有发行及评分数据/Apps Script触发器/日程/任何交易权限。未创建新预测或定时任务。

## 5. 原始证据路径

- GitHub：`data/learned_shadow_v1/events/1792108800_{1,24}.json`
- 事前见证：`data/learned_shadow_v1/witness_bindings/1792108800_{1,24}_prediction.json`
- 到期证据：`data/learned_shadow_v1/scores/1792108800_1.json` 及 `data/learned_shadow_v1/witness_bindings/1792108800_1_target.json`
- 冻结规则：`config/learned_shadow_v1.json`、`config/phase2_forward_v2.json`
- 冻结完整分母：`plus_scorecard.py`；旧独立版本基准不写入新的 original18 / R2 分母。

**最终判定：首次公开发行证据范围通过；首个1H评分已有合格原始目标证据，但仅1个样本且名义净收益为负。整体系统仍 REVISE，不对70%准确率和稳定盈利能力作任何通过声明。**

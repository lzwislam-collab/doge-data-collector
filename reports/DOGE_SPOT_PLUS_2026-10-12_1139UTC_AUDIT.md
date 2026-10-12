# DOGE Plus｜本版直接审计

**裁决：报告披露与工程检查PASS；数据覆盖PARTIAL，真实连续运行WAIT，预测与盈利尚未通过；整体REVISE。**
审计UTC：2026-10-12T11:39:07.368872+00:00；被审报告：[DOGE_SPOT_PLUS_2026-10-12_1139UTC_FORECAST.md](DOGE_SPOT_PLUS_2026-10-12_1139UTC_FORECAST.md)。
仓库输入固定于 `72ec006eca2f389ea1f9804fc27fe8e0a5d6918d`；证据：[DOGE_SPOT_PLUS_2026-10-12_1139UTC_EVIDENCE.json](DOGE_SPOT_PLUS_2026-10-12_1139UTC_EVIDENCE.json)；报告SHA256：`1b1c1357e899e7eba9cdc89adf9309c4c42ee48607aa37f45c7e0952b273a41b`。
审计在同一会话中独立复核指标与原始CSV，不声称外部第三方审计。

## 实际执行的验证

- 137项相关测试全部通过；完整已提交预测/评分历史和冻结协议完整性检查通过。
- 日线、小时线直接读CSV重算唯一键、缺失间隔与对齐，不仅沿用已有JSON的PASS。
- 重算Plus历史纸面与成本敏感性；真实报价直接取OKX并检查未来时点、年龄及买卖价顺序。
- 正式成绩沿用各自实际截止的V2记录，没有将本次现价倒灌到期成绩。

|复核|结果|证据说明|
|---|---|---|
|来源健康复核实际路径|PASS|ok_with_coverage_limits|
|DOGE-USDT 日线键/时间间隔|PASS|{'rows': 2476, 'missing': 0, 'duplicates': 0, 'last_day_utc': '2026-10-11'}|
|BTC-USDT 日线键/时间间隔|PASS|{'rows': 3196, 'missing': 0, 'duplicates': 0, 'last_day_utc': '2026-10-11'}|
|ETH-USDT 日线键/时间间隔|PASS|{'rows': 3196, 'missing': 0, 'duplicates': 0, 'last_day_utc': '2026-10-11'}|
|BTC-USDT 对齐DOGE|PASS|未插值补齐|
|ETH-USDT 对齐DOGE|PASS|未插值补齐|
|DOGE-USDT 小时历史|PASS|{'rows': 19333, 'duplicates': 0, 'missing_between_endpoints': 0}|
|BTC-USDT 小时历史|PASS|{'rows': 19333, 'duplicates': 0, 'missing_between_endpoints': 0}|
|ETH-USDT 小时历史|PASS|{'rows': 19333, 'duplicates': 0, 'missing_between_endpoints': 0}|
|预测ID唯一|PASS|58条；正式与旧诊断不合并|
|预测UTC因果顺序|PASS|发行/行情/目标顺序；目标未来不等于评分|
|每日账本哈希与幂等|PASS|回读既有账本，未新增或改写行|
|正式V2覆盖率算术|PASS|零成熟样本为null；不删除缺失分母|
|上游审计不来自未来|PASS|各自截止时间分别列示，未冒充同时刷新|
|未授权预测/利润/交易|PASS|事实判断保持未验证|
|报告回读及边界|PASS|报告保持REVISE，明确历史与前瞻分离|
|证据文件回读|PASS|来源哈希与原始快照可回读|

## 不通过项与残留风险

- P0 1h正式发行仍4/9=44.44%；缺失5，不能剔除或补造。3/3到期报价与2条非重叠评分不足完整7天。30天99%证据尚缺。
- 24h正式成熟成绩0；P1两版尚未生效/成熟，P2完成区间0；100/30天与300/90天跨行情要求均未满足。
- 五模块PIT历史、实体标签、全球强平和宏观因果未完成，不认定五大不足全部修复。
- 本版短期DOWN来自既有冻结登记，并非Plus新模型独立发行；不能附加未经验证的胜率、仓位或盈利承诺。
- 公开后单次新盘口不能补证历史可成交价；成本仍假设，P2仅检查点回撤；不能声称可执行真实利润。
- 仓库审计中部分旧Plus说明仍提“先冻结纸面规则”，当前P2已冻结但未生效；本版以P2实际协议为准。旧说明的滞后不等于P2盈利已通过。
- 来源健康文件与小时/P0/每日评分截止不同：本版明确各自时间，不冒称整条链同一时刻完成更新。
- GitHub管理员可改写历史或删除归档；未配置外部不可改写存证。Sheets触发器/执行/回读本轮未验证。

## 准入判定

工程PASS仅适用于本次检查；P0连续可靠性WAIT、P1统计优势BLOCKED_NO_VALIDATED_EDGE、P2经济优势NOT_PROVEN、真实交易NO_TRADE。继续按单一权威路线图积累未来证据，禁止自动晋级。
冻结协议、数据源账本、定投规则和定时工作流均未改动。

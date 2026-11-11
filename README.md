# DOGE data collector

<a id="current-reports"></a>
## 当前报告导航（唯一仓库入口）

正式前瞻结果请从[正式前瞻验证 V2 正文](reports/DOGE_FORWARD_VALIDATION_V2.md)及其[对应 JSON](data/stage5/forward_validation_v2/latest.json)读取。下表是持续导航和字段说明，不把某一次检查时间写成永久“最新”；`latest` 文件名、文件修改时间或其他来源的成功均不能代替本来源的正文、状态和截止字段回读。

|报告轨道与来源|版本与身份|实际截止字段及含义|
|---|---|---|
|正式前瞻 V2：[正文](reports/DOGE_FORWARD_VALIDATION_V2.md) / [JSON](data/stage5/forward_validation_v2/latest.json)|JSON `schema_version=2`；`protocol_id=DOGE_FORWARD_RAW_QUOTE_BOUND_V2_20261012_0200`；`effective_from_utc=2026-10-12T02:00:00+00:00`。版本身份仍须与当次 JSON 核对。|正文“截止 UTC”对应本 JSON 的 `audited_at_utc`，它是本轨审计截止，并非每个行情、预测或评分都刷新到该时刻。`effective_from_utc` 是协议生效时间，不是数据截止。JSON 未给出独立的全来源最新报价截止，不以 V4.2 时间补填。|
|V4.2 采集 / 探索诊断：[forward_latest.json](data/direction_v42/forward_latest.json)|JSON `schema_version=1`；本文件未提供 `protocol_id` / `effective_from_utc`，不套用 V2 身份。`official_score_report` 指向 V2，不把本文件变成正式成绩。|`generated_utc` 为诊断生成时间；`quote_at_utc` 为本次参考报价时间；`feature_close_at_utc` 为特征 K 线收盘时间。三者分列核对，并结合本文件的 `reference_quote_age_sec` / `feature_lag_min`；不得用任一时点刷新 V2 截止。|

正式 V2 是自 E（`effective_from_utc`）起的累计口径；P0-2 的滚动 168h / 7 日运行门禁单独读取[现有 P0 运行源](data/p0_operations/latest.json)中的 `audited_at_utc`、`input_head`、`rolling_7d` 和 `p0_7d_accepted`。两者的时间窗口与验收对象不同，累计 V2 不能替代滚动门禁，任一来源的 `status=ok` 都不等于 `p0_7d_accepted=true`；P0 来源缺失、字段缺失或过期仍按其既有规则降级，不从 V2 或 V4.2 补填。

V2 的 `status=ok` 只说明该次审计程序运行状态；覆盖率须另读 `horizons.*.coverage_state`、`formal_status` 及发行覆盖，预测能力须另读 `accuracy_70pct_validated` / `edge_validated`，收益验收须另读 `economic_edge_validated` 及纸面区间完整性。运行成功不等于覆盖率、70%预测能力、统计晋升或收益验收通过。V4.2 的 `status=ok` 也仅属于该来源，探索成绩不能替代正式 V2。

各轨独立保留版本、固定分母、缺失和截止：original18、R2、正式 V2、V4.2 与旧 Plus 成绩不合并、不互相补分。正文或 JSON 缺失、必要字段缺失、时间无效/未来/不匹配、`blocked`、刷新失败或来源超过各自既有协议阈值时，明确降级为 `NOT_VERIFIED` / 来源原有阻断或失败状态，并展示仍可核实的旧截止；阈值本身无法核对时不声称新鲜。沿用各来源原阈值，不另设统一宽限，不以旧 DAILY 顶替新结果，也不借另一轨的新时间掩盖过期。

### 历史 / 兼容报告

- [旧 Plus DAILY](reports/DOGE_SPOT_PLUS_DAILY.md)：兼容历史正文，结合其自己的 [evolution_latest.json](data/stage5/spot_plus/evolution_latest.json) 核对，分别保留正文与 JSON 截止。
- [旧 V2 DAILY](reports/DOGE_FORWARD_VALIDATION_V2_DAILY.md)：兼容历史正文，结合其自己的 [daily_latest.json](data/stage5/forward_validation_v2/daily_latest.json) 核对，分别保留正文与 JSON 截止。

两个 DAILY 都不是当前正式主入口；新 JSON 的时间不自动刷新旧正文。旧审计、报告会话和 Library 只保留原时点及治理身份，不据其历史 PASS 声称当前整体验收成功。

### 治理引用与读取边界

沿用原已登记的 Library CURRENT，不新建并行 CURRENT。**Library 正文：NOT_VERIFIED（未读取确认）**；保留该身份不表示已实时回读或更新其正文。本导航负责仓库报告分流，不替代治理正文核验，也不修改 DOGE 定投规则；定投以“DOGE 定投｜5.4 FINAL”为准。

---

Automated **hourly** market snapshots from OKX's public spot API for BTC-USDT, ETH-USDT, DOGE-USDT. No API key is required.

## Outputs

- `data/latest.json`: last **successfully validated** spot snapshot (not a live feed).
- `data/hourly.csv`: one row per confirmed 1-hour candle per market, deduplicated by symbol + candle start.
- GitHub Actions log: execution and error visibility.

## Run

GitHub → Actions → Collect OKX public market data → Run workflow. A scheduled run is also requested at minute 7 of every UTC hour (actual start may be delayed or skipped by GitHub). Actions must be enabled and token must have **Contents: read and write** permission. If the first run fails on `git push`, check Settings → Actions → General → Workflow permissions.

No GitHub-hosted cron workflow is a guarantee of 24/7 real-time data collection. A report must check `collected_at_ms` and the individual OKX market timestamps before presenting figures as current; if stale, fail closed.

## Coverage and limits

Phase 1 collects **spot prices, bid/ask, 24h volume, and confirmed 1H OHLCV**. It does **not** collect funding, open interest, liquidations, ETF flows, chain activity, macro series, or news; those require additional validated data sources and independent monitors.

This is a **research input** only, not a trading or forecasting system. Do not claim that GitHub storage automatically gives ChatGPT direct access to private account data.

## Rolling historical PoR UTXO audit and quality alerts

The existing `doge_rolling_utxo.yml` follows successful scheduled confirmed-block
whale scans created during 03:00–03:59 UTC. It adds no schedule. Each run attempts
up to 240 pending transactions within 390 seconds, preserving the 0.68-second
request spacing. Individual requests have a 45-second retry budget. Successful
rows survive individual failures, and a persisted `last_attempted_txid` cursor
rotates attempts across runs so persistent failures cannot starve later IDs.
Reconciled rows are not fetched again. The source CSV and immutable PoR address
hash must validate before any historical CSV is changed.

`events_pending` retains its meaning of source IDs without an audit row;
`events_pending_reconciliation` also includes existing unbalanced rows. A sample
is complete only when every source ID is reconciled. Individual API failures or
unbalanced rows fail the audit after saving valid progress and diagnostics.
The old push bootstrap trigger was removed so code pushes do not run UTXO
requests or replace scheduled-run provenance. Existing manual dispatch remains
available but is not required for rollout.

The existing scheduled-run evidence workflow also checks UTXO failures and
persists quality metrics under `data/stage1/schedule_evidence.json` →
`stage1_rolling_utxo.data_quality`. Pull requests run offline tests only.
The monitor compares both CSVs at the current source cutoff and reports coverage,
reconciled coverage, backlog, duplicates, orphans, missing fields/files, invalid
amounts, unreconciled rows, and mixed/self-return economic ambiguity separately.
Absent data is not reported as zero backlog or complete coverage.

Alerts use Actions error annotations, the existing job summary, and workflow
failure (GitHub notifications follow the account's Actions notification settings).
Invalid data, unreconciled amounts, unverified scheduled linkage, backlog above
240, pending age above 30 hours, UTXO snapshot age above 30 hours, or whale snapshot
age above 2 hours fail the check. Ordinary backlog within the daily window is
informational. The 30-hour thresholds allow the next 03 UTC window plus delay;
they are operational limits, not trading rules. No separate issue/email service
or timer is created.

Mixed inputs, change, and self-return remain economically ambiguous even if
amounts balance. Historical PoR address-set deltas are not verified exchange
netflow, and these audits do not modify or supply DOGE investment rules.

Offline regression checks:

```sh
python -m unittest -v test_doge_rolling_utxo test_stage1_quality test_stage1_rolling_integrity test_doge_schedule_evidence
```

## 历史①②③综合审计（2026-10-13 JST）

[完整审计与复现证据](reports/DOGE_SYSTEM_123_COMPREHENSIVE_AUDIT_20261013.md)已登记至[当前系统审计](system/current_comprehensive_audit.json)。③两项实现缺陷已在独立V3.1修复并通过线上验证，详见[修复验收](reports/DOGE_SYSTEM_123_V3_1_REPAIR_20261013.md)。当前③指针为[注册V3.1](system/current_p2_execution_shadow_v3_1.json)，前瞻盈利仍WAIT；①②统计有效性及晋级仍未验收。完整510项测试通过不等于准确率或盈利通过。失败V3、冻结版本、历史漏发与原始证据保留；定投及真实交易权限不变。

# DOGE data collector

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

## 最新①②③综合审计（2026-10-13 JST）

[完整审计与复现证据](reports/DOGE_SYSTEM_123_COMPREHENSIVE_AUDIT_20261013.md)已登记至[当前系统审计](system/current_comprehensive_audit.json)。①②基础前瞻机制通过，有效性仍WAIT；③覆盖率分母和尾部采样缺口两项实现问题使盈利验收BLOCKED。441项测试通过不代表新发现的问题已经修复。原始采集继续，冻结版本、历史、定投及真实交易权限不变。

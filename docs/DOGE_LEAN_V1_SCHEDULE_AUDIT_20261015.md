# DOGE Predict PLUS Lean-v1.0 — schedule and feature-value audit (2026-10-15 JST)

## Scope, decision, non-negotiable evidence
* Goal: reduce unproven auxiliary data collection without modifying frozen production forecast logic, scoring, historical results, or independent evidence.
* Audit scope: 71 YAML workflow files and one Python file in `.github/workflows/`, plus the three original ChatGPT automations. **GitHub "workflow files" does not equal currently active schedules.**
* In repository main prior to this proposed change, 27 YAML workflows carried at least one `on.schedule.cron`.
* This branch removes `on.schedule` from **23 auxiliary workflows** but retains their files, `workflow_dispatch`, existing `push`, `pull_request`, and `workflow_run` dependencies. It is reversible and leaves their data/audit history untouched.
* Only four existing scheduled jobs remain: `collect.yml` (hourly OKX market data), `doge_history.yml` (confirmed daily history and registered backtests), `okx_hourly_extreme_history.yml` (time-sensitive genuine 1H/24H frozen forecasts, raw target quote and P0/P1/P2 evidence), and `prediction_gate.yml` (daily model/data gate).
* Important: workflow_run and path-scoped push events can still generate automatic dependent jobs; a schedule cut is **not** equivalent to disabling a workflow or claiming all automation has stopped. `oss_inventory_retention.yml` was specifically kept event-triggered to preserve independent retention experiments.
* Production shell `okx_hourly_extreme_history.yml` was not edited or rescheduled: its existing 10-minute quote-grace design remains.
* No Google Sheet contents, Google Apps Script triggers, DOGE investment execution rules, data records, forecast protocols, or real orders were changed as part of this GitHub branch.

## 23 automated schedules paused (manual invocations and evidence remain)
### High-frequency, incomplete or hard-to-interpret source sampling
`binance_liquidations.yml`, `bybit_liquidations.yml`, `liquidations.yml`, `okx_doge_balances.yml`, `whale_blocks.yml`, `whale.yml`, `liquidity.yml`.
Reason: incomplete sampling windows, exchange-entity ambiguity or sparse order book paths; the official model has no independently verified directional increment from these sources.

### Low-frequency background feeds without approved intraday incremental prediction
`onchain.yml`, `onchain_metrics.yml`, `etf_flows.yml`, `macro.yml`, `policy_events.yml`, `regulatory_events.yml`, `sec_events.yml`.
Reason: slow update, publication/revision PIT issues, unproved DOGE 1H/24H edge, and maintenance burden. Existing historical files and manual runs remain.

### Legacy/unnecessary independent periodic analysis and duplicate monitoring
`doge_multifactor.yml`, `doge_validation.yml`, `doge_stat_audit.yml`, `v42_direction_baseline.yml`, `v42_microprobe.yml`, `doge_stage1_audit.yml`, `doge_schedule_evidence.yml`, `event_price_audit.yml`, `feed_freshness_audit.yml`.
Reason: core P0/P1 and daily gating now already capture issuance, verified due quotes, data freshness and frozen-baseline comparison; separate automatic scans have not shown material incremental value. Manual and event-driven behavior remains to avoid breaking dependencies.

## CI repair in this branch
`test_forward_validation.yml` previously failed at `test_real_sdk_constructs_short_lived_sts_client_without_network` with `ModuleNotFoundError: aliyunsdkcore`. 
`oss_inventory_retention.yml` also ran the same test without the provider SDK installed.
The two workflows now pin `oss2==2.19.1` and `aliyun-python-sdk-core==2.16.1`. Installation/build/runtime must be verified in actual GitHub Actions; editing configuration alone is not PASS.

## Feature-value policy (not predictive evidence)
1. Source veracity/coverage: immutable IDs, source clock, missing/duplicates, official OKX historical source where needed.
2. Causality: `available_at <= forecast_issued_at`; do not treat revised news, trade-day ETF values, chain timestamps or reconstructed backfills as prior public knowledge.
3. Incremental value: fixed same-event ablation against frozen DOGE and robust BTC/majority/unconditional direction baselines, with multiplicity and autocorrelation considered.
4. Economic value: lower-bound conservative spot spread/fees/slippage and missing-path denominator, compare against cash and DOGE buy-and-hold. No P2 acceptance from NO_TRADE-only paper paths.
No factor enters the frozen model until a separately registered, pre-target, independent evaluation supports its gain. Limit the active challenger research queue to one feature family at a time; DOGE/BTC return/volatility first, then OI/funding only if deemed worth keeping after sufficient clean PIT.

## Acceptance and rollback
* Before merge: exact 23-file schedule diff plus two isolated CI dependency edits, verify all 4 protected cron configurations are unchanged and no protocol or data file was edited. Open PR and check its status.
* After merge: verify on main by exact SHA and readbacks, confirm subsequent `schedule` runs of all 4 protected workflows actually fire/commit/read back as before. Do not claim production success from a PR alone.
* If primary forecast issues, scoring, or scheduled collection regress, restore the original YAML schedules from the merge base by PR, never silently edit frozen prediction rows.
* Operational P0 remains on its own time window and missing historically issued/quoted events cannot be fabricated.
* `LATEST_CLOSED_BAR_MISSING` remains a tracked genuine collector-quality fault until independently resolved in scheduled execution; pausing optional sources does not cure it.
* Overall prediction advantage and 70% directional accuracy are **NOT PROVEN**, as is net economic edge.

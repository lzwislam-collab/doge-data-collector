# Paired forecast and selective paper decision V1

Goal: Preserve full daily/hourly forecasting while precommitting a selective paper action; compare frozen production and a new challenger at identical issue context and outcome.

Execution: inline, with one independent final code audit. User authorized implementation, audit and system registration. No repeated approval flow, no orders or DCA changes.

1. Implement/test causal fixed logistic + separate Platt calibration + untouched historical diagnostic validation. Freeze confidence, liquidity, volatility and conservative cost-edge conditions. Historical checks are only shadow selection criteria, never proof of forward calibration or profit.
2. Prepare before calling existing production issue. Verify unchanged input hash and identical production feature calculations. Bind paired challenger, full original forecast, selection reason and raw live depth to original quote, target and issue ID. Never attach to old forecasts or reconstruct missing pairs. Append hash-linked records in a new namespace; detect removals against full reachable Git history.
3. Publish paired records with original ISSUE data in the same commit and read back both. Audit only original V2-eligible forecasts and same qualified outcomes; keep missing/difficult/nontrading samples in full accuracy denominator. Report all/independent samples, coverage, paired McNemar with fixed five-comparison adjustment, ex ante regimes and separate paper decision subset.
4. Add audited system registration with code/protocol hashes and future activation (2026-10-12 16:00 UTC); preserve old system versions. Integrate existing hourly workflow only; failures archive before job fails. No new cron.
5. Full regression, independent adversarial review, production run/log/data readback. Register only reviewed exact bytes; forward effectiveness WAIT until qualified samples mature.

Files: paired_selection_v1.py, paired_forward_cohort.py, paired_cohort_audit.py, tests, protocol, independent system pointer/version and engineering audit; existing hourly workflow only.

Review focus: delayed clocks, train/calibration/test overlap, missing records, changed source/old-model mismatch, public witness after outcome, dropped hash-chain tail, skewed NO_TRADE denominator, inability to publish model before target.

Ruling: use PAPER_LONG/NO_TRADE as precommitted *shadow* decisions; actual authorized action remains NO_TRADE. Only long spot; downside forecasts abstain. Historical calibration/edge is a price-label proxy, not validated forward profitability. No newly sourced leverage/sentiment features may be fabricated.

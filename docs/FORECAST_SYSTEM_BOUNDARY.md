# DOGE forecasting system boundary (effective 2026-10-12)

This repository's forecasting work has exactly three responsibilities:

- **P0 – Data reliability:** collection, point-in-time timestamps, deduplication, missed-slot denominators, immutable-as-practical evidence, and prospective quote coverage.
- **P1 – Directional validation:** frozen 1h / 24h forecasts, nonoverlapping forward samples, independent baselines, confidence intervals, regime checks and leak-free challenger evaluation.
- **P2 – Standalone spot economics:** prospective DOGE spot paper strategy, realistic spread/fees/slippage, cash and buy-and-hold comparisons, drawdown and capacity checks.

**Out of scope:** any dollar-cost averaging (DCA) strategy, DCA-specific version, budgets, execution cadence, position sizing, cost basis, portfolio reporting, or updates to any separate DCA project's rules. No DCA data may enter predictors, evaluation metrics, or paper trading parameters. The spot paper strategy is an entirely independent research portfolio.

## Claims / gates

A green GitHub Actions run proves only that the job completed, not that all scheduled samples were issued or that target quotes were captured. Inspect scheduled runs, the original forecast publication time, source quote timestamps, final committed records, and missing denominators. Never retroactively fill an absent prediction as a prospective issuance. Report `N/A` for accuracy, quote coverage and PnL without matured, scored intervals.

P0 acceptance: prospective rolling 7-day forecast issuance **>=90%**, reported separately from matured target-quote coverage **>=90%** and with sample counts and provenance. P1 70% is a *research target, not a capability claim*: report number of nonoverlapping, preregistered trials, model-to-baseline paired results and confidence intervals. P2: after explicit realistic costs, compare spot strategy versus buy-and-hold and cash, including maximum drawdown. No actual order submission by this research pipeline.

## Follow-up code cleanup

Remove DCA-specific conditions and text from `spot_plus_daily_evolution.py` and related forecasting-only reports in a **separate tested change**, preserving independent validation gates. The old presence of a `dca_rules_changed == False` field is historical coupling, **not** an invitation to modify any DCA strategy or files.

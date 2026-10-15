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

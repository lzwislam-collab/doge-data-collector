# DOGE Predict PLUS — News Event Research V2 (isolated prototype)

**Status: RESEARCH_ONLY. No scheduled task, collector, production model, original18/R2, or historical score is changed.**

## Verified diagnostic, fixed snapshot
Read-only scan of all 152 distinct entries in `data/feature_ablation_v1/contexts.jsonl` as available at 2026-10-16 05:10 UTC:
- `NEWS_TONE`: 0/152 changes to original UP/DOWN direction; 78 zero, 74 positive and 0 negative feature values; mean absolute probability movement approx. 0.233 percentage points.
- `BTC_SENTIMENT`: 0/152 changes; all positive, but this is a BTC proxy, not DOGE public sentiment.
- Thus prior `NEWS_TONE` testing is **not** a reliable test of causal, event-driven DOGE news alpha.

## Current prototype
`news_event_research_v2.py` is **read-only** and uses only existing `data/enhancement_sources_v1/observations.jsonl` as the source. No API keys, network fetches, new billing, new GitHub workflows, timers or modifications to prior events. It:
1. Rejects rows whose first observed, publisher or usable timestamps are after the caller's as-of; rejects inconsistent, naive or missing clocks.
2. Preserves source identity and raw SHA; treats later revisions as unavailable to earlier prediction time; deduplicates matching canonical links without rewriting the evidence ledger.
3. Flags macro-policy, regulation, DOGE-specific, security/infrastructure, and geopolitical headline events; distinguishes explicitly positive, negative, mixed and unspecified wording.
4. Emits `market_direction=UNKNOWN`, `macro_surprise=UNKNOWN`, and `coverage_sufficient_for_directional_claim=false`; these are event classifications, **not** calibrated price forecasts or reliable source-wide coverage.
5. Includes missing/stale RSS-source flags. This cannot guarantee completeness of social platforms, breaking news, DOGE tweets or other channels.

## Local tests
`python -m unittest -q test_news_event_research_v2.py`: 14 synthetic, isolated tests PASS before upload. Tests cover negative wording, mixed wording, timestamp leakage, delayed first-seen, missing/stale source, same-link deduplication, later revisions and immutability. Re-run GitHub branch tests and validate actual historical ledger separately before production claims.

Read-only usage (repo root):

```powershell
python -m unittest -q test_news_event_research_v2.py
python news_event_research_v2.py --asof 2026-10-16T05:00:00Z
```

## The next gate — NOT implemented or accepted
1. Independently verify source coverage, news event relevance and publication-first-observed clock for all critical classes; unverified article summaries and macro surprise remain UNKNOWN.
2. Study genuinely pre-registered event interaction with BTC 1H/24H, DOGE/BTC relative returns, point-in-time liquidity and leverage, with non-overlapping outcomes.
3. Compare same-event published baseline vs challenger: accuracy, Brier, extreme-risk precision/recall, cash/hold and nominal/double-cost simulated net returns, drawdown and missing source denominators.
4. Require at least 100 independent qualified nonoverlap events spanning 30 days for first research review, and 300 over 90 days across states for stronger evidence. Use multiple-testing controls, keep true missed events; any eventual model promotion requires independent approval and the original frozen gate.

## Reusable resources
FinBERT and event-study / purged walk-forward methodology are available as community examples, but installing a large ML inference dependency or changing the old frozen news weight does not establish edge. This prototype deliberately retains stdlib-only deterministic triage. No paid providers or new monitoring task.

**No automatic merge, model retraining, market claim or deployment is authorized by this PR.**

## Finite real-source decision — 2026-10-16

Read-only replay of the existing **main-branch** `data/enhancement_sources_v1/observations.jsonl` (9,744 JSONL records), through the last news first-seen timestamp **2026-10-15T07:56:25Z**. News sources were FED_NEWS, ECB_NEWS, CRYPTO_NEWS; deduplicate by `source_id,observation_key`, preserving the earliest first-seen timestamp. This is a latency census, not event-return attribution.

| Measure | Evidence |
|---|---:|
| Raw news-source rows (including revisions) | 102 |
| Unique publisher record keys | 100 |
| First observed within 1h after publication | **3/100 = 3%** |
| First observed within 6h | **30/100 = 30%** |
| First observed within 24h | **59/100 = 59%** |
| First observed more than 24h after publication | **41/100 = 41%** |
| Distinct DOGE/Dogecoin/Elon-specific headlines captured | **0/100** |

The three within-one-hour headlines were: BlackRock crypto ETF assets (+11 minutes); former FTX Europe chief regulatory-exemption story (+34 minutes); and digital-economy power-grid story (+52 minutes). None provided a DOGE-specific near-real-time sentiment signal. A breaking-news *coverage denominator* cannot be inferred from this collected-news-only census: missed events are unknown.

**Decision: NO-GO for news as a 1H directional feature.** It is not responsible to change production weights or deploy paid/social/extra scheduled news collection based on this ledger. Research-only triage can flag what is actually observed on time, while BTC regime, DOGE relative price/volume, and liquidity/leverage remain the forecasting focus under the existing frozen model and same-event challenger rules.

### Exactly one change set; no endless remediation loop
1. **Now delivered:** read-only event classifier, first-visible-time protection, source identity, cross-source duplicates, revision handling; delayed imports explicitly `backfill_not_timely_for_1h`, and current risk-watch only for a classified event first seen ≤1h after publisher time and observed within the past hour. All market directions remain UNKNOWN. Tests have been expanded from 14 to **18 local synthetic checks**.
2. **No-go until separate evidence exists:** Do not merge this draft into production or modify original18/R2, prior feature scores, collector cadences or task counts. No new data sources, model weights, retry loops, model competition projects or repetitive audits authorized by this finding.
3. **Pre-defined reconsideration:** Reconsider a news-feature candidate only if an existing reliable first-seen source establishes sufficient contemporaneous coverage (including known missed items), then a preregistered non-overlap same-event BTC + liquidity control demonstrates incremental directional accuracy, extreme-event precision/recall and cost-adjusted return. Existing independent statistical admission gates and frozen dates remain binding; no backfilled legacy news counted as forward predictions.

**Explicit limits:** The 3% number is capture delay among recorded headlines, not market-news recall. Source poll success cannot be inferred from last observed *new article*; old but accurate news is still valid historical content, simply not an immediate one-hour catalyst. No actual model-performance improvement has been shown or claimed.

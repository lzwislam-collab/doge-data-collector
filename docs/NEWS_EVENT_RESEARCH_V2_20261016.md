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
| First observed within 6h | **27/100 = 27%** |
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


## 2026-10-16 — Actual retained-news audit and stopping decision

**Immutable evidence reference:** \`data/enhancement_sources_v1/observations.jsonl\`, main Git blob \`9284561e48159d4f721f4173c6095ead4514fc4c\` (9,744 ledger rows). Audit as-of **2026-10-16T05:10:24.396Z** (14:10:24 JST). Read-only inspected all 9,744 valid JSON rows through the authenticated GitHub repository API, filtered to three already authorized RSS news sources; no data collector was invoked.

- RSS news rows: **102**, with **100** unique (source, provider-key) records after preserving revision timing; **100** unique canonical event URLs in this snapshot. Other rows (DXY, FRED dollar index, BTC sentiment) are *not* treated as news headlines.
- Original first-seen lag versus stated publisher time for the 100 deduplicated articles: **3** within 1h, **24** between 1–6h, **32** between 6–24h, **41** older than 24h. Publisher time alone never confers historical availability.
- Latest archived headline observed by source: FED **2026-10-14T02:41:23Z** (~50.48h before as-of), ECB **2026-10-14T14:42:42Z** (~38.46h), CRYPTO_NEWS **2026-10-15T07:56:25Z** (~21.23h). Absence of newer headlines does **not** prove fetch job failed; it does mean current one-hour-news coverage remains unproven.
- Exactly **four** unique articles were first captured within the last 24h; all four had publication-to-first-seen lags between **1.64 and 3.98 hours**. A local Python replay of these four actual archived observations yielded one \`LEVERAGE_STRESS\` classification (liquidation headline), one \`MACRO_POLICY\` context, two unclassified, **zero timely one-hour risk flags**, and \`market_direction=UNKNOWN\` for all. An eventual 1H classification is **not** a prediction issued before the market reaction.
- Local synthetic regression suite: \`python -m unittest -q test_news_event_research_v2.py\` **20/20 PASS** after narrow improvements: distinguish liquidation/forced unwinds as leverage context and do not automatically call every Fed/ECB RSS item a major monetary-policy event. This is **local test evidence only**; GitHub branch CI has not run.
- Diagnostic evaluation does not show incremental 1H/24H accuracy, DOGE-specific event coverage, global news completeness, latency, or positive net profitability. **Do not merge this PR as a validated predictor.**

**Finite decision — RESEARCH_NO_GO_NOW.** The diagnostic is sufficient to reject the current claim that this RSS tone data is a reliable time-sensitive forecasting input. Stop adding classifiers, sources, weights or new scheduled jobs merely to improve apparent signal volume. Continue frozen original18/R2 and their independent scorekeeping as before. Revisit event-driven forecasting only after (a) auditable first-seen coverage/latency appropriate to the chosen horizon, (b) registered nonoverlap events and same-event BTC/liquidity comparators, and (c) pre-registered forward uplift that survives costs and missingness. Keep this draft as historical research evidence; no model promotion, trade, schedule or strategy change.

Note: the source Git blob binds the archived input only; real-time endpoint status, future article completeness and actual market impact remain unverified.


## Fixed-as-of real data acceptance (2026-10-16 05:10:24 UTC)

**Reproducible GitHub run:** [38858462401](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38858462401), branch `research/news-event-pit-v2-20261016`, code commit `8251d40f3405dc26a609ca907600d17b90821d1d`. Read back: isolated job `success`, **23/23 unit tests PASS**, actual retained ledger replay PASS. This run has no schedule and does not produce or deploy forecasts.

Frozen sample: 9,744 retained mixed-source observations, including 102 news ledger rows; 100 distinct observed news events after per-provider revision selection and canonical URL deduplication. Publication-to-first-seen lag: **3 within 1 hour, 24 within 1–6 hours, 32 within 6–24 hours, 41 over 24 hours**. Source success receipts from all three RSS providers were **21.233 h old** at the fixed as-of; latest actual new-item ages were 21.233h (crypto), 38.462h (ECB), and 50.484h (Fed). This demonstrates why last new headline age is NOT poll health. 21 historical category-relevant headline mentions, but **0 timely current eligible risk watches**. These measures are of available feed entries, not independent market-impact events or calibrated recall.

The Oct 15 `Nearly $1 billion in crypto bets liquidated as bitcoin, ether and XRP slide` article is now `LEVERAGE_STRESS` and `AFTER_MOVE_HEADLINE`. It is a report of an already observed price move; it is **not** a verified advance signal. The model does not assign an UP/DOWN direction, and publication time is never treated as first availability.

### Stop / go (single scoped experiment)

- **PASS only:** research script compiles and runs on actual frozen evidence, 23 tests pass, original18/R2/previous ablation records remain untouched, zero trading permission, source receipts clearly separate from headlines.
- **NO-GO:** current news feed is not timely enough to substantiate **1H** news-led directional forecasts (3/100 <=1h), has incomplete DOGE social and major-breaking-event coverage, and has no demonstrable incremental predictive advantage or economic return. Do not merge as a production predictor or set automatic model weight.
- **NEXT RESEARCH GATE:** test whether pre-known BTC trend, DOGE/BTC relative movement, and observed leverage/market depth offer incremental **same-event** validation; news remains qualitative risk context pending materially more timely and well-attributed sources. Do not restart feature sprawl or relabel post-move articles as forecasts.
- **No indefinite remediation:** this V2 prototype closes at `RESEARCH_DIAGNOSTIC_PASS / PREDICTIVE_ADMISSION_REJECTED` on this evidence. A new iteration requires genuinely new data/evidence, not repeated nominal changes.

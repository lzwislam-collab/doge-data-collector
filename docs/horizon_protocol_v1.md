# DOGE PLUS bounded horizon repair — 2026-10-22

Scope: 1H, 24H, 7D only. No new factors, DCA changes, orders, production
promotion, automatic retraining, or additional cron / Apps Script triggers.

## Ledger diagnosis

The initial local prefix failure was a Windows checkout conversion, not a
published ledger change. For example, the committed
`data/phase2_forward_v2/events/1792569600_formal.json` contained 154,774 bytes;
the converted checkout contained 160,203 bytes with 5,429 CRLF pairs.
Restoring exact Git blob bytes made `e1_forward.py check` pass for all 10 events
and 10 scores. No existing ledger or registered E1 implementation was edited.
`.gitattributes` now disables conversion for the affected journals and the new
journal. The original append-only guard remains unchanged.

## Six bounded repairs

1. **1H abstention:** existing frozen R2 inference; five historical empirical
   confidence margins are evaluated on a chronological threshold-selection
   partition, after a separate calibration partition and before an untouched
   validation partition. Labels crossing split boundaries are purged. Selection
   requires at least 30 selected examples, at least 10% coverage and a Wilson
   lower bound above 0.5. Maximize that bound, then coverage. The freeze package
   records the curve, split digests, cutoff, candidate margins and exact rule.
   None of the candidates passed: the frozen policy is `FROZEN_NO_SIGNAL`, so
   all formal selective decisions are `NO_TRADE`. This is not evidence of
   profitable abstention or statistical model approval. Future outcomes cannot
   change the frozen rule.
2. **24H:** one daily UTC 00:00 / JST 09:00 slot, target at the next fixed daily
   boundary; 100 independent formal samples required. The existing rolling and
   25-hour legacy research cohorts remain separate and are not imported.
3. **7D:** Monday UTC 00:00 / JST 09:00, target the next Monday. Separate model
   trained with the existing nine features and fixed phase1 optimizer. Historical
   weekly labels are research only; no historical event is issued retrospectively.
4. **Isolation:** model identity, horizon, protocol package, source snapshot and
   derived decisions must replay exactly. 1H uses the existing R2 model; 24H
   uses the existing horizon-specific frozen full model; 7D has its own weights.
   The 24H frozen-model comparator is intentionally the same frozen model, not
   an invented challenger advantage. A 7D frozen production comparator does not
   exist and is N/A.
5. **Calibration:** a separate sigmoid layer requires >=100 independent fit
   and >=100 held-out validation labels, and validation loss gates. All three
   currently remain `UNCALIBRATED`; `probability_up=null`. The existing PLUS
   display also labels uncalibrated output as raw score. Historical proxy
   calibration would still not establish future quote calibration.
6. **Core table:** Issued/Matured/Scored/Missing, issuance and outcome coverage,
   direction/selective accuracy, conservative missing-wrong accuracy, abstention,
   Brier, calibration reliability bins, Always Up/Down, BTC momentum, frozen
   model, net return and drawdown. Unknown economics and uncalibrated Brier are
   N/A, not zero. Raw-score squared error is separately named. Missing slots
   remain in the fixed denominator; both coverage gates must reach 90%.

## Prospective execution and evidence

The new callback listens to the **existing** hourly-history completion event;
it has no schedule stanza. A manual main run can validate registration and
exercise the same tick. Branch/PR runs are offline only. Main-only registration
requires a successful server job with exact frozen code/workflow/package bytes
before the prospective start. Predictions and scores require separate successful
server witnesses at pinned heads within their respective windows. Locators are
retained so old evidence is not lost when it leaves the first page of Actions.

Issuance is restricted to the current 45-minute window; there is no backfill.
The final quote window is 25 minutes. Endpoints are fixed slot+h, rather than
issue+h for daily/weekly slots, so their quote-to-target duration can be up to
45 minutes shorter than the nominal horizon. 1H retains the existing actual
issue+1H target and uses the final possible issue+target deadline for missing
slots. Daily/weekly prior target capture happens before the next entry
capture; if the previous target remains open without a score, issuance waits.
Failures remain missing and never silently become historical predictions.

Economics are isolated spot paper accounting: selective UP holds long; DOWN and
NO_TRADE hold cash. Same-event depth, fees and slippage are required; no actual
fills or short orders are claimed. Records, quote timestamps, source hashes,
decisions, scores, duplicates and grid membership are checked on reread.

`config/horizon_protocol_v1.json` is the immutable published freeze;
`reports/horizon_1h_coverage_accuracy.svg` and `.csv` visualize its historical
candidate curve. Actions artifacts contain runtime JSON and the unified table.
Before target outcomes mature, every performance track remains **WAIT**.

Methods: [independent probability calibration](https://scikit-learn.org/stable/modules/calibration.html)
and [separate decision-threshold tuning](https://scikit-learn.org/stable/modules/classification_threshold.html).
These methods do not imply any DOGE model is profitable or calibrated.

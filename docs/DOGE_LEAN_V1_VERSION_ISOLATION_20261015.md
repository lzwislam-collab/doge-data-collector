# DOGE Predict PLUS Lean-v1.0 — version and prediction provenance isolation

**Governance-only patch, 2026-10-15.** Renaming the ChatGPT project or suspending auxiliary schedules is **not** a new predictive model, a replacement of historical evidence, or a new P0/P1/P2 evaluation window.

## Canonical hierarchy and separate records

* Canonical Library CURRENT: `libfile_a418bf13c4548191ad5765d68d0f3abe`; Google Sheet `Authority Registry` and `Review Split v1.0` delegate narrowly scoped report routing. This document is supplementary, never another CURRENT.
* **Old Sheet legacy:** `Forecast Log`, `Model Learning Log`, previous V3 scores, prior reports and 36-point materials remain read-only where frozen. Old informal/regression results never become new GitHub prospective successes. Any legitimate target amendment must stay on the exact original key under original Sheet policy.
* **V4.2 reference:** `DOGE_V42_DIRECTION_SHADOW_LOCK_20261012`, frozen original forecast and raw-quote scoring in `data/stage5/forward_validation_v2`; its old failures remain in the historical denominator.
* **PLUS original18:** `DOGE_LEARNED_SHADOW_V1_20261016` in `data/learned_shadow_v1`; 1h/24h, separate registered source, actual pre-target publication/score, future-only eligibility. Not an approved replacement of V4.2.
* **PLUS R2:** `DOGE_V2_FROZEN120_20261020_R2` in `data/phase2_forward_v2`; formal 1h only; canary always excluded from formal accuracy/returns. Never pool with original18 or Sheet.
* **Lean-v1.0:** version-control, task and research-priority policy. No new Lean forecast cohort has yet been registered/issued. `data/lean_v1/` is reserved for a separately pre-registered future research cohort; do not create or ingest historical V3/V4.2/original18/R2 observations there by copying or renaming.

## Enforcement

* `config/lean_version_isolation_v1.json` contains exact registries, namespace, source path and effective protocol IDs.
* `lean_version_gate.py` checks protocol files and effective UTC times before admitting the **read-only** PLUS projection; it rejects mixed/duplicate/out-of-protocol report rows, unverified direction/profits, wrong canary horizons and authority drift. The reserved Lean future-batch API rejects unknown/old namespaces, duplicate immutable version keys, post-issuance data or unproven outcomes.
* `plus_shadow.py` calls the gate after obtaining the source-proven report; failure already renders its existing `BLOCKED_INTEGRITY_OR_REMOTE_EVIDENCE` state and clears stale report directions. It **does not change existing forecast production, old scores, frozen models or GitHub target scoring**.
* New PR/push-only `lean_version_isolation.yml` runs regression tests and static gate. It creates **no new schedule**, does not repair/recompute old scores, and does not represent external immutable retention.
* Current scheduled issuance `okx_hourly_extreme_history.yml`, daily `doge_history.yml`, `collect.yml`, and `prediction_gate.yml` remain unchanged. Existing fail-closed frozen integrity protections remain the primary issuer defense.

## Admission policy for anything new

1. Identify protocol, model, cohort, source namespace, model-release eligibility, original `issued_at_utc` and source `available_at_utc`.
2. Reject any post-target information, duplicate `(namespace,protocol_id,model_identity,forecast_id,horizon)` identity, nonverified release, revised history without original publication evidence or source/target mismatch.
3. Run same-event, non-overlapping comparisons versus frozen primary and simple baselines; do not cherry-pick conditions. Maintain missing issuance/score/cost in fixed denominators.
4. Admit a new factor **only** through an independently witnessed prior registration and validated incremental statistical/economic value. Proposed weights/reports cannot silently promote an existing model.
5. A software/test PASS must never be reported as 70% directional forecasting or economic profit PASS.

## What was deliberately not changed

* No new forecast was issued; no historical prediction, outcome, id, model version, source timestamp, metric or row was overwritten; no prediction window reset.
* No Google Apps Script collector, daily `runOkxDaily`, existing trigger, DOGE investing rules, accounts or trading.
* No modification to the independent original18/R2 frozen protocol files or existing scores; no claim that GitHub commits are immutable external WORM storage.

## Independent acceptance and rollback

1. Branch PR check must pass version-isolation unit tests and current read-only projection smoke test.
2. Merge, then inspect actual `main` file contents and CI state. Record exact commit and runner logs.
3. After the next genuine GitHub scheduled production forecast, verify issued and target rows plus server publication from old protected protocol; continue evaluating the unchanged 7d/30d denominators. The static gate cannot substitute for this.
4. For daily/weekly ChatGPT task, independently inspect live task ID/config, then read Google Sheet `Task Status`, Forecast/Model Learning and underlying evidence; first post-change run before calling auto-verified.
5. On regression, rollback **only** the new report gate and scoped policy by new PR. Preserve the original frozen ledgers and current timeline; do not restore or overwrite whole Google Sheets.

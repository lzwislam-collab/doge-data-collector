# Independent review snapshot repair

## Scope and evidence

The old full review 38730988947 ran from commit
`c3ed6bfe15d6c6fecae462c8e8932a5f36724ea8` on 2026-10-15 UTC.
Its successful job reports WAIT_ACTIVATION, scheduled_events=0,
qualified_events=0 and statistical_gate_passed=false. Its original artifact
11971330131 has SHA256
`9af7b3ee25a3fd0d52efce71b16b79adaf755d594505a68177feea71b8295795`.
The formal protocol starts 2026-10-20T00:00:00Z. That commit contains one
canary event and score, and no formal events.

The report's locator still selects this old review. Previously review_source
validated its artifact, then supplied today's events and scores to the
frozen evaluator with the old artifact's at_utc. New formal events therefore
raised future/overlapping forecast even though they did not exist in the
old checkout. This error alone is not evidence of a malformed forecast.

## Repair and checks

- Read event and score membership from the successful review's server head_sha.
- Keep original artifact at_utc and all original rows, including future-dated
  or otherwise invalid rows; never filter rows by claimed timestamp.
- Require original canonical bytes to remain present in today's append ledger.
- Fail closed on unknown commits, missing history, deleted or modified records,
  unsafe tree entries and invalid record identities.
- Continue validating today's complete ledger independently before old replay.
- Keep the existing report's as-of display and current_full_statistics_claimed=false.
- New full reviews use their own checkout and actual clock, including the
  frozen schedule's missing-issuance and missing-score penalties.

Only prediction_release_control.py and new regression tests change behavior.
No frozen assessment/forward code, registration hash, protocol, historical
forecast, score, locator or workflow is changed. No schedules are created.

Regression cases use real temporary Git commits and the frozen evaluator:
old zero-formal review with later events/scores; original nonempty snapshot;
later appends; deleted/changed original bytes; genuine future timestamps;
invalid overlapping target/horizon; missing issuance and score retained in
the denominator; invalid refs; and artifact-loader integration for old and
fresh snapshots. Original source/job/artifact checks remain in force.

## Acceptance boundaries

Unit/CI success proves software checks only. Successful old replay restores
the old as-of result, never current performance. A newly generated assessment
may still report insufficient evidence or missing observations. A branch
Actions run is diagnostic and cannot satisfy the main-only release authority.
No result here authorizes trading or proves profitability, a 70% lower bound,
or completion of the independent audit.

## Verification plan

1. Run targeted regressions, full unittest discovery and original integrity check.
2. Obtain a separate code review and open a scoped PR.
3. Dispatch existing prediction_release_control.yml in prepare mode against
   the old run on the repair branch; retrieve control and evidence artifacts.
4. Dispatch unchanged phase3_independent.yml in full mode on that same branch;
   retrieve the actual assessment, job status, logs and artifact digest.
5. Report software/CI status separately from statistical and release acceptance.

Runtime evidence and run IDs are recorded with the PR and external readback
report after the workflows finish; no prewritten PASS claim is made here.

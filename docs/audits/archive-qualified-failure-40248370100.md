# Run40248370100 reservation context and unsafe final output

The missing source context is explained by the immutable global nonoverlap reservations. It is not a network failure and must not be repaired by selecting an excluded candidate. A separate runtime defect exposed a failed required drain as `fatal=false`; this change makes that result unsafe before any final publication.

## Fixed evidence

Source schedule run [40248370100](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/40248370100), actual checkout `dc1297bc6afade3a644db4fa1d251d5e91284ea8` from job121342440960 log. Run metadata head938cb65765475c13479188f688c995efd0c77a67 is not substituted for checkout. End-of-capture public commit `02fb28f0948ea12417d9d25836314a4160aee816`.

The original frozen P2 engine Git blob is `0e597807124fc89fe28508a7a7b0c1fb57e93c44`. The investigation executed that unchanged engine's `reserve()` on the complete original registry prefix from the P2 effective boundary, before filtering active intervals. Registry Git blob `ebc161708cb0220b8545fd91c2ae9121aa09a3bf` and paired audit blob `2de5abbe6d28d04866a030939414bea9eef4c88c` were reconstructed from the exact connector content and matched before use.

All times below are UTC on 2026-10-26.

| Evidence | Time or result |
|---|---|
| Last reserved 1H event | 1792969200000_1h; issued00:00:41.167026, target01:00:41.167026, capture relevance ends01:25:41.167026 |
| Current overlapping 1H event | 1792972800000_1h; issued01:01:45.704191, before prior reservation end01:25:41.167026; excluded by frozen reserve |
| Last reserved 24H event | 1792886400000_24h; target01:09:29.563822, capture relevance ends01:34:29.563822 |
| Archive service | 01:43:26.258526–01:53:33.944244; both reserved intervals already expired |
| Matching source audit | status ok, evaluated01:46:33.483708, registered_pairs504; current1H not listed in rejected_or_missing |
| Actual archive receipts | 9 cycles; 7 capture rows have empty source_items, 2 smoke cycles; no formally eligible book |
| Terminal outcome | errors0, fatalfalse, DEGRADED, qualified_stop_satisfiedfalse, no qualified hash; child exit1 |
| Existing protections | stop failed; marker publication skipped; OSS refused the new/old binding mismatch |

A fresh audit cannot turn an unreserved original event into a reserved source. The exact code applies global reservations before source verification and again before append relevance. Even a still-relevant post-target book cannot satisfy the stricter `quote < target` shutdown predicate.

The downloaded diagnostic [artifact12623145630](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/40248370100/artifacts/12623145630) SHA256 is `be2a6dec64c09f61515fe73c27643a1c30b3fbca21aae6d053a37e55b1a6c476`, equal to its upload log; outer CRC passed. The 920-row public book ledger Git blob `99a5a6187c3cab08f64c4b7e85ce33f82ba524c2`, complete event hash chain and timestamp order passed. No event or snapshot was rewritten.

## Minimal fix

When required qualification fails, `run()` now records its requirement, sets fataltrue/DEGRADED and a stable fatal reason. `finish()` independently rejects a missing/false required proof before final publication; it cannot append a safe output for that result. Existing cycle publications and raw evidence remain preserved. Failed final receipts remain available to the existing artifact preservation path.

Book-action receipts distinguish audit wait, missing formal event, an empty frozen reservation context, nonqualified reserved context and a qualified formal book. Counts come from the frozen event already read. No frozen predicate, reservation, model, ledger, grace period or source audit rule changes.

[PR83](https://github.com/lzwislam-collab/doge-data-collector/pull/83), discovered merged during this investigation by another operation, separately reserves foreground time for the archive. This PR does not duplicate that timing change or claim its production acceptance. The implementation is rebased onto the existing main before submission.

## Verification scope

Eight new regression cases first failed against the old behavior (seven assertion failures, no test errors), then passed. Combined targeted runtime/audit/drain/archive suite:56 passed, zero failures/errors/skips. Independent reviewer reran all56 and found no P1/P2 blockers; additional publication-error and disk-write-error probes remained unsafe.

Full-suite results, source hashes and final PR readback are recorded in the PR description and the linked Issue5 continuation. An initial sparse Windows checkout lacked research fixtures and had checkout line-ending conversion:1239 tests,2 failures,33 errors,21 skips; that run is retained and is not called a pass.

No production dispatch, rerun, merge, deployment, timer/permission/lock change, OSS upload, historical reconstruction or trade was performed. PR65/66 and81/82 boundaries remain unchanged. Required qualification may still fail honestly during future reservation gaps. Actual source-qualified handoff, marker publication, OSS custody and complete natural production tail remain unverified by this offline fix.

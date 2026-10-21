# E1 cloud anomaly notification

The user requests notifications that continue while the desktop is off, covering missing issuance, missing real scoring, and missing independent public witnesses. Report only new actionable anomalies. No new GitHub/GAS timer, frozen-file modification, historical repair, or acceptance claim is authorized.

## Design

Add an independent observer workflow responding to existing hourly/E1 workflow completion events, including failed upstream runs. Do not change any existing workflow. Run read-only E1 checks on the actual main tree, preserving actual server time and original policy boundaries. The observer has contents/actions read and issues write only; it cannot publish forecasts or scores. Tests on pull requests never deliver notifications. A manual delivery probe is explicitly labelled OBSERVER_TEST and uses the same notification route without creating a forecast or fault in the production ledger.

Use a dedicated GitHub issue for anomaly records, delivered by github-actions[bot] with an explicit mention of the verified repository owner. Stable protocol/registration/event/stage keys and serialized observer delivery deduplicate notifications across checks, runs, and retries. Only bot-authored markers count. Read every comment page; after an uncertain POST, re-read before retrying. A failed GitHub read is an observation failure, never evidence that a forecast/score/witness is absent.

Missing issuance is due at slot+45 minutes. Prediction public evidence is due before the actual one-hour target; scoring and target public evidence are due before target+25 minutes. Classify invalid/late evidence separately, respect the observation end, and exclude all old R2 gaps. Reuse frozen byte/formula/witness validation without calling any write/dispatch operation. No score means a scoring alert, without redundant target-witness alerts. Keep all success/performance/acceptance claims separate.

The verified notification address is the connected user's Gmail; no address or credential is added to repository files. Real acceptance requires a successful GitHub cloud probe, comment readback, and a corresponding received Gmail message with the unique probe ID. A second probe with the same key must be deduplicated. Record exact commit/PR/run/comment/artifact/mail evidence.

## Limits

Detection occurs on the next existing completion event, not at an exact deadline. If the entire Actions service or every upstream event stops, an event-only observer cannot wake itself; no independent cloud heartbeat is introduced. Mail delivery depends on GitHub subscription/email configuration and is verified rather than assumed.

## Implementation and verification

- [x] Add detector tests first; observe expected failure, then implement read-only detector.
- [x] Add notification selection/deduplication tests first; implement isolated delivery.
- [x] Add workflow security/no-schedule tests, then independent observer and CI.
- [ ] Run focused regression tests and existing repository CI; verify all frozen/historical blobs unchanged.
- [ ] Review PR, deploy the authorized observer, verify real cloud scan and notification probe.
- [ ] Read actual Gmail receipt and repeat probe for deduplication; keep production evidence unchanged.

## Review rulings

- Capture real evidence cutoff before checkout, so dependency installation cannot mature a deadline against an older tree. Keep actual check time separate.
- Wrap I/O validation errors before calling the frozen witness predicate; its malformed-candidate suppression must not turn a read failure into a missing witness.
- Deliver at most 20 new stable keys per comment, retaining unnotified backlog for later existing callbacks; oversized details point to the complete artifact.
- Persist only already verified, closed-window positive witness receipts in independently authenticated observer artifacts, bound to exact required/frozen bytes and observer implementation. Do not cache absence or skip real checks for new deadlines. These are engineering monitoring receipts, not a new statistical review or modification of frozen qualification.

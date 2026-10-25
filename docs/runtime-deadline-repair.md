# Runtime deadline repair — 2026-10-25

This changes operations only. Frozen models, registered protocols, original scores, missing denominators, append-only book history and blocked economic prefixes are preserved. No historical data is reconstructed and no new economic cohort is activated.

## Runtime

The existing hourly workflow retains its single `7,17,27,37,47,57` cron and shared writer lock. After initial publication, a bounded 40-minute foreground service polls sequentially. It services still-open genuine targets, refreshes current-hour features and invokes the unchanged issuer only before a conservative five-minute operational reserve, verifies public paired source context and captures fresh P2 depth. Full research and archive work remain after this service. Job timeout is 65 minutes to cover the service, the previously observed 952-second archive tail and margin.

The service never backdates a quote, reopens a final missing score, changes an old decision or skips a failed economic interval. P2 guard, full append-only history and raw snapshot checks run before every publication and again after rebase. Only explicit evidence paths may be staged; core and supplementary bytes require remote readback. Server-witness dispatch is recorded only as a request; the existing independent verifier still decides whether an exact public witness qualifies.

Any publication/integrity failure stops subsequent writers. Missing `fatal=false` output is unsafe, including process termination or disk failure. Immutable cycle receipts are retained in `data/runtime_deadline_v1/receipts`; the artifact step runs before the workflow stop guard. Diagnostics explicitly do not claim economic acceptance or repair of historical gaps.

## Acceptance boundaries

Code acceptance: focused failure-injection regressions, complete project suite, unchanged frozen-byte hashes and independent review. Deployment occurs only after these gates and Linux CI pass.

Operational acceptance remains future evidence: first genuine complete 1h issuance/publication/target/score cycle, then first 24h cycle; all newly matured IDs and the maximum observed book interval must be checked. A successful job alone is insufficient. Observe at T0+2h, +25h and +72h; evaluate the complete rolling 7-day >=90% and 30-day >=99% issuance/target gates independently. T0 is the first observed use of the merged service, not this document's date or a queued event's SHA.

GitHub may not start a runner on time. The service reduces dependence on repeated dispatch while a runner is alive; it cannot guarantee continuity during an external scheduler outage or runner loss. Every overrun/failure remains visible. Old V3.1 and the existing locked economic cohort remain blocked by their original gaps. A separate future economic registration would require an independently frozen protocol before its first observation; this patch does not silently create one.

## Review and test history

Failure-first tests cover expired/future targets, the last five valid target minutes, preparation crossing an hour/deadline, publication failure after partial capture, source-audit failure, corrupt real P2 JSONL, fatal finalization and disk-full output handling. Independent review identified and resolved the target reserve, P2 publication validation, fatal continuation and absent-output cases.

Windows baseline exposed existing CRLF checkout/UTF-8 environment issues and two platform-dependent path mocks. Exact registered Git bytes were restored locally (not changed in the patch); the path mocks use `as_posix()` so their original failure-injection behavior works on Windows and Linux. The timeout regression now accounts for the service plus the observed archive tail.

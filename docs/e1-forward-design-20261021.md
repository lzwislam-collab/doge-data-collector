# E1 prospective engineering continuation

The user explicitly authorized a new engineering version, independent registration,
and subsequent real issuance after learning the original R2 runtime/workflows are
hash frozen. R2 strategy, files, registration, forecasts, scores and all gaps remain
unchanged. No cron or Apps Script triggers may be added.

E1 uses data/e1_forward_v1, its own protocol/registration and two new callback/manual
workflows. It reuses the original prediction/score function code with an isolated
policy scope; no original module globals or files are changed. The new protocol
retains the same model, features, horizon, slot interval, issue and target windows,
cost rules and missing-data treatment. Its independently registered observation
window begins 2026-10-21T00:00:00Z for 90 days. If registration cannot finish before
that instant, no event may issue; change the not-yet-registered prospective version
before registration, never backdate activation.

Registration requires a successful same-repository main manual E1 witness run,
with exact registered code/policy bytes, finished before effective_from and first
issuance. A subsequently appended locator is reread from GitHub, not trusted as a
boolean. No historical E1 samples or R2 imports are permitted.

The source witness, prediction publication and target publication each require a
successful actual main E1 witness job/run and exact pinned bytes. Optional absence
in an existing verified Git tree means mismatch. Unknown commits, network/auth
failures and frozen-code mismatch fail closed. Successful predicates, timestamps
and identities are reread from GitHub. All captures use the real UTC clock.

The live workflow receives existing hourly completion callbacks even when unrelated
upstream work failed; it still requires fresh exact source bars and independent
source proof. A successful E1 witness callback retries tick, resolving source-proof
ordering. No new timer polls exist. A genuinely new forecast creates one bounded
score job in that workflow, waiting until its actual target, then checking out main
and capturing only inside the original target window. Event-level job concurrency
does not hold the write lock during the wait. Push conflicts have bounded retries
and journal revalidation. Successful pushes immediately dispatch the E1 read-only
witness. Expired targets are recorded as gaps and never reconstructed.

The PLUS report adds a separate E1 engineering panel and keeps original R2 metrics,
fixed denominator, nine historical gaps, and statistical_gate_passed=false intact.
E1 is not evidence of 70% accuracy, profitability or production promotion. Trading
and automatic model promotion remain disabled.

Interfaces: e1_forward.py provides guard(), panel(online=True), CLI tick, score
--event-id ID, confirm-registration --run-id ID, and witness --kind register|verify.
Outputs live in e1-forward-output/runtime.json or witness.json. CLI sets GitHub
outputs event_id, target_utc, request_witness, has_errors. BASE registration locator
is data/e1_forward_v1/registration_locator.json. Forecasts/scores/attempts/bindings
are append-only. Workflows: e1_forward_live.yml and e1_forward_witness.yml. The
registration manifest freezes e1_forward.py, both workflows, config/e1_forward_policy_v1.json,
and original R2 registered bytes; the original guard remains mandatory.

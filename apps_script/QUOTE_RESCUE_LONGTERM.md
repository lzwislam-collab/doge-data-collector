# Independent long-term quote rescue

This caller uses the already verified `quote_only_rescue.yml` main entry. It
does not change the normal collector, frozen implementations, protocols,
thresholds or historical evidence. Loading either script creates no triggers.
Keep `quote_rescue_live.gs` unchanged: its CSV parser and decision function are
dependencies of the new caller. Preserve the old trial states and failures.

## Installation and minimum authorization

1. Complete automated tests and independent review first. Save the new
   `quote_rescue_longterm.gs` as its own file in the existing **independent**
   Apps Script project; never install it in the normal collector project.
2. Keep existing `GH_ACTIONS_READ_TOKEN` and `GH_QUOTE_RESCUE_DISPATCH_TOKEN`
   separate. Only the latter needs fine-grained Actions read/write for the
   single `lzwislam-collab/doge-data-collector` repository, plus mandatory
   Metadata read-only. No Contents or account permission is required. GitHub
   cannot limit this permission to a single workflow; code enforces that URL.
3. The owner privately extends/replaces any short-lived token as necessary.
   Never read, copy, screenshot or log credential values. Check only permission
   metadata, expiry and whether the saved property is nonempty. Save verified
   UTC expiry metadata in `QLT1_READ_TOKEN_EXPIRES_UTC` and
   `QLT1_DISPATCH_TOKEN_EXPIRES_UTC`. Expiry metadata must match GitHub; it is
   not an authorization check and cannot prevent early revocation.
4. Add only the nonsecret properties `QLT1_DEPLOYMENT_READY` =
   `QLT1_REVIEW_20261016` and `QLT1_MAIL_ENABLED` = `true` after review.
5. MailApp requires the Google `script.send_mail` scope, not access to read
   Gmail. The owner must approve Google's authorization screen. Notification
   code fixes the recipient to `lzwislam@gmail.com`. This restriction is in
   code: Google's mail scope itself is not restricted to one recipient.
6. Run `testQuoteRescueLongTermNotification` once and verify receipt. It sends
   one test mail, never captures or starts timers. Its durable one-shot marker
   intentionally refuses an automatic repeat after an uncertain result.
7. Verify no live-validation timer remains. Run `startQuoteRescueLongTerm`
   once: exactly one 5-minute `runQuoteRescueLongTerm` timer and one 15-minute
   `watchQuoteRescueLongTerm` timer should exist. Startup requires at least
   24 hours credential lifetime and a nonzero remaining mail recipient quota.

## Limits and accounting

Counters use hourly buckets including the oldest partially overlapping hour
plus one hour for executions spanning a bucket boundary. This conservatively
includes up to 26 hours and never resets at midnight.
Caps: 1,200 URL Fetch attempts, 48 dispatch intents and 30 minutes **internal**
execution budget. Each invocation must reserve the full 6-minute execution
allowance before HTTP or an expiry-warning mail; interruption retains that
reservation. Another full 6-minute allowance is held for one stop/emergency
notice. Normal completion charges measured handler time, with floors of
3 seconds per main poll and 1 second per watchdog. An idle 24-hour model is
576 GETs and 16 minutes internal budget, excluding extra elapsed time.
Admission can stop before a nominal cap because a full reservation must fit.

Idle polls read and validate only the registry and scores. If the existing
decision function finds an eligible candidate, the caller then reads the real
bounded Actions run list and repeats the decision with a fresh clock before
reserving any dispatch. An empty provisional run list is never permission to
dispatch. In-flight monitoring is unchanged. This avoids an unused request on
no-target polls; it does not cache target data or remove the run guard.
Credential expiry metadata is still checked every invocation. An early token
revocation or low GitHub API quota is detected when API work is needed rather
than on every idle poll. No token permissions are expanded.

These counters are not platform execution durations or the account's exact
remaining Google quota. The earlier 12-sample mean full duration was 3.713333
seconds, but individual platform/network delays remain possible. Check real
Time-driven platform rows after activation, retaining every slow sample. Do
not replace full platform duration with handler time or claim latency cannot
recur. Google quotas are per user and shared with other projects. Current
consumer limits are 20,000 URL Fetch calls/day, 90 trigger minutes/day and
100 email recipients/day; this code does not enable paid services. The public
repository's standard Ubuntu Actions minutes are free; artifact storage is
shared and the reused workflow retains only its compact result for 3 days.

## Failure behavior and notifications

* A durable poll reservation precedes reads. Durable hashed target attempts
  and a dispatch intent precede the only allowed POST. A target is never
  requested again while it could still be eligible; expired attempts can be
  pruned because old targets can no longer be dispatched.
* Only naturally open, unscored targets at most 20 minutes past target time
  qualify. Existing scores, including missing, exclude a target. The server
  rechecks the window after the shared lock and before HTTP/publication. No
  historical backfill, prediction issuance or trading is added.
* Watch one accepted numeric run ID before considering another dispatch. Run
  identity, completion and the corresponding scored CSV rows are health checks;
  they are not a new attestation of raw bindings or remote readback. The unchanged
  server entry validates that full evidence. A green workflow by itself is
  never reported as genuine capture success.
* Authentication/schema/persistence errors, low API quota, ambiguous POST
  result (including legacy 204 without an ID), interrupted execution, missing
  result, failed/stuck run, expired credentials or budget exhaustion stop this
  caller. Queued work is never cancelled; the server rejects late requests.
* The watchdog performs zero HTTP, checks the main timer and last poll, and
  stops if no poll has entered for more than 15 minutes. It is not an independent
  external uptime service. Whole-platform outage or exhausted account trigger
  quota can delay the watchdog and email notification too.
* Operational emails are deduplicated and limited to three recipients in the
  conservative rolling budget. A send intent is saved before sending; unknown
  mail outcome is not automatically retried. Failure to deliver an expiry
  warning stops the caller. Corrupt-state/persistence failure uses the separately
  reserved one-shot stop/emergency capacity for this installation. When even
  that full reservation cannot fit the recorded runtime budget, no mail is sent.
  If even
  its marker cannot be saved, delivery cannot be guaranteed; inspect logs.
* Only this caller's two handler names are removed. Other project triggers,
  the normal Apps Script project and morning briefing remain untouched. No
  automatic recovery or state reset is provided. Preserve the stopped state
  and diagnose before any explicitly reviewed manual recovery.
  An independent `QLT1_FAILURE_LATCH` blocks later HTTP after uncertain state
  persistence, even if the failed write actually committed and trigger deletion
  also failed. Surviving stopped callbacks retry their own trigger cleanup only.

Use `showQuoteRescueLongTerm` for nonsecret state. Use
`stopQuoteRescueLongTerm` for a deliberate stop. Never run the timed handlers
manually, reset old trial state to simulate success, or extend permission
scope in response to an error.

## Fixed performance retest after the idle-query optimization

Deploy only after regression tests and independent review. Update only the
independent saved long-term source while retaining the existing two timers,
state, counters, attempts, notification markers and every prior failure.
Do not run the starter again. Logs identify this version with
`caller_revision: IDLE_GATE_20261016`.

Before deployment record the save boundary and a two-hour deadline. Assess
exactly the next 12 genuine Time-Driven main executions after that boundary,
including any slow, failed or incomplete sample. Never manually supplement,
exclude a slow sample, restart or extend the cohort to improve the mean. Require
at least one genuine watchdog in the same observation period. A missing version
marker or missing log is an attribution/verification failure, not a reason to
silently select a later fast execution.

The mean **full platform duration of the 12 main executions must be <=5 seconds**;
main intervals must be <=600 seconds. Report watchdog durations separately so
they cannot dilute the main mean. Estimate total daily runtime from 288 main
executions plus 96 watchdog executions using their respective full platform
means; the projection must be <=30 minutes. Account-wide precise remaining
quota is unknown and the estimate is not a measurement of the entire account.
The original 5.096-second failed cohort remains failed. Idle skips and a green
workflow do not establish a new genuine quote capture.

Official references:
* https://developers.google.com/apps-script/guides/services/quotas
* https://developers.google.com/apps-script/reference/mail/mail-app
* https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
* https://docs.github.com/en/billing/concepts/product-billing/github-actions

## Commit-pinned completion check and one-shot reviewed recovery

The completion check resolves main through the branch API, then reads the score
file through the contents API at that exact commit. It validates the file path,
encoding, byte size and Git blob hash before applying the original scored-status
and 25-minute timestamp checks. Only a completed-run check gains one GET; idle
polls remain two GETs and pending-run checks remain one GET. Inflight admission
reserves three GETs. Rolling limits and all server/frozen rules stay unchanged.
Logs include caller_revision PINNED_SCORE_20261016, score_ref, score_blob_sha and
bounded per-target failure reasons. They contain no credential or raw response.
The historical stopped response was not captured: stale CDN data is a supported
reproduction, not a proven historical cause.

After tests and independent review, save only this independent source. Preserve
all original STOPPED evidence and performance cohorts. Set the nonsecret
QLT1_SCORE_FIX_READY marker to PINNED_SCORE_20261016 only after review.
resumeQuoteRescueLongTermAfterScoreFix is an explicit, one-shot recovery, not a
new starter: it requires the specific RESULT_NOT_SCORED stop, no owned timers,
no failure latch and no prior recovery/archive. It requires at least 24 hours
credential headroom, available mail quota and reserved stop-notice capacity.
A callback encountering an incomplete recovery marker stops before HTTP. Before runtime changes it saves
the exact old JSON in QLT1_STOP_ARCHIVE_20261016 and reserves the recovery in
QLT1_SCORE_RECOVERY_20261016. It charges three reads and runtime to the original
rolling counters, reconciles the existing successful run with commit-pinned
scores, and only then creates the same two owned timers. It sends no collection
POST and preserves original started_ms, target attempts, dispatch counts and
all earlier samples. A partial/uncertain recovery stops and cannot be retried
by clearing markers. Original stop mail remains in the archived state; future
incidents can notify separately. Never delete an archive or reset a budget.

Record a recovery/save boundary and a fixed two-hour deadline. Observe exactly
the next 12 genuine main Time-Driven executions, plus real watchdog samples,
without mixing in the manual recovery or prior cohorts. Retain every slow,
failed, missing or unattributable sample; mean full platform main time must
remain <=5 seconds, intervals <=600 seconds and projected 288 main plus 96
watchdog full durations <=30 minutes/day. Do not restart or add samples to
improve the result. The original interrupted 24-hour window remains interrupted;
recovery does not retroactively establish 24-hour stability.

## Local-only phase timing (PHASE_TIMING_20261017)

This diagnostic revision is prepared locally only. It does not authorize a save
to Apps Script, deployment, execution, new observation, timer change or recovery.
The completed fixed 12-run cohort, all slow samples, the extra 20-second execution,
hourly failures and original interrupted-window evidence remain immutable.

Timed callbacks add a runtime-only `timing` object to the existing log:

- `entry_at_utc`: first clock inside the shared timed handler, before lock and
  property-service acquisition; `legacy_started_offset_ms` locates the existing
  `started_at_utc` relative to this clock.
- `spans_ms`: lock acquisition, property-service handle acquisition, aggregate
  property gets/sets, state loading, admission persistence, credential checks,
  CSV/JSON parsing, decision calls, pinned-content integrity, dispatch persistence
  and final persistence. A failing operation still records its elapsed span.
- `requests`: serial request attempts with closed endpoint labels, method,
  available numeric HTTP status, `completed`, and separate fetch, response-code,
  header and content-text spans. No URLs, property names, property values,
  credentials, headers or response bodies are added to the diagnostic log.
- `measured_to_log_ms`: handler entry through final persistence and any failure
  handling, ending before log serialization/output and lock release. It excludes
  platform initialization before handler entry. It is **not full platform time**.

Spans overlap: for example state loading contains a property read, credentials
contain property reads, and final persistence contains a property write. Do not
sum nested spans as exclusive time. Unlisted gaps remain unattributed. Missing
spans mean that stage was not reached; `completed:false` means the request did
not finish all response processing, including when an existing safety check
rejected it. A stage interrupted by hard termination cannot emit a final log.

`LONGTERM_LOCK_BUSY` explicitly records a failed immediate lock attempt, without
properties, HTTP, email, state changes or releasing another callback's lock. It
cannot retroactively explain the two historical watchdogs with unavailable logs.
Failure to acquire the lock service itself or the property handle still propagates
as before; those failures may have no final diagnostic log.

Legacy `elapsed_ms`, `request_ms`, deadline placement, rolling budget floors and
all admission/dispatch/score/stop rules retain their existing definitions. In
particular legacy elapsed ends before final persistence, while legacy request
time ends after response-code access, before header/content-text access. Added
instrumentation has overhead and does not promise lower runtime. The original
<=5-second **full platform main mean**, <=600-second intervals and <=30-minute
daily full-platform projection remain unchanged. No batching, timeout, retries,
new requests, extra property operations or persisted timing fields are added.

Local synthetic-delay tests check exact boundaries and unchanged call counts,
slow and failed requests, lock-busy zero-work returns, failed final persistence,
CSV failure, and the existing dependent commit-pinned completion checks. Only a
separately authorized future deployment and real observations could establish
live behavior; local timings are not new performance samples.

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
864 GETs and 16 minutes internal budget, excluding extra elapsed time.
Admission can stop before a nominal cap because a full reservation must fit.

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

Use `showQuoteRescueLongTerm` for nonsecret state. Use
`stopQuoteRescueLongTerm` for a deliberate stop. Never run the timed handlers
manually, reset old trial state to simulate success, or extend permission
scope in response to an error.

Official references:
* https://developers.google.com/apps-script/guides/services/quotas
* https://developers.google.com/apps-script/reference/mail/mail-app
* https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
* https://docs.github.com/en/billing/concepts/product-billing/github-actions

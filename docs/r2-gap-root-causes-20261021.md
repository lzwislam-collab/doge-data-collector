# R2 gaps: evidence, permanent losses and prospective repair

Evidence cutoff: independent main review 39509612011 at
2026-10-20T22:40:32.128137Z; source commit e2dcc677822e72d42fe85aa420729949bd6f3856.
Original result: 11 matured scheduled slots, 2 qualified events, statistical gate
false. Newer reports may show additional matured old-R2 gaps; this table is the
specific original six/one/two requested, not a claim that later R2 collection healed.

| Original gap (UTC) | Observed chain failure and evidence | Historical disposition |
|---|---|---|
| 00:00 issuance | First successful completion callback 39349768181 reached actual runtime at00:48:48, after00:45; it reported WAIT_EXACT_INPUT_WITNESS. Next callback39353465874 at01:27:58 still lacked exact source proof. | No actual issue in the frozen window; never synthesize one. |
| 04:00 issuance | Live39367129655 crossed04:00 and failed on absent old-snapshot score HTTP404.04:30 callback39369895926 was skipped after cancelled upstream. | No original event. |
| 06:00 issuance | Upstream39378756167 cancelled at06:19:22; live39379705946 skipped. Next callback07:11 was also skipped; no eligible actual capture exists. | No original event. |
| 08:00 issuance | Live39393159813 started runtime08:25 and failed reading a score absent at aacf5cf27eb3e37bb85d53c5206b1081cbad2a75. | No original event. |
| 10:00 issuance | Live39407091485 started runtime10:20 and failed HTTP404 before new capture. | No original event. |
| 12:00 issuance | Upstream39418718175,39424546781,39424658759 cancelled; callbacks39419808098/39425894058 skipped. | No original event. |
| 18:41:01 issue: missing score | Event1792519200_formal target19:41:01.076161, deadline20:06:01.076161. No original target attempt exists. Upstream39482582489 cancelled19:29;39487765035 cancelled20:06:01. Downstream39483875332/39489094358 skipped; next actual manual live20:40 was already outside target window. | Raw target capture is permanently absent; later candles or today's ticker cannot substitute. |
| 02:11 issue: missing target witness | Real score captured03:17:12.482249 inside window ending03:36:20.416990. Dispatch39363489721 cancelled; successor witness39363501498 failed03:19:54 on normal absence of the new score at older commit aacf5cf... . | Preserve genuine score as unqualified; a present-day successful job cannot become an on-time historical witness. |
| 20:41 issue: missing target witness | Real score captured21:47:37.310665 inside window ending22:06:40.502247. Live39503042900 persisted score despite separate missed-issue-window status. Dispatch39503216917 cancelled; completion witness39503231111 skipped because publisher failed. Later readers39507324235/39511578175 failed probing absent score at df80d320... . | Preserve score bytes. No independently successful qualifying target witness was found before deadline; no retrofit qualification. |

All run IDs refer to lzwislam-collab/doge-data-collector GitHub Actions. Downloaded
artifact ZIP SHA256 digests and logs were saved in the task's r2-chain evidence
directory. Original canonical event/score/attempt/binding files were read, not edited.

## Root causes and limits of certainty

The concrete recurring bug is frozen phase2_forward_v2.witness calling its reader
for a candidate commit lacking a newly added score. The reader's Git fallback then
HTTP404 raises OSError; witness catches only KeyError/ValueError/TypeError, so the
normal mismatch aborts publication, issuance and audit rather than trying another
candidate. This is distinct from auth/network errors, which must still fail closed.

There are also orchestration faults: the live callback gates on the entire large
hourly workflow's success, source proof discovery has no completion retry, and
target capture relies on unrelated completion callbacks instead of a per-event
bounded target job. A benign missed issuance attempt can mark an otherwise
successful genuine target publication failed, causing its automatic witness to skip.

Cancelled upstream runs lasted approximately8–9minutes and the job declares an
8-minute limit. Retrieved logs/metadata prove cancellation and downstream skipping;
they do not establish the exact cancellation initiator. Do not relabel all of them
as proven scheduler failures or proven timeouts.

## Repair boundary

Original code/workflows themselves are hash registered, and new exact procedures
must be publicly registered before original effective_from/first formal event.
Editing their hashes today would invalidate, not repair, the original R2 experiment.
The user therefore authorized separate future E1 registration and issuance.

E1 fixes optional absence handling, exact snapshot proof, source retry callbacks,
independent capture timing, immediate publication witnesses, and bounded conflict
handling. It uses the same original formula/model/fees/horizons and separate
90-day engineering window. R2 files remain byte-identical; its gaps stay in its
fixed denominator. E1 has separate evidence and no claimed statistical acceptance.

Acceptance requires real main registration, real prospective event, pre-target
prediction witness, actual due target packet, timely target witness and a new
PLUS report artifact. Unit tests, successful workflow dispatch and preserved raw
scores alone do not satisfy those acceptance conditions.

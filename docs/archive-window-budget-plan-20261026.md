# Reserve time for archive capture before a frozen source target

Root cause: run 40248370100 began archive capture at 01:43:26 UTC, after
the final nonoverlap-reserved 1h target (01:00:41) and 24h target (01:09:29).
Even their 25 minute grace periods had ended. Frozen source_items correctly
remained empty. Audit readiness alone cannot create a qualifying source.

Keep frozen reservation and strict quote-before-target qualification intact.
At startup of the existing foreground deadline service only, derive currently
active reserved targets using the frozen reserve function. Shorten its existing
2400 second limit to leave 1800 seconds before the latest active target for
research, daily PIT preparation, snapshot and upload. The observed failing tail
took about 20 minutes before capture; the reserve adds margin. If no active
reserved target exists, retain the existing duration so genuine new issuance
can occur. The existing hourly scoring/issuance cycle still runs before exit.
Do not extend a target, alter a cohort, invent eligibility, or modify bridge
limits. Earlier service exit lets the existing next task take over sooner.

This fixes the demonstrated initial-window budgeting error, not arbitrary
external delays. If the remaining tail still misses the target, qualified stop
must fail as before. It is not acceptable to count the timing calculation as
production acceptance. Persist the selected target, reserve and chosen duration.

Reproduce the 00:48:01 startup with exact recorded target times: old 40 minute
service plus observed tail misses both targets; budgeted service permits archive
capture before the last target. Test frozen nonoverlap exclusion of 01:01:45,
no-active-target fallback, long-active-target cap and unchanged bridge behavior.
Run full Linux suite and committed history integrity before merge, then require
actual formal raw books during upload and safe drain/full tail/readback.

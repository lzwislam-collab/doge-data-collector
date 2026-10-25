# Qualified archive drain repair — 2026-10-25

Production run 40190572097 completed successfully, but engineering success did not prove qualified handoff continuity. PLUS upload ran 12:58:42–13:03:07 UTC (21:58:42–22:03:07 JST). Qualified books were captured at 12:58:45.540109 and 12:59:44.841695 UTC. New-hour issuance at 13:00:49 invalidated the read-only audit input; the old audit remained pending until shutdown killed it. Subsequent books were smoke only. The next actual qualified book was 13:13:02.863008 UTC (22:13:02.863008 JST), a 798.021313-second formal gap. All three original raw snapshot hashes were independently recomputed and matched. This is an acceptance failure, not retrospective eligibility.

The independent archive manifest SHA-256 is 742a3c6c29268325b89ceb8aff1a69e2fe4c140b6bc54d15a45238a2085b1f07. The downloaded receipt artifact ZIP independently matches e395cb44e4013445f1031fa0274fd958049b18584a1e809a4aa4f9b1a1bf0d10. Snapshot hash verification and successful upload alone do not establish qualified continuity.

## Repair

- Cancel an obsolete read-only audit clone immediately when input fingerprints change; start its replacement without waiting for the obsolete audit deadline.
- Explicitly opt new compiled workflows into qualified archive drain. Old running workflows retain their existing timeout contract.
- After stop is requested, retain the sole live writer until a real, hash-validated latest formal book has a verified paired source and frozen publication/request/quote/target ordering, with matching current audit input. Runtime expiration without this proof fails closed. No smoke book is promoted.
- Extend only the new workflow stop budget to nine minutes, with a seven-minute helper wait and a ten-minute bounded runtime. Later PIT writers still require successful drain, final remote readback and independent snapshot verification.
- Report actual formal eligibility from the recorded qualified event rather than the command mode label.

## Validation and deployment

Reproduced obsolete audit survival and premature shutdown in regression tests before implementation. Cover pending-to-ready drain, bounded failure, actual subprocess termination, paired verification and public/request/target time ordering. Run targeted and full tests, independent review, Linux tests and frozen full-history integrity checks before merging.

Merge time is not effective time. Production acceptance additionally requires opt-in started receipt, qualified_stop_satisfied, matching original raw snapshot SHA, qualified books during upload, artifact success, complete tail success, and the next actual first qualified book. Report formal and engineering handoff intervals separately. Old ledgers, models, protocols, prefix counts and historical gaps are preserved.

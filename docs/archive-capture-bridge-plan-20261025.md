# Archive capture bridge

The 534.2s handoff gap is not all archive time. Workflow-level concurrency
holds the single-writer lease until all steps finish. Old forward upload
took 15s, plus upload 187s (1.53GB); diagnostics after last book and next
runner preparation account for the remainder. Keep that lease and frozen
evidence rules. Do not cancel the active producer or create another cron.

1. Test graceful stop at cycle boundaries and a byte-independent archive
   snapshot. Snapshot data/reports/system/publication-refresh-output only,
   reject links/special files and verify SHA256 before starting a writer.
2. Finish all live diagnostics before freezing independent archive inputs.
   Start one bounded capture process while those archives upload from the
   frozen copy. Stop by a sentinel, drain the current cycle, verify snapshot
   and runtime result, then permit PIT Git publication and preserve new receipts separately. No shared
   mutable archive input and no concurrent Git writers.
3. Existing open-target guard may run a bounded 5-minute bridge only on the
   trusted main runner, immediately after a successfully finished service,
   when a target is imminent or the current hour lacks an issue. It requires an explicit opt-in in the newly compiled workflow, so an
   already-running older workflow cannot launch a child without safety gates.
   The current 12:02:38Z target still needs actual handoff/score monitoring.
   Keep its normal short target check unchanged elsewhere.
4. Regression, independent review, Linux full tests and frozen integrity
   precede merge. Deployment is not activation: verify actual guard bridge
   and snapshot/capture/upload overlap in production receipts. Old artifacts,
   ledgers, missing windows, model and protocol remain intact.

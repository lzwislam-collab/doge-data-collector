# Slow source audit isolation

Production evidence at 2026-10-25 10:46 UTC established the 197.4-second
book gap from 10:42:55.552 to 10:46:12.952 UTC. The corresponding serial
cycle ran source_audit before book and took 170.323 seconds. The prior
refresh similarly produced a 196.65-second book gap. Existing receipts
do not attribute the three individual audit programs' elapsed time.

The runtime now launches those unchanged programs in a separate local Git
clone pinned to a committed head. The clone has its own index, refs, audit
caches and reports. It never publishes or writes the live ledger. Live
watch, issuance, depth capture and publication remain serial on the main
runner. Polling never waits for audit completion.

Only successful P0 and paired audit reports may return. Registry, scores,
quote bindings, paired records and hourly source hashes must still match
the snapshot. Changed inputs cause discard and restart. Frozen guards,
freshness, original public witness and at-capture qualification still apply.
While context is absent/stale/mismatched, genuine raw engineering depth
is retained without formal eligibility; it is never replayed into an entry.

The worker is bounded to 600 seconds and killed with its process group
when the runtime exits. Its clone is nested under the parent's managed
temporary directory. Each action and each background audit now has elapsed
timing evidence. No frozen protocol, model, historical score or P2 prefix
is changed. No additional production schedule or Apps Script trigger exists.

Acceptance requires Linux regression and frozen integrity before merge,
then a production receipt showing pending audits alongside capture/watch,
successful matching report adoption and actual quote gaps. Neither tests
nor deployment alone establishes successful cross-hour scoring or profit.

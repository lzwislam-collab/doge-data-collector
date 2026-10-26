# Archive qualified drain failure propagation

> For agentic workers: implement inline with superpowers:executing-plans and test-driven-development. User explicitly authorized autonomous investigation, isolated repairs and a draft PR; no additional implementation approval is required.

**Goal:** Preserve the frozen qualification gate while making its failure unambiguously unsafe to every output consumer, with capture-time context diagnostics.

**Architecture:** Existing frozen P2 reservation and source verification remain authoritative. The runtime records the source context of the book it just inspected, and terminal qualification failure sets fatal before publication/output. Finalization independently refuses an explicitly required but absent qualification.

**Tech stack:** Existing Python standard library/unittest; local Git and isolated fixtures.

**Spec:** User request and docs/qualified-archive-drain-plan-20261025.md. Run40248370100 is the real failure sample. Source context gaps are legitimate consequences of the frozen reservation; they must never be repaired by selecting excluded events.

## Global constraints

- No changes to frozen source reservation, protocols, models, ledgers, old captures or scores.
- No timer, workflow trigger, timeout, permission, lock or publication gate changes.
- No production rerun, dispatch, merge, deployment, OSS upload or real trade.
- PR65/66 and81/82 constraints unchanged. PR75 work is not duplicated.
- New terminal diagnostics do not certify production recovery or economic acceptance.

## Review focus

- Missing qualification output must fail closed when required.
- Qualified success still follows final remote publication and receipt persistence.
- Legacy non-qualified service behavior remains unchanged.
- No-source gaps, unqualified reserved sources and audit-not-ready are distinguished without promoting any capture.
- Failed terminal qualification must not launch a new publication.

## Task 1

Files: runtime_deadline_service.py; new test_archive_qualification_failure.py; this plan.

- [ ] Reproduce the exact failure with the existing bounded runtime and fixed clock: stop requested, can_stop false, no cycle errors. Assert fatal true, DEGRADED and qualified_stop_satisfied false. Watch the original code fail the fatal assertion.
- [ ] Test finalization with required qualification false/missing: unsafe output only, no publication; qualified and legacy success retain publication semantics.
- [ ] Test book-action diagnostics on empty reserved context, rejected context and qualified context. Mock only the external book command and frozen ledger I/O, never the runtime behavior.
- [ ] Set required/satisfied fields and fatal reason on terminal qualification failure. Add the same fail-closed check at finalization before any subsequent publication.
- [ ] Add bounded source-count/qualification diagnostics from the frozen event already read. Preserve eligibility predicates and all producer calls.
- [ ] Run targeted suite and project full suite; list every environmental or baseline failure separately.
- [ ] Recheck exact historical reservation/timing, protected file hashes and branch diff. Fresh independent code review; create draft PR and exact remote readback. Do not merge/deploy.

## Ledger

Ruling: Preserve the strict new-qualified-book shutdown condition even when the frozen reservation creates a gap. Any alternate idle-drain admission would change the acceptance contract and is outside this fix. Cost: such natural runs may still fail honestly until a real qualified event exists.

Ruling: Use a fresh sparse clone inside the task workspace, since the task root is not a managed Git checkout and no attached worktree exists. Existing workspaces and sources remain untouched.

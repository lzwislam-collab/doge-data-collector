# DOGE report and PLUS audit fixes

> Execute inline under superpowers:executing-plans; user approved all accepted review items.

**Goal:** Integrate verified PLUS into the existing eleven chapters and prevent stale or mixed evidence claims.
**Architecture:** Keep frozen engines and journals unchanged. A read-only report adapter gathers current evidence directly, degrades unavailable PLUS independently, and produces an explicit global evidence status.
**Tech Stack:** Python unittest, existing GitHub Actions.
**Spec:** Accepted review decisions in this conversation, approved 2026-10-15.

## Global Constraints
- No new schedules, Apps Script triggers, trading, automatic promotion, or frozen scoring changes.
- Historical research, old forward, original18, R2 and canary stay separate.
- Published forecasts may display before target; scoring requires target proof. Missing costs remain null.
- Independent custodian is not a required condition; disclose retention limits.

## Review Focus
- Stale/future/missing quality timestamps cannot claim current PASS.
- Wrong direction cutoff blocks that direction section, not factual background chapters.
- PLUS failure cannot reuse an old snapshot or display stale probabilities.
- Every displayed event separates reference, issue and target clocks.
- Canary/history injected into formal paths must not improve formal results.

## Tasks and verification ledger
- [ ] 1. Add failing report tests: evidence state, timing, chapter integration, unavailable adapter; implement report_evidence.py and report_v41.py hooks.
- [ ] 2. Add cohort injection / flat / cost regression tests; clarify PLUS DOWN display and reference clocks without changing scoring.
- [ ] 3. Update existing report workflows only: dependencies, current read-only PLUS, evidence artifacts, test triggers.
- [ ] 4. Run full suite, frozen guards, review diff, push PR, run Actions and reread actual report/artifact. Claim deployment only after main verification.

Ruling: Continue in the existing clean checkout on a dedicated feature branch; no additional worktree or permission cycle is needed for this authorized modification.

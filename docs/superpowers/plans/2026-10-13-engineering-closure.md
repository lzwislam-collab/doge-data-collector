# DOGE engineering closure implementation plan

> **For agentic workers:** Use superpowers:executing-plans; one independent whole-change review before deployment.

**Goal:** Close existing collection, issuance, publication, scoring, recovery and archive engineering paths in one audited version.
**Architecture:** Preserve all frozen models, original evidence and statistical denominators. Fix orchestration defects; exercise real local Git publication plus injected transport/archive faults, then verify deployed runs before writing acceptance.
**Tech Stack:** Python unittest, Git, GitHub Actions.
**Spec:** User-authorized six-stage closure and failure scenarios in this conversation.

## Global Constraints

- No trading or DCA changes, no fabricated historical forecasts or target quotes.
- No external storage account: independent retention remains NOT_CONFIGURED.
- Never relabel simulated failure tests as actual production failures or historical PIT proof.
- System acceptance only after independent review, CI and deployed readback.

## Review Focus

- API callers must inherit authentication without logging credentials.
- Zero-new issuance must not imply a successful new forecast.
- Failed push/readback must not produce publication success.
- Timeout/offline failures must preserve denominators and immutable bytes.
- Upload failure must remain visible and permit same-day recovery.

### Task 1: Inventory and workflow authentication

Files: .github/workflows/okx_hourly_extreme_history.yml; test_engineering_closure.py.
Consumes: existing forward_validation.github_json GITHUB_TOKEN contract.
Produces: authenticated audit step with no frozen-code change.
- [ ] Reproduce missing token in minimal_audit; write regression assertion and observe failure.
- [ ] Add narrowly scoped audit environment; ensure no new schedule.
- [ ] Run regression and frozen-integrity/full suite.

### Task 2: Failure-path verification

Files: test_engineering_closure.py; existing test suites unchanged unless an actual defect is found.
Consumes: publisher.publish, retry.capture, prepare_daily/record_artifact contracts.
Produces: six-stage scenario matrix with named runnable tests and outcomes.
- [ ] Exercise actual local Git push exhaustion/recovery, remote mismatch, zero-new repeat; timeout/offline and mutation safety.
- [ ] Verify stale/missing/duplicate data, genuine scoring evidence, upload failure recovery, timeout receipt, immutable archive tests.
- [ ] Fix substantive independently reviewed defects with RED/GREEN evidence in this same branch.

### Task 3: Review, runtime and acceptance

Files: final audit/report and system engineering pointer only after acceptance.
Consumes: exact code hashes, full suite, reviewer findings, GitHub runs and artifacts.
Produces: auditable engineering version separate from statistical/PIT acceptance.
- [ ] Independent review; full Linux CI; integrate only clean reviewed code.
- [ ] Read deployed scheduled run, new/zero issuance outcomes and archive content; preserve unresolved items explicitly.
- [ ] Record acceptance only for verified scope, freeze orchestration hashes; do not claim prediction advantage.

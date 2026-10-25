# Runtime deadline repair implementation plan

Goal: recover future issuance, real target quotes and fresh depth observations without changing frozen files or reconstructing historical evidence.

Architecture: add a serial bounded foreground service to the existing hourly job after early publication and before slow research. The service reuses frozen collectors, issuers, publishers and guards; it holds the existing concurrency lock and never runs concurrent writers. Existing cron remains unchanged. GitHub scheduler absence remains an external limitation. No new economic cohort is activated.

Constraints: preserve frozen models, protocols, registration identities and historical records; no late issuance, historical quotes, synthetic books, automatic promotion, real orders, or additional Apps Script triggers. Runtime evidence lives in data/runtime_deadline_v1. Deployment is authorized by the user after tests and review.

Review focus: partial command failure must preserve evidence; clocks must advance after slow work; publication failure must not be called success; source audit freshness cannot be bypassed; existing old prefix blocks must remain.

- [ ] Task 1: failing behavior tests for deadline selection, target priority, retry, fresh-clock boundaries, publication failures, lifecycle receipts, and nonoverlapping serial scheduling.
- [ ] Task 2: implement runtime_deadline_service.py with injected external boundaries, frozen Python subprocesses, readback-verified publication and exact-head server witnesses. Capture P2 before slow research and periodically while the runner is alive; always retain errors separately from economic success.
- [ ] Task 3: wire the existing job (same cron and concurrency), preserve runtime artifacts, propagate service failures after saving evidence; add dedicated PR-only regression workflow with no production writes.
- [ ] Task 4: run targeted and complete project tests, frozen-byte checks, independent code review, then publish PR and deploy only if accepted. Report actual merge/first-run times and relative checkpoints; never claim live recovery without evidence.

Ruling: the user explicitly authorized implementation and deployment without additional questions. Old economic cohorts remain blocked; creating a new frozen economic protocol would exceed the requirement to preserve current protocols and is deferred until runtime reliability is established.

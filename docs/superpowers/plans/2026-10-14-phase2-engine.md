# Phase2 Research Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement deterministic, budgeted factor/model research with validation-only selection and reproducible artifacts.
**Architecture:** Add a separate causal price factor library and engine; reuse immutable phase1 source export, temporal splits, numeric contract and audits. Preserve old registered modules.
**Tech Stack:** Python 3.11 in Actions, NumPy 2.3.5, JSON artifacts, existing GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-14-phase2-engine.md

## Global Constraints

- 17 features, 5 groups, 11 factor combinations ×4 settings, 44 hypotheses; max600 fits.
- Default4096/512/512 bars,24 embargo,1H/24H,6 folds; train-only transformations, validation-only selection.
- No old code/data changes, no cron, no paid dependencies, no production promotion.
- Historical/pristine/prospective/cost/70% claims remain explicitly false.

## Review Focus

- Future/validation/test mutations cannot change prior features or fitted model.
- Receipt-time mode must not pretend downloaded historical data was timely available.
- Missing data stays WAIT with due denominator, not fabricated test metrics.
- Output symlinks/foreign files/protected directories cannot be overwritten.
- Every artifact and candidate model must have verifiable identity and exact bytes.

### Task1: Causal factors and fixed hypotheses

Create `phase2_factor_library.py`, `config/phase2_research_v1.json`, tests `test_phase2_research.py`.
Interfaces: `rows(frame,h,mode)` produces phase1-compatible labeled causal feature rows; `variants()` returns fixed columns, `hypotheses(policy)` produces44 declared configurations.
- [ ] RED: future mutation, timestamps/gaps, receipt restrictions, fixed family, bounded policy tests.
- [ ] GREEN: implement17 numeric causal features with72h history and 5 named groups; reject invalid sources.

### Task2: Nested rolling research

Create `phase2_research.py`; interfaces `fit(setting,x,y,policy)`, `predict(model,x)`, `choose(validation_metrics)`, `experiment(frame,policy)`.
- [ ] RED: train-only scale, single-class, test mutation invariance, same-event baseline/ablation, missing cohort and budget tests.
- [ ] GREEN: logistic and shrinkage-LDA fit, validation Brier selection, paired test events, descriptive nonoverlap/regime/error summaries and model hashes.

### Task3: Registry, immutable outputs and cloud execution

Same engine `run(root,out,policy_path)` and `verify_output(out)`; new `.github/workflows/phase2_research.yml`.
- [ ] RED: foreign directory/symlink/registry conflict and hash tampering tests.
- [ ] GREEN: guard original frozen files before/after; export original snapshot; record identities, per-fold models and candidates, original source audit; verify byte-identical reruns.
- [ ] Run targeted/full tests and integrity; fresh whole-branch review; fix actual issues using RED/GREEN.
- [ ] Push PR, actual Actions run, readback digest/models; merge and verify actualmain run before completion.

## Ledger

Base c2b5d699b21a13a8ff62c19c02f0c1f533860739. Existing checkout reused on separate research/phase2-engine-20261014 branch; earlier downloaded evidence remains untracked and excluded from commits. User's repeated immediate-execution authorization supersedes redundant approval pauses. No original registered module modified.

Task1/2 initial20 tests RED (missingengine) →GREEN. Real fixed snapshot export verified2670 unchanged files; actual528fits/528candidate models with source cutoff2026-10-13 13:00UTC, no duplicate/gap. Original candidate package SHA c93004aadc669c074f51a4cb6411769c3d84d388b31773d1251823b1d45ccad3 unchanged.

Review fixes: empty/incomplete manifest and precision tie selection reproduced RED→GREEN; strict necessary artifacts, same experiment identity and model/candidate references enforced; validation quantized before selection. Git metadata output boundary reproduced RED→GREEN, original symlink paths checked before resolve. Targeted26 tests GREEN (Windows2symlink privileges skipped; Linux Actions must execute them).

Ruling: source snapshot stays original registered298debd rather than latest downloadedmain, so historical reproduction remains exact. This engine version's automatic tasks run on code/config changes or explicit dispatch, with no new cron; rolling adaptive prospective retraining requires a separately registered future protocol.

# PLUS P1/P2 shadow upgrade — 2026-10-15

Base: `59dab912fd08bc3033355dc76696b13be37a4dd7` (main, fetched 2026-10-15 JST).
The user requests immediate parallel implementation and reviewable delivery, independent of P0.

## Scope and design

P1 owns source completeness/PIT quality and offline frozen rolling research. P2 owns independent same-event diagnostics against the frozen V4.2 baseline. Keep their interfaces additive and their results separate from registered decision statistics.

Reuse the existing `factor_eligibility_v1`, `phase1_research`, `phase2_research_v2`, `phase2_forward_v2`, `phase3_independent`, paired-cohort and spot-paper modules. Existing registered files are hash-frozen: changing them would silently alter an experiment. New audits call those modules through sibling adapters and retain their identity and original conclusions.

P1 must reject incomplete asset panels, report actual source coverage and first visibility, and distinguish downloaded historical data from prospective PIT evidence. Replay existing rolling research with a new input identity; retain negative feature and model results. Missing external PIT cannot be repaired by assigning historical event times as receipt times.

P2 diagnostics must include failed/missing scheduled observations in denominators, report both directional recalls, fixed calibration bins and Brier, preserve the independent fixed-look multiplicity protocol, and distinguish paper costs and cash/hold baselines. Diagnostic additions do not amend the active registration or authorize promotion. Future proposals require a new pre-window public witness.

## Invariants

No edits to existing config, system pointers, data, research archives, predictions, scores, registered code or existing Actions. No scheduled trigger additions, workflow dispatch of issuers, backdated forecasts, production switches, live trading or DOGE investment-rule edits. P0 remains with its original work line. Passing code does not establish a 70% forecasting advantage or profit.

## Execution and verification

1. Fetch main and inventory the six requested repository areas; isolate a new branch.
2. P1 and P2 agents inspect independently, write regression tests, observe failure, implement minimal additive fixes, and run real read-only audits.
3. Independently review the combined patch. Run complete tests, original frozen integrity and independent registration guard.
4. Commit only explicit deliverable paths, push independent branch, open draft PR and read back exact head, changed paths and CI results. No merge or activation.

Acceptance separates engineering PASS, source/research REVISE and unavailable prospective evidence BLOCKED. Archived outputs identify their source commit and UTC/JST cutoff; local offline replay cannot certify public issuance, locked retention or future scores.

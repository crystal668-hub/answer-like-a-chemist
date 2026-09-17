# Retired Benchmark Removal

Date: 2026-09-17

Scope: ChemBench, FrontierScience, HLE, and SUPERChem evaluator/prompt support
and dashboard Dataset/Subset choices, including the frozen ChemQA entrypoint.

Status: complete; implementation, full-suite validation and live dashboard
verification passed.

Evidence baseline: clean Git `766b830`. Focused pre-change baseline: 86 passed.
The user's explicit removal request supersedes the earlier instruction to retain
these four evaluator and prompt implementations. VGB and historical result
preservation boundaries remain in effect.

## Changes

- Deleted `benchmarking/scoring/evaluators/chembench.py`, `frontierscience.py`,
  `hle.py`, and `superchem.py`, including their judge prompts. VGB and generic
  evaluators remain. The registry explicitly rejects retired identifiers before
  evaluator overrides or generic fallback.
- Removed four-family answer-kind inference and dedicated benchmark prompts
  from `service/chemdebate/prompts.py`. Generic instructions, explicit protocol
  answer kinds, and `verifier_grounded_candidate` remain supported. Frozen
  discovery/record admission and direct runner calls reject retired benchmarks.
- Removed obsolete dataset-specific random sampling flags and their now-unused
  helpers. Shared historical dataset normalization and artifact parsing remain.
- Removed HLE-specific instructions from the active `act-like-a-chemist` skill
  and replaced dataset-named routing notes in the frozen ChemQA skill with
  task-neutral guidance. All provider skill directories and the routing matrix
  remain intact.
- VGB normal/rescue answer checks now dispatch before historical HLE recognition.
  HLE-only text no longer passes VGB completeness or candidate-contract checks.
  Historical parsers retain their non-VGB interpretation behavior.
- Dashboard responses add `selectable_facets`, excluding exact retired dataset,
  subset and eval-kind labels. Dataset/Subset controls consume these pairs,
  preserve VGB choices in mixed runs, scope subsets to the chosen dataset, and
  reset obsolete selections. Existing display facets and detail APIs retain
  historical information; no run data is deleted or rewritten.

## VGB Preservation

- VGB release resources, scorer, runtime bridge, worker and skill routing matrix
  have no diff against the baseline.
- All four formal VGB input JSONL files match the retained SHA-256 inventory
  from the preceding cleanup. The same 85 provider skills remain exposed.
- New tests cover every release track's ChemQA prompt identity and VGB answer
  contract, including negative HLE-only input and positive line/block answers.
- Dashboard mixed-run tests keep all four VGB facets, preserve similarly named
  unrelated datasets, and verify historical HLE detail reading without changing
  the saved result file.
- Retired scorer-only and prompt-only tests were replaced with retirement
  rejection tests. Protocol/lifecycle tests now use generic inputs or explicit
  answer kinds. Historical artifacts, schema readers, recovery, judge, cleanroom,
  and VGB coverage remain.

## Verification

- Focused boundary/dashboard regression: 77 passed.
- The managed local dashboard service was reloaded through its existing launchd
  job. Live API still lists 82 runs, including 20 readable historical legacy runs;
  none of the retired values appears in selectable facets.
- Browser verification at `http://127.0.0.1:8765`: Dataset contains `tests`,
  `verifier_grounded`, and `vgb` plus the all option. Selecting `vgb` yields
  42 runs; selecting `property_calculation_basic` yields 6. All four retired
  families and their subsets are absent from the controls; the page console
  has no errors. Existing historical VGB subset aliases remain selectable.
- Full suite: 1003 passed, 11 existing skips, 164 subtests passed in 97.84 seconds.
  The five warnings are existing SWIG deprecation warnings in paper parsing.
  No new test skip was introduced, and no real model call was needed.
- Final scoped regression after extending direct-runner rejection to retired
  subset labels: 172 passed, 14 subtests passed. Documentation catalog validation
  resolves all 50 targets, and `git diff --check` passes.

Generated evidence lives under
`state/benchmark-runs/temporary/vgb-retirement/no-model/vgb-retirement-no-model-20260917/`,
including `dashboard-facets.json` and `full-suite.xml`.

## Remaining History

Names can still occur in shared historical dataset classification, HLE and
FrontierScience answer recovery, saved metrics, media bundle readers, frozen
artifact reconstruction, rejection tests and past documents. These preserve
readability of stored evidence; they do not restore retired evaluator modules
or permit new benchmark execution. The dashboard intentionally continues to show
historical dataset names in run summaries and record details, while excluding
them from Dataset/Subset selection.

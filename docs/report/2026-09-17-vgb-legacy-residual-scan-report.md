# VGB Legacy Residual Scan

Date: 2026-09-17

Scope: tracked repository source, skills, tests and documents; running dashboard
Dataset/Subset metadata; active single-LLM answer admission.

Status: scan complete. Two active HLE residues remain unmodified. The original
[delivery document](2026-09-17-vgb-runtime-legacy-facility-cleanup-report.md) is
marked `close` at the user's request; that administrative status does not resolve
the new findings below.

Supersession (2026-09-17): the user subsequently authorized removal of the four
retired evaluators/prompts and their dashboard filter choices. The active HLE
findings were addressed in that follow-up; see the
[removal report](2026-09-17-retired-benchmark-removal-report.md). The scan results
below remain the original pre-removal evidence.

Evidence baseline: `42178e42d14f443d0f9fef397cbb341b30ca1350`, clean worktree.
The scan uses case-insensitive tracked-text searches for ChemBench,
FrontierScience, SUPERChem and HLE identifiers, including underscore variants.
Raw line-level inventory, live API results, and answer probes are retained in
`state/benchmark-runs/temporary/vgb-runtime-cleanup/no-model/vgb-runtime-cleanup-no-model-20260917/residual-scan.json`;
`scan_residuals.py` contains the reproducible checks. Counts describe the
pre-document-update baseline, not the new report's own references.

## Active Findings

### HLE answer recognition reaches VGB

`benchmarking/core/convergence.py:430` accepts an HLE response through
`HLE_ANSWER_RE`. Both `is_complete_answer_for_eval` at line 521 and
`is_complete_rescue_answer` at line 507 call that generic predicate before
checking `eval_kind`. Active callers include
`benchmarking/service/single/runner.py:386` and
`benchmarking/service/single/openclaw_wrapper.py:468`.

This input was tested against real first-record RDKit and xTB public schemas:

```text
Explanation: A checked derivation.
Answer: CCO
Confidence: 90%
```

For both records, normal completeness, rescue completeness and the candidate
contract returned true, while `has_final_answer_marker` was false. The xTB
record requires `final_answer_block`, but this text has no XYZ block. This is
an active behavioral residue, not solely a frozen parser. It can incorrectly
stop answer collection or accept rescue output as complete. This probe does not
claim that the verifier awards a positive score to such output.

Recommended follow-up: dispatch completeness by eval kind before applying
historical format recognition, preserving HLE behavior for explicit frozen
callers. Add negative VGB tests for HLE-only text in both line and block schemas.

### Exposed skill retains HLE instructions

`skills/act-like-a-chemist/SKILL.md:70` retains an `HLE Tasks` checklist, including
the official HLE answer format at line 74. Its routing matrix entry has
`single_agent_exposure=true`, and runtime inventory confirms it remains available
to VGB skills-on agents. This is optional agent-readable guidance; the scan did
not observe a model consuming it or generating an incorrect answer because of it.

Recommended follow-up: replace dataset-specific guidance with task-neutral
answer-format guidance while retaining the provider skill itself.

## Retained Modules

Fourteen `benchmarking/` source files match the scan:

| Surface | Files | Current reachability |
| --- | --- | --- |
| Evaluators and judge prompts | `scoring/evaluators/chembench.py`, `frontierscience.py`, `hle.py`, `superchem.py` | Callable shared implementations, explicitly composed by frozen ChemQA. |
| Registry | `scoring/registry.py:19` | `legacy_evaluators()` and explicit public `register_default_evaluators()` retain old kinds and generic fallback. |
| Frozen arguments and answer prompts | `service/chemdebate/execution.py:34`, `service/chemdebate/prompts.py:60` | Explicit legacy entrypoint only. |
| Shared dataset/subset handling | `core/datasets.py:112`, `workflow/dataset_selection.py:24` | Old classifications and subset sampling remain for frozen callers. |
| Answer and prompt helpers | `core/convergence.py:430`, `core/prompt_inputs.py:10` | Historical support retained; convergence also has the active defect above. |
| Input media bundles | `runtime/bundles.py:325` | HLE/SUPERChem materialization retained for frozen callers; VGB adapter supplies no bundle. |
| Reporting and analysis | `core/reporting.py:171`, `analysis/automated.py:554` | Historical HLE calibration and SUPERChem RPF handling retained; VGB-only analysis takes the verifier branch. |

An isolated-process import check confirms the active evaluator table is exactly
`["verifier_grounded"]` and no ChemDebate or old evaluator module is loaded.
There are no direct old-dataset name hits in `service/single/` or the default
CLI. This lexical result alone does not rule out shared-helper behavior, as the
HLE probe demonstrates.

Four tracked skill files also match: the active skill document above, frozen
`skills/chemqa-review/SKILL.md`, its `scripts/chemqa_artifact_flow.py`, and its
artifact-flow test. Twenty top-level test files and fifteen historical/current
documents contain references. No tracked `scripts/` entrypoint matched. These
counts exclude ignored environments, release wheels, bytecode and generated run
content; live run metadata is checked separately below.

## Dashboard Selectors

The running API at `http://127.0.0.1:8765/api/runs` returns 82 visible runs.
Twenty completed historical runs expose legacy facets, all beneath
`state/benchmark-runs/temporary/`. Including hidden runs yields the same counts.

| Dataset still available | Subset values still available |
| --- | --- |
| `chembench` | `chembench` |
| `frontierscience` | `frontierscience_Olympiad`, `frontierscience_Research` |
| `hle` | `hle_chemistry` |
| `superchem` | `superchem_multimodal` |

There are no hard-coded old names in the dashboard source. The actual flow is:

1. `dashboard/service.py:66` normalizes VGB display names and returns other
   persisted dataset/subset pairs unchanged.
2. `dashboard/service.py:370` derives each run's facets from its saved records.
3. `dashboard/static/app.js:157` builds Dataset from all loaded run facets and
   Subset from the runs matching the selected Dataset. The HTML at
   `dashboard/static/index.html:28` defines the two select controls.

Examples include the September `infra-superchem`/`infra-multimodal` acceptance
runs and May `temporary/chembench`, `temporary/frontierscience` and mixed runs.
Deleting formal/temp input data does not remove these saved run records, so it
does not remove their dashboard options. This is consistent with the original
requirement to preserve historical read support. A VGB-only default selector or
separate historical view would be an additional display-policy change; deleting
run evidence is not needed to implement such a policy.

## Verification And Limits

- Focused service-boundary, evaluator and dashboard tests: 70 passed.
- Live API metadata and frontend option-generation code were inspected. No
  browser screenshot or real-model/Docker attempt was performed in this scan.
- The HLE probes use the current public dataset schemas and pure local answer
  checks; no verifier execution or model call was needed.
- Existing tests pass despite the reproduced HLE defect; they are not evidence
  that the active answer-format boundary is complete.
- Production code, exposed skill content, dashboard policy, and run evidence
  were not edited or removed by this scan. Changes are documentation and
  generated audit artifacts only.

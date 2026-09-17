# VGB Runtime Cleanup Implementation

Date: 2026-09-17

Scope: active VGB runtime composition and frozen ChemQA preservation.

Status: source cleanup and validation complete (Phases 0-2 and 4). Phase 3 local
input deletion is pending a separate user decision.

Evidence baseline: clean Git HEAD `271368429cdb897ecdb54593e353fcf17cbbb394`.
Initial full suite: 1001 passed, 11 skipped, 164 subtests passed.

This report executes the source-boundary work proposed in the
[cleanup handoff](2026-09-17-vgb-runtime-legacy-facility-cleanup-report.md).
The original report remains historical evidence. The user's request authorized
implementation; no additional approval was requested for reversible source work.

## Implemented Boundaries

- Active discovery uses the pinned release's four datasets. All loaded records
  are validated before filtering: eval kind, release, dataset/track, and task ID.
  Explicit files use the existing `<dataset>/data/<file>.jsonl` layout and pass
  the same validation. The canonical directory mapping is checked against release
  inventory; formal/temporary classification and mixed VGB runs are retained.
- The active invocation has its own verifier-only evaluator table. It does not
  import old evaluators eagerly, use semantic fallback, or create a judge runtime.
  Run configs contain only the selected single agent. Frozen ChemQA owns the old
  subset/sampling/judge arguments and explicitly composes historical evaluators.
- Single prompts, rescue prompts, and candidate contracts retain VGB behavior;
  the runner rejects old eval kinds. Both backends retain attempt environments,
  dependency evidence, retries, cancellation, and workspace audit. The adapter
  does not materialize dataset input bundles; path projection remains shared.
- VGB-only automated analysis uses scored verifier averages even when some
  records fail. Shared historical scoring, bundles, answer helpers, dashboard
  readers, and ChemQA analysis remain intact.
- Frozen source and all three skill bundles remain at their original paths.
  Persisted identifiers, artifact filenames, distribution name, extras, and
  historical documents are retained. Mixed tests now distinguish active
  single-LLM lifecycle coverage from frozen/shared contracts. Only obsolete
  active prompt/research-format cases were removed; frozen tests were retained.

## Preflight Evidence

Raw artifacts are under
`state/benchmark-runs/temporary/vgb-runtime-cleanup/no-model/vgb-runtime-cleanup-no-model-20260917/`:

- `preflight.json`: HEAD, clean-worktree evidence, tracked files, 144 run
  manifests, formal/temp input inventory, per-file SHA-256, frozen import graph,
  process inventory, and Docker inventory.
- `state-check.json`: no active benchmark entrypoint/wrapper processes, active
  progress records, or cleanroom leases. Three workspace sentinels are in the
  pre-existing quarantine tree; all were retained. The unrelated review-system
  Docker container was retained.
- `acceptance.json`: one CLI selection and one real pinned isolated verifier
  evaluation per track. All four returned `scored` without a failure type. RDKit
  used benzene; xTB used a seeded RDKit/MMFF pyridine geometry; property tasks
  used finite zero-valued test answers. These are infrastructure checks, not
  benchmark model-quality measurements.

The frozen import graph retains shared datasets, scoring, judge, bundle,
orchestration, config, and cleanroom dependencies. Active imports are checked in
an independent Python process; invoking frozen registration in the same process
cannot change the active invocation's scoring table.

## Validation

- First frozen preservation run: 128 passed.
- Final frozen/shared, dashboard, and analysis preservation run: 205 passed,
  2 subtests passed.
- Four release tracks: selection and actual isolated scoring passed.
- Final `uv run pytest -q --tb=short`: 1004 passed, 11 skipped, 164 subtests
  passed in 79.22 seconds. `full-suite.xml` retains the machine-readable result.
  The five warnings are the existing SWIG deprecation warnings in paper parsing.
- Default discovery lists only four VGB datasets; all 105 formal records pass
  validation, and mixed selection resolves to the canonical `formal/mixed-datasets`
  hierarchy. The 85 exposed provider skills are retained; none of the three
  frozen orchestration bundles enters single-agent exposure.
- `git diff --check` passed. All 48 documentation catalog targets resolve, with
  no unlisted maintained document. The production name scan has no legacy
  dataset, subset-sampling, or judge-option hits in `service/single` or the CLI.
- All 1824 retained legacy input file hashes match the preflight inventory.

No real model calls or new Docker model attempts were launched. Docker behavior
is covered by the existing runtime regression suite; this is not fresh real-model
Docker acceptance. Existing skips are retained, with no new skip added.

## Retained Inputs

No dataset directories or historical runtime state were deleted. The exact
deletion candidates remain the four names `chembench`, `frontierscience`, `hle`,
and `superchem` under each of `/Users/xutao/.openclaw/data/formal-benchmarks` and
`/Users/xutao/.openclaw/data/temp-benchmarks`.

| Root | Files | Bytes |
| --- | ---: | ---: |
| formal-benchmarks | 1820 | 97544553 |
| temp-benchmarks | 4 | 983632 |
| Total | 1824 | 98528185 |

Before any later deletion, recheck the inventory and runtime activity. Removing
these inputs removes default local replay data for frozen ChemQA; preserving or
archiving them keeps that replay option. Active VGB discovery already excludes
them. External callers and all historical agent/log/database references were not
exhaustively audited; no runtime evidence or dashboard database is a deletion
candidate in this change.

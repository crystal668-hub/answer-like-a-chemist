# VGB Track-Only Identity Specification

- Date: 2026-09-17
- Status: IMPLEMENTED
- Scope: benchmark input identity, CLI selection, result schema, dashboard facets,
  and historical compatibility

## Identity Contract

The pinned `benchmarking/resources/verifier_grounded/release.json` Track table is
the only executable benchmark identity inventory. Its ordered keys are `rdkit`,
`xtb`, `property_calculation_advanced`, and `property_calculation_basic`.
Execution rejects every other Track before workspace allocation or scoring.

`BenchmarkRecord` and schema-v4 `GroupRecordResult` carry one `track` field.
Current records and APIs do not expose `dataset` or `subset`. Aggregates use
`by_track` and `group_track`.

## Selection And Storage

The canonical CLI uses `--tracks` and `--list-tracks`. Removed dataset/subset
flags have no compatibility aliases. Omitted `--tracks` requires all four pinned
Track files to be present; an explicit selection requires every selected Track.

Sanitized inputs use this exact layout:

```text
<benchmark-root>/<track>/data/<track>.jsonl
```

Each record must declare the same Track in `verifier_grounded.track`, match the
pinned release identity, and use a task ID owned by that Track. Checked-in
snapshots live under `benchmarking/resources/verifier_grounded/tracks/`.

## Result And Dashboard Contract

New per-record and aggregate results use schema version 4. Run metadata uses
`track_files`; summaries use `by_track` and `group_track`. Single-Track run
directories retain the established `vgb-*` slugs; multi-Track runs use
`mixed-tracks`.

The dashboard obtains its selector options directly from the pinned Track table,
so the four options are present independently of scanned run contents. Run,
record, search, and detail projections expose only Track identity.

## Historical Compatibility

Historical artifacts are immutable. The read adapter resolves identity in this
order: existing canonical `track`, pinned task ID, known VGB dataset/subset alias,
then known source-path alias. Other historical identities become
`legacy:<identifier>`. Legacy values remain readable but are neither executable
nor added to the dashboard selector.

Historical replay writes schema-v4 replacements only after preserving the
original snapshot. The current output drops historical `dataset` and `subset`
keys while the snapshot retains the original evidence bytes.

## Layout Migration

The migration stages all four canonical directories, validates Track, release
identity, task order, and task count, and then promotes them. The four exact
legacy `verifier_grounded_*` directories are deleted only after all canonical
targets pass validation. Any earlier failure leaves every legacy directory
untouched.

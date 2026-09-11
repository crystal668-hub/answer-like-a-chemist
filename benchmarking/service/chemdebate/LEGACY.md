# ChemDebate / ChemQA — legacy, frozen

This service is deprecated and excluded from the default benchmark catalog.
No new features, model adaptations, protocol changes, or future OpenClaw
compatibility updates are planned. Explicit historical execution uses:

```sh
uv run python -m benchmarking.service.chemdebate.cli
```

It owns the fixed-lane runner, six-role workspace lifecycle, preset/profile and
slot configuration, Artifact Flow reconstruction, status polling, and cleanroom
integration. The dependencies `skills/chemqa-review`, `skills/debateclaw-v1`, and
`skills/benchmark-cleanroom` are frozen with it. Their physical paths remain
stable for script and persisted-artifact compatibility.

Persisted `chemqa`, `chemqa_skills_on`, artifact filenames, historical results,
and read-only dashboard/analysis support are retained. Provider skills, shared
scoring, and the project distribution name `chemqa` are not deprecated.

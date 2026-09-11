# Benchmark-managed TOOLS.md

Read the selected skill's `SKILL.md` for its script path, inputs, and CLI arguments.
If it needs input files, write them with a structured file tool under
`scratch/requests/`.

Invoke its script with `exec`:

`cd "$BENCHMARK_SKILL_SCRATCH_DIR" && mkdir -p "outputs/OUTPUT_NAME" && python "$BENCHMARK_SKILL_RUNNER" --workspace-root "$BENCHMARK_PROJECT_ROOT" --execution-cwd "$BENCHMARK_SKILL_SCRATCH_DIR" --script SCRIPT_PATH -- SCRIPT_ARGS`

Replace only `OUTPUT_NAME`, `SCRIPT_PATH` (project-relative `skills/.../scripts/...py`),
and `SCRIPT_ARGS`; keep the `$BENCHMARK_*` environment variables intact.
Arguments after `--` belong to the selected script. For scripts that document
them, use `--request-json "requests/REQUEST_NAME.json" --output-dir "outputs/OUTPUT_NAME" --json`;
other scripts may require different arguments, such as `--query`.
These paths are relative to scratch after `cd`, so omit the `scratch/` prefix.
The wrapper selects the injected attempt Python when available, otherwise the
workspace `uv` environment.

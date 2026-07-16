# Benchmark-managed TOOLS.md

Run local skill scripts through this two-step OpenClaw tool-call pattern.

1. Use a structured file tool to write request JSON to `scratch/requests/<name>.json`.
2. Use `exec` with this command after replacing every uppercase placeholder:

`cd "$BENCHMARK_SKILL_SCRATCH_DIR" && mkdir -p "outputs/OUTPUT_NAME" && python "$BENCHMARK_SKILL_RUNNER" --workspace-root "$BENCHMARK_PROJECT_ROOT" --execution-cwd "$BENCHMARK_SKILL_SCRATCH_DIR" --script SCRIPT_PATH -- --request-json "requests/REQUEST_NAME.json" --output-dir "outputs/OUTPUT_NAME" --json`

Use `scratch/outputs` for outputs, `scratch/notes` for notes, and `scratch/tmp` for temporary scripts needed by the current calculation. Do not execute canonical `skills/...` source directly or invent tool names.

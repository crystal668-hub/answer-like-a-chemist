# Benchmark-managed TOOLS.md

Run local skill scripts through this two-step OpenClaw tool-call pattern.

1. Write request JSON under `$BENCHMARK_SKILL_REQUEST_DIR`.
2. Use `exec` with this command after replacing every uppercase placeholder:

`python /Users/xutao/.openclaw/workspace/scripts/run_skill.py --workspace-root /Users/xutao/.openclaw/workspace --execution-cwd "$BENCHMARK_SKILL_SCRATCH_DIR" --script SCRIPT_PATH -- --request-json REQUEST_JSON_PATH --output-dir OUTPUT_DIR --json`

Use `$BENCHMARK_SKILL_OUTPUT_DIR` for outputs and `$BENCHMARK_SKILL_NOTES_DIR` for notes. Do not write exploration artifacts in the workspace root. Do not use direct `python skills/...`, pipes, redirects, inline Python, temporary runner scripts, or invented tool names. If a verification target gets two usage, request-shape, or tool errors, mark it blocked and continue.

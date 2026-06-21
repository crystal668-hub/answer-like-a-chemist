# xtb-cli Contracts

## Shared CLI Contract

Run through the workspace skill runner:

```bash
python /Users/xutao/.openclaw/workspace/scripts/run_skill.py \
  --workspace-root /Users/xutao/.openclaw/workspace \
  --execution-cwd "$PWD" \
  --script skills/xtb-cli/scripts/xtb_runner.py -- \
  --request-json /path/to/request.json \
  --output-dir /tmp/xtb-cli-out \
  --json
```

Rules:

- `--request-json` is the canonical input file.
- `--output-dir` is required and created if missing.
- `--json` prints the same payload written to `result.json`.
- The runner returns structured JSON for request, provider, and parser failures instead of using process failure as the main error channel.

## Request Shape

Provide either inline XYZ text or a local XYZ file path:

```json
{
  "geometry_xyz": "3\nwater\nO 0 0 0\nH 0 0 1\nH 1 0 0\n",
  "run_type": "single_point",
  "gfn": 2,
  "charge": 0,
  "uhf": 0,
  "solvent_model": "alpb",
  "solvent": "water",
  "timeout_seconds": 60,
  "write_json_control": false
}
```

Fields:

- `geometry_xyz`: inline XYZ geometry.
- `geometry_path`: local path to an XYZ geometry. Used only when `geometry_xyz` is absent.
- `run_type`: `single_point`, `opt`, `hess`, `ohess`, `vomega`, or `vfukui`. Defaults to `single_point`.
- `gfn`: integer method selector `0`, `1`, or `2`. Defaults to `2`.
- `charge`: integer molecular charge. Defaults to `0`.
- `uhf`: integer number of unpaired electrons. Defaults to `0`.
- `solvent_model`: optional `alpb` or `gbsa`.
- `solvent`: solvent name required when `solvent_model` is set.
- `timeout_seconds`: positive command timeout. Defaults to `60`.
- `write_json_control`: boolean. When true, writes a minimal xcontrol file requesting xTB JSON output.

## Output Shape

```json
{
  "status": "success|error",
  "request": {},
  "primary_result": {},
  "candidates": [],
  "diagnostics": [],
  "warnings": [],
  "errors": [],
  "tool_trace": [],
  "source_trace": [],
  "provider_health": {}
}
```

`primary_result` includes:

- `run_type`, `method`, `gfn`, `charge`, `uhf`
- `command`, `returncode`, `timeout_seconds`, `elapsed_ms`
- `stdout_excerpt`, `stderr_excerpt`
- `normal_termination`
- parsed scalar fields when found: `total_energy_Eh`, `homo_energy_Eh`, `lumo_energy_Eh`, `homo_lumo_gap_eV`, `dipole_Debye`, `polarizability_au`, `gsolv_Eh`, `imaginary_frequency_count`, `global_electrophilicity_eV`
- `optimized_geometry_xyz` when `xtbopt.xyz` is produced
- `xtb_json` when xTB writes valid JSON
- `artifacts`, including the temporary working directory and generated artifact paths

## Failure Semantics

- Invalid request JSON, unsupported run types, invalid solvent fields, invalid XYZ, and missing geometry return `status: "error"` with an entry under `errors`.
- Missing `xtb` returns `provider_health.xtb-cli.status = "missing_executable"` and `status: "error"`.
- Non-zero xTB exits return `status: "error"` while preserving stdout/stderr excerpts, return code, command, and artifacts.
- Parser misses add warnings when appropriate; raw output excerpts remain available for version-specific inspection.

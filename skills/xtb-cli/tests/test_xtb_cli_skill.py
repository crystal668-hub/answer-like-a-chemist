from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills" / "xtb-cli"
SCRIPT_PATH = SKILL_ROOT / "scripts" / "xtb_runner.py"


WATER_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.758602 0.000000 0.504284
H -0.758602 0.000000 0.504284
"""


def run_xtb_runner(request: dict, *, env: dict[str, str] | None = None) -> tuple[dict, Path, subprocess.CompletedProcess[str]]:
    temp_path = Path(tempfile.mkdtemp(prefix="xtb-cli-test-"))
    request_path = temp_path / "request.json"
    output_dir = temp_path / "out"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--request-json",
            str(request_path),
            "--output-dir",
            str(output_dir),
            "--json",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(completed.stdout or "{}")
    result_path = output_dir / "result.json"
    if result_path.exists():
        disk_payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload == disk_payload
    return payload, result_path, completed


def fake_xtb_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_xtb = bin_dir / "xtb"
    fake_xtb.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            from pathlib import Path
            import json
            import sys

            if "--version" in sys.argv:
                print("   * xtb version 6.7.1 (fake-test)")
                print("normal termination of xtb")
                raise SystemExit(0)

            Path("xtbopt.xyz").write_text("3\\nfake optimized\\nO 0 0 0\\nH 0 0 1\\nH 1 0 0\\n", encoding="utf-8")
            if "xcontrol.inp" in sys.argv:
                Path("xtbout.json").write_text(json.dumps({"fake": True}), encoding="utf-8")
            print("TOTAL ENERGY              -5.000000000000 Eh")
            print("         4        2.0000           -0.4465085             -12.1501 (HOMO)")
            print("         5                          0.1534904               4.1767 (LUMO)")
            print("HOMO-LUMO GAP             5.4423 eV")
            print("molecular dipole:")
            print("  total     1.234 Debye")
            print("Mol. C8AA polarizability /au  4.560")
            print("Gsolv      -0.010000 Eh")
            print("number of imaginary frequencies: 0")
            print("normal termination of xtb", file=sys.stderr)
            """
        ),
        encoding="utf-8",
    )
    fake_xtb.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


class XtbCliSkillTests(unittest.TestCase):
    def test_skill_layout_files_exist(self) -> None:
        expected = [
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "references" / "contracts.md",
            SCRIPT_PATH,
        ]
        for path in expected:
            self.assertTrue(path.is_file(), f"missing required file: {path}")

    def test_skill_docs_are_self_contained_without_host_absolute_paths(self) -> None:
        forbidden_fragments = ("/Users/", ".openclaw", "/opt/homebrew/")
        docs = [
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "references" / "contracts.md",
        ]

        for path in docs:
            text = path.read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, text, f"{path} contains host-specific path fragment {fragment!r}")

    def test_invalid_request_returns_structured_error_and_writes_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = fake_xtb_env(Path(tmpdir))
            payload, result_path, completed = run_xtb_runner({"run_type": "single_point"}, env=env)

        self.assertEqual(0, completed.returncode)
        self.assertTrue(result_path.is_file())
        self.assertEqual("error", payload["status"])
        self.assertEqual("missing_geometry", payload["errors"][0]["code"])

    def test_missing_xtb_executable_returns_provider_health_error(self) -> None:
        env = os.environ.copy()
        env["PATH"] = tempfile.mkdtemp(prefix="xtb-cli-empty-path-")

        payload, _, completed = run_xtb_runner({"geometry_xyz": WATER_XYZ, "run_type": "single_point"}, env=env)

        self.assertEqual(0, completed.returncode)
        self.assertEqual("error", payload["status"])
        self.assertEqual("missing_executable", payload["errors"][0]["code"])
        self.assertEqual("missing_executable", payload["provider_health"]["xtb-cli"]["status"])

    def test_fake_single_point_run_builds_command_and_parses_properties(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = fake_xtb_env(Path(tmpdir))
            payload, result_path, completed = run_xtb_runner(
                {
                    "geometry_xyz": WATER_XYZ,
                    "run_type": "single_point",
                    "gfn": 2,
                    "charge": 0,
                    "uhf": 0,
                    "solvent_model": "alpb",
                    "solvent": "water",
                    "write_json_control": True,
                },
                env=env,
            )

        self.assertEqual(0, completed.returncode)
        self.assertTrue(result_path.is_file())
        self.assertEqual("success", payload["status"])
        self.assertTrue(payload["provider_health"]["xtb-cli"]["available"])
        self.assertIn("6.7.1", payload["provider_health"]["xtb-cli"]["version"])
        primary = payload["primary_result"]
        self.assertEqual(-5.0, primary["total_energy_Eh"])
        self.assertEqual(-0.4465085, primary["homo_energy_Eh"])
        self.assertEqual(0.1534904, primary["lumo_energy_Eh"])
        self.assertEqual(5.4423, primary["homo_lumo_gap_eV"])
        self.assertEqual(1.234, primary["dipole_Debye"])
        self.assertEqual(4.56, primary["polarizability_au"])
        self.assertEqual(-0.01, primary["gsolv_Eh"])
        self.assertEqual(0, primary["imaginary_frequency_count"])
        command = primary["command"]
        self.assertIn("--gfn", command)
        self.assertIn("--chrg", command)
        self.assertIn("--uhf", command)
        self.assertIn("--alpb", command)
        self.assertEqual("single_point", primary["run_type"])
        self.assertTrue(primary["normal_termination"])
        self.assertTrue(Path(primary["artifacts"]["working_directory"]).is_dir())
        self.assertTrue(Path(primary["artifacts"]["candidate.xyz"]).is_file())

    def test_fake_optimization_captures_optimized_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = fake_xtb_env(Path(tmpdir))
            payload, _, completed = run_xtb_runner(
                {"geometry_xyz": WATER_XYZ, "run_type": "opt", "gfn": 2, "charge": 0, "uhf": 0},
                env=env,
            )

        self.assertEqual(0, completed.returncode)
        self.assertEqual("success", payload["status"])
        primary = payload["primary_result"]
        self.assertIn("--opt", primary["command"])
        self.assertEqual("fake optimized", primary["optimized_geometry_xyz"].splitlines()[1])

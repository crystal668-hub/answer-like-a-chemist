from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ServiceScriptTests(unittest.TestCase):
    def test_docker_services_only_manages_grobid(self) -> None:
        script = (ROOT / "scripts" / "docker_services.sh").read_text(encoding="utf-8")

        self.assertIn("GROBID_DIR=", script)
        self.assertIn('wait_for_http "grobid" "http://127.0.0.1:8070/api/isalive"', script)
        self.assertNotIn("MINERU_DIR=", script)
        self.assertNotIn("mineru-api-docker", script)
        self.assertNotIn("run_compose \"$MINERU_DIR\"", script)

    def test_local_mineru_service_script_defines_expected_commands(self) -> None:
        script_path = ROOT / "scripts" / "mineru_service.sh"
        self.assertTrue(script_path.exists())
        script = script_path.read_text(encoding="utf-8")

        for command in ["install", "download-models", "up", "down", "restart", "ps", "logs", "health"]:
            self.assertIn(command, script)

        self.assertIn("VENV_BIN=", script)
        self.assertIn('pip install -U "mineru[all]"', script)
        self.assertIn("mineru-models-download", script)
        self.assertIn("mineru-api", script)
        self.assertIn("--host", script)
        self.assertIn("127.0.0.1", script)
        self.assertIn("--port", script)
        self.assertIn("8000", script)
        self.assertIn("--enable-vlm-preload", script)
        self.assertIn("MINERU_API_URL", script)
        self.assertIn("MINERU_MODEL_SOURCE", script)
        self.assertIn("local", script)
        self.assertIn("MINERU_DOWNLOAD_SOURCE", script)
        self.assertIn("--source", script)
        self.assertIn("--model_type all", script)


if __name__ == "__main__":
    unittest.main()

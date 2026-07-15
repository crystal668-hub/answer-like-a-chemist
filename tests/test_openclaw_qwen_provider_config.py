import json
import unittest

from scripts.sync_openclaw_qwen_provider import (
    QWEN_MODEL_IDS,
    TOKEN_PLAN_BASE_URL,
    sync_config_payload,
    sync_models_payload,
)


class OpenClawQwenProviderConfigTests(unittest.TestCase):
    def test_sync_config_payload_adds_new_qwen_models_and_default_aliases(self) -> None:
        payload = {
            "models": {
                "providers": {
                    "qwen": {
                        "baseUrl": "${QWEN_BASE_URL}",
                        "apiKey": {
                            "source": "env",
                            "provider": "default",
                            "id": "QWEN_API_KEY",
                        },
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "qwen3.6-plus",
                                "name": "qwen3.6-plus",
                                "reasoning": True,
                                "input": ["text"],
                                "cost": {
                                    "input": 0,
                                    "output": 0,
                                    "cacheRead": 0,
                                    "cacheWrite": 0,
                                },
                                "contextWindow": 1000000,
                                "maxTokens": 65536,
                                "api": "openai-completions",
                                "compat": {"thinkingFormat": "qwen"},
                            }
                        ],
                    }
                }
            },
            "agents": {
                "defaults": {
                    "models": {
                        "qwen/qwen3.6-plus": {
                            "alias": "qwen3.6-plus",
                        }
                    }
                }
            },
        }

        changed = sync_config_payload(payload)

        self.assertTrue(changed)
        qwen = payload["models"]["providers"]["qwen"]
        models = {entry["id"]: entry for entry in qwen["models"]}
        self.assertEqual(set(QWEN_MODEL_IDS), set(models))
        for model_id in QWEN_MODEL_IDS:
            with self.subTest(model_id=model_id):
                self.assertEqual(model_id, models[model_id]["name"])
                self.assertIs(True, models[model_id]["reasoning"])
                self.assertEqual(["text"], models[model_id]["input"])
                self.assertEqual(1000000, models[model_id]["contextWindow"])
                self.assertEqual(65536, models[model_id]["maxTokens"])
                self.assertEqual("openai-completions", models[model_id]["api"])
                self.assertEqual({"thinkingFormat": "qwen"}, models[model_id]["compat"])
                self.assertEqual({"alias": model_id}, payload["agents"]["defaults"]["models"][f"qwen/{model_id}"])

        self.assertFalse(sync_config_payload(payload))

    def test_sync_config_payload_reports_change_when_provider_metadata_is_missing(self) -> None:
        payload = {
            "models": {
                "providers": {
                    "qwen": {
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": model_id,
                                "name": model_id,
                                "reasoning": True,
                                "input": ["text"],
                                "cost": {
                                    "input": 0,
                                    "output": 0,
                                    "cacheRead": 0,
                                    "cacheWrite": 0,
                                },
                                "contextWindow": 1000000,
                                "maxTokens": 65536,
                                "api": "openai-completions",
                                "compat": {"thinkingFormat": "qwen"},
                            }
                            for model_id in QWEN_MODEL_IDS
                        ],
                    }
                }
            },
            "agents": {"defaults": {"models": {f"qwen/{model_id}": {"alias": model_id} for model_id in QWEN_MODEL_IDS}}},
        }

        changed = sync_config_payload(payload)

        self.assertTrue(changed)
        qwen = payload["models"]["providers"]["qwen"]
        self.assertEqual("${QWEN_BASE_URL}", qwen["baseUrl"])
        self.assertEqual(
            {"source": "env", "provider": "default", "id": "QWEN_API_KEY"},
            qwen["apiKey"],
        )
        self.assertEqual("openai-completions", qwen["api"])

    def test_sync_config_payload_replaces_literal_provider_credentials_with_env_refs(self) -> None:
        payload = {
            "models": {
                "providers": {
                    "qwen": {
                        "baseUrl": TOKEN_PLAN_BASE_URL,
                        "apiKey": "stale-token",
                        "api": "openai-completions",
                        "models": [],
                    }
                }
            },
            "agents": {"defaults": {"models": {}}},
        }

        changed = sync_config_payload(payload)

        self.assertTrue(changed)
        qwen = payload["models"]["providers"]["qwen"]
        self.assertEqual("${QWEN_BASE_URL}", qwen["baseUrl"])
        self.assertEqual(
            {"source": "env", "provider": "default", "id": "QWEN_API_KEY"},
            qwen["apiKey"],
        )

    def test_sync_models_payload_clears_token_plan_qwen_provider_cache(self) -> None:
        payload = {
            "providers": {
                "qwen": {
                    "baseUrl": TOKEN_PLAN_BASE_URL,
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "qwen3.6-plus",
                            "name": "qwen3.6-plus",
                            "reasoning": True,
                            "input": ["text"],
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                            "contextWindow": 1000000,
                            "maxTokens": 65536,
                            "api": "openai-completions",
                            "compat": {"thinkingFormat": "qwen"},
                        }
                    ],
                    "apiKey": "QWEN_API_KEY",
                }
            }
        }

        changed = sync_models_payload(payload)

        self.assertTrue(changed)
        self.assertNotIn("qwen", payload["providers"])
        self.assertFalse(sync_models_payload(payload))

    def test_sync_models_payload_skips_non_token_plan_qwen_provider(self) -> None:
        payload = {
            "providers": {
                "qwen": {
                    "baseUrl": "https://coding-intl.dashscope.aliyuncs.com/v1",
                    "api": "openai-completions",
                    "models": [{"id": "qwen3.5-plus", "name": "qwen3.5-plus"}],
                    "apiKey": "QWEN_API_KEY",
                }
            }
        }
        before = json.loads(json.dumps(payload))

        changed = sync_models_payload(payload)

        self.assertFalse(changed)
        self.assertEqual(before, payload)

    def test_sync_models_payload_can_clear_explicit_agent_override(self) -> None:
        payload = {
            "providers": {
                "qwen": {
                    "baseUrl": "https://example.invalid/v1",
                    "apiKey": "stale-token",
                    "models": [],
                }
            }
        }

        changed = sync_models_payload(payload, clear_all=True)

        self.assertTrue(changed)
        self.assertNotIn("qwen", payload["providers"])


if __name__ == "__main__":
    unittest.main()

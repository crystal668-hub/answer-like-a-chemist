from pathlib import Path

from benchmarking.core.finalization_context import (
    build_finalization_context_bundle,
    write_finalization_context_bundle,
)


def test_context_bundle_is_bounded_and_redacts_sensitive_values(tmp_path: Path) -> None:
    snapshot = tmp_path / "primary.jsonl"
    snapshot.write_text(
        '{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"answer /Users/alice/private"}]}}\n'
        '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"ignored"}]}}\n',
        encoding="utf-8",
    )
    bundle = build_finalization_context_bundle(
        snapshot,
        source_session_id="primary",
        eval_kind="hle",
        answer_schema={"token": "secret"},
        original_task="Question",
        primary_native_output="partial",
        max_chars=2000,
    )
    assert bundle.source_snapshot_sha256
    rendered = bundle.prompt_projection()
    assert "[path omitted]" in rendered
    assert "secret" not in rendered
    output = tmp_path / "scratch" / "notes" / "finalization-rescue-context.json"
    write_finalization_context_bundle(bundle, output)
    assert output.is_file()

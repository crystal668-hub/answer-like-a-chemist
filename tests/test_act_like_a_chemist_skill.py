from __future__ import annotations

from pathlib import Path


SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "act-like-a-chemist" / "SKILL.md"


def test_act_like_a_chemist_defines_atomic_checklist_contract() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "Atomic Coverage Checklist" in text
    assert "atomic task" in text
    assert "known givens" in text
    assert "Re-check every `blocked` atom" in text
    assert "scoped evidence" in text
    assert "does not override prompt constraints" in text

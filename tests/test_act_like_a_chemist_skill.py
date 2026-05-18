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


def test_act_like_a_chemist_guides_enumeration_by_constraints_first() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "enumeration" in text
    assert "deterministic prompt constraints" in text
    assert "narrow the candidate set" in text
    assert "Do not enumerate every possible candidate first" in text

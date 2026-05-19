from __future__ import annotations

import re
from pathlib import Path


SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "act-like-a-chemist" / "SKILL.md"
TRIGGER_RULES_PATH = SKILL_PATH.parent / "contract" / "skill-triggers.md"


def test_act_like_a_chemist_defines_atomic_checklist_contract() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "Atomic Coverage Checklist" in text
    assert "atomic task" in text
    assert "known givens" in text
    assert "Re-check every `blocked` atom" in text
    assert "scoped evidence" in text
    assert "Treat tool results as evidence, not verdicts" in text
    assert "A database hit, formula match, approximate numeric match, valid structure, or retrieved source" in text
    assert "Organize all atomic reasoning steps into a smooth, complete reasoning trace" in text
    assert "preserve all verified visible checkpoints" in text
    assert "将所有 atomic 推理步骤梳理为一个流畅的、完整的推理轨迹" not in text
    assert "End in the exact requested format while preserving the visible checkpoints that justify it." not in text


def test_act_like_a_chemist_guides_enumeration_by_constraints_first() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "enumeration" in text
    assert "deterministic prompt constraints" in text
    assert "narrow the candidate set" in text
    assert "Do not enumerate every possible candidate first" in text


def test_act_like_a_chemist_avoids_topic_specific_sop_sections() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "## Organic Mechanism SOP" not in text


def test_act_like_a_chemist_links_provider_trigger_rules_contract() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "## Mandatory Verification Triggers" not in text
    assert "contract/skill-triggers.md" in text
    assert "Choose only the skills needed by referring to `contract/skill-triggers.md`" in text
    assert "Before each tool call, name the exact atom it should inform and the expected output shape" in text


def test_provider_skill_trigger_rules_define_layered_routing_contract() -> None:
    text = TRIGGER_RULES_PATH.read_text(encoding="utf-8")

    assert "# Provider Skill Trigger Rules" in text
    assert "## Purpose" in text
    assert "## Capability Need First" in text
    assert "## Primary Before Specialized" in text
    assert "## Capability Routing Matrix" in text
    assert "Mandatory Verification Triggers" not in text
    assert "Every tool call must target a concrete Atomic Coverage Checklist atom" in text
    assert "An unexecuted skill is not evidence" in text
    assert not re.search(r"[\u4e00-\u9fff]", text)

    for domain in (
        "numeric calculation",
        "molecular structure",
        "literature",
        "materials database",
        "spectra",
        "protein",
        "MD",
        "HPC",
        "ML",
        "drug safety",
    ):
        assert domain in text

    for skill in (
        "chem-calculator",
        "rdkit",
        "opsin",
        "pubchem",
        "paper-retrieval",
        "pymatgen",
        "spectral-analysis",
        "pdb-database",
        "molecular-dynamics",
        "hpc-gaussian",
        "matminer",
        "chembl-database",
        "tooluniverse-chemical-safety",
    ):
        assert skill in text

    for runtime_skill in ("benchmark-cleanroom", "debateclaw-v1", "chemqa-review"):
        assert runtime_skill in text

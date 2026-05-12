from __future__ import annotations

from benchmarking.skills.tree import (
    benchmark_skill_allowlist,
    load_chemistry_skill_inventory,
    load_skill_tree,
    lookup_skill_family,
    render_top_level_skill_tree,
)


def _all_tree_skills() -> list[str]:
    skills: list[str] = []
    for domain in load_skill_tree():
        for family in domain["families"]:
            skills.extend(str(skill) for skill in family["skills"])
    return skills


def test_benchmark_skill_allowlist_includes_all_matrix_skills_and_paper_pipeline() -> None:
    inventory = load_chemistry_skill_inventory()
    allowlist = benchmark_skill_allowlist()

    assert len(allowlist) == 85
    assert len(allowlist) == len(set(allowlist))
    assert allowlist == tuple(str(entry["skill"]) for entry in inventory["skills"])
    assert "act-like-a-chemist" in allowlist
    assert {"paper-retrieval", "paper-access", "paper-parse", "paper-rerank"} <= set(allowlist)
    assert {"chem-calculator", "rdkit", "opsin", "pubchem"} <= set(allowlist)


def test_skill_tree_covers_every_allowlisted_skill() -> None:
    allowlist = set(benchmark_skill_allowlist())
    tree_skills = _all_tree_skills()

    assert allowlist <= set(tree_skills)
    assert not (set(tree_skills) - allowlist)


def test_skill_tree_has_three_layers_and_paper_pipeline_family() -> None:
    tree = load_skill_tree()

    assert tree[0]["id"] == "benchmark-solving-protocol"
    sop_family = lookup_skill_family("chemistry-reasoning-sop")
    assert sop_family["id"] == "chemistry-reasoning-sop"
    assert sop_family["skills"] == ("act-like-a-chemist",)
    assert any(domain["id"] == "literature-evidence" for domain in tree)
    family = lookup_skill_family("paper-pipeline")
    assert family["id"] == "paper-pipeline"
    assert family["skills"] == ("paper-retrieval", "paper-access", "paper-rerank", "paper-parse")


def test_top_level_skill_tree_is_compact_and_not_a_router() -> None:
    rendered = render_top_level_skill_tree()

    assert "Skill capability tree" in rendered
    assert "Read `act-like-a-chemist` first" in rendered
    assert "First choose a capability domain" in rendered
    assert "benchmark-solving-protocol" in rendered
    assert "chemistry-reasoning-sop" in rendered
    assert "paper-pipeline" in rendered
    assert "literature-evidence" in rendered
    assert "--workspace-root /Users/xutao/.openclaw/workspace" in rendered
    assert "--execution-cwd \"$PWD\"" in rendered
    assert "--script skills/<skill>/scripts/<script>.py --" in rendered
    assert "fact ledger" not in rendered
    assert "Organic mechanism SOP" not in rendered
    assert "Experimental chemistry skill routing rules" not in rendered
    assert "first matching primary route" not in rendered
    assert "selected skill route" not in rendered.lower()
    assert "SKILL TRACE: skipped" not in rendered
    assert "If you skip" not in rendered
    assert "find run_skill" not in rendered.lower()
    assert "python3 <skill-root>" not in rendered
    assert "`chem-calculator`" not in rendered
    assert "`rdkit`" not in rendered
    assert "`paper-retrieval`" not in rendered
    assert len(rendered.splitlines()) < 60


def test_top_level_skill_tree_reflects_health_filtered_availability() -> None:
    rendered = render_top_level_skill_tree(available_skills={"act-like-a-chemist", "rdkit", "paper-access"})

    assert "Only health-checked skills in this run are available" in rendered
    assert "benchmark-solving-protocol" in rendered
    assert "molecular-structure-identity" in rendered
    assert "literature-evidence" in rendered

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"


EXPECTED_EXPERIMENTAL_SKILLS = {
    "pymatgen",
    "ase",
    "cclib",
    "datamol",
    "molfeat",
    "chembl-database",
    "zinc-database",
    "materials-project",
    "cod",
    "oqmd",
    "jarvis",
    "cccbdb",
    "molssi-qca",
    "molecular-dynamics",
    "openmm",
    "open-forcefield-toolkit",
    "tooluniverse-chemical-safety",
    "tooluniverse-small-molecule-discovery",
    "tooluniverse-chemical-compound-retrieval",
    "hpc-orca",
    "hpc-pyscf",
    "hpc-xtb",
    "hpc-vasp",
    "hpc-gaussian",
    "q-chem",
    "hpc-nwchem",
    "hpc-cp2k",
    "hpc-quantum-espresso",
    "qc-output-analysis",
    "spectral-analysis",
    "cif",
    "jcamp-dx",
    "cml",
    "crystal-viewer",
    "xtal2png",
    "doped-perovskite-structure-analysis",
    "mace",
    "chgnet",
    "mattersim",
    "mattergen",
    "diffcsp",
    "crystalflow",
    "chemprop",
    "schnet",
    "nequip",
    "matformer",
    "orb",
    "reann",
    "torchmd-net",
    "chemistry-query",
    "pubchem-database",
    "medchem",
    "atb",
    "pdb-database",
    "alphafold-database",
    "reactome-database",
    "pubmed-database",
    "openalex-database",
    "paper-retrieval",
    "paper-access",
    "paper-parse",
    "paper-rerank",
    "literature-review",
    "synthesize-literature",
    "matminer",
    "matbench",
    "modnet",
    "crabnet",
    "xenonpy",
    "optimade",
    "optimade-python-tools",
    "aiida",
    "atomate",
    "fireworks",
    "custodian",
    "quacc",
    "pyiron",
    "qmflows",
    "qmforge",
    "blue-obelisk",
}


def test_experimental_matrix_covers_selected_mid_plus_skills() -> None:
    from benchmarking.skills.tree import benchmark_skill_allowlist, load_chemistry_skill_inventory

    inventory = load_chemistry_skill_inventory()
    skill_names = set(benchmark_skill_allowlist())

    assert len(inventory["skills"]) == 85
    assert len(skill_names) == len(inventory["skills"])
    assert "act-like-a-chemist" in skill_names
    assert EXPECTED_EXPERIMENTAL_SKILLS <= skill_names
    assert {"rdkit", "opsin", "pubchem", "chem-calculator"} <= skill_names
    assert inventory["mode"] == "experimental_mid_plus"


def test_act_like_a_chemist_skill_bundle_is_installed() -> None:
    skill_root = SKILLS_ROOT / "act-like-a-chemist"

    assert (skill_root / "SKILL.md").is_file()


def test_selected_experimental_skills_are_installed_as_skill_bundles() -> None:
    for skill in EXPECTED_EXPERIMENTAL_SKILLS:
        skill_root = SKILLS_ROOT / skill
        assert (skill_root / "SKILL.md").is_file(), skill


def test_core_new_skill_wrappers_are_installed() -> None:
    expected_wrappers = {
        "cclib": "scripts/parse_output.py",
        "chembl-database": "scripts/bioactivity_query.py",
        "pymatgen": "scripts/structure_summary.py",
        "molecular-dynamics": "scripts/trajectory_summary.py",
        "open-forcefield-toolkit": "scripts/parameterize_molecule.py",
    }

    for skill, wrapper in expected_wrappers.items():
        assert (SKILLS_ROOT / skill / wrapper).is_file(), f"{skill}/{wrapper}"


def test_top_level_skill_tree_is_grouped_not_full_skill_docs() -> None:
    from benchmarking.skills.tree import render_top_level_skill_tree

    tree = render_top_level_skill_tree()

    for domain_or_family in (
        "benchmark-solving-protocol",
        "chemistry-reasoning-sop",
        "calculation-math",
        "molecular-structure-identity",
        "literature-evidence",
        "paper-pipeline",
        "materials-crystals",
        "quantum-hpc",
        "workflow-automation",
    ):
        assert domain_or_family in tree

    assert "Quick Start Guide" not in tree
    assert "fact ledger" not in tree
    assert "Organic mechanism SOP" not in tree
    assert "Core Workflow: OpenMM Simulation" not in tree
    assert "Installation and Setup" not in tree
    assert "first matching primary route" not in tree
    assert len(tree.splitlines()) < 60


def test_single_agent_prompt_injects_skill_tree() -> None:
    from benchmarking.core.datasets import BenchmarkRecord
    from benchmarking.workflow.prompts import build_single_llm_prompt

    record = BenchmarkRecord(
        record_id="route-cif",
        dataset="hle",
        source_file="/tmp/hle.jsonl",
        eval_kind="hle",
        prompt="What coordination polyhedra does this CIF crystal structure contain?",
        reference_answer="Al, Re2Al13",
    )

    prompt = build_single_llm_prompt(record, websearch_enabled=False)

    assert "Skill capability tree:" in prompt
    assert "Read `act-like-a-chemist` first" in prompt
    assert "materials-crystals" in prompt
    assert "paper-pipeline" in prompt
    assert "Organic mechanism SOP" not in prompt
    assert "Experimental chemistry skill routing rules" not in prompt
    assert "--workspace-root /Users/xutao/.openclaw/workspace" in prompt
    assert "--execution-cwd \"$PWD\"" in prompt
    assert "python3 <skill-root>" not in prompt


def test_single_agent_skills_off_prompt_does_not_expose_chemist_sop() -> None:
    from benchmarking.core.datasets import BenchmarkRecord
    from benchmarking.workflow.prompts import build_single_llm_prompt

    record = BenchmarkRecord(
        record_id="skills-off",
        dataset="chembench",
        source_file="/tmp/chembench.jsonl",
        eval_kind="chembench_open_ended",
        prompt="Calculate the pH.",
        reference_answer="7",
    )

    prompt = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=False)

    assert "Do not use OpenClaw skills" in prompt
    assert "act-like-a-chemist" not in prompt
    assert "Skill capability tree:" not in prompt
    assert "Organic mechanism SOP" not in prompt


def test_experimental_skill_dependencies_are_optional_and_scoped() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional = pyproject["project"]["optional-dependencies"]

    assert {
        "chem-materials",
        "chem-quantum-parse",
        "chem-bioactivity",
        "chem-md",
        "chem-cheminformatics-ml",
        "chem-materials-ml",
        "chem-workflows",
        "chem-experimental",
    } <= set(optional)

    expected_by_extra = {
        "chem-materials": {"pymatgen==2026.5.4", "mp-api==0.46.1", "ase==3.28.0"},
        "chem-quantum-parse": {"cclib==1.8.1"},
        "chem-bioactivity": {"chembl_webresource_client==0.10.9", "pubchempy==1.0.5"},
        "chem-md": {"openmm==8.5.1", "MDAnalysis==2.10.0"},
        "chem-cheminformatics-ml": {"datamol==0.12.5", "molfeat==0.11.0"},
        "chem-materials-ml": {"matminer==0.10.1", "jarvis-tools==2026.4.2"},
        "chem-workflows": {"custodian==2025.12.14", "fireworks==2.1.3", "quacc==1.2.6"},
    }

    for extra, dependencies in expected_by_extra.items():
        assert dependencies <= set(optional[extra])

    full = set(optional["full"])
    experimental = set(optional["chem-experimental"])
    assert "chemqa[chem-experimental]" not in full
    assert {
        "chemqa[chem-materials]",
        "chemqa[chem-quantum-parse]",
        "chemqa[chem-bioactivity]",
        "chemqa[chem-md]",
        "chemqa[chem-cheminformatics-ml]",
        "chemqa[chem-materials-ml]",
        "chemqa[chem-workflows]",
    } <= experimental

    all_optional_items = {dependency for dependencies in optional.values() for dependency in dependencies}
    assert not any("openff" in dependency.lower() for dependency in all_optional_items)

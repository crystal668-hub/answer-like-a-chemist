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
    from benchmarking.chemistry_routing import load_chemistry_routing_matrix

    matrix = load_chemistry_routing_matrix()
    skill_names = {entry["skill"] for entry in matrix["skills"]}

    assert len(matrix["skills"]) == 84
    assert len(skill_names) == len(matrix["skills"])
    assert EXPECTED_EXPERIMENTAL_SKILLS <= skill_names
    assert {"rdkit", "opsin", "pubchem", "chem-calculator"} <= skill_names
    assert matrix["mode"] == "experimental_mid_plus"


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


def test_compact_prompt_routing_is_grouped_not_full_skill_docs() -> None:
    from benchmarking.chemistry_routing import render_compact_skill_routing_table

    table = render_compact_skill_routing_table()

    for skill in (
        "rdkit",
        "pymatgen",
        "ase",
        "cclib",
        "chembl-database",
        "molecular-dynamics",
        "open-forcefield-toolkit",
        "tooluniverse-chemical-safety",
        "q-chem",
        "chgnet",
    ):
        assert f"`{skill}`" in table

    assert "Quick Start Guide" not in table
    assert "Core Workflow: OpenMM Simulation" not in table
    assert "Installation and Setup" not in table
    assert len(table.splitlines()) < 90


def test_route_scenarios_use_unique_primary_skills() -> None:
    from benchmarking.chemistry_routing import route_skill_for_text

    cases = {
        "Count NMR symmetry classes for the SMILES Cc1ccccc1.": "rdkit",
        "Determine coordination polyhedra from this CIF crystal structure.": "pymatgen",
        "Build an ASE slab adsorption model and NEB path for CO on Pt.": "ase",
        "Parse Gaussian output for SCF energy, HOMO/LUMO, and vibrational frequencies.": "cclib",
        "Standardize a molecule batch and compute Bemis-Murcko scaffolds with Datamol.": "datamol",
        "Build QSAR molecular embeddings and molecular fingerprints with MolFeat.": "molfeat",
        "Find EGFR inhibitors with IC50 below 100 nM and summarize SAR.": "chembl-database",
        "Search ZINC for purchasable drug-like analogs for virtual screening.": "zinc-database",
        "Retrieve Materials Project band gap and energy above hull for mp-149.": "materials-project",
        "Find the COD entry for an experimental crystal structure.": "cod",
        "Query OQMD for formation energy and phase stability.": "oqmd",
        "Look up JARVIS 2D material exfoliation energy and DFT properties.": "jarvis",
        "Check CCCBDB reference thermochemistry for a small molecule.": "cccbdb",
        "Compare with MolSSI QCA quantum chemistry benchmark data.": "molssi-qca",
        "Analyze an OpenMM trajectory for RMSD and RMSF.": "molecular-dynamics",
        "Set up an OpenMM simulation system with force fields and solvation.": "openmm",
        "Assign AM1-BCC partial charges using SMIRNOFF.": "open-forcefield-toolkit",
        "Assess chemical safety with ADMET, FDA labels, CTD, and STITCH evidence.": "tooluniverse-chemical-safety",
        "Create a Q-Chem TDDFT input with PCM solvent and fix SCF convergence.": "q-chem",
        "Choose ORCA DLPNO-CCSD(T) input settings and diagnose convergence errors.": "hpc-orca",
        "Interpret an IR spectrum encoded in JCAMP-DX format.": "jcamp-dx",
        "Use CHGNet to relax a crystal structure with an ML potential.": "chgnet",
        "Search papers and return normalized literature candidates for CO2 reduction catalysts.": "paper-retrieval",
        "Resolve DOI and download the open access PDF for this paper candidate.": "paper-access",
        "Parse paper PDF fulltext and extract sections from the local paper PDF.": "paper-parse",
        "Rerank papers using GROBID profiles and listwise rerank decisions.": "paper-rerank",
    }

    for prompt, expected in cases.items():
        assert route_skill_for_text(prompt) == expected, prompt


def test_single_agent_prompt_injects_same_compact_matrix() -> None:
    from benchmarking.datasets import BenchmarkRecord
    from benchmarking.prompts import build_single_llm_prompt

    record = BenchmarkRecord(
        record_id="route-cif",
        dataset="hle",
        source_file="/tmp/hle.jsonl",
        eval_kind="hle",
        prompt="What coordination polyhedra does this CIF crystal structure contain?",
        reference_answer="Al, Re2Al13",
    )

    prompt = build_single_llm_prompt(record, websearch_enabled=False)

    assert "Experimental chemistry skill routing rules:" in prompt
    assert "`pymatgen`" in prompt
    assert "`tooluniverse-chemical-safety`" in prompt
    assert "Quick Start Guide" not in prompt


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

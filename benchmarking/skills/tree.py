from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "skills" / "chemistry-routing-matrix.json"


SKILL_TREE: tuple[dict[str, Any], ...] = (
    {
        "id": "chemist-sop",
        "label": "Chemist benchmark SOP",
        "families": (
            {
                "id": "chemistry-reasoning-sop",
                "label": "Chemist-style reasoning, verification, and answer tracing",
                "skills": ("act-like-a-chemist",),
            },
        ),
    },
    {
        "id": "calculation-math",
        "label": "Calculation and formula math",
        "families": (
            {
                "id": "deterministic-chemistry-calculation",
                "label": "Deterministic chemistry calculation",
                "skills": ("chem-calculator",),
            },
        ),
    },
    {
        "id": "molecular-structure-identity",
        "label": "Molecular structure, identity, and cheminformatics",
        "families": (
            {
                "id": "structure-toolkit",
                "label": "Structure parsing, descriptors, fingerprints, and scaffolds",
                "skills": ("rdkit", "datamol", "molfeat"),
            },
            {
                "id": "name-resolution",
                "label": "Chemical name and identifier resolution",
                "skills": ("opsin", "pubchem"),
            },
            {
                "id": "compound-profile",
                "label": "Compound public records and profiles",
                "skills": ("pubchem-database", "chemistry-query"),
            },
            {
                "id": "medchem-filters",
                "label": "Medicinal chemistry filters and drug-likeness checks",
                "skills": ("medchem",),
            },
        ),
    },
    {
        "id": "literature-evidence",
        "label": "Literature evidence and paper processing",
        "families": (
            {
                "id": "paper-pipeline",
                "label": "Paper retrieval, access, reranking, and parsing",
                "skills": ("paper-retrieval", "paper-access", "paper-rerank", "paper-parse"),
            },
            {
                "id": "literature-databases",
                "label": "Bibliographic databases",
                "skills": ("pubmed-database", "openalex-database"),
            },
            {
                "id": "review-synthesis",
                "label": "Literature review and synthesis",
                "skills": ("literature-review", "synthesize-literature"),
            },
        ),
    },
    {
        "id": "bioactivity-safety-discovery",
        "label": "Bioactivity, safety, and discovery databases",
        "families": (
            {
                "id": "bioactivity-databases",
                "label": "Bioactivity, screening, and purchasable compounds",
                "skills": ("chembl-database", "zinc-database"),
            },
            {
                "id": "tooluniverse-discovery",
                "label": "ToolUniverse chemical safety, retrieval, and small-molecule discovery",
                "skills": (
                    "tooluniverse-chemical-safety",
                    "tooluniverse-small-molecule-discovery",
                    "tooluniverse-chemical-compound-retrieval",
                ),
            },
            {
                "id": "biological-databases",
                "label": "Protein, pathway, and structure databases",
                "skills": ("pdb-database", "alphafold-database", "reactome-database"),
            },
        ),
    },
    {
        "id": "materials-crystals",
        "label": "Materials, crystals, and solid-state data",
        "families": (
            {
                "id": "crystal-structure",
                "label": "Crystal structure parsing, analysis, and editing",
                "skills": ("pymatgen", "ase", "cod"),
            },
            {
                "id": "materials-databases",
                "label": "Materials databases and property lookup",
                "skills": ("materials-project", "oqmd", "jarvis"),
            },
            {
                "id": "specialist-materials",
                "label": "Specialist materials workflows",
                "skills": ("doped-perovskite-structure-analysis",),
            },
        ),
    },
    {
        "id": "simulation-forcefield-md",
        "label": "Simulation, force fields, and molecular dynamics",
        "families": (
            {
                "id": "md-analysis",
                "label": "Molecular dynamics setup and trajectory analysis",
                "skills": ("molecular-dynamics", "openmm"),
            },
            {
                "id": "forcefield-parameterization",
                "label": "Force-field assignment and parameterization",
                "skills": ("open-forcefield-toolkit", "atb"),
            },
        ),
    },
    {
        "id": "quantum-hpc",
        "label": "Quantum chemistry, HPC engines, and parsed outputs",
        "families": (
            {
                "id": "quantum-inputs",
                "label": "Quantum chemistry input preparation and engine guidance",
                "skills": (
                    "q-chem",
                    "hpc-orca",
                    "hpc-gaussian",
                    "hpc-pyscf",
                    "hpc-xtb",
                    "hpc-vasp",
                    "hpc-nwchem",
                    "hpc-cp2k",
                    "hpc-quantum-espresso",
                ),
            },
            {
                "id": "quantum-output-analysis",
                "label": "Quantum output parsing and benchmark references",
                "skills": ("cclib", "qc-output-analysis", "cccbdb", "molssi-qca"),
            },
        ),
    },
    {
        "id": "spectra-formats-visualization",
        "label": "Spectra, chemical formats, and visualization",
        "families": (
            {
                "id": "spectra-analysis",
                "label": "Spectral formats and spectral interpretation",
                "skills": ("spectral-analysis", "jcamp-dx"),
            },
            {
                "id": "chemical-file-formats",
                "label": "Chemical and crystallographic file formats",
                "skills": ("cif", "cml", "blue-obelisk"),
            },
            {
                "id": "structure-visualization",
                "label": "Crystal and structure visualization",
                "skills": ("crystal-viewer", "xtal2png"),
            },
        ),
    },
    {
        "id": "ml-generative-modeling",
        "label": "ML potentials, generative modeling, and property prediction",
        "families": (
            {
                "id": "ml-potentials",
                "label": "Atomistic ML potentials",
                "skills": ("mace", "chgnet", "mattersim", "schnet", "nequip", "orb", "reann", "torchmd-net"),
            },
            {
                "id": "generative-materials",
                "label": "Generative crystal and materials models",
                "skills": ("mattergen", "diffcsp", "crystalflow"),
            },
            {
                "id": "molecular-ml",
                "label": "Molecular ML models",
                "skills": ("chemprop",),
            },
            {
                "id": "materials-ml",
                "label": "Materials ML datasets and models",
                "skills": ("matformer", "matminer", "matbench", "modnet", "crabnet", "xenonpy"),
            },
        ),
    },
    {
        "id": "workflow-automation",
        "label": "Workflow automation and interoperable infrastructure",
        "families": (
            {
                "id": "materials-api-interoperability",
                "label": "Materials API interoperability",
                "skills": ("optimade", "optimade-python-tools"),
            },
            {
                "id": "workflow-engines",
                "label": "Workflow engines and job orchestration",
                "skills": ("aiida", "atomate", "fireworks", "custodian", "quacc", "pyiron", "qmflows", "qmforge"),
            },
        ),
    },
)


@lru_cache(maxsize=1)
def load_chemistry_skill_inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def benchmark_skill_allowlist() -> tuple[str, ...]:
    return tuple(
        str(entry["skill"])
        for entry in load_chemistry_skill_inventory().get("skills", [])
        if entry.get("single_agent_exposure") is True
    )


def load_skill_tree() -> tuple[dict[str, Any], ...]:
    return SKILL_TREE


def lookup_skill_family(family_id: str) -> dict[str, Any]:
    normalized = str(family_id or "").strip().lower()
    for domain in SKILL_TREE:
        for family in domain["families"]:
            if str(family["id"]).lower() == normalized:
                return family
    raise KeyError(f"unknown skill family: {family_id}")


def render_top_level_skill_tree(available_skills: set[str] | None = None) -> str:
    lines = [
        "Skill capability tree:",
        "Read `act-like-a-chemist` first for the chemistry solving SOP and Atomic Coverage Checklist, then choose provider skills only when they help answer the record.",
        "First choose a capability domain, then a skill family, then call a concrete skill only when it helps answer the record.",
    ]
    if available_skills is None:
        lines.append("All benchmark skills remain available; this tree is a discovery aid, not a router or allowlist filter.")
    else:
        lines.append("Only health-checked skills in this run are available; unavailable skills were omitted from the runtime allowlist.")
    for domain in SKILL_TREE:
        families: list[str] = []
        for family in domain["families"]:
            family_skills = set(str(skill) for skill in family["skills"])
            if available_skills is not None and not (family_skills & available_skills):
                continue
            families.append(f"`{family['id']}`")
        if families:
            lines.append(f"- `{domain['id']}`: {domain['label']} Families: {', '.join(families)}.")
    return "\n".join(lines)

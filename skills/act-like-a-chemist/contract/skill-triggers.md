# Provider Skill Trigger Rules

## Purpose

Use this contract only when choosing provider skills for concrete Atomic Coverage Checklist atoms. Every tool call must target a concrete Atomic Coverage Checklist atom, state the expected output shape before execution, and classify the result after execution as `supports`, `partially supports`, `contradicts`, or `only verifies an intermediate step`.

Provider output is scoped evidence, not a verdict. An unexecuted skill is not evidence. When a provider skill contributes, cite its output path, structured tool trace, or retrieved source in the answer or artifact trace.

## Capability Need First

First decide the capability domain required by the unresolved atom: 数值, 结构, 文献, 材料数据库, 谱图, 蛋白, MD, HPC, ML, 药物安全, or another explicit domain in the prompt. Do not start from a tool name and reverse-fit the problem to that tool.

The selected domain must match the atom's evidence need:

- 数值 atoms need deterministic calculation, unit conversion, or algebraic checking.
- 结构 atoms need molecular identity, name resolution, descriptors, substructure, stereochemistry, or public compound metadata.
- 文献 atoms need source-specific paper, protocol, target, assay, material-source, or experimental-condition evidence.
- 材料数据库 atoms need crystal/material structures, phase stability, computed material properties, or federated materials records.
- 谱图 atoms need provided XRD/IR data, spectroscopic exchange formats, plots, images, tables, or structure visualization.
- 蛋白 atoms need protein structure, predicted structure, or pathway evidence.
- MD atoms need simulation setup, trajectory analysis, force-field assignment, or topology generation.
- HPC atoms need engine-specific quantum/DFT input authoring, execution debugging, or output parsing.
- ML atoms need featurization, model training, inference, force-field prediction, structure generation, or model benchmarking.
- 药物安全 atoms need bioactivity, drug-likeness, purchasability, ADMET, toxicology, regulatory, or discovery-pipeline evidence.

## Primary Before Specialized

Use the primary provider first when it can close the exact checklist atom with local, deterministic, or narrowly scoped evidence. Escalate to specialized skills only when the atom explicitly needs the specialized capability, input type, data source, model family, workflow, or output artifact.

Specialized skills are not fallback exploration. They are upgrades for clear triggers such as named database requirements, supplied CIF/PDB/PDF/ZIP/checkpoint files, requested API-specific evidence, engine-specific input/output workflows, systematic literature review needs, or ML training/inference tasks.

If a provider path fails twice for the same verification target, mark that atom `blocked` and continue with bounded reasoning. Do not debug tool invocation style during a benchmark run.

## Capability Routing Matrix

| Capability need | Primary provider | Specialized upgrade options | Upgrade only when... |
| --- | --- | --- | --- |
| 数值 / equations / units | `chem-calculator` | `cclib`, `qc-output-analysis` | The atom requires parsing existing quantum chemistry output files or extracting structured calculation results from Gaussian/ORCA-style outputs. |
| 结构 / molecular identity | `rdkit`, `opsin`, `pubchem` | `datamol`, `pubchem-database`, `chemistry-query`, `blue-obelisk`, `cml` | The atom requires batch drug-discovery processing, richer PubChem/PUG workflows, synthesis or reaction-chain workflows, or interoperability / format-standard work. |
| 文献 / source evidence | `paper-retrieval` -> `paper-access` -> `paper-rerank` -> `paper-parse` | `openalex-database`, `pubmed-database`, `literature-review`, `synthesize-literature` | The atom requires OpenAlex/PubMed-specific querying, bibliometrics, citation/trend analysis, systematic review, meta-analysis, or long-form literature synthesis. |
| 材料数据库 / crystals / solid-state records | `pymatgen` | `materials-project`, `jarvis`, `cod`, `oqmd`, `optimade`, `optimade-python-tools`, `cccbdb`, `molssi-qca` | The atom requires a named materials database lookup, phase stability or hull data, CIF/crystal retrieval, computational chemistry benchmark data, QC archive data, or federated materials search. |
| 谱图 / formats / visualization | Inspect the local bundle, image, plot, or table first; use `spectral-analysis` only for provided XRD/IR data requiring AI analysis. | `jcamp-dx`, `cif`, `crystal-viewer`, `xtal2png` | The atom requires spectroscopic exchange format handling, CIF validation, interactive crystal visualization, or crystal-image ML encoding/decoding. |
| 蛋白 / biological structure / pathways | `pdb-database`, `alphafold-database` | `reactome-database` | The atom specifically needs pathway enrichment, gene-pathway mapping, disease pathway evidence, or pathway interaction data. |
| MD / force fields / atomistic setup | `molecular-dynamics`, `openmm` | `open-forcefield-toolkit`, `atb`, `ase` | The atom requires force-field parameterization, topology generation, ASE atomistic setup/conversion, or an actual simulation workflow rather than a conceptual MD answer. |
| HPC / quantum / DFT workflows | Choose the named engine skill only when the prompt requires that engine or input/output workflow. | `hpc-gaussian`, `hpc-orca`, `hpc-pyscf`, `hpc-xtb`, `hpc-cp2k`, `hpc-nwchem`, `hpc-vasp`, `hpc-quantum-espresso`, `q-chem`, `cclib`, `qc-output-analysis` | The atom requires authoring, reviewing, debugging, running, or parsing engine-specific workflows. Do not use HPC skills for ordinary chemistry reasoning. |
| ML / generative / property prediction | None by default for ordinary benchmark reasoning. | `matminer`, `molfeat`, `matbench`, `crabnet`, `modnet`, `xenonpy`, `matformer`, `chemprop`, `schnet`, `chgnet`, `mattersim`, `nequip`, `orb`, `mace`, `reann`, `torchmd-net`, `mattergen`, `diffcsp`, `crystalflow` | The atom asks for ML featurization, model training, inference, force-field prediction, structure generation, high-throughput screening, or model benchmarking. |
| 药物安全 / discovery / bioactivity | `chembl-database`, `medchem` | `zinc-database`, `tooluniverse-chemical-compound-retrieval`, `tooluniverse-chemical-safety`, `tooluniverse-small-molecule-discovery` | The atom requires purchasable compound libraries, docking/analog discovery, comprehensive compound profiles, ADMET/toxicology, regulatory safety, or small-molecule discovery pipelines. |
| Workflow / orchestration / runtime | Not provider skills for single-agent chemistry answering. | `benchmark-cleanroom`, `debateclaw-v1`, `chemqa-review` | Do not use these for trigger routing. They are benchmark/ChemQA runtime infrastructure and are excluded from single-agent provider exposure. |

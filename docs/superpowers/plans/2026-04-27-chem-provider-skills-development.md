# Chem Provider Skills Implementation Plan

## Purpose

This document defines the first implementation batch for chemistry provider
skills in OpenClaw's workspace skill system. The first batch contains:

- `rdkit`
- `pubchem`
- `opsin`
- `chem-calculator`

The design follows a provider-first model. A skill represents one provider or
one coherent tool source. The top-level `SKILL.md` is the unified entry point,
provider capabilities are exposed as independent executable scripts under
`scripts/`, and `routing-rules.md` tells agents when to choose each script.

This phase does not add a dedicated image-reading or OCSR skill. Lightweight
image or structure-figure assistance, `MolScribe`, and `RxnScribe` remain
future optional additions.

## Goals

- Improve `FrontierScience` numeric chemistry reasoning with reproducible
  calculations instead of free-form arithmetic.
- Improve `SuperChem` structure and reaction reasoning by exposing deterministic
  cheminformatics tools.
- Keep the first batch lightweight: local Python and RDKit first, public HTTP
  APIs only for provider lookups.
- Make every skill portable and callable outside ChemQA runtime state.

## Non-Goals

- Do not implement skill code in this document phase.
- Do not add a reader/OCR/OCSR skill in the first batch.
- Do not add heavy quantum chemistry, docking, molecular dynamics, GPU, or
  long-running service dependencies.
- Do not treat external provider results as final truth without local
  validation where validation is possible.

## Shared Skill Layout

Each skill must use this directory shape:

```text
skills/<skill-name>/
├── SKILL.md
├── routing-rules.md
├── references/
│   └── contracts.md
├── scripts/
│   ├── <capability>.py
│   └── ...
└── tests/
```

Responsibilities:

- `SKILL.md`: compact unified entry point. It lists capabilities, default
  execution rules, dependency expectations, and the standard command pattern.
- `routing-rules.md`: agent-facing selection guide. It answers "when should I
  call which script?" and should stay short enough to read during task routing.
- `references/contracts.md`: input and output contracts, request examples,
  status semantics, provider-specific limits, and failure modes.
- `scripts/`: deterministic command-line tools. Each file should do one
  provider capability well.
- `tests/`: unit tests and CLI smoke tests for scripts and error paths.

## Shared Script Contract

Every script in the first batch should support:

```bash
python3 <skill-root>/scripts/<capability>.py \
  --request-json /path/to/request.json \
  --output-dir /tmp/<skill-out> \
  --json
```

Rules:

- `--request-json` is the canonical input path.
- `--output-dir` is required and must be created if it does not exist.
- `--json` writes the same top-level result payload to stdout.
- Each script must write one stable result file in `--output-dir`.
- Invalid input, missing dependencies, provider failures, no-hit cases, and
  partial results must be returned as structured JSON.
- Scripts must avoid hidden dependency on ChemQA, DebateClaw, benchmark run
  directories, agent sessions, or runtime-generated state.

Shared output shape:

```json
{
  "status": "success|partial|error",
  "request": {},
  "primary_result": {},
  "candidates": [],
  "diagnostics": [],
  "warnings": [],
  "errors": [],
  "tool_trace": [],
  "source_trace": [],
  "provider_health": {}
}
```

Status semantics:

- `success`: the requested operation completed and returned usable output.
- `partial`: some output is usable, but the result has missing data, provider
  degradation, ambiguity, unsupported features, or validation warnings.
- `error`: no usable result is available for the requested operation.

For network-backed scripts:

- Use explicit timeouts.
- Use a small bounded retry policy only for transient errors.
- Include provider URL, HTTP status, elapsed time, timeout status, and parse
  status in `provider_health` or `source_trace`.
- Never silently fall back between providers inside a provider-specific skill.

For structure-bearing results:

- Include the original input string.
- Include canonicalized output when available.
- Include validation status and validation errors.
- Prefer handoff to the `rdkit` skill for structural validation after `pubchem`
  or `opsin` lookups.

## Skill: rdkit

### Purpose

`rdkit` is the local cheminformatics provider skill. It should handle structure
normalization, descriptors, functional groups, substructure matching,
ring/aromaticity analysis, stereochemistry, similarity, reaction SMARTS,
conformer generation, and lightweight NMR/symmetry heuristics.

### Dependencies

- Required: `rdkit==2026.3.1`, already present in the `chem` optional
  dependency group in `workspace/pyproject.toml`.
- No network access required.
- No heavy simulation dependencies in the first batch.

### Scripts

| Script | Capability |
|---|---|
| `canonicalize.py` | Parse SMILES/InChI where supported, sanitize, strip atom maps when requested, return canonical/isomeric SMILES and validation status. |
| `descriptors.py` | Compute formula, molecular weight, exact mass, charge, atom counts, HBD/HBA, rotatable bonds, TPSA, logP-like RDKit descriptors where available. |
| `functional_groups.py` | Match a curated SMARTS set for common functional groups and reactive handles. |
| `substructure.py` | Match user-provided SMARTS or a named built-in SMARTS pattern against one or more molecules. |
| `rings_aromaticity.py` | Report ring count, ring sizes, aromatic atoms/rings, fused ring hints, heteroaromatic features. |
| `stereochemistry.py` | Detect chiral centers, double-bond stereochemistry, specified/unspecified stereochemical features. |
| `similarity.py` | Compute Morgan fingerprint similarity or rank candidate molecules against a query. |
| `reaction_smarts.py` | Apply or validate reaction SMARTS, check reactant/product compatibility, and report product candidates. |
| `conformer_embed.py` | Generate 3D conformers, optimize with available RDKit force fields, and report embedding/optimization status. |
| `nmr_symmetry_heuristics.py` | Estimate proton/carbon equivalence classes from graph symmetry as a heuristic for NMR-style questions. |

### Routing Rules

`routing-rules.md` should include:

- Use `canonicalize.py` before any downstream RDKit operation when the input is
  a raw SMILES, InChI, or externally sourced structure.
- Use `descriptors.py` for formula, mass, charge, donor/acceptor counts, and
  quick molecule summaries.
- Use `functional_groups.py` when a question asks about chemical class,
  reactive handles, donor/acceptor behavior, polymerizable groups, or option
  elimination by structure.
- Use `substructure.py` when the question includes a structural motif or a
  SMARTS-like condition.
- Use `rings_aromaticity.py` for aromaticity, fused rings, ring strain hints,
  heteroaromatic classification, and ring-system comparisons.
- Use `stereochemistry.py` for chirality, E/Z, enantiomer/diastereomer, and
  unspecified stereochemistry checks.
- Use `similarity.py` for ranking candidates against a known molecule or
  finding closest structural analogs among options.
- Use `reaction_smarts.py` for reaction compatibility, product plausibility,
  atom-mapping checks, and mechanism option filtering.
- Use `conformer_embed.py` only when 3D geometry matters; do not use it for
  simple formula or name lookup tasks.
- Use `nmr_symmetry_heuristics.py` only as a heuristic. Output must state that
  equivalence classes do not replace expert NMR interpretation.

### Test Requirements

- Valid and invalid SMILES canonicalization.
- Aromatic and aliphatic ring examples.
- Chiral and achiral molecules.
- Functional group matches for alcohol, amine, carbonyl, carboxylic acid,
  ester, amide, alkene, alkyne, aryl halide, nitrile, nitro, and thiol.
- Substructure positive and negative cases.
- Similarity ranking with deterministic candidate order.
- Reaction SMARTS success and failure cases.
- Conformer embedding success and embedding failure cases.
- NMR/symmetry heuristic examples with explicitly documented uncertainty.

## Skill: pubchem

### Purpose

`pubchem` is the PubChem provider skill. It should resolve names, CIDs,
synonyms, formulas, basic compound properties, and similarity searches through
PubChem PUG REST.

### Dependencies

- Required: existing `requests` dependency.
- Network access to PubChem PUG REST.
- Optional future cache: JSON or SQLite cache keyed by query and normalized
  request options.

### Scripts

| Script | Capability |
|---|---|
| `name_to_cid.py` | Resolve a compound name or synonym to PubChem CID candidates. |
| `cid_to_properties.py` | Fetch canonical SMILES, isomeric SMILES, InChI, InChIKey, formula, molecular weight, charge, and selected properties for CIDs. |
| `synonyms.py` | Fetch synonyms for a CID or resolved name. |
| `formula_search.py` | Search PubChem by molecular formula and return candidates with basic metadata. |
| `similarity_search.py` | Run PubChem similarity lookup from SMILES/InChI where supported. |
| `compound_summary.py` | Produce a compact compound profile by chaining CID resolution and property lookup inside the PubChem provider. |

### Routing Rules

`routing-rules.md` should include:

- Use `name_to_cid.py` when a prompt gives a common name, trivial name, drug
  name, material name, or synonym rather than a systematic name.
- Use `cid_to_properties.py` when a CID is already known or a previous PubChem
  script returned candidate CIDs.
- Use `synonyms.py` when matching aliases across prompt text, options, and
  evidence sources.
- Use `formula_search.py` when the prompt gives only a molecular formula and
  asks for candidate structures or identity hints.
- Use `similarity_search.py` when a known structure should be compared against
  public PubChem analogs.
- Use `compound_summary.py` for agent-facing quick lookup when a single
  compact result is more useful than raw provider responses.
- After PubChem returns a structure, call `rdkit canonicalize.py` or an
  equivalent RDKit validation step before using the structure for reasoning.

### Test Requirements

- Mocked successful name-to-CID lookup.
- No-hit response.
- Provider timeout and HTTP error response.
- Multiple CID candidates for an ambiguous name.
- Property parsing for complete and partial PubChem responses.
- Formula search with multiple candidates.
- Similarity search request construction and partial failure handling.
- Result JSON includes provider health and source trace.

## Skill: opsin

### Purpose

`opsin` is the chemical name-to-structure provider skill for systematic,
IUPAC-like, and semi-systematic organic names. It should not be used as a
general fact database.

### Dependencies

- Required in first batch: existing `requests` dependency.
- Default provider: EMBL-EBI OPSIN web service.
- Optional future local mode: OPSIN Java CLI or library, activated only after
  the web-service workflow is stable.
- Optional validation handoff: `rdkit`.

### Scripts

| Script | Capability |
|---|---|
| `name_to_structure.py` | Resolve one chemical name to SMILES/InChI-style structure outputs and diagnostics. |
| `batch_name_to_structure.py` | Resolve multiple names in one request while preserving per-name status. |
| `parse_diagnostics.py` | Normalize OPSIN parse failures, unsupported syntax, ambiguity, and partial parse messages. |
| `validate_with_rdkit.py` | Validate OPSIN outputs with RDKit and return canonicalized structure fields when possible. |

### Routing Rules

`routing-rules.md` should include:

- Use `name_to_structure.py` when the prompt contains a clear systematic or
  IUPAC-like chemical name and no explicit structure is available.
- Use `batch_name_to_structure.py` when options contain multiple systematic
  names that should be resolved consistently.
- Use `parse_diagnostics.py` when OPSIN fails and the agent needs to know
  whether the issue is unsupported syntax, ambiguity, malformed input, or a
  non-systematic name.
- Use `validate_with_rdkit.py` after any successful OPSIN result before using
  it in structural reasoning.
- Prefer `pubchem` over OPSIN for trivial names, trade names, drug names,
  abbreviations, minerals, materials shorthand, and broad synonym lookup.

### Test Requirements

- Successful systematic-name resolution.
- Unparseable common-name case.
- Batch input with mixed success and failure.
- Provider timeout and HTTP error response.
- Diagnostics normalization for unsupported or ambiguous names.
- RDKit validation success and failure from mocked OPSIN outputs.
- Result JSON distinguishes no result from provider failure.

## Skill: chem-calculator

### Purpose

`chem-calculator` is the first-batch exception to provider-first naming. It is
a local calculation toolbox for reproducible numeric chemistry reasoning,
primarily for `FrontierScience`.

It should answer "is this chemistry calculation correct?" rather than "what
chemical structure is this?"

### Dependencies

- Required: Python standard library.
- Recommended for implementation: `sympy` for equation solving and symbolic
  manipulation; `pint` for units and conversions.
- Optional later enhancement: `scipy` for nonlinear numerical solving.
- Element data should start as a small local table for common benchmark needs.
  Add `mendeleev` or `periodictable` only if local data becomes insufficient.

### Scripts

| Script | Capability |
|---|---|
| `molar_mass.py` | Parse formulas and compute molar masses from local element data. |
| `stoichiometry.py` | Solve mole, mass, volume, limiting reagent, yield, combustion analysis, and empirical/molecular formula problems. |
| `concentration.py` | Handle molarity, dilution, mixing, concentration conversions, and solution bookkeeping. |
| `ksp_solver.py` | Solve precipitation and solubility equilibrium problems with ion concentration tracking. |
| `acid_base_solver.py` | Solve common acid/base, pH, pOH, buffer, and neutralization calculations. |
| `gas_law.py` | Solve ideal gas, partial pressure, volume, temperature, and gas stoichiometry calculations. |
| `thermo_solver.py` | Solve enthalpy, entropy, Gibbs free energy, equilibrium relation, and temperature conversion problems. |
| `redox_balance.py` | Compute oxidation states and balance redox half-reactions where feasible. |
| `electrochemistry.py` | Solve Nernst equation and Faraday electrolysis calculations. |
| `unit_convert.py` | Convert chemistry-relevant units with explicit dimensional checks. |
| `answer_check.py` | Compare a candidate final answer against calculated values, unit compatibility, tolerance, and significant-figure expectations. |

### Routing Rules

`routing-rules.md` should include:

- Use `molar_mass.py` when a calculation needs molecular or formula mass.
- Use `stoichiometry.py` for balanced-reaction mole/mass/volume, limiting
  reagent, combustion, empirical formula, or yield questions.
- Use `concentration.py` for solution mixing, dilution, and molarity tasks.
- Use `ksp_solver.py` when the question mentions solubility product,
  precipitation, saturated solution, dissolved ion mass, or common-ion effect.
- Use `acid_base_solver.py` for pH, pOH, Ka, Kb, Henderson-Hasselbalch,
  buffer, titration, and neutralization tasks.
- Use `gas_law.py` for ideal gas law, partial pressures, gas volumes, and gas
  stoichiometry.
- Use `thermo_solver.py` for enthalpy, entropy, Gibbs energy, equilibrium
  constants from thermodynamics, and temperature-dependent feasibility.
- Use `redox_balance.py` for oxidation states, electron accounting, and redox
  equation balancing.
- Use `electrochemistry.py` for Nernst equation, cell potentials, charge,
  current, time, and electrolysis mass deposition.
- Use `unit_convert.py` whenever units are mixed or the final answer unit must
  be checked.
- Use `answer_check.py` after a candidate answer exists and the agent needs an
  independent numerical consistency check.

### Test Requirements

- Molar mass parsing for simple, parenthesized, and hydrated formulas where
  supported.
- Stoichiometry examples for limiting reagent, combustion analysis, and yield.
- Concentration examples for dilution and mixed solutions.
- Ksp examples for precipitation and residual dissolved ion concentration.
- Acid/base examples for strong acid/base, weak acid/base, and buffer.
- Gas law examples for ideal gas and partial pressure calculations.
- Thermodynamics examples for delta G, equilibrium relation, and unit handling.
- Redox examples for oxidation states and electron count.
- Electrochemistry examples for Nernst and Faraday calculations.
- Answer checking examples covering correct value, wrong unit, rounding
  mismatch, and tolerance mismatch.

## ChemQA Prompt Integration

After the four skills exist:

- Add the four skill names to the ChemQA required sibling skill list.
- For `FrontierScience` numeric questions, tell proposer and
  `reasoning_consistency` reviewer to prefer `chem-calculator` before web
  search.
- For `SuperChem` structure questions, tell proposer to extract available
  SMILES/name text first, then use `rdkit`, `opsin`, and `pubchem` as routed.
- Reviewer lanes should cite script result files or structured tool traces when
  challenging numeric or structural claims.
- Do not instruct agents to use a dedicated image-reading skill in this phase.

## Future Optional Additions

The following are intentionally out of scope for the first implementation
batch:

- `molscribe` or `rxnscribe` provider skills.
- A lightweight visual-context or OCSR assistant skill.
- `chembl` for bioactivity and target/mechanism lookups.
- `nist-webbook` for thermochemistry, spectra, and physical property lookup.
- OpenBabel, xtb, ORCA, Gaussian, docking, molecular dynamics, or GPU-backed
  calculation providers.

## Implementation Order

1. Create the `rdkit` skill first because it is the local validation layer for
   structure outputs from other providers.
2. Create `pubchem` next for common-name and CID lookup.
3. Create `opsin` next for systematic name-to-structure conversion.
4. Create `chem-calculator` after the provider skills because its test fixtures
   can reuse the shared script contract but do not require structure lookup.
5. Integrate ChemQA prompt routing only after all four skills have working CLI
   smoke tests.

## Verification Expectations

Each implementation task should run:

```bash
pytest skills/<skill-name>/tests -q
python3 skills/<skill-name>/scripts/<representative-script>.py --request-json <fixture> --output-dir /tmp/<skill-name>-smoke --json
```

For documentation-only changes, run:

```bash
git diff --check
```

When later code or prompt behavior changes are implemented, update
`GLOBAL_DEV_SPEC.md` if system structure, behavior, feature status, module
boundaries, or execution flow changed.

## Reference Sources

- RDKit documentation: https://www.rdkit.org/docs/
- PubChem PUG REST: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
- OPSIN web service: https://www.ebi.ac.uk/opsin
- SymPy solving documentation: https://docs.sympy.org/latest/guides/solving/
- Pint documentation: https://pint.readthedocs.io/

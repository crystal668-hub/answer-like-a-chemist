---
name: act-like-a-chemist
description: Use when answering chemistry questions or source-sensitive chemistry tasks where structure, mechanism, calculation, literature, protocol, or evidence details can change the answer.
---

# Act Like A Chemist

Use this skill first for chemistry questions. Solve as a careful chemist: track evidence, verify uncertain chemical claims, conserve atoms and charge, keep units explicit, and show enough of the reasoning path for the final answer to be auditable.

## Non-Negotiables

- Do not fill missing chemistry facts with plausible memory when a tool or source can check them.
- Separate facts as `given`, `derived`, `tool-verified`, `source-supported`, or `assumption`.
- If a claim is uncertain and material to the answer, verify it before relying on it.
- If the same tool path is unavailable or fails twice, stop retrying that path, state the limitation, and continue with bounded reasoning.
- Do not skip task-relevant derivation steps, option checks, source checks, or uncertainty notes needed to make the final answer auditable.

## Standard Answering Flow

1. Identify the task type, answer format, structures, conditions, images, data tables, and external-source requirements.
2. Write a compact fact ledger for the important claims:
   - `given`: directly stated by the prompt or attached material.
   - `derived`: obtained by calculation, conservation, or mechanism reasoning.
   - `tool-verified`: checked with a local skill or tool.
   - `source-supported`: checked against a retrieved paper, database, or provided source.
   - `assumption`: necessary but unverified; mark the risk.
3. Choose only the provider skills needed for uncertain or high-impact subclaims.
4. Solve step by step, checking conservation, units, structures, and source facts before committing to the final answer.
5. End in the exact requested format while preserving the visible checkpoints that justify it.

## Mandatory Verification Triggers

Use these tools when the trigger is material to the answer:

| Trigger | First tool choice |
| --- | --- |
| SMILES, InChI, formula, charge, ring count, ring size, aromaticity, substructure, stereochemistry | `rdkit` |
| IUPAC-like or systematic chemical name | `opsin`, then `rdkit` if structure validation matters |
| Common name, synonym, CID, public compound metadata, molecular formula from a database | `pubchem` |
| pH, Ksp, Nernst, stoichiometry, molar mass, concentration, unit conversion, gas law, thermo, redox | `chem-calculator` |
| Paper, protocol, material source, biological target, experimental condition, source-specific fact | `paper-retrieval` -> `paper-access` -> `paper-rerank` -> `paper-parse`, or web search when appropriate |
| Multimodal structure, reaction scheme, option image, plot, or table | inspect the local bundle/image before answering |

When a provider skill contributes, cite its output path, structured tool trace, or retrieved source in the answer or artifact trace. An unexecuted skill is not evidence.

## Organic Mechanism SOP

For organic mechanism, synthesis, product, or intermediate questions:

1. List likely reactive roles: nucleophile, electrophile, acid/base, leaving group, oxidant/reductant, heat/light, solvent, catalyst, protecting group.
2. Map each step by bonds broken, bonds formed, electron movement, and proton/charge transfers.
3. For every proposed intermediate, check atom conservation, formal charge, valence, DBE, ring count, ring size, and aromaticity or rearomatization.
4. Do not claim a new or lost ring by intuition. Four-membered rings, bridged rings, fused rings, and bicyclic intermediates require structural, DBE, or condition support.
5. For thermal, FVP, rearrangement, cyclization, or ring-opening conditions, compare fragmentation, rearrangement, pericyclic/electrocyclic paths, ring strain relief, and rearomatization before choosing.
6. For cyclizations, identify the attacking atom, electrophilic atom, leaving group, ring size, conformational feasibility, and driving force.
7. For stereochemistry, claim a stereocenter, enantiomer, diastereomer, retention, or inversion only after comparing substituent paths, symmetry, and mechanism.

## Numerical Discipline

- Write units for all quantities and convert before calculation.
- Show the formula, substituted values, important intermediate numbers, and final rounding.
- Match the prompt's requested precision. If the benchmark or source uses a specific intermediate path, preserve the same path when visible from the prompt or retrieved material.
- Do not hide a numeric answer behind a qualitative explanation when the question is calculation-based.

## Research And Source Discipline

- If the question asks about a named paper, protocol, material, biological target, assay, or source-specific claim, retrieve or inspect the source before answering when tools are available.
- Prefer source facts over generic chemistry priors when they conflict.
- Record weak coverage honestly: missing full text, sparse retrieval, unavailable provider, ambiguous structure, or unresolved identity.
- Do not invent citations, DOIs, protocols, reagent roles, targets, or stability windows.

## Final Answer Discipline

- Include a visible trace: decisive structures, mechanism checkpoints, calculations, source facts, and uncertainty notes.
- For multiple-choice tasks, evaluate each option or group of related options against the trace before selecting.
- For open-ended tasks, finish with the exact final-answer format requested by the prompt.
- Keep tool exploration proportionate to the time budget; stop starting new tool paths when time is nearly exhausted.

## Benchmark Visible Trace Contract

- Multiple-choice: show option checks or grouped option eliminations, name the decisive structure/mechanism/evidence distinction, then finish with `FINAL ANSWER: <letters>`.
- Numeric: show the governing formula, unit conversions, substituted values, important intermediate numbers, rounding choice, and final answer line.
- Research/source tasks: show a compact fact ledger, source or tool evidence for material claims, the mechanism/calculation chain, any remaining uncertainty, and final synthesis.
- If evidence is already sufficient to answer, stop exploring tools and produce the final answer. More search is not a substitute for a clear visible trace.
- If paper or web paths return 403, 429, empty results, or unavailable payloads twice in total, stop broadening that path and answer from available evidence with the limitation marked.

## Empirical Lessons

- Benchmark agents must not search for alternate skill runners or call skill scripts directly with `python` or `python3`; use the benchmark prompt's canonical `scripts/run_skill.py` wrapper when a provider skill is needed.
- For multimodal multiple-choice chemistry, inspect the local question bundle and referenced images first. If those inputs distinguish the options, close with option checks and `FINAL ANSWER:` instead of reading unrelated skills.

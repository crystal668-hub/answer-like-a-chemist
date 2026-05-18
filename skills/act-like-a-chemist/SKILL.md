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
2. Write a compact Atomic Coverage Checklist using the relevant task template below. Split the solution path into atomic tasks, including known givens, formulas, unit conversions, intermediate values, mechanism steps, comparison classes, prompt constraints, and final-answer slots. Use `todo` until each atom is locally solved, `done` only after its derivation or evidence is complete, and `blocked` for atoms that cannot be resolved after bounded verification.
3. Write a compact fact ledger for the important claims:
   - `given`: directly stated by the prompt or attached material.
   - `derived`: obtained by calculation, conservation, or mechanism reasoning.
   - `tool-verified`: checked with a local skill or tool.
   - `source-supported`: checked against a retrieved paper, database, or provided source.
   - `assumption`: necessary but unverified; mark the risk.
4. Choose only the provider skills needed to close concrete `todo` atoms for uncertain or high-impact subclaims. Before each tool call, name the exact atom it should inform and the expected output shape.
5. Solve step by step, checking conservation, units, structures, and source facts. After each tool result, classify what it does for the targeted atom: `supports`, `partially supports`, `contradicts`, or `only verifies an intermediate step`. Mark an atom `done` only after that exact task is satisfied; a useful tool result does not close neighboring atoms.
6. When all atoms are `done` or `blocked`, run a final consistency review: Re-check every `blocked` atom to see whether prompt evidence, derivation, or earlier tool output now resolves it; confirm each completed atom appears in the answer plan; verify final values, units, dimensionality, rounding, formula or concept fit, structure/count/option constraints, and comparison cases one by one.
7. End in the exact requested format while preserving the visible checkpoints that justify it.

## Atomic Coverage Checklist

For chemistry questions, start visible work by writing a compact Atomic Coverage Checklist. It is a high-granularity solution outline, not just a list of unknowns. Include atomic tasks for prompt-provided facts and known givens as well as unknowns, because the final answer must visibly carry the whole reasoning path.

Use only these states:

- `todo`: an atomic task that is part of the solution path and has not yet been shown.
- `done`: an atomic task whose local derivation, prompt support, source evidence, or scoped evidence is complete.
- `blocked`: an atomic task that cannot be resolved within the available tools or after the allowed failure budget.

Build atoms at the granularity a grader would need to award visible reasoning credit:

- givens and known prompt facts that must be reused in calculations or comparisons;
- requested output format, units, rounding, and final-answer slots;
- formulas, substitutions, conversions, algebraic systems, and intermediate values;
- mechanism steps, reactive roles, oxidation states, bond changes, site/structure constraints, and competing pathways;
- all options, candidates, catalyst/material variants, protocol changes, or comparison classes named by the prompt;
- source-specific entities, assay/protocol conditions, named intermediates, spectra peaks, identifiers, and experimental observations.

Every tool call must target a specific `todo` atom. Before calling a tool, state the atom it will inform and the expected output shape. After the call, classify the result as `supports`, `partially supports`, `contradicts`, or `only verifies an intermediate step`. Mark the atom `done` only if that exact atom is covered; otherwise keep it `todo` or mark it `blocked`.

Tool output is scoped evidence, not absolute authority. A tool result can verify an intermediate atom, such as a molar mass, structure validity, database property, or paper excerpt. It does not override prompt constraints, competing candidates, rubric-like requirements, dimensional checks, or final reasoning. If a tool verifies only one step, continue solving the remaining atoms instead of treating the tool result as the final answer.

If the same verification target fails twice, mark that item `blocked` and stop trying alternate commands for that target. A script usage error, request-shape error, malformed JSON request, missing required argument, invalid input shape, or timeout counts as a failed attempt for that verification target. Do not spend the benchmark run debugging tool invocation style.

Do not use `python`, `python3`, `pip`, temporary runner scripts, or searches for alternate runners to call skill scripts directly. Benchmark agents may run skill scripts only through the canonical `scripts/run_skill.py` wrapper command supplied in the benchmark prompt. If that wrapper returns a usage error or structured unavailable/error payload twice for the same target, mark the target `blocked`.

### Numeric, Formula, Or Table Tasks

- `todo`: atomize requested quantity, prompt givens, formula/law, unit conversions, table values, substitutions, intermediate values, algebraic solves, rounding rule, and final value.
- `done`: each atom is visibly derived or checked, and final precision matches the prompt.
- `blocked`: missing table/image/source value, inconsistent units, or unavailable calculation verification after two failed attempts.

### Multiple-Choice Tasks

- `todo`: atomize answer format, all provided options/images, discriminating criteria, and accept/reject checks for each option or option group.
- `done`: every plausible option has a visible accept/reject reason tied to structure, mechanism, calculation, or source evidence.
- `blocked`: an option depends on unavailable source/image/tool evidence after two failed attempts; choose from remaining evidence and state the limitation.

### Research Or Open-Ended Tasks

- `todo`: atomize source-specific claims, required entities, mechanisms, assays, protocols, materials, calculations, named comparison cases, and final synthesis slots.
- `done`: each material atom is supported by retrieved/provided sources, scoped evidence, or explicit derivation, with uncertainty separated from facts.
- `blocked`: full text, database record, identifier resolution, or provider access remains unavailable after two failed attempts.

### HLE Tasks

- `todo`: atomize answer type, required final format, decisive facts, image/table inputs, elimination checks, confidence basis, and any source or tool evidence needed.
- `done`: explanation covers the decisive facts and checks, answer is directly stated, and confidence reflects remaining uncertainty.
- `blocked`: unresolved evidence is explicitly named before giving the best supported answer in the official HLE format.

## Candidate / Hypothesis Verification

- Treat tool results as evidence, not verdicts. If a tool checks a guessed answer, state whether it verifies an intermediate step or the decisive final-answer condition.
- When enumeration is needed, first use deterministic prompt constraints, conservation laws, formula/mass/charge limits, symmetry, option constraints, or required mechanism steps to narrow the candidate set. Then enumerate and verify the reduced plausible set. Do not enumerate every possible candidate first and test them one by one when deterministic reasoning can shrink the space.
- When plausible competing candidates exist, compare the key candidates side by side before choosing. Do not verify only the first candidate that gives a usable tool result.
- A database hit, formula match, approximate numeric match, valid structure, or retrieved source can establish local support. It is not sufficient final-answer evidence unless it also satisfies the task's discriminating constraints or the competing candidates have been rejected.

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

## Numerical Discipline

- Write units for all quantities and convert before calculation.
- Show the formula, substituted values, important intermediate numbers, and final rounding.
- Match the prompt's requested precision. If the benchmark or source uses a specific intermediate path, preserve the same path when visible from the prompt or retrieved material.
- When a numeric result determines identity, composition, or formula, solve for the unknown directly when possible before testing named candidates.
- If using candidate verification for a numeric identity task, compare residuals for nearby or chemically plausible competitors. Do not accept a candidate only because it is approximately close.
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

## Visible Trace Contract

- Multiple-choice: show option checks or grouped option eliminations, name the decisive structure/mechanism/evidence distinction, then finish with `FINAL ANSWER: <letters>`.
- Numeric: show the governing formula, unit conversions, substituted values, important intermediate numbers, rounding choice, and final answer line.
- Research/source tasks: show a compact fact ledger, source or tool evidence for material claims, the mechanism/calculation chain, any remaining uncertainty, and final synthesis.
- If evidence is already sufficient to answer because every checklist atom is fully covered or explicitly blocked, stop exploring tools and produce the final answer. Do not stop only because one tool call returned a useful or promising intermediate result.
- If paper or web paths return 403, 429, empty results, or unavailable payloads twice in total, stop broadening that path and answer from available evidence with the limitation marked.

# RDKit Routing Rules

- Use `canonicalize.py` before any downstream RDKit operation when the input is
  a raw SMILES, InChI, or externally sourced structure.
- Use `descriptors.py` for formula, exact mass, molecular weight, charge,
  donor/acceptor counts, TPSA, logP, and quick molecule summaries.
- Use `functional_groups.py` when the question asks about chemical class,
  reactive handles, polymerizable groups, donor/acceptor behavior, or
  structure-driven option elimination.
- Use `substructure.py` when the prompt includes a structural motif, SMARTS
  constraint, or a named local motif check.
- Use `rings_aromaticity.py` for aromaticity, ring-system comparison, fused
  rings, and heteroaromatic analysis.
- Use `stereochemistry.py` for chirality, E/Z checks, enantiomer or
  diastereomer reasoning, and unspecified stereo detection.
- Use `similarity.py` for ranking supplied candidate molecules against a known
  structure using deterministic fingerprint similarity.
- Use `reaction_smarts.py` for reaction compatibility, product plausibility,
  and reaction-option filtering with explicit structural transforms.
- Use conformer scripts only when approximate 3D geometry matters; do not use
  them for name lookup or simple formula questions.
- Use `conformer_mmff.py` when the task or protocol specifies MMFF. Use
  `conformer_uff.py` when it specifies UFF. Use `conformer_embed.py` only when a
  caller must choose dynamically, and always pass `force_field` explicitly.
  None of these scripts falls back to the other force-field family when the
  requested parameters are unavailable.
- For NMR peak-count questions, use RDKit scripts only to verify structural
  facts. Do not use graph-symmetry proton-class tooling for NMR signal counts.

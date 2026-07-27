from __future__ import annotations

from typing import Any

from rdkit_conformer_common import (
    conformer_result,
    embed_conformers,
    prepare_conformer_request,
)
from rdkit_skill_common import ProcessingError, RequestError, run_named_capability


def conformer_mmff(
    request: dict[str, Any], rdkit_ctx: dict[str, Any]
) -> dict[str, Any]:
    AllChem = rdkit_ctx["AllChem"]
    mol, metadata, num_conformers, random_seed = prepare_conformer_request(
        request, rdkit_ctx
    )
    variant = str(request.get("mmff_variant") or "MMFF94").strip()
    if variant not in {"MMFF94", "MMFF94s"}:
        raise RequestError(
            "unsupported_mmff_variant",
            "Request field `mmff_variant` must be `MMFF94` or `MMFF94s`.",
        )
    if not AllChem.MMFFHasAllMoleculeParams(mol):
        raise ProcessingError(
            "mmff_parameters_unavailable",
            "MMFF parameters are not available for this molecule.",
        )

    conformer_ids = embed_conformers(
        mol,
        all_chem=AllChem,
        num_conformers=num_conformers,
        random_seed=random_seed,
    )
    properties = AllChem.MMFFGetMoleculeProperties(mol, mmffVariant=variant)
    if properties is None:
        raise ProcessingError(
            "mmff_properties_unavailable",
            f"RDKit could not construct {variant} properties for this molecule.",
        )
    optimize_results = AllChem.MMFFOptimizeMoleculeConfs(
        mol, mmffVariant=variant
    )
    energies = []
    for conf_id in conformer_ids:
        force_field = AllChem.MMFFGetMoleculeForceField(
            mol, properties, confId=conf_id
        )
        if force_field is None:
            raise ProcessingError(
                "mmff_force_field_unavailable",
                f"RDKit could not construct the {variant} force field.",
            )
        energies.append(round(float(force_field.CalcEnergy()), 6))
    return conformer_result(
        metadata=metadata,
        conformer_ids=conformer_ids,
        optimize_results=optimize_results,
        energies=energies,
        force_field="MMFF",
        force_field_variant=variant,
        random_seed=random_seed,
        operation="conformer_mmff",
    )


if __name__ == "__main__":
    raise SystemExit(run_named_capability("conformer_mmff", conformer_mmff))

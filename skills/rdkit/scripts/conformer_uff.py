from __future__ import annotations

from typing import Any

from rdkit_conformer_common import (
    conformer_result,
    embed_conformers,
    prepare_conformer_request,
)
from rdkit_skill_common import ProcessingError, run_named_capability


def conformer_uff(
    request: dict[str, Any], rdkit_ctx: dict[str, Any]
) -> dict[str, Any]:
    AllChem = rdkit_ctx["AllChem"]
    mol, metadata, num_conformers, random_seed = prepare_conformer_request(
        request, rdkit_ctx
    )
    if not AllChem.UFFHasAllMoleculeParams(mol):
        raise ProcessingError(
            "uff_parameters_unavailable",
            "UFF parameters are not available for this molecule.",
        )

    conformer_ids = embed_conformers(
        mol,
        all_chem=AllChem,
        num_conformers=num_conformers,
        random_seed=random_seed,
    )
    optimize_results = AllChem.UFFOptimizeMoleculeConfs(mol)
    energies = []
    for conf_id in conformer_ids:
        force_field = AllChem.UFFGetMoleculeForceField(mol, confId=conf_id)
        if force_field is None:
            raise ProcessingError(
                "uff_force_field_unavailable",
                "RDKit could not construct the UFF force field.",
            )
        energies.append(round(float(force_field.CalcEnergy()), 6))
    return conformer_result(
        metadata=metadata,
        conformer_ids=conformer_ids,
        optimize_results=optimize_results,
        energies=energies,
        force_field="UFF",
        random_seed=random_seed,
        operation="conformer_uff",
    )


if __name__ == "__main__":
    raise SystemExit(run_named_capability("conformer_uff", conformer_uff))

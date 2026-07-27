from __future__ import annotations

from typing import Any

from rdkit_skill_common import ProcessingError, RequestError, load_molecule


def prepare_conformer_request(
    request: dict[str, Any], rdkit_ctx: dict[str, Any]
) -> tuple[Any, dict[str, Any], int, int]:
    Chem = rdkit_ctx["Chem"]
    if "num_conformers" not in request:
        raise RequestError(
            "missing_field",
            "Request field `num_conformers` is required.",
        )
    num_conformers = int(request["num_conformers"])
    if num_conformers <= 0:
        raise RequestError(
            "invalid_num_conformers",
            "Request field `num_conformers` must be a positive integer.",
            primary_result={"embedded_conformer_count": 0},
        )
    if "random_seed" not in request:
        raise RequestError(
            "missing_field",
            "Request field `random_seed` is required.",
        )
    random_seed = int(request["random_seed"])
    mol, metadata = load_molecule(rdkit_ctx, request.get("molecule"))
    return Chem.AddHs(mol), metadata, num_conformers, random_seed


def embed_conformers(
    mol: Any,
    *,
    all_chem: Any,
    num_conformers: int,
    random_seed: int,
) -> list[int]:
    params = all_chem.ETKDGv3()
    params.randomSeed = random_seed
    if num_conformers == 1:
        conf_id = all_chem.EmbedMolecule(mol, params)
        conformer_ids = [] if conf_id < 0 else [int(conf_id)]
    else:
        conformer_ids = [
            int(conf_id)
            for conf_id in all_chem.EmbedMultipleConfs(
                mol, numConfs=num_conformers, params=params
            )
        ]
    if not conformer_ids:
        raise ProcessingError(
            "embed_failed",
            "RDKit could not embed the requested conformers.",
            primary_result={"embedded_conformer_count": 0},
        )
    return conformer_ids


def conformer_result(
    *,
    metadata: dict[str, Any],
    conformer_ids: list[int],
    optimize_results: Any,
    energies: list[float],
    force_field: str,
    random_seed: int,
    operation: str,
    force_field_variant: str | None = None,
) -> dict[str, Any]:
    primary_result = {
        **metadata,
        "embedded_conformer_count": len(conformer_ids),
        "optimized_conformer_count": len(conformer_ids),
        "force_field": force_field,
        "random_seed": random_seed,
        "conformer_ids": conformer_ids,
        "optimization_status_codes": [int(item[0]) for item in optimize_results],
        "energies_kcal_mol": energies,
        "lowest_energy_kcal_mol": min(energies),
    }
    if force_field_variant is not None:
        primary_result["force_field_variant"] = force_field_variant
    return {
        "status": "success",
        "primary_result": primary_result,
        "source_trace": [{"provider": "rdkit", "operation": operation}],
    }

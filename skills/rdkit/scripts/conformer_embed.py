from __future__ import annotations

from typing import Any

from conformer_mmff import conformer_mmff
from conformer_uff import conformer_uff
from rdkit_skill_common import RequestError, get_required_string, run_named_capability


def conformer_embed(request: dict[str, Any], rdkit_ctx: dict[str, Any]) -> dict[str, Any]:
    force_field = get_required_string(request, "force_field").upper()
    if force_field == "MMFF":
        return conformer_mmff(request, rdkit_ctx)
    if force_field == "UFF":
        return conformer_uff(request, rdkit_ctx)
    raise RequestError(
        "unsupported_force_field",
        "Request field `force_field` must be `MMFF` or `UFF`.",
    )


if __name__ == "__main__":
    raise SystemExit(run_named_capability("conformer_embed", conformer_embed))

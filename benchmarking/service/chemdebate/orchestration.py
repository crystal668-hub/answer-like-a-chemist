from __future__ import annotations
from .convergence import ChemQAConvergencePolicy as ConvergencePolicy

from .experiments import CHEMQA_SLOT_SETS

def runner_options(*, group, output_root, config_path, chemqa_root, chemqa_timeout, chemqa_model_profile, review_rounds=None, rebuttal_rounds=None, chemqa_convergence_policy=None, workspace_manager=None, cancellation_token=None, process_registry=None):
    runtime_bundle_root = output_root / "input-bundles"
    return dict(
        chemqa_root=chemqa_root,
        timeout_seconds=chemqa_timeout,
        config_path=config_path,
        slot_set=CHEMQA_SLOT_SETS[group.id],
        review_rounds=review_rounds,
        rebuttal_rounds=rebuttal_rounds,
        model_profile=chemqa_model_profile,
        runtime_bundle_root=runtime_bundle_root,
        launch_workspace_root=output_root / "chemqa-launch",
        convergence_policy=chemqa_convergence_policy or ConvergencePolicy(timeout_seconds=chemqa_timeout),
        workspace_manager=workspace_manager,
        cancellation_token=cancellation_token,
        process_registry=process_registry,
    )

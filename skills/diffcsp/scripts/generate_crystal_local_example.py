"""
Example helper for organizing a local DiffCSP crystal generation task.

This script does not assume a fixed upstream DiffCSP config schema for
composition input. It prepares a minimal config template and tells you
where to fill in the composition-specific field used by your local code.
"""

import argparse
import json
from pathlib import Path


def parse_composition(comp_text):
    comp_text = comp_text.strip()
    if comp_text.startswith("{"):
        return json.loads(comp_text)
    return comp_text


def format_composition_comment(composition):
    if isinstance(composition, str):
        return composition
    return json.dumps(composition, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description="Prepare a local DiffCSP config template for crystal generation."
    )
    parser.add_argument("--composition", required=True, help='e.g. "BaTiO3" or \'{"Ba": 1, "Ti": 1, "O": 3}\'')
    parser.add_argument("--n_structures", type=int, default=1, help="Number of candidate structures to generate.")
    parser.add_argument("--checkpoint", required=True, help="Local DiffCSP checkpoint path.")
    parser.add_argument("--app_dir", required=True, help="Local DiffCSP application directory.")
    parser.add_argument(
        "--config_out",
        default="generated_diffcsp_config.yaml",
        help="Path to write the example config template.",
    )
    parser.add_argument(
        "--result_path",
        default="./eval_results.pkl",
        help="Value to place into test.eval_save_path.",
    )
    args = parser.parse_args()

    composition = parse_composition(args.composition)
    config_path = Path(args.config_out)
    app_dir = Path(args.app_dir)

    config_text = f"""checkpoint:
  last_path: {args.checkpoint}

test:
  num_eval: {args.n_structures}
  eval_save_path: {args.result_path}

# TODO:
# Add the composition input field expected by your local DiffCSP codebase.
# Target composition for this task: {format_composition_comment(composition)}
# Depending on your local implementation, this may live in config.yaml,
# an input json/csv/txt file, or a dataset split description.
"""

    config_path.write_text(config_text, encoding="utf-8")

    print("Config template written to:")
    print(f"  {config_path.resolve()}")
    print()
    print("Next steps:")
    print("  1. Open the generated config and fill in the composition-specific input section.")
    print("  2. Confirm the checkpoint path and output path are correct.")
    print("  3. Run the local inference command:")
    print(f"     cd {app_dir}")
    print(f"     python evaluate.py --config_path {config_path.resolve()}")
    print()
    print("If your local DiffCSP fork uses a different CLI flag than --config_path,")
    print("replace that part of the command with the correct entrypoint options.")


if __name__ == "__main__":
    main()

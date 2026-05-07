"""
Example helper for organizing a local CrystalFlow crystal generation task.

This script prepares a minimal config template and records the target
composition for the run. You still need to adapt the composition-related
input section to match your local CrystalFlow codebase.
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
        description="Prepare a local CrystalFlow config template for crystal generation."
    )
    parser.add_argument("--composition", required=True, help='e.g. "Li2O" or \'{"Li": 2, "O": 1}\'')
    parser.add_argument("--n_structures", type=int, default=1, help="Number of candidate structures to generate.")
    parser.add_argument("--checkpoint", required=True, help="Local CrystalFlow checkpoint path.")
    parser.add_argument("--app_dir", required=True, help="Local CrystalFlow application directory.")
    parser.add_argument(
        "--config_out",
        default="generated_crystalflow_config.yaml",
        help="Path to write the example config template.",
    )
    parser.add_argument(
        "--save_dir",
        default="./results",
        help="Value to place into test.save_dir.",
    )
    args = parser.parse_args()

    composition = parse_composition(args.composition)
    config_path = Path(args.config_out)
    app_dir = Path(args.app_dir)

    config_text = f"""test:
  num_eval: {args.n_structures}
  checkpoint_path: {args.checkpoint}
  save_dir: {args.save_dir}

# TODO:
# Add the composition input field expected by your local CrystalFlow codebase.
# Target composition for this task: {format_composition_comment(composition)}
# Depending on your local implementation, this may be read from config.yaml,
# a dataset/index file, or a separate generation input manifest.
"""

    config_path.write_text(config_text, encoding="utf-8")

    print("Config template written to:")
    print(f"  {config_path.resolve()}")
    print()
    print("Next steps:")
    print("  1. Open the generated config and fill in the composition-specific input section.")
    print("  2. Confirm the checkpoint path and save directory are correct.")
    print("  3. Run the local inference command:")
    print(f"     cd {app_dir}")
    print(f"     python evaluate.py --config_path {config_path.resolve()}")
    print()
    print("If your local CrystalFlow fork uses a different CLI flag or entrypoint,")
    print("replace that part of the command with the correct local invocation.")


if __name__ == "__main__":
    main()

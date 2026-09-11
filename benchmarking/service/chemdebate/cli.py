"""Explicit entrypoint for frozen legacy runs."""
import sys


def main():
    from benchmarking.workflow.cli import main as run
    from . import execution
    print("ChemQA/chemdebate is LEGACY and FROZEN; no future runtime compatibility is promised.", file=sys.stderr)
    return run(service=execution)


if __name__ == "__main__":
    raise SystemExit(main())

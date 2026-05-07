#!/usr/bin/env python3
"""
Energy above hull calculator using Materials Project reference data.

Computes thermodynamic stability (energy above hull) for:
  - Local structure files with DFT energies (e.g., VASP OUTCAR/vasprun.xml)
  - Hypothetical compositions with a given energy per atom
  - Batch mode from a CSV file

The script downloads phase diagram data from Materials Project for the relevant
chemical system, then evaluates each entry against the convex hull.

Usage:
    # Single structure from vasprun.xml (reads energy automatically)
    python energy_above_hull.py --vasprun vasprun.xml

    # Single structure + explicit energy
    python energy_above_hull.py --structure structure.cif --energy -3.456

    # Composition + energy per atom (no structure file needed)
    python energy_above_hull.py --formula LiFeO2 --energy-per-atom -4.123

    # Batch mode from CSV (columns: formula, energy_per_atom)
    python energy_above_hull.py --csv my_calcs.csv

    # Also include unstable phases from MP in the output table
    python energy_above_hull.py --vasprun vasprun.xml --show-all-mp
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

try:
    from pymatgen.core import Structure, Composition
    from pymatgen.analysis.phase_diagram import PhaseDiagram, PDEntry
except ImportError:
    print("Error: pymatgen is not installed. Install with: pip install pymatgen")
    sys.exit(1)

try:
    from mp_api.client import MPRester
except ImportError:
    print("Error: mp-api is not installed. Install with: pip install mp-api")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    api_key = os.environ.get("MP_API_KEY")
    if not api_key:
        print("Error: MP_API_KEY environment variable not set.")
        print("Get your key at https://next-gen.materialsproject.org/")
        print("Then run: export MP_API_KEY='your_key_here'")
        sys.exit(1)
    return api_key


def chemsys_from_composition(comp: Composition) -> str:
    """Return dash-joined sorted element string, e.g. 'Fe-Li-O'."""
    return "-".join(sorted(str(el) for el in comp.elements))


def stability_label(e_above_hull: float) -> str:
    if e_above_hull < 1e-3:
        return "STABLE"
    elif e_above_hull < 0.05:
        return "metastable"
    elif e_above_hull < 0.20:
        return "unstable"
    else:
        return "UNSTABLE"


def fetch_pd(chemsys: str, api_key: str) -> PhaseDiagram:
    """Download MP entries for chemsys and build a PhaseDiagram."""
    print(f"  Fetching MP entries for {chemsys} ...")
    with MPRester(api_key) as mpr:
        entries = mpr.get_entries_in_chemsys(chemsys)
    if not entries:
        print(f"  Error: no MP entries found for {chemsys}")
        sys.exit(1)
    print(f"  ✓ {len(entries)} entries retrieved, building phase diagram ...")
    pd = PhaseDiagram(entries)
    print(f"  ✓ {len(pd.stable_entries)} stable phases on the hull")
    return pd


def analyse_entry(pd: PhaseDiagram, entry: PDEntry) -> dict:
    """Return stability dict for a single PDEntry."""
    e_hull = pd.get_e_above_hull(entry, allow_negative=True)
    decomp = pd.get_decomposition(entry.composition)
    decomp_str = " + ".join(
        f"{frac:.3f}×{e.composition.reduced_formula}"
        for e, frac in decomp.items()
    )
    # get_equilibrium_reaction_energy raises ValueError for unstable entries
    if e_hull < 1e-3:
        try:
            rxn_e = pd.get_equilibrium_reaction_energy(entry)
        except ValueError:
            rxn_e = 0.0
    else:
        rxn_e = -e_hull  # driving force = energy above hull (negative = decomposition favoured)
    return {
        "formula": entry.composition.reduced_formula,
        "energy_per_atom": entry.energy_per_atom,
        "e_above_hull": e_hull,
        "status": stability_label(e_hull),
        "decomposition": decomp_str,
        "rxn_energy_eV_per_atom": rxn_e,
    }


def print_result(r: dict, show_decomp: bool = True) -> None:
    print(f"\n  Formula          : {r['formula']}")
    print(f"  Energy/atom      : {r['energy_per_atom']:.4f} eV/atom")
    print(f"  E above hull     : {r['e_above_hull']:.4f} eV/atom")
    print(f"  Status           : {r['status']}")
    if show_decomp and r['e_above_hull'] > 1e-3:
        print(f"  Decomposes to    : {r['decomposition']}")
        print(f"  Rxn energy       : {r['rxn_energy_eV_per_atom']:.4f} eV/atom")


def print_table(results: list[dict]) -> None:
    header = f"{'Formula':<20} {'E/atom (eV)':<14} {'E_hull (eV/atom)':<18} {'Status':<12}"
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['formula']:<20} {r['energy_per_atom']:<14.4f} "
            f"{r['e_above_hull']:<18.4f} {r['status']:<12}"
        )
    print("=" * len(header))


# ──────────────────────────────────────────────────────────────────────────────
# Mode implementations
# ──────────────────────────────────────────────────────────────────────────────

def mode_vasprun(vasprun_path: str, api_key: str, show_all_mp: bool) -> None:
    """Read energy + structure from vasprun.xml."""
    from pymatgen.io.vasp import Vasprun

    print(f"\nReading {vasprun_path} ...")
    try:
        vr = Vasprun(vasprun_path)
    except Exception as e:
        print(f"Error reading vasprun.xml: {e}")
        sys.exit(1)

    struct = vr.final_structure
    total_energy = vr.final_energy
    energy_per_atom = total_energy / len(struct)
    comp = struct.composition

    print(f"  Formula     : {comp.reduced_formula}")
    print(f"  Natoms      : {len(struct)}")
    print(f"  Total E     : {total_energy:.6f} eV")
    print(f"  E/atom      : {energy_per_atom:.6f} eV/atom")

    chemsys = chemsys_from_composition(comp)
    pd = fetch_pd(chemsys, api_key)

    entry = PDEntry(comp, total_energy)
    r = analyse_entry(pd, entry)
    print_result(r)

    if show_all_mp:
        _print_mp_table(pd)


def mode_structure_energy(struct_path: str, energy: float, api_key: str, show_all_mp: bool) -> None:
    """Structure file + explicit total energy."""
    print(f"\nReading {struct_path} ...")
    struct = Structure.from_file(struct_path)
    comp = struct.composition
    natoms = len(struct)
    energy_per_atom = energy / natoms

    print(f"  Formula     : {comp.reduced_formula}")
    print(f"  Natoms      : {natoms}")
    print(f"  Total E     : {energy:.6f} eV")
    print(f"  E/atom      : {energy_per_atom:.6f} eV/atom")

    chemsys = chemsys_from_composition(comp)
    pd = fetch_pd(chemsys, api_key)

    entry = PDEntry(comp, energy)
    r = analyse_entry(pd, entry)
    print_result(r)

    if show_all_mp:
        _print_mp_table(pd)


def mode_formula_energy(formula: str, energy_per_atom: float, api_key: str, show_all_mp: bool) -> None:
    """Composition + explicit energy per atom (no structure file)."""
    comp = Composition(formula)
    chemsys = chemsys_from_composition(comp)

    print(f"\n  Formula     : {comp.reduced_formula}")
    print(f"  E/atom      : {energy_per_atom:.6f} eV/atom")

    pd = fetch_pd(chemsys, api_key)

    # Use 1 formula unit so total = energy_per_atom * natoms_per_fu
    natoms = sum(comp.values())
    entry = PDEntry(comp, energy_per_atom * natoms)
    r = analyse_entry(pd, entry)
    print_result(r)

    if show_all_mp:
        _print_mp_table(pd)


def mode_csv(csv_path: str, api_key: str, output_csv: Optional[str]) -> None:
    """Batch mode: CSV with columns formula,energy_per_atom."""
    import csv

    print(f"\nReading {csv_path} ...")
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        print("Error: CSV is empty or missing header.")
        sys.exit(1)

    required = {"formula", "energy_per_atom"}
    if not required.issubset(rows[0].keys()):
        print(f"Error: CSV must have columns: {required}")
        sys.exit(1)

    # Group by chemical system to minimise API calls
    chemsys_cache: dict[str, PhaseDiagram] = {}
    results = []

    for row in rows:
        formula = row["formula"].strip()
        energy_per_atom = float(row["energy_per_atom"])
        comp = Composition(formula)
        chemsys = chemsys_from_composition(comp)

        if chemsys not in chemsys_cache:
            print(f"\n[{chemsys}]")
            chemsys_cache[chemsys] = fetch_pd(chemsys, api_key)

        pd = chemsys_cache[chemsys]
        natoms = sum(comp.values())
        entry = PDEntry(comp, energy_per_atom * natoms)
        r = analyse_entry(pd, entry)
        results.append(r)

    print_table(results)

    if output_csv:
        import csv as _csv
        with open(output_csv, "w", newline="") as f:
            fieldnames = ["formula", "energy_per_atom", "e_above_hull", "status",
                          "decomposition", "rxn_energy_eV_per_atom"]
            writer = _csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"\n✓ Results written to {output_csv}")


def _print_mp_table(pd: PhaseDiagram) -> None:
    """Print all MP entries with their e_above_hull values."""
    print("\n--- All MP entries ---")
    mp_results = []
    for entry in sorted(pd.all_entries, key=lambda e: e.composition.reduced_formula):
        e_hull = pd.get_e_above_hull(entry)
        mp_results.append({
            "formula": entry.composition.reduced_formula,
            "energy_per_atom": entry.energy_per_atom,
            "e_above_hull": e_hull,
            "status": stability_label(e_hull),
        })
    print_table(mp_results)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Compute energy above hull using Materials Project reference data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From VASP output (energy read automatically)
  %(prog)s --vasprun vasprun.xml

  # Structure file + total DFT energy
  %(prog)s --structure structure.cif --energy -42.56

  # Composition + energy per atom only
  %(prog)s --formula LiFeO2 --energy-per-atom -4.123

  # Batch from CSV (columns: formula, energy_per_atom)
  %(prog)s --csv calcs.csv --output results.csv

  # Also print all MP entries and their hull distances
  %(prog)s --vasprun vasprun.xml --show-all-mp
        """
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--vasprun", metavar="FILE",
                     help="Path to vasprun.xml (energy read automatically)")
    src.add_argument("--structure", metavar="FILE",
                     help="Structure file (CIF, POSCAR, …); requires --energy")
    src.add_argument("--formula", metavar="FORMULA",
                     help="Chemical formula, e.g. LiFeO2; requires --energy-per-atom")
    src.add_argument("--csv", metavar="FILE",
                     help="CSV with columns: formula, energy_per_atom")

    parser.add_argument("--energy", type=float, metavar="EV",
                        help="Total DFT energy in eV (used with --structure)")
    parser.add_argument("--energy-per-atom", type=float, metavar="EV",
                        help="Energy per atom in eV (used with --formula)")
    parser.add_argument("--output", metavar="FILE",
                        help="Write CSV results to this file (batch mode only)")
    parser.add_argument("--show-all-mp", action="store_true",
                        help="Print hull distances for all MP entries in the system")

    args = parser.parse_args()
    api_key = get_api_key()

    print(f"\n{'='*60}")
    print("ENERGY ABOVE HULL CALCULATOR")
    print(f"{'='*60}")

    if args.vasprun:
        mode_vasprun(args.vasprun, api_key, args.show_all_mp)

    elif args.structure:
        if args.energy is None:
            parser.error("--structure requires --energy (total DFT energy in eV)")
        mode_structure_energy(args.structure, args.energy, api_key, args.show_all_mp)

    elif args.formula:
        if args.energy_per_atom is None:
            parser.error("--formula requires --energy-per-atom")
        mode_formula_energy(args.formula, args.energy_per_atom, api_key, args.show_all_mp)

    elif args.csv:
        mode_csv(args.csv, api_key, args.output)

    print()


if __name__ == "__main__":
    main()

# Force Fields

## Force Field Hierarchy

```
Biomolecules ─────────────────────────────────────────────── Specialized
    │
    ├── Amber19 (latest AMBER, recommended)
    │       │
    │       ├── Amber14 (older but stable)
    │       │
    │       └── CHARMM36 (CHARMM force field)
    │               │
    │               └── AMOEBA (polarizable)
    │
    └── Older force fields (Amber99, Amber03, etc.)
```

## Amber19

Most recent AMBER force field — recommended for biomolecular simulations.

### Component Files

| File | Parameters |
|------|------------|
| `amber19/protein.ff19SB.xml` | Protein |
| `amber19/DNA.OL21.xml` | DNA |
| `amber14/RNA.OL3.xml` | RNA |
| `amber19/lipid21.xml` | Lipid |
| `amber14/GLYCAM_06j-1.xml` | Carbohydrates |
| `amber19/tip3pfb.xml` | TIP3P-FB water + ions |
| `amber19/tip4pfb.xml` | TIP4P-FB water + ions |
| `amber19/opc.xml` | OPC water + ions |

### Shortcut File

```python
forcefield = ForceField('amber19-all.xml', 'amber19/tip3pfb.xml')
```

`amber19-all.xml` bundles: protein, DNA, RNA, lipid.

For carbohydrates:

```python
forcefield = ForceField('amber19-all.xml', 'amber19/tip3pfb.xml', 'amber14/GLYCAM_06j-1.xml')
```

## Amber14

Older AMBER force field, still widely used.

| File | Parameters |
|------|------------|
| `amber14/protein.ff14SB.xml` | Protein |
| `amber14/DNA.OL15.xml` | DNA |
| `amber14/RNA.OL3.xml` | RNA |
| `amber14/lipid17.xml` | Lipid |

```python
forcefield = ForceField('amber14-all.xml', 'amber14/tip3pfb.xml')
```

## CHARMM36

CHARMM force field for proteins, nucleic acids, lipids, carbohydrates.

```python
forcefield = ForceField('charmm36_2024.xml', 'charmm36_2024/water.xml')
```

| File | Parameters |
|------|------------|
| `charmm36_2024.xml` | Protein, DNA, RNA, lipids, carbohydrates |
| `charmm36_2024/water.xml` | Modified TIP3P + ions |
| `charmm36_2024/tip4pew.xml` | TIP4P-Ew + ions |

## AMOEBA

Polarizable force field using atomic multipoles.

```python
forcefield = ForceField('amoeba2018.xml')
```

For implicit solvent:

```python
forcefield = ForceField('amoeba2018.xml', 'amoeba2018_gk.xml')
```

## CHARMM Polarizable (Drude)

Polarizable force field with Drude particles.

```python
forcefield = ForceField('charmm_polar_2023.xml')
```

Requires: Drude integrators (DrudeLangevinIntegrator, DrudeSCFIntegrator).

## Water Models

### Amber-Compatible

| File | Model | Sites |
|------|-------|-------|
| `amber19/tip3p.xml` | TIP3P | 3 |
| `amber19/tip3pfb.xml` | TIP3P-FB | 3 |
| `amber19/tip4pew.xml` | TIP4P-Ew | 4 |
| `amber19/tip4pfb.xml` | TIP4P-FB | 4 |
| `amber19/opc.xml` | OPC | 4 |
| `amber19/opc3.xml` | OPC3 | 3 |
| `amber19/spce.xml` | SPC/E | 3 |

### Standalone Water Files

| File | Model |
|------|-------|
| `tip3p.xml` | TIP3P |
| `tip3pfb.xml` | TIP3P-FB |
| `tip4pew.xml` | TIP4P-Ew |
| `tip4pfb.xml` | TIP4P-FB |
| `tip5p.xml` | TIP5P |
| `spce.xml` | SPC/E |

## Force Field Selection Guide

| Use Case | Recommended |
|----------|-------------|
| General biomolecular | Amber19 + tip3pfb |
| CHARMM-native systems | CHARMM36 + water |
| Polarizable simulation | AMOEBA 2018 |
| Reproducing older work | Amber14 or Amber99SB |
| Small molecules | Amber + GAFF (via openmmforcefields) |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Missing water model file | No parameters for water | Add water XML file |
| Wrong water for force field | Incompatible parameters | Use matching pair (amber19/tip3p.xml) |
| Missing GLYCAM | Carbohydrates unparameterized | Add GLYCAM file |
| Using standalone tip3p.xml | Missing ion parameters | Use forcefield-specific water file |
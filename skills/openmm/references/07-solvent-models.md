# Solvent Models

## Solvent Types

```
Explicit Solvent ─────────────────────────────────── Implicit Solvent
    │
    ├── 3-site (TIP3P, OPC3, SPC/E)
    │       │
    │       ├── 4-site (TIP4P, OPC)
    │       │
    │       ├── 5-site (TIP5P)
    │
    └── Implicit (GBSA, GBn2)
```

## Explicit Solvent Models

### 3-Site Water Models

| Model | XML File | Characteristics |
|-------|----------|-----------------|
| TIP3P | `tip3p.xml` | Classic, widely used |
| TIP3P-FB | `tip3pfb.xml` | Force balance optimized |
| OPC3 | `opc3.xml` | Optimized 3-site |
| SPC/E | `spce.xml` | Extended simple point charge |

### 4-Site Water Models

| Model | XML File | Characteristics |
|-------|----------|-----------------|
| TIP4P-Ew | `tip4pew.xml` | Ewald-optimized |
| TIP4P-FB | `tip4pfb.xml` | Force balance optimized |
| OPC | `opc.xml` | Optimized 4-site |

### 5-Site Water Models

| Model | XML File | Characteristics |
|-------|----------|-----------------|
| TIP5P | `tip5p.xml` | 5-site, good for density anomaly |
| TIP5P-Ew | `tip5pew.xml` | Ewald-optimized 5-site |

## Force Field-Specific Water Files

Use force field-specific water files that include ion parameters:

| Force Field | Water File |
|-------------|------------|
| Amber19 | `amber19/tip3pfb.xml` |
| Amber14 | `amber14/tip3pfb.xml` |
| CHARMM36 | `charmm36_2024/water.xml` |

### Example

```python
forcefield = ForceField('amber19-all.xml', 'amber19/tip3pfb.xml')
```

## Implicit Solvent Models

### GBSA Models

| Model | XML File | AMBER igb |
|-------|----------|-----------|
| HCT | `implicit/hct.xml` | igb=1 |
| OBC1 | `implicit/obc1.xml` | igb=2 |
| OBC2 | `implicit/obc2.xml` | igb=5 |
| GBn | `implicit/gbn.xml` | igb=7 |
| GBn2 | `implicit/gbn2.xml` | igb=8 |

### Using Implicit Solvent

```python
forcefield = ForceField('amber19-all.xml', 'implicit/gbn2.xml')
system = forcefield.createSystem(topology,
    nonbondedMethod=NoCutoff,
    soluteDielectric=1.0,
    solventDielectric=80.0)
```

### Implicit Solvent Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `soluteDielectric` | 1.0 | Solute dielectric constant |
| `solventDielectric` | 78.5 | Solvent dielectric constant |
| `implicitSolventKappa` | 0 | Debye-Huckel screening |

### Salt Concentration

```python
kappa = 367.434915 * sqrt(ionic_strength / (solvent_dielectric * temperature))
system = forcefield.createSystem(topology, implicitSolventKappa=kappa/nanometer)
```

## AMOEBA Implicit Solvent

Generalized Kirkwood for AMOEBA:

```python
forcefield = ForceField('amoeba2018.xml', 'amoeba2018_gk.xml')
```

## Adding Solvent

### Add Water Box

```python
from openmm.app import Modeller

modeller = Modeller(pdb.topology, pdb.positions)
modeller.addSolvent(forcefield, padding=1.0*nanometer)
```

| Parameter | Meaning |
|-----------|---------|
| `padding` | Distance from solute to box edge |
| `boxSize` | Explicit box dimensions |
| `model` | Water model (default: tip3p) |

### Add Membrane

```python
modeller.addMembrane(forcefield, lipidType='POPC',
    minimumPadding=1.0*nanometer)
```

## Solvent Model Selection

| Simulation Type | Recommended |
|-----------------|-------------|
| Standard biomolecular | Explicit: TIP3P-FB |
| Faster simulation | Implicit: GBn2 |
| Protein folding | Implicit: OBC2 |
| Membrane systems | Explicit: CHARMM36 water |
| High accuracy | Explicit: TIP4P-Ew or OPC |

## Nonbonded Method with Solvent

| Solvent | Recommended Method |
|---------|--------------------|
| Explicit periodic | PME |
| Explicit non-periodic | CutoffNonPeriodic |
| Implicit | NoCutoff or CutoffNonPeriodic |

## Comparison: Explicit vs Implicit

| Property | Explicit | Implicit |
|----------|----------|----------|
| Speed | Slower | Faster |
| Accuracy | Higher | Lower |
| Water structure | Realistic | Approximated |
| Periodic boundary | Required | Optional |
| Memory use | Higher | Lower |
| Use case | Production | Screening, folding |

## SASA Methods

For implicit solvent surface area calculation:

| Method | Description |
|--------|-------------|
| `'ACE'` | ACE approximation (default) |
| `'LCPO'` | LCPO approximation |
| `None` | Disable SASA term |

```python
system = forcefield.createSystem(topology, sasaMethod='LCPO')
```
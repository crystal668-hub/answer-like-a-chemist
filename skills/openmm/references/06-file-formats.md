# File Formats

## Supported Formats

OpenMM supports multiple molecular file formats:

| Format | Files | Use Case |
|--------|-------|----------|
| PDB | .pdb | Universal, simple |
| AMBER | .prmtop + .inpcrd | AMBER-prepared systems |
| GROMACS | .top + .gro | GROMACS-prepared systems |
| CHARMM | .psf + .pdb/.crd | CHARMM-prepared systems |
| Tinker | .xyz + .prm/.key | AMOEBA systems |

## PDB Files

### Basic Loading

```python
pdb = PDBFile('input.pdb')
```

Returns: topology and positions.

### PDBx/mmCIF Format

```python
pdb = PDBxFile('input.cif')
```

### Notes

- PDB files may need hydrogen addition
- Force field must match residue names
- Missing atoms cause parameterization errors

## AMBER Files

### Loading

```python
inpcrd = AmberInpcrdFile('input.inpcrd')
prmtop = AmberPrmtopFile('input.prmtop', periodicBoxVectors=inpcrd.boxVectors)
```

| Object | Contains |
|--------|----------|
| `AmberPrmtopFile` | Topology, force field parameters |
| `AmberInpcrdFile` | Positions, box vectors |

### Creating System

```python
system = prmtop.createSystem(nonbondedMethod=PME, constraints=HBonds)
simulation = Simulation(prmtop.topology, system, integrator)
simulation.context.setPositions(inpcrd.positions)
```

### Important

- Periodic box vectors come from inpcrd file
- Use `periodicBoxVectors=inpcrd.boxVectors` when loading prmtop
- AMBER files are fully parameterized — no ForceField needed

## GROMACS Files

### Loading

```python
gro = GromacsGroFile('input.gro')
top = GromacsTopFile('input.top',
    periodicBoxVectors=gro.getPeriodicBoxVectors(),
    includeDir='/usr/local/gromacs/share/gromacs/top')
```

| Object | Contains |
|--------|----------|
| `GromacsTopFile` | Topology, force field references |
| `GromacsGroFile` | Positions, box vectors |

### Include Directory

GROMACS top files reference force field files:

```python
includeDir='/usr/local/gromacs/share/gromacs/top'
```

Typical locations:
- Linux: `/usr/local/gromacs/share/gromacs/top`
- Conda: `$CONDA_PREFIX/share/gromacs/top`

### Creating System

```python
system = top.createSystem(nonbondedMethod=PME, constraints=HBonds)
simulation = Simulation(top.topology, system, integrator)
simulation.context.setPositions(gro.positions)
```

## CHARMM Files

### Loading PSF

```python
psf = CharmmPsfFile('input.psf')
pdb = PDBFile('input.pdb')
params = CharmmParameterSet('charmm22.rtf', 'charmm22.prm')
```

### Creating System

```python
system = psf.createSystem(params, nonbondedMethod=NoCutoff, constraints=HBonds)
simulation = Simulation(psf.topology, system, integrator)
simulation.context.setPositions(pdb.positions)
```

### Parameter Files

CHARMM uses multiple parameter files:
- `.rtf` or `.top` — Residue topology
- `.prm` or `.par` — Force field parameters
- `.str` — Stream files (additional parameters)

### CHARMM Coordinate Files

```python
crd = CharmmCrdFile('input.crd')
rst = CharmmRstFile('input.rst')
```

## Tinker Files (AMOEBA)

### Loading

```python
tinker = TinkerFiles('system.xyz', ['amoeba2018.prm'])
```

### Creating System

```python
system = tinker.createSystem(nonbondedMethod=PME,
    nonbondedCutoff=0.7*nanometer,
    vdwCutoff=0.9*nanometer)
simulation = Simulation(tinker.topology, system, integrator)
simulation.context.setPositions(tinker.positions)
```

## File Format Selection Guide

| Source | Recommended Format |
|--------|-------------------|
| AMBER-prepared | .prmtop + .inpcrd |
| GROMACS-prepared | .top + .gro |
| CHARMM-prepared | .psf + parameter files |
| Manual/PDB download | .pdb + force field |
| AMOEBA simulation | Tinker .xyz + .prm |

## Missing Atom Handling

PDB files often need additional atoms:

```python
from openmm.app import Modeller

pdb = PDBFile('input.pdb')
forcefield = ForceField('amber19-all.xml', 'amber19/tip3pfb.xml')
modeller = Modeller(pdb.topology, pdb.positions)
modeller.addHydrogens(forcefield)
modeller.addSolvent(forcefield)
```

### Modeller Functions

| Function | Purpose |
|----------|---------|
| `addHydrogens()` | Add missing hydrogens |
| `addSolvent()` | Add water box |
| `addMembrane()` | Add lipid membrane |
| `removeWater()` | Remove water molecules |

## Output Files

### Trajectory Formats

| Reporter | Format | Content |
|----------|--------|---------|
| `DCDReporter` | .dcd | Positions (binary) |
| `PDBReporter` | .pdb | Positions (text) |
| `PDBxReporter` | .cif | Positions (mmCIF) |
| `HDF5Reporter` | .h5 | Positions, velocities (requires h5py) |

### State Files

```python
simulation.saveState('state.xml')  # Full state
simulation.saveCheckpoint('checkpoint.chk')  # Binary checkpoint
```

## Box Information

| Format | Box Source |
|--------|------------|
| PDB | CRYST1 record |
| AMBER | inpcrd file |
| GROMACS | gro file |
| CHARMM | psf file or explicit setting |

### Manual Box Setting

```python
from openmm import Vec3

box_vectors = Vec3(5*nanometer, 0, 0), Vec3(0, 5*nanometer, 0), Vec3(0, 0, 5*nanometer)
system.setDefaultPeriodicBoxVectors(*box_vectors)
```
# Python API Structure

## Script Anatomy

An OpenMM script has five main parts:

```
Imports                    ← Load OpenMM libraries
Topology/System creation   ← Build molecular system
Integrator configuration   ← Set simulation parameters
Simulation setup           ← Combine topology, system, integrator
Execution                  ← Run minimization, dynamics, output
```

## Required Imports

```python
from openmm.app import *
from openmm import *
from openmm.unit import *
from sys import stdout
```

| Module | Purpose |
|--------|---------|
| `openmm.app` | Application layer: file loaders, topology, reporters |
| `openmm` | Core library: System, Context, Integrator |
| `openmm.unit` | Unit handling: nanometer, kelvin, picosecond |

## Topology and System

### From PDB File

```python
pdb = PDBFile('input.pdb')
forcefield = ForceField('amber19-all.xml', 'amber19/tip3pfb.xml')
system = forcefield.createSystem(pdb.topology,
    nonbondedMethod=PME,
    nonbondedCutoff=1*nanometer,
    constraints=HBonds)
```

### From AMBER Files

```python
inpcrd = AmberInpcrdFile('input.inpcrd')
prmtop = AmberPrmtopFile('input.prmtop', periodicBoxVectors=inpcrd.boxVectors)
system = prmtop.createSystem(nonbondedMethod=PME, constraints=HBonds)
```

### From GROMACS Files

```python
gro = GromacsGroFile('input.gro')
top = GromacsTopFile('input.top',
    periodicBoxVectors=gro.getPeriodicBoxVectors(),
    includeDir='/usr/local/gromacs/share/gromacs/top')
system = top.createSystem(nonbondedMethod=PME, constraints=HBonds)
```

## Integrator

```python
integrator = LangevinMiddleIntegrator(300*kelvin, 1/picosecond, 0.004*picoseconds)
```

| Parameter | Meaning |
|-----------|---------|
| `300*kelvin` | Temperature |
| `1/picosecond` | Friction coefficient |
| `0.004*picoseconds` | Timestep (4 fs) |

## Simulation Setup

```python
simulation = Simulation(pdb.topology, system, integrator)
simulation.context.setPositions(pdb.positions)
```

Optional platform specification:

```python
platform = Platform.getPlatform('CUDA')
properties = {'Precision': 'mixed'}
simulation = Simulation(topology, system, integrator, platform, properties)
```

## Reporters

### Trajectory Output

```python
simulation.reporters.append(DCDReporter('output.dcd', 1000))
simulation.reporters.append(PDBReporter('output.pdb', 1000))
```

### State Data

```python
simulation.reporters.append(StateDataReporter(stdout, 1000,
    step=True,
    potentialEnergy=True,
    temperature=True))
```

### Checkpoint

```python
simulation.reporters.append(CheckpointReporter('checkpoint.chk', 5000))
```

| Reporter | Output | Frequency Parameter |
|----------|---------|---------------------|
| `DCDReporter` | DCD trajectory | Steps per frame |
| `PDBReporter` | PDB trajectory | Steps per frame |
| `StateDataReporter` | Energy, temperature | Steps per report |
| `CheckpointReporter` | Full state checkpoint | Steps per checkpoint |

## Execution

### Energy Minimization

```python
simulation.minimizeEnergy()
```

### Run Dynamics

```python
simulation.step(10000)
```

## Complete Example

```python
from openmm.app import *
from openmm import *
from openmm.unit import *
from sys import stdout

pdb = PDBFile('input.pdb')
forcefield = ForceField('amber19-all.xml', 'amber19/tip3pfb.xml')
system = forcefield.createSystem(pdb.topology,
    nonbondedMethod=PME,
    nonbondedCutoff=1*nanometer,
    constraints=HBonds)
integrator = LangevinMiddleIntegrator(300*kelvin, 1/picosecond, 0.004*picoseconds)
simulation = Simulation(pdb.topology, system, integrator)
simulation.context.setPositions(pdb.positions)
simulation.minimizeEnergy()
simulation.reporters.append(DCDReporter('output.dcd', 1000))
simulation.reporters.append(StateDataReporter(stdout, 1000, step=True,
    potentialEnergy=True, temperature=True))
simulation.step(10000)
```

## Nonbonded Methods

| Method | Use Case |
|--------|----------|
| `PME` | Explicit solvent with periodic boundaries (recommended) |
| `NoCutoff` | Implicit solvent, small systems |
| `CutoffNonPeriodic` | Implicit solvent with cutoff |
| `CutoffPeriodic` | Explicit solvent without PME |

## Constraint Options

| Option | Meaning |
|--------|---------|
| `None` | No constraints (slowest) |
| `HBonds` | Constrain bonds involving hydrogen |
| `AllBonds` | Constrain all bonds |
| `HAngles` | Constrain bonds and angles involving hydrogen |
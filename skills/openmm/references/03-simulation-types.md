# Simulation Types

## Energy Minimization

Find a low-energy starting structure before dynamics:

```python
simulation.minimizeEnergy()
```

### With Max Iterations

```python
simulation.minimizeEnergy(maxIterations=1000)
```

Use minimization before any production simulation to remove bad contacts.

## Equilibrium MD

Standard molecular dynamics at constant temperature:

```python
simulation.step(10000)
```

### Typical Protocol

1. Minimize energy
2. Short equilibration (1-10 ns)
3. Production run (10-100+ ns)

## Production MD

Extended simulation for data collection:

```python
# Equilibration
simulation.step(5000)  # 20 ps at 4 fs steps

# Production
simulation.reporters.append(DCDReporter('production.dcd', 1000))
simulation.step(2500000)  # 10 ns
```

## Simulated Annealing

Gradually change temperature:

```python
simulation.minimizeEnergy()
for i in range(100):
    integrator.setTemperature(3*(100-i)*kelvin)  # 300K to 0K
    simulation.step(1000)
```

### Heating Protocol

```python
for i in range(50):
    integrator.setTemperature((i+1)*6*kelvin)  # 0K to 300K
    simulation.step(1000)
```

## Enhanced Sampling

### Replica Exchange

Use OpenMMTools for replica exchange:

```python
from openmmtools.multistate import ReplicaExchangeSampler
# Requires OpenMMTools installation
```

### Metadynamics

Use OpenMM-PLUMED for metadynamics:

```python
from openmmplumed import PlumedForce
plumed = PlumedForce('METAD ...')
system.addForce(plumed)
```

## Custom Forces and Restraints

### Position Restraints

```python
force = CustomExternalForce('k*(x-x0)^2 + k*(y-y0)^2 + k*(z-z0)^2')
force.addGlobalParameter('k', 1000*kilojoules/mole/nanometer**2)
for i, pos in enumerate(pdb.positions):
    force.addParticle(i, [pos.x, pos.y, pos.z])
system.addForce(force)
```

### Spherical Container

```python
force = CustomExternalForce('100*max(0, r-2)^2; r=sqrt(x*x+y*y+z*z)')
for i in range(system.getNumParticles()):
    force.addParticle(i, [])
system.addForce(force)
```

### Distance Restraint

```python
force = CustomBondForce('k*(r-r0)^2')
force.addGlobalParameter('k', 100*kilojoules/mole/nanometer**2)
force.addGlobalParameter('r0', 0.5*nanometer)
force.addBond(atom1, atom2, [])
system.addForce(force)
```

## Important Warning

**Add custom forces BEFORE creating Simulation:**

```python
system = forcefield.createSystem(...)
force = CustomExternalForce(...)
system.addForce(force)  # BEFORE Simulation
simulation = Simulation(topology, system, integrator)
```

Modifying the System after creating Simulation has no effect.

## Checkpoint and Restart

### Save Checkpoint

```python
simulation.reporters.append(CheckpointReporter('checkpoint.chk', 5000))
```

Or manual checkpoint:

```python
simulation.saveCheckpoint('checkpoint.chk')
```

### Load and Restart

```python
simulation = Simulation(topology, system, integrator)
simulation.loadCheckpoint('checkpoint.chk')
simulation.step(remaining_steps)
```

### Save State (positions, velocities)

```python
state = simulation.context.getState(getPositions=True, getVelocities=True)
simulation.saveState('state.xml')
```

```python
simulation.loadState('state.xml')
```

## Simulation Timing

| Duration | Steps (4 fs timestep) | Typical Use |
|----------|----------------------|-------------|
| 20 ps | 5,000 | Minimization test |
| 1 ns | 250,000 | Short equilibration |
| 10 ns | 2,500,000 | Standard production |
| 100 ns | 25,000,000 | Extended sampling |
| 1 μs | 250,000,000 | Long simulation |

## Workflow Summary

```
1. Load topology and positions
   ↓
2. Create system with force field
   ↓
3. Add custom forces (if needed)
   ↓
4. Create integrator
   ↓
5. Create simulation with platform
   ↓
6. Minimize energy
   ↓
7. Equilibration phase
   ↓
8. Production simulation with reporters
   ↓
9. Save final state/checkpoint
```
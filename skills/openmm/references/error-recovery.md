# Error Recovery

## Diagnostic First Steps

Read the Python error traceback from the terminal or SLURM output file.

## Input File Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `FileNotFound` | Missing input file | Check file path |
| `No residue template found` | Unknown residue | Check residue names match force field |
| `Missing atoms` | Incomplete structure | Use Modeller to add atoms |
| `Invalid PDB format` | Malformed PDB | Validate PDB structure |

### Missing Atoms Fix

```python
from openmm.app import Modeller

modeller = Modeller(pdb.topology, pdb.positions)
modeller.addHydrogens(forcefield)
modeller.addSolvent(forcefield)
```

## Platform Initialization Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `CUDA platform not available` | No NVIDIA GPU/driver | Install NVIDIA driver, check GPU allocation |
| `OpenCL platform not available` | No OpenCL runtime | Install GPU driver |
| `HIP platform not available` | No ROCm installed | Install ROCm (Linux only) |
| `No platform available` | No GPU drivers | Use CPU platform or install drivers |

### Platform Test

```bash
python -m openmm.testInstallation
```

## Force Field Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `No parameters for atom` | Force field mismatch | Check force field matches system |
| `Unknown atom type` | Non-standard residue | Use appropriate force field or add parameters |
| `Missing water model` | No water XML file | Add water model file |
| `Incompatible water model` | Wrong water file | Use force field-specific water file |

### Force Field Fix

```python
# Use matching force field and water
forcefield = ForceField('amber19-all.xml', 'amber19/tip3pfb.xml')
```

## Memory Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `CUDA out of memory` | GPU memory exceeded | Reduce system size or use CPU |
| `Memory allocation failed` | RAM exceeded | Request more memory |
| `Cannot allocate array` | System too large | Use smaller cutoff or reduce atoms |

### Memory Fix

1. Check GPU memory: `nvidia-smi`
2. Reduce system size
3. Use CPU platform for large systems

## Integration Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `NaN in energy` | Unstable integration | Minimize first, reduce timestep |
| `Particles exploded` | Bad initial geometry | Minimize thoroughly |
| `Constraint failure` | Too large timestep | Reduce timestep or add constraints |
| `Temperature diverging` | Bad integrator settings | Check friction and timestep |

### Integration Fix

1. Always minimize before dynamics:
```python
simulation.minimizeEnergy()
```

2. Start with smaller timestep:
```python
integrator = LangevinMiddleIntegrator(300*kelvin, 1/picosecond, 0.002*picoseconds)
```

## System Creation Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Cannot create system` | Topology mismatch | Check topology matches force field |
| `Periodic box not set` | Missing box vectors | Load from coordinate file or set manually |
| `Topology has no atoms` | Empty topology | Check file loaded correctly |

### Box Vector Fix

```python
prmtop = AmberPrmtopFile('input.prmtop', periodicBoxVectors=inpcrd.boxVectors)
```

Or manually:

```python
box = Vec3(10*nanometer, 0, 0), Vec3(0, 10*nanometer, 0), Vec3(0, 0, 10*nanometer)
system.setDefaultPeriodicBoxVectors(*box)
```

## Checkpoint Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Cannot load checkpoint` | Corrupt checkpoint | Restart from last valid checkpoint |
| `Checkpoint incompatible` | Different system | Recreate simulation exactly |
| `Checkpoint not found` | Wrong path | Use absolute path |

### Checkpoint Recovery

1. Keep multiple checkpoints
2. Use StateDataReporter to track progress
3. Restart from last good checkpoint

## Reporter Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Cannot write DCD` | Disk full | Free disk space |
| `Permission denied` | Write permission | Check directory permissions |
| `Reporter interval error` | Invalid interval | Use positive integer interval |

## Runtime Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Segmentation fault` | GPU driver issue | Update driver, try CPU platform |
| `Timeout` | Walltime exceeded | Increase time limit, use checkpointing |
| `Job killed` | Out of memory | Request more resources |
| `CUDA error` | GPU failure | Restart job, check GPU |

## Recovery Workflow

```
1. Job fails
   ↓
2. Read error from traceback
   ↓
3. Classify: Input / Platform / Force Field / Memory / Integration / Checkpoint
   ↓
4. For input: check file paths, add missing atoms
   ↓
5. For platform: test platform, check GPU allocation
   ↓
6. For force field: match system to force field, add water model
   ↓
7. For memory: reduce system size, use smaller cutoff
   ↓
8. For integration: minimize first, reduce timestep
   ↓
9. For checkpoint: restore from last valid checkpoint
   ↓
10. Test locally before resubmitting
```

## Testing Locally

Before submitting to cluster:

```python
# Quick test with fewer steps
simulation.step(100)  # Test run
```

Check:
- Platform loads correctly
- Energy minimizes
- First dynamics steps succeed
- Output files created

## Common Fixes Summary

| Problem | Quick Fix |
|---------|-----------|
| Platform unavailable | Use CPU platform for testing |
| NaN energy | Minimize before dynamics |
| Missing atoms | Use Modeller.addHydrogens() |
| Wrong parameters | Match force field to system |
| Out of memory | Reduce cutoff, use CPU |
| Constraint failure | Reduce timestep to 2 fs |
# Cluster Execution

## OpenMM on HPC

OpenMM uses GPU acceleration (CUDA, OpenCL, HIP) for high performance.

Each simulation typically uses one GPU.

## SLURM Batch Script

```bash
#!/bin/bash
#SBATCH --job-name=openmm_job
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --time=24:00:00
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err

set -euo pipefail

cd "${SLURM_SUBMIT_DIR:-$PWD}"

# Load OpenMM environment (if using conda)
# source activate openmm_env

# Run simulation
python simulate.py
```

## GPU Allocation

### Single GPU

```bash
#SBATCH --gres=gpu:1
```

In Python script:

```python
platform = Platform.getPlatform('CUDA')
properties = {'DeviceIndex': '0', 'Precision': 'mixed'}
simulation = Simulation(topology, system, integrator, platform, properties)
```

### Multi-GPU (Single Node)

```bash
#SBATCH --gres=gpu:4
```

```python
properties = {'DeviceIndex': '0,1,2,3', 'Precision': 'mixed'}
```

Note: Multi-GPU in OpenMM parallelizes computation across GPUs for one simulation.

## Platform-Specific Scripts

### CUDA (NVIDIA)

```bash
#SBATCH --gres=gpu:1
#SBATCH --constraint=gpu_nvidia
```

```python
platform = Platform.getPlatform('CUDA')
```

### HIP (AMD)

```bash
#SBATCH --gres=gpu:1
#SBATCH --constraint=gpu_amd
```

```python
platform = Platform.getPlatform('HIP')
```

## Checkpoint and Restart

### Regular Checkpoints

```python
simulation.reporters.append(CheckpointReporter('checkpoint.chk', 5000))
```

### Resume from Checkpoint

```python
from openmm.app import *

# Recreate simulation (same topology, system, integrator)
simulation = Simulation(topology, system, integrator)
simulation.loadCheckpoint('checkpoint.chk')

# Continue
remaining_steps = total_steps - simulation.currentStep
simulation.step(remaining_steps)
```

### Walltime-Aware Restart

In job script:

```bash
# Check for existing checkpoint
if [ -f checkpoint.chk ]; then
    python resume_simulation.py
else
    python initial_simulation.py
fi
```

## Memory and Scratch

OpenMM stores simulation state in memory. Large systems need sufficient RAM:

| System Size | Recommended RAM |
|-------------|-----------------|
| < 50,000 atoms | 16 GB |
| 50,000 - 100,000 atoms | 32 GB |
| > 100,000 atoms | 64+ GB |

## Job Sizing

| Simulation | Steps | GPU Time (estimate) |
|------------|-------|---------------------|
| Minimization | 1000 | Minutes |
| 1 ns equilibration | 250,000 | ~10 min |
| 10 ns production | 2,500,000 | ~2 hours |
| 100 ns production | 25,000,000 | ~20 hours |
| 1 μs production | 250,000,000 | ~8 days |

Times vary by system size and GPU performance.

## Parallel Strategies

### Multiple Independent Simulations

Run separate simulations simultaneously:

```bash
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4

# Run 4 simulations, one per GPU
srun --ntasks=4 python simulate.py
```

Use `--ntasks` to parallelize independent simulations.

### Replica Exchange

Requires OpenMMTools:

```python
from openmmtools.multistate import ReplicaExchangeSampler
```

Multiple replicas run simultaneously at different temperatures.

## Preflight Checklist

Before submitting:

- [ ] Python script tested locally
- [ ] GPU platform available and tested
- [ ] Input files present (pdb, topology)
- [ ] Checkpoint directory writable
- [ ] Output directory writable
- [ ] Sufficient walltime requested
- [ ] Correct GPU type requested

## Monitoring

Check output file:

```bash
tail -f slurm-$SLURM_JOB_ID.out
```

Key indicators:

```
#"Step","Potential Energy (kJ/mole)","Temperature (K)"
1000, -123456.78, 300.5
```

For GPU errors:

```
CUDA error: out of memory
```

## Common Batch Errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| `CUDA not available` | Missing GPU/driver | Check GPU allocation |
| `Out of memory` | System too large | Request more memory |
| `Checkpoint not found` | Path issue | Use absolute paths |
| `Segmentation fault` | GPU driver issue | Update driver |
| `Timeout` | Insufficient walltime | Increase time limit |

## Environment Setup

### Conda Environment

```bash
conda create -n openmm_env openmm
conda activate openmm_env
```

### In SLURM Script

```bash
source $HOME/.bashrc
conda activate openmm_env
```

## Output Organization

Recommended structure:

```
job_directory/
├── input.pdb
├── simulate.py
├── checkpoint.chk
├── output.dcd
├── slurm-12345.out
└── state_data.csv
```

## Long Simulations

For very long simulations (> 24 hours):

1. Use checkpointing every 1-5 ns
2. Chain jobs with checkpoint restart
3. Monitor progress periodically

```bash
# Chain jobs
sbatch --dependency=afterok:12345 next_job.sh
```
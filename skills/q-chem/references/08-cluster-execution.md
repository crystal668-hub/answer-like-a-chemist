# Cluster Execution

## Q-Chem on HPC

Q-Chem supports two parallelization modes:
- OpenMP (shared memory, single node)
- MPI (distributed memory, multi-node)

## SLURM Batch Script

```bash
#!/bin/bash
#SBATCH --job-name=qchem_job
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=04:00:00
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err

set -euo pipefail

input_file="${1:-job.in}"
output_file="${2:-job.out}"

cd "${SLURM_SUBMIT_DIR:-$PWD}"

# Set Q-Chem environment
# export QC=/path/to/qchem
# export QCAUX=/path/to/qchem/auxiliary

# Create scratch directory
export QCSCRATCH="${TMPDIR:-/tmp}/${USER}/qchem-${SLURM_JOB_ID}"
mkdir -p "$QCSCRATCH"

# Run Q-Chem
qchem -nt "$SLURM_CPUS_PER_TASK" "$input_file" "$output_file" "$QCSCRATCH"
```

## OpenMP Parallelism

Set thread count:

```bash
qchem -nt 16 input.in output.out scratch
```

Or in `$rem`:

```
$rem
   NTHREADS        16
$end
```

Match thread count to CPU allocation.

## MPI Parallelism

For multi-node jobs:

```bash
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=16

mpirun -np 64 qchem input.in output.out scratch
```

### MPI Requirements

- Q-Chem MPI version installed
- Proper MPI configuration
- Shared filesystem access

## Memory Management

Q-Chem memory is controlled via:

```
$rem
   MEM_TOTAL       16000
$end
```

| Value | Memory |
|-------|--------|
| 4000 | 4 GB |
| 8000 | 8 GB |
| 16000 | 16 GB |
| 32000 | 32 GB |

### Memory Guidelines

| System Size | Basis | Recommended Memory |
|------------|-------|-------------------|
| < 20 atoms | 6-31G* | 4-8 GB |
| 20-50 atoms | 6-31G* | 8-16 GB |
| 50-100 atoms | 6-31G* | 16-32 GB |
| > 100 atoms | 6-31G* | 32+ GB |
| Correlated methods | cc-pVTZ | Double typical values |

## Scratch Directory

Q-Chem uses scratch for:
- Integral storage
- Temporary files
- MO files

```bash
export QCSCRATCH="/scratch/${USER}/qchem_${SLURM_JOB_ID}"
mkdir -p "$QCSCRATCH"
```

### Scratch Size Guidelines

| Calculation | Scratch Size |
|-------------|--------------|
| DFT small | 1-10 GB |
| DFT medium | 10-50 GB |
| MP2 medium | 20-100 GB |
| CCSD(T) small | 50-200 GB |

## PBS Batch Script

```bash
#!/bin/bash
#PBS -N qchem_job
#PBS -l nodes=1:ppn=16
#PBS -l walltime=04:00:00
#PBS -o pbs-$PBS_JOBID.out
#PBS -e pbs-$PBS_JOBID.err

cd $PBS_O_WORKDIR

export QCSCRATCH="/scratch/${USER}/qchem-${PBS_JOBID}"
mkdir -p $QCSCRATCH

qchem -nt 16 input.in output.out $QCSCRATCH
```

## Job Sizing

| System Size | Atoms | CPUs | Memory | Time |
|------------|-------|------|--------|------|
| Small | < 20 | 8-16 | 4-8 GB | min-hrs |
| Medium | 20-50 | 16-32 | 8-16 GB | hrs |
| Large | 50-100 | 32-64 | 16-32 GB | hrs-days |
| Very large | > 100 | 64+ | 32+ GB | days |

## Preflight Checklist

Before submitting:

- [ ] Input file parses correctly (test locally)
- [ ] NTHREADS matches CPU allocation
- [ ] MEM_TOTAL is appropriate
- [ ] Scratch space is available
- [ ] Q-Chem module is loaded

## Monitoring

Check output file:

```bash
tail -f output.out
```

Key indicators:
```
SCF converged
Total energy
Have a nice day.
```

## Common Batch Errors

| Error | Cause | Fix |
|-------|-------|-----|
| Out of memory | MEM_TOTAL too low | Increase memory |
| Scratch full | Scratch exhausted | Request more scratch |
| Timeout | Walltime exceeded | Increase walltime or checkpoint |
| MPI error | MPI config wrong | Check MPI setup |
| License error | No license | Check license server |

## Restart After Timeout

If job exceeds walltime:

1. Check output for last completed cycle
2. Extract last geometry
3. Start new job with that geometry

Use batch jobs to chain calculations.
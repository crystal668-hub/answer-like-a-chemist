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

# Set Q-Chem environment (uncomment and modify as needed)
# export QC=/path/to/qchem
# export QCAUX=/path/to/qchem/auxiliary

# Create scratch directory
export QCSCRATCH="${TMPDIR:-/tmp}/${USER}/qchem-${SLURM_JOB_ID}"
mkdir -p "$QCSCRATCH"

# Run Q-Chem with OpenMP parallelism
qchem -nt "$SLURM_CPUS_PER_TASK" "$input_file" "$output_file" "$QCSCRATCH"
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

# Activate OpenMM environment if using conda
# source $HOME/.bashrc
# conda activate openmm_env

# Check for existing checkpoint and resume
if [ -f checkpoint.chk ]; then
    python resume_simulation.py
else
    python simulate.py
fi
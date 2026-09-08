#!/bin/sh
set -eu

if [ "${BENCHMARK_CREATE_ATTEMPT_VENV:-0}" = "1" ]; then
    mkdir -p "${BENCHMARK_ATTEMPT_VENV_DIR:-/benchmark/workspace/scratch/venv}"
    uv venv --seed --no-project --python "${BENCHMARK_BOOTSTRAP_PYTHON:-/usr/local/bin/python}" \
        "${BENCHMARK_ATTEMPT_VENV_DIR:-/benchmark/workspace/scratch/venv}"
fi

exec "$@"

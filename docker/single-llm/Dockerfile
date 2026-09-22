FROM node:24.16-bookworm-slim AS node-runtime
FROM python:3.12-slim-bookworm

COPY --from=node-runtime /usr/local/ /usr/local/
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PATH=/opt/benchmark/.venv/bin:/usr/local/bin:/usr/bin:/bin

WORKDIR /opt/benchmark
COPY pyproject.toml uv.lock README.md ./
COPY benchmarking ./benchmarking

RUN npm install --global openclaw@2026.9.5 \
    && uv sync --frozen --no-dev \
    && python -c "from pathlib import Path; Path('/etc/group').open('a').write('benchmark:x:1000:\\n'); Path('/etc/passwd').open('a').write('benchmark:x:1000:1000::/home/benchmark:/bin/sh\\n'); Path('/home/benchmark').mkdir(parents=True, exist_ok=True)"

COPY docker/single-llm/entrypoint.sh /opt/benchmark/entrypoint.sh
RUN chmod 0555 /opt/benchmark/entrypoint.sh \
    && chown -R benchmark:benchmark /opt/benchmark

USER benchmark:benchmark
ENTRYPOINT ["/opt/benchmark/entrypoint.sh"]

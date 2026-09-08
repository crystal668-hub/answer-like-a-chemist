FROM node:24-bookworm-slim AS node-runtime
FROM python:3.12-slim-bookworm

COPY --from=node-runtime /usr/local/ /usr/local/
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PATH=/opt/benchmark/.venv/bin:/usr/local/bin:/usr/bin:/bin

WORKDIR /opt/benchmark
COPY pyproject.toml uv.lock README.md ./
COPY benchmarking ./benchmarking
COPY scripts ./scripts

RUN npm install --global openclaw@2026.6.9 \
    && uv sync --frozen --no-dev \
    && useradd --create-home --uid 1000 --shell /bin/sh benchmark

COPY docker/single-llm/entrypoint.sh /opt/benchmark/entrypoint.sh
RUN chmod 0555 /opt/benchmark/entrypoint.sh \
    && chown -R benchmark:benchmark /opt/benchmark

USER benchmark:benchmark
ENTRYPOINT ["/opt/benchmark/entrypoint.sh"]

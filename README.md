## Local paper-processing services

This project uses two local services for paper-processing workflows:

- `GROBID` at `http://127.0.0.1:8070`, managed by Docker Compose.
- `MinerU API` at `http://127.0.0.1:8000`, managed as a native macOS process.

They are referenced by the default environment variables in `~/.openclaw/.env`:

- `GROBID_URL=http://localhost:8070`
- `MINERU_API_URL=http://127.0.0.1:8000`

### GROBID Docker

Use the repo helper script for GROBID:

```bash
cd ~/.openclaw/workspace
bash scripts/docker_services.sh up
bash scripts/docker_services.sh ps
bash scripts/docker_services.sh health
```

Common operations:

```bash
bash scripts/docker_services.sh down
bash scripts/docker_services.sh restart
bash scripts/docker_services.sh logs grobid
```

The service-specific Compose project lives in:

- `grobid-docker/compose.yaml`

### Native MinerU

On macOS, MinerU should run natively instead of through Docker. Install the CLI/runtime, pre-download models, then start the long-lived API:

```bash
cd ~/.openclaw/workspace
bash scripts/mineru_service.sh install
bash scripts/mineru_service.sh download-models
bash scripts/mineru_service.sh up
bash scripts/mineru_service.sh health
```

Common operations:

```bash
bash scripts/mineru_service.sh ps
bash scripts/mineru_service.sh logs
bash scripts/mineru_service.sh restart
bash scripts/mineru_service.sh down
```

Notes:

- Both services bind to loopback only and are not exposed on the LAN.
- `paper-parse` still reads `MINERU_API_URL` and passes it to the local `mineru` CLI, so existing runtime config can keep `MINERU_API_URL=http://127.0.0.1:8000`.
- `mineru_service.sh up` defaults to `MINERU_MODEL_SOURCE=local`, so run `mineru_service.sh download-models` before the first service start.
- `mineru_service.sh download-models` defaults to `MINERU_DOWNLOAD_SOURCE=modelscope`; set `MINERU_DOWNLOAD_SOURCE=huggingface` if that source is preferred.

# MinerU API Docker

This folder is retained as a legacy Linux/reference Docker packaging for `mineru-api`.

The active macOS service path is native MinerU:

```bash
cd ~/.openclaw/workspace
bash scripts/mineru_service.sh install
bash scripts/mineru_service.sh download-models
bash scripts/mineru_service.sh up
```

Current OpenClaw config can keep:

```bash
MINERU_API_URL=http://127.0.0.1:8000
```

The native helper defaults runtime model loading to local models and model download to ModelScope:

```bash
MINERU_MODEL_SOURCE=local
MINERU_DOWNLOAD_SOURCE=modelscope
```

## Legacy Docker start

Use this only on hosts where Docker deployment is appropriate:

```bash
cd ~/.openclaw/workspace/mineru-api-docker
docker compose up -d --build
```

Check health:

```bash
curl -fsS http://127.0.0.1:8000/health
docker compose ps
```

Stop:

```bash
docker compose down
```

## Notes

- The repo-root `scripts/docker_services.sh` no longer manages this Compose project.
- The native service helper writes logs and a PID file under `workspace/.run/mineru` by default.
- The container binds only to loopback if used.

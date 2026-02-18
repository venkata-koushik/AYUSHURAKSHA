# Deployment Quick Guide

## Required Environment
- `DATABASE_URL`
- `JWT_SECRET_KEY`
- `CORS_ALLOWED_ORIGINS`
- `TRUSTED_HOSTS`
- Optional integrations: SendGrid, Twilio, AI provider keys

## Health Checks
- `/healthz`
- `/readyz`

## Preflight
Run:

```powershell
python scripts/deploy_preflight.py
```

## Production Run (example)

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Use a reverse proxy (Nginx/Caddy) with HTTPS and websocket forwarding.

# Project Architecture

## Root Structure
- `app/` - FastAPI application code and web templates/static assets
- `docs/` - architecture and deployment documentation
- `scripts/` - operational utilities (preflight, migration, seeding, dumps)
- `tests/` - E2E tests (Playwright)
- `.env` - runtime configuration (local)
- `.env.example` - environment template
- `requirements.txt` - Python dependencies
- `package.json` - Playwright tooling
- `playwright.config.js` - E2E configuration

## App Structure (`app/`)
- `main.py` - app bootstrap, route mounting, websocket endpoints
- `db.py` - SQLAlchemy engine/session setup
- `models/`
  - `db_models.py` - ORM entities
  - `schemas.py` - request/response schemas
- `routers/` - API and page routers by domain
  - `api.py`, `auth.py`, `doctor.py`, `patient.py`, `student.py`
- `services/` - business logic and integrations
  - auth, AI, OTP, email/SMS, realtime, relational data workflows
- `templates/` - server-rendered HTML pages
- `frontend/` - static CSS/JS/vendor assets and geojson
- `uploads/` - runtime uploaded files (manual reports, patient docs)
- `generated_qr/` - runtime generated QR images

## Deployment Profile
- Required for deployment:
  - `app/`, `requirements.txt`, `.env` (or env vars), `README.md`
- Optional in production image:
  - `tests/`, `scripts/`, `package*.json`, `playwright.config.js`

## Notes
- Keep runtime generated files out of version control.
- Keep secrets only in environment variables in production.
- Use PostgreSQL as production DB via `DATABASE_URL`.

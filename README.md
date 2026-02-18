# AI-Integrated AYUSH Healthcare System

## Setup

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Deployment Readiness

1. Copy `.env.example` to `.env` and set production values.
2. Do **not** use default `JWT_SECRET_KEY` in production.
3. Set `CORS_ALLOWED_ORIGINS` and `TRUSTED_HOSTS` to your real domain(s).
4. Keep `SEED_DEMO_DATA=false` in production.
5. Ensure `ffmpeg` is installed for voice transcription.

Preflight check:

```bash
python scripts/deploy_preflight.py
```

SQLite -> PostgreSQL migration:

```bash
# Example
set POSTGRES_URL=postgresql+psycopg2://postgres:your_password@127.0.0.1:5432/ayush
python scripts/migrate_sqlite_to_postgres.py
```

Then set in `.env`:

```env
DATABASE_URL=postgresql+psycopg2://postgres:your_password@127.0.0.1:5432/ayush
```

Health endpoints:

- `GET /healthz`
- `GET /readyz`

## Multi-page Routes

- `/select-role`
- `/patient/register`
- `/patient/dashboard`
- `/patient/qr`
- `/patient/ai-chat`
- `/patient/language-select`
- `/doctor/login`
- `/doctor/dashboard`
- `/hospital/scan`
- `/student/register`
- `/student/login`
- `/student/language-selection`
- `/student/dashboard`
- `/government/dashboard`

## Core APIs

- `POST /api/patient/register`
- `POST /api/patient/login`
- `GET /api/patient/profile?patient_id=...`
- `GET /api/patient/qr?patient_id=...`
- `POST /api/ai/chat`
- `POST /api/hospital/scan`
- `POST /api/doctor/register`
- `POST /api/doctor/login`
- `GET /api/doctor/stats?doctor_id=...`
- `POST /api/create_visit`
- `POST /api/create_diagnosis`
- `POST /api/create_recommendation`
- `GET /api/patient_history/{uhid}`
- `GET /api/patient_summary/{uhid}`
- `GET /api/alerts/{state}/{district}`
- `GET /api/gov/state-summary`
- `GET /api/gov/district-summary`
- `POST /api/student/verify-college`
- `POST /api/student/confirm-otp`
- `POST /api/student/register`
- `POST /api/student/login`
- `POST /api/student/online`

## Notes

- Database: `DATABASE_URL` env supported (defaults to local SQLite `ayush.db`).
- QR images saved as files in `app/generated_qr/`.
- APScheduler runs periodic surveillance alert generation every 10 minutes (toggle with `ENABLE_BACKGROUND_SCHEDULER`).

## Project Structure
- See docs/ARCHITECTURE.md for planned folder architecture and deployment boundaries.
- See docs/DEPLOYMENT.md for deployment checklist.


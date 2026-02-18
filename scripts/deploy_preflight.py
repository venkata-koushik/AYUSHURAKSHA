from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv


def ok(label: str, value: str | bool) -> None:
    print(f"[OK] {label}: {value}")


def warn(label: str, value: str | bool) -> None:
    print(f"[WARN] {label}: {value}")


def fail(label: str, value: str | bool) -> None:
    print(f"[FAIL] {label}: {value}")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    os.chdir(project_root)
    load_dotenv(project_root / ".env")
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    exit_code = 0
    env = os.environ

    # Required in production
    database_url = env.get("DATABASE_URL", "").strip()
    jwt_secret = env.get("JWT_SECRET_KEY", "").strip()

    if database_url:
        ok("DATABASE_URL", "set")
    else:
        fail("DATABASE_URL", "missing")
        exit_code = 1

    if jwt_secret and jwt_secret != "ayush-dev-secret-change-in-env":
        ok("JWT_SECRET_KEY", "set")
    else:
        fail("JWT_SECRET_KEY", "missing or default insecure value")
        exit_code = 1

    # Optional but strongly recommended
    cors = env.get("CORS_ALLOWED_ORIGINS", "").strip() or "*"
    trusted_hosts = env.get("TRUSTED_HOSTS", "").strip() or "*"
    if cors == "*":
        warn("CORS_ALLOWED_ORIGINS", "wildcard; restrict in production")
    else:
        ok("CORS_ALLOWED_ORIGINS", cors)
    if trusted_hosts == "*":
        warn("TRUSTED_HOSTS", "wildcard; restrict in production")
    else:
        ok("TRUSTED_HOSTS", trusted_hosts)

    # External integrations
    if env.get("AI_API_KEY", "").strip() or env.get("OPENAI_API_KEY", "").strip() or env.get("NEW_API_KEY", "").strip():
        ok("AI provider key", "set")
    else:
        warn("AI provider key", "not set (AI chat/STT may fall back or degrade)")

    if env.get("SENDGRID_API_KEY", "").strip():
        ok("SENDGRID_API_KEY", "set")
    else:
        warn("SENDGRID_API_KEY", "not set (email OTP/reset may fail)")

    if env.get("TWILIO_ACCOUNT_SID", "").strip() and env.get("TWILIO_AUTH_TOKEN", "").strip():
        ok("Twilio credentials", "set")
    else:
        warn("Twilio credentials", "not set (SMS OTP may fail)")

    # Runtime binaries/files
    ffmpeg = shutil.which("ffmpeg") or env.get("FFMPEG_BIN", "").strip()
    if ffmpeg:
        ok("ffmpeg", ffmpeg)
    else:
        warn("ffmpeg", "not found (audio transcription may fail)")

    frontend_dir = project_root / "app" / "frontend"
    templates_dir = project_root / "app" / "templates"
    if frontend_dir.exists():
        ok("frontend static dir", str(frontend_dir))
    else:
        fail("frontend static dir", "missing")
        exit_code = 1
    if templates_dir.exists():
        ok("templates dir", str(templates_dir))
    else:
        fail("templates dir", "missing")
        exit_code = 1

    # Import/syntax smoke
    try:
        import app.main  # noqa: F401
        ok("python import app.main", "success")
    except Exception as exc:
        fail("python import app.main", str(exc))
        exit_code = 1

    print(f"\nPreflight result: {'PASS' if exit_code == 0 else 'FAIL'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

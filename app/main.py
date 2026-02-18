from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
import json
import shutil

from app.routers import api, auth, doctor, patient, student
from app.db import Base, db_session, engine
from app import models  # importing lib
from app.models.db_models import ConsultationSession
from app.services.relational_service import relational_service
from app.services.realtime_service import chat_manager, notification_manager, signaling_manager
from app.services.security_service import security_service


def _ensure_sqlite_columns() -> None:
    # Lightweight compatibility migration for local sqlite.
    if engine.url.get_backend_name() != "sqlite":
        return
    with engine.begin() as conn:
        student_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(students)").fetchall()}
        if "phone" not in student_cols:
            conn.exec_driver_sql("ALTER TABLE students ADD COLUMN phone VARCHAR(20)")
        if "institute_name" not in student_cols:
            conn.exec_driver_sql("ALTER TABLE students ADD COLUMN institute_name VARCHAR(128)")
        if "full_name" not in student_cols:
            conn.exec_driver_sql("ALTER TABLE students ADD COLUMN full_name VARCHAR(128)")
        if "rating_count" not in student_cols:
            conn.exec_driver_sql("ALTER TABLE students ADD COLUMN rating_count INTEGER DEFAULT 0")
        session_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(consultation_sessions)").fetchall()}
        if "rating_score" not in session_cols:
            conn.exec_driver_sql("ALTER TABLE consultation_sessions ADD COLUMN rating_score INTEGER")
        if "rating_feedback" not in session_cols:
            conn.exec_driver_sql("ALTER TABLE consultation_sessions ADD COLUMN rating_feedback TEXT")
        if "rated_at" not in session_cols:
            conn.exec_driver_sql("ALTER TABLE consultation_sessions ADD COLUMN rated_at DATETIME")
        verify_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(student_verifications)").fetchall()}
        if "institute_name" not in verify_cols:
            conn.exec_driver_sql("ALTER TABLE student_verifications ADD COLUMN institute_name VARCHAR(128)")


def create_app() -> FastAPI:
    load_dotenv()
    app = FastAPI(title="AI-Integrated AYUSH Healthcare Ecosystem", version="0.1.0")

    # Basic production hardening controls (safe defaults keep local dev working).
    allowed_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "*").strip()
    allowed_origins = [v.strip() for v in allowed_origins_raw.split(",") if v.strip()] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    trusted_hosts_raw = os.getenv("TRUSTED_HOSTS", "*").strip()
    trusted_hosts = [v.strip() for v in trusted_hosts_raw.split(",") if v.strip()] or ["*"]
    if "*" not in trusted_hosts and "testserver" not in trusted_hosts:
        trusted_hosts.append("testserver")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(patient.router, prefix="/patient", tags=["patient"])
    app.include_router(doctor.router, prefix="/doctor", tags=["doctor"])
    app.include_router(student.router, prefix="/student", tags=["student"])
    app.include_router(api.router)

    frontend_dir = Path(__file__).parent / "frontend"
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()
    if os.getenv("SEED_DEMO_DATA", "false").strip().lower() in {"1", "true", "yes", "on"}:
        with db_session() as db:
            relational_service.seed_demo_records(db)

    scheduler = BackgroundScheduler()

    def generate_alerts_job() -> None:
        with db_session() as db:
            relational_service.generate_alerts(db)
            relational_service.reassign_expired_sessions(db)

    if os.getenv("ENABLE_BACKGROUND_SCHEDULER", "true").strip().lower() in {"1", "true", "yes", "on"}:
        scheduler.add_job(generate_alerts_job, "interval", minutes=10, id="surveillance_alert_job", replace_existing=True)
        scheduler.start()

    @app.on_event("shutdown")
    def shutdown_event() -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)

    def render(request: Request, page: str):
        return templates.TemplateResponse(page, {"request": request})

    @app.get("/", include_in_schema=False)
    def serve_frontend(request: Request):
        return render(request, "select-role.html")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        checks: dict[str, str | bool] = {
            "database": False,
            "static_dir": frontend_dir.exists(),
            "templates_dir": (Path(__file__).parent / "templates").exists(),
            "ffmpeg": bool(shutil.which("ffmpeg") or os.getenv("FFMPEG_BIN", "").strip()),
        }
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            checks["database"] = True
        except Exception:
            checks["database"] = False
        status = "ready" if bool(checks["database"]) and bool(checks["static_dir"]) and bool(checks["templates_dir"]) else "degraded"
        return {"status": status, "checks": checks}

    @app.get("/select-role", include_in_schema=False)
    def select_role(request: Request):
        return render(request, "select-role.html")

    @app.get("/patient/register", include_in_schema=False)
    def patient_register_page(request: Request):
        return render(request, "patient-register.html")

    @app.get("/patient/login", include_in_schema=False)
    def patient_login_page(request: Request):
        return render(request, "patient-login.html")

    @app.get("/patient/dashboard", include_in_schema=False)
    def patient_dashboard_page(request: Request):
        return render(request, "patient-dashboard.html")

    @app.get("/patient/qr", include_in_schema=False)
    def patient_qr_page(request: Request):
        return render(request, "patient-qr.html")

    @app.get("/patient/ai-chat", include_in_schema=False)
    def patient_ai_chat_page(request: Request):
        return render(request, "patient-ai-chat.html")

    @app.get("/patient/language-select", include_in_schema=False)
    def patient_language_page(request: Request):
        return render(request, "patient-language-select.html")

    @app.get("/patient/reports", include_in_schema=False)
    def patient_reports_page(request: Request):
        return render(request, "patient-reports.html")

    @app.get("/patient/timeline", include_in_schema=False)
    def patient_timeline_page(request: Request):
        return render(request, "patient-timeline.html")

    @app.get("/patient/profile-settings", include_in_schema=False)
    def patient_profile_settings_page(request: Request):
        return render(request, "patient-profile-settings.html")

    @app.get("/patient/notifications", include_in_schema=False)
    def patient_notifications_page(request: Request):
        return render(request, "patient-notifications.html")

    @app.get("/patient/upload-report", include_in_schema=False)
    def patient_upload_report_page(request: Request):
        return render(request, "patient-upload-report.html")

    @app.get("/patient/health-in-my-area", include_in_schema=False)
    def patient_health_area_page(request: Request):
        return render(request, "patient-health-area.html")

    @app.get("/doctor/login", include_in_schema=False)
    def doctor_login_page(request: Request):
        return render(request, "doctor-login.html")

    @app.get("/doctor/dashboard", include_in_schema=False)
    def doctor_dashboard_page(request: Request):
        return render(request, "doctor-dashboard.html")

    @app.get("/student/register", include_in_schema=False)
    def student_register_page(request: Request):
        return render(request, "student-register.html")

    @app.get("/student/login", include_in_schema=False)
    def student_login_page(request: Request):
        return render(request, "student-login.html")

    @app.get("/student/language-selection", include_in_schema=False)
    def student_language_page(request: Request):
        return RedirectResponse(url="/student/dashboard", status_code=307)

    @app.get("/student/dashboard", include_in_schema=False)
    def student_dashboard_page(request: Request):
        return render(request, "student-dashboard.html")

    @app.get("/student/profile", include_in_schema=False)
    def student_profile_page(request: Request):
        return render(request, "student-profile.html")

    @app.get("/student/ratings", include_in_schema=False)
    def student_ratings_page(request: Request):
        return render(request, "student-ratings.html")

    @app.get("/forgot-password", include_in_schema=False)
    def forgot_password_page(request: Request, role: str = "patient"):
        return templates.TemplateResponse("forgot-password.html", {"request": request, "role": role})

    @app.get("/government/dashboard", include_in_schema=False)
    def government_dashboard_page(request: Request):
        return render(request, "government-dashboard.html")

    @app.get("/government/login", include_in_schema=False)
    def government_login_page(request: Request):
        return render(request, "government-login.html")

    @app.get("/hospital/scan", include_in_schema=False)
    def hospital_scan_page(request: Request):
        return render(request, "hospital-scan.html")

    @app.get("/session/video", include_in_schema=False)
    def video_session_page(request: Request):
        return render(request, "video-session.html")

    @app.get("/session/chat", include_in_schema=False)
    def chat_session_page(request: Request):
        return render(request, "chat-session.html")

    @app.websocket("/ws/session/{session_id}")
    async def websocket_signaling(websocket: WebSocket, session_id: str):
        role = (websocket.query_params.get("role") or "").strip().lower()
        token = (websocket.query_params.get("token") or "").strip()
        if not session_id.strip():
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "invalid_session_id"})
            await websocket.close(code=1008)
            return
        if role not in {"patient", "student"}:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "invalid_role"})
            await websocket.close(code=1008)
            return
        try:
            claims = security_service.decode_token(token)
        except ValueError:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "invalid_token"})
            await websocket.close(code=1008)
            return
        if str(claims.get("role", "")).lower() != role:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "role_token_mismatch"})
            await websocket.close(code=1008)
            return
        sub = str(claims.get("sub", ""))
        with db_session() as db:
            session = db.query(ConsultationSession).filter(ConsultationSession.session_id == session_id).first()
            if not session:
                await websocket.accept()
                await websocket.send_json({"type": "error", "message": "session_not_found"})
                await websocket.close(code=1008)
                return
            if role == "patient" and sub != session.patient_id:
                await websocket.accept()
                await websocket.send_json({"type": "error", "message": "session_forbidden"})
                await websocket.close(code=1008)
                return
            if role == "student" and sub != session.student_id:
                await websocket.accept()
                await websocket.send_json({"type": "error", "message": "session_forbidden"})
                await websocket.close(code=1008)
                return
        await signaling_manager.connect(session_id, role, websocket)
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    payload = json.loads(msg)
                except Exception:
                    payload = {"type": "raw", "data": msg}
                signal_type = str(payload.get("type", "")).lower()
                if signal_type in {"offer", "answer", "candidate", "ice-candidate"}:
                    print(f"[signal] {signal_type} received session={session_id} role={role}")
                await signaling_manager.relay(session_id, role, payload)
        except WebSocketDisconnect:
            pass
        finally:
            signaling_manager.disconnect(session_id, role, websocket)

    @app.websocket("/ws/chat/{session_id}")
    async def websocket_chat(websocket: WebSocket, session_id: str):
        token = (websocket.query_params.get("token") or "").strip()
        try:
            claims = security_service.decode_token(token)
        except ValueError:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "invalid_token"})
            await websocket.close(code=1008)
            return
        role = str(claims.get("role", "")).lower()
        sub = str(claims.get("sub", ""))
        if role not in {"patient", "student"}:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "invalid_role"})
            await websocket.close(code=1008)
            return
        with db_session() as db:
            session = db.query(ConsultationSession).filter(ConsultationSession.session_id == session_id).first()
            if not session:
                await websocket.accept()
                await websocket.send_json({"type": "error", "message": "session_not_found"})
                await websocket.close(code=1008)
                return
            if role == "patient" and sub != session.patient_id:
                await websocket.accept()
                await websocket.send_json({"type": "error", "message": "session_forbidden"})
                await websocket.close(code=1008)
                return
            if role == "student" and sub != session.student_id:
                await websocket.accept()
                await websocket.send_json({"type": "error", "message": "session_forbidden"})
                await websocket.close(code=1008)
                return
        await chat_manager.connect(session_id, websocket)
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    payload = json.loads(msg)
                except Exception:
                    payload = {"type": "chat", "text": msg}
                await chat_manager.broadcast(session_id, payload, sender=websocket)
        except WebSocketDisconnect:
            pass
        finally:
            chat_manager.disconnect(session_id, websocket)

    @app.websocket("/ws/notifications/{role}/{user_id}")
    async def websocket_notifications(websocket: WebSocket, role: str, user_id: str):
        token = (websocket.query_params.get("token") or "").strip()
        try:
            claims = security_service.decode_token(token)
        except ValueError:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "invalid_token"})
            await websocket.close(code=1008)
            return
        if str(claims.get("role", "")).lower() != role.lower().strip() or str(claims.get("sub", "")) != user_id:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "token_subject_mismatch"})
            await websocket.close(code=1008)
            return
        await notification_manager.connect(role, user_id, websocket)
        await websocket.send_json({"type": "system", "message": "notifications_connected"})
        try:
            while True:
                _ = await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            notification_manager.disconnect(role, user_id, websocket)

    return app


app = create_app()

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, func, case
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db import get_db
from app.models.db_models import ConsultationSession, Patient, Diagnosis, Visit, ChatMessage, ConsultationLog, Notification, Student, GovernmentOfficial, DoctorManualReport
from app.services.ai_service import ai_service
from app.services.email_service import email_service
from app.services.otp_service import otp_service
from app.services.patient_service import patient_service
from app.services.relational_service import relational_service
from app.services.realtime_service import notification_manager
from app.services.security_service import security_service
from app.services.sms_service import sms_service

router = APIRouter(prefix="/api", tags=["api"])
PATIENT_UPLOAD_DIR = Path("app/uploads/patient_reports")
PATIENT_UPLOAD_INDEX = PATIENT_UPLOAD_DIR / "index.json"
DOCTOR_MANUAL_REPORT_DIR = Path("app/uploads/doctor_manual_reports")
DOCTOR_MANUAL_REPORT_INDEX = DOCTOR_MANUAL_REPORT_DIR / "index.json"


def _parse_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    return parts[1].strip()


def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = _parse_bearer_token(authorization)
    try:
        return security_service.decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def government_user(user: dict = Depends(current_user)) -> dict:
    if str(user.get("role", "")).lower() != "government":
        raise HTTPException(status_code=403, detail="Government access required")
    return user


def _authorize_session_actor(user: dict, session: ConsultationSession, allow_roles: set[str]) -> None:
    role = str(user.get("role", "")).lower()
    sub = str(user.get("sub", ""))
    if role not in allow_roles:
        raise HTTPException(status_code=403, detail="Role not allowed")
    if role == "patient" and sub != session.patient_id:
        raise HTTPException(status_code=403, detail="Patient not part of session")
    if role == "student" and sub != session.student_id:
        raise HTTPException(status_code=403, detail="Student not part of session")


def _log_consultation_event(db: Session, session_id: str, event_type: str, metadata: dict | None = None) -> None:
    row = ConsultationLog(
        session_id=session_id,
        event_type=event_type,
        metadata_json=json.dumps(metadata or {}, default=str),
    )
    db.add(row)
    db.commit()


def _persist_notification(db: Session, role: str, user_id: str, ntype: str, message: str) -> None:
    db.add(Notification(user_id=user_id, role=role, type=ntype, message=message, read_status=False))
    db.commit()


def _disease_name(category: int) -> str:
    names = {
        1: "Respiratory",
        2: "Gastrointestinal",
        3: "Musculoskeletal",
        4: "Neurological",
        5: "Dermatological",
        6: "Cardiovascular",
        7: "Endocrine",
        8: "Genitourinary",
        9: "ENT",
        10: "Ophthalmic",
        11: "Mental Health",
        12: "General / Other",
    }
    return names.get(int(category or 12), "General / Other")


def _wellness_tip_for_category(category: int) -> str:
    tips = {
        1: "Breathe steam, drink warm fluids, and practice gentle pranayama daily.",
        2: "Prefer light warm meals, avoid oily/spicy food, and maintain hydration.",
        3: "Do gentle stretching/yoga and keep good posture through the day.",
        4: "Maintain sleep routine, hydration, and reduce screen strain.",
        5: "Keep skin clean, hydrated, and avoid known irritants/allergens.",
        6: "Limit salt, walk daily, manage stress, and monitor BP regularly.",
        7: "Reduce refined sugar, eat balanced meals, and exercise consistently.",
        8: "Stay hydrated, maintain hygiene, and avoid delaying urination.",
        9: "Use warm saline gargles/steam and avoid very cold beverages.",
        10: "Reduce screen glare, rest eyes, and maintain proper lighting.",
        11: "Follow stress-control practices: yoga, breathing, and regular sleep.",
        12: "Maintain a balanced AYUSH lifestyle: yoga, satvik meals, and hydration.",
    }
    return tips.get(int(category or 12), tips[12])


def _build_patient_ai_health_notifications(db: Session, uhid: str, visit_id: str | None) -> list[str]:
    q = select(Diagnosis).where(Diagnosis.uhid == uhid)
    if visit_id:
        q = q.where(Diagnosis.visit_id == visit_id)
    rows = db.scalars(q.order_by(Diagnosis.created_at.desc())).all()
    if not rows:
        return [
            "AI Health Tip: Maintain hydration, daily yoga, balanced meals, and adequate sleep.",
        ]
    seen: set[int] = set()
    out: list[str] = []
    for row in rows:
        cat = int(row.disease_category or 12)
        if cat in seen:
            continue
        seen.add(cat)
        out.append(
            f"AI Health Tip ({_disease_name(cat)}): {_wellness_tip_for_category(cat)}"
        )
        if len(out) >= 3:
            break
    return out


async def _emit_government_outbreak_notifications(
    db: Session,
    disease_category: int,
    state: str,
    district: str,
) -> None:
    threshold = int(os.getenv("GOV_OUTBREAK_THRESHOLD", "5") or 5)
    if not state or not district:
        return
    window_days = int(os.getenv("GOV_OUTBREAK_WINDOW_DAYS", "3") or 3)
    now = datetime.utcnow()
    since = now - timedelta(days=window_days)
    case_count = (
        db.query(func.count(Diagnosis.id))
        .join(Patient, Patient.uhid == Diagnosis.uhid)
        .filter(
            Diagnosis.disease_category == disease_category,
            Diagnosis.created_at >= since,
            Patient.state == state,
            Patient.district == district,
        )
        .scalar()
        or 0
    )
    if int(case_count) < threshold:
        return

    disease = _disease_name(disease_category)
    message = (
        f"Outbreak Alert: {case_count} recent {disease} cases in {district}, {state} "
        f"(last {window_days} days)."
    )
    dedupe_since = now - timedelta(hours=6)
    duplicate = db.scalar(
        select(Notification).where(
            Notification.role == "government",
            Notification.type == "outbreak_alert",
            Notification.message == message,
            Notification.created_at >= dedupe_since,
        )
    )
    if duplicate:
        return

    officials = db.scalars(
        select(GovernmentOfficial).where(GovernmentOfficial.is_active.is_(True))
    ).all()
    for off in officials:
        _persist_notification(db, "government", off.username, "outbreak_alert", message)
        await notification_manager.push(
            "government",
            off.username,
            {
                "type": "outbreak_alert",
                "title": "Disease Spike Detected",
                "message": message,
                "state": state,
                "district": district,
                "disease_category": disease_category,
                "created_at": now.isoformat(),
            },
        )


def _ensure_upload_store() -> None:
    PATIENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not PATIENT_UPLOAD_INDEX.exists():
        PATIENT_UPLOAD_INDEX.write_text("{}", encoding="utf-8")


def _read_upload_index() -> dict[str, list[dict]]:
    _ensure_upload_store()
    try:
        raw = PATIENT_UPLOAD_INDEX.read_text(encoding="utf-8").strip() or "{}"
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _write_upload_index(payload: dict[str, list[dict]]) -> None:
    _ensure_upload_store()
    PATIENT_UPLOAD_INDEX.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _ensure_doctor_manual_store() -> None:
    DOCTOR_MANUAL_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not DOCTOR_MANUAL_REPORT_INDEX.exists():
        DOCTOR_MANUAL_REPORT_INDEX.write_text("{}", encoding="utf-8")


def _read_doctor_manual_index() -> dict[str, list[dict]]:
    _ensure_doctor_manual_store()
    try:
        raw = DOCTOR_MANUAL_REPORT_INDEX.read_text(encoding="utf-8").strip() or "{}"
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _write_doctor_manual_index(payload: dict[str, list[dict]]) -> None:
    _ensure_doctor_manual_store()
    DOCTOR_MANUAL_REPORT_INDEX.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _otp_debug_enabled() -> bool:
    v = os.getenv("OTP_DEBUG_ECHO", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _phone_otp_enabled() -> bool:
    # Optional explicit override.
    override = os.getenv("OTP_PHONE_ENABLED", "").strip().lower()
    if override:
        return override in {"1", "true", "yes", "on"}
    # Safe default: disable when Twilio is disabled.
    twilio_disabled = os.getenv("TWILIO_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    return not twilio_disabled


def _validate_identifier_channel(identifier: str, channel: str) -> str:
    value = (identifier or "").strip()
    if not value:
        raise ValueError("Identifier is required")
    ch = (channel or "email").strip().lower()
    if ch == "email":
        if "@" not in value:
            raise ValueError("Provide a registered email for Email OTP")
        return value.lower()
    if ch == "phone":
        if not value.isdigit() or len(value) < 10 or len(value) > 15:
            raise ValueError("Provide a valid registered phone number for SMS OTP")
        return value
    raise ValueError("Invalid channel")


class PatientRegisterRequest(BaseModel):
    full_name: str
    aadhaar: str
    dob: str
    email: EmailStr
    phone: str
    language: str
    otp: str
    address: str
    blood_group: str
    live_location_enabled: bool = False
    latitude: float = 0.0
    longitude: float = 0.0
    password: str
    confirm_password: str


class PatientLoginRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    password: str


class ForgotPasswordRequest(BaseModel):
    identifier: str
    channel: str = "email"
    role: str = "patient"


class VerifyForgotOTPRequest(BaseModel):
    identifier: str
    otp: str
    role: str = "patient"
    channel: str = "email"


class ResetPasswordRequest(BaseModel):
    identifier: str
    otp: str | None = None
    reset_token: str | None = None
    new_password: str
    role: str = "patient"
    channel: str = "email"


class AIChatRequest(BaseModel):
    question: str = Field(
        min_length=1,
        description="Patient question or symptom context for AI assistant.",
        examples=["I have mild fever and dry cough for 2 days. What should I do?"],
    )
    language: str | None = None


class AIChatResponse(BaseModel):
    question: str
    guidance: list[str]
    summary: dict
    urgency: str | None = "medium"
    provider: str
    disclaimer: str


class ChatPromptRequest(BaseModel):
    message: str = Field(
        min_length=1,
        description="General AI chat message. Used by realtime consultation chat helper endpoint.",
    )
    session_id: str | None = None
    language: str | None = None


class HospitalScanRequest(BaseModel):
    qr_token: str


class DoctorRegisterRequest(BaseModel):
    full_name: str
    government_license_id: str
    otp: str
    email: EmailStr
    phone: str
    address: str
    password: str
    confirm_password: str


class DoctorLoginRequest(BaseModel):
    identifier: str = Field(alias="username")
    password: str
    model_config = {"populate_by_name": True}


class DoctorProfileUpdateRequest(BaseModel):
    doctor_id: str
    full_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None


class PatientReportNotifyRequest(BaseModel):
    uhid: str
    doctor_id: str
    visit_id: str | None = None


class PatientProfileUpdateRequest(BaseModel):
    patient_id: str
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    language: str | None = None


class VisitCreateRequest(BaseModel):
    uhid: str = Field(alias="UHID")
    doctor_id: Optional[str] = Field(default=None, alias="Doctor_ID")
    student_id: Optional[str] = Field(default=None, alias="Student_ID")
    consultation_type: str = Field(default="doctor", alias="Consultation_Type")
    visit_notes: str = Field(default="", alias="Visit_Notes")
    visit_date: Optional[str] = Field(default=None, alias="Visit_Date")

    model_config = {"populate_by_name": True}


class DiagnosisCreateRequest(BaseModel):
    visit_id: str
    uhid: str = Field(alias="UHID")
    disease_category: int = Field(alias="Disease_Category", ge=1, le=12)
    disease_name: Optional[str] = Field(default=None, alias="Disease_Name")
    severity: Optional[str] = Field(default=None, alias="Severity")

    model_config = {"populate_by_name": True}


class RecommendationCreateRequest(BaseModel):
    visit_id: str
    uhid: str = Field(alias="UHID")
    advice_text: str = Field(alias="Advice_Text")
    confidence_score: float = Field(alias="Confidence_Score", ge=0, le=1)

    model_config = {"populate_by_name": True}


class ConsultationSummarizeRequest(BaseModel):
    transcription: str = Field(
        min_length=1,
        description="Full consultation transcript text to summarize into structured clinical output.",
    )


class EnglishNormalizeRequest(BaseModel):
    text: str = Field(min_length=1, description="Input text in any language to normalize into English.")


class StudentVerifyRequest(BaseModel):
    college_id: str
    institute_name: str = "AYUSH Medical College"
    official_email: EmailStr


class StudentConfirmOTPRequest(BaseModel):
    college_id: str
    otp: str


class StudentRegisterRequest(BaseModel):
    college_id: str
    institute_name: str = "AYUSH Medical College"
    official_email: EmailStr
    phone: str | None = None
    full_name: str | None = None
    password: str


class StudentLoginRequest(BaseModel):
    identifier: str | None = None
    official_email: str | None = None
    password: str


class StudentOnlineRequest(BaseModel):
    student_id: str
    language: str


class StudentProfileUpdateRequest(BaseModel):
    student_id: str
    full_name: str | None = None
    institute_name: str | None = None
    official_email: EmailStr | None = None
    phone: str | None = None
    languages: list[str] | None = None
    language_preference: str | None = None


class SessionAcceptRequest(BaseModel):
    session_id: str


class SessionEndRequest(BaseModel):
    session_id: str


class SessionRejectRequest(BaseModel):
    session_id: str


class ConsultantVideoRequest(BaseModel):
    patient_id: str
    language: str
    problem: str = "General consultation"


class ChatSaveRequest(BaseModel):
    session_id: str
    sender_role: str
    sender_id: str
    message_text: str


class VideoJoinRequest(BaseModel):
    session_id: str
    requester_id: str


@router.get("/session/status/{session_id}")
def session_status(session_id: str, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    sess = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == session_id))
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    _authorize_session_actor(user, sess, {"patient", "student"})
    return {
        "session_id": sess.session_id,
        "status": sess.status,
        "mode": sess.mode,
        "patient_id": sess.patient_id,
        "student_id": sess.student_id,
        "created_at": sess.created_at.isoformat() if sess.created_at else None,
        "accepted_at": sess.accepted_at.isoformat() if sess.accepted_at else None,
        "ended_at": sess.ended_at.isoformat() if sess.ended_at else None,
    }


class RegistrationOTPRequest(BaseModel):
    identifier: str
    channel: str = "email"


class VerifyRegistrationOTPRequest(BaseModel):
    identifier: str
    otp: str
    channel: str = "email"


class TestNotificationRequest(BaseModel):
    role: str
    user_id: str
    title: str = "Test Notification"
    message: str
    type: str = "system"


class MarkNotificationReadRequest(BaseModel):
    notification_id: int


class SessionLogEventRequest(BaseModel):
    session_id: str
    event_type: str
    metadata: dict = {}


class ConnectNowRequest(BaseModel):
    language: str
    problem: str = "General consultation"
    mode: str = "chat"


class SessionRatingRequest(BaseModel):
    session_id: str
    rating: int = Field(ge=1, le=5)
    feedback: str = ""


class GovernmentLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/patient/register")
def register_patient(payload: PatientRegisterRequest, db: Session = Depends(get_db)):
    try:
        relational_service.verify_patient_registration_otp(payload.email, payload.otp)
    except ValueError:
        try:
            # fallback: allow phone-based registration OTP
            relational_service.verify_patient_registration_otp(payload.phone, payload.otp)
        except ValueError as exc:
            # Twilio Verify fallback for phone OTP registration.
            if not sms_service.verify_otp_phone(payload.phone, payload.otp):
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return relational_service.register_patient(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Duplicate registration data. Check email/phone/aadhaar.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error during patient registration.") from exc


@router.post("/patient/login")
def patient_login(payload: PatientLoginRequest, db: Session = Depends(get_db)):
    try:
        return relational_service.login_patient(db, payload.full_name, payload.phone, str(payload.email) if payload.email else None, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/send-registration-otp")
def send_registration_otp(payload: RegistrationOTPRequest, db: Session = Depends(get_db)):
    try:
        data = relational_service.send_patient_registration_otp(db, payload.identifier)
        delivery: dict = {"channel": payload.channel, "sent": False}
        channel = payload.channel.strip().lower()
        if channel == "phone" and not _phone_otp_enabled():
            raise ValueError("SMS OTP is disabled. Use Email OTP.")
        if channel == "email":
            delivery = email_service.send_otp_email(
                payload.identifier,
                "Account Verification",
                data["otp_dev_hint"],
            )
        if channel == "phone":
            delivery = sms_service.send_otp_phone(payload.identifier)
        response = {"status": "otp_sent", "expires_at": data["expires_at"], "delivery": delivery}
        # Dev-only: allow easy end-to-end UI testing even if email/SMS delivery is delayed or blocked.
        if _otp_debug_enabled() or not delivery.get("sent", False):
            response["otp_debug"] = data["otp_dev_hint"]
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/send-registration-otp")
def send_registration_otp_get(
    identifier: str = Query(...),
    channel: str = Query(default="email"),
    db: Session = Depends(get_db),
):
    payload = RegistrationOTPRequest(identifier=identifier, channel=channel)
    return send_registration_otp(payload, db)


@router.post("/verify-registration-otp")
def verify_registration_otp(payload: VerifyRegistrationOTPRequest):
    try:
        if payload.channel.strip().lower() == "phone" and not _phone_otp_enabled():
            raise ValueError("SMS OTP is disabled. Use Email OTP.")
        if payload.channel.strip().lower() == "phone":
            # Prefer provider verification.
            if sms_service.verify_otp_phone(payload.identifier, payload.otp):
                return {"status": "otp_verified"}
            # Dev-only fallback: allow verifying against the internally issued OTP.
            if _otp_debug_enabled():
                return relational_service.verify_patient_registration_otp(payload.identifier, payload.otp)
            raise ValueError("Invalid or expired OTP")
            return {"status": "otp_verified"}
        return relational_service.verify_patient_registration_otp(payload.identifier, payload.otp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/verify-registration-otp")
def verify_registration_otp_get(
    identifier: str = Query(...),
    otp: str = Query(...),
    channel: str = Query(default="email"),
):
    payload = VerifyRegistrationOTPRequest(identifier=identifier, otp=otp, channel=channel)
    return verify_registration_otp(payload)


@router.post("/forgot-password")
def patient_forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    try:
        role = payload.role.strip().lower()
        channel = payload.channel.strip().lower()
        if role not in {"patient", "doctor", "student"}:
            raise ValueError("Invalid role")
        if channel not in {"email", "phone"}:
            raise ValueError("Invalid channel")
        if channel == "phone" and not _phone_otp_enabled():
            raise ValueError("SMS OTP is disabled. Use Email OTP.")
        identifier = _validate_identifier_channel(payload.identifier, channel)
        data = relational_service.send_forgot_password_otp(db, role, identifier)
        delivery: dict = {"channel": payload.channel, "sent": False}
        if channel == "email":
            delivery = email_service.send_otp_email(
                identifier,
                "Password Reset Verification",
                data["otp_dev_hint"],
            )
        if channel == "phone":
            delivery = sms_service.send_otp_phone(identifier)
        response = {"status": "otp_sent", "expires_at": data["expires_at"], "delivery": delivery}
        # Dev-only: allow easy end-to-end UI testing even if email/SMS delivery is delayed or blocked.
        if _otp_debug_enabled() or not delivery.get("sent", False):
            response["otp_debug"] = data["otp_dev_hint"]
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/verify-forgot-otp")
def verify_forgot_otp(payload: VerifyForgotOTPRequest):
    try:
        channel = payload.channel.strip().lower()
        if channel == "phone" and not _phone_otp_enabled():
            raise ValueError("SMS OTP is disabled. Use Email OTP.")
        identifier = _validate_identifier_channel(payload.identifier, channel)
        if channel == "phone":
            if sms_service.verify_otp_phone(identifier, payload.otp):
                token, expires_at = otp_service.issue_ticket(
                    f"{payload.role.strip().lower()}_forgot_verified",
                    identifier.lower().strip(),
                )
                return {"status": "otp_verified", "reset_token": token, "reset_token_expires_at": expires_at}
            if _otp_debug_enabled():
                relational_service.verify_forgot_otp(payload.role, identifier, payload.otp)
                token, expires_at = otp_service.issue_ticket(
                    f"{payload.role.strip().lower()}_forgot_verified",
                    identifier.lower().strip(),
                )
                return {"status": "otp_verified", "reset_token": token, "reset_token_expires_at": expires_at}
            raise ValueError("Invalid or expired OTP")
            return {"status": "otp_verified"}
        relational_service.verify_forgot_otp(payload.role, identifier, payload.otp)
        token, expires_at = otp_service.issue_ticket(
            f"{payload.role.strip().lower()}_forgot_verified",
            identifier.lower().strip(),
        )
        return {"status": "otp_verified", "reset_token": token, "reset_token_expires_at": expires_at}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/patient/forgot-password")
def patient_forgot_password_compat(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    return patient_forgot_password(payload, db)


@router.post("/patient/reset-password")
def patient_reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        identifier = _validate_identifier_channel(payload.identifier, payload.channel)
        if payload.channel.strip().lower() == "phone" and not _phone_otp_enabled():
            raise ValueError("SMS OTP is disabled. Use Email OTP.")
        if payload.reset_token:
            ok = otp_service.consume_ticket(
                f"{payload.role.strip().lower()}_forgot_verified",
                identifier.lower().strip(),
                payload.reset_token,
            )
            if not ok:
                raise ValueError("Invalid or expired reset token. Verify OTP again.")
        elif payload.otp:
            if payload.channel.strip().lower() == "phone":
                if not sms_service.verify_otp_phone(identifier, payload.otp):
                    raise ValueError("Invalid or expired OTP")
            else:
                relational_service.verify_forgot_otp(payload.role, identifier, payload.otp)
        else:
            raise ValueError("OTP or reset token is required")
        return relational_service.reset_password_by_role(db, payload.role, identifier, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error during password reset.") from exc


@router.get("/patient/profile")
def patient_profile(patient_id: str = Query(...), db: Session = Depends(get_db)):
    try:
        return relational_service.get_patient_profile(db, patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/patient/profile/update")
def patient_profile_update(payload: PatientProfileUpdateRequest, db: Session = Depends(get_db)):
    try:
        data = payload.model_dump()
        patient_id = data.pop("patient_id")
        return relational_service.update_patient_profile(db, patient_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/patient/qr")
def patient_qr(patient_id: str = Query(...), db: Session = Depends(get_db)):
    try:
        path = relational_service.get_patient_qr(db, patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path)


@router.post("/patient/upload-report")
async def patient_upload_report(
    patient_id: str = Form(...),
    file: UploadFile = File(...),
    hospital_name: str = Form(default="External Hospital"),
    note: str = Form(default=""),
):
    _ensure_upload_store()
    safe_patient_id = patient_id.strip().replace("/", "_").replace("\\", "_")
    if not safe_patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")
    patient_dir = PATIENT_UPLOAD_DIR / safe_patient_id
    patient_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    original = Path(file.filename or "uploaded_report").name
    stored_name = f"{timestamp}_{original}"
    save_path = patient_dir / stored_name
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    save_path.write_bytes(content)

    index = _read_upload_index()
    reports = index.setdefault(safe_patient_id, [])
    record = {
        "file_name": stored_name,
        "original_name": original,
        "hospital_name": hospital_name,
        "note": note,
        "uploaded_at": datetime.utcnow().isoformat(),
        "size_bytes": len(content),
        "content_type": file.content_type or "application/octet-stream",
    }
    reports.append(record)
    _write_upload_index(index)
    return {"uploaded": True, "patient_id": safe_patient_id, "report": record}


@router.get("/patient/uploaded-reports")
def patient_uploaded_reports(patient_id: str = Query(...)):
    index = _read_upload_index()
    return {"patient_id": patient_id, "reports": index.get(patient_id, [])}


@router.get("/patient/uploaded-report-file/{patient_id}/{file_name}")
def patient_uploaded_report_file(patient_id: str, file_name: str):
    safe_patient_id = patient_id.strip().replace("/", "_").replace("\\", "_")
    safe_file = Path(file_name).name
    path = PATIENT_UPLOAD_DIR / safe_patient_id / safe_file
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=safe_file)


@router.post("/doctor/upload-manual-report")
async def doctor_upload_manual_report(
    uhid: str = Form(...),
    visit_id: str = Form(...),
    doctor_id: str = Form(...),
    file: UploadFile = File(...),
    note: str = Form(default=""),
    db: Session = Depends(get_db),
):
    _ensure_doctor_manual_store()
    safe_uhid = uhid.strip().replace("/", "_").replace("\\", "_")
    safe_visit_id = visit_id.strip().replace("/", "_").replace("\\", "_")
    safe_doctor_id = doctor_id.strip().replace("/", "_").replace("\\", "_")
    if not safe_uhid or not safe_visit_id or not safe_doctor_id:
        raise HTTPException(status_code=400, detail="uhid, visit_id and doctor_id are required")

    patient_dir = DOCTOR_MANUAL_REPORT_DIR / safe_uhid
    patient_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    original = Path(file.filename or "manual_report").name
    stored_name = f"{timestamp}_{safe_visit_id}_{original}"
    save_path = patient_dir / stored_name
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    save_path.write_bytes(content)

    record = {
        "visit_id": safe_visit_id,
        "doctor_id": safe_doctor_id,
        "file_name": stored_name,
        "original_name": original,
        "note": note,
        "uploaded_at": datetime.utcnow().isoformat(),
        "size_bytes": len(content),
        "content_type": file.content_type or "application/octet-stream",
        "file_url": f"/api/doctor/manual-report-file/{safe_uhid}/{stored_name}",
    }
    db.add(
        DoctorManualReport(
            uhid=safe_uhid,
            visit_id=safe_visit_id,
            doctor_id=safe_doctor_id,
            file_name=stored_name,
            original_name=original,
            file_url=record["file_url"],
            note=note,
            content_type=record["content_type"],
            size_bytes=record["size_bytes"],
            uploaded_at=datetime.utcnow(),
        )
    )
    db.commit()
    return {"uploaded": True, "uhid": safe_uhid, "manual_report": record}


@router.get("/doctor/manual-report-file/{uhid}/{file_name}")
def doctor_manual_report_file(uhid: str, file_name: str):
    safe_uhid = uhid.strip().replace("/", "_").replace("\\", "_")
    safe_file = Path(file_name).name
    path = DOCTOR_MANUAL_REPORT_DIR / safe_uhid / safe_file
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=safe_file)


@router.get("/doctor/manual-reports")
def doctor_manual_reports(uhid: str = Query(...), visit_id: str | None = Query(default=None), db: Session = Depends(get_db)):
    q = select(DoctorManualReport).where(DoctorManualReport.uhid == uhid)
    if visit_id:
        q = q.where(DoctorManualReport.visit_id == visit_id)
    q = q.order_by(DoctorManualReport.uploaded_at.desc())
    rows = db.scalars(q).all()
    reports = [
        {
            "visit_id": r.visit_id,
            "doctor_id": r.doctor_id,
            "file_name": r.file_name,
            "original_name": r.original_name,
            "file_url": r.file_url,
            "note": r.note,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "size_bytes": r.size_bytes,
            "content_type": r.content_type,
        }
        for r in rows
    ]
    return {"uhid": uhid, "reports": reports}


@router.get("/patient/notifications")
def api_patient_notifications(patient_id: str = Query(...), db: Session = Depends(get_db)):
    try:
        items = patient_service.get_notifications(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    out = [item.model_dump() for item in items]
    for row in out:
        created_at = row.get("created_at")
        if isinstance(created_at, datetime):
            row["created_at"] = created_at.isoformat()
        elif created_at is None:
            row["created_at"] = ""
        else:
            row["created_at"] = str(created_at)
    db_rows = db.scalars(
        select(Notification)
        .where(Notification.role == "patient", Notification.user_id == patient_id)
        .order_by(Notification.created_at.desc())
        .limit(100)
    ).all()
    for row in db_rows:
        out.append(
            {
                "message": row.message,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "category": row.type or "system",
                "read_status": bool(row.read_status),
            }
        )
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


@router.post(
    "/ai/chat",
    response_model=AIChatResponse,
    summary="Patient AI Assistant",
    description=(
        "Patient-facing chatbot endpoint. Accepts free-text question context and returns guidance, "
        "basic symptom tags, urgency hint, and safety disclaimer. "
        "If external AI provider is unavailable, returns a safe fallback response."
    ),
)
def ai_chat(payload: AIChatRequest):
    return ai_service.patient_chatbot_reply(payload.question)


@router.post("/chat")
def chat(payload: ChatPromptRequest):
    reply = ai_service.assistant_reply(payload.message)
    return {
        "session_id": payload.session_id,
        "message": payload.message,
        "response": " ".join(reply.get("guidance", [])),
        "guidance": reply.get("guidance", []),
    }


@router.post("/hospital/scan")
def hospital_scan(payload: HospitalScanRequest, db: Session = Depends(get_db)):
    try:
        return relational_service.process_qr_scan(db, payload.qr_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/doctor/register")
def doctor_register(payload: DoctorRegisterRequest, db: Session = Depends(get_db)):
    if len(payload.otp.strip()) < 4:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    try:
        return relational_service.register_doctor(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Duplicate doctor registration data.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error during doctor registration.") from exc


@router.post("/doctor/login")
def doctor_login(payload: DoctorLoginRequest, db: Session = Depends(get_db)):
    try:
        return relational_service.login_doctor(db, payload.identifier, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/doctor/stats")
def doctor_stats(doctor_id: str, db: Session = Depends(get_db)):
    return relational_service.doctor_stats(db, doctor_id)


@router.get("/doctor/profile")
def doctor_profile(doctor_id: str, db: Session = Depends(get_db)):
    try:
        return relational_service.get_doctor_profile(db, doctor_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/doctor/profile/update")
def doctor_profile_update(payload: DoctorProfileUpdateRequest, db: Session = Depends(get_db)):
    try:
        data = payload.model_dump()
        doctor_id = data.pop("doctor_id")
        return relational_service.update_doctor_profile(db, doctor_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str | None = Form(default=None)):
    filename = (file.filename or "audio.webm").lower()
    content_type = (file.content_type or "").lower()
    allowed_ext = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_ext and not content_type.startswith(("audio/", "video/")):
        raise HTTPException(status_code=400, detail="Unsupported audio format. Use webm/wav/mp3/m4a/ogg/mp4.")
    content = await file.read()
    # Fast-fail for empty/tiny payloads so UI buttons do not appear stuck.
    if not content or len(content) < 1024:
        return {"transcript": "", "confidence": 0.0, "warning": "No speech detected in audio"}
    try:
        return ai_service.transcribe_audio_bytes(file.filename or "audio.webm", content, language_hint=language)
    except RuntimeError as exc:
        msg = str(exc)
        if "no speech detected" in msg.lower():
            return {"transcript": "", "confidence": 0.0, "warning": "No speech detected in audio"}
        raise HTTPException(status_code=500, detail=msg) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Audio transcription failed: {exc}") from exc


@router.post("/consultation/transcribe-audio")
async def transcribe_audio(file: UploadFile = File(...)):
    return await transcribe(file)


@router.post("/consultation/transcribe")
async def transcribe_compatible(
    manual_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
):
    if file is not None:
        return await transcribe(file)
    if manual_text:
        raise HTTPException(status_code=400, detail="manual_text mode removed. Upload .webm audio via multipart/form-data.")
    raise HTTPException(status_code=400, detail="Provide either manual_text or file")


@router.post("/consultation/summarize")
def summarize_consultation(payload: ConsultationSummarizeRequest):
    return ai_service.summarize_consultation(payload.transcription)


@router.post("/text/normalize-english")
def normalize_english(payload: EnglishNormalizeRequest):
    return {"text": ai_service.normalize_to_english(payload.text)}


@router.post("/create_visit")
def create_visit(payload: VisitCreateRequest, db: Session = Depends(get_db)):
    data = payload.model_dump(by_alias=False)
    if not data.get("visit_date"):
        data["visit_date"] = datetime.utcnow().isoformat()
    try:
        return relational_service.create_visit(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/create_diagnosis")
async def create_diagnosis(payload: DiagnosisCreateRequest, db: Session = Depends(get_db)):
    try:
        result = relational_service.create_diagnosis(db, payload.model_dump(by_alias=False))
        patient = db.scalar(select(Patient).where(Patient.uhid == payload.uhid))
        if patient:
            await _emit_government_outbreak_notifications(
                db,
                int(payload.disease_category),
                str(patient.state or ""),
                str(patient.district or ""),
            )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/create_recommendation")
def create_recommendation(payload: RecommendationCreateRequest, db: Session = Depends(get_db)):
    try:
        return relational_service.create_recommendation(db, payload.model_dump(by_alias=False))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/patient/notify-report-updated")
async def patient_notify_report_updated(payload: PatientReportNotifyRequest, db: Session = Depends(get_db)):
    try:
        result = relational_service.notify_patient_report_updated(db, payload.uhid, payload.doctor_id, payload.visit_id)
        await notification_manager.push(
            "patient",
            result["patient_id"],
            {
                "type": "report_update",
                "title": "New Medical Report Generated",
                "message": f"New medical report updated by {payload.doctor_id}",
                "visit_id": payload.visit_id or "",
                "uhid": payload.uhid,
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        tips = _build_patient_ai_health_notifications(db, payload.uhid, payload.visit_id)
        for tip in tips:
            duplicate = db.scalar(
                select(Notification).where(
                    Notification.role == "patient",
                    Notification.user_id == result["patient_id"],
                    Notification.type == "ai_health_tip",
                    Notification.message == tip,
                    Notification.created_at >= datetime.utcnow() - timedelta(hours=12),
                )
            )
            if duplicate:
                continue
            _persist_notification(db, "patient", result["patient_id"], "ai_health_tip", tip)
            await notification_manager.push(
                "patient",
                result["patient_id"],
                {
                    "type": "ai_health_tip",
                    "title": "AI Wellness Guidance",
                    "message": tip,
                    "visit_id": payload.visit_id or "",
                    "uhid": payload.uhid,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/patient_history/{uhid}")
def patient_history(uhid: str, db: Session = Depends(get_db)):
    payload = relational_service.fetch_patient_history(db, uhid)
    by_visit: dict[str, list[dict]] = {}
    rows = db.scalars(
        select(DoctorManualReport)
        .where(DoctorManualReport.uhid == uhid)
        .order_by(DoctorManualReport.uploaded_at.desc())
    ).all()
    for r in rows:
        row_copy = {
            "visit_id": r.visit_id,
            "doctor_id": r.doctor_id,
            "file_name": r.file_name,
            "original_name": r.original_name,
            "file_url": r.file_url,
            "note": r.note,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "size_bytes": r.size_bytes,
            "content_type": r.content_type,
        }
        by_visit.setdefault(r.visit_id, []).append(row_copy)
    for entry in payload.get("history", []):
        visit = entry.get("visit", {}) or {}
        vid = str(visit.get("visit_id", "")).strip()
        entry["manual_reports"] = by_visit.get(vid, [])
    return payload


@router.get("/patient_summary/{uhid}")
def patient_summary(uhid: str, db: Session = Depends(get_db)):
    try:
        return relational_service.fetch_patient_summary(db, uhid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/alerts/{state}/{district}")
def alerts(state: str, district: str, db: Session = Depends(get_db)):
    return {"alerts": relational_service.fetch_alerts(db, state, district)}


@router.get("/gov/state-summary")
def gov_state_summary(db: Session = Depends(get_db), _user: dict = Depends(government_user)):
    return relational_service.state_summary(db)


@router.get("/gov/district-summary")
def gov_district_summary(db: Session = Depends(get_db), _user: dict = Depends(government_user)):
    return relational_service.district_summary(db)


@router.post("/student/verify-college")
def student_verify(payload: StudentVerifyRequest, db: Session = Depends(get_db)):
    try:
        return relational_service.verify_college_db(db, payload.college_id, payload.institute_name, str(payload.official_email))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/student/confirm-otp")
def student_confirm(payload: StudentConfirmOTPRequest, db: Session = Depends(get_db)):
    try:
        return relational_service.confirm_student_otp_db(db, payload.college_id, payload.otp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/student/register")
def student_register(payload: StudentRegisterRequest, db: Session = Depends(get_db)):
    try:
        return relational_service.register_student(
            db,
            payload.college_id,
            payload.institute_name,
            str(payload.official_email),
            payload.phone,
            payload.password,
            payload.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Duplicate student registration data.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error during student registration.") from exc


@router.post("/student/login")
def student_login(payload: StudentLoginRequest, db: Session = Depends(get_db)):
    try:
        identifier = (payload.identifier or payload.official_email or "").strip()
        if not identifier:
            raise ValueError("identifier or official_email is required")
        return relational_service.login_student(db, identifier, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/student/online")
def student_online(payload: StudentOnlineRequest, db: Session = Depends(get_db)):
    try:
        return relational_service.set_student_online(db, payload.student_id, payload.language)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/student/profile")
def student_profile(student_id: str = Query(...), db: Session = Depends(get_db)):
    try:
        return relational_service.get_student_profile(db, student_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/student/profile/update")
def student_profile_update(payload: StudentProfileUpdateRequest, db: Session = Depends(get_db)):
    try:
        data = payload.model_dump()
        student_id = data.pop("student_id")
        if data.get("official_email") is not None:
            data["official_email"] = str(data["official_email"])
        return relational_service.update_student_profile(db, student_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/session/accept")
async def session_accept(payload: SessionAcceptRequest, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    try:
        sess = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == payload.session_id))
        if not sess:
            raise ValueError("Session not found")
        _authorize_session_actor(user, sess, {"student"})
        result = relational_service.accept_consultation_session(db, payload.session_id)
        if sess:
            result["mode"] = sess.mode
            _log_consultation_event(
                db,
                payload.session_id,
                "SESSION_ACCEPTED",
                {"student_id": sess.student_id, "patient_id": sess.patient_id, "mode": sess.mode},
            )
        if sess:
            _persist_notification(db, "patient", sess.patient_id, "session_update", f"Your consultation was accepted by {sess.student_id}.")
            await notification_manager.push(
                "patient",
                sess.patient_id,
                {
                    "type": "session_update",
                    "title": "Consultation Accepted",
                    "message": f"Your consultation was accepted by {sess.student_id}.",
                    "session_id": payload.session_id,
                    "mode": sess.mode,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/session/reject-rematch")
async def session_reject_rematch(payload: SessionRejectRequest, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    role = str(user.get("role", "")).lower()
    student_id = str(user.get("sub", ""))
    if role != "student":
        raise HTTPException(status_code=403, detail="Only student can reject consultation requests")
    try:
        out = relational_service.reject_and_rematch_session(db, payload.session_id, student_id)
        _log_consultation_event(
            db,
            payload.session_id,
            "SESSION_REJECTED",
            {"student_id": student_id},
        )
        old_session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == payload.session_id))
        patient_id = old_session.patient_id if old_session else ""
        if out.get("rematched"):
            _persist_notification(db, "patient", patient_id, "session_update", "Previous student rejected. Reassigning to another available student.")
            _persist_notification(db, "student", out["student_id"], "session_request", f"Patient {patient_id} requested consultation ({out.get('mode', 'chat')}).")
            await notification_manager.push(
                "student",
                out["student_id"],
                {
                    "type": "session_request",
                    "title": "New Consultation Request",
                    "message": f"Patient {patient_id} requested consultation.",
                    "session_id": out["session_id"],
                    "mode": out.get("mode", "chat"),
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            await notification_manager.push(
                "patient",
                patient_id,
                {
                    "type": "session_update",
                    "title": "Request Reassigned",
                    "message": "Previous student rejected. Trying another available student.",
                    "session_id": out["session_id"],
                    "mode": out.get("mode", "chat"),
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
        else:
            _persist_notification(db, "patient", patient_id, "session_update", "No students available right now. Please retry.")
            await notification_manager.push(
                "patient",
                patient_id,
                {
                    "type": "session_update",
                    "title": "No Students Available",
                    "message": "No students available right now. Please retry.",
                    "session_id": payload.session_id,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
        return out
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/session/end")
async def session_end(payload: SessionEndRequest, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    try:
        sess = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == payload.session_id))
        if not sess:
            raise ValueError("Session not found")
        _authorize_session_actor(user, sess, {"patient", "student"})
        result = relational_service.end_consultation_session(db, payload.session_id)
        if sess:
            _log_consultation_event(
                db,
                payload.session_id,
                "SESSION_ENDED",
                {"student_id": sess.student_id, "patient_id": sess.patient_id},
            )
            _persist_notification(db, "patient", sess.patient_id, "session_update", "Your consultation session has ended.")
            _persist_notification(db, "student", sess.student_id, "session_update", "Consultation session closed.")
            await notification_manager.push(
                "patient",
                sess.patient_id,
                {
                    "type": "session_update",
                    "title": "Session Ended",
                    "message": "Your consultation session has ended.",
                    "session_id": payload.session_id,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            await notification_manager.push(
                "student",
                sess.student_id,
                {
                    "type": "session_update",
                    "title": "Session Ended",
                    "message": "Consultation session closed.",
                    "session_id": payload.session_id,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/session/connect-now")
async def session_connect_now(payload: ConnectNowRequest, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    role = str(user.get("role", "")).lower()
    patient_id = str(user.get("sub", ""))
    if role != "patient":
        raise HTTPException(status_code=403, detail="Only patient can start random matching")
    mode = (payload.mode or "chat").strip().lower()
    if mode not in {"chat", "video"}:
        mode = "chat"
    try:
        result = relational_service.request_consultant_video_call(
            db,
            patient_id=patient_id,
            language=payload.language,
            problem=payload.problem,
            mode=mode,
        )
    except ValueError as exc:
        if "No Senior Medical Student Consultant available" in str(exc):
            return {
                "available": False,
                "status": "no_students",
                "message": "No Students Available",
            }
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _log_consultation_event(
        db,
        result["session_id"],
        "SESSION_STARTED",
        {"mode": mode, "patient_id": patient_id, "student_id": result["student_id"], "language": payload.language},
    )
    _persist_notification(db, "student", result["student_id"], "session_request", f"Patient {patient_id} requested {mode} consultation.")
    _persist_notification(db, "patient", patient_id, "session_request", "Consultation request sent. Waiting for student acceptance.")
    await notification_manager.push(
        "student",
        result["student_id"],
        {
            "type": "incoming_request",
            "title": "Incoming Request",
            "message": f"Patient {patient_id} requested consultation.",
            "session_id": result["session_id"],
            "mode": mode,
            "created_at": datetime.utcnow().isoformat(),
        },
    )
    await notification_manager.push(
        "patient",
        patient_id,
        {
            "type": "session_request",
            "title": "Request Sent",
            "message": "Consultation request sent. Waiting for student acceptance.",
            "session_id": result["session_id"],
            "mode": mode,
            "created_at": datetime.utcnow().isoformat(),
        },
    )
    return {"available": True, **result}


@router.post("/patient/request-consultant-video")
async def request_consultant_video(payload: ConsultantVideoRequest, db: Session = Depends(get_db)):
    try:
        result = relational_service.request_consultant_video_call(db, payload.patient_id, payload.language, payload.problem, mode="video")
        _log_consultation_event(
            db,
            result["session_id"],
            "SESSION_STARTED",
            {"mode": "video", "patient_id": payload.patient_id, "student_id": result["student_id"], "language": payload.language},
        )
        _persist_notification(db, "student", result["student_id"], "session_request", f"Patient {payload.patient_id} requested video consultation.")
        _persist_notification(db, "patient", payload.patient_id, "session_request", "Consultation request sent. Waiting for student acceptance.")
        await notification_manager.push(
            "student",
            result["student_id"],
            {
                "type": "session_request",
                "title": "New Video Consultation Request",
                "message": f"Patient {payload.patient_id} requested consultation ({payload.language}).",
                "session_id": result["session_id"],
                "mode": "video",
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        await notification_manager.push(
            "patient",
            payload.patient_id,
            {
                "type": "session_request",
                "title": "Request Sent",
                "message": "Consultation request sent. Waiting for student acceptance.",
                "session_id": result["session_id"],
                "mode": "video",
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/student/request-session")
async def student_request_session(payload: ConsultantVideoRequest, db: Session = Depends(get_db)):
    try:
        result = relational_service.request_consultant_video_call(db, payload.patient_id, payload.language, payload.problem, mode="chat")
        _log_consultation_event(
            db,
            result["session_id"],
            "SESSION_STARTED",
            {"mode": "chat", "patient_id": payload.patient_id, "student_id": result["student_id"], "language": payload.language},
        )
        _persist_notification(db, "student", result["student_id"], "session_request", f"Patient {payload.patient_id} requested chat consultation.")
        _persist_notification(db, "patient", payload.patient_id, "session_request", "Chat request sent. Waiting for student acceptance.")
        await notification_manager.push(
            "student",
            result["student_id"],
            {
                "type": "session_request",
                "title": "New Chat Consultation Request",
                "message": f"Patient {payload.patient_id} requested chat ({payload.language}).",
                "session_id": result["session_id"],
                "mode": "chat",
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        await notification_manager.push(
            "patient",
            payload.patient_id,
            {
                "type": "session_request",
                "title": "Request Sent",
                "message": "Chat request sent. Waiting for student acceptance.",
                "session_id": result["session_id"],
                "mode": "chat",
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/student/pending-requests/{student_id}")
def student_pending_requests(student_id: str, db: Session = Depends(get_db)):
    return {"requests": relational_service.student_pending_requests(db, student_id)}


@router.get("/student/ratings/{student_id}")
def student_ratings_api(student_id: str, db: Session = Depends(get_db)):
    try:
        profile = relational_service.get_student_profile(db, student_id)
        return {
            "student_id": student_id,
            "rating_avg": profile.get("rating_avg", 0.0),
            "rating_count": profile.get("rating_count", 0),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/student/ratings-history/{student_id}")
def student_ratings_history(student_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ConsultationSession)
        .where(ConsultationSession.student_id == student_id)
        .order_by(ConsultationSession.created_at.desc())
        .limit(150)
    ).all()
    if not rows:
        return {"student_id": student_id, "history": []}
    patient_ids = [r.patient_id for r in rows]
    patient_map = {
        p.patient_id: p
        for p in db.scalars(select(Patient).where(Patient.patient_id.in_(patient_ids))).all()
    }
    history = []
    for r in rows:
        p = patient_map.get(r.patient_id)
        history.append(
            {
                "session_id": r.session_id,
                "patient_id": r.patient_id,
                "patient_name": p.full_name if p else "Unknown",
                "patient_uhid": p.uhid if p else "-",
                "mode": r.mode,
                "status": r.status,
                "problem": r.problem,
                "rating": r.rating_score,
                "feedback": r.rating_feedback or "",
                "rated_at": r.rated_at.isoformat() if r.rated_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return {"student_id": student_id, "history": history}


@router.post("/session/rating")
def session_rating(
    payload: SessionRatingRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(current_user),
):
    session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == payload.session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    role = str(user.get("role", "")).lower()
    sub = str(user.get("sub", ""))
    if role != "patient" or sub != session.patient_id:
        raise HTTPException(status_code=403, detail="Only assigned patient can rate this session")
    session.rating_score = int(payload.rating)
    session.rating_feedback = payload.feedback.strip()[:500]
    session.rated_at = datetime.utcnow()

    student = db.scalar(select(Student).where(Student.student_id == session.student_id))
    if student:
        rated_rows = db.query(ConsultationSession).filter(
            ConsultationSession.student_id == student.student_id,
            ConsultationSession.rating_score.is_not(None),
        ).all()
        if rated_rows:
            avg = sum(int(x.rating_score or 0) for x in rated_rows) / len(rated_rows)
            student.rating_avg = round(avg, 2)
            student.rating_count = len(rated_rows)
    db.commit()
    return {
        "status": "rating_saved",
        "session_id": session.session_id,
        "student_id": session.student_id,
        "rating": session.rating_score,
    }


@router.post("/government/login")
def government_login(payload: GovernmentLoginRequest, db: Session = Depends(get_db)):
    username = payload.username.strip()
    row = db.scalar(
        select(GovernmentOfficial).where(
            GovernmentOfficial.username == username,
            GovernmentOfficial.is_active.is_(True),
        )
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid government credentials")
    try:
        valid = security_service.verify_password(payload.password, row.password_hash)
    except Exception:
        valid = payload.password == row.password_hash
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid government credentials")
    token = security_service.create_access_token(row.username, "government")
    return {"status": "login_success", "username": row.username, "token": token}


@router.get("/student/analytics/{student_id}")
def student_analytics(
    student_id: str,
    days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(days=days)
    day_rows = (
        db.query(
            func.date(ConsultationSession.created_at).label("day"),
            func.count(ConsultationSession.id).label("sessions"),
            func.sum(case((ConsultationSession.mode == "video", 1), else_=0)).label("video"),
            func.sum(case((ConsultationSession.mode == "chat", 1), else_=0)).label("chat_mode"),
        )
        .filter(ConsultationSession.student_id == student_id, ConsultationSession.created_at >= since)
        .group_by(func.date(ConsultationSession.created_at))
        .order_by(func.date(ConsultationSession.created_at).asc())
        .all()
    )

    chat_rows = (
        db.query(
            func.date(ChatMessage.created_at).label("day"),
            func.count(ChatMessage.id).label("chat_messages"),
        )
        .join(ConsultationSession, ConsultationSession.session_id == ChatMessage.session_id)
        .filter(ConsultationSession.student_id == student_id, ChatMessage.created_at >= since)
        .group_by(func.date(ChatMessage.created_at))
        .order_by(func.date(ChatMessage.created_at).asc())
        .all()
    )

    start_day = since.date()
    day_list = [(start_day + timedelta(days=i)).isoformat() for i in range(days)]
    sessions_by_day = {str(day): int(cnt or 0) for day, cnt, _v, _c in day_rows if day is not None}
    video_by_day = {str(day): int(v or 0) for day, _cnt, v, _c in day_rows if day is not None}
    chat_mode_by_day = {str(day): int(c or 0) for day, _cnt, _v, c in day_rows if day is not None}
    chat_msg_by_day = {str(day): int(cnt or 0) for day, cnt in chat_rows if day is not None}

    trend = [
        {
            "date": day,
            "sessions": sessions_by_day.get(day, 0),
            "video_sessions": video_by_day.get(day, 0),
            "chat_sessions": chat_mode_by_day.get(day, 0),
            "chat_messages": chat_msg_by_day.get(day, 0),
        }
        for day in day_list
    ]

    base_q = db.query(ConsultationSession).filter(
        ConsultationSession.student_id == student_id,
        ConsultationSession.created_at >= since,
    )
    total_sessions = base_q.count()
    active_sessions = base_q.filter(ConsultationSession.status == "active").count()
    pending_sessions = base_q.filter(ConsultationSession.status == "pending").count()
    ended_sessions = base_q.filter(ConsultationSession.status == "ended").count()
    video_sessions = base_q.filter(ConsultationSession.mode == "video").count()
    chat_sessions = base_q.filter(ConsultationSession.mode == "chat").count()

    total_chat_messages = (
        db.query(func.count(ChatMessage.id))
        .join(ConsultationSession, ConsultationSession.session_id == ChatMessage.session_id)
        .filter(ConsultationSession.student_id == student_id, ChatMessage.created_at >= since)
        .scalar()
        or 0
    )

    return {
        "student_id": student_id,
        "days": days,
        "trend": trend,
        "metrics": {
            "total_sessions": int(total_sessions),
            "active_sessions": int(active_sessions),
            "pending_sessions": int(pending_sessions),
            "ended_sessions": int(ended_sessions),
            "video_sessions": int(video_sessions),
            "chat_sessions": int(chat_sessions),
            "chat_messages": int(total_chat_messages),
        },
        "series_meta": [
            {"key": "sessions", "label": "Total Sessions", "color": "#2563eb"},
            {"key": "video_sessions", "label": "Video Sessions", "color": "#f59e0b"},
            {"key": "chat_sessions", "label": "Chat Sessions", "color": "#16a34a"},
            {"key": "chat_messages", "label": "Chat Messages", "color": "#7c3aed"},
        ],
    }


@router.get("/metrics")
def consultation_metrics(
    student_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(days=days)

    session_q = db.query(ConsultationSession).filter(ConsultationSession.created_at >= since)
    if student_id:
        session_q = session_q.filter(ConsultationSession.student_id == student_id)
    if session_id:
        session_q = session_q.filter(ConsultationSession.session_id == session_id)
    sessions = session_q.order_by(ConsultationSession.created_at.asc()).all()

    if not sessions:
        return {
            "metrics": {
                "session_duration": [],
                "user_activity": [],
                "connection_quality": [],
                "health_metrics": [],
            },
            "summary": {"total_sessions": 0},
            "no_data": True,
        }

    def _duration_minutes(sess: ConsultationSession) -> float:
        start = sess.accepted_at or sess.created_at
        end = sess.ended_at or datetime.utcnow()
        return max(0.0, round((end - start).total_seconds() / 60.0, 2))

    # Session duration by created date
    duration_rows: list[dict] = []
    for sess in sessions:
        duration_rows.append(
            {
                "date": sess.created_at.date().isoformat(),
                "value": _duration_minutes(sess),
                "session_id": sess.session_id,
            }
        )

    # User activity: chat messages/day for filtered sessions
    session_ids = [s.session_id for s in sessions]
    chat_rows = (
        db.query(func.date(ChatMessage.created_at), func.count(ChatMessage.id))
        .filter(ChatMessage.session_id.in_(session_ids), ChatMessage.created_at >= since)
        .group_by(func.date(ChatMessage.created_at))
        .order_by(func.date(ChatMessage.created_at).asc())
        .all()
    )
    user_activity = [{"date": str(day), "value": int(cnt or 0)} for day, cnt in chat_rows if day is not None]

    # Connection quality from consultation logs (if present), otherwise derive proxy from completed sessions.
    quality_logs = (
        db.query(ConsultationLog.timestamp, ConsultationLog.metadata_json)
        .filter(ConsultationLog.session_id.in_(session_ids), ConsultationLog.event_type.in_(["QUALITY_SNAPSHOT", "VIDEO_JOIN"]))
        .order_by(ConsultationLog.timestamp.asc())
        .all()
    )
    connection_quality: list[dict] = []
    for ts, meta in quality_logs:
        value = None
        try:
            raw = json.loads(meta or "{}")
            q = raw.get("quality_score")
            if q is not None:
                value = float(q)
        except Exception:
            value = None
        if value is None:
            value = 0.75
        connection_quality.append({"date": ts.date().isoformat(), "value": round(max(0.0, min(1.0, value)), 3)})
    if not connection_quality:
        connection_quality = [{"date": s.created_at.date().isoformat(), "value": 0.8} for s in sessions]

    # Health metrics proxy from diagnosis counts by date for session participants.
    patient_ids = [s.patient_id for s in sessions]
    patient_uhids = db.scalars(select(Patient.uhid).where(Patient.patient_id.in_(patient_ids))).all()
    health_rows = (
        db.query(func.date(Diagnosis.created_at), func.count(Diagnosis.id))
        .filter(Diagnosis.uhid.in_(patient_uhids), Diagnosis.created_at >= since)
        .group_by(func.date(Diagnosis.created_at))
        .order_by(func.date(Diagnosis.created_at).asc())
        .all()
    )
    health_metrics = [{"date": str(day), "value": int(cnt or 0)} for day, cnt in health_rows if day is not None]

    # Sort chronologically and drop undefined
    duration_rows = sorted([r for r in duration_rows if r.get("date")], key=lambda x: x["date"])
    user_activity = sorted([r for r in user_activity if r.get("date")], key=lambda x: x["date"])
    connection_quality = sorted([r for r in connection_quality if r.get("date")], key=lambda x: x["date"])
    health_metrics = sorted([r for r in health_metrics if r.get("date")], key=lambda x: x["date"])

    return {
        "metrics": {
            "session_duration": duration_rows,
            "user_activity": user_activity,
            "connection_quality": connection_quality,
            "health_metrics": health_metrics,
        },
        "summary": {
            "total_sessions": len(sessions),
            "active_sessions": sum(1 for s in sessions if s.status == "active"),
            "ended_sessions": sum(1 for s in sessions if s.status == "ended"),
        },
        "no_data": False,
    }


@router.post("/chat/save")
def save_chat(payload: ChatSaveRequest, db: Session = Depends(get_db)):
    try:
        out = relational_service.save_chat_message(
            db,
            payload.session_id,
            payload.sender_role,
            payload.sender_id,
            payload.message_text,
        )
        _log_consultation_event(
            db,
            payload.session_id,
            "CHAT_MESSAGE",
            {"sender_role": payload.sender_role, "sender_id": payload.sender_id},
        )
        return out
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/chat/recall/{session_id}")
def chat_recall(session_id: str, db: Session = Depends(get_db)):
    return relational_service.session_recall(db, session_id)


@router.post("/session/video-join")
def session_video_join(payload: VideoJoinRequest, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    try:
        session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == payload.session_id))
        if not session:
            raise ValueError("Session not found")
        _authorize_session_actor(user, session, {"patient", "student"})
        out = relational_service.video_join_info(db, payload.session_id, payload.requester_id)
        _log_consultation_event(
            db,
            payload.session_id,
            "VIDEO_JOIN",
            {"requester_id": payload.requester_id, "mode": out.get("mode", "video")},
        )
        return out
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/patient/health-in-my-area")
def health_in_my_area(
    patient_id: str = Query(...),
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
):
    try:
        return relational_service.health_in_my_area(db, patient_id, days)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/gov/monitoring")
def gov_monitoring(
    disease_category: int | None = Query(default=None, ge=1, le=12),
    days: int = Query(default=30, ge=7, le=365),
    gender: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: dict = Depends(government_user),
):
    return relational_service.gov_monitoring_dashboard(
        db=db,
        disease_category=disease_category,
        days=days,
        gender=gender,
        state=state,
    )


def _norm_label(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _resolve_state_name(db: Session, state_name: str | None) -> str | None:
    if not state_name:
        return None
    target = _norm_label(state_name)
    if not target:
        return None
    states = db.scalars(select(Patient.state).distinct()).all()
    for candidate in states:
        if _norm_label(candidate) == target:
            return candidate
    return state_name


def _resolve_district_name(db: Session, district_name: str | None, resolved_state: str | None = None) -> str | None:
    if not district_name:
        return None
    target = _norm_label(district_name)
    if not target:
        return None
    q = select(Patient.district).distinct()
    if resolved_state:
        q = q.where(Patient.state == resolved_state)
    districts = db.scalars(q).all()
    for candidate in districts:
        if _norm_label(candidate) == target:
            return candidate
    return district_name


@router.get("/government/states")
def government_states(
    days: int = Query(default=30, ge=7, le=365),
    disease_category: int | None = Query(default=None, ge=1, le=12),
    gender: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: dict = Depends(government_user),
):
    since = datetime.utcnow() - timedelta(days=days)
    if disease_category:
        rows = (
            db.query(Patient.state, func.count(func.distinct(Diagnosis.uhid)))
            .join(Diagnosis, Diagnosis.uhid == Patient.uhid)
            .filter(Diagnosis.created_at >= since, Diagnosis.disease_category == disease_category)
            .group_by(Patient.state)
            .all()
        )
    else:
        rows = (
            db.query(Patient.state, func.count(func.distinct(Visit.uhid)))
            .join(Visit, Visit.uhid == Patient.uhid)
            .filter(Visit.visit_date >= since)
            .group_by(Patient.state)
            .all()
        )
        if not rows:
            rows = (
                db.query(Patient.state, func.count(Patient.id))
                .filter(Patient.created_at >= since)
                .group_by(Patient.state)
                .all()
            )

    values = [int(c or 0) for _, c in rows]
    min_count = min(values) if values else 0
    max_count = max(values) if values else 1
    states = []
    for st, cnt in rows:
        patient_count = int(cnt or 0)
        if max_count <= min_count:
            risk_level = "medium"
        else:
            ratio = (patient_count - min_count) / max(1, (max_count - min_count))
            risk_level = "low" if ratio < 0.34 else ("medium" if ratio < 0.67 else "high")
        states.append(
            {
                "state": st,
                "patient_count": patient_count,
                "risk_score": patient_count,
                "risk_level": risk_level,
            }
        )

    total_patients = db.query(func.count(Patient.id)).scalar() or 0
    return {
        "states": states,
        "disease_categories": [{"category": i, "color": c} for i, c in enumerate(
            ["#2563eb", "#f59e0b", "#16a34a", "#7c3aed", "#db2777", "#ef4444", "#0891b2", "#84cc16", "#f97316", "#64748b", "#0d9488", "#ca8a04"],
            start=1,
        )],
        "summary_stats": {
            "total_patients": int(total_patients),
            "male_percentage": 0,
            "female_percentage": 0,
            "age_groups": {"children": 0, "adults": int(total_patients), "elderly": 0},
        },
        "filters_applied": {"disease_category": disease_category, "days": days, "gender": gender},
    }


@router.get("/government/state/{state_name}")
def government_state(
    state_name: str,
    days: int = Query(default=30, ge=7, le=365),
    disease_category: int | None = Query(default=None, ge=1, le=12),
    gender: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: dict = Depends(government_user),
):
    resolved_state = _resolve_state_name(db, state_name.replace("_", " "))
    since = datetime.utcnow() - timedelta(days=days)

    district_rows = (
        db.query(Patient.district, func.count(Patient.id))
        .filter(Patient.state == resolved_state)
        .group_by(Patient.district)
        .all()
    )
    district_count_map = {d: int(c or 0) for d, c in district_rows}

    trend_group_rows = (
        db.query(Patient.district, Diagnosis.disease_category, func.count(func.distinct(Diagnosis.uhid)))
        .join(Diagnosis, Diagnosis.uhid == Patient.uhid)
        .filter(Patient.state == resolved_state, Diagnosis.created_at >= since)
        .group_by(Patient.district, Diagnosis.disease_category)
        .all()
    )
    if disease_category:
        trend_group_rows = [r for r in trend_group_rows if int(r[1] or 0) == disease_category]

    trend_rows = [
        {
            "state": resolved_state,
            "district": district,
            "disease_category": int(cat or 0),
            "percentage_change": int(count or 0),
        }
        for district, cat, count in trend_group_rows
    ]

    district_names = sorted({d for d in district_count_map.keys() if d})
    district_stats: list[dict] = []
    for district in district_names:
        d_rows = [r for r in trend_rows if r["district"] == district]
        patient_count = district_count_map.get(district, 0)
        district_stats.append(
            {
                "district": district,
                "patient_count": patient_count,
                "risk_score": patient_count,
                "trend_rows": d_rows,
            }
        )

    patients = (
        db.query(Patient.uhid, Patient.full_name, Patient.district, Patient.state, Patient.dob)
        .filter(Patient.state == resolved_state)
        .limit(200)
        .all()
    )
    visits_query = (
        db.query(Visit.visit_id, Visit.uhid, Visit.doctor_id, Visit.visit_date, Visit.notes)
        .join(Patient, Patient.uhid == Visit.uhid)
        .filter(Patient.state == resolved_state, Visit.visit_date >= since)
        .order_by(Visit.visit_date.desc())
    )
    recent_visits = visits_query.limit(50).all()

    active = (
        db.query(func.count(func.distinct(Visit.uhid)))
        .join(Patient, Patient.uhid == Visit.uhid)
        .filter(Patient.state == resolved_state, Visit.visit_date >= since)
        .scalar()
        or 0
    )
    total_patients = db.query(func.count(Patient.id)).filter(Patient.state == resolved_state).scalar() or 0
    appointments = db.query(func.count(Visit.id)).join(Patient, Patient.uhid == Visit.uhid).filter(
        Patient.state == resolved_state, Visit.visit_date >= since
    ).scalar() or 0
    today = datetime.utcnow().date()

    return {
        "state": resolved_state,
        "districts": district_stats,
        "metrics": {
            "active_patients": int(active),
            "discharged": max(0, int(total_patients) - int(active)),
            "appointments": int(appointments),
            "total_patients": int(total_patients),
        },
        "patients": [
            {
                "uhid": p.uhid,
                "name": p.full_name,
                "district": p.district,
                "state": p.state,
                "age": (today.year - p.dob.year - ((today.month, today.day) < (p.dob.month, p.dob.day))) if p.dob else None,
            }
            for p in patients
        ],
        "visits": [
            {
                "visit_id": v.visit_id,
                "uhid": v.uhid,
                "doctor_id": v.doctor_id,
                "visit_date": v.visit_date.isoformat() if v.visit_date else None,
                "notes": v.notes,
            }
            for v in recent_visits
        ],
        "summary_stats": {
            "total_patients": int(total_patients),
            "male_percentage": 0,
            "female_percentage": 0,
            "age_groups": {"children": 0, "adults": int(total_patients), "elderly": 0},
        },
    }


@router.get("/government/district/{district_name}")
def government_district(
    district_name: str,
    state: str | None = Query(default=None),
    days: int = Query(default=30, ge=7, le=365),
    disease_category: int | None = Query(default=None, ge=1, le=12),
    gender: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: dict = Depends(government_user),
):
    resolved_state = _resolve_state_name(db, state)
    resolved_district = _resolve_district_name(db, district_name.replace("_", " "), resolved_state)
    since = datetime.utcnow() - timedelta(days=days)

    patient_query = db.query(Patient.uhid, Patient.full_name, Patient.state, Patient.district, Patient.dob).filter(Patient.district == resolved_district)
    if resolved_state:
        patient_query = patient_query.filter(Patient.state == resolved_state)
    patients = patient_query.limit(200).all()

    visit_query = (
        db.query(Visit.visit_id, Visit.uhid, Visit.doctor_id, Visit.visit_date, Visit.notes)
        .join(Patient, Patient.uhid == Visit.uhid)
        .filter(Patient.district == resolved_district, Visit.visit_date >= since)
    )
    if resolved_state:
        visit_query = visit_query.filter(Patient.state == resolved_state)
    visits = visit_query.order_by(Visit.visit_date.desc()).limit(200).all()

    diagnosis_query = (
        db.query(
            func.date(Diagnosis.created_at).label("day"),
            Diagnosis.disease_category,
            func.count(func.distinct(Diagnosis.uhid)).label("patient_count"),
        )
        .join(Patient, Patient.uhid == Diagnosis.uhid)
        .filter(Patient.district == resolved_district, Diagnosis.created_at >= since)
    )
    if resolved_state:
        diagnosis_query = diagnosis_query.filter(Patient.state == resolved_state)
    if disease_category:
        diagnosis_query = diagnosis_query.filter(Diagnosis.disease_category == disease_category)
    diagnosis_rows = (
        diagnosis_query
        .group_by(func.date(Diagnosis.created_at), Diagnosis.disease_category)
        .order_by(func.date(Diagnosis.created_at).asc(), Diagnosis.disease_category.asc())
        .all()
    )

    # Fallback to real patient registration counts (still DB-driven, no synthetic data)
    # when there are no diagnosis rows for the selected district/time window.
    if not diagnosis_rows:
        if disease_category is None or disease_category == 12:
            patient_trend_query = (
                db.query(
                    func.date(Patient.created_at).label("day"),
                    func.count(Patient.id).label("patient_count"),
                )
                .filter(Patient.district == resolved_district, Patient.created_at >= since)
            )
            if resolved_state:
                patient_trend_query = patient_trend_query.filter(Patient.state == resolved_state)
            registration_rows = (
                patient_trend_query
                .group_by(func.date(Patient.created_at))
                .order_by(func.date(Patient.created_at).asc())
                .all()
            )
            diagnosis_rows = [(day, 12, count) for day, count in registration_rows if day is not None]

    disease_time_series = [
        {"date": str(day), "disease_category": int(cat), "patient_count": int(count)}
        for day, cat, count in diagnosis_rows
        if day is not None and cat is not None
    ]
    trend_rows = [
        {
            "state": resolved_state,
            "district": resolved_district,
            "disease_category": row["disease_category"],
            "percentage_change": row["patient_count"],
        }
        for row in disease_time_series
    ]

    disease_counter: dict[int, int] = {}
    for _day, cat, count in diagnosis_rows:
        category = int(cat or 0)
        if category <= 0:
            continue
        disease_counter[category] = disease_counter.get(category, 0) + int(count or 0)

    patient_trend_map: dict[str, int] = {}
    for item in disease_time_series:
        patient_trend_map[item["date"]] = patient_trend_map.get(item["date"], 0) + item["patient_count"]
    patient_trend = [
        {"date": day, "count": patient_trend_map[day]}
        for day in sorted(patient_trend_map.keys())
    ]
    disease_stats = [
        {"disease_category": cat, "count": count}
        for cat, count in sorted(disease_counter.items(), key=lambda x: x[0])
        if cat > 0
    ]

    latest_count = patient_trend[-1]["count"] if patient_trend else 0
    prev_count = patient_trend[-2]["count"] if len(patient_trend) > 1 else 0
    active = int(latest_count)
    appointments = len(visits)
    total_patients = db.query(func.count(Patient.id)).filter(Patient.district == resolved_district).scalar() or 0
    if resolved_state:
        total_patients = db.query(func.count(Patient.id)).filter(Patient.district == resolved_district, Patient.state == resolved_state).scalar() or 0
    today = datetime.utcnow().date()

    return {
        "district": resolved_district,
        "state": resolved_state,
        "trend": trend_rows,
        "disease_time_series": disease_time_series,
        "active_patients": int(active),
        "new_patients": int(latest_count - prev_count),
        "disease_stats": disease_stats,
        "patient_trend": patient_trend,
        "metrics": {
            "active_patients": int(active),
            "discharged": max(0, int(total_patients) - int(active)),
            "appointments": int(appointments),
            "total_patients": int(total_patients),
        },
        "patients": [
            {
                "uhid": p.uhid,
                "name": p.full_name,
                "district": p.district,
                "state": p.state,
                "age": (today.year - p.dob.year - ((today.month, today.day) < (p.dob.month, p.dob.day))) if p.dob else None,
            }
            for p in patients
        ],
        "visits": [
            {
                "visit_id": v.visit_id,
                "uhid": v.uhid,
                "doctor_id": v.doctor_id,
                "visit_date": v.visit_date.isoformat() if v.visit_date else None,
                "notes": v.notes,
            }
            for v in visits
        ],
    }


@router.post("/test-notification")
async def test_notification(payload: TestNotificationRequest, db: Session = Depends(get_db)):
    body = {
        "type": payload.type,
        "title": payload.title,
        "message": payload.message,
        "created_at": datetime.utcnow().isoformat(),
    }
    _persist_notification(db, payload.role, payload.user_id, payload.type, payload.message)
    await notification_manager.push(payload.role, payload.user_id, body)
    return {"sent": True, "target": {"role": payload.role, "user_id": payload.user_id}, "payload": body}


@router.get("/notifications/{role}/{user_id}")
def get_notifications(role: str, user_id: str, unread_only: bool = Query(default=False), db: Session = Depends(get_db), user: dict = Depends(current_user)):
    role_norm = role.strip().lower()
    if str(user.get("role", "")).lower() != role_norm or str(user.get("sub", "")) != user_id:
        raise HTTPException(status_code=403, detail="Not allowed")
    q = db.query(Notification).filter(Notification.role == role_norm, Notification.user_id == user_id)
    if unread_only:
        q = q.filter(Notification.read_status.is_(False))
    rows = q.order_by(Notification.created_at.desc()).limit(100).all()
    return {
        "items": [
            {
                "id": r.id,
                "role": r.role,
                "user_id": r.user_id,
                "type": r.type,
                "message": r.message,
                "read_status": r.read_status,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/notifications/{role}/{user_id}/read")
def mark_notification_read(role: str, user_id: str, payload: MarkNotificationReadRequest, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    role_norm = role.strip().lower()
    if str(user.get("role", "")).lower() != role_norm or str(user.get("sub", "")) != user_id:
        raise HTTPException(status_code=403, detail="Not allowed")
    row = db.scalar(select(Notification).where(Notification.id == payload.notification_id, Notification.role == role_norm, Notification.user_id == user_id))
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    row.read_status = True
    db.commit()
    return {"ok": True}


@router.post("/session/log-event")
def session_log_event(payload: SessionLogEventRequest, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == payload.session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _authorize_session_actor(user, session, {"patient", "student"})
    _log_consultation_event(
        db,
        payload.session_id,
        payload.event_type.strip()[:64] or "EVENT",
        payload.metadata or {},
    )
    return {"ok": True}


@router.get("/webrtc/config")
def webrtc_config():
    stun_url = os.getenv("WEBRTC_STUN_URL", "stun:stun.l.google.com:19302").strip()
    turn_url = os.getenv("WEBRTC_TURN_URL", "").strip()
    turn_user = os.getenv("WEBRTC_TURN_USERNAME", "").strip()
    turn_cred = os.getenv("WEBRTC_TURN_CREDENTIAL", "").strip()
    ice_servers: list[dict] = [{"urls": stun_url}]
    if turn_url and turn_user and turn_cred:
        ice_servers.append({"urls": turn_url, "username": turn_user, "credential": turn_cred})
    return {"iceServers": ice_servers}

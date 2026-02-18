from __future__ import annotations

import hashlib
import os
import random
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, select, text

from app.db import SessionLocal
from app.models.db_models import (
    ChatMessage,
    ConsultationLog,
    ConsultationSession,
    Diagnosis,
    Doctor,
    DoctorLicenseRegistry,
    DoctorManualReport,
    DoctorProfile,
    GovernmentOfficial,
    Notification,
    Patient,
    Recommendation,
    Student,
    StudentRegistry,
    Visit,
)
from app.services.relational_service import relational_service
from app.services.security_service import security_service


EMAIL_BASES = [
    "koushik.chinu.2007@gmail.com",
    "chinu.koushik.2007@gmail.com",
    "venkatakoushik19144@gmail.com",
    "sharadbabhare9@gmail.com",
    "dshekhar81121@gmail.com",
]

ADDITIONAL_DOCTOR_PATIENT_EMAILS = [
    "koushikklu.4@gmail.com",
    "venkatakoushikpotta091@gmail.com",
    "k928309473@gmail.com",
    "chinnuidontknow@gmail.com",
]

DOCTOR_PATIENT_EMAILS = EMAIL_BASES + ADDITIONAL_DOCTOR_PATIENT_EMAILS

PHONE_BASES = [
    "7815873699",
    "8121521944",
    "9030230441",
]


def _phone_for(idx: int) -> str:
    base = PHONE_BASES[(idx - 1) % len(PHONE_BASES)]
    return f"{base[:7]}{idx:03d}"


def _doctor_license(idx: int) -> str:
    return f"GOV-AYUSH-{2000 + idx}"


def _refresh_postgres_dump_file() -> None:
    db_url = (os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        return
    engine = create_engine(db_url, future=True, pool_pre_ping=True)
    insp = inspect(engine)
    tables = sorted(insp.get_table_names())
    lines: list[str] = []
    lines.append(f"Database: {db_url}")
    lines.append("Tables:")
    with engine.connect() as conn:
        for t in tables:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
            lines.append(f"- {t}: {count} rows")
        lines.append("")
        lines.append("Sample rows (first 3):")
        for t in tables:
            lines.append("")
            lines.append(f"[{t}]")
            if t in {"patients", "doctors", "students"}:
                rows = conn.execute(text(f'SELECT * FROM "{t}" ORDER BY id DESC LIMIT 3')).fetchall()
            else:
                rows = conn.execute(text(f'SELECT * FROM "{t}" LIMIT 3')).fetchall()
            for r in rows:
                lines.append(str(tuple(r)))

        lines.append("")
        lines.append("")
        lines.append("=== FULL ROLE DETAILS ===")
        lines.append("")
        lines.append("[patients_full]")
        for r in conn.execute(
            text(
                """
                SELECT patient_id, uhid, full_name, email, phone, language, state, district, blood_group, created_at
                FROM patients
                ORDER BY created_at DESC
                """
            )
        ).fetchall():
            lines.append(str(tuple(r)))

        lines.append("")
        lines.append("[doctors_full]")
        for r in conn.execute(
            text(
                """
                SELECT d.doctor_id, d.full_name, d.government_license_id, d.email, p.phone, p.address, d.created_at
                FROM doctors d
                LEFT JOIN doctor_profiles p ON p.doctor_id = d.doctor_id
                ORDER BY d.created_at DESC
                """
            )
        ).fetchall():
            lines.append(str(tuple(r)))

        lines.append("")
        lines.append("[students_full]")
        for r in conn.execute(
            text(
                """
                SELECT student_id, full_name, college_id, institute_name, official_email, phone, language_preference, rating_avg, rating_count, created_at
                FROM students
                ORDER BY created_at DESC
                """
            )
        ).fetchall():
            lines.append(str(tuple(r)))
    Path("postgres_dump.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cleanup_old_seeded_rows(db) -> dict:
    removed = {
        "sessions": 0,
        "logs": 0,
        "chat": 0,
        "notifications": 0,
        "visits": 0,
        "diagnosis": 0,
        "recommendations": 0,
        "manual_reports": 0,
        "doctor_profiles": 0,
        "doctors": 0,
        "students": 0,
        "patients": 0,
        "student_registry": 0,
    }

    old_doctors = db.scalars(select(Doctor).where(Doctor.full_name.like("doctor%"))).all()
    old_students = db.scalars(select(Student).where(Student.full_name.like("student%"))).all()
    old_patients = db.scalars(select(Patient).where(Patient.full_name.like("patient%"))).all()
    doctor_ids = [d.doctor_id for d in old_doctors]
    student_ids = [s.student_id for s in old_students]
    patient_ids = [p.patient_id for p in old_patients]
    patient_uhids = [p.uhid for p in old_patients]
    session_ids = db.scalars(
        select(ConsultationSession.session_id).where(
            ConsultationSession.patient_id.in_(patient_ids) if patient_ids else False
        )
    ).all()
    if student_ids:
        more_sessions = db.scalars(
            select(ConsultationSession.session_id).where(ConsultationSession.student_id.in_(student_ids))
        ).all()
        session_ids = list(set(session_ids + more_sessions))

    if session_ids:
        for row in db.scalars(select(ConsultationLog).where(ConsultationLog.session_id.in_(session_ids))).all():
            db.delete(row)
            removed["logs"] += 1
        for row in db.scalars(select(ChatMessage).where(ChatMessage.session_id.in_(session_ids))).all():
            db.delete(row)
            removed["chat"] += 1
        for row in db.scalars(select(ConsultationSession).where(ConsultationSession.session_id.in_(session_ids))).all():
            db.delete(row)
            removed["sessions"] += 1

    if patient_ids:
        for row in db.scalars(
            select(Notification).where(Notification.role == "patient", Notification.user_id.in_(patient_ids))
        ).all():
            db.delete(row)
            removed["notifications"] += 1
    if student_ids:
        for row in db.scalars(
            select(Notification).where(Notification.role == "student", Notification.user_id.in_(student_ids))
        ).all():
            db.delete(row)
            removed["notifications"] += 1

    if patient_uhids:
        visit_ids = db.scalars(select(Visit.visit_id).where(Visit.uhid.in_(patient_uhids))).all()
        if visit_ids:
            for row in db.scalars(select(Diagnosis).where(Diagnosis.visit_id.in_(visit_ids))).all():
                db.delete(row)
                removed["diagnosis"] += 1
            for row in db.scalars(select(Recommendation).where(Recommendation.visit_id.in_(visit_ids))).all():
                db.delete(row)
                removed["recommendations"] += 1
        for row in db.scalars(select(DoctorManualReport).where(DoctorManualReport.uhid.in_(patient_uhids))).all():
            db.delete(row)
            removed["manual_reports"] += 1
        for row in db.scalars(select(Visit).where(Visit.uhid.in_(patient_uhids))).all():
            db.delete(row)
            removed["visits"] += 1

    for row in db.scalars(select(DoctorProfile).where(DoctorProfile.doctor_id.in_(doctor_ids) if doctor_ids else False)).all():
        db.delete(row)
        removed["doctor_profiles"] += 1
    for row in db.scalars(select(Doctor).where(Doctor.doctor_id.in_(doctor_ids) if doctor_ids else False)).all():
        db.delete(row)
        removed["doctors"] += 1
    for row in db.scalars(select(StudentRegistry).where(StudentRegistry.college_id.like("COLL-8%"))).all():
        db.delete(row)
        removed["student_registry"] += 1
    for row in db.scalars(select(Student).where(Student.student_id.in_(student_ids) if student_ids else False)).all():
        db.delete(row)
        removed["students"] += 1
    for row in db.scalars(select(Patient).where(Patient.patient_id.in_(patient_ids) if patient_ids else False)).all():
        db.delete(row)
        removed["patients"] += 1
    return removed


def run() -> None:
    load_dotenv(".env", override=False)
    fixed_password = "123456"
    os.environ["DEMO_FIXED_PASSWORD"] = fixed_password

    districts = [
        ("Maharashtra", "Nagpur"),
        ("Maharashtra", "Pune"),
        ("Telangana", "Hyderabad"),
        ("Rajasthan", "Jaipur"),
        ("Karnataka", "Bengaluru"),
        ("Tamil Nadu", "Chennai"),
    ]
    blood_groups = ["O+", "A+", "B+", "AB+", "O-", "A-"]
    languages = ["English", "Hindi", "Telugu", "Marathi", "Tamil", "Kannada"]

    with SessionLocal() as db:
        cleanup_info = _cleanup_old_seeded_rows(db)
        db.flush()
        seeded_doctors = 0
        seeded_students = 0
        seeded_patients = 0
        seeded_registry = 0
        seeded_sessions = 0

        for i in range(1, len(DOCTOR_PATIENT_EMAILS) + 1):
            license_id = _doctor_license(i)
            if not db.scalar(select(DoctorLicenseRegistry).where(DoctorLicenseRegistry.government_license_id == license_id)):
                db.add(
                    DoctorLicenseRegistry(
                        government_license_id=license_id,
                        doctor_name=f"doctor{i}",
                        is_active=True,
                    )
                )
                seeded_registry += 1

        for i, email in enumerate(DOCTOR_PATIENT_EMAILS, start=1):
            phone = _phone_for(i)
            existing = db.scalar(select(Doctor).where(Doctor.email == email))
            if existing:
                existing.full_name = f"doctor{i}"
                existing.password_hash = security_service.hash_password(fixed_password)
                existing.government_license_id = _doctor_license(i)
                profile = db.scalar(select(DoctorProfile).where(DoctorProfile.doctor_id == existing.doctor_id))
                if not profile:
                    profile = DoctorProfile(doctor_id=existing.doctor_id, phone=phone, address=f"Block {i}, AYUSH Hospital")
                    db.add(profile)
                else:
                    profile.phone = phone
                    profile.address = f"Block {i}, AYUSH Hospital"
                continue

            row = Doctor(
                doctor_id=f"DOC-{uuid.uuid4().hex[:8]}",
                full_name=f"doctor{i}",
                government_license_id=_doctor_license(i),
                email=email,
                password_hash=security_service.hash_password(fixed_password),
            )
            db.add(row)
            db.flush()
            db.add(DoctorProfile(doctor_id=row.doctor_id, phone=phone, address=f"Block {i}, AYUSH Hospital"))
            seeded_doctors += 1

        for i, email in enumerate(EMAIL_BASES, start=1):
            college_id = f"COLL-{8000 + i}"
            institute = "AYUSH Medical College"
            phone = _phone_for(i)
            if not db.scalar(select(StudentRegistry).where(StudentRegistry.college_id == college_id)):
                db.add(
                    StudentRegistry(
                        college_id=college_id,
                        institute_name=institute,
                        official_email=email,
                        is_active=True,
                    )
                )

            existing = db.scalar(select(Student).where(Student.official_email == email))
            langs = ",".join(random.sample(languages, k=3))
            if existing:
                existing.full_name = f"student{i}"
                existing.college_id = college_id
                existing.institute_name = institute
                existing.phone = phone
                existing.language_preference = langs
                existing.password_hash = security_service.hash_password(fixed_password)
                continue

            db.add(
                Student(
                    student_id=f"STU-{uuid.uuid4().hex[:8]}",
                    full_name=f"student{i}",
                    college_id=college_id,
                    institute_name=institute,
                    official_email=email,
                    phone=phone,
                    password_hash=security_service.hash_password(fixed_password),
                    language_preference=langs,
                    rating_avg=0.0,
                    rating_count=0,
                )
                )
            seeded_students += 1

        for i, email in enumerate(DOCTOR_PATIENT_EMAILS, start=1):
            phone = _phone_for(i)
            existing = db.scalar(select(Patient).where(Patient.email == email))
            if existing:
                existing.full_name = f"patient{i}"
                existing.phone = phone
                existing.password_hash = security_service.hash_password(fixed_password)
                continue

            state, district = districts[(i - 1) % len(districts)]
            aadhaar = f"99998888{i:04d}"
            aadhaar_hash = hashlib.sha256(aadhaar.encode("utf-8")).hexdigest()
            qr_token, qr_path = relational_service.generate_qr(f"UHID2026P{i:03d}")
            db.add(
                Patient(
                    patient_id=f"PAT-{uuid.uuid4().hex[:8]}",
                    uhid=f"UHID2026P{i:03d}",
                    full_name=f"patient{i}",
                    aadhaar_hash=aadhaar_hash,
                    dob=date(1998 + (i % 6), (i % 12) + 1, (i % 27) + 1),
                    email=email,
                    phone=phone,
                    language=languages[(i - 1) % len(languages)],
                    address=f"{district}, {state}",
                    state=state,
                    district=district,
                    blood_group=blood_groups[(i - 1) % len(blood_groups)],
                    password_hash=security_service.hash_password(fixed_password),
                    qr_token=qr_token,
                    qr_path=qr_path,
                    live_location_enabled=False,
                    latitude=0.0,
                    longitude=0.0,
                )
            )
            seeded_patients += 1

        db.flush()
        patients = db.scalars(select(Patient).where(Patient.full_name.like("patient%")).order_by(Patient.full_name)).all()
        students = db.scalars(select(Student).where(Student.full_name.like("student%")).order_by(Student.full_name)).all()
        if patients and students:
            for i, patient in enumerate(patients):
                student = students[i % len(students)]
                started = datetime.utcnow() - timedelta(days=(len(patients) - i))
                status = "ended" if i < max(1, len(patients) - 1) else "active"
                accepted_at = started + timedelta(minutes=1)
                ended_at = (accepted_at + timedelta(minutes=12)) if status == "ended" else None
                session = ConsultationSession(
                    session_id=f"SES-VERIFY-{i+1:02d}",
                    patient_id=patient.patient_id,
                    student_id=student.student_id,
                    language=patient.language or "English",
                    mode="chat" if i % 2 == 0 else "video",
                    status=status,
                    problem=f"Verification consult for {patient.full_name}",
                    created_at=started,
                    expires_at=started + timedelta(minutes=10),
                    accepted_at=accepted_at,
                    ended_at=ended_at,
                    rating_score=5 if status == "ended" else None,
                    rating_feedback="good session" if status == "ended" else None,
                    rated_at=(ended_at + timedelta(minutes=1)) if status == "ended" and ended_at else None,
                )
                db.add(session)
                db.add(
                    ConsultationLog(
                        session_id=session.session_id,
                        event_type="SESSION_STARTED",
                        metadata_json='{"source":"seed_postgres_entities"}',
                    )
                )
                seeded_sessions += 1

        gov_username = (os.getenv("GOV_USERNAME") or "gov_admin").strip()
        gov_password = (os.getenv("GOV_PASSWORD") or "Gov@123456").strip()
        gov = db.scalar(select(GovernmentOfficial).where(GovernmentOfficial.username == gov_username))
        if not gov:
            db.add(
                GovernmentOfficial(
                    username=gov_username,
                    full_name="Government Official",
                    designation="Health Monitoring Officer",
                    password_hash=security_service.hash_password(gov_password),
                    is_active=True,
                )
            )

        db.commit()

        total_doctors = len(db.scalars(select(Doctor)).all())
        total_students = len(db.scalars(select(Student)).all())
        total_patients = len(db.scalars(select(Patient)).all())
        print(
            {
                "cleanup": cleanup_info,
                "seeded_doctors_new": seeded_doctors,
                "seeded_students_new": seeded_students,
                "seeded_patients_new": seeded_patients,
                "seeded_doctor_registry_new": seeded_registry,
                "seeded_sessions_new": seeded_sessions,
                "total_doctors": total_doctors,
                "total_students": total_students,
                "total_patients": total_patients,
            }
        )
    _refresh_postgres_dump_file()
    print({"postgres_dump_refreshed": True, "dump_file": "postgres_dump.txt"})


if __name__ == "__main__":
    run()

from __future__ import annotations

import hashlib
import os
import random
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import qrcode
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.ai_service import ai_service
from app.services.otp_service import otp_service
from app.services.security_service import security_service
from app.models.db_models import (
    Alert,
    ChatMessage,
    ConsultationSession,
    Diagnosis,
    Doctor,
    DoctorLicenseRegistry,
    DoctorProfile,
    GovernmentOfficial,
    Patient,
    Recommendation,
    Student,
    StudentRegistry,
    StudentVerification,
    Visit,
)
from app.services.storage.mock_storage import mock_storage


class RelationalService:
    def __init__(self) -> None:
        self.qr_dir = Path("app/generated_qr")
        self.qr_dir.mkdir(parents=True, exist_ok=True)
        self.otp_store: dict[str, dict[str, str]] = defaultdict(dict)

    def _hash(self, value: str) -> str:
        # Demo mode: force a known password for all accounts for easy testing.
        forced = os.getenv("DEMO_FIXED_PASSWORD", "").strip()
        if forced:
            return security_service.hash_password(forced)
        return security_service.hash_password(value)

    def _verify_hash(self, plain: str, hashed: str) -> bool:
        try:
            return security_service.verify_password(plain, hashed)
        except Exception:
            # Backward compatibility for legacy rows saved before bcrypt migration.
            legacy_sha256 = hashlib.sha256(plain.encode("utf-8")).hexdigest()
            if len(hashed) == 64 and all(c in "0123456789abcdef" for c in hashed.lower()):
                return legacy_sha256 == hashed.lower()
            # Last-resort compatibility for accidental plain-text demo rows.
            return plain == hashed

    def _is_bcrypt_hash(self, value: str) -> bool:
        return value.startswith("$2a$") or value.startswith("$2b$") or value.startswith("$2y$")

    def _next_uhid(self, db: Session) -> str:
        year = datetime.utcnow().year
        prefix = f"UHID{year}"
        existing = db.scalars(select(Patient.uhid).where(Patient.uhid.like(f"{prefix}%"))).all()
        max_suffix = 0
        for u in existing:
            tail = str(u)[len(prefix):]
            if tail.isdigit():
                max_suffix = max(max_suffix, int(tail))
        return f"{prefix}{(max_suffix + 1):04d}"

    def _parse_location(self, address: str) -> tuple[str, str]:
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 2:
            return parts[-1], parts[-2]
        return "Unknown", "Unknown"

    def _normalize_language_csv(self, raw: str | None) -> str:
        if not raw:
            return "English"
        seen: set[str] = set()
        out: list[str] = []
        for item in str(raw).replace(";", ",").split(","):
            name = item.strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return ",".join(out) if out else "English"

    def _split_languages(self, raw: str | None) -> list[str]:
        if not raw:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in str(raw).replace(";", ",").split(","):
            name = item.strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return out

    def generate_qr(self, uhid: str) -> tuple[str, str]:
        qr_token = f"QR::{uhid}::{uuid.uuid4().hex[:12]}"
        filename = f"{uhid}_qr.png"
        path = self.qr_dir / filename
        img = qrcode.make(qr_token)
        img.save(path)
        return qr_token, str(path)

    def register_patient(self, db: Session, payload: dict) -> dict:
        payload["email"] = payload["email"].strip().lower()
        payload["phone"] = payload["phone"].strip()
        aadhaar_hash = hashlib.sha256(payload["aadhaar"].encode("utf-8")).hexdigest()
        if db.scalar(select(Patient).where(Patient.aadhaar_hash == aadhaar_hash)):
            raise ValueError("Aadhaar already registered")
        if db.scalar(select(Patient).where(Patient.phone == payload["phone"])):
            raise ValueError("Phone already registered")
        if db.scalar(select(Patient).where(Patient.email == payload["email"])):
            raise ValueError("Email already registered")
        if payload["password"] != payload["confirm_password"]:
            raise ValueError("Password mismatch")

        uhid = self._next_uhid(db)
        qr_token, qr_path = self.generate_qr(uhid)
        state, district = self._parse_location(payload["address"])
        patient = Patient(
            patient_id=f"PAT-{uuid.uuid4().hex[:8]}",
            uhid=uhid,
            full_name=payload["full_name"],
            aadhaar_hash=aadhaar_hash,
            dob=datetime.fromisoformat(payload["dob"]).date(),
            email=payload["email"],
            phone=payload["phone"],
            language=payload.get("language", "English"),
            address=payload["address"],
            state=state,
            district=district,
            blood_group=payload["blood_group"],
            password_hash=self._hash(payload["password"]),
            qr_token=qr_token,
            qr_path=qr_path,
            live_location_enabled=payload.get("live_location_enabled", False),
            latitude=payload.get("latitude", 0.0),
            longitude=payload.get("longitude", 0.0),
        )
        db.add(patient)
        db.commit()
        return {"uhid": patient.uhid, "qr_token": patient.qr_token, "success": True, "patient_id": patient.patient_id}

    def login_patient(self, db: Session, full_name: str | None, phone: str | None, email: str | None, password: str) -> dict:
        q = select(Patient)
        if full_name:
            q = q.where(Patient.full_name == full_name)
        elif phone:
            q = q.where(Patient.phone == phone)
        elif email:
            q = q.where(Patient.email == email.lower().strip())
        else:
            raise ValueError("Provide full_name, phone, or email")
        patient = db.scalar(q)
        if not patient or not self._verify_hash(password, patient.password_hash):
            raise ValueError("Invalid credentials")
        if not self._is_bcrypt_hash(patient.password_hash):
            patient.password_hash = self._hash(password)
            db.commit()
        return {
            "patient_id": patient.patient_id,
            "uhid": patient.uhid,
            "token": security_service.create_access_token(patient.patient_id, "patient", {"uhid": patient.uhid}),
        }

    def get_patient_profile(self, db: Session, patient_id: str) -> dict:
        patient = db.scalar(select(Patient).where(Patient.patient_id == patient_id))
        if not patient:
            raise ValueError("Patient not found")
        today = datetime.utcnow().date()
        age = today.year - patient.dob.year - ((today.month, today.day) < (patient.dob.month, patient.dob.day))
        return {
            "name": patient.full_name,
            "uhid": patient.uhid,
            "blood_group": patient.blood_group,
            "age": age,
            "email": patient.email,
            "phone": patient.phone,
            "language": patient.language,
        }

    def update_patient_profile(self, db: Session, patient_id: str, payload: dict) -> dict:
        patient = db.scalar(select(Patient).where(Patient.patient_id == patient_id))
        if not patient:
            raise ValueError("Patient not found")

        name = (payload.get("name") or "").strip()
        email = (payload.get("email") or "").strip().lower()
        phone = (payload.get("phone") or "").strip()
        language = (payload.get("language") or "").strip()

        if name:
            patient.full_name = name
        if email and email != patient.email:
            exists = db.scalar(select(Patient).where(Patient.email == email, Patient.patient_id != patient_id))
            if exists:
                raise ValueError("Patient email already exists")
            patient.email = email
        if phone and phone != patient.phone:
            exists = db.scalar(select(Patient).where(Patient.phone == phone, Patient.patient_id != patient_id))
            if exists:
                raise ValueError("Patient phone already exists")
            patient.phone = phone
        if language:
            patient.language = language

        db.commit()
        data = self.get_patient_profile(db, patient_id)
        data["email"] = patient.email
        data["phone"] = patient.phone
        data["language"] = patient.language
        return data

    def get_patient_qr(self, db: Session, patient_id: str) -> str:
        patient = db.scalar(select(Patient).where(Patient.patient_id == patient_id))
        if not patient:
            raise ValueError("Patient not found")
        if not os.path.exists(patient.qr_path):
            token, path = self.generate_qr(patient.uhid)
            patient.qr_token = token
            patient.qr_path = path
            db.commit()
        return patient.qr_path

    def process_qr_scan(self, db: Session, qr_token: str) -> dict:
        patient = db.scalar(select(Patient).where(Patient.qr_token == qr_token))
        if not patient:
            raise ValueError("Invalid QR token")
        return self.fetch_patient_history(db, patient.uhid) | {"patient_profile": self.get_patient_profile(db, patient.patient_id)}

    def register_doctor(self, db: Session, payload: dict) -> dict:
        payload["government_license_id"] = payload["government_license_id"].strip().upper()
        payload["email"] = payload["email"].strip().lower()
        payload["phone"] = payload.get("phone", "").strip()
        license_row = db.scalar(
            select(DoctorLicenseRegistry).where(
                DoctorLicenseRegistry.government_license_id == payload["government_license_id"],
                DoctorLicenseRegistry.is_active.is_(True),
            )
        )
        if not license_row:
            raise ValueError("Invalid government license ID")
        if payload["password"] != payload["confirm_password"]:
            raise ValueError("Password mismatch")
        existing_by_license = db.scalar(select(Doctor).where(Doctor.government_license_id == payload["government_license_id"]))
        if existing_by_license:
            conflict_email = db.scalar(
                select(Doctor).where(
                    Doctor.email == payload["email"],
                    Doctor.doctor_id != existing_by_license.doctor_id,
                )
            )
            if conflict_email:
                raise ValueError("Doctor email already exists")
            conflict_phone = db.scalar(
                select(DoctorProfile).where(
                    DoctorProfile.phone == payload.get("phone", ""),
                    DoctorProfile.doctor_id != existing_by_license.doctor_id,
                )
            )
            if conflict_phone:
                raise ValueError("Doctor phone already exists")
            existing_by_license.full_name = payload["full_name"]
            existing_by_license.email = payload["email"]
            existing_by_license.password_hash = self._hash(payload["password"])
            profile = db.scalar(select(DoctorProfile).where(DoctorProfile.doctor_id == existing_by_license.doctor_id))
            if profile:
                profile.phone = payload.get("phone", "")
                profile.address = payload.get("address", "")
            else:
                db.add(
                    DoctorProfile(
                        doctor_id=existing_by_license.doctor_id,
                        phone=payload.get("phone", ""),
                        address=payload.get("address", ""),
                    )
                )
            db.commit()
            return {
                "doctor_id": existing_by_license.doctor_id,
                "token": security_service.create_access_token(existing_by_license.doctor_id, "doctor"),
                "updated_existing": True,
            }
        if db.scalar(select(Doctor).where(Doctor.email == payload["email"])):
            raise ValueError("Doctor email already exists")
        if db.scalar(select(DoctorProfile).where(DoctorProfile.phone == payload.get("phone", ""))):
            raise ValueError("Doctor phone already exists")
        doctor = Doctor(
            doctor_id=f"DOC-{uuid.uuid4().hex[:8]}",
            full_name=payload["full_name"],
            government_license_id=payload["government_license_id"],
            email=payload["email"],
            password_hash=self._hash(payload["password"]),
        )
        db.add(doctor)
        db.flush()
        doctor_profile = DoctorProfile(
            doctor_id=doctor.doctor_id,
            phone=payload.get("phone", ""),
            address=payload.get("address", ""),
        )
        db.add(doctor_profile)
        db.commit()
        return {
            "doctor_id": doctor.doctor_id,
            "token": security_service.create_access_token(doctor.doctor_id, "doctor"),
        }

    def login_doctor(self, db: Session, identifier: str, password: str) -> dict:
        doctor = self._find_doctor_by_identifier(db, identifier.strip())
        if not doctor or not self._verify_hash(password, doctor.password_hash):
            raise ValueError("Invalid credentials")
        if not self._is_bcrypt_hash(doctor.password_hash):
            doctor.password_hash = self._hash(password)
            db.commit()
        return {
            "doctor_id": doctor.doctor_id,
            "token": security_service.create_access_token(doctor.doctor_id, "doctor"),
        }

    def doctor_stats(self, db: Session, doctor_id: str) -> dict:
        now = datetime.utcnow()
        day_start = now - timedelta(days=1)
        week_start = now - timedelta(days=7)
        month_start = now - timedelta(days=30)
        today = db.query(Visit).filter(Visit.doctor_id == doctor_id, Visit.visit_date >= day_start).count()
        week = db.query(Visit).filter(Visit.doctor_id == doctor_id, Visit.visit_date >= week_start).count()
        month = db.query(Visit).filter(Visit.doctor_id == doctor_id, Visit.visit_date >= month_start).count()
        return {"patients_today": today, "patients_this_week": week, "patients_this_month": month}

    def get_doctor_profile(self, db: Session, doctor_id: str) -> dict:
        doctor = db.scalar(select(Doctor).where(Doctor.doctor_id == doctor_id))
        if not doctor:
            raise ValueError("Doctor not found")
        profile = db.scalar(select(DoctorProfile).where(DoctorProfile.doctor_id == doctor_id))
        return {
            "doctor_id": doctor.doctor_id,
            "full_name": doctor.full_name,
            "government_license_id": doctor.government_license_id,
            "email": doctor.email,
            "phone": profile.phone if profile else "",
            "address": profile.address if profile else "",
        }

    def update_doctor_profile(self, db: Session, doctor_id: str, payload: dict) -> dict:
        doctor = db.scalar(select(Doctor).where(Doctor.doctor_id == doctor_id))
        if not doctor:
            raise ValueError("Doctor not found")
        profile = db.scalar(select(DoctorProfile).where(DoctorProfile.doctor_id == doctor_id))
        if not profile:
            profile = DoctorProfile(doctor_id=doctor_id, phone="", address="")
            db.add(profile)
            db.flush()

        full_name = (payload.get("full_name") or "").strip()
        email = (payload.get("email") or "").strip().lower()
        phone = (payload.get("phone") or "").strip()
        address = (payload.get("address") or "").strip()

        if full_name:
            doctor.full_name = full_name
        if email and email != doctor.email:
            if db.scalar(select(Doctor).where(Doctor.email == email, Doctor.doctor_id != doctor_id)):
                raise ValueError("Doctor email already exists")
            doctor.email = email
        if phone and phone != profile.phone:
            if db.scalar(select(DoctorProfile).where(DoctorProfile.phone == phone, DoctorProfile.doctor_id != doctor_id)):
                raise ValueError("Doctor phone already exists")
            profile.phone = phone
        if address:
            profile.address = address

        db.commit()
        return self.get_doctor_profile(db, doctor_id)

    def notify_patient_report_updated(self, db: Session, uhid: str, doctor_id: str, visit_id: str | None = None) -> dict:
        patient = db.scalar(select(Patient).where(Patient.uhid == uhid))
        if not patient:
            raise ValueError("Patient not found")
        item = {
            "message": f"New medical report updated by {doctor_id}",
            "created_at": datetime.utcnow().isoformat(),
            "category": "report_update",
            "visit_id": visit_id or "",
            "uhid": uhid,
        }
        mock_storage.save_notification(role="patient", user_id=patient.patient_id, item=item)
        return {"notified": True, "patient_id": patient.patient_id, "uhid": uhid}

    def create_visit(self, db: Session, payload: dict) -> dict:
        patient = db.scalar(select(Patient).where(Patient.uhid == payload["uhid"]))
        if not patient:
            raise ValueError("UHID not found")
        doctor_id = payload.get("doctor_id")
        student_id = payload.get("student_id")
        if not doctor_id and not student_id:
            raise ValueError("Either doctor_id or student_id is required")
        if doctor_id and not db.scalar(select(Doctor).where(Doctor.doctor_id == doctor_id)):
            raise ValueError("Doctor not found")
        if student_id and not db.scalar(select(Student).where(Student.student_id == student_id)):
            raise ValueError("Student not found")

        visit = Visit(
            visit_id=f"VIS-{uuid.uuid4().hex[:10]}",
            uhid=payload["uhid"],
            doctor_id=doctor_id,
            student_id=student_id,
            consultation_type=payload.get("consultation_type", "doctor"),
            notes=payload.get("visit_notes", ""),
            visit_date=datetime.fromisoformat(payload["visit_date"]) if payload.get("visit_date") else datetime.utcnow(),
        )
        db.add(visit)
        db.commit()
        return {"visit_id": visit.visit_id}

    def create_diagnosis(self, db: Session, payload: dict) -> dict:
        visit = db.scalar(select(Visit).where(Visit.visit_id == payload["visit_id"]))
        if not visit:
            raise ValueError("Visit not found")
        if visit.uhid != payload["uhid"]:
            raise ValueError("visit_id and uhid mismatch")
        diagnosis = Diagnosis(
            diagnosis_id=f"DIA-{uuid.uuid4().hex[:10]}",
            visit_id=visit.visit_id,
            uhid=payload["uhid"],
            disease_category=payload["disease_category"],
            disease_name=payload.get("disease_name"),
            severity=payload.get("severity"),
        )
        db.add(diagnosis)
        db.commit()
        return {"diagnosis_id": diagnosis.diagnosis_id}

    def create_recommendation(self, db: Session, payload: dict) -> dict:
        visit = db.scalar(select(Visit).where(Visit.visit_id == payload["visit_id"]))
        if not visit:
            raise ValueError("Visit not found")
        if visit.uhid != payload["uhid"]:
            raise ValueError("visit_id and uhid mismatch")
        rec = Recommendation(
            recommendation_id=f"REC-{uuid.uuid4().hex[:10]}",
            visit_id=visit.visit_id,
            uhid=payload["uhid"],
            advice_text=payload["advice_text"],
            confidence_score=payload.get("confidence_score", 0.0),
        )
        db.add(rec)
        db.commit()
        return {"recommendation_id": rec.recommendation_id}

    def fetch_patient_history(self, db: Session, uhid: str) -> dict:
        visits = db.scalars(select(Visit).where(Visit.uhid == uhid).order_by(Visit.visit_date.desc())).all()
        grouped = []
        for visit in visits:
            diagnoses = db.scalars(select(Diagnosis).where(Diagnosis.visit_id == visit.visit_id)).all()
            recs = db.scalars(select(Recommendation).where(Recommendation.visit_id == visit.visit_id)).all()
            grouped.append(
                {
                    "visit": {
                        "visit_id": visit.visit_id,
                        "uhid": visit.uhid,
                        "doctor_id": visit.doctor_id,
                        "student_id": visit.student_id,
                        "consultation_type": visit.consultation_type,
                        "notes": visit.notes,
                        "visit_date": visit.visit_date.isoformat(),
                    },
                    "diagnosis": [
                        {
                            "diagnosis_id": d.diagnosis_id,
                            "disease_category": d.disease_category,
                            "disease_name": d.disease_name,
                            "severity": d.severity,
                        }
                        for d in diagnoses
                    ],
                    "recommendations": [
                        {
                            "recommendation_id": r.recommendation_id,
                            "advice_text": r.advice_text,
                            "confidence_score": r.confidence_score,
                        }
                        for r in recs
                    ],
                }
            )
        return {"uhid": uhid, "history": grouped}

    def fetch_patient_summary(self, db: Session, uhid: str) -> dict:
        patient = db.scalar(select(Patient).where(Patient.uhid == uhid))
        if not patient:
            raise ValueError("Patient not found")
        visits = db.scalars(select(Visit).where(Visit.uhid == uhid).order_by(Visit.visit_date.desc()).limit(5)).all()
        summary_rows = (
            db.query(Diagnosis.disease_category, func.count(Diagnosis.id))
            .filter(Diagnosis.uhid == uhid)
            .group_by(Diagnosis.disease_category)
            .all()
        )
        return {
            "patient": {
                "full_name": patient.full_name,
                "uhid": patient.uhid,
                "blood_group": patient.blood_group,
                "state": patient.state,
                "district": patient.district,
            },
            "recent_visits": [{"visit_id": v.visit_id, "visit_date": v.visit_date.isoformat(), "notes": v.notes} for v in visits],
            "diagnosis_summary": [{"disease_category": row[0], "count": row[1]} for row in summary_rows],
        }

    def verify_college(self, college_id: str, official_email: str) -> dict:
        raise ValueError("Use verify_college_db with institute_name and database context")

    def verify_college_db(self, db: Session, college_id: str, institute_name: str, official_email: str) -> dict:
        college_id = college_id.strip().upper()
        institute_name = institute_name.strip()
        normalized_email = official_email.lower().strip()
        registry_row = db.scalar(
            select(StudentRegistry).where(
                StudentRegistry.college_id == college_id,
                StudentRegistry.institute_name == institute_name,
                StudentRegistry.is_active.is_(True),
            )
        )
        if not registry_row:
            raise ValueError("Not in verified student registry")
        row = db.scalar(
            select(StudentVerification).where(
                StudentVerification.college_id == college_id,
                StudentVerification.institute_name == institute_name,
                StudentVerification.official_email == normalized_email,
            )
        )
        otp_code, expires_at = otp_service.issue("student_registration", f"{college_id}:{normalized_email}")
        if not row:
            row = StudentVerification(
                college_id=college_id,
                institute_name=institute_name,
                official_email=normalized_email,
                otp_code=otp_code,
                is_confirmed=False,
            )
            db.add(row)
        else:
            row.otp_code = otp_code
            row.is_confirmed = False
            row.created_at = datetime.utcnow()
        db.commit()
        return {"status": "otp_sent", "expires_at": expires_at, "otp_dev_hint": otp_code}

    def confirm_student_otp(self, college_id: str, otp: str) -> dict:
        if self.otp_store["student"].get(college_id) != otp:
            raise ValueError("Invalid OTP")
        self.otp_store["student_ok"][college_id] = "ok"
        return {"status": "otp_confirmed"}

    def confirm_student_otp_db(self, db: Session, college_id: str, otp: str) -> dict:
        college_id = college_id.strip().upper()
        row = db.scalar(
            select(StudentVerification).where(StudentVerification.college_id == college_id).order_by(StudentVerification.created_at.desc())
        )
        if not row:
            raise ValueError("Invalid OTP")
        id_key = f"{row.college_id}:{row.official_email.lower().strip()}"
        if not otp_service.verify("student_registration", id_key, otp):
            raise ValueError("Invalid or expired OTP")
        row.is_confirmed = True
        db.commit()
        return {"status": "otp_confirmed"}

    def register_student(
        self,
        db: Session,
        college_id: str,
        institute_name: str,
        official_email: str,
        phone: str | None,
        password: str,
        full_name: str | None = None,
    ) -> dict:
        normalized_email = official_email.lower().strip()
        college_id = college_id.strip().upper()
        institute_name = institute_name.strip()
        phone = phone.strip() if phone else None
        verification = db.scalar(
            select(StudentVerification).where(
                StudentVerification.college_id == college_id,
                StudentVerification.institute_name == institute_name,
                StudentVerification.official_email == normalized_email,
                StudentVerification.is_confirmed.is_(True),
            )
        )
        if not verification:
            raise ValueError("Verification pending")
        if phone and db.scalar(select(Student).where(Student.phone == phone)):
            raise ValueError("Phone already registered")
        existing = db.scalar(select(Student).where(Student.official_email == normalized_email))
        if existing:
            # Allow reset-style re-registration after verification so login succeeds immediately.
            existing.password_hash = self._hash(password)
            existing.college_id = college_id
            existing.institute_name = institute_name
            existing.phone = phone
            if full_name and full_name.strip():
                existing.full_name = full_name.strip()
            db.commit()
            return {"status": "registration_success", "student_id": existing.student_id, "updated_existing": True}
        inferred_name = (full_name or "").strip() or normalized_email.split("@")[0].replace(".", " ").title()
        student = Student(
            student_id=f"STU-{uuid.uuid4().hex[:8]}",
            full_name=inferred_name,
            college_id=college_id,
            institute_name=institute_name,
            official_email=normalized_email,
            phone=phone,
            password_hash=self._hash(password),
        )
        db.add(student)
        db.commit()
        return {"status": "registration_success", "student_id": student.student_id}

    def login_student(self, db: Session, identifier: str, password: str) -> dict:
        student = self._find_student_by_identifier(db, identifier.lower().strip())
        if not student or not self._verify_hash(password, student.password_hash):
            raise ValueError("Invalid credentials")
        if not self._is_bcrypt_hash(student.password_hash):
            student.password_hash = self._hash(password)
            db.commit()
        return {
            "student_id": student.student_id,
            "token": security_service.create_access_token(student.student_id, "student"),
        }

    def _find_patient_by_identifier(self, db: Session, identifier: str) -> Patient | None:
        ident = identifier.strip().lower()
        return db.scalar(select(Patient).where((Patient.email == ident) | (Patient.phone == ident)))

    def _find_doctor_by_identifier(self, db: Session, identifier: str) -> Doctor | None:
        ident = identifier.strip().lower()
        doctor = db.scalar(select(Doctor).where(Doctor.email == ident))
        if doctor:
            return doctor
        profile = db.scalar(select(DoctorProfile).where(DoctorProfile.phone == ident))
        if not profile:
            return None
        return db.scalar(select(Doctor).where(Doctor.doctor_id == profile.doctor_id))

    def _find_student_by_identifier(self, db: Session, identifier: str) -> Student | None:
        ident = identifier.strip().lower()
        student = db.scalar(select(Student).where(Student.official_email == ident))
        if student:
            return student
        return db.scalar(select(Student).where(Student.phone == ident))

    def send_patient_registration_otp(self, db: Session, identifier: str) -> dict:
        patient = self._find_patient_by_identifier(db, identifier)
        if patient:
            raise ValueError("Identifier already registered")
        otp, expires_at = otp_service.issue("patient_registration", identifier.lower().strip())
        return {"status": "otp_sent", "expires_at": expires_at, "otp_dev_hint": otp}

    def verify_patient_registration_otp(self, identifier: str, otp: str) -> dict:
        ok = otp_service.verify("patient_registration", identifier.lower().strip(), otp)
        if not ok:
            raise ValueError("Invalid or expired OTP")
        return {"status": "otp_verified"}

    def send_patient_forgot_password_otp(self, db: Session, identifier: str) -> dict:
        patient = self._find_patient_by_identifier(db, identifier)
        if not patient:
            raise ValueError("Identifier not registered")
        otp, expires_at = otp_service.issue("patient_forgot", identifier.lower().strip())
        return {"status": "otp_sent", "expires_at": expires_at, "otp_dev_hint": otp}

    def send_forgot_password_otp(self, db: Session, role: str, identifier: str) -> dict:
        normalized_role = role.lower().strip()
        finder = {
            "patient": self._find_patient_by_identifier,
            "doctor": self._find_doctor_by_identifier,
            "student": self._find_student_by_identifier,
        }.get(normalized_role)
        if finder is None:
            raise ValueError("Invalid role")
        account = finder(db, identifier)
        if not account:
            raise ValueError("Identifier not registered")
        otp, expires_at = otp_service.issue(f"{normalized_role}_forgot", identifier.lower().strip())
        return {"status": "otp_sent", "expires_at": expires_at, "otp_dev_hint": otp}

    def verify_patient_forgot_otp(self, identifier: str, otp: str) -> dict:
        ok = otp_service.verify("patient_forgot", identifier.lower().strip(), otp)
        if not ok:
            raise ValueError("Invalid or expired OTP")
        return {"status": "otp_verified"}

    def verify_forgot_otp(self, role: str, identifier: str, otp: str) -> dict:
        normalized_role = role.lower().strip()
        ok = otp_service.verify(f"{normalized_role}_forgot", identifier.lower().strip(), otp)
        if not ok:
            raise ValueError("Invalid or expired OTP")
        return {"status": "otp_verified"}

    def reset_patient_password(self, db: Session, identifier: str, new_password: str) -> dict:
        patient = self._find_patient_by_identifier(db, identifier)
        if not patient:
            raise ValueError("Identifier not registered")
        patient.password_hash = self._hash(new_password)
        db.commit()
        return {"status": "password_reset_success"}

    def reset_password_by_role(self, db: Session, role: str, identifier: str, new_password: str) -> dict:
        normalized_role = role.lower().strip()
        if normalized_role == "patient":
            return self.reset_patient_password(db, identifier, new_password)
        if normalized_role == "doctor":
            doctor = self._find_doctor_by_identifier(db, identifier)
            if not doctor:
                raise ValueError("Identifier not registered")
            doctor.password_hash = self._hash(new_password)
            db.commit()
            return {"status": "password_reset_success"}
        if normalized_role == "student":
            student = self._find_student_by_identifier(db, identifier)
            if not student:
                raise ValueError("Identifier not registered")
            student.password_hash = self._hash(new_password)
            db.commit()
            return {"status": "password_reset_success"}
        raise ValueError("Invalid role")

    def seed_demo_records(self, db: Session) -> dict:
        seeded_doctors = 0
        seeded_students = 0
        seeded_doctor_registry = 0
        seeded_student_registry = 0
        seeded_government_officials = 0

        for license_id, doctor_name in [
            ("GOV-AYUSH-1001", "Verified Doctor 1"),
            ("GOV-AYUSH-1006", "Verified Doctor 6"),
            ("DOC-434f62fb", "Demo Verified Doctor"),
        ]:
            if not db.scalar(select(DoctorLicenseRegistry).where(DoctorLicenseRegistry.government_license_id == license_id)):
                db.add(DoctorLicenseRegistry(government_license_id=license_id, doctor_name=doctor_name, is_active=True))
                seeded_doctor_registry += 1

        student_registry_rows = [
            ("COLL-5001", "AYUSH Medical College", "koushik.chinu.2007@gmail.com"),
            ("COLL-5002", "AYUSH Medical College", "koushik.chinu.2007@gmail.com"),
            ("COLL-5003", "National AYUSH Institute", "koushik.chinu.2007@gmail.com"),
        ]
        for college_id, institute_name, official_email in student_registry_rows:
            if not db.scalar(
                select(StudentRegistry).where(
                    StudentRegistry.college_id == college_id,
                    StudentRegistry.institute_name == institute_name,
                )
            ):
                db.add(
                    StudentRegistry(
                        college_id=college_id,
                        institute_name=institute_name,
                        official_email=official_email,
                        is_active=True,
                    )
                )
                seeded_student_registry += 1

        gov_username = os.getenv("GOV_USERNAME", "gov_admin").strip()
        gov_password = os.getenv("GOV_PASSWORD", "Gov@123456").strip()
        if gov_username and gov_password:
            gov_row = db.scalar(select(GovernmentOfficial).where(GovernmentOfficial.username == gov_username))
            if not gov_row:
                db.add(
                    GovernmentOfficial(
                        username=gov_username,
                        full_name="Government Official",
                        designation="Health Monitoring Officer",
                        password_hash=security_service.hash_password(gov_password),
                        is_active=True,
                    )
                )
                seeded_government_officials += 1
            else:
                try:
                    if not security_service.verify_password(gov_password, gov_row.password_hash):
                        gov_row.password_hash = security_service.hash_password(gov_password)
                except Exception:
                    gov_row.password_hash = security_service.hash_password(gov_password)

        # Keep seed size minimal to avoid growing demo DB.
        db.commit()
        return {
            "seeded_doctors": seeded_doctors,
            "seeded_students": seeded_students,
            "seeded_doctor_registry": seeded_doctor_registry,
            "seeded_student_registry": seeded_student_registry,
            "seeded_government_officials": seeded_government_officials,
        }

    def set_student_online(self, db: Session, student_id: str, language: str | None = None) -> dict:
        student = db.scalar(select(Student).where(Student.student_id == student_id))
        if not student:
            raise ValueError("Student not found")
        stale_cutoff = datetime.utcnow() - timedelta(minutes=45)
        stale_active = db.scalars(
            select(ConsultationSession).where(
                ConsultationSession.student_id == student_id,
                ConsultationSession.status == "active",
                ConsultationSession.accepted_at.is_not(None),
                ConsultationSession.accepted_at < stale_cutoff,
            )
        ).all()
        for sess in stale_active:
            sess.status = "ended"
            sess.ended_at = datetime.utcnow()
        if language:
            student.language_preference = self._normalize_language_csv(language)
        db.commit()
        return {"online": True, "stale_sessions_closed": len(stale_active)}

    def request_consultant_video_call(
        self,
        db: Session,
        patient_id: str,
        language: str,
        problem: str,
        mode: str = "video",
    ) -> dict:
        mode_norm = (mode or "video").strip().lower()
        if mode_norm not in {"video", "chat"}:
            mode_norm = "video"
        now = datetime.utcnow()
        active_patient_session = db.scalar(
            select(ConsultationSession).where(
                ConsultationSession.patient_id == patient_id,
                (
                    (ConsultationSession.status == "active")
                    | (
                        (ConsultationSession.status == "pending")
                        & (ConsultationSession.expires_at > now)
                    )
                ),
            )
        )
        if active_patient_session:
            return {
                "session_id": active_patient_session.session_id,
                "student_id": active_patient_session.student_id,
                "mode": active_patient_session.mode,
                "status": active_patient_session.status,
                "expires_at": active_patient_session.expires_at.isoformat() if active_patient_session.expires_at else None,
                "notification": "Existing consultation session in progress",
            }

        busy_student_ids = set(
            db.scalars(
                select(ConsultationSession.student_id).where(
                    ConsultationSession.status == "active"
                )
            ).all()
        )
        requested_languages = self._split_languages(language) or ["English"]
        candidates = db.scalars(select(Student)).all()
        online_candidates = []
        for student in candidates:
            if busy_student_ids and student.student_id in busy_student_ids:
                continue
            student_languages = {x.lower() for x in self._split_languages(student.language_preference)}
            if not student_languages:
                continue
            if any(req.lower() in student_languages for req in requested_languages):
                online_candidates.append(student)
        online_student = random.choice(online_candidates) if online_candidates else None
        if not online_student:
            raise ValueError("No Senior Medical Student Consultant available for this language")
        session = ConsultationSession(
            session_id=f"SES-{uuid.uuid4().hex[:10]}",
            patient_id=patient_id,
            student_id=online_student.student_id,
            language=self._normalize_language_csv(",".join(requested_languages)),
            mode=mode_norm,
            status="pending",
            problem=problem,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        db.add(session)
        db.commit()
        return {
            "session_id": session.session_id,
            "student_id": session.student_id,
            "mode": session.mode,
            "status": session.status,
            "expires_at": session.expires_at.isoformat(),
            "notification": "Request sent to Senior Medical Student Consultant",
        }

    def reject_and_rematch_session(self, db: Session, session_id: str, rejecting_student_id: str) -> dict:
        session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == session_id))
        if not session:
            raise ValueError("Session not found")
        if session.student_id != rejecting_student_id:
            raise ValueError("Rejecting student does not own this session")
        session.status = "rejected"
        session.ended_at = datetime.utcnow()
        db.flush()

        now = datetime.utcnow()
        busy_student_ids = set(
            db.scalars(
                select(ConsultationSession.student_id).where(
                    ConsultationSession.status == "active"
                )
            ).all()
        )
        busy_student_ids.add(rejecting_student_id)

        requested_languages = self._split_languages(session.language) or ["English"]
        candidates = db.scalars(select(Student)).all()
        replacement_pool = []
        for student in candidates:
            if busy_student_ids and student.student_id in busy_student_ids:
                continue
            student_languages = {x.lower() for x in self._split_languages(student.language_preference)}
            if not student_languages:
                continue
            if any(req.lower() in student_languages for req in requested_languages):
                replacement_pool.append(student)
        replacement = random.choice(replacement_pool) if replacement_pool else None
        if not replacement:
            db.commit()
            return {"rejected": True, "rematched": False, "session_id": session_id}

        new_session = ConsultationSession(
            session_id=f"SES-{uuid.uuid4().hex[:10]}",
            patient_id=session.patient_id,
            student_id=replacement.student_id,
            language=session.language,
            mode=session.mode,
            status="pending",
            problem=session.problem,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        db.add(new_session)
        db.commit()
        return {
            "rejected": True,
            "rematched": True,
            "old_session_id": session_id,
            "session_id": new_session.session_id,
            "student_id": replacement.student_id,
            "mode": new_session.mode,
            "status": new_session.status,
            "expires_at": new_session.expires_at.isoformat(),
        }

    def video_join_info(self, db: Session, session_id: str, requester_id: str) -> dict:
        session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == session_id))
        if not session:
            raise ValueError("Session not found")
        if requester_id not in {session.patient_id, session.student_id}:
            raise ValueError("Requester not part of this session")
        if session.status not in {"active", "pending"}:
            raise ValueError("Session is not joinable")
        room = f"ayush-room-{session.session_id}"
        return {
            "session_id": session_id,
            "room_id": room,
            "join_url": f"/webrtc/{room}",
            "mode": session.mode,
            "status": session.status,
        }

    def student_pending_requests(self, db: Session, student_id: str) -> list[dict]:
        sessions = db.scalars(
            select(ConsultationSession)
            .where(ConsultationSession.student_id == student_id, ConsultationSession.status == "pending")
            .order_by(ConsultationSession.created_at.desc())
        ).all()
        out: list[dict] = []
        for s in sessions:
            patient = db.scalar(select(Patient).where(Patient.patient_id == s.patient_id))
            out.append(
                {
                    "session_id": s.session_id,
                    "patient_id": s.patient_id,
                    "patient_name": patient.full_name if patient else "Unknown",
                    "patient_uhid": patient.uhid if patient else "-",
                    "patient_age": int(((datetime.utcnow().date() - patient.dob).days // 365)) if patient and patient.dob else None,
                    "language": s.language,
                    "mode": s.mode,
                    "problem": s.problem,
                    "expires_at": s.expires_at.isoformat(),
                    "alert": "Pick within 5-10 min",
                }
            )
        return out

    def get_student_profile(self, db: Session, student_id: str) -> dict:
        student = db.scalar(select(Student).where(Student.student_id == student_id))
        if not student:
            raise ValueError("Student not found")
        sessions = db.scalars(
            select(ConsultationSession)
            .where(ConsultationSession.student_id == student_id)
            .order_by(ConsultationSession.created_at.desc())
            .limit(15)
        ).all()
        patient_ids = [s.patient_id for s in sessions]
        patient_map = {
            p.patient_id: p
            for p in db.scalars(select(Patient).where(Patient.patient_id.in_(patient_ids))).all()
        } if patient_ids else {}
        recent_patients: list[dict] = []
        seen: set[str] = set()
        for s in sessions:
            if s.patient_id in seen:
                continue
            seen.add(s.patient_id)
            p = patient_map.get(s.patient_id)
            recent_patients.append(
                {
                    "patient_id": s.patient_id,
                    "patient_name": p.full_name if p else "Unknown",
                    "uhid": p.uhid if p else "-",
                    "last_problem": s.problem or "-",
                    "last_mode": s.mode,
                    "last_status": s.status,
                    "last_session_id": s.session_id,
                }
            )
            if len(recent_patients) >= 8:
                break
        rating_count = int(student.rating_count or 0)
        if rating_count <= 0:
            rating_count = len([s for s in sessions if s.status == "ended"])
        return {
            "student_id": student.student_id,
            "full_name": student.full_name or "Senior Medical Student",
            "college_id": student.college_id,
            "institute_name": student.institute_name,
            "official_email": student.official_email,
            "phone": student.phone,
            "language_preference": student.language_preference or "English",
            "languages": self._split_languages(student.language_preference) or ["English"],
            "rating_avg": float(student.rating_avg or 0.0),
            "rating_count": rating_count,
            "recent_patients": recent_patients,
        }

    def update_student_profile(self, db: Session, student_id: str, payload: dict) -> dict:
        student = db.scalar(select(Student).where(Student.student_id == student_id))
        if not student:
            raise ValueError("Student not found")
        full_name = (payload.get("full_name") or "").strip()
        institute_name = (payload.get("institute_name") or "").strip()
        phone = (payload.get("phone") or "").strip()
        official_email = (payload.get("official_email") or "").strip().lower()
        languages = payload.get("languages")
        language_preference = (payload.get("language_preference") or "").strip()

        if full_name:
            student.full_name = full_name
        if institute_name:
            student.institute_name = institute_name
        if phone:
            conflict_phone = db.scalar(select(Student).where(Student.phone == phone, Student.student_id != student_id))
            if conflict_phone:
                raise ValueError("Phone already in use")
            student.phone = phone
        if official_email:
            conflict_email = db.scalar(select(Student).where(Student.official_email == official_email, Student.student_id != student_id))
            if conflict_email:
                raise ValueError("Official email already in use")
            student.official_email = official_email
        if isinstance(languages, list) and languages:
            student.language_preference = self._normalize_language_csv(",".join([str(x) for x in languages]))
        elif language_preference:
            student.language_preference = self._normalize_language_csv(language_preference)
        db.commit()
        return self.get_student_profile(db, student_id)

    def accept_consultation_session(self, db: Session, session_id: str) -> dict:
        session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == session_id))
        if not session:
            raise ValueError("Session not found")
        session.status = "active"
        session.accepted_at = datetime.utcnow()
        db.commit()
        return {"status": "accepted", "session_id": session_id}

    def end_consultation_session(self, db: Session, session_id: str) -> dict:
        session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == session_id))
        if not session:
            raise ValueError("Session not found")
        session.status = "ended"
        session.ended_at = datetime.utcnow()
        db.commit()
        return {"status": "ended", "session_id": session_id}

    def reassign_expired_sessions(self, db: Session) -> dict:
        now = datetime.utcnow()
        expired = db.scalars(
            select(ConsultationSession).where(
                ConsultationSession.status == "pending",
                ConsultationSession.expires_at <= now,
            )
        ).all()
        moved = 0
        for session in expired:
            requested_languages = self._split_languages(session.language) or ["English"]
            candidates = db.scalars(
                select(Student).where(Student.student_id != session.student_id)
            ).all()
            pool = []
            for student in candidates:
                student_languages = {x.lower() for x in self._split_languages(student.language_preference)}
                if not student_languages:
                    continue
                if any(req.lower() in student_languages for req in requested_languages):
                    pool.append(student)
            replacement = random.choice(pool) if pool else None
            if replacement:
                session.student_id = replacement.student_id
                session.expires_at = now + timedelta(minutes=10)
                moved += 1
        db.commit()
        return {"reassigned": moved}

    def save_chat_message(self, db: Session, session_id: str, sender_role: str, sender_id: str, message_text: str) -> dict:
        session = db.scalar(select(ConsultationSession).where(ConsultationSession.session_id == session_id))
        if not session:
            raise ValueError("Session not found")
        summary = ai_summary = "No summary"
        recent = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(5)
        ).all()
        convo = " ".join([m.message_text for m in reversed(recent)] + [message_text])
        ai = ai_service.assistant_reply(f"Summarize this chat briefly: {convo}")
        ai_summary = "; ".join(ai.get("guidance", []))[:400]
        msg = ChatMessage(
            session_id=session_id,
            sender_role=sender_role,
            sender_id=sender_id,
            message_text=message_text,
            ai_summary=ai_summary,
        )
        db.add(msg)
        db.commit()
        return {"saved": True, "session_id": session_id, "ai_summary": ai_summary}

    def session_recall(self, db: Session, session_id: str) -> dict:
        messages = db.scalars(
            select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc())
        ).all()
        if not messages:
            return {"session_id": session_id, "last_chat_summary": "No chat yet", "video_recall": "No session recall yet"}
        return {
            "session_id": session_id,
            "last_chat_summary": messages[-1].ai_summary,
            "video_recall": "Consultation was completed with notes captured in visit history.",
        }

    def health_in_my_area(self, db: Session, patient_id: str, days: int = 30) -> dict:
        patient = db.scalar(select(Patient).where(Patient.patient_id == patient_id))
        if not patient:
            raise ValueError("Patient not found")
        since = datetime.utcnow() - timedelta(days=days)
        local_rows = (
            db.query(Diagnosis.disease_category, func.count(Diagnosis.id))
            .join(Patient, Patient.uhid == Diagnosis.uhid)
            .filter(Patient.state == patient.state, Patient.district == patient.district, Diagnosis.created_at >= since)
            .group_by(Diagnosis.disease_category)
            .all()
        )
        state_rows = (
            db.query(Diagnosis.disease_category, func.count(Diagnosis.id))
            .join(Patient, Patient.uhid == Diagnosis.uhid)
            .filter(Patient.state == patient.state, Diagnosis.created_at >= since)
            .group_by(Diagnosis.disease_category)
            .all()
        )
        india_rows = db.query(Diagnosis.disease_category, func.count(Diagnosis.id)).filter(Diagnosis.created_at >= since).group_by(Diagnosis.disease_category).all()
        return {
            "district": patient.district,
            "state": patient.state,
            "most_common_diseases_local": [{"disease_category": d, "count": c} for d, c in local_rows],
            "comparison": {
                "district": [{"disease_category": d, "count": c} for d, c in local_rows],
                "state": [{"disease_category": d, "count": c} for d, c in state_rows],
                "india": [{"disease_category": d, "count": c} for d, c in india_rows],
            },
            "time_range_days": days,
        }

    def gov_monitoring_dashboard(
        self,
        db: Session,
        disease_category: int | None = None,
        days: int = 30,
        gender: str | None = None,
        state: str | None = None,
    ) -> dict:
        since = datetime.utcnow() - timedelta(days=days)
        query = db.query(
            Patient.state,
            Patient.district,
            Diagnosis.disease_category,
            func.count(Diagnosis.id),
        ).join(Patient, Patient.uhid == Diagnosis.uhid).filter(Diagnosis.created_at >= since)
        if disease_category:
            query = query.filter(Diagnosis.disease_category == disease_category)
        if state:
            query = query.filter(Patient.state == state)
        rows = query.group_by(Patient.state, Patient.district, Diagnosis.disease_category).all()

        by_state: dict[str, int] = defaultdict(int)
        for st, _dist, _cat, cnt in rows:
            by_state[st] += int(cnt)

        totals = db.query(func.count(Patient.id)).scalar() or 0
        male = db.query(func.count(Patient.id)).filter(func.lower(Patient.full_name).like("%")).scalar() or 0
        female = totals - male
        return {
            "india_map": [{"state": st, "risk_score": val} for st, val in by_state.items()],
            "disease_categories": [{"category": i, "color": c} for i, c in enumerate(
                ["#3B82F6", "#22C55E", "#F59E0B", "#EF4444", "#8B5CF6", "#06B6D4", "#84CC16", "#F97316", "#EC4899", "#64748B", "#10B981", "#EAB308"],
                start=1,
            )],
            "district_spike_graph": [
                {"state": st, "district": dist, "disease_category": cat, "percentage_change": count}
                for st, dist, cat, count in rows
            ],
            "summary_stats": {
                "total_patients": totals,
                "male_percentage": round((male / totals) * 100, 2) if totals else 0,
                "female_percentage": round((female / totals) * 100, 2) if totals else 0,
                "age_groups": {"children": 0, "adults": totals, "elderly": 0},
            },
            "filters_applied": {"disease_category": disease_category, "days": days, "gender": gender, "state": state},
        }

    def generate_alerts(self, db: Session) -> dict:
        since = datetime.utcnow() - timedelta(hours=24)
        rows = (
            db.query(Patient.state, Patient.district, Diagnosis.disease_category, func.count(Diagnosis.id))
            .join(Patient, Patient.uhid == Diagnosis.uhid)
            .filter(Diagnosis.created_at >= since)
            .group_by(Patient.state, Patient.district, Diagnosis.disease_category)
            .all()
        )
        inserted = 0
        for state, district, category, count in rows:
            if count >= 3:
                severity = "high" if count >= 10 else "medium"
                alert = Alert(state=state, district=district, disease_category=category, severity=severity)
                db.add(alert)
                inserted += 1
        db.commit()
        return {"alerts_generated": inserted}

    def fetch_alerts(self, db: Session, state: str, district: str) -> list[dict]:
        alerts = db.scalars(
            select(Alert)
            .where(Alert.state == state, Alert.district == district)
            .order_by(Alert.detected_at.desc())
        ).all()
        return [
            {
                "state": a.state,
                "district": a.district,
                "disease_category": a.disease_category,
                "severity": a.severity,
                "detected_at": a.detected_at.isoformat(),
            }
            for a in alerts
        ]

    def state_summary(self, db: Session) -> dict:
        rows = db.query(Patient.state, func.count(Patient.id)).group_by(Patient.state).all()
        return {"top_states": [{"state": s, "patients": c} for s, c in rows], "heatmap": [{"state": s, "value": c} for s, c in rows]}

    def district_summary(self, db: Session) -> dict:
        district_rows = db.query(Patient.district, func.count(Patient.id)).group_by(Patient.district).all()
        doctor_rows = db.query(Visit.doctor_id, func.count(Visit.id)).filter(Visit.doctor_id.is_not(None)).group_by(Visit.doctor_id).all()
        return {
            "district_panel": [{"district": d, "patients": c} for d, c in district_rows],
            "doctor_metrics": [{"doctor_id": did, "consultations": c} for did, c in doctor_rows],
        }


relational_service = RelationalService()

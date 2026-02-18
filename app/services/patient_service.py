from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime

from app.models.schemas import NotificationItem, PatientDashboardResponse, PatientSummary, VisitRecord
from app.services.otp_service import otp_service
from app.services.storage.mock_storage import mock_storage


class PatientService:
    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def _age_from_dob(self, dob_str: str) -> int:
        dob = date.fromisoformat(dob_str)
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    def register_patient(self, payload: dict) -> dict:
        patient_id = f"PAT-{uuid.uuid4().hex[:8]}"
        uhid = f"UHID-{uuid.uuid4().hex[:10]}"
        patient = {
            "patient_id": patient_id,
            "uhid": uhid,
            "aadhaar_hash": hashlib.sha256(payload["aadhaar"].encode("utf-8")).hexdigest(),
            "phone": payload["phone"],
            "full_name": payload["full_name"],
            "age": payload["age"],
            "gender": payload["gender"],
            "blood_group": payload["blood_group"],
            "allergies": payload["allergies"],
            "chronic_conditions": payload["chronic_conditions"],
            "address": payload["address"],
            "latitude": payload["latitude"],
            "longitude": payload["longitude"],
            "health_qr_payload": f"ENC:{uhid}:{uuid.uuid4().hex}",
            "created_at": datetime.utcnow().isoformat(),
        }
        mock_storage.save_patient(patient)
        return patient

    def register_patient_v2(self, payload: dict) -> dict:
        if payload["password"] != payload["confirm_password"]:
            raise ValueError("Password mismatch")
        if mock_storage.get_patient_by_phone(payload["phone"]):
            raise ValueError("Phone already registered")
        if mock_storage.get_patient_by_email(payload["email"]):
            raise ValueError("Email already registered")

        patient_id = f"PAT-{uuid.uuid4().hex[:8]}"
        uhid = f"UHID-{uuid.uuid4().hex[:10]}"
        qr_token = f"QR::{uhid}::{uuid.uuid4().hex[:12]}"
        patient = {
            "patient_id": patient_id,
            "uhid": uhid,
            "aadhaar_hash": payload["hashed_aadhaar"],
            "dob": payload["dob"],
            "email": payload["email"],
            "phone": payload["phone"],
            "language": payload["language"],
            "full_name": payload["full_name"],
            "age": self._age_from_dob(payload["dob"]),
            "gender": payload.get("gender", "unknown"),
            "blood_group": payload["blood_group"],
            "allergies": payload.get("allergies", []),
            "chronic_conditions": payload.get("chronic_conditions", []),
            "address": payload["address"],
            "live_location_enabled": payload.get("live_location_enabled", False),
            "latitude": payload.get("latitude", 0.0),
            "longitude": payload.get("longitude", 0.0),
            "password_hash": self._hash_password(payload["password"]),
            "health_qr_payload": qr_token,
            "created_at": datetime.utcnow().isoformat(),
        }
        mock_storage.save_patient(patient)
        return {"patient_id": patient_id, "uhid": uhid, "qr_token": qr_token, "success": True}

    def login_patient_v2(self, full_name: str | None, phone: str | None, password: str) -> dict:
        patient = None
        if full_name:
            patient = mock_storage.get_patient_by_full_name(full_name)
        if not patient and phone:
            patient = mock_storage.get_patient_by_phone(phone)
        if not patient:
            raise ValueError("Patient not found")
        if patient.get("password_hash") != self._hash_password(password):
            raise ValueError("Invalid password")
        return {"patient_id": patient["patient_id"], "uhid": patient["uhid"], "success": True}

    def patient_profile_v2(self, patient_id: str) -> dict:
        patient = mock_storage.get_patient(patient_id)
        if not patient:
            raise ValueError("Patient not found")
        return {
            "name": patient["full_name"],
            "uhid": patient["uhid"],
            "blood_group": patient["blood_group"],
            "age": patient["age"],
        }

    def create_password_reset_otp(self, identifier: str) -> dict:
        patient = mock_storage.get_patient_by_phone(identifier) or mock_storage.get_patient_by_email(identifier)
        if not patient:
            raise ValueError("Patient not found")
        otp, expires_at = otp_service.issue("patient_reset", patient["patient_id"])
        return {"status": "otp_sent", "expires_at": expires_at, "otp_hint": otp}

    def reset_password_with_otp(self, identifier: str, otp: str, new_password: str) -> dict:
        patient = mock_storage.get_patient_by_phone(identifier) or mock_storage.get_patient_by_email(identifier)
        if not patient:
            raise ValueError("Patient not found")
        ok = otp_service.verify("patient_reset", patient["patient_id"], otp)
        if not ok:
            raise ValueError("Invalid OTP")
        patient["password_hash"] = self._hash_password(new_password)
        return {"status": "password_reset_success"}

    def get_patient_dashboard(self, patient_id: str) -> PatientDashboardResponse:
        patient = mock_storage.get_patient(patient_id)
        if not patient:
            raise ValueError("Patient not found")
        return PatientDashboardResponse(
            patient_id=patient["patient_id"],
            uhid=patient["uhid"],
            full_name=patient["full_name"],
            age=patient["age"],
            blood_group=patient["blood_group"],
            allergies=patient["allergies"],
            chronic_conditions=patient["chronic_conditions"],
        )

    def get_health_qr(self, uhid: str) -> dict:
        patient = mock_storage.get_patient_by_uhid(uhid)
        if not patient:
            raise ValueError("Patient not found")
        return {
            "uhid": patient["uhid"],
            "health_qr_payload": patient["health_qr_payload"],
        }

    def get_summary(self, uhid: str) -> PatientSummary:
        patient = mock_storage.get_patient_by_uhid(uhid)
        if not patient:
            raise ValueError("Patient not found")
        visits = mock_storage.get_reports(uhid)[-5:]
        parsed_visits = [VisitRecord(**visit) for visit in visits]
        return PatientSummary(
            uhid=patient["uhid"],
            age=patient["age"],
            blood_group=patient["blood_group"],
            allergies=patient["allergies"],
            chronic_conditions=patient["chronic_conditions"],
            last_5_visits=parsed_visits,
        )

    def get_reports(self, uhid: str) -> list[VisitRecord]:
        return [VisitRecord(**r) for r in mock_storage.get_reports(uhid)]

    def get_timeline(self, uhid: str) -> list[VisitRecord]:
        reports = sorted(mock_storage.get_reports(uhid), key=lambda item: item["created_at"])
        return [VisitRecord(**r) for r in reports]

    def get_notifications(self, patient_id: str) -> list[NotificationItem]:
        notifications = mock_storage.get_notifications(role="patient", user_id=patient_id)
        patient = mock_storage.get_patient(patient_id)
        if patient:
            for suggestion in self._lifestyle_suggestions(patient):
                notifications.append(
                    {
                        "message": suggestion,
                        "created_at": datetime.utcnow(),
                        "category": "lifestyle",
                    }
                )
        return [NotificationItem(**item) for item in notifications]

    def _lifestyle_suggestions(self, patient: dict) -> list[str]:
        advice: list[str] = []
        chronic = [item.lower() for item in patient.get("chronic_conditions", [])]
        if "diabetes" in chronic:
            advice.append("Use low glycemic meals and 30 minutes of daily walking.")
        if "hypertension" in chronic:
            advice.append("Limit salt and practice controlled breathing exercises.")
        if "asthma" in chronic:
            advice.append("Avoid smoke exposure and continue prescribed breathing routines.")
        if not advice:
            advice.append("Maintain balanced nutrition, hydration, and regular exercise.")
        return advice


patient_service = PatientService()

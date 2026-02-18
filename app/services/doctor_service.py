from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.schemas import DoctorDashboardResponse, DoctorStatsResponse, PatientSummary, StructuredClinicalOutput, VisitRecord
from app.services.storage.mock_storage import mock_storage


class DoctorService:
    def __init__(self) -> None:
        self.active_uhid_by_doctor: dict[str, str] = {}

    def get_dashboard(self, doctor_id: str) -> DoctorDashboardResponse:
        doctor = mock_storage.get_doctor_by_id(doctor_id)
        if not doctor:
            raise ValueError("Doctor not found")
        stats = self._build_stats(doctor_id)
        active_uhid = self.active_uhid_by_doctor.get(doctor_id)
        active_patient = self._build_patient_summary(active_uhid) if active_uhid else None
        return DoctorDashboardResponse(
            doctor_id=doctor_id,
            waiting_for_patient=active_patient is None,
            stats=stats,
            active_patient=active_patient,
        )

    def get_stats(self, doctor_id: str) -> DoctorStatsResponse:
        doctor = mock_storage.get_doctor_by_id(doctor_id)
        if not doctor:
            raise ValueError("Doctor not found")
        return self._build_stats(doctor_id)

    def patient_summary_from_qr(self, uhid: str) -> dict:
        patient = mock_storage.get_patient_by_uhid(uhid)
        if not patient:
            raise ValueError("Patient not found")

        visits = mock_storage.get_reports(uhid)[-5:]
        return {
            "uhid": patient["uhid"],
            "age": patient["age"],
            "blood_group": patient["blood_group"],
            "allergies": patient["allergies"],
            "chronic_conditions": patient["chronic_conditions"],
            "last_5_visits": visits,
        }

    def assign_patient_from_scan(self, doctor_id: str, uhid: str) -> dict:
        if not mock_storage.get_doctor_by_id(doctor_id):
            raise ValueError("Doctor not found")
        summary = self.patient_summary_from_qr(uhid)
        self.active_uhid_by_doctor[doctor_id] = uhid
        return summary

    def save_approved_report(
        self, doctor_id: str, uhid: str, structured: StructuredClinicalOutput, doctor_notes: str | None
    ) -> VisitRecord:
        doctor = mock_storage.get_doctor_by_id(doctor_id)
        if not doctor:
            raise ValueError("Doctor not found")
        patient = mock_storage.get_patient_by_uhid(uhid)
        if not patient:
            raise ValueError("Patient not found")

        visit = {
            "visit_id": f"VIS-{uuid.uuid4().hex[:10]}",
            "uhid": uhid,
            "doctor_id": doctor_id,
            "symptoms": structured.symptoms,
            "diagnosis": structured.diagnosis,
            "treatment": "; ".join(structured.advice),
            "notes": doctor_notes or "",
            "created_at": datetime.utcnow(),
        }
        mock_storage.save_visit(visit)
        mock_storage.save_notification(
            role="patient",
            user_id=patient["patient_id"],
            item={
                "message": "New doctor report has been approved and saved.",
                "created_at": datetime.utcnow(),
                "category": "report",
            },
        )
        return VisitRecord(**visit)

    def edit_report(self, doctor_id: str, visit_id: str, structured: StructuredClinicalOutput, doctor_notes: str | None) -> VisitRecord:
        visit = mock_storage.get_visit(visit_id)
        if not visit:
            raise ValueError("Visit not found")
        if visit["doctor_id"] != doctor_id:
            raise ValueError("Only the report owner can edit this visit")

        created_at = visit["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        now_utc = datetime.now(timezone.utc)
        created_at_utc = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at.astimezone(timezone.utc)
        if created_at_utc.date() != now_utc.date():
            raise ValueError("Only same-day reports can be edited")

        patch = {
            "symptoms": structured.symptoms,
            "diagnosis": structured.diagnosis,
            "treatment": "; ".join(structured.advice),
            "notes": doctor_notes or "",
            "updated_at": datetime.utcnow(),
        }
        mock_storage.update_visit(visit_id, patch)
        updated = mock_storage.get_visit(visit_id)
        if not updated:
            raise ValueError("Visit update failed")
        return VisitRecord(**updated)

    def _build_stats(self, doctor_id: str) -> DoctorStatsResponse:
        visits = mock_storage.get_doctor_visits(doctor_id)
        now = datetime.now(timezone.utc)
        day_start = now - timedelta(days=1)
        week_start = now - timedelta(days=7)
        month_start = now - timedelta(days=30)

        def in_window(item: dict, start: datetime) -> bool:
            created_at = item["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            else:
                created_at = created_at.astimezone(timezone.utc)
            return created_at >= start

        return DoctorStatsResponse(
            patients_today=sum(1 for v in visits if in_window(v, day_start)),
            patients_this_week=sum(1 for v in visits if in_window(v, week_start)),
            patients_this_month=sum(1 for v in visits if in_window(v, month_start)),
        )

    def _build_patient_summary(self, uhid: str) -> PatientSummary:
        patient = mock_storage.get_patient_by_uhid(uhid)
        if not patient:
            raise ValueError("Patient not found")
        visits = [VisitRecord(**item) for item in mock_storage.get_reports(uhid)[-5:]]
        return PatientSummary(
            uhid=patient["uhid"],
            age=patient["age"],
            blood_group=patient["blood_group"],
            allergies=patient["allergies"],
            chronic_conditions=patient["chronic_conditions"],
            last_5_visits=visits,
        )


doctor_service = DoctorService()

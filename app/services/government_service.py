from __future__ import annotations

from collections import Counter
from datetime import datetime

from app.services.storage.mock_storage import mock_storage


class GovernmentService:
    def _all_visits(self) -> list[dict]:
        visits: list[dict] = []
        for items in mock_storage.visits_by_uhid.values():
            visits.extend(items)
        return visits

    def _state_from_patient(self, patient: dict) -> str:
        address = patient.get("address", "")
        if "," in address:
            return address.split(",")[-1].strip() or "Unknown"
        return "Unknown"

    def _district_from_patient(self, patient: dict) -> str:
        address = patient.get("address", "")
        if "," in address:
            parts = [p.strip() for p in address.split(",")]
            if len(parts) >= 2:
                return parts[-2] or "Unknown"
        return "Unknown"

    def state_summary(self) -> dict:
        counts = Counter()
        for patient in mock_storage.patients.values():
            counts[self._state_from_patient(patient)] += 1
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "top_states": [{"state": k, "patients": v} for k, v in counts.most_common(10)],
            "heatmap": [{"state": k, "value": v} for k, v in counts.items()],
        }

    def district_summary(self) -> dict:
        district_counts = Counter()
        doctor_counts = Counter()
        for patient in mock_storage.patients.values():
            district_counts[self._district_from_patient(patient)] += 1
        for visit in self._all_visits():
            doctor_counts[visit["doctor_id"]] += 1
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "district_panel": [{"district": k, "patients": v} for k, v in district_counts.items()],
            "doctor_metrics": [{"doctor_id": k, "consultations": v} for k, v in doctor_counts.items()],
        }


government_service = GovernmentService()

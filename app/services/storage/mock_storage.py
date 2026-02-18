from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Optional

from app.services.storage.base import StorageGateway


class MockStorageGateway(StorageGateway):
    def __init__(self) -> None:
        self.patients: dict[str, dict[str, Any]] = {}
        self.patients_by_phone: dict[str, str] = {}
        self.patients_by_email: dict[str, str] = {}
        self.patients_by_full_name: dict[str, str] = {}
        self.patients_by_uhid: dict[str, str] = {}
        self.doctors_by_username: dict[str, dict[str, Any]] = {}
        self.doctors_by_id: dict[str, dict[str, Any]] = {}
        self.students_by_email: dict[str, dict[str, Any]] = {}
        self.students_by_id: dict[str, dict[str, Any]] = {}
        self.visits_by_uhid: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.visits_by_id: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.online_students_by_language: dict[str, set[str]] = defaultdict(set)
        self.notifications: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.otp_store: dict[str, dict[str, str]] = defaultdict(dict)

        self.valid_doctor_license_ids = {"GOV-AYUSH-1001", "GOV-AYUSH-1002"}
        self.valid_college_ids = {"COLL-5001", "COLL-5002"}
        self.verified_students_registry = {
            ("COLL-5001", "koushik.chinu.2007@gmail.com"),
            ("COLL-5002", "koushik.chinu.2007@gmail.com"),
        }

    def get_patient(self, patient_id: str) -> Optional[dict[str, Any]]:
        return self.patients.get(patient_id)

    def get_patient_by_phone(self, phone: str) -> Optional[dict[str, Any]]:
        patient_id = self.patients_by_phone.get(phone)
        if not patient_id:
            return None
        return self.patients.get(patient_id)

    def get_patient_by_full_name(self, full_name: str) -> Optional[dict[str, Any]]:
        patient_id = self.patients_by_full_name.get(full_name.lower().strip())
        if not patient_id:
            return None
        return self.patients.get(patient_id)

    def get_patient_by_email(self, email: str) -> Optional[dict[str, Any]]:
        patient_id = self.patients_by_email.get(email.lower().strip())
        if not patient_id:
            return None
        return self.patients.get(patient_id)

    def save_patient(self, patient: dict[str, Any]) -> None:
        self.patients[patient["patient_id"]] = patient
        self.patients_by_phone[patient["phone"]] = patient["patient_id"]
        if patient.get("email"):
            self.patients_by_email[patient["email"].lower().strip()] = patient["patient_id"]
        self.patients_by_full_name[patient["full_name"].lower().strip()] = patient["patient_id"]
        self.patients_by_uhid[patient["uhid"]] = patient["patient_id"]

    def get_patient_by_uhid(self, uhid: str) -> Optional[dict[str, Any]]:
        patient_id = self.patients_by_uhid.get(uhid)
        if not patient_id:
            return None
        return self.patients.get(patient_id)

    def save_visit(self, visit: dict[str, Any]) -> None:
        self.visits_by_uhid[visit["uhid"]].append(visit)
        self.visits_by_id[visit["visit_id"]] = visit

    def get_reports(self, uhid: str) -> list[dict[str, Any]]:
        return list(self.visits_by_uhid.get(uhid, []))

    def save_doctor(self, doctor: dict[str, Any]) -> None:
        self.doctors_by_username[doctor["username"]] = doctor
        self.doctors_by_id[doctor["doctor_id"]] = doctor

    def get_doctor_by_username(self, username: str) -> Optional[dict[str, Any]]:
        return self.doctors_by_username.get(username)

    def get_doctor_by_id(self, doctor_id: str) -> Optional[dict[str, Any]]:
        return self.doctors_by_id.get(doctor_id)

    def save_student(self, student: dict[str, Any]) -> None:
        self.students_by_email[student["institutional_email"]] = student
        self.students_by_id[student["student_id"]] = student

    def get_student_by_email(self, email: str) -> Optional[dict[str, Any]]:
        return self.students_by_email.get(email)

    def get_student_by_id(self, student_id: str) -> Optional[dict[str, Any]]:
        return self.students_by_id.get(student_id)

    def set_student_online(self, student_id: str, language: str) -> None:
        self.online_students_by_language[language].add(student_id)

    def random_online_student(self, language: str) -> Optional[dict[str, Any]]:
        candidates = list(self.online_students_by_language.get(language, []))
        if not candidates:
            return None
        chosen_id = random.choice(candidates)
        for student in self.students_by_email.values():
            if student["student_id"] == chosen_id:
                return student
        return None

    def save_session(self, session: dict[str, Any]) -> None:
        self.sessions[session["session_id"]] = session

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        return self.sessions.get(session_id)

    def update_session(self, session_id: str, patch: dict[str, Any]) -> None:
        if session_id in self.sessions:
            self.sessions[session_id].update(patch)

    def get_student_sessions(self, student_id: str) -> list[dict[str, Any]]:
        return [s for s in self.sessions.values() if s["student_id"] == student_id]

    def get_patient_sessions(self, patient_id: str) -> list[dict[str, Any]]:
        return [s for s in self.sessions.values() if s["patient_id"] == patient_id]

    def get_doctor_visits(self, doctor_id: str) -> list[dict[str, Any]]:
        visits: list[dict[str, Any]] = []
        for per_patient in self.visits_by_uhid.values():
            visits.extend([v for v in per_patient if v["doctor_id"] == doctor_id])
        return visits

    def get_visit(self, visit_id: str) -> Optional[dict[str, Any]]:
        return self.visits_by_id.get(visit_id)

    def update_visit(self, visit_id: str, patch: dict[str, Any]) -> None:
        if visit_id in self.visits_by_id:
            self.visits_by_id[visit_id].update(patch)

    def save_notification(self, role: str, user_id: str, item: dict[str, Any]) -> None:
        key = f"{role}:{user_id}"
        self.notifications[key].append(item)

    def get_notifications(self, role: str, user_id: str) -> list[dict[str, Any]]:
        key = f"{role}:{user_id}"
        return list(self.notifications.get(key, []))

    def set_otp(self, namespace: str, key: str, otp: str) -> None:
        self.otp_store[namespace][key] = otp

    def get_otp(self, namespace: str, key: str) -> Optional[str]:
        return self.otp_store.get(namespace, {}).get(key)

    def clear_otp(self, namespace: str, key: str) -> None:
        if namespace in self.otp_store and key in self.otp_store[namespace]:
            del self.otp_store[namespace][key]


mock_storage = MockStorageGateway()

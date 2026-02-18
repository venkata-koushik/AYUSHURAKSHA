from __future__ import annotations

import uuid
from datetime import datetime
import hashlib

from app.models.schemas import StudentDashboardResponse, StudentPastSessionSummary, StudentSessionResponse
from app.services.otp_service import otp_service
from app.services.storage.mock_storage import mock_storage


class StudentService:
    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def verify_college(self, college_id: str, official_email: str) -> dict:
        key = (college_id, official_email.lower().strip())
        if key not in mock_storage.verified_students_registry:
            raise ValueError("College ID and official email not in verified registry")
        otp, expires_at = otp_service.issue("student_verify", college_id.strip().upper())
        return {"status": "otp_sent", "expires_at": expires_at, "otp_hint": otp}

    def confirm_college_otp(self, college_id: str, otp: str) -> dict:
        if not otp_service.verify("student_verify", college_id.strip().upper(), otp):
            raise ValueError("Invalid OTP")
        mock_storage.set_otp(namespace="student_confirmed", key=college_id, otp="ok")
        return {"status": "otp_confirmed"}

    def register_verified_student(self, college_id: str, official_email: str, password: str) -> dict:
        confirmed = mock_storage.get_otp(namespace="student_confirmed", key=college_id)
        if confirmed != "ok":
            raise ValueError("College verification not completed")
        if mock_storage.get_student_by_email(official_email):
            raise ValueError("Email already registered")
        student = {
            "student_id": f"STU-{uuid.uuid4().hex[:8]}",
            "college_id": college_id,
            "institutional_email": official_email.lower().strip(),
            "password_hash": self._hash_password(password),
            "language": None,
            "rating_avg": 0.0,
            "rating_count": 0,
            "online": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        mock_storage.save_student(student)
        mock_storage.clear_otp(namespace="student_confirmed", key=college_id)
        return {"status": "registration_success", "student_id": student["student_id"]}

    def login_v2(self, official_email: str, password: str) -> dict:
        student = mock_storage.get_student_by_email(official_email.lower().strip())
        if not student:
            raise ValueError("Student not found")
        if student["password_hash"] != self._hash_password(password):
            raise ValueError("Invalid password")
        token = f"mock-student-jwt-{student['student_id']}-{uuid.uuid4().hex[:8]}"
        return {"student_id": student["student_id"], "token": token}

    def go_online(self, student_id: str, language: str) -> None:
        mock_storage.set_student_online(student_id=student_id, language=language)
        for student in mock_storage.students_by_email.values():
            if student["student_id"] == student_id:
                student["language"] = language
                student["online"] = True
                mock_storage.save_notification(
                    role="student",
                    user_id=student_id,
                    item={
                        "message": f"You are online for {language} consultations.",
                        "created_at": datetime.utcnow(),
                        "category": "availability",
                    },
                )
                break

    def request_consultation(self, patient_id: str, language: str, problem: str = "General consultation") -> dict:
        student = mock_storage.random_online_student(language)
        if not student:
            raise ValueError("No student available in selected language")
        patient = mock_storage.get_patient(patient_id)
        if not patient:
            raise ValueError("Patient not found")

        session = {
            "session_id": f"SES-{uuid.uuid4().hex[:10]}",
            "patient_id": patient_id,
            "patient_name": patient["full_name"],
            "student_id": student["student_id"],
            "language": language,
            "status": "pending",
            "problem": problem,
            "messages": [],
            "created_at": datetime.utcnow().isoformat(),
            "rating": None,
            "feedback": "",
        }
        mock_storage.save_session(session)
        mock_storage.save_notification(
            role="student",
            user_id=student["student_id"],
            item={
                "message": f"New consultation request in {language}.",
                "created_at": datetime.utcnow(),
                "category": "session",
            },
        )
        return session

    def decide_session(self, session_id: str, accepted: bool) -> dict:
        session = mock_storage.get_session(session_id)
        if not session:
            raise ValueError("Session not found")
        status = "active" if accepted else "rejected"
        mock_storage.update_session(session_id, {"status": status})
        updated = mock_storage.get_session(session_id)
        if not updated:
            raise ValueError("Session update failed")
        mock_storage.save_notification(
            role="patient",
            user_id=session["patient_id"],
            item={
                "message": f"Student has {status} your consultation request.",
                "created_at": datetime.utcnow(),
                "category": "session",
            },
        )
        return updated

    def end_session(self, session_id: str) -> dict:
        session = mock_storage.get_session(session_id)
        if not session:
            raise ValueError("Session not found")
        mock_storage.update_session(session_id, {"status": "completed", "ended_at": datetime.utcnow().isoformat()})
        updated = mock_storage.get_session(session_id)
        if not updated:
            raise ValueError("Session update failed")
        mock_storage.save_notification(
            role="patient",
            user_id=session["patient_id"],
            item={
                "message": "Consultation session ended. Please submit your rating.",
                "created_at": datetime.utcnow(),
                "category": "session",
            },
        )
        return updated

    def submit_rating(self, session_id: str, rating: int, feedback: str) -> dict:
        session = mock_storage.get_session(session_id)
        if not session:
            raise ValueError("Session not found")

        mock_storage.update_session(session_id, {"rating": rating, "feedback": feedback})
        student_id = session["student_id"]
        for student in mock_storage.students_by_email.values():
            if student["student_id"] == student_id:
                old_count = student["rating_count"]
                old_avg = student["rating_avg"]
                new_avg = ((old_avg * old_count) + rating) / (old_count + 1)
                student["rating_count"] = old_count + 1
                student["rating_avg"] = round(new_avg, 2)
                return {"student_id": student_id, "rating_avg": student["rating_avg"], "rating_count": student["rating_count"]}
        raise ValueError("Student not found")

    def get_dashboard(self, student_id: str) -> StudentDashboardResponse:
        student = mock_storage.get_student_by_id(student_id)
        if not student:
            raise ValueError("Student not found")
        return StudentDashboardResponse(
            student_id=student_id,
            online=bool(student.get("online", False)),
            language=student.get("language"),
            rating_avg=student.get("rating_avg", 0.0),
            rating_count=student.get("rating_count", 0),
        )

    def get_incoming_requests(self, student_id: str) -> list[StudentSessionResponse]:
        sessions = [
            s
            for s in mock_storage.get_student_sessions(student_id)
            if s["status"] == "pending"
        ]
        return [StudentSessionResponse(**session) for session in sessions]

    def get_past_sessions(self, student_id: str) -> list[StudentPastSessionSummary]:
        sessions = [
            s
            for s in mock_storage.get_student_sessions(student_id)
            if s["status"] in {"completed", "rejected"}
        ]
        return [
            StudentPastSessionSummary(
                session_id=item["session_id"],
                patient_name=item.get("patient_name", "Unknown"),
                problem=item.get("problem", "General consultation"),
                status=item["status"],
                ended_at=item.get("ended_at"),
            )
            for item in sessions
        ]

    def get_notifications(self, student_id: str) -> list[dict]:
        return mock_storage.get_notifications(role="student", user_id=student_id)


student_service = StudentService()

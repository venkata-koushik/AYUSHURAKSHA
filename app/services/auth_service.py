from __future__ import annotations

import hashlib
import uuid

from app.models.schemas import TokenResponse
from app.services.otp_service import otp_service
from app.services.storage.mock_storage import mock_storage


class AuthService:

    def _hash_password(self, password: str) -> str:
        # Upgrade path: swap to passlib[bcrypt].
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def verify_password(self, plain: str, hashed: str) -> bool:
        return self._hash_password(plain) == hashed

    def create_simulated_token(self, role: str, user_id: str) -> TokenResponse:
        # Upgrade path: swap to JWT with role claims.
        token = f"mock-{role}-{user_id}-{uuid.uuid4().hex[:8]}"
        return TokenResponse(access_token=token, role=role, user_id=user_id)

    def verify_otp(self, identifier: str, otp: str) -> bool:
        return otp_service.verify("patient_login", identifier.strip().lower(), otp)

    def register_doctor(
        self, government_license_id: str, full_name: str, age: int, username: str, password: str
    ) -> dict:
        if government_license_id not in mock_storage.valid_doctor_license_ids:
            raise ValueError("Invalid government license ID")
        if mock_storage.get_doctor_by_username(username):
            raise ValueError("Username already exists")

        doctor = {
            "doctor_id": f"DOC-{uuid.uuid4().hex[:8]}",
            "government_license_id": government_license_id,
            "full_name": full_name,
            "age": age,
            "username": username,
            "password_hash": self._hash_password(password),
        }
        mock_storage.save_doctor(doctor)
        return doctor

    def register_student(self, college_id: str, email: str, password: str) -> dict:
        if college_id not in mock_storage.valid_college_ids:
            raise ValueError("Invalid college ID")
        if mock_storage.get_student_by_email(email):
            raise ValueError("Email already exists")

        student = {
            "student_id": f"STU-{uuid.uuid4().hex[:8]}",
            "college_id": college_id,
            "institutional_email": email,
            "password_hash": self._hash_password(password),
            "language": None,
            "rating_avg": 0.0,
            "rating_count": 0,
        }
        mock_storage.save_student(student)
        return student

    def doctor_login(self, username: str, password: str) -> TokenResponse:
        doctor = mock_storage.get_doctor_by_username(username)
        if not doctor or not self.verify_password(password, doctor["password_hash"]):
            raise ValueError("Invalid credentials")
        return self.create_simulated_token(role="doctor", user_id=doctor["doctor_id"])

    def student_login(self, email: str, password: str) -> TokenResponse:
        student = mock_storage.get_student_by_email(email)
        if not student or not self.verify_password(password, student["password_hash"]):
            raise ValueError("Invalid credentials")
        return self.create_simulated_token(role="student", user_id=student["student_id"])

    def patient_login_with_phone(self, phone: str, otp: str) -> TokenResponse:
        patient = mock_storage.get_patient_by_phone(phone)
        if not patient:
            raise ValueError("Invalid credentials")
        if not self.verify_otp(phone, otp):
            raise ValueError("Invalid OTP")
        return self.create_simulated_token(role="patient", user_id=patient["patient_id"])


auth_service = AuthService()

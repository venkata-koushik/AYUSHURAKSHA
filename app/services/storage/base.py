from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class StorageGateway(ABC):
    """
    Contract for persistence access.
    Swap this implementation from in-memory to SQL without changing route/service logic.
    """

    @abstractmethod
    def get_patient(self, patient_id: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_patient_by_phone(self, phone: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_patient_by_full_name(self, full_name: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_patient_by_email(self, email: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def save_patient(self, patient: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_patient_by_uhid(self, uhid: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def save_visit(self, visit: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_reports(self, uhid: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def save_doctor(self, doctor: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_doctor_by_username(self, username: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_doctor_by_id(self, doctor_id: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def save_student(self, student: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_student_by_email(self, email: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_student_by_id(self, student_id: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def set_student_online(self, student_id: str, language: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def random_online_student(self, language: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def save_session(self, session: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def update_session(self, session_id: str, patch: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_student_sessions(self, student_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_patient_sessions(self, patient_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_doctor_visits(self, doctor_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_visit(self, visit_id: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def update_visit(self, visit_id: str, patch: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_notification(self, role: str, user_id: str, item: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_notifications(self, role: str, user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def set_otp(self, namespace: str, key: str, otp: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_otp(self, namespace: str, key: str) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def clear_otp(self, namespace: str, key: str) -> None:
        raise NotImplementedError

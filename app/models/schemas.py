from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


Role = Literal["patient", "doctor", "student"]
SessionStatus = Literal["pending", "active", "rejected", "completed"]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    user_id: str


class LoginRequest(BaseModel):
    role: Role
    username: str
    password: str


class PatientPhoneLoginRequest(BaseModel):
    phone: str
    otp: str


class PatientRegisterRequest(BaseModel):
    aadhaar: str = Field(min_length=12, max_length=12)
    phone: str
    otp: str
    full_name: str
    age: int
    gender: str
    blood_group: str
    allergies: list[str] = []
    chronic_conditions: list[str] = []
    address: str
    latitude: float
    longitude: float


class DoctorRegisterRequest(BaseModel):
    government_license_id: str
    full_name: str
    age: int
    username: str
    password: str
    confirm_password: str


class StudentRegisterRequest(BaseModel):
    college_id: str
    institutional_email: EmailStr
    otp: str
    password: str


class ProfileResponse(BaseModel):
    user_id: str
    role: Role
    display_name: str


class VisitRecord(BaseModel):
    visit_id: str
    uhid: str
    doctor_id: str
    symptoms: list[str] = []
    diagnosis: list[str] = []
    treatment: str = ""
    notes: str = ""
    created_at: datetime


class ProcessVoiceRequest(BaseModel):
    manual_text: str


class StructuredClinicalOutput(BaseModel):
    raw_text: str
    symptoms: list[str]
    diagnosis: list[str]
    advice: list[str]


class PatientSummary(BaseModel):
    uhid: str
    age: int
    blood_group: str
    allergies: list[str]
    chronic_conditions: list[str]
    last_5_visits: list[VisitRecord]


class SaveReportRequest(BaseModel):
    uhid: str
    structured_report: StructuredClinicalOutput
    doctor_notes: Optional[str] = None


class StudentLanguageRequest(BaseModel):
    language: str


class ConsultationRequest(BaseModel):
    patient_id: str
    language: str
    problem: str = "General consultation"


class SessionDecisionRequest(BaseModel):
    session_id: str
    accepted: bool


class RatingRequest(BaseModel):
    session_id: str
    rating: int = Field(ge=1, le=5)
    feedback: str = ""


class NotificationItem(BaseModel):
    message: str
    created_at: datetime
    category: str = "general"
    visit_id: str | None = None
    uhid: str | None = None


class PatientDashboardResponse(BaseModel):
    patient_id: str
    uhid: str
    full_name: str
    age: int
    blood_group: str
    allergies: list[str]
    chronic_conditions: list[str]


class DoctorStatsResponse(BaseModel):
    patients_today: int
    patients_this_week: int
    patients_this_month: int


class DoctorDashboardResponse(BaseModel):
    doctor_id: str
    waiting_for_patient: bool
    stats: DoctorStatsResponse
    active_patient: Optional[PatientSummary] = None


class EditReportRequest(BaseModel):
    visit_id: str
    structured_report: StructuredClinicalOutput
    doctor_notes: Optional[str] = None


class StudentSessionResponse(BaseModel):
    session_id: str
    patient_id: str
    student_id: str
    language: str
    status: SessionStatus
    created_at: str
    rating: Optional[int] = None
    feedback: str = ""


class StudentPastSessionSummary(BaseModel):
    session_id: str
    patient_name: str
    problem: str
    status: SessionStatus
    ended_at: Optional[str] = None


class StudentDashboardResponse(BaseModel):
    student_id: str
    online: bool
    language: Optional[str] = None
    rating_avg: float
    rating_count: int

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    uhid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    aadhaar_hash: Mapped[str] = mapped_column(String(128))
    dob: Mapped[datetime.date] = mapped_column(Date)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    language: Mapped[str] = mapped_column(String(32), default="English")
    address: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(64), default="Unknown")
    district: Mapped[str] = mapped_column(String(64), default="Unknown")
    blood_group: Mapped[str] = mapped_column(String(12))
    password_hash: Mapped[str] = mapped_column(String(128))
    qr_token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    qr_path: Mapped[str] = mapped_column(String(255))
    live_location_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    latitude: Mapped[float] = mapped_column(Float, default=0.0)
    longitude: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    visits: Mapped[list["Visit"]] = relationship(back_populates="patient")


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    government_license_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DoctorLicenseRegistry(Base):
    __tablename__ = "doctor_license_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    government_license_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    doctor_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    phone: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    college_id: Mapped[str] = mapped_column(String(64), index=True)
    institute_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    official_email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    language_preference: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rating_avg: Mapped[float] = mapped_column(Float, default=0.0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StudentVerification(Base):
    __tablename__ = "student_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    college_id: Mapped[str] = mapped_column(String(64), index=True)
    institute_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    official_email: Mapped[str] = mapped_column(String(128), index=True)
    otp_code: Mapped[str] = mapped_column(String(12))
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StudentRegistry(Base):
    __tablename__ = "student_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    college_id: Mapped[str] = mapped_column(String(64), index=True)
    institute_name: Mapped[str] = mapped_column(String(128), index=True)
    official_email: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Visit(Base):
    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visit_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    uhid: Mapped[str] = mapped_column(ForeignKey("patients.uhid"), index=True)
    doctor_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    student_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    consultation_type: Mapped[str] = mapped_column(String(64), default="doctor")
    notes: Mapped[str] = mapped_column(Text, default="")
    visit_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    patient: Mapped[Patient] = relationship(back_populates="visits")
    diagnoses: Mapped[list["Diagnosis"]] = relationship(back_populates="visit")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="visit")


class Diagnosis(Base):
    __tablename__ = "diagnosis"
    __table_args__ = (UniqueConstraint("diagnosis_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    diagnosis_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("visits.visit_id"), index=True)
    uhid: Mapped[str] = mapped_column(String(32), index=True)
    disease_category: Mapped[int] = mapped_column(Integer, index=True)
    disease_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    visit: Mapped[Visit] = relationship(back_populates="diagnoses")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("visits.visit_id"), index=True)
    uhid: Mapped[str] = mapped_column(String(32), index=True)
    advice_text: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    visit: Mapped[Visit] = relationship(back_populates="recommendations")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(64), index=True)
    district: Mapped[str] = mapped_column(String(64), index=True)
    disease_category: Mapped[int] = mapped_column(Integer, index=True)
    severity: Mapped[str] = mapped_column(String(32), default="medium")
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ConsultationSession(Base):
    __tablename__ = "consultation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    patient_id: Mapped[str] = mapped_column(String(32), index=True)
    student_id: Mapped[str] = mapped_column(String(32), index=True)
    language: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="video")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    problem: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rating_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    sender_role: Mapped[str] = mapped_column(String(24))
    sender_id: Mapped[str] = mapped_column(String(32))
    message_text: Mapped[str] = mapped_column(Text)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ConsultationLog(Base):
    __tablename__ = "consultation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(24), index=True)
    type: Mapped[str] = mapped_column(String(32), default="system", index=True)
    message: Mapped[str] = mapped_column(String(255))
    read_status: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class GovernmentOfficial(Base):
    __tablename__ = "government_officials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class DoctorManualReport(Base):
    __tablename__ = "doctor_manual_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uhid: Mapped[str] = mapped_column(String(32), index=True)
    visit_id: Mapped[str] = mapped_column(String(32), index=True)
    doctor_id: Mapped[str] = mapped_column(String(32), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    original_name: Mapped[str] = mapped_column(String(255))
    file_url: Mapped[str] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

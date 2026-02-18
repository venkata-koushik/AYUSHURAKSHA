from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    DoctorRegisterRequest,
    LoginRequest,
    PatientPhoneLoginRequest,
    PatientRegisterRequest,
    StudentRegisterRequest,
)
from app.services.auth_service import auth_service
from app.services.patient_service import patient_service

router = APIRouter()


@router.post("/register/patient")
def register_patient(payload: PatientRegisterRequest):
    if not auth_service.verify_otp(payload.otp):
        raise HTTPException(status_code=400, detail="Invalid OTP")
    patient = patient_service.register_patient(payload.model_dump())
    token = auth_service.create_simulated_token(role="patient", user_id=patient["patient_id"])
    return {"uhid": patient["uhid"], "health_qr_payload": patient["health_qr_payload"], "token": token}


@router.post("/register/doctor")
def register_doctor(payload: DoctorRegisterRequest):
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Password mismatch")
    try:
        doctor = auth_service.register_doctor(
            government_license_id=payload.government_license_id,
            full_name=payload.full_name,
            age=payload.age,
            username=payload.username,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"doctor_id": doctor["doctor_id"]}


@router.post("/register/student")
def register_student(payload: StudentRegisterRequest):
    if not auth_service.verify_otp(payload.otp):
        raise HTTPException(status_code=400, detail="Invalid OTP")
    try:
        student = auth_service.register_student(
            college_id=payload.college_id,
            email=payload.institutional_email,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"student_id": student["student_id"]}


@router.post("/login")
def login(payload: LoginRequest):
    try:
        if payload.role == "doctor":
            return auth_service.doctor_login(payload.username, payload.password)
        if payload.role == "student":
            return auth_service.student_login(payload.username, payload.password)
        if payload.role == "patient":
            # Simulated patient login by patient_id for mock stage.
            patient = patient_service.get_patient_dashboard(payload.username)
            return auth_service.create_simulated_token(role="patient", user_id=patient.patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Unsupported role")


@router.post("/login/patient-phone")
def patient_phone_login(payload: PatientPhoneLoginRequest):
    try:
        return auth_service.patient_login_with_phone(payload.phone, payload.otp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

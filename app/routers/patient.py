from fastapi import APIRouter, HTTPException

from app.models.schemas import ConsultationRequest, RatingRequest
from app.services.ai_service import ai_service
from app.services.patient_service import patient_service
from app.services.student_service import student_service

router = APIRouter()


@router.get("/dashboard/{patient_id}")
def patient_dashboard(patient_id: str):
    try:
        return patient_service.get_patient_dashboard(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/health-qr/{uhid}")
def my_health_qr(uhid: str):
    try:
        qr = patient_service.get_health_qr(uhid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return qr


@router.get("/reports/{uhid}")
def medical_reports(uhid: str):
    return patient_service.get_reports(uhid)


@router.get("/timeline/{uhid}")
def history_timeline(uhid: str):
    return {"timeline": patient_service.get_timeline(uhid)}


@router.get("/notifications/{patient_id}")
def patient_notifications(patient_id: str):
    return patient_service.get_notifications(patient_id)


@router.get("/ai-assistant")
def ai_health_assistant(question: str):
    return ai_service.assistant_reply(question)


@router.post("/book-consultation")
def book_consultation(payload: ConsultationRequest):
    try:
        return student_service.request_consultation(payload.patient_id, payload.language, payload.problem)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rating")
def submit_rating(payload: RatingRequest):
    try:
        return student_service.submit_rating(payload.session_id, payload.rating, payload.feedback)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

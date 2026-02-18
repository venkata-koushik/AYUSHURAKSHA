from fastapi import APIRouter, HTTPException

from app.models.schemas import SessionDecisionRequest, StudentLanguageRequest
from app.services.student_service import student_service

router = APIRouter()


@router.get("/dashboard/{student_id}")
def student_dashboard(student_id: str):
    try:
        return student_service.get_dashboard(student_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/go-online")
def go_online(student_id: str, payload: StudentLanguageRequest):
    student_service.go_online(student_id, payload.language)
    return {"online": True, "language": payload.language}


@router.post("/session-decision")
def session_decision(payload: SessionDecisionRequest):
    try:
        return student_service.decide_session(payload.session_id, payload.accepted)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/end-session/{session_id}")
def end_session(session_id: str):
    try:
        return student_service.end_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/incoming-requests/{student_id}")
def incoming_requests(student_id: str):
    return student_service.get_incoming_requests(student_id)


@router.get("/past-sessions/{student_id}")
def past_sessions(student_id: str):
    return student_service.get_past_sessions(student_id)


@router.get("/ratings/{student_id}")
def student_ratings(student_id: str):
    try:
        dashboard = student_service.get_dashboard(student_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"student_id": student_id, "rating_avg": dashboard.rating_avg, "rating_count": dashboard.rating_count}


@router.get("/notifications/{student_id}")
def student_notifications(student_id: str):
    return student_service.get_notifications(student_id)

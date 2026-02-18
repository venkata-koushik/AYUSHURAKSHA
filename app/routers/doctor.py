from fastapi import APIRouter, HTTPException

from app.models.schemas import EditReportRequest, ProcessVoiceRequest, SaveReportRequest
from app.services.ai_service import ai_service
from app.services.doctor_service import doctor_service

router = APIRouter()


@router.get("/dashboard/{doctor_id}")
def doctor_dashboard(doctor_id: str):
    try:
        return doctor_service.get_dashboard(doctor_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/scan/{uhid}")
def scan_patient_qr(uhid: str, doctor_id: str | None = None):
    try:
        if doctor_id:
            return doctor_service.assign_patient_from_scan(doctor_id, uhid)
        return doctor_service.patient_summary_from_qr(uhid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/process_voice")
def process_voice(payload: ProcessVoiceRequest):
    return ai_service.process_voice_text(payload.manual_text)


@router.get("/mic-status")
def mic_status_check():
    return {"connected": True, "mode": "mock"}


@router.post("/approve-report")
def approve_and_save_report(doctor_id: str, payload: SaveReportRequest):
    try:
        visit = doctor_service.save_approved_report(
            doctor_id=doctor_id,
            uhid=payload.uhid,
            structured=payload.structured_report,
            doctor_notes=payload.doctor_notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": True, "visit": visit, "notification": "Patient report updated"}


@router.post("/edit-report")
def edit_report(doctor_id: str, payload: EditReportRequest):
    try:
        visit = doctor_service.edit_report(
            doctor_id=doctor_id,
            visit_id=payload.visit_id,
            structured=payload.structured_report,
            doctor_notes=payload.doctor_notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"edited": True, "visit": visit}

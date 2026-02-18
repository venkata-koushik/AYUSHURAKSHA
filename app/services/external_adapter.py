from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("external_adapter")


class STTNormalizedResponse(BaseModel):
    text: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)


class SummaryNormalizedResponse(BaseModel):
    text: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
        if parsed < 0:
            return 0.0
        if parsed > 1:
            return 1.0
        return parsed
    except Exception:
        return None


def _walk_values(data: Any):
    if isinstance(data, dict):
        for value in data.values():
            yield from _walk_values(value)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_values(item)
    else:
        yield data


def _extract_first_string(data: Any) -> str:
    for value in _walk_values(data):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_confidence(data: Any) -> float | None:
    if isinstance(data, dict):
        for key in ("confidence", "score", "avg_confidence", "probability"):
            if key in data:
                parsed = _safe_float(data.get(key))
                if parsed is not None:
                    return parsed
        for value in data.values():
            nested = _extract_confidence(value)
            if nested is not None:
                return nested
    elif isinstance(data, list):
        for item in data:
            nested = _extract_confidence(item)
            if nested is not None:
                return nested
    return None


def normalize_stt_response(raw_response: dict) -> dict:
    logger.info("normalize_stt_response.start", extra={"raw_type": type(raw_response).__name__})
    data: Any = raw_response if isinstance(raw_response, dict) else {}

    text = ""
    candidates = [
        data.get("transcript"),
        data.get("transcription"),
        data.get("text"),
        data.get("result"),
        data.get("message"),
    ]
    for item in candidates:
        if isinstance(item, str) and item.strip():
            text = item.strip()
            break
        if isinstance(item, dict):
            nested = (
                item.get("text")
                or item.get("transcript")
                or item.get("value")
                or item.get("content")
            )
            if isinstance(nested, str) and nested.strip():
                text = nested.strip()
                break

    if not text:
        segments = data.get("segments") or data.get("results")
        if isinstance(segments, list):
            parts: list[str] = []
            for seg in segments:
                if isinstance(seg, dict):
                    seg_text = seg.get("text") or seg.get("transcript") or seg.get("value")
                    if isinstance(seg_text, str) and seg_text.strip():
                        parts.append(seg_text.strip())
            if parts:
                text = " ".join(parts).strip()

    if not text:
        alternatives = data.get("alternatives")
        if isinstance(alternatives, list):
            for alt in alternatives:
                if isinstance(alt, dict):
                    alt_text = alt.get("transcript") or alt.get("text")
                    if isinstance(alt_text, str) and alt_text.strip():
                        text = alt_text.strip()
                        break

    if not text:
        logger.warning("normalize_stt_response.schema_mismatch")
        text = _extract_first_string(data)

    confidence = _extract_confidence(data)
    normalized = STTNormalizedResponse(text=text or "", confidence=confidence)
    payload = normalized.model_dump()
    logger.info("normalize_stt_response.done", extra={"has_text": bool(payload["text"]), "confidence": payload["confidence"]})
    return payload


def normalize_summary_response(raw_response: dict) -> dict:
    logger.info("normalize_summary_response.start", extra={"raw_type": type(raw_response).__name__})
    data: Any = raw_response if isinstance(raw_response, dict) else {}

    text = ""
    summary_obj = data.get("summary")
    if isinstance(summary_obj, str):
        text = summary_obj.strip()
    elif isinstance(summary_obj, dict):
        for key in ("text", "summary_text", "recommendation", "content"):
            value = summary_obj.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        if not text:
            symptoms = summary_obj.get("symptoms")
            diagnosis = summary_obj.get("diagnosis")
            recommendation = summary_obj.get("recommendation")
            fragments = [x for x in [symptoms, diagnosis, recommendation] if isinstance(x, str) and x.strip()]
            if fragments:
                text = " | ".join(fragments)

    if not text:
        for key in ("text", "output", "result", "message", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break

    if not text:
        logger.warning("normalize_summary_response.schema_mismatch")
        text = _extract_first_string(data)

    confidence = _extract_confidence(data)
    normalized = SummaryNormalizedResponse(text=text or "", confidence=confidence)
    payload = normalized.model_dump()
    logger.info("normalize_summary_response.done", extra={"has_text": bool(payload["text"]), "confidence": payload["confidence"]})
    return payload

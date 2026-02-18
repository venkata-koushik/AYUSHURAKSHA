from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
from typing import Any

import requests
from dotenv import load_dotenv

from app.models.schemas import StructuredClinicalOutput
from app.services.external_adapter import normalize_stt_response, normalize_summary_response


class AIService:
    def __init__(self) -> None:
        self._logger = logging.getLogger("ai_service")
        self._whisper_model = None
        self._whisper_model_name = "base"
        self._llm_enabled = False
        self._ai_api_key = ""
        self._ai_base_url = ""
        self._ai_chat_model = ""
        self._ai_stt_model = ""
        self._external_stt_url = os.getenv("SPEECH_TO_TEXT_API", "").strip()
        self._external_summarize_url = (
            os.getenv("TEXT_SUMMERISATION_API", "").strip()
            or os.getenv("TEXT_SUMMARIZATION_API", "").strip()
        )
        self._external_chat_url = os.getenv("AI_ASSISTANT_CHATBOOT", "").strip()
        self._refresh_external_endpoints()

    def _ensure_ffmpeg_available(self) -> str | None:
        # 1) Normal PATH check.
        ff = shutil.which("ffmpeg")
        if ff:
            return ff

        # 2) Explicit env hint.
        explicit = os.getenv("FFMPEG_BIN", "").strip()
        if explicit and os.path.exists(explicit):
            bin_dir = explicit if os.path.isdir(explicit) else os.path.dirname(explicit)
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + bin_dir
            ff = shutil.which("ffmpeg")
            if ff:
                return ff

        # 3) Windows winget default location fallback.
        local = os.getenv("LOCALAPPDATA", "")
        if local:
            base = os.path.join(
                local,
                "Microsoft",
                "WinGet",
                "Packages",
                "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
            )
            if os.path.isdir(base):
                candidates: list[str] = []
                for root, _dirs, files in os.walk(base):
                    if "ffmpeg.exe" in files:
                        candidates.append(root)
                if candidates:
                    # Prefer shortest path (typically ...\bin)
                    best = sorted(candidates, key=len)[0]
                    os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + best
                    ff = shutil.which("ffmpeg")
                    if ff:
                        return ff
        return None

    def _refresh_external_endpoints(self) -> None:
        # Ensure .env values are available even when this service is imported
        # before app startup wiring.
        load_dotenv(override=False)
        # Read latest env values at runtime (important when .env is updated after import).
        self._external_stt_url = os.getenv("SPEECH_TO_TEXT_API", "").strip()
        self._external_summarize_url = (
            os.getenv("TEXT_SUMMERISATION_API", "").strip()
            or os.getenv("TEXT_SUMMARIZATION_API", "").strip()
        )
        self._external_chat_url = os.getenv("AI_ASSISTANT_CHATBOOT", "").strip()
        if not self._external_chat_url:
            self._external_chat_url = os.getenv("AI_ASSISTANT_CHATBOT", "").strip()
        if not self._external_chat_url:
            self._external_chat_url = os.getenv("AI_ASSISTAT_CHATBOT", "").strip()
        unified_url = os.getenv("AI_UNIFIED_API_URL", "").strip()
        if unified_url:
            if not self._external_chat_url:
                self._external_chat_url = unified_url
            if not self._external_stt_url:
                self._external_stt_url = unified_url
            if not self._external_summarize_url:
                self._external_summarize_url = unified_url

        key = (
            os.getenv("AI_API_KEY", "").strip()
            or os.getenv("NEW_API_KEY", "").strip()
            or os.getenv("OPENAI_API_KEY", "").strip()
        )
        base_url = (
            os.getenv("AI_BASE_URL", "").strip()
            or os.getenv("OPENAI_BASE_URL", "").strip()
        )
        if not base_url:
            # Groq keys usually start with gsk_; use OpenAI-compatible Groq endpoint automatically.
            base_url = "https://api.groq.com/openai/v1" if key.startswith("gsk_") else "https://api.openai.com/v1"
        self._ai_api_key = key
        self._ai_base_url = base_url.rstrip("/")
        self._ai_chat_model = os.getenv("AI_CHAT_MODEL", "").strip() or os.getenv("OPENAI_MODEL", "llama-3.1-8b-instant").strip()
        self._ai_stt_model = os.getenv("AI_STT_MODEL", "whisper-large-v3-turbo").strip()
        self._llm_enabled = bool(self._ai_api_key)

    def _provider_chat_completion(self, text: str, system_prompt: str) -> dict[str, Any] | None:
        self._refresh_external_endpoints()
        if not self._ai_api_key:
            return None
        url = f"{self._ai_base_url}/chat/completions"
        payload = {
            "model": self._ai_chat_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._ai_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=8,
            )
            resp.raise_for_status()
            body = resp.json()
            choices = body.get("choices", [])
            if not choices:
                return None
            content = choices[0].get("message", {}).get("content", "")
            parsed = json.loads(content) if isinstance(content, str) else content
            return parsed if isinstance(parsed, dict) else None
        except Exception as exc:
            self._logger.warning("provider_chat_completion_error: %s", exc)
            return None

    def _ensure_english_text(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        parsed = self._provider_chat_completion(
            cleaned,
            (
                "Translate the user text to clear professional English. "
                "Return STRICT JSON with one key: english_text. "
                "Do not add extra commentary."
            ),
        )
        if parsed and isinstance(parsed, dict):
            english = str(parsed.get("english_text", "")).strip()
            if english:
                return english
        return cleaned

    def _ensure_english_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(summary, dict):
            return {"symptoms": "Not detected", "diagnosis": "Needs clinical review", "recommendation": "Consult clinician"}
        symptoms = self._ensure_english_text(str(summary.get("symptoms", "Not detected")))
        diagnosis = self._ensure_english_text(str(summary.get("diagnosis", "Needs clinical review")))
        recommendation = self._ensure_english_text(str(summary.get("recommendation", "Consult clinician")))
        return {
            "symptoms": symptoms or "Not detected",
            "diagnosis": diagnosis or "Needs clinical review",
            "recommendation": recommendation or "Consult clinician",
        }

    def _normalize_language_hint(self, language_hint: str | None) -> str | None:
        raw = (language_hint or "").strip().lower().replace("_", "-")
        if not raw:
            return None
        mapping = {
            "english": "en",
            "en": "en",
            "en-in": "en",
            "hindi": "hi",
            "hindhi": "hi",
            "hi": "hi",
            "hi-in": "hi",
            "telugu": "te",
            "telgu": "te",
            "te": "te",
            "te-in": "te",
            "tamil": "ta",
            "ta": "ta",
            "ta-in": "ta",
            "kannada": "kn",
            "kannda": "kn",
            "kanda": "kn",
            "kn": "kn",
            "kn-in": "kn",
            "malayalam": "ml",
            "ml": "ml",
            "ml-in": "ml",
            "marathi": "mr",
            "marati": "mr",
            "marathi-in": "mr",
            "mr": "mr",
            "mr-in": "mr",
            "gujarati": "gu",
            "gu": "gu",
            "gu-in": "gu",
            "bengali": "bn",
            "bn": "bn",
            "bn-in": "bn",
            "punjabi": "pa",
            "pa": "pa",
            "pa-in": "pa",
            "odia": "or",
            "oriya": "or",
            "or": "or",
            "ur": "ur",
            "urdu": "ur",
            "ur-in": "ur",
            "assamese": "as",
            "as": "as",
            "as-in": "as",
            "nepali": "ne",
            "ne": "ne",
            "ne-in": "ne",
            "sanskrit": "sa",
            "sa": "sa",
            "sa-in": "sa",
        }
        return mapping.get(raw, raw.split("-")[0] if "-" in raw else raw)

    def _provider_stt(self, filename: str, audio_bytes: bytes, language_hint: str | None = None) -> dict[str, Any] | None:
        self._refresh_external_endpoints()
        if not self._ai_api_key:
            return None
        url = f"{self._ai_base_url}/audio/transcriptions"
        try:
            files = {"file": (filename, audio_bytes, "audio/webm")}
            data = {"model": self._ai_stt_model}
            hint = self._normalize_language_hint(language_hint)
            if hint:
                data["language"] = hint
            resp = requests.post(
                url,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {self._ai_api_key}"},
                timeout=12,
            )
            resp.raise_for_status()
            raw = resp.json() if resp.content else {}
            normalized = normalize_stt_response(raw)
            text = (normalized.get("text") or "").strip()
            if not text:
                return None
            confidence_value = normalized.get("confidence")
            if confidence_value is None:
                confidence_value = 0.8
            return {
                "transcript": text,
                "confidence": round(max(0.0, min(1.0, confidence_value)), 4),
                "provider": "provider_stt_api",
            }
        except Exception as exc:
            self._logger.warning("provider_stt_error: %s", exc)
            return None

    def _external_stt(self, filename: str, audio_bytes: bytes, language_hint: str | None = None) -> dict[str, Any] | None:
        self._refresh_external_endpoints()
        if not self._external_stt_url:
            return None
        try:
            files = {"file": (filename, audio_bytes, "audio/webm")}
            data = {}
            hint = self._normalize_language_hint(language_hint)
            if hint:
                data["language"] = hint
            resp = requests.post(self._external_stt_url, files=files, data=data or None, timeout=8)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            normalized = normalize_stt_response(data)
            transcript_text = (normalized.get("text") or "").strip()
            if not transcript_text:
                self._logger.warning("external_stt_empty_text")
                return None
            confidence_value = normalized.get("confidence")
            if confidence_value is None:
                confidence_value = 0.8
            return {
                "transcript": transcript_text,
                "confidence": round(max(0.0, min(1.0, confidence_value)), 4),
                "provider": "external_stt_api",
            }
        except Exception as exc:
            self._logger.warning("external_stt_error: %s", exc)
            return None

    def _external_summarize(self, transcription: str) -> dict[str, Any] | None:
        self._refresh_external_endpoints()
        if not self._external_summarize_url:
            return None
        payload_candidates = [
            {"transcription": transcription},
            {"text": transcription},
            {"input": transcription},
        ]
        for payload in payload_candidates:
            try:
                resp = requests.post(self._external_summarize_url, json=payload, timeout=8)
                resp.raise_for_status()
                data = resp.json() if resp.content else {}
                normalized = normalize_summary_response(data)
                summary = data.get("summary", data)
                if isinstance(summary, str):
                    return {
                        "summary": {
                            "symptoms": "Not detected",
                            "diagnosis": "Needs clinical review",
                            "recommendation": summary or normalized.get("text", ""),
                        },
                        "provider": "external_summarization_api",
                    }
                if isinstance(summary, dict):
                    symptoms = str(summary.get("symptoms", summary.get("symptom", "Not detected")))
                    diagnosis = str(summary.get("diagnosis", summary.get("possible_diagnosis", "Needs clinical review")))
                    recommendation = str(summary.get("recommendation", summary.get("advice", normalized.get("text", "Consult clinician"))))
                    return {
                        "summary": {
                            "symptoms": symptoms,
                            "diagnosis": diagnosis,
                            "recommendation": recommendation,
                        },
                        "provider": "external_summarization_api",
                    }
                if normalized.get("text"):
                    return {
                        "summary": {
                            "symptoms": "Not detected",
                            "diagnosis": "Needs clinical review",
                            "recommendation": normalized["text"],
                        },
                        "provider": "external_summarization_api",
                    }
            except Exception as exc:
                self._logger.warning("external_summarize_error: %s", exc)
                continue
        return None

    def _external_chat(self, question: str) -> dict[str, Any] | None:
        self._refresh_external_endpoints()
        if not self._external_chat_url:
            return None
        payload_candidates = [
            {"message": question},
            {"question": question},
            {"text": question},
            {"input": question},
        ]
        for payload in payload_candidates:
            try:
                resp = requests.post(self._external_chat_url, json=payload, timeout=4)
                resp.raise_for_status()
                data = resp.json() if resp.content else {}
                if isinstance(data, dict):
                    text = (
                        data.get("reply")
                        or data.get("response")
                        or data.get("message")
                        or data.get("text")
                        or ""
                    )
                    if isinstance(text, str) and text.strip():
                        return {
                            "guidance": [text.strip()],
                            "summary": {"symptoms_detected": [], "possible_diagnosis_tags": []},
                            "urgency": "medium",
                            "provider": "external_chat_api",
                        }
                if isinstance(data, str) and data.strip():
                    return {
                        "guidance": [data.strip()],
                        "summary": {"symptoms_detected": [], "possible_diagnosis_tags": []},
                        "urgency": "medium",
                        "provider": "external_chat_api",
                    }
            except Exception as exc:
                self._logger.warning("external_chat_error: %s", exc)
                continue
        return None

    def patient_chatbot_reply(self, question: str) -> dict[str, Any]:
        english_question = self._ensure_english_text(question)
        chat_mode = os.getenv("AI_CHAT_MODE", "rules").strip().lower()
        if chat_mode in {"rules", "deterministic", "fast"}:
            structured = self.process_voice_text(english_question or question)
            fallback = structured.advice if structured.advice else [
                "Maintain hydration, balanced diet, and adequate sleep."
            ]
            return {
                "question": english_question or question,
                "guidance": self._ayush_enrich_guidance(english_question, fallback),
                "summary": {
                    "symptoms_detected": structured.symptoms or [],
                    "possible_diagnosis_tags": structured.diagnosis or [],
                },
                "urgency": "medium",
                "provider": "rules",
                "disclaimer": "For urgent or severe symptoms, consult a registered doctor immediately.",
            }
        external_chat = self._external_chat(english_question)
        if external_chat:
            guidance = self._ayush_enrich_guidance(english_question, external_chat.get("guidance") or [])
            return {
                "question": english_question or question,
                "guidance": guidance,
                "summary": external_chat["summary"],
                "urgency": external_chat["urgency"],
                "provider": external_chat["provider"],
                "disclaimer": "For urgent or severe symptoms, consult a registered doctor immediately.",
            }
        llm_reply = self._llm_clinical_reply(english_question)
        if llm_reply:
            guidance = self._ayush_enrich_guidance(
                english_question,
                llm_reply["guidance"] or ["Consult a registered doctor for clinical confirmation."],
            )
            return {
                "question": english_question or question,
                "guidance": guidance,
                "summary": llm_reply["summary"],
                "urgency": llm_reply["urgency"],
                "provider": llm_reply["provider"],
                "disclaimer": "For urgent or severe symptoms, consult a registered doctor immediately.",
            }
        # Local deterministic fallback: keep chatbot usable even when external endpoints are down.
        structured = self.process_voice_text(english_question or question)
        fallback = structured.advice if structured.advice else [
            "Maintain hydration, balanced diet, and adequate sleep."
        ]
        return {
            "question": english_question or question,
            "guidance": self._ayush_enrich_guidance(english_question, fallback),
            "summary": {
                "symptoms_detected": structured.symptoms or [],
                "possible_diagnosis_tags": structured.diagnosis or [],
            },
            "urgency": "medium",
            "provider": "rules",
            "disclaimer": "For urgent or severe symptoms, consult a registered doctor immediately.",
        }

    def _ayush_enrich_guidance(self, question: str, base_guidance: list[str]) -> list[str]:
        q = (question or "").lower()
        merged: list[str] = [str(x).strip() for x in (base_guidance or []) if str(x).strip()]

        ayush_blocks: list[str] = []
        if any(k in q for k in ["cold", "cough", "throat", "fever", "flu"]):
            ayush_blocks.append(
                "AYUSH self-care: warm water, tulsi-ginger infusion, steam inhalation, and adequate rest can support recovery."
            )
        if any(k in q for k in ["stress", "anxiety", "sleep", "insomnia", "mental"]):
            ayush_blocks.append(
                "Yoga/pranayama support: try 10-15 minutes of Anulom Vilom or Bhramari and keep a fixed sleep routine."
            )
        if any(k in q for k in ["joint", "pain", "arthritis", "back pain", "stiffness"]):
            ayush_blocks.append(
                "Lifestyle support: gentle yoga stretching, warm compress, and posture care may reduce discomfort."
            )
        if any(k in q for k in ["digestion", "acidity", "gas", "bloating", "constipation"]):
            ayush_blocks.append(
                "Diet support: prefer light warm meals, avoid oily/spicy late-night food, and hydrate regularly."
            )
        if not ayush_blocks:
            ayush_blocks.append(
                "AYUSH wellness tip: maintain daily yoga, balanced satvik-style meals, hydration, and regular sleep."
            )

        safety = "If symptoms are severe, persistent, or worsening, consult a registered doctor immediately."

        for line in ayush_blocks + [safety]:
            if line not in merged:
                merged.append(line)
        return merged

    def _llm_clinical_reply(self, text: str) -> dict[str, Any] | None:
        self._refresh_external_endpoints()
        if not self._ai_api_key:
            return None
        system_prompt = (
            "You are a cautious AYUSH triage assistant. Return STRICT JSON with keys: "
            "guidance (array of short strings), symptoms_detected (array), possible_diagnosis_tags (array), "
            "urgency (low|medium|high). Never provide definitive diagnosis. Include one safety line in guidance."
        )
        try:
            parsed = self._provider_chat_completion(text, system_prompt)
            if parsed is None:
                return None
            guidance = parsed.get("guidance") or []
            symptoms = parsed.get("symptoms_detected") or []
            tags = parsed.get("possible_diagnosis_tags") or []
            if not isinstance(guidance, list):
                guidance = [str(guidance)]
            if not isinstance(symptoms, list):
                symptoms = [str(symptoms)]
            if not isinstance(tags, list):
                tags = [str(tags)]
            return {
                "guidance": [str(x) for x in guidance if str(x).strip()],
                "summary": {
                    "symptoms_detected": [str(x) for x in symptoms if str(x).strip()],
                    "possible_diagnosis_tags": [str(x) for x in tags if str(x).strip()],
                },
                "urgency": str(parsed.get("urgency", "medium")).lower(),
                "provider": "llm",
            }
        except Exception:
            return None

    def _get_whisper_model(self):
        if self._whisper_model is not None:
            return self._whisper_model
        if self._ensure_ffmpeg_available() is None:
            raise RuntimeError("ffmpeg is not installed or not available in PATH")
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as exc:
            raise RuntimeError("faster-whisper is not installed") from exc
        self._whisper_model = WhisperModel(self._whisper_model_name, device="cpu", compute_type="int8")
        return self._whisper_model

    def transcribe_audio_bytes(self, filename: str, audio_bytes: bytes, language_hint: str | None = None) -> dict[str, Any]:
        if not audio_bytes:
            raise RuntimeError("Audio file is empty")
        provider_stt = self._provider_stt(filename, audio_bytes, language_hint=language_hint)
        if provider_stt:
            provider_stt["transcript"] = self._ensure_english_text(str(provider_stt.get("transcript", "")))
            return provider_stt
        external = self._external_stt(filename, audio_bytes, language_hint=language_hint)
        if external:
            external["transcript"] = self._ensure_english_text(str(external.get("transcript", "")))
            return external
        model = self._get_whisper_model()
        suffix = ".webm" if filename.lower().endswith(".webm") else ".webm"
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            hint = self._normalize_language_hint(language_hint)
            transcribe_kwargs: dict[str, Any] = {"beam_size": 1, "vad_filter": True}
            if hint:
                transcribe_kwargs["language"] = hint
            segments, _info = model.transcribe(tmp_path, **transcribe_kwargs)
            text_parts: list[str] = []
            conf_points: list[float] = []
            for seg in segments:
                seg_text = (getattr(seg, "text", "") or "").strip()
                if seg_text:
                    text_parts.append(seg_text)
                avg_logprob = getattr(seg, "avg_logprob", None)
                if isinstance(avg_logprob, (int, float)):
                    conf_points.append(max(0.0, min(1.0, math.exp(avg_logprob))))
            transcript = " ".join(text_parts).strip()
            if not transcript:
                raise RuntimeError("No speech detected in audio")
            transcript = self._ensure_english_text(transcript)
            confidence = sum(conf_points) / len(conf_points) if conf_points else 0.75
            return {"transcript": transcript, "confidence": round(float(confidence), 4)}
        finally:
            if tmp_path:
                try:
                    import os
                    os.remove(tmp_path)
                except Exception:
                    pass

    def process_voice_text(self, text: str) -> StructuredClinicalOutput:
        # Upgrade path: /process_voice keeps same contract, swap manual text with faster-whisper.
        lowered = text.lower()
        symptom_words = ["fever", "cough", "pain", "fatigue", "headache", "cold"]
        disease_words = ["diabetes", "hypertension", "flu", "arthritis", "asthma"]

        symptoms = [w for w in symptom_words if w in lowered]
        diagnosis = [w for w in disease_words if w in lowered]
        advice = self.recommendations(symptoms=symptoms, diagnosis=diagnosis)

        return StructuredClinicalOutput(
            raw_text=text,
            symptoms=symptoms,
            diagnosis=diagnosis,
            advice=advice,
        )

    def recommendations(self, symptoms: list[str], diagnosis: list[str]) -> list[str]:
        # Upgrade path: hybrid rules + ML model.
        suggestions: list[str] = []
        if "fever" in symptoms:
            suggestions.append("Hydrate and monitor body temperature.")
        if "cough" in symptoms:
            suggestions.append("Use warm fluids and avoid cold irritants.")
        if "diabetes" in diagnosis:
            suggestions.append("Follow low glycemic meals and daily walking.")
        if "hypertension" in diagnosis:
            suggestions.append("Reduce salt intake and practice breathing exercises.")
        if not suggestions:
            suggestions.append("Maintain balanced diet, sleep, and routine activity.")
        return suggestions

    def assistant_reply(self, question: str) -> dict:
        english_question = self._ensure_english_text(question)
        external_chat = self._external_chat(english_question)
        if external_chat:
            return {
                "question": english_question or question,
                "guidance": external_chat["guidance"],
                "summary": external_chat["summary"],
                "urgency": external_chat["urgency"],
                "provider": external_chat["provider"],
                "disclaimer": "For urgent or severe symptoms, consult a registered doctor immediately.",
            }

        llm_reply = self._llm_clinical_reply(english_question)
        if llm_reply:
            guidance = llm_reply["guidance"] or ["Consult a registered doctor for clinical confirmation."]
            return {
                "question": english_question or question,
                "guidance": guidance,
                "summary": llm_reply["summary"],
                "urgency": llm_reply["urgency"],
                "provider": llm_reply["provider"],
                "disclaimer": "For urgent or severe symptoms, consult a registered doctor immediately.",
            }

        structured = self.process_voice_text(english_question)
        q = english_question.lower()
        extra: list[str] = []
        if any(k in q for k in ["severe", "chest pain", "breathless", "faint", "bleeding", "stroke"]):
            extra.append("Seek urgent in-person medical care immediately.")
        if any(k in q for k in ["diet", "food", "eat"]):
            extra.append("Prefer light, low-oil meals, hydration, and avoid processed sugar.")
        if any(k in q for k in ["sleep", "stress", "anxiety"]):
            extra.append("Maintain fixed sleep timing and add 10-15 minutes of breathing or mindfulness daily.")
        guidance = structured.advice + [x for x in extra if x not in structured.advice]
        return {
            "question": english_question or question,
            "guidance": guidance,
            "summary": {
                "symptoms_detected": structured.symptoms,
                "possible_diagnosis_tags": structured.diagnosis,
            },
            "provider": "rules",
            "disclaimer": "For urgent or severe symptoms, consult a registered doctor immediately.",
        }

    def summarize_consultation(self, transcription: str) -> dict[str, Any]:
        english_transcription = self._ensure_english_text(transcription)
        external = self._external_summarize(english_transcription)
        if external:
            external["summary"] = self._ensure_english_summary(external.get("summary", {}))
            external["transcription_english"] = english_transcription
            return external
        llm_reply = self._llm_clinical_reply(f"Summarize this doctor consultation:\n{english_transcription}")
        if llm_reply:
            payload = {
                "summary": {
                    "symptoms": ", ".join(llm_reply["summary"]["symptoms_detected"]) or "Not detected",
                    "diagnosis": ", ".join(llm_reply["summary"]["possible_diagnosis_tags"]) or "Needs clinical review",
                    "recommendation": "; ".join(llm_reply["guidance"]) or "Consult clinician",
                },
                "provider": llm_reply["provider"],
                "urgency": llm_reply["urgency"],
                "transcription_english": english_transcription,
            }
            payload["summary"] = self._ensure_english_summary(payload.get("summary", {}))
            return payload
        structured = self.process_voice_text(english_transcription)
        payload = {
            "summary": {
                "symptoms": ", ".join(structured.symptoms) if structured.symptoms else "Not detected",
                "diagnosis": ", ".join(structured.diagnosis) if structured.diagnosis else "Needs clinical review",
                "recommendation": "; ".join(structured.advice),
            },
            "provider": "rules",
            "transcription_english": english_transcription,
        }
        payload["summary"] = self._ensure_english_summary(payload.get("summary", {}))
        return payload

    def normalize_to_english(self, text: str) -> str:
        return self._ensure_english_text(text)


ai_service = AIService()

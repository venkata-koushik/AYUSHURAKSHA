from __future__ import annotations

import os

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client


class SMSService:
    def __init__(self) -> None:
        self.account_sid = ""
        self.auth_token = ""
        self.verify_service_sid = ""
        self.test_override_phone = ""

    def _reload_config(self) -> None:
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        self.verify_service_sid = os.getenv("TWILIO_VERIFY_SERVICE_SID", "").strip()
        # Optional dev/testing override: when set, force OTP delivery to this number.
        self.test_override_phone = os.getenv("OTP_PHONE_OVERRIDE", "").strip()

    def is_configured(self) -> bool:
        self._reload_config()
        return bool(self.account_sid and self.auth_token and self.verify_service_sid)

    def _normalize_phone(self, phone: str) -> str:
        p = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
        if p.startswith("+"):
            return p
        # Default to India country code when not provided.
        if len(p) == 10:
            return f"+91{p}"
        return f"+{p}" if p and not p.startswith("+") else p

    def send_otp_phone(self, phone: str) -> dict:
        self._reload_config()
        # Demo mode: disable Twilio calls entirely.
        if os.getenv("TWILIO_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            to = self._normalize_phone(self.test_override_phone or phone)
            return {"channel": "phone", "sent": True, "mock": True, "status": "pending", "target_phone": to}
        target = self.test_override_phone or phone
        to = self._normalize_phone(target)
        if not self.is_configured():
            return {"channel": "phone", "sent": False, "detail": "Twilio not configured", "target_phone": to}
        try:
            client = Client(self.account_sid, self.auth_token)
            verification = client.verify.v2.services(self.verify_service_sid).verifications.create(to=to, channel="sms")
            return {
                "channel": "phone",
                "sent": verification.status in {"pending", "approved"},
                "status": verification.status,
                "target_phone": to,
            }
        except TwilioRestException as exc:
            return {"channel": "phone", "sent": False, "detail": str(exc), "target_phone": to}
        except Exception as exc:
            return {"channel": "phone", "sent": False, "detail": str(exc), "target_phone": to}

    def verify_otp_phone(self, phone: str, otp: str) -> bool:
        self._reload_config()
        if os.getenv("TWILIO_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            return False
        to = self._normalize_phone(phone)
        if not self.is_configured():
            return False
        try:
            client = Client(self.account_sid, self.auth_token)
            check = client.verify.v2.services(self.verify_service_sid).verification_checks.create(to=to, code=otp)
            return check.status == "approved"
        except Exception:
            return False


sms_service = SMSService()

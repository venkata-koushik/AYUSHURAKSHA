from __future__ import annotations

import os

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


class EmailService:
    def __init__(self) -> None:
        self.api_key = ""
        self.email_from = "no-reply@ayush.local"
        self.test_override_to = "koushik.chinu.2007@gmail.com"

    def _reload_config(self) -> None:
        # Read env dynamically so values are available even if dotenv is loaded after module import.
        self.api_key = os.getenv("SENDGRID_API_KEY", "").strip()
        self.email_from = os.getenv("EMAIL_FROM", "no-reply@ayush.local").strip()
        self.test_override_to = os.getenv("OTP_EMAIL_OVERRIDE", "koushik.chinu.2007@gmail.com").strip()

    def is_configured(self) -> bool:
        self._reload_config()
        return bool(self.api_key)

    def send_otp_email(self, to_email: str, subject: str, otp: str) -> dict:
        self._reload_config()
        target_email = self.test_override_to or to_email
        if not self.api_key:
            # Dev fallback to keep flow testable without SendGrid.
            return {
                "sent": False,
                "mock": True,
                "detail": "SENDGRID_API_KEY not configured",
                "target_email": target_email,
            }
        content = (
            f"Your Verification Code: {otp}\n\n"
            "This code will expire in 5 minutes.\n"
            "If you did not request this, please ignore."
        )
        message = Mail(
            from_email=self.email_from,
            to_emails=target_email,
            subject=subject,
            plain_text_content=content,
        )
        try:
            response = SendGridAPIClient(self.api_key).send(message)
            return {
                "sent": 200 <= response.status_code < 300,
                "status_code": response.status_code,
                "target_email": target_email,
            }
        except Exception as exc:
            return {
                "sent": False,
                "status_code": getattr(exc, "status_code", None),
                "target_email": target_email,
                "detail": str(exc),
            }


email_service = EmailService()

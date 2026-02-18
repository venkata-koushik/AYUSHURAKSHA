from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext


class SecurityService:
    def __init__(self) -> None:
        self._pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
        self.jwt_secret_key = os.getenv("JWT_SECRET_KEY", "ayush-dev-secret-change-in-env")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.jwt_expiry_minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "120"))

    def hash_password(self, plain_password: str) -> str:
        return self._pwd.hash(plain_password)

    def verify_password(self, plain_password: str, password_hash: str) -> bool:
        return self._pwd.verify(plain_password, password_hash)

    def create_access_token(self, subject: str, role: str, extra: dict[str, Any] | None = None) -> str:
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "sub": subject,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=self.jwt_expiry_minutes)).timestamp()),
        }
        if extra:
            payload.update(extra)
        return jwt.encode(payload, self.jwt_secret_key, algorithm=self.jwt_algorithm)

    def decode_token(self, token: str) -> dict[str, Any]:
        try:
            return jwt.decode(token, self.jwt_secret_key, algorithms=[self.jwt_algorithm])
        except JWTError as exc:
            raise ValueError("Invalid or expired token") from exc


security_service = SecurityService()

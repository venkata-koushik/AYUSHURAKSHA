from __future__ import annotations

import os
import random
import time
import uuid
from collections import defaultdict

import redis


class OTPService:
    def __init__(self) -> None:
        self.expiry_seconds = int(os.getenv("OTP_EXPIRY_SECONDS", "300"))
        self.cooldown_seconds = int(os.getenv("OTP_COOLDOWN_SECONDS", "30"))
        self.max_attempts = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis = None
        self._store: dict[str, dict[str, dict[str, int | str]]] = defaultdict(dict)
        self._ticket_store: dict[str, dict[str, dict[str, int | str]]] = defaultdict(dict)

    def _fixed_code(self) -> str | None:
        code = os.getenv("OTP_FIXED_CODE", "").strip()
        if not code:
            return None
        # Only allow 6-digit numeric codes.
        if len(code) == 6 and code.isdigit():
            return code
        return None

    def _client(self):
        if self._redis is not None:
            return self._redis
        try:
            self._redis = redis.Redis.from_url(self.redis_url, decode_responses=True)
            self._redis.ping()
            return self._redis
        except Exception:
            self._redis = None
            return None

    def _now(self) -> int:
        return int(time.time())

    def generate(self) -> str:
        fixed = self._fixed_code()
        if fixed:
            return fixed
        return f"{random.randint(0, 999999):06d}"

    def issue(self, namespace: str, identifier: str) -> tuple[str, int]:
        now = self._now()
        redis_client = self._client()
        key = f"otp:{namespace}:{identifier}"
        if redis_client:
            current = redis_client.hgetall(key)
            if current and int(current.get("cooldown_until", "0")) > now:
                raise ValueError("Please wait before requesting another OTP")
            otp = self.generate()
            expires_at = now + self.expiry_seconds
            payload = {
                "otp": otp,
                "expires_at": str(expires_at),
                "attempts": "0",
                "cooldown_until": str(now + self.cooldown_seconds),
            }
            redis_client.hset(key, mapping=payload)
            redis_client.expire(key, self.expiry_seconds)
            return otp, expires_at

        current = self._store[namespace].get(identifier)
        if current and int(current.get("cooldown_until", 0)) > now:
            raise ValueError("Please wait before requesting another OTP")
        otp = self.generate()
        expires_at = now + self.expiry_seconds
        self._store[namespace][identifier] = {
            "otp": otp,
            "expires_at": expires_at,
            "attempts": 0,
            "cooldown_until": now + self.cooldown_seconds,
        }
        return otp, expires_at

    def verify(self, namespace: str, identifier: str, otp: str) -> bool:
        now = self._now()
        redis_client = self._client()
        key = f"otp:{namespace}:{identifier}"
        if redis_client:
            current = redis_client.hgetall(key)
            if not current:
                return False
            if int(current.get("expires_at", "0")) < now:
                redis_client.delete(key)
                return False
            attempts = int(current.get("attempts", "0"))
            if attempts >= self.max_attempts:
                redis_client.delete(key)
                return False
            if current.get("otp") != otp:
                redis_client.hset(key, "attempts", str(attempts + 1))
                return False
            redis_client.delete(key)
            return True

        current = self._store[namespace].get(identifier)
        if not current:
            return False
        if int(current.get("expires_at", 0)) < now:
            del self._store[namespace][identifier]
            return False
        if int(current.get("attempts", 0)) >= self.max_attempts:
            del self._store[namespace][identifier]
            return False
        if current.get("otp") != otp:
            current["attempts"] = int(current.get("attempts", 0)) + 1
            return False
        del self._store[namespace][identifier]
        return True

    def issue_ticket(self, namespace: str, identifier: str, ttl_seconds: int = 600) -> tuple[str, int]:
        now = self._now()
        expires_at = now + max(60, int(ttl_seconds))
        token = f"rst_{uuid.uuid4().hex}"
        redis_client = self._client()
        key = f"otp_ticket:{namespace}:{identifier}"
        if redis_client:
            redis_client.hset(key, mapping={"token": token, "expires_at": str(expires_at)})
            redis_client.expire(key, max(60, int(ttl_seconds)))
            return token, expires_at
        self._ticket_store[namespace][identifier] = {"token": token, "expires_at": expires_at}
        return token, expires_at

    def consume_ticket(self, namespace: str, identifier: str, token: str) -> bool:
        now = self._now()
        redis_client = self._client()
        key = f"otp_ticket:{namespace}:{identifier}"
        if redis_client:
            current = redis_client.hgetall(key)
            if not current:
                return False
            if int(current.get("expires_at", "0")) < now:
                redis_client.delete(key)
                return False
            if current.get("token") != token:
                return False
            redis_client.delete(key)
            return True
        current = self._ticket_store[namespace].get(identifier)
        if not current:
            return False
        if int(current.get("expires_at", 0)) < now:
            del self._ticket_store[namespace][identifier]
            return False
        if current.get("token") != token:
            return False
        del self._ticket_store[namespace][identifier]
        return True


otp_service = OTPService()

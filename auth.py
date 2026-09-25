"""
auth.py — JWT authentication, cookie management, and rate limiting
===================================================================
"""

import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Request

from config import (
    SECRET_KEY,
    JWT_ALGORITHM,
    JWT_EXPIRY_HOURS,
    AUTH_RATE_LIMIT_MINS,
    AUTH_RATE_LIMIT_ATTEMPTS,
)

# ── Rate limit state (per-IP) ───────────────────────────────────────────────
_auth_attempts: dict[str, list[float]] = {}


def check_auth_rate_limit(ip: str) -> None:
    """Enforce per-IP rate limiting on authentication attempts."""
    now = time.time()
    cutoff = now - (AUTH_RATE_LIMIT_MINS * 60)
    attempts = _auth_attempts.get(ip, [])
    attempts = [t for t in attempts if t > cutoff]

    if len(attempts) >= AUTH_RATE_LIMIT_ATTEMPTS:
        _auth_attempts[ip] = attempts
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts. Please try again later.",
        )

    attempts.append(now)
    _auth_attempts[ip] = attempts


def create_token(subject: str = "user") -> str:
    """Create a signed JWT with an expiry."""
    payload = {
        "sub": subject,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """Decode and verify a JWT. Raises HTTPException on failure."""
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_auth(request: Request) -> dict:
    """FastAPI dependency — rejects unauthenticated requests based on cookie."""
    token = request.cookies.get("factory_eye_token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization cookie")
    return verify_token(token)

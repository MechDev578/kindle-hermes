"""Optional PIN/Token authentication & login brute-force rate limiting for KindleHermes."""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque

from fastapi import HTTPException, Request, status

from backend.config import config

_MAX_FAILED_ATTEMPTS = 10
_LOCKOUT_WINDOW_SEC = 15 * 60
_FAILED_LOGINS: dict[str, deque[float]] = {}
_RATE_LOCK = threading.Lock()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").strip()
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_login_rate_limit(ip: str) -> None:
    now = time.monotonic()
    cutoff = now - _LOCKOUT_WINDOW_SEC
    with _RATE_LOCK:
        bucket = _FAILED_LOGINS.get(ip)
        if not bucket:
            return
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _MAX_FAILED_ATTEMPTS:
            retry_after = max(60, int(_LOCKOUT_WINDOW_SEC - (now - bucket[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )


def record_login_attempt(ip: str, success: bool) -> None:
    now = time.monotonic()
    with _RATE_LOCK:
        if success:
            _FAILED_LOGINS.pop(ip, None)
            return
        bucket = _FAILED_LOGINS.setdefault(ip, deque())
        bucket.append(now)


def reset_login_rate_limits() -> None:
    with _RATE_LOCK:
        _FAILED_LOGINS.clear()


def is_auth_enabled() -> bool:
    return bool(config.hermes_token)


def extract_token(request: Request) -> str:
    # 1. Query parameter (convenient for Kindle URLs)
    q_token = request.query_params.get("token", "").strip()
    if q_token:
        return q_token

    # 2. Custom header
    h_token = request.headers.get("X-Hermes-Token", "").strip()
    if h_token:
        return h_token

    # 3. Bearer header
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    # 4. Cookie (used by /app PWA on iOS/macOS/Windows)
    c_token = request.cookies.get("hermes_token", "").strip()
    if c_token:
        return c_token

    return ""


def verify_token(token: str) -> bool:
    if not config.hermes_token:
        return True
    if not token:
        return False
    return secrets.compare_digest(token, config.hermes_token)


def require_auth(request: Request) -> None:
    if not config.hermes_token:
        return
    token = extract_token(request)
    if not verify_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Hermes access token",
        )

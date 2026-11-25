"""Optional PIN/Token authentication for cloud-hosted KindleHermes."""

from __future__ import annotations

import secrets
from fastapi import HTTPException, Request, status

from backend.config import config


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

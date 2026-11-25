"""Google Calendar OAuth2 & Multi-Calendar API v3 integration (no heavy SDK required)."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import urllib.parse
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

import httpx

from backend.config import config
from backend.database import db

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CAL_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/calendar.readonly"

# In-memory CSRF state store: state -> (created_ts, redirect_uri)
_OAUTH_STATES: dict[str, tuple[float, str]] = {}
# Short TTL cache for merged Google Calendar events: key -> (fetched_ts, today_raw, tomorrow_raw)
_EVENTS_CACHE: dict[str, tuple[float, list[dict[str, Any]], list[dict[str, Any]]]] = {}
_EVENTS_TTL_SECONDS = 120.0


def clear_google_events_cache() -> None:
    _EVENTS_CACHE.clear()


def _prune_states() -> None:
    cutoff = time.time() - 600.0
    expired = [k for k, (ts, _) in _OAUTH_STATES.items() if ts < cutoff]
    for k in expired:
        _OAUTH_STATES.pop(k, None)


def get_public_status(redirect_uri: str) -> dict[str, Any]:
    oauth = db.get_google_oauth()
    return {
        "has_credentials": oauth["has_credentials"],
        "client_id_preview": (oauth["client_id"][:14] + "...") if len(oauth["client_id"]) > 18 else oauth["client_id"],
        "connected": oauth["connected"],
        "account_email": oauth["account_email"],
        "calendars": oauth["calendars"],
        "selected_ids": oauth["selected_ids"],
        "redirect_uri": config.google_redirect_uri or redirect_uri,
    }


def build_authorization_url(redirect_uri: str) -> str:
    oauth = db.get_google_oauth()
    if not oauth["has_credentials"]:
        raise ValueError("Google OAuth Client ID and Client Secret must be configured first.")

    effective_redirect = config.google_redirect_uri or redirect_uri
    _prune_states()
    state = secrets.token_urlsafe(24)
    _OAUTH_STATES[state] = (time.time(), effective_redirect)

    params = {
        "client_id": oauth["client_id"],
        "redirect_uri": effective_redirect,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code_for_tokens(code: str, state: str) -> dict[str, Any]:
    _prune_states()
    state_entry = _OAUTH_STATES.pop(state, None)
    if not state_entry:
        raise ValueError("Invalid or expired OAuth state. Please start Connect Google Calendar again.")
    _ts, redirect_uri = state_entry

    oauth = db.get_google_oauth()
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": oauth["client_id"],
                "client_secret": oauth["client_secret"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code >= 400:
            raise ValueError(f"Google token exchange failed: {resp.text[:240]}")
        tok = resp.json()

    access_token = tok.get("access_token", "")
    refresh_token = tok.get("refresh_token") or oauth.get("refresh_token", "")
    expires_in = int(tok.get("expires_in", 3500))
    if not access_token:
        raise ValueError("Google returned no access token.")

    db.update_google_oauth(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + max(60, expires_in - 60),
        }
    )

    calendars = await refresh_calendar_list(auto_select_defaults=True)
    # Switch out of mock data automatically if user just connected Google Calendar
    db.update_settings({"use_mock_data": False})
    clear_google_events_cache()
    return {"connected": True, "calendars": calendars}


async def _ensure_valid_access_token() -> str:
    oauth = db.get_google_oauth()
    if not oauth["connected"]:
        raise RuntimeError("Google Calendar is not connected")

    if oauth["access_token"] and time.time() < oauth["expires_at"]:
        return oauth["access_token"]

    if not oauth["refresh_token"] or not oauth["has_credentials"]:
        raise RuntimeError("Google Calendar session expired; reconnect in /app")

    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": oauth["client_id"],
                "client_secret": oauth["client_secret"],
                "refresh_token": oauth["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Google token refresh failed ({resp.status_code}): {resp.text[:160]}")
        tok = resp.json()

    access_token = tok.get("access_token", "")
    expires_in = int(tok.get("expires_in", 3500))
    db.update_google_oauth(
        {
            "access_token": access_token,
            "expires_at": time.time() + max(60, expires_in - 60),
        }
    )
    return access_token


async def refresh_calendar_list(auto_select_defaults: bool = False) -> list[dict[str, Any]]:
    token = await _ensure_valid_access_token()
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(
            f"{GOOGLE_CAL_BASE}/users/me/calendarList",
            headers={"Authorization": f"Bearer {token}"},
            params={"minAccessRole": "reader"},
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

    calendars: list[dict[str, Any]] = []
    primary_email = ""
    for item in items:
        cal_id = item.get("id", "")
        if not cal_id:
            continue
        is_primary = bool(item.get("primary"))
        if is_primary:
            primary_email = cal_id
        calendars.append(
            {
                "id": cal_id,
                "summary": (item.get("summaryOverride") or item.get("summary") or cal_id).strip(),
                "primary": is_primary,
                "selected_in_google": bool(item.get("selected", True)),
                "access_role": item.get("accessRole", "reader"),
            }
        )
    calendars.sort(key=lambda c: (not c["primary"], c["summary"].lower()))

    oauth = db.get_google_oauth()
    existing_ids = set(oauth.get("selected_ids") or [])
    valid_ids = {c["id"] for c in calendars}
    kept_ids = [cid for cid in oauth.get("selected_ids") or [] if cid in valid_ids]

    if auto_select_defaults or not kept_ids:
        # Select calendars the user has checked in Google Calendar (excluding hidden ones), or primary
        default_selected = [c["id"] for c in calendars if c["primary"] or c["selected_in_google"]]
        if not default_selected and calendars:
            default_selected = [calendars[0]["id"]]
        selected_ids = default_selected
    else:
        selected_ids = kept_ids

    db.update_google_oauth(
        {
            "account_email": primary_email or oauth.get("account_email", ""),
            "calendars": calendars,
            "selected_ids": selected_ids,
        }
    )
    clear_google_events_cache()
    return calendars


def parse_google_event_item(
    item: dict[str, Any],
    calendar_name: str,
    tz: Any,
) -> dict[str, Any] | None:
    """Convert a Google Calendar v3 event object into KindleHermes's normalized event dict."""
    if item.get("status") == "cancelled":
        return None
    start_obj = item.get("start") or {}
    end_obj = item.get("end") or {}
    title = (item.get("summary") or "Untitled Event").strip()
    location = (item.get("location") or "").strip()

    if "dateTime" in start_obj:
        start_dt = datetime.fromisoformat(start_obj["dateTime"].replace("Z", "+00:00")).astimezone(tz)
        end_raw = end_obj.get("dateTime")
        if end_raw:
            end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(tz)
        else:
            end_dt = start_dt + timedelta(hours=1)
        all_day = False
    elif "date" in start_obj:
        s_day = date.fromisoformat(start_obj["date"])
        e_day = date.fromisoformat(end_obj.get("date", start_obj["date"]))
        # Google Calendar all-day end date is exclusive (e.g., Nov 25 -> Nov 26 means only Nov 25)
        inclusive_end_day = max(s_day, e_day - timedelta(days=1))
        start_dt = datetime.combine(s_day, dtime(0, 0), tzinfo=tz)
        end_dt = datetime.combine(inclusive_end_day, dtime(23, 59), tzinfo=tz)
        all_day = True
    else:
        return None

    return {
        "title": title,
        "location": location,
        "category": calendar_name,
        "start": start_dt,
        "end": end_dt,
        "all_day": all_day,
    }


async def fetch_google_events_for_today_and_tomorrow(
    now: datetime,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    oauth = db.get_google_oauth()
    selected_ids: list[str] = oauth.get("selected_ids") or []
    if not selected_ids:
        selected_ids = ["primary"]

    cal_names = {c["id"]: c["summary"] for c in oauth.get("calendars") or []}
    today = now.date()
    tomorrow = today + timedelta(days=1)

    cache_key = f"{today.isoformat()}|{','.join(sorted(selected_ids))}"
    if not force_refresh:
        cached = _EVENTS_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _EVENTS_TTL_SECONDS:
            return cached[1], cached[2]

    token = await _ensure_valid_access_token()
    tz = now.tzinfo
    window_start = datetime.combine(today, dtime(0, 0), tzinfo=tz)
    window_end = datetime.combine(tomorrow + timedelta(days=1), dtime(0, 0), tzinfo=tz)

    async def fetch_one(client: httpx.AsyncClient, cal_id: str) -> list[dict[str, Any]]:
        encoded = urllib.parse.quote(cal_id, safe="")
        resp = await client.get(
            f"{GOOGLE_CAL_BASE}/calendars/{encoded}/events",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "timeMin": window_start.isoformat(),
                "timeMax": window_end.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 50,
            },
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        cal_label = cal_names.get(cal_id, "Google")
        parsed: list[dict[str, Any]] = []
        for item in resp.json().get("items", []):
            ev = parse_google_event_item(item, cal_label, tz)
            if ev is not None:
                parsed.append(ev)
        return parsed

    async with httpx.AsyncClient(timeout=12.0) as client:
        batches = await asyncio.gather(*(fetch_one(client, cid) for cid in selected_ids))

    merged: list[dict[str, Any]] = [ev for batch in batches for ev in batch]
    merged.sort(key=lambda e: (not e["all_day"], e["start"]))

    today_raw: list[dict[str, Any]] = []
    tomorrow_raw: list[dict[str, Any]] = []
    for ev in merged:
        s_day = ev["start"].date()
        e_day = ev["end"].date()
        if s_day <= today <= e_day:
            today_raw.append(ev)
        if s_day <= tomorrow <= e_day:
            tomorrow_raw.append(ev)

    _EVENTS_CACHE[cache_key] = (time.time(), today_raw, tomorrow_raw)
    return today_raw, tomorrow_raw

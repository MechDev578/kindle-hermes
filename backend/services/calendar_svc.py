"""Calendar service: .ics parser with RRULE expansion + realistic mock fallback + NOW/NEXT context."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, time as dtime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events

from backend.database import db
from backend.services import google_cal_svc

logger = logging.getLogger(__name__)

_ICS_CACHE: dict[str, tuple[float, str]] = {}
_ICS_TTL_SECONDS = 300.0


def clear_ics_cache() -> None:
    _ICS_CACHE.clear()


def get_local_now(tz_name: str, sim_time: str | None = None) -> datetime:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    if sim_time:
        try:
            parts = sim_time.strip().split(":")
            hour = max(0, min(23, int(parts[0])))
            minute = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
            now = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except Exception:
            pass
    return now


def _format_duration(minutes: int) -> str:
    if minutes <= 0:
        return "now"
    if minutes < 60:
        return f"{minutes}m"
    hours, rem = divmod(minutes, 60)
    if rem == 0:
        return f"{hours}h"
    return f"{hours}h {rem}m"


def _build_mock_events(now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tz = now.tzinfo
    today = now.date()
    tomorrow = today + timedelta(days=1)

    def make(d: date, sh: int, sm: int, eh: int, em: int, title: str, location: str = "", category: str = "Work") -> dict[str, Any]:
        start = datetime.combine(d, dtime(sh, sm), tzinfo=tz)
        end = datetime.combine(d, dtime(eh, em), tzinfo=tz)
        return {
            "title": title,
            "location": location,
            "category": category,
            "start": start,
            "end": end,
            "all_day": False,
        }

    today_events = [
        make(today, 9, 0, 9, 30, "Morning Standup", "Zoom · Core Team", "Team"),
        make(today, 10, 30, 11, 30, "Product Architecture Review", "Room 3B / Meet", "Work"),
        make(today, 13, 0, 13, 45, "Lunch & Walk Outside", "Park Loop", "Personal"),
        make(today, 14, 0, 15, 30, "Deep Work: KindleHermes OS", "Desk · Focus Mode", "Focus"),
        make(today, 16, 0, 16, 45, "Design Sync with Alex", "Huddle", "Work"),
        make(today, 18, 30, 19, 30, "Strength & Mobility Session", "Local Gym", "Health"),
        make(today, 20, 30, 21, 15, "Paperwhite Reading Hour", "Offline", "Reading"),
    ]

    tomorrow_events = [
        make(tomorrow, 9, 30, 10, 0, "Weekly Planning & Metrics", "Main Desk", "Work"),
        make(tomorrow, 11, 0, 12, 30, "Deep Work: API & Deployment", "Focus Block", "Focus"),
        make(tomorrow, 15, 0, 15, 45, "1:1 Catch-Up with Maya", "Coffee Shop", "Personal"),
        make(tomorrow, 19, 0, 20, 30, "Dinner with Friends", "Mitte", "Social"),
    ]
    return today_events, tomorrow_events


async def _fetch_ics_text(ics_url: str) -> str:
    now_ts = time.time()
    cached = _ICS_CACHE.get(ics_url)
    if cached and (now_ts - cached[0]) < _ICS_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(ics_url)
        resp.raise_for_status()
        text = resp.text
    _ICS_CACHE[ics_url] = (now_ts, text)
    return text


def _normalize_dt(raw_dt: Any, tz: Any) -> tuple[datetime, bool]:
    if isinstance(raw_dt, datetime):
        if raw_dt.tzinfo is None:
            return raw_dt.replace(tzinfo=tz), False
        return raw_dt.astimezone(tz), False
    # date-only all-day event
    return datetime.combine(raw_dt, dtime(0, 0), tzinfo=tz), True


def _parse_ics_for_range(ics_text: str, start_day: date, end_day: date, tz: Any) -> list[dict[str, Any]]:
    cal = icalendar.Calendar.from_ical(ics_text)
    events = recurring_ical_events.of(cal).between(start_day, end_day + timedelta(days=1))
    parsed: list[dict[str, Any]] = []
    for comp in events:
        summary = str(comp.get("SUMMARY", "Untitled Event")).strip()
        location = str(comp.get("LOCATION", "") or "").strip()
        dtstart_prop = comp.get("DTSTART")
        if not dtstart_prop:
            continue
        start_dt, all_day = _normalize_dt(dtstart_prop.dt, tz)

        dtend_prop = comp.get("DTEND")
        if dtend_prop:
            end_dt, _ = _normalize_dt(dtend_prop.dt, tz)
        else:
            duration_prop = comp.get("DURATION")
            if duration_prop:
                end_dt = start_dt + duration_prop.dt
            else:
                end_dt = start_dt + (timedelta(days=1) if all_day else timedelta(hours=1))

        if all_day:
            # Make inclusive end-of-day for display
            end_dt = max(start_dt + timedelta(hours=23, minutes=59), end_dt - timedelta(seconds=1))

        parsed.append(
            {
                "title": summary,
                "location": location,
                "category": "Calendar",
                "start": start_dt,
                "end": end_dt,
                "all_day": all_day,
            }
        )
    parsed.sort(key=lambda e: (not e["all_day"], e["start"]))
    return parsed


def _serialize_event(ev: dict[str, Any], now: datetime, status: str) -> dict[str, Any]:
    start: datetime = ev["start"]
    end: datetime = ev["end"]
    all_day = bool(ev.get("all_day", False))

    if all_day:
        time_range = "ALL DAY"
        start_label = "ALL DAY"
        end_label = ""
    else:
        start_label = start.strftime("%H:%M")
        end_label = end.strftime("%H:%M")
        time_range = f"{start_label} – {end_label}"

    remaining_min = max(0, int((end - now).total_seconds() // 60)) if status == "now" else 0
    total_min = max(1, int((end - start).total_seconds() // 60))
    elapsed_min = max(0, int((now - start).total_seconds() // 60)) if status == "now" else 0
    progress_pct = min(100, max(0, int((elapsed_min / total_min) * 100))) if status == "now" else 0
    starts_in_min = max(0, int((start - now).total_seconds() // 60)) if start > now else 0

    return {
        "title": ev["title"],
        "location": ev.get("location", ""),
        "category": ev.get("category", "Event"),
        "all_day": all_day,
        "start_iso": start.isoformat(),
        "end_iso": end.isoformat(),
        "start_label": start_label,
        "end_label": end_label,
        "time_range": time_range,
        "status": status,
        "remaining_min": remaining_min,
        "remaining_label": f"{_format_duration(remaining_min)} left" if status == "now" else "",
        "starts_in_min": starts_in_min,
        "starts_in_label": f"in {_format_duration(starts_in_min)}" if starts_in_min > 0 else "starting",
        "progress_pct": progress_pct,
    }


async def get_calendar_context(
    settings: dict[str, Any],
    now: datetime,
    force_refresh: bool = False,
) -> dict[str, Any]:
    use_mock = settings.get("use_mock_data", True)
    ics_url = (settings.get("calendar_ics_url") or "").strip()
    source = "mock"
    error_note = ""

    today = now.date()
    tomorrow = today + timedelta(days=1)

    today_raw: list[dict[str, Any]] = []
    tomorrow_raw: list[dict[str, Any]] = []

    google_oauth = db.get_google_oauth()
    ics_urls = [u.strip() for u in ics_url.replace(",", "\n").splitlines() if u.strip()]

    if not use_mock and google_oauth.get("connected"):
        try:
            today_raw, tomorrow_raw = await google_cal_svc.fetch_google_events_for_today_and_tomorrow(
                now, force_refresh=force_refresh
            )
            source = "google"
        except Exception as exc:
            logger.warning("Google Calendar fetch failed (%s), falling back to mock", exc)
            error_note = "Google Calendar sync error — reconnect in /app"
            today_raw, tomorrow_raw = _build_mock_events(now)
            source = "mock_fallback"
    elif not use_mock and ics_urls:
        try:
            all_two_days: list[dict[str, Any]] = []
            for url in ics_urls:
                if force_refresh:
                    _ICS_CACHE.pop(url, None)
                ics_text = await _fetch_ics_text(url)
                all_two_days.extend(_parse_ics_for_range(ics_text, today, tomorrow, now.tzinfo))
            all_two_days.sort(key=lambda e: (not e["all_day"], e["start"]))
            for ev in all_two_days:
                ev_start_date = ev["start"].date()
                ev_end_date = ev["end"].date()
                if ev_start_date <= today <= ev_end_date:
                    today_raw.append(ev)
                if ev_start_date <= tomorrow <= ev_end_date:
                    tomorrow_raw.append(ev)
            source = "ics"
        except Exception as exc:
            logger.warning("ICS calendar fetch failed (%s), falling back to mock", exc)
            error_note = "ICS unreachable — showing demo schedule"
            today_raw, tomorrow_raw = _build_mock_events(now)
            source = "mock_fallback"
    else:
        today_raw, tomorrow_raw = _build_mock_events(now)

    # Classify today's events relative to `now`
    current_ev_raw = None
    next_ev_raw = None
    all_today_serialized: list[dict[str, Any]] = []
    upcoming_today_serialized: list[dict[str, Any]] = []

    for ev in today_raw:
        if ev["all_day"]:
            status = "upcoming"
        elif ev["start"] <= now < ev["end"]:
            status = "now"
            if current_ev_raw is None:
                current_ev_raw = ev
        elif ev["start"] > now:
            if next_ev_raw is None:
                next_ev_raw = ev
                status = "next"
            else:
                status = "upcoming"
        else:
            status = "past"

        item = _serialize_event(ev, now, status)
        all_today_serialized.append(item)
        if status in ("next", "upcoming"):
            upcoming_today_serialized.append(item)

    current_serialized = (
        next((e for e in all_today_serialized if e["status"] == "now"), None)
    )
    next_serialized = (
        next((e for e in all_today_serialized if e["status"] == "next"), None)
    )
    tomorrow_serialized = [_serialize_event(ev, now, "upcoming") for ev in tomorrow_raw]

    if current_serialized:
        schedule_state = "in_event"
    elif next_serialized:
        schedule_state = "between_events"
    else:
        schedule_state = "day_complete"

    return {
        "source": source,
        "error_note": error_note,
        "state": schedule_state,
        "current_event": current_serialized,
        "next_event": next_serialized,
        "upcoming_today": upcoming_today_serialized,
        "all_today": all_today_serialized,
        "tomorrow_events": tomorrow_serialized,
        "today_count": len(all_today_serialized),
        "remaining_count": len(upcoming_today_serialized) + (1 if current_serialized else 0),
    }


def reclassify_calendar_context(cal_data: dict[str, Any], target_now: datetime) -> dict[str, Any]:
    """Re-classify an already-fetched calendar context for another timestamp today (zero network calls)."""
    all_today_serialized: list[dict[str, Any]] = []
    upcoming_today_serialized: list[dict[str, Any]] = []
    current_found = False
    next_found = False

    for item in cal_data.get("all_today") or []:
        try:
            start = datetime.fromisoformat(item["start_iso"])
            end = datetime.fromisoformat(item["end_iso"])
        except Exception:
            continue
        raw = {
            "title": item.get("title", ""),
            "location": item.get("location", ""),
            "category": item.get("category", "Event"),
            "all_day": bool(item.get("all_day", False)),
            "start": start,
            "end": end,
        }
        if raw["all_day"]:
            status = "upcoming"
        elif start <= target_now < end:
            status = "now"
            current_found = True
        elif start > target_now:
            if not next_found:
                next_found = True
                status = "next"
            else:
                status = "upcoming"
        else:
            status = "past"

        serialized = _serialize_event(raw, target_now, status)
        all_today_serialized.append(serialized)
        if status in ("next", "upcoming"):
            upcoming_today_serialized.append(serialized)

    current_serialized = next((e for e in all_today_serialized if e["status"] == "now"), None) if current_found else None
    next_serialized = next((e for e in all_today_serialized if e["status"] == "next"), None)

    if current_serialized:
        schedule_state = "in_event"
    elif next_serialized:
        schedule_state = "between_events"
    else:
        schedule_state = "day_complete"

    return {
        "source": cal_data.get("source", "mock"),
        "error_note": cal_data.get("error_note", ""),
        "state": schedule_state,
        "current_event": current_serialized,
        "next_event": next_serialized,
        "upcoming_today": upcoming_today_serialized,
        "all_today": all_today_serialized,
        "tomorrow_events": cal_data.get("tomorrow_events") or [],
        "today_count": len(all_today_serialized),
        "remaining_count": len(upcoming_today_serialized) + (1 if current_serialized else 0),
    }


def compute_lockscreen_time_slots(
    cal_data: dict[str, Any],
    now: datetime,
    max_slots: int = 10,
) -> list[dict[str, Any]]:
    """Compute time-sliced intervals covering today's upcoming event transitions for offline lock screens."""
    transitions: list[datetime] = [now]
    for ev in cal_data.get("all_today") or []:
        if ev.get("all_day"):
            continue
        try:
            start = datetime.fromisoformat(ev["start_iso"])
            end = datetime.fromisoformat(ev["end_iso"])
        except Exception:
            continue
        if start > now + timedelta(seconds=60):
            transitions.append(start)
        if end > now + timedelta(seconds=60):
            transitions.append(end)

    transitions.sort()
    deduped: list[datetime] = []
    for ts in transitions:
        if not deduped or (ts - deduped[-1]).total_seconds() >= 120:
            deduped.append(ts)

    deduped = deduped[:max_slots]
    end_of_day = datetime.combine(now.date() + timedelta(days=1), dtime(5, 0), tzinfo=now.tzinfo)

    slots: list[dict[str, Any]] = []
    for idx, start_dt in enumerate(deduped):
        end_dt = deduped[idx + 1] if idx + 1 < len(deduped) else max(end_of_day, start_dt + timedelta(hours=6))
        slots.append(
            {
                "id": f"slot_{idx}",
                "dt": start_dt,
                "start_unix": int(start_dt.timestamp()),
                "end_unix": int(end_dt.timestamp()),
                "label": f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}",
            }
        )
    return slots

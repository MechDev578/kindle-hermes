"""Plug-and-play Hermes OS Module Registry & Unified Action Dispatcher.

To add a new app in v2+ (e.g. Habits, Quick Notes, Apple Music, Reading Stats):
1. Define a HermesModule entry in MODULES below (with an inline monochrome SVG icon).
2. Add its screen partial in frontend/templates/kindle_shell.html.
3. Add any custom action handler in dispatch_action().
It automatically appears on the Kindle Home Screen and in /app's Module Manager.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.database import db
from backend.modules.base import HermesModule
from backend.services import calendar_svc, gemini_svc, google_cal_svc, pomodoro_svc, weather_svc


def _icon(body: str) -> str:
    return (
        '<svg width="46" height="46" viewBox="0 0 32 32" '
        'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        f"{body}</svg>"
    )


def _today_summary(bundle: dict[str, Any]) -> tuple[str, str]:
    cal = bundle["calendar"]
    tasks = bundle["tasks"]
    open_tasks = sum(1 for t in tasks if not t["completed"])
    if cal["current_event"]:
        title = cal["current_event"]["title"]
        short = title if len(title) <= 28 else (title[:27].rstrip() + "...")
        return f"Now: {short}", "LIVE"
    if cal["next_event"]:
        return f"Next {cal['next_event']['start_label']} · {open_tasks} tasks", f"{open_tasks} OPEN"
    return f"All clear · {open_tasks} tasks", f"{open_tasks} OPEN"


def _calendar_summary(bundle: dict[str, Any]) -> tuple[str, str]:
    cal = bundle["calendar"]
    if cal["current_event"]:
        return cal["current_event"]["time_range"], "NOW"
    if cal["next_event"]:
        return f"Next: {cal['next_event']['start_label']}", f"{cal['remaining_count']} LEFT"
    return f"{len(cal['tomorrow_events'])} events tomorrow", "CLEAR"


def _planner_summary(bundle: dict[str, Any]) -> tuple[str, str]:
    tasks = bundle["tasks"]
    total = len(tasks)
    done = sum(1 for t in tasks if t["completed"])
    left = total - done
    return f"{done} of {total} completed", f"{left} LEFT" if left else "DONE"


def _pomodoro_summary(bundle: dict[str, Any]) -> tuple[str, str]:
    pomo = bundle["pomodoro"]
    if pomo["is_running"]:
        return f"{pomo['mode_label']} running", pomo["remaining_label"]
    if pomo["status"] == "paused":
        return f"Paused · {pomo['mode_label']}", pomo["remaining_label"]
    return f"{pomo['completed_sessions']} sessions today", pomo["remaining_label"]


def _weather_summary(bundle: dict[str, Any]) -> tuple[str, str]:
    w = bundle["weather"]
    return f"{w['condition']} · {w['rain_prob_6h']}% rain", f"{w['temp']}°C"


def _briefing_summary(bundle: dict[str, Any]) -> tuple[str, str]:
    b = bundle["briefing"]
    term = (b.get("concept_of_day") or {}).get("term", "Daily Pulse")
    return f"Concept: {term[:22]}", "DAILY"


MODULES: list[HermesModule] = [
    HermesModule(
        id="today",
        title="Today",
        tagline="Unified live dashboard",
        nav_order=1,
        supports_lockscreen=True,
        lockscreen_slide="smart",
        summary_builder=_today_summary,
        icon_svg=_icon(
            '<rect x="4" y="4" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.4"/>'
            '<rect x="18" y="4" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.4"/>'
            '<rect x="4" y="18" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.4"/>'
            '<rect x="18" y="18" width="10" height="10" fill="currentColor"/>'
        ),
    ),
    HermesModule(
        id="calendar",
        title="Calendar",
        tagline="Today & tomorrow agenda",
        nav_order=2,
        supports_lockscreen=True,
        lockscreen_slide="calendar",
        summary_builder=_calendar_summary,
        icon_svg=_icon(
            '<rect x="4" y="6" width="24" height="22" fill="none" stroke="currentColor" stroke-width="2.4"/>'
            '<path d="M4 13h24M10 3v6M22 3v6" stroke="currentColor" stroke-width="2.4"/>'
            '<rect x="9" y="17" width="5" height="5" fill="currentColor"/>'
        ),
    ),
    HermesModule(
        id="planner",
        title="Planner",
        tagline="Priorities & focus checklist",
        nav_order=3,
        supports_lockscreen=True,
        lockscreen_slide="planning",
        summary_builder=_planner_summary,
        icon_svg=_icon(
            '<path d="M5 9l3 3 5-5M16 10h11M5 17l3 3 5-5M16 18h11M6 25h6M16 25h11" '
            'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="square"/>'
        ),
    ),
    HermesModule(
        id="pomodoro",
        title="Pomodoro",
        tagline="E-ink focus timer",
        nav_order=4,
        supports_lockscreen=False,
        summary_builder=_pomodoro_summary,
        icon_svg=_icon(
            '<circle cx="16" cy="17" r="10" fill="none" stroke="currentColor" stroke-width="2.4"/>'
            '<path d="M12 4h8M16 4v3M16 17v-5M16 17l4 3" '
            'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="square"/>'
        ),
    ),
    HermesModule(
        id="weather",
        title="Weather",
        tagline="Hourly & 5-day forecast",
        nav_order=5,
        supports_lockscreen=True,
        lockscreen_slide="weather",
        summary_builder=_weather_summary,
        icon_svg=_icon(
            '<circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2.2"/>'
            '<path d="M12 4v2.5M4 12h2.5M6.4 6.4l1.8 1.8" stroke="currentColor" stroke-width="2.2"/>'
            '<path d="M10 26h14a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.5-1.5A4.2 4.2 0 0 0 10 26z" '
            'fill="#FFFFFF" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/>'
        ),
    ),
    HermesModule(
        id="briefing",
        title="Briefing",
        tagline="Morning market & concept",
        nav_order=6,
        supports_lockscreen=False,
        summary_builder=_briefing_summary,
        icon_svg=_icon(
            '<rect x="4" y="5" width="24" height="22" fill="none" stroke="currentColor" stroke-width="2.4"/>'
            '<path d="M8 10h16M8 15h9M8 20h9M20 15h4v7h-4z" '
            'fill="none" stroke="currentColor" stroke-width="2.2"/>'
        ),
    ),
]

MODULES_BY_ID: dict[str, HermesModule] = {m.id: m for m in MODULES}

VALID_SCREENS = {
    "home",
    "today",
    "calendar",
    "calendar_tomorrow",
    "planner",
    "pomodoro",
    "weather",
    "briefing",
    "lockscreen_smart",
    "lockscreen_calendar",
    "lockscreen_planning",
    "lockscreen_weather",
}


def list_enabled_modules(enabled_ids: list[str] | None = None) -> list[HermesModule]:
    if not enabled_ids:
        return sorted(MODULES, key=lambda m: m.nav_order)
    allowed = set(enabled_ids)
    selected = [m for m in MODULES if m.id in allowed]
    return sorted(selected or MODULES, key=lambda m: m.nav_order)


def resolve_lockscreen_slide(settings: dict[str, Any], now: datetime, requested: str = "auto", advance: bool = False) -> str:
    valid_slides = {"smart", "calendar", "planning", "weather"}
    if requested in valid_slides:
        return requested

    mode = settings.get("lockscreen_mode", "smart")
    if mode == "smart":
        return "smart"

    if mode == "time_of_day":
        hour = now.hour
        if 5 <= hour < 9:
            return "weather"
        if 9 <= hour < 19:
            return "smart"
        return "planning"

    # mode == "cycle"
    slides = [s for s in settings.get("lockscreen_slides", ["smart"]) if s in valid_slides] or ["smart"]
    if advance:
        return db.advance_lockscreen_cycle()
    idx = settings.get("lockscreen_cycle_index", 0) % len(slides)
    return slides[idx]


async def build_data_bundle(
    settings: dict[str, Any],
    now: datetime,
    force_refresh: bool = False,
    consume_pomo_alert: bool = False,
) -> dict[str, Any]:
    cal_data = await calendar_svc.get_calendar_context(settings, now, force_refresh=force_refresh)
    weather_data = await weather_svc.get_weather_context(settings, now, force_refresh=force_refresh)
    # Keep briefing fast during normal sync if today's Gemini briefing is already cached;
    # explicit "briefing:refresh" regenerates it on demand.
    briefing_data = await gemini_svc.get_daily_briefing(settings, now, force_refresh=False)
    tasks_data = db.list_tasks()
    pomo_data = pomodoro_svc.get_pomodoro_view(consume_alert=consume_pomo_alert)

    bundle: dict[str, Any] = {
        "now": now,
        "date_long": now.strftime("%A, %b %d"),
        "date_short": now.strftime("%a, %b %d").upper(),
        "time_str": now.strftime("%H:%M"),
        "settings": settings,
        "calendar": cal_data,
        "weather": weather_data,
        "briefing": briefing_data,
        "tasks": tasks_data,
        "pomodoro": pomo_data,
    }

    enabled = list_enabled_modules(settings.get("enabled_modules"))
    bundle["tiles"] = [m.to_tile(bundle) for m in enabled]
    bundle["all_modules"] = [
        {
            "id": m.id,
            "title": m.title,
            "tagline": m.tagline,
            "enabled": m.id in set(settings.get("enabled_modules") or []),
            "supports_lockscreen": m.supports_lockscreen,
        }
        for m in sorted(MODULES, key=lambda item: item.nav_order)
    ]
    return bundle


async def dispatch_action(
    action: str,
    current_screen: str,
    settings: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Unified interactive action handler used by Native FBInk Client, /kindle, and /preview."""
    act = (action or "").strip()
    next_screen = current_screen if current_screen in VALID_SCREENS else "home"
    force_refresh = False
    should_exit = False
    message = ""

    if act == "system:exit":
        should_exit = True
        message = "Returning to Kindle Home"

    elif act.startswith("nav:"):
        target = act.split(":", 1)[1].strip()
        if target in VALID_SCREENS:
            next_screen = target

    elif act.startswith("task:toggle:"):
        try:
            task_id = int(act.split(":", 2)[2])
            db.toggle_task(task_id)
            message = f"Toggled task {task_id}"
        except ValueError:
            pass

    elif act == "task:clear_done":
        db.clear_completed_tasks()
        message = "Cleared completed tasks"

    elif act == "pomo:toggle":
        pomodoro_svc.toggle_start_pause()
        message = "Toggled Pomodoro"

    elif act == "pomo:reset":
        pomodoro_svc.reset_pomodoro()
        message = "Reset Pomodoro"

    elif act.startswith("pomo:mode:"):
        mode = act.split(":", 2)[2].strip()
        pomodoro_svc.set_pomodoro_mode(mode)
        message = f"Pomodoro mode: {mode}"

    elif act == "briefing:refresh":
        await gemini_svc.get_daily_briefing(settings, now, force_refresh=True)
        message = "Refreshed briefing"

    elif act == "sync:all":
        force_refresh = True
        calendar_svc.clear_ics_cache()
        google_cal_svc.clear_google_events_cache()
        weather_svc.clear_weather_cache()
        # Actively re-fetch fresh calendar & weather right now so both /kindle and native client see live updates immediately
        await calendar_svc.get_calendar_context(settings, now, force_refresh=True)
        await weather_svc.get_weather_context(settings, now, force_refresh=True)
        message = "Synced calendar, weather & tasks"

    elif act == "lockscreen:next":
        slide = db.advance_lockscreen_cycle()
        next_screen = f"lockscreen_{slide}"
        message = f"Slide: {slide}"

    pomo = pomodoro_svc.get_pomodoro_view(consume_alert=False)
    refresh_after_sec = pomo["next_tick_sec"] if pomo["is_running"] else 300

    return {
        "ok": True,
        "action": act,
        "next_screen": next_screen,
        "force_refresh": force_refresh,
        "exit": should_exit,
        "invert_flash": pomo["alert_flash"],
        "refresh_after_sec": refresh_after_sec,
        "message": message,
    }

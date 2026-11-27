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
from backend.services import (
    calendar_svc,
    gemini_svc,
    german_svc,
    google_cal_svc,
    google_tasks_svc,
    pomodoro_svc,
    weather_svc,
)


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


def _deutsch_summary(bundle: dict[str, Any]) -> tuple[str, str]:
    g = bundle.get("german") or {}
    m = g.get("mastery") or {}
    due = int(m.get("due_count") or 0)
    cefr = m.get("cefr_label") or "A2.0"
    total = int(m.get("total") or 180)
    return f"Sprachindex {total}/1000 · {due} due", cefr


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
    HermesModule(
        id="deutsch",
        title="Deutsch",
        tagline="A2→C2 FSRS, speeches & slang",
        nav_order=7,
        supports_lockscreen=True,
        lockscreen_slide="deutsch",
        summary_builder=_deutsch_summary,
        icon_svg=_icon(
            '<path d="M4 6h10a3 3 0 0 1 2 1 3 3 0 0 1 2-1h10v20H18a3 3 0 0 0-2 1 3 3 0 0 0-2-1H4z" '
            'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/>'
            '<path d="M16 7v20M8 12h4M8 17h4M20 12h4M20 17h4" stroke="currentColor" stroke-width="2.2"/>'
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
    "planner_list_0",
    "planner_list_1",
    "planner_list_2",
    "pomodoro",
    "weather",
    "briefing",
    "deutsch",
    "deutsch_drill",
    "deutsch_classic",
    "deutsch_classic_parallel",
    "deutsch_classic_xray",
    "deutsch_read",
    "deutsch_read_parallel",
    "deutsch_root",
    "lockscreen_smart",
    "lockscreen_calendar",
    "lockscreen_planning",
    "lockscreen_weather",
    "lockscreen_deutsch",
}

OFFLINE_SCREENS = [
    "home",
    "today",
    "calendar",
    "calendar_tomorrow",
    "planner",
    "pomodoro",
    "weather",
    "briefing",
    "deutsch",
    "deutsch_read",
    "deutsch_read_parallel",
    "deutsch_root",
]


def list_enabled_modules(enabled_ids: list[str] | None = None) -> list[HermesModule]:
    if not enabled_ids:
        return sorted(MODULES, key=lambda m: m.nav_order)
    allowed = set(enabled_ids)
    selected = [m for m in MODULES if m.id in allowed]
    return sorted(selected or MODULES, key=lambda m: m.nav_order)


def resolve_lockscreen_slide(settings: dict[str, Any], now: datetime, requested: str = "auto", advance: bool = False) -> str:
    valid_slides = {"smart", "calendar", "planning", "weather", "deutsch"}
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
    german_data = await german_svc.get_german_context(settings, now, force_refresh=False)
    tasks_ctx = await google_tasks_svc.get_tasks_context(settings, now, force_refresh=force_refresh)
    tasks_data = tasks_ctx["tasks"]
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
        "german": german_data,
        "tasks": tasks_data,
        "tasks_meta": tasks_ctx,
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
            await google_tasks_svc.toggle_task_two_way(task_id, settings=settings)
            message = f"Toggled task {task_id}"
        except ValueError:
            pass

    elif act.startswith("task:set:"):
        try:
            parts = act.split(":")
            task_id = int(parts[2])
            completed = parts[3].strip() in ("1", "true", "yes")
            await google_tasks_svc.set_task_completed_two_way(task_id, completed, settings=settings)
            message = f"Set task {task_id} completed={completed}"
        except (ValueError, IndexError):
            pass

    elif act == "task:clear_done":
        list_id = ""
        if current_screen.startswith("planner_list_"):
            try:
                idx = int(current_screen.split("_")[-1])
                oauth = db.get_google_oauth()
                sel = set(oauth.get("selected_task_list_ids") or [])
                active_lists = [tl for tl in (oauth.get("task_lists") or []) if not sel or tl["id"] in sel]
                if 0 <= idx < len(active_lists):
                    list_id = active_lists[idx]["id"]
            except ValueError:
                pass
        await google_tasks_svc.clear_completed_two_way(list_id=list_id, settings=settings)
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
        google_tasks_svc.clear_google_tasks_cache()
        weather_svc.clear_weather_cache()
        # Actively re-fetch fresh calendar, tasks & weather right now so both /kindle and native client see live updates immediately
        await calendar_svc.get_calendar_context(settings, now, force_refresh=True)
        await google_tasks_svc.get_tasks_context(settings, now, force_refresh=True)
        await weather_svc.get_weather_context(settings, now, force_refresh=True)
        message = "Synced calendar, weather & tasks"

    elif act == "lockscreen:next":
        slide = db.advance_lockscreen_cycle()
        next_screen = f"lockscreen_{slide}"
        message = f"Slide: {slide}"

    # ---------- Hermes Deutsch Interactive & Offline-Replayed Actions ----------

    elif act == "deutsch:reveal":
        db.update_settings({"german_drill_flipped": True, "german_drill_choice": ""})
        next_screen = "deutsch_drill"
        message = "Revealed answer"

    elif act.startswith("deutsch:guess:"):
        choice = act.split(":", 2)[2].strip().lower()
        db.update_settings({"german_drill_flipped": True, "german_drill_choice": choice})
        next_screen = "deutsch_drill"
        message = f"Guessed {choice}"

    elif act.startswith("deutsch:rate:"):
        try:
            parts = act.split(":")
            card_id = int(parts[2])
            rating = int(parts[3])
            german_svc.review_card(card_id, rating, now.strftime("%Y-%m-%d"))
            next_screen = "deutsch_drill"
            message = f"Reviewed card {card_id} (rating {rating})"
        except (ValueError, IndexError):
            pass

    elif act in ("deutsch:classic:next", "deutsch:classic:prev"):
        german_svc.ensure_seeded()
        classics = db.list_german_classics()
        total = max(1, len(classics))
        cur_idx = int(settings.get("german_active_classic") or 0)
        delta = 1 if act.endswith(":next") else -1
        db.update_settings({"german_active_classic": (cur_idx + delta) % total})
        if next_screen not in ("deutsch_classic", "deutsch_classic_parallel", "deutsch_classic_xray"):
            next_screen = "deutsch_classic"
        message = "Switched famous text"

    elif act.startswith("deutsch:classic:set:"):
        try:
            german_svc.ensure_seeded()
            classics = db.list_german_classics()
            total = max(1, len(classics))
            idx = int(act.split(":", 3)[3]) % total
            db.update_settings({"german_active_classic": idx})
            message = f"Set active famous text index to {idx}"
        except ValueError:
            pass

    elif act.startswith("deutsch:classic:read:"):
        try:
            cid = int(act.split(":", 3)[3])
            german_svc.mark_classic_read(cid, now.strftime("%Y-%m-%d"))
            message = f"Marked text {cid} read"
        except ValueError:
            pass

    elif act.startswith("deutsch:classic:mine:"):
        try:
            cid = int(act.split(":", 3)[3])
            added = german_svc.mine_classic_chunks(cid)
            message = f"Mined {added} chunks into FSRS"
        except ValueError:
            pass

    elif act in ("deutsch:root:next", "deutsch:root:prev"):
        from backend.services.german_data import ROOT_PACKS

        cur_idx = int(settings.get("german_active_root") or 0)
        delta = 1 if act.endswith(":next") else -1
        db.update_settings({"german_active_root": (cur_idx + delta) % len(ROOT_PACKS)})
        next_screen = "deutsch_root"
        message = "Switched root & slang pack"

    elif act.startswith("deutsch:root:set:"):
        try:
            from backend.services.german_data import ROOT_PACKS

            idx = int(act.split(":", 3)[3]) % len(ROOT_PACKS)
            db.update_settings({"german_active_root": idx})
            message = f"Set active root pack index to {idx}"
        except ValueError:
            pass

    elif act == "deutsch:read_done":
        g_ctx = await german_svc.get_german_context(settings, now, force_refresh=False)
        reader = g_ctx["reader"]
        if not reader.get("read_done"):
            db.update_german_daily_progress(reader["date_key"], read_done=True)
            db.log_german_activity(
                now.strftime("%Y-%m-%d"),
                "read",
                correct=True,
                words_count=int(reader.get("word_count") or 80),
                points=15,
                detail=f"reader:{reader['date_key']}",
            )
        message = "Daily reading completed"

    elif act.startswith("deutsch:quiz:"):
        try:
            choice_idx = int(act.split(":", 2)[2])
            g_ctx = await german_svc.get_german_context(settings, now, force_refresh=False)
            reader = g_ctx["reader"]
            correct_idx = int((reader.get("quiz") or {}).get("answer_idx", 0))
            already_answered = int(reader.get("quiz_answered", -1)) >= 0
            db.update_german_daily_progress(
                reader["date_key"], read_done=True, quiz_answered=choice_idx
            )
            if not already_answered:
                db.log_german_activity(
                    now.strftime("%Y-%m-%d"),
                    "read",
                    correct=(choice_idx == correct_idx),
                    words_count=int(reader.get("word_count") or 80),
                    points=20 if choice_idx == correct_idx else 8,
                    detail=f"quiz:{reader['date_key']}:{choice_idx}",
                )
            message = "Quiz answered"
        except ValueError:
            pass

    elif act == "deutsch:reader:refresh":
        await german_svc.get_german_context(settings, now, force_refresh=True)
        next_screen = "deutsch_read"
        message = "Refreshed daily graded reader"

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

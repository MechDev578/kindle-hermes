"""KindleHermes FastAPI application — Hermes OS Server, Smart Lock-Screen Engine & Cross-Device Companion."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone as dt_timezone
import io
import json
from pathlib import Path
import time
from typing import Any
import zipfile
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend.auth import is_auth_enabled, require_auth, verify_token
from backend.config import DEVICE_NAME, KINDLE_HEIGHT, KINDLE_PPI, KINDLE_WIDTH, STATIC_DIR, TEMPLATES_DIR
from backend.database import db
from backend.modules.registry import (
    OFFLINE_SCREENS,
    VALID_SCREENS,
    build_data_bundle,
    dispatch_action,
    resolve_lockscreen_slide,
)
from backend.services import calendar_svc, gemini_svc, google_cal_svc, pomodoro_svc, weather_svc
from backend.services.renderer_svc import renderer


def _detect_host_iana_timezone() -> str:
    """Detect local macOS/Linux IANA timezone from /etc/localtime symlink (ignoring container UTC defaults)."""
    try:
        target = str(Path("/etc/localtime").resolve())
        marker = "zoneinfo/"
        if marker in target:
            tz_name = target.split(marker, 1)[1].strip()
            if tz_name in ("UTC", "Etc/UTC", "Etc/GMT", "GMT", "Universal", "UCT"):
                return ""
            ZoneInfo(tz_name)
            return tz_name
    except Exception:
        pass
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    settings = db.get_all_settings()
    if settings.get("auto_sync_location", True) and settings.get("location_source", "default") == "default":
        host_tz = _detect_host_iana_timezone()
        if host_tz and host_tz != settings.get("timezone"):
            db.update_settings({"timezone": host_tz})
    yield
    await renderer.close()


app = FastAPI(
    title="KindleHermes OS",
    version="1.0.0",
    description="Modular e-ink OS, context-aware lock screen, and cross-device web companion for Kindle Paperwhite 10th Gen.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_KINDLE_CSS_CACHE: str | None = None


def _read_kindle_css() -> str:
    global _KINDLE_CSS_CACHE
    css_path = STATIC_DIR / "kindle.css"
    if css_path.exists():
        return css_path.read_text(encoding="utf-8")
    return _KINDLE_CSS_CACHE or ""


# ---------- Pydantic Request Schemas ----------


class LoginPayload(BaseModel):
    token: str = ""


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=160)
    priority: int = Field(default=1, ge=1, le=3)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    completed: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=3)


class SettingsUpdate(BaseModel):
    use_mock_data: bool | None = None
    daily_focus: str | None = Field(default=None, max_length=200)
    calendar_ics_url: str | None = Field(default=None, max_length=1000)
    weather_city: str | None = Field(default=None, max_length=120)
    weather_lat: float | None = None
    weather_lon: float | None = None
    timezone: str | None = Field(default=None, max_length=80)
    auto_sync_location: bool | None = None
    lockscreen_mode: str | None = None
    lockscreen_slides: list[str] | None = None
    enabled_modules: list[str] | None = None


class LocationSyncPayload(BaseModel):
    lat: float | None = None
    lon: float | None = None
    city: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=80)
    source: str = Field(default="device", max_length=40)


class ActionPayload(BaseModel):
    action: str
    current_screen: str = "home"
    sim_time: str | None = None
    mock: bool | None = None


class SyncBatchAction(BaseModel):
    action: str
    current_screen: str = "home"
    ts: int | None = None


class SyncBatchPayload(BaseModel):
    actions: list[SyncBatchAction] = Field(default_factory=list)
    refresh_feeds: bool = True


class PomodoroModePayload(BaseModel):
    mode: str = "focus"


class GoogleCredentialsPayload(BaseModel):
    client_id: str = Field(..., min_length=5, max_length=300)
    client_secret: str = Field(..., min_length=5, max_length=300)


class GoogleCalendarsSelectPayload(BaseModel):
    selected_ids: list[str]


# ---------- Helpers ----------


def _effective_settings(mock_override: bool | None = None) -> dict[str, Any]:
    settings = db.get_all_settings()
    if mock_override is not None:
        settings["use_mock_data"] = mock_override
    return settings


def _render_html_from_bundle(
    screen_id: str,
    bundle: dict[str, Any],
    settings: dict[str, Any],
    *,
    inline_css: bool = False,
    sim_time: str | None = None,
    mock_override: bool | None = None,
    token: str = "",
) -> str:
    is_lockscreen = screen_id.startswith("lockscreen_")
    template = templates.get_template("kindle_shell.html")
    return template.render(
        screen=screen_id,
        is_lockscreen=is_lockscreen,
        inline_css=_read_kindle_css() if inline_css else "",
        bundle=bundle,
        cal=bundle["calendar"],
        weather=bundle["weather"],
        briefing=bundle["briefing"],
        tasks=bundle["tasks"],
        pomo=bundle["pomodoro"],
        tiles=bundle["tiles"],
        settings=settings,
        sim_time=sim_time or "",
        mock_override="" if mock_override is None else ("1" if mock_override else "0"),
        token=token,
        device_width=KINDLE_WIDTH,
        device_height=KINDLE_HEIGHT,
    )


async def _render_kindle_html(
    screen: str,
    sim_time: str | None = None,
    mock_override: bool | None = None,
    force_refresh: bool = False,
    inline_css: bool = False,
    token: str = "",
    consume_pomo_alert: bool = False,
) -> tuple[str, dict[str, Any]]:
    screen_id = screen if screen in VALID_SCREENS else "home"
    settings = _effective_settings(mock_override)
    now = calendar_svc.get_local_now(settings["timezone"], sim_time=sim_time)
    bundle = await build_data_bundle(
        settings,
        now,
        force_refresh=force_refresh,
        consume_pomo_alert=consume_pomo_alert,
    )
    html = _render_html_from_bundle(
        screen_id,
        bundle,
        settings,
        inline_css=inline_css,
        sim_time=sim_time,
        mock_override=mock_override,
        token=token,
    )
    return html, bundle


# ---------- Health & PWA Manifest ----------


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "device": DEVICE_NAME,
        "resolution": f"{KINDLE_WIDTH}x{KINDLE_HEIGHT}",
        "ppi": KINDLE_PPI,
        "auth_enabled": is_auth_enabled(),
    }


@app.get("/manifest.json")
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


# ---------- Auth Routes ----------


@app.get("/api/auth/status")
async def auth_status(request: Request):
    if not is_auth_enabled():
        return {"auth_enabled": False, "authenticated": True}
    from backend.auth import extract_token

    token = extract_token(request)
    return {"auth_enabled": True, "authenticated": verify_token(token)}


@app.post("/api/auth/login")
async def auth_login(payload: LoginPayload, response: Response):
    if not verify_token(payload.token.strip()):
        raise HTTPException(status_code=401, detail="Invalid PIN / access token")
    response.set_cookie(
        key="hermes_token",
        value=payload.token.strip(),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return {"ok": True}


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie("hermes_token")
    return {"ok": True}


# ---------- Frontend Views ----------


@app.get("/")
async def index():
    return RedirectResponse(url="/preview")


@app.get("/kindle", response_class=HTMLResponse)
async def kindle_view(
    request: Request,
    screen: str = Query(default="home"),
    sim_time: str | None = Query(default=None),
    mock: bool | None = Query(default=None),
    token: str = Query(default=""),
    _: None = Depends(require_auth),
):
    html, _ = await _render_kindle_html(
        screen=screen,
        sim_time=sim_time,
        mock_override=mock,
        inline_css=False,
        token=token,
    )
    return HTMLResponse(content=html)


@app.get("/app", response_class=HTMLResponse)
async def companion_app(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="companion.html",
        context={
            "auth_enabled": is_auth_enabled(),
            "device_name": DEVICE_NAME,
            "resolution": f"{KINDLE_WIDTH} × {KINDLE_HEIGHT}",
        },
    )


@app.get("/preview", response_class=HTMLResponse)
async def preview_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="preview.html",
        context={
            "auth_enabled": is_auth_enabled(),
            "device_name": DEVICE_NAME,
            "kindle_width": KINDLE_WIDTH,
            "kindle_height": KINDLE_HEIGHT,
            "kindle_ppi": KINDLE_PPI,
        },
    )


# ---------- Server-Driven Interactive Screen Protocol (Native FBInk Client & Simulator) ----------


@app.get("/api/screen/{screen_id}.png")
async def get_screen_png(
    screen_id: str,
    v: str = Query(default=""),
    sim_time: str | None = Query(default=None),
    mock: bool | None = Query(default=None),
    force_refresh: bool = Query(default=False),
    _: None = Depends(require_auth),
):
    clean_screen = screen_id if screen_id in VALID_SCREENS else "home"
    if v and not force_refresh:
        cached = renderer.get_cached_png(v)
        if cached is not None:
            return Response(
                content=cached,
                media_type="image/png",
                headers={
                    "Cache-Control": "no-store",
                    "X-Hermes-Screen": clean_screen,
                    "X-Hermes-Cache": "HIT",
                    "X-Hermes-Resolution": f"{KINDLE_WIDTH}x{KINDLE_HEIGHT}",
                },
            )
    html, _ = await _render_kindle_html(
        screen=clean_screen,
        sim_time=sim_time,
        mock_override=mock,
        force_refresh=force_refresh,
        inline_css=True,
    )
    rendered = await renderer.render_html(html)
    return Response(
        content=rendered.png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Hermes-Screen": clean_screen,
            "X-Hermes-Cache": "MISS",
            "X-Hermes-Resolution": f"{KINDLE_WIDTH}x{KINDLE_HEIGHT}",
        },
    )


@app.get("/api/screen/{screen_id}")
async def get_screen_metadata(
    request: Request,
    screen_id: str,
    sim_time: str | None = Query(default=None),
    mock: bool | None = Query(default=None),
    force_refresh: bool = Query(default=False),
    _: None = Depends(require_auth),
):
    clean_screen = screen_id if screen_id in VALID_SCREENS else "home"
    html, bundle = await _render_kindle_html(
        screen=clean_screen,
        sim_time=sim_time,
        mock_override=mock,
        force_refresh=force_refresh,
        inline_css=True,
        consume_pomo_alert=True,
    )
    rendered = await renderer.render_html(html)
    pomo = bundle["pomodoro"]
    refresh_after = pomo["next_tick_sec"] if pomo["is_running"] else 300

    query_parts = [f"v={rendered.etag}"]
    if sim_time:
        query_parts.append(f"sim_time={sim_time}")
    if mock is not None:
        query_parts.append(f"mock={'1' if mock else '0'}")
    tok = request.query_params.get("token", "").strip()
    if tok:
        query_parts.append(f"token={tok}")

    image_url = f"/api/screen/{clean_screen}.png?{'&'.join(query_parts)}"
    return {
        "screen_id": clean_screen,
        "etag": rendered.etag,
        "width": rendered.width,
        "height": rendered.height,
        "image_url": image_url,
        "hitboxes": rendered.hitboxes,
        "refresh_after_sec": refresh_after,
        "invert_flash": pomo["alert_flash"],
        "server_time": bundle["time_str"],
    }


@app.post("/api/action")
async def handle_interactive_action(
    payload: ActionPayload,
    _: None = Depends(require_auth),
):
    settings = _effective_settings(payload.mock)
    now = calendar_svc.get_local_now(settings["timezone"], sim_time=payload.sim_time)
    result = await dispatch_action(
        action=payload.action,
        current_screen=payload.current_screen,
        settings=settings,
        now=now,
    )
    return result


# ---------- Offline-First Sync Protocol (Single-Shot Bundle + Queued Outbox Replay) ----------


@app.post("/api/sync/batch")
async def api_sync_batch(
    payload: SyncBatchPayload,
    _: None = Depends(require_auth),
):
    """Replay queued offline actions from the Kindle (task toggles, clears, etc.) and clear feed caches."""
    settings = db.get_all_settings()
    now = calendar_svc.get_local_now(settings["timezone"])
    applied = 0
    for item in payload.actions:
        act = (item.action or "").strip()
        if not act or act == "system:exit":
            continue
        await dispatch_action(
            action=act,
            current_screen=item.current_screen or "home",
            settings=settings,
            now=now,
        )
        applied += 1

    if payload.refresh_feeds:
        calendar_svc.clear_ics_cache()
        google_cal_svc.clear_google_events_cache()
        weather_svc.clear_weather_cache()

    return {
        "ok": True,
        "applied": applied,
        "server_time_unix": int(time.time()),
    }


@app.get("/api/offline-bundle")
async def api_offline_bundle(
    force_refresh: bool = Query(default=True),
    include_slots: bool = Query(default=True),
    sim_time: str | None = Query(default=None),
    mock: bool | None = Query(default=None),
    _: None = Depends(require_auth),
):
    """Return a single ZIP containing all 8 rendered Kindle screens, hitboxes, layout rects, and time-sliced lock screens."""
    settings = _effective_settings(mock)
    now = calendar_svc.get_local_now(settings["timezone"], sim_time=sim_time)

    if force_refresh:
        calendar_svc.clear_ics_cache()
        google_cal_svc.clear_google_events_cache()
        weather_svc.clear_weather_cache()

    base_bundle = await build_data_bundle(settings, now, force_refresh=force_refresh)

    html_items: list[tuple[str, str]] = []
    for scr in OFFLINE_SCREENS:
        html_items.append(
            (
                f"screen:{scr}",
                _render_html_from_bundle(
                    scr,
                    base_bundle,
                    settings,
                    inline_css=True,
                    sim_time=sim_time,
                    mock_override=mock,
                ),
            )
        )

    # Default lock screen right now
    default_slide = resolve_lockscreen_slide(settings, now, requested="auto", advance=False)
    html_items.append(
        (
            "lock:default",
            _render_html_from_bundle(
                f"lockscreen_{default_slide}",
                base_bundle,
                settings,
                inline_css=True,
                sim_time=sim_time,
                mock_override=mock,
            ),
        )
    )

    # Time-sliced Smart Lock Screens for today's upcoming event transitions
    slot_defs: list[dict[str, Any]] = []
    if include_slots and settings.get("lockscreen_mode", "smart") != "cycle":
        raw_slots = calendar_svc.compute_lockscreen_time_slots(base_bundle["calendar"], now, max_slots=10)
        for slot in raw_slots:
            slot_dt: datetime = slot["dt"]
            slot_cal = calendar_svc.reclassify_calendar_context(base_bundle["calendar"], slot_dt)
            slot_bundle = {
                **base_bundle,
                "now": slot_dt,
                "date_long": slot_dt.strftime("%A, %b %d"),
                "date_short": slot_dt.strftime("%a, %b %d").upper(),
                "time_str": slot_dt.strftime("%H:%M"),
                "calendar": slot_cal,
            }
            slot_slide = resolve_lockscreen_slide(settings, slot_dt, requested="auto", advance=False)
            key = f"lock:{slot['id']}"
            html_items.append(
                (
                    key,
                    _render_html_from_bundle(
                        f"lockscreen_{slot_slide}",
                        slot_bundle,
                        settings,
                        inline_css=True,
                        sim_time=slot_dt.strftime("%H:%M"),
                        mock_override=mock,
                    ),
                )
            )
            slot_defs.append(
                {
                    "id": slot["id"],
                    "key": key,
                    "file": f"lockscreens/{slot['id']}.png",
                    "start_unix": slot["start_unix"],
                    "end_unix": slot["end_unix"],
                    "label": slot["label"],
                }
            )

    # Cycle-mode slides if enabled
    cycle_defs: list[dict[str, str]] = []
    if settings.get("lockscreen_mode") == "cycle":
        valid_slides = {"smart", "calendar", "planning", "weather"}
        slides = [s for s in settings.get("lockscreen_slides", ["smart"]) if s in valid_slides] or ["smart"]
        for s in slides:
            key = f"lock:cycle_{s}"
            html_items.append(
                (
                    key,
                    _render_html_from_bundle(
                        f"lockscreen_{s}",
                        base_bundle,
                        settings,
                        inline_css=True,
                        sim_time=sim_time,
                        mock_override=mock,
                    ),
                )
            )
            cycle_defs.append({"id": s, "key": key, "file": f"lockscreens/cycle_{s}.png"})

    rendered_map = await renderer.render_batch(html_items)

    screens_manifest: dict[str, Any] = {}
    for scr in OFFLINE_SCREENS:
        rend = rendered_map[f"screen:{scr}"]
        screens_manifest[scr] = {
            "file": f"screens/{scr}.png",
            "etag": rend.etag,
            "hitboxes": rend.hitboxes,
            "layout": rend.layout,
        }

    pomo = base_bundle["pomodoro"]
    tz_offset = int((now.utcoffset().total_seconds()) if now.utcoffset() else 0)
    manifest = {
        "version": 1,
        "synced_at_unix": int(time.time()),
        "synced_at_label": now.strftime("%H:%M"),
        "date_short": base_bundle["date_short"],
        "timezone": settings["timezone"],
        "tz_offset_sec": tz_offset,
        "lockscreen_mode": settings.get("lockscreen_mode", "smart"),
        "lockscreen_default": "lockscreens/default.png",
        "lockscreen_slots": [
            {
                "id": s["id"],
                "file": s["file"],
                "start_unix": s["start_unix"],
                "end_unix": s["end_unix"],
                "label": s["label"],
            }
            for s in slot_defs
        ],
        "lockscreen_cycle_files": [c["file"] for c in cycle_defs],
        "screens": screens_manifest,
        "tasks": [
            {
                "id": t["id"],
                "title": t["title"],
                "completed": bool(t["completed"]),
                "priority": int(t["priority"]),
            }
            for t in base_bundle["tasks"]
        ],
        "pomodoro": {
            "mode": pomo["mode"],
            "status": pomo["status"],
            "mode_label": pomo["mode_label"],
            "duration_sec": int(pomo["duration_sec"]),
            "remaining_sec": int(pomo["remaining_sec"]),
            "completed_sessions": int(pomo["completed_sessions"]),
        },
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
        for scr in OFFLINE_SCREENS:
            zf.writestr(f"screens/{scr}.png", rendered_map[f"screen:{scr}"].png_bytes)
        zf.writestr("lockscreens/default.png", rendered_map["lock:default"].png_bytes)
        for s in slot_defs:
            zf.writestr(s["file"], rendered_map[s["key"]].png_bytes)
        for c in cycle_defs:
            zf.writestr(c["file"], rendered_map[c["key"]].png_bytes)

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="hermes_offline_bundle.zip"',
            "X-Hermes-Bundle-Screens": str(len(OFFLINE_SCREENS)),
            "X-Hermes-Lockscreen-Slots": str(len(slot_defs)),
        },
    )


# ---------- Smart Context-Aware Lock Screen Renderer ----------


async def _render_lockscreen_response(
    slide: str,
    advance: bool,
    sim_time: str | None,
    mock: bool | None,
) -> Response:
    settings = _effective_settings(mock)
    now = calendar_svc.get_local_now(settings["timezone"], sim_time=sim_time)
    resolved_slide = resolve_lockscreen_slide(settings, now, requested=slide, advance=advance)
    screen_id = f"lockscreen_{resolved_slide}"
    html, _ = await _render_kindle_html(
        screen=screen_id,
        sim_time=sim_time,
        mock_override=mock,
        inline_css=True,
    )
    rendered = await renderer.render_html(html)
    return Response(
        content=rendered.png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="hermes_{resolved_slide}_{KINDLE_WIDTH}x{KINDLE_HEIGHT}.png"',
            "X-Hermes-Slide": resolved_slide,
            "X-Hermes-Resolution": f"{KINDLE_WIDTH}x{KINDLE_HEIGHT}",
        },
    )


@app.get("/lockscreen.png")
async def lockscreen_png(
    slide: str = Query(default="auto"),
    advance: bool = Query(default=False),
    sim_time: str | None = Query(default=None),
    mock: bool | None = Query(default=None),
    _: None = Depends(require_auth),
):
    return await _render_lockscreen_response(slide=slide, advance=advance, sim_time=sim_time, mock=mock)


@app.get("/render.png")
async def render_png(
    slide: str = Query(default="auto"),
    advance: bool = Query(default=False),
    sim_time: str | None = Query(default=None),
    mock: bool | None = Query(default=None),
    _: None = Depends(require_auth),
):
    return await _render_lockscreen_response(slide=slide, advance=advance, sim_time=sim_time, mock=mock)


@app.get("/api/lockscreen/slides")
async def get_lockscreen_slides(
    request: Request,
    _: None = Depends(require_auth),
):
    settings = db.get_all_settings()
    now = calendar_svc.get_local_now(settings["timezone"])
    mode = settings["lockscreen_mode"]
    slides = settings["lockscreen_slides"] if mode == "cycle" else [resolve_lockscreen_slide(settings, now, "auto")]
    tok = request.query_params.get("token", "").strip()
    suffix = f"&token={tok}" if tok else ""
    return {
        "mode": mode,
        "slides": [
            {
                "id": s,
                "filename": f"hermes_{idx + 1:02d}_{s}.png",
                "url": f"/lockscreen.png?slide={s}{suffix}",
            }
            for idx, s in enumerate(slides)
        ],
    }


# ---------- Companion APIs (iOS / macOS / Windows Sync) ----------


@app.get("/api/state")
async def get_full_state(
    sim_time: str | None = Query(default=None),
    mock: bool | None = Query(default=None),
    _: None = Depends(require_auth),
):
    settings = _effective_settings(mock)
    now = calendar_svc.get_local_now(settings["timezone"], sim_time=sim_time)
    bundle = await build_data_bundle(settings, now)
    return {
        "device": DEVICE_NAME,
        "resolution": {"width": KINDLE_WIDTH, "height": KINDLE_HEIGHT, "ppi": KINDLE_PPI},
        "date_long": bundle["date_long"],
        "date_short": bundle["date_short"],
        "time_str": bundle["time_str"],
        "settings": bundle["settings"],
        "tasks": bundle["tasks"],
        "pomodoro": bundle["pomodoro"],
        "calendar": bundle["calendar"],
        "weather": bundle["weather"],
        "briefing": bundle["briefing"],
        "modules": bundle["all_modules"],
    }


@app.get("/api/tasks")
async def api_list_tasks(_: None = Depends(require_auth)):
    return {"tasks": db.list_tasks()}


@app.post("/api/tasks")
async def api_create_task(payload: TaskCreate, _: None = Depends(require_auth)):
    try:
        task = db.create_task(payload.title, priority=payload.priority)
        return {"task": task, "tasks": db.list_tasks()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tasks/{task_id}/toggle")
async def api_toggle_task(task_id: int, _: None = Depends(require_auth)):
    task = db.toggle_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task, "tasks": db.list_tasks()}


@app.patch("/api/tasks/{task_id}")
async def api_update_task(task_id: int, payload: TaskUpdate, _: None = Depends(require_auth)):
    task = db.update_task(task_id, title=payload.title, completed=payload.completed, priority=payload.priority)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task, "tasks": db.list_tasks()}


@app.delete("/api/tasks/{task_id}")
async def api_delete_task(task_id: int, _: None = Depends(require_auth)):
    deleted = db.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "tasks": db.list_tasks()}


@app.post("/api/tasks/clear-completed")
async def api_clear_completed(_: None = Depends(require_auth)):
    removed = db.clear_completed_tasks()
    return {"removed": removed, "tasks": db.list_tasks()}


@app.get("/api/pomodoro")
async def api_get_pomodoro(_: None = Depends(require_auth)):
    return pomodoro_svc.get_pomodoro_view()


@app.post("/api/pomodoro/toggle")
async def api_toggle_pomodoro(_: None = Depends(require_auth)):
    return pomodoro_svc.toggle_start_pause()


@app.post("/api/pomodoro/reset")
async def api_reset_pomodoro(_: None = Depends(require_auth)):
    return pomodoro_svc.reset_pomodoro()


@app.post("/api/pomodoro/mode")
async def api_mode_pomodoro(payload: PomodoroModePayload, _: None = Depends(require_auth)):
    return pomodoro_svc.set_pomodoro_mode(payload.mode)


@app.get("/api/settings")
async def api_get_settings(_: None = Depends(require_auth)):
    return db.get_all_settings()


@app.patch("/api/settings")
async def api_patch_settings(payload: SettingsUpdate, _: None = Depends(require_auth)):
    updates = payload.model_dump(exclude_none=True)
    if "lockscreen_mode" in updates and updates["lockscreen_mode"] not in ("smart", "cycle", "time_of_day"):
        raise HTTPException(status_code=400, detail="Invalid lockscreen_mode")
    if "weather_lat" in updates or "weather_lon" in updates or "weather_city" in updates:
        updates["location_source"] = "manual"
        weather_svc.clear_weather_cache()
    updated = db.update_settings(updates)
    return updated


@app.post("/api/location/sync")
async def api_sync_location(payload: LocationSyncPayload, _: None = Depends(require_auth)):
    """Sync live location (lat/lon/city) and IANA timezone from iPhone, MacBook, or Windows."""
    updates: dict[str, Any] = {}
    if payload.timezone:
        try:
            ZoneInfo(payload.timezone.strip())
            updates["timezone"] = payload.timezone.strip()
        except Exception:
            pass

    if payload.lat is not None and payload.lon is not None:
        lat = round(float(payload.lat), 4)
        lon = round(float(payload.lon), 4)
        city = (payload.city or "").strip()
        if not city:
            city = await weather_svc.reverse_geocode_city(lat, lon)
        updates["weather_lat"] = lat
        updates["weather_lon"] = lon
        updates["weather_city"] = city
        updates["location_source"] = payload.source or "device"
        updates["location_updated_at"] = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        weather_svc.clear_weather_cache()
    elif payload.city and payload.city.strip():
        updates["weather_city"] = payload.city.strip()
        updates["location_source"] = payload.source or "device"
        updates["location_updated_at"] = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        weather_svc.clear_weather_cache()

    if updates:
        settings = db.update_settings(updates)
    else:
        settings = db.get_all_settings()

    now = calendar_svc.get_local_now(settings["timezone"])
    weather = await weather_svc.get_weather_context(settings, now, force_refresh=bool(updates))
    return {"ok": True, "settings": settings, "weather": weather, "time_str": now.strftime("%H:%M")}


@app.post("/api/location/auto-detect")
async def api_auto_detect_location(payload: LocationSyncPayload | None = None, _: None = Depends(require_auth)):
    """Auto-detect city, coordinates, and timezone via IP + optional browser/host timezone."""
    updates: dict[str, Any] = {}
    detected = await weather_svc.detect_ip_location()
    if detected:
        updates["weather_city"] = detected["city"]
        updates["weather_lat"] = detected["lat"]
        updates["weather_lon"] = detected["lon"]
        updates["location_source"] = detected["source"]
        updates["location_updated_at"] = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if detected.get("timezone"):
            try:
                ZoneInfo(detected["timezone"])
                updates["timezone"] = detected["timezone"]
            except Exception:
                pass
        weather_svc.clear_weather_cache()

    # Prefer the client's exact browser/device timezone if provided, else host macOS timezone
    preferred_tz = (payload.timezone.strip() if payload and payload.timezone else "") or _detect_host_iana_timezone()
    if preferred_tz:
        try:
            ZoneInfo(preferred_tz)
            updates["timezone"] = preferred_tz
        except Exception:
            pass

    settings = db.update_settings(updates) if updates else db.get_all_settings()
    now = calendar_svc.get_local_now(settings["timezone"])
    weather = await weather_svc.get_weather_context(settings, now, force_refresh=True)
    return {
        "ok": bool(detected or preferred_tz),
        "detected": detected,
        "settings": settings,
        "weather": weather,
        "time_str": now.strftime("%H:%M"),
    }


@app.get("/api/weather/search")
async def api_weather_search(q: str = Query(default=""), _: None = Depends(require_auth)):
    try:
        results = await weather_svc.search_city_geocoding(q)
        return {"results": results}
    except Exception as exc:
        return JSONResponse(status_code=200, content={"results": [], "error": str(exc)})


@app.post("/api/briefing/refresh")
async def api_refresh_briefing(_: None = Depends(require_auth)):
    settings = db.get_all_settings()
    now = calendar_svc.get_local_now(settings["timezone"])
    briefing = await gemini_svc.get_daily_briefing(settings, now, force_refresh=True)
    return briefing


# ---------- Google Calendar OAuth2 & Multi-Calendar Sync ----------


def _google_callback_url(request: Request) -> str:
    url = str(request.url_for("google_oauth_callback"))
    # Google OAuth rejects 0.0.0.0; normalize local bind addresses to localhost
    return url.replace("://0.0.0.0:", "://localhost:").replace("://127.0.0.1:", "://localhost:")


@app.get("/api/google/status")
async def api_google_status(request: Request, _: None = Depends(require_auth)):
    return google_cal_svc.get_public_status(_google_callback_url(request))


@app.post("/api/google/credentials")
async def api_google_save_credentials(
    request: Request,
    payload: GoogleCredentialsPayload,
    _: None = Depends(require_auth),
):
    db.update_google_oauth(
        {
            "client_id": payload.client_id.strip(),
            "client_secret": payload.client_secret.strip(),
        }
    )
    return google_cal_svc.get_public_status(_google_callback_url(request))


@app.get("/api/google/auth")
async def api_google_start_auth(request: Request, _: None = Depends(require_auth)):
    try:
        auth_url = google_cal_svc.build_authorization_url(_google_callback_url(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(url=auth_url)


@app.get("/api/google/callback", name="google_oauth_callback")
async def google_oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    if error:
        return RedirectResponse(url=f"/app?google=error&reason={error}")
    if not code or not state:
        return RedirectResponse(url="/app?google=error&reason=missing_code")
    try:
        await google_cal_svc.exchange_code_for_tokens(code, state)
        return RedirectResponse(url="/app?google=connected")
    except Exception as exc:
        import urllib.parse

        reason = urllib.parse.quote(str(exc)[:140])
        return RedirectResponse(url=f"/app?google=error&reason={reason}")


@app.post("/api/google/calendars/refresh")
async def api_google_refresh_calendars(request: Request, _: None = Depends(require_auth)):
    try:
        await google_cal_svc.refresh_calendar_list(auto_select_defaults=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return google_cal_svc.get_public_status(_google_callback_url(request))


@app.patch("/api/google/calendars")
async def api_google_select_calendars(
    request: Request,
    payload: GoogleCalendarsSelectPayload,
    _: None = Depends(require_auth),
):
    clean_ids = [cid.strip() for cid in payload.selected_ids if cid.strip()]
    db.update_google_oauth({"selected_ids": clean_ids})
    google_cal_svc.clear_google_events_cache()
    return google_cal_svc.get_public_status(_google_callback_url(request))


@app.post("/api/google/disconnect")
async def api_google_disconnect(request: Request, _: None = Depends(require_auth)):
    db.clear_google_tokens()
    google_cal_svc.clear_google_events_cache()
    return google_cal_svc.get_public_status(_google_callback_url(request))

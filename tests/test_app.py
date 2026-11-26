"""End-to-end verification tests for KindleHermes OS (Paperwhite 10th Gen — 1072x1448)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.config import KINDLE_HEIGHT, KINDLE_WIDTH
from backend.database import HermesDB
import backend.database as db_module
import backend.modules.registry as reg_module
import backend.services.calendar_svc as cal_module
import backend.services.gemini_svc as gemini_module
import backend.services.google_cal_svc as gcal_module
import backend.services.pomodoro_svc as pomo_module
import backend.main as main_module


@pytest.fixture()
def client(tmp_path: Path):
    test_db = HermesDB(db_path=tmp_path / "test_hermes.db")
    test_db.update_settings({"use_mock_data": True})
    db_module.db = test_db
    reg_module.db = test_db
    cal_module.db = test_db
    gemini_module.db = test_db
    gcal_module.db = test_db
    pomo_module.db = test_db
    main_module.db = test_db
    gcal_module.clear_google_events_cache()

    with TestClient(main_module.app) as c:
        yield c


def test_health_and_frontend_pages(client: TestClient):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["resolution"] == f"{KINDLE_WIDTH}x{KINDLE_HEIGHT}"
    assert body["ppi"] == 300

    for path in ("/app", "/preview", "/manifest.json"):
        r = client.get(path)
        assert r.status_code == 200, f"Failed route {path}"

    for screen in ("home", "today", "calendar", "calendar_tomorrow", "planner", "pomodoro", "weather", "briefing"):
        r = client.get(f"/kindle?screen={screen}&sim_time=14:20")
        assert r.status_code == 200, f"Failed kindle screen {screen}"
        assert "HERMES" in r.text or "HOME" in r.text


def test_tasks_crud_and_interactive_action_toggle(client: TestClient):
    r = client.post("/api/tasks", json={"title": "Test PW4 touch target", "priority": 1})
    assert r.status_code == 200
    task_id = r.json()["task"]["id"]
    assert r.json()["task"]["completed"] is False

    # Interactive tap via /api/action toggles task cleanly
    act = client.post(
        "/api/action",
        json={"action": f"task:toggle:{task_id}", "current_screen": "planner"},
    )
    assert act.status_code == 200
    assert act.json()["ok"] is True
    assert act.json()["next_screen"] == "planner"

    tasks = client.get("/api/tasks").json()["tasks"]
    toggled = next(t for t in tasks if t["id"] == task_id)
    assert toggled["completed"] is True

    # Exit action returns exit=True
    exit_act = client.post("/api/action", json={"action": "system:exit", "current_screen": "home"})
    assert exit_act.status_code == 200
    assert exit_act.json()["exit"] is True


def test_pomodoro_server_state(client: TestClient):
    r = client.get("/api/pomodoro")
    assert r.status_code == 200
    assert r.json()["remaining_min"] == 25
    assert r.json()["is_running"] is False

    started = client.post("/api/pomodoro/toggle").json()
    assert started["is_running"] is True

    switched = client.post("/api/pomodoro/mode", json={"mode": "short_break"}).json()
    assert switched["mode"] == "short_break"
    assert switched["remaining_min"] == 5
    assert switched["is_running"] is False


def test_calendar_context_aware_states(client: TestClient):
    # 14:20 -> During "Deep Work: KindleHermes OS" (14:00 - 15:30)
    st_now = client.get("/api/state?sim_time=14:20&mock=1").json()
    assert st_now["calendar"]["state"] == "in_event"
    assert st_now["calendar"]["current_event"] is not None
    assert st_now["calendar"]["next_event"] is not None

    # 15:40 -> Between Deep Work (ends 15:30) and Design Sync (16:00)
    st_between = client.get("/api/state?sim_time=15:40&mock=1").json()
    assert st_between["calendar"]["state"] == "between_events"
    assert st_between["calendar"]["current_event"] is None
    assert st_between["calendar"]["next_event"]["start_label"] == "16:00"

    # 22:00 -> All events done, tomorrow preview ready
    st_done = client.get("/api/state?sim_time=22:00&mock=1").json()
    assert st_done["calendar"]["state"] == "day_complete"
    assert len(st_done["calendar"]["tomorrow_events"]) > 0


def test_ota_settings_and_lockscreen_cycle(client: TestClient):
    upd = client.patch(
        "/api/settings",
        json={
            "daily_focus": "Finish hardware testing",
            "lockscreen_mode": "cycle",
            "lockscreen_slides": ["smart", "calendar", "weather"],
            "enabled_modules": ["today", "calendar", "planner", "pomodoro"],
        },
    )
    assert upd.status_code == 200
    data = upd.json()
    assert data["daily_focus"] == "Finish hardware testing"
    assert data["lockscreen_mode"] == "cycle"
    assert len(data["enabled_modules"]) == 4

    slides = client.get("/api/lockscreen/slides").json()
    assert slides["mode"] == "cycle"
    assert [s["id"] for s in slides["slides"]] == ["smart", "calendar", "weather"]


def test_native_screen_protocol_and_1072x1448_grayscale_png(client: TestClient):
    meta_resp = client.get("/api/screen/home?sim_time=14:20&mock=1")
    assert meta_resp.status_code == 200
    meta = meta_resp.json()
    assert meta["width"] == KINDLE_WIDTH
    assert meta["height"] == KINDLE_HEIGHT

    actions = {h["action"] for h in meta["hitboxes"]}
    assert "nav:today" in actions
    assert "nav:calendar" in actions
    assert "nav:planner" in actions
    assert "nav:pomodoro" in actions
    assert "system:exit" in actions

    # Every hitbox must be inside 1072x1448 with large e-ink touch area
    for h in meta["hitboxes"]:
        assert 0 <= h["x"] < KINDLE_WIDTH
        assert 0 <= h["y"] < KINDLE_HEIGHT
        assert h["w"] >= 70
        assert h["h"] >= 70

    # Verify Home PNG is exact 1072x1448 8-bit grayscale
    png_resp = client.get("/api/screen/home.png?sim_time=14:20&mock=1")
    assert png_resp.status_code == 200
    assert png_resp.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(png_resp.content))
    assert img.size == (KINDLE_WIDTH, KINDLE_HEIGHT)
    assert img.mode == "L"

    # Verify Smart Lock Screen PNG
    ls_resp = client.get("/lockscreen.png?slide=smart&sim_time=14:20&mock=1")
    assert ls_resp.status_code == 200
    ls_img = Image.open(io.BytesIO(ls_resp.content))
    assert ls_img.size == (KINDLE_WIDTH, KINDLE_HEIGHT)
    assert ls_img.mode == "L"


def test_location_and_timezone_sync(client: TestClient):
    resp = client.post(
        "/api/location/sync",
        json={
            "lat": 48.1351,
            "lon": 11.5820,
            "city": "Munich",
            "timezone": "Europe/Berlin",
            "source": "macbook-gps",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["settings"]["weather_city"] == "Munich"
    assert body["settings"]["timezone"] == "Europe/Berlin"
    assert body["settings"]["location_source"] == "macbook-gps"


def test_google_calendar_oauth_setup_and_event_parsing(client: TestClient):
    from zoneinfo import ZoneInfo

    st = client.get("/api/google/status").json()
    assert st["connected"] is False
    assert st["redirect_uri"].endswith("/api/google/callback")

    saved = client.post(
        "/api/google/credentials",
        json={"client_id": "123456-test.apps.googleusercontent.com", "client_secret": "GOCSPX-testsecret"},
    )
    assert saved.status_code == 200
    assert saved.json()["has_credentials"] is True

    # Auth redirect builds valid Google OAuth URL
    auth_resp = client.get("/api/google/auth", follow_redirects=False)
    assert auth_resp.status_code == 307
    assert "accounts.google.com/o/oauth2/v2/auth" in auth_resp.headers["location"]
    assert "calendar.readonly" in auth_resp.headers["location"]

    # Calendar selection persistence
    sel = client.patch("/api/google/calendars", json={"selected_ids": ["primary", "work@group.calendar.google.com"]})
    assert sel.status_code == 200
    assert sel.json()["selected_ids"] == ["primary", "work@group.calendar.google.com"]

    # Event parser handles timed and all-day Google Calendar v3 payloads
    tz = ZoneInfo("Europe/Berlin")
    timed = gcal_module.parse_google_event_item(
        {
            "summary": "Architecture Sync",
            "location": "Meet",
            "start": {"dateTime": "2026-11-25T14:00:00+01:00"},
            "end": {"dateTime": "2026-11-25T15:00:00+01:00"},
        },
        "Work",
        tz,
    )
    assert timed is not None
    assert timed["all_day"] is False
    assert timed["title"] == "Architecture Sync"
    assert timed["category"] == "Work"

    all_day = gcal_module.parse_google_event_item(
        {
            "summary": "Public Holiday",
            "start": {"date": "2026-11-25"},
            "end": {"date": "2026-11-26"},
        },
        "Holidays",
        tz,
    )
    assert all_day is not None
    assert all_day["all_day"] is True
    assert all_day["start"].date().isoformat() == "2026-11-25"
    assert all_day["end"].date().isoformat() == "2026-11-25"


def test_offline_bundle_and_batch_sync(client: TestClient):
    import json
    import zipfile

    # Create a task, then replay idempotent offline actions via /api/sync/batch
    created = client.post("/api/tasks", json={"title": "Offline Airplane Task", "priority": 1}).json()
    task_id = created["task"]["id"]

    batch = client.post(
        "/api/sync/batch",
        json={
            "actions": [
                {"action": f"task:set:{task_id}:1", "current_screen": "planner"},
                {"action": "pomo:mode:short_break", "current_screen": "pomodoro"},
            ],
            "refresh_feeds": False,
        },
    )
    assert batch.status_code == 200
    assert batch.json()["ok"] is True
    assert batch.json()["applied"] == 2

    tasks = client.get("/api/tasks").json()["tasks"]
    assert next(t for t in tasks if t["id"] == task_id)["completed"] is True
    assert client.get("/api/pomodoro").json()["mode"] == "short_break"

    # Download single-shot offline ZIP bundle with time-sliced lock screens
    bundle_resp = client.get("/api/offline-bundle?force_refresh=0&sim_time=10:00&mock=1")
    assert bundle_resp.status_code == 200
    assert bundle_resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(bundle_resp.content)) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "lockscreens/default.png" in names
        manifest = json.loads(zf.read("manifest.json"))

        expected_screens = {
            "home",
            "today",
            "calendar",
            "calendar_tomorrow",
            "planner",
            "pomodoro",
            "weather",
            "briefing",
        }
        assert set(manifest["screens"].keys()) == expected_screens
        for scr in expected_screens:
            path = f"screens/{scr}.png"
            assert path in names
            img = Image.open(io.BytesIO(zf.read(path)))
            assert img.size == (KINDLE_WIDTH, KINDLE_HEIGHT)
            assert img.mode == "L"

        # Planner hitboxes include exact checkbox_rect for pixel-exact offline toggling
        planner_boxes = manifest["screens"]["planner"]["hitboxes"]
        task_boxes = [b for b in planner_boxes if b["action"].startswith("task:toggle:")]
        assert len(task_boxes) > 0
        assert "checkbox_rect" in task_boxes[0]

        # Pomodoro layout rects captured for crisp local offline countdown rendering
        pomo_layout = manifest["screens"]["pomodoro"]["layout"].get("pomo")
        assert pomo_layout is not None
        assert pomo_layout["digits"] is not None
        assert len(pomo_layout["seg_cells"]) == 10

        # Multiple time-sliced Smart Lock Screens pre-rendered for the day (10:00 onward has several mock meetings)
        slots = manifest["lockscreen_slots"]
        assert len(slots) >= 4
        for slot in slots:
            assert slot["file"] in names
            assert slot["end_unix"] > slot["start_unix"]

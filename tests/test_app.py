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
import backend.services.german_svc as german_module
import backend.services.google_cal_svc as gcal_module
import backend.services.google_tasks_svc as gtasks_module
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
    german_module.db = test_db
    gcal_module.db = test_db
    gtasks_module.db = test_db
    pomo_module.db = test_db
    main_module.db = test_db
    gcal_module.clear_google_events_cache()
    gtasks_module.clear_google_tasks_cache()

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
            "lat": 47.3769,
            "lon": 8.5417,
            "city": "Zurich",
            "timezone": "Europe/Zurich",
            "source": "macbook-gps",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["settings"]["weather_city"] == "Zurich"
    assert body["settings"]["timezone"] == "Europe/Zurich"
    assert body["settings"]["location_source"] == "macbook-gps"


def test_google_calendar_oauth_setup_and_event_parsing(client: TestClient):
    from zoneinfo import ZoneInfo

    st = client.get("/api/google/status").json()
    assert st["connected"] is False
    assert st["redirect_uri"].endswith("/api/google/callback")

    saved = client.post(
        "/api/google/credentials",
        json={"client_id": "123456-test.apps.googleusercontent.com", "client_secret": "example-oauth-client-secret"},
    )
    assert saved.status_code == 200
    assert saved.json()["has_credentials"] is True

    # Auth redirect builds valid Google OAuth URL (includes both Calendar and Tasks scopes)
    auth_resp = client.get("/api/google/auth", follow_redirects=False)
    assert auth_resp.status_code == 307
    assert "accounts.google.com/o/oauth2/v2/auth" in auth_resp.headers["location"]
    assert "calendar.readonly" in auth_resp.headers["location"]
    assert "auth%2Ftasks" in auth_resp.headers["location"]

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
            "deutsch",
            "deutsch_read",
            "deutsch_read_parallel",
            "deutsch_root",
        }
        assert set(manifest["screens"].keys()) == expected_screens
        for scr in expected_screens:
            path = f"screens/{scr}.png"
            assert path in names
            img = Image.open(io.BytesIO(zf.read(path)))
            assert img.size == (KINDLE_WIDTH, KINDLE_HEIGHT)
            assert img.mode == "L"

        # Hermes Deutsch offline bundle ships pre-rendered FSRS deck, Famous Texts (3 modes) & all Root packs
        de = manifest["deutsch"]
        assert len(de["deck"]) >= 8
        assert de["deck_done_file"] in names
        assert de["deck"][0]["front_file"] in names
        assert de["deck"][0]["back_file"] in names
        assert de["classic_total"] >= 15
        assert len(de["classics"]) >= 3
        assert de["classics"][0]["idx"] == 0
        assert de["classics"][0]["gloss_file"] in names
        assert de["classics"][0]["parallel_file"] in names
        assert de["classics"][0]["xray_file"] in names
        assert len(de["roots"]) == 6
        assert de["roots"][0]["file"] in names

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


def test_security_headers_and_login_rate_limiting(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import backend.auth as auth_module

    auth_module.reset_login_rate_limits()
    resp = client.get("/api/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "no-referrer"
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"

    # Simulate token protection enabled and verify brute-force lockout after 10 failures
    monkeypatch.setattr(main_module, "verify_token", lambda tok: tok == "super-secret-pin")
    for _ in range(10):
        r = client.post("/api/auth/login", json={"token": "wrong-pin"})
        assert r.status_code == 401
    blocked = client.post("/api/auth/login", json={"token": "super-secret-pin"})
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers
    auth_module.reset_login_rate_limits()


def test_hermes_deutsch_end_to_end(client: TestClient):
    # Home screen includes Deutsch app tile while keeping strictly 6 bottom dock items
    home_html = client.get("/kindle?screen=home&mock=1").text
    assert 'data-action="nav:deutsch"' in home_html
    assert home_html.count('class="dock-item') == 6

    # All Deutsch screens render cleanly
    for scr in (
        "deutsch",
        "deutsch_drill",
        "deutsch_classic",
        "deutsch_classic_parallel",
        "deutsch_classic_xray",
        "deutsch_read",
        "deutsch_read_parallel",
        "deutsch_root",
    ):
        r = client.get(f"/kindle?screen={scr}&mock=1")
        assert r.status_code == 200, f"Failed Deutsch screen {scr}"

    # Verify standalone lockscreen_deutsch PNG is exact 1072x1448 8-bit grayscale
    ls_de = client.get("/lockscreen.png?slide=deutsch&mock=1")
    assert ls_de.status_code == 200
    ls_img = Image.open(io.BytesIO(ls_de.content))
    assert ls_img.size == (KINDLE_WIDTH, KINDLE_HEIGHT)
    assert ls_img.mode == "L"

    # Initial Sprachindex & seeded classics/deck
    st0 = client.get("/api/german/state").json()
    score0 = st0["mastery"]["total"]
    assert st0["mastery"]["band"] == "A2"
    assert len(st0["classics"]) >= 15
    assert len(st0["deck"]) >= 10

    # FSRS review via interactive action + reveal
    first_card_id = st0["deck"][0]["id"]
    rev_act = client.post("/api/action", json={"action": "deutsch:reveal", "current_screen": "deutsch_drill"})
    assert rev_act.status_code == 200
    rate_act = client.post(
        "/api/action",
        json={"action": f"deutsch:rate:{first_card_id}:3", "current_screen": "deutsch_drill"},
    )
    assert rate_act.status_code == 200

    # Mark famous text read & mine its chunks
    first_classic = st0["classics"][0]
    read_res = client.post(f"/api/german/classics/{first_classic['id']}/read")
    assert read_res.status_code == 200
    mine_res = client.post(f"/api/german/classics/{first_classic['id']}/mine")
    assert mine_res.status_code == 200
    assert mine_res.json()["added"] >= 1

    # Daily reader comprehension quiz + Companion Schreib-Sprint + Audio shadowing
    quiz_act = client.post("/api/action", json={"action": "deutsch:quiz:0", "current_screen": "deutsch_read"})
    assert quiz_act.status_code == 200

    write_res = client.post(
        "/api/german/writing",
        json={"user_text": "Ich habe heute Bock auf Deutsch und will mehr Demokratie wagen.", "target_chunks": ["Bock", "wagen"]},
    )
    assert write_res.status_code == 200
    assert "recast_de" in write_res.json()["entry"]

    shadow_res = client.post("/api/german/shadow")
    assert shadow_res.status_code == 200

    # Custom card creation & custom speech/text import
    card_res = client.post(
        "/api/german/cards",
        json={
            "kind": "slang",
            "cefr": "B1",
            "category": "Umgangssprache",
            "prompt_de": "Alles klar, ________ schon!",
            "answer_de": "passt",
            "full_de": "Alles klar, passt schon!",
            "hint_en": "works for me",
            "translation_en": "All good, that works!",
            "rule_note": "Everyday spoken German approval.",
        },
    )
    assert card_res.status_code == 200

    imp_res = client.post(
        "/api/german/classics/import",
        json={
            "title": "Test Rede",
            "author": "Test Autor",
            "german_text": "Das ist ein eigener deutscher Text zum schnellen Lernen.",
            "english_translation": "This is a custom German text for rapid learning.",
        },
    )
    assert imp_res.status_code == 200

    # Replaying offline Deutsch bookmark & card rating pulls fresh content on next sync
    batch_de = client.post(
        "/api/sync/batch",
        json={
            "actions": [
                {"action": f"deutsch:rate:{first_card_id}:3", "current_screen": "deutsch_drill"},
                {"action": "deutsch:classic:set:4", "current_screen": "deutsch_classic"},
                {"action": "deutsch:root:set:2", "current_screen": "deutsch_root"},
            ],
            "refresh_feeds": False,
        },
    )
    assert batch_de.status_code == 200
    assert batch_de.json()["applied"] == 3

    # Sprachindex must have climbed meaningfully across all strands!
    st1 = client.get("/api/german/state").json()
    assert st1["mastery"]["total"] > score0
    assert st1["mastery"]["classics_read"] >= 1
    assert st1["mastery"]["writing_sessions"] >= 1
    assert st1["mastery"]["streak_days"] >= 1
    assert st1["classic_idx"] == 4
    assert st1["root_idx"] == 2


def test_google_tasks_two_way_sync_ui_and_offline_bundle(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import json
    import zipfile

    # Seed a local task first — should stay tucked away while Google Tasks is connected, then return on disconnect
    local_res = client.post("/api/tasks", json={"title": "Local Fallback Task", "priority": 2})
    assert local_res.status_code == 200
    assert local_res.json()["tasks_meta"]["source"] == "local"

    # Switch to live mode & configure connected Google OAuth with Calendar + Tasks scopes
    db_module.db.update_settings({"use_mock_data": False})
    db_module.db.update_google_oauth(
        {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_at": 9999999999,
            "granted_scopes": (
                "https://www.googleapis.com/auth/calendar.readonly "
                "https://www.googleapis.com/auth/tasks"
            ),
            "task_lists": [
                {"id": "list_work", "title": "Work", "is_default": True},
                {"id": "list_personal", "title": "Personal", "is_default": False},
            ],
            "selected_task_list_ids": ["list_work", "list_personal"],
            "default_task_list_id": "list_work",
        }
    )

    # Stub Google Tasks HTTP traffic (GET lists/tasks, POST create, PATCH update, DELETE, POST clear)
    today_iso = gcal_module.calendar_svc if hasattr(gcal_module, "calendar_svc") else None
    del today_iso
    now_dt = cal_module.get_local_now("Europe/Berlin")
    today_str = now_dt.date().isoformat()
    yesterday_str = (now_dt.date().fromordinal(now_dt.date().toordinal() - 1)).isoformat()
    tomorrow_str = (now_dt.date().fromordinal(now_dt.date().toordinal() + 1)).isoformat()

    remote_store: dict[str, list[dict]] = {
        "list_work": [
            {
                "id": "gt_1",
                "title": "Ship v3.3 Google Tasks",
                "notes": "Verify 2-way sync and e-ink badges",
                "due": f"{today_str}T00:00:00.000Z",
                "status": "needsAction",
                "position": "00000000000000000001",
                "updated": "2026-11-27T10:00:00.000Z",
            },
            {
                "id": "gt_1a",
                "parent": "gt_1",
                "title": "Test offline checkbox replay",
                "status": "needsAction",
                "position": "00000000000000000002",
                "updated": "2026-11-27T10:01:00.000Z",
            },
            {
                "id": "gt_2",
                "title": "Fix overdue ticket",
                "due": f"{yesterday_str}T00:00:00.000Z",
                "status": "needsAction",
                "position": "00000000000000000003",
                "updated": "2026-11-27T09:00:00.000Z",
            },
        ],
        "list_personal": [
            {
                "id": "gt_3",
                "title": "Buy coffee beans",
                "due": f"{tomorrow_str}T00:00:00.000Z",
                "status": "needsAction",
                "position": "00000000000000000001",
                "updated": "2026-11-27T08:00:00.000Z",
            },
        ],
    }
    remote_calls: list[tuple[str, str, dict | None]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict | None = None):
            self.status_code = status_code
            self._payload = payload or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url: str, headers=None, params=None):
            remote_calls.append(("GET", url, params))
            if url.endswith("/users/@me/lists"):
                return FakeResponse(
                    200,
                    {
                        "items": [
                            {"id": "list_work", "title": "Work"},
                            {"id": "list_personal", "title": "Personal"},
                        ]
                    },
                )
            for lid, items in remote_store.items():
                if f"/lists/{lid}/tasks" in url:
                    p = params or {}
                    filtered = list(items)
                    # Mirror real Google Tasks API v1 behavior:
                    # - completedMin excludes incomplete (needsAction) tasks!
                    # - showCompleted=false excludes completed tasks!
                    if p.get("completedMin"):
                        filtered = [i for i in filtered if i.get("status") == "completed"]
                    if str(p.get("showCompleted", "true")).lower() == "false":
                        filtered = [i for i in filtered if i.get("status") != "completed"]
                    return FakeResponse(200, {"items": filtered})
            return FakeResponse(404, {})

        async def post(self, url: str, headers=None, json=None):
            remote_calls.append(("POST", url, json))
            if url.endswith("/clear"):
                for lid in remote_store:
                    if f"/lists/{lid}/clear" in url:
                        remote_store[lid] = [i for i in remote_store[lid] if i.get("status") != "completed"]
                return FakeResponse(204, {})
            for lid, items in remote_store.items():
                if url.endswith(f"/lists/{lid}/tasks"):
                    new_item = {
                        "id": f"gt_new_{len(items) + 1}",
                        "title": json["title"],
                        "notes": json.get("notes", ""),
                        "due": json.get("due"),
                        "status": "needsAction",
                        "position": "00000000000000000099",
                        "updated": "2026-11-27T12:00:00.000Z",
                    }
                    items.append(new_item)
                    return FakeResponse(200, new_item)
            return FakeResponse(404, {})

        async def patch(self, url: str, headers=None, json=None):
            remote_calls.append(("PATCH", url, json))
            for lid, items in remote_store.items():
                for item in items:
                    if url.endswith(f"/lists/{lid}/tasks/{item['id']}"):
                        item.update({k: v for k, v in (json or {}).items() if v is not None})
                        item["updated"] = "2026-11-27T12:05:00.000Z"
                        return FakeResponse(200, item)
            return FakeResponse(404, {})

        async def delete(self, url: str, headers=None):
            remote_calls.append(("DELETE", url, None))
            for lid, items in remote_store.items():
                for idx, item in enumerate(items):
                    if url.endswith(f"/lists/{lid}/tasks/{item['id']}"):
                        items.pop(idx)
                        return FakeResponse(204, {})
            return FakeResponse(404, {})

    monkeypatch.setattr(gtasks_module.httpx, "AsyncClient", FakeAsyncClient)
    # Avoid live external calls for calendar/weather/briefing during bundle test
    orig_get_cal = cal_module.get_calendar_context
    monkeypatch.setattr(
        cal_module,
        "get_calendar_context",
        lambda settings, now, force_refresh=False: orig_get_cal(
            {**settings, "use_mock_data": True}, now, force_refresh=False
        ),
    )
    orig_get_weather = reg_module.weather_svc.get_weather_context
    monkeypatch.setattr(
        reg_module.weather_svc,
        "get_weather_context",
        lambda settings, now, force_refresh=False: orig_get_weather(
            {**settings, "use_mock_data": True}, now, force_refresh=False
        ),
    )
    orig_get_briefing = gemini_module.get_daily_briefing
    monkeypatch.setattr(
        gemini_module,
        "get_daily_briefing",
        lambda settings, now, force_refresh=False: orig_get_briefing(
            {**settings, "use_mock_data": True}, now, force_refresh=False
        ),
    )
    orig_get_german = german_module.get_german_context
    monkeypatch.setattr(
        german_module,
        "get_german_context",
        lambda settings, now, force_refresh=False: orig_get_german(
            {**settings, "use_mock_data": True}, now, force_refresh=False
        ),
    )

    # 1. Sync from Google replaces local tasks cleanly, computes due badges, and nests subtask under parent
    sync_res = client.post("/api/tasks/sync")
    assert sync_res.status_code == 200
    body = sync_res.json()
    assert body["tasks_meta"]["source"] == "google"
    assert len(body["tasks_meta"]["lists"]) == 2
    titles = [t["title"] for t in body["tasks"]]
    assert "Local Fallback Task" not in titles
    # Ordering: OVERDUE first, then TODAY parent + its nested subtask right after, then TMRW
    assert titles[0] == "Fix overdue ticket"
    assert body["tasks"][0]["due_badge"] == "OVERDUE"
    assert body["tasks"][0]["due_urgent"] is True
    assert titles[1] == "Ship v3.3 Google Tasks"
    assert body["tasks"][1]["due_badge"] == "TODAY"
    assert titles[2] == "Test offline checkbox replay"
    assert body["tasks"][2]["is_subtask"] is True
    assert titles[3] == "Buy coffee beans"
    assert body["tasks"][3]["due_badge"] == "TMRW"

    parent_id = body["tasks"][1]["id"]

    # 2. Stable integer IDs preserved across re-sync (critical for Kindle offline outbox replay!)
    resync = client.post("/api/tasks/sync").json()
    parent_after = next(t for t in resync["tasks"] if t["google_task_id"] == "gt_1")
    assert parent_after["id"] == parent_id

    # 3. 2-way Create pushes to Google Tasks
    created = client.post(
        "/api/tasks",
        json={
            "title": "Pack Kindle charger",
            "list_id": "list_personal",
            "due_date": tomorrow_str,
            "notes": "USB-C cable",
        },
    )
    assert created.status_code == 200
    new_task = created.json()["task"]
    assert new_task["source"] == "google"
    assert new_task["google_list_id"] == "list_personal"
    assert any(m == "POST" and "/lists/list_personal/tasks" in u for m, u, _ in remote_calls)

    # 4. Offline Kindle outbox replay (`task:set:<id>:1`) patches Google Tasks remotely
    batch = client.post(
        "/api/sync/batch",
        json={"actions": [{"action": f"task:set:{parent_id}:1", "current_screen": "planner"}], "refresh_feeds": False},
    )
    assert batch.status_code == 200
    assert any(
        m == "PATCH" and "/lists/list_work/tasks/gt_1" in u and p and p.get("status") == "completed"
        for m, u, p in remote_calls
    )

    # 5. Kindle Planner & per-list filter screen render tabs, due badges, subtask arrow & notes
    planner_html = client.get("/kindle?screen=planner").text
    assert "GOOGLE TASKS" in planner_html
    assert "SYNC GOOGLE TASKS" in planner_html
    assert 'data-action="nav:planner_list_0"' in planner_html
    assert 'data-action="nav:planner_list_1"' in planner_html
    assert "OVERDUE" in planner_html
    assert "Verify 2-way sync and e-ink badges" in planner_html

    list1_html = client.get("/kindle?screen=planner_list_1").text
    assert "Buy coffee beans" in list1_html
    assert "Fix overdue ticket" not in list1_html

    # 6. Offline ZIP bundle dynamically includes planner_list_0 & planner_list_1 when >=2 lists exist
    bundle_resp = client.get("/api/offline-bundle?force_refresh=0&include_slots=0")
    assert bundle_resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle_resp.content)) as zf:
        names = set(zf.namelist())
        assert "screens/planner_list_0.png" in names
        assert "screens/planner_list_1.png" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert "planner_list_0" in manifest["screens"]
        assert "planner_list_1" in manifest["screens"]

    # 7. Delete & clear-completed push to Google Tasks
    del_res = client.delete(f"/api/tasks/{new_task['id']}")
    assert del_res.status_code == 200
    assert any(m == "DELETE" and "/lists/list_personal/tasks/" in u for m, u, _ in remote_calls)

    clear_res = client.post("/api/tasks/clear-completed?list_id=list_work")
    assert clear_res.status_code == 200
    assert any(m == "POST" and "/lists/list_work/clear" in u for m, u, _ in remote_calls)

    # 8. Disconnect Google -> local fallback task seamlessly returns
    disc = client.post("/api/google/disconnect")
    assert disc.status_code == 200
    after_disc = client.get("/api/tasks").json()
    assert after_disc["tasks_meta"]["source"] == "local"
    assert any(t["title"] == "Local Fallback Task" for t in after_disc["tasks"])


async def _async_val(val):
    return val

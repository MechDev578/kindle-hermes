"""Two-way Google Tasks API v1 sync service with SQLite offline mirror (reuses Google OAuth2)."""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from backend.database import db
from backend.services import google_cal_svc

logger = logging.getLogger(__name__)

GOOGLE_TASKS_BASE = "https://tasks.googleapis.com/tasks/v1"
_TASKS_TTL_SECONDS = 45.0
_LAST_SYNC_TS: float = 0.0
_LAST_SYNC_ERROR: str = ""


def clear_google_tasks_cache() -> None:
    global _LAST_SYNC_TS
    _LAST_SYNC_TS = 0.0


def _touch_cache_after_write() -> None:
    """Keep the SQLite mirror warm for a short window after a write-through mutation."""
    global _LAST_SYNC_TS
    min_fresh_ts = time.time() - _TASKS_TTL_SECONDS + 15.0
    if _LAST_SYNC_TS < min_fresh_ts:
        _LAST_SYNC_TS = min_fresh_ts


def get_last_error() -> str:
    return _LAST_SYNC_ERROR


def is_google_tasks_active(settings: dict[str, Any] | None = None) -> bool:
    """True when Google OAuth has the Tasks scope and live mode is active."""
    if settings is None:
        settings = db.get_all_settings()
    if settings.get("use_mock_data"):
        return False
    oauth = db.get_google_oauth()
    return bool(oauth.get("connected") and oauth.get("has_tasks_scope"))


def _parse_google_due_date(raw_due: str | None) -> str:
    """Extract YYYY-MM-DD from Google Tasks RFC3339 due string (e.g. 2026-11-28T00:00:00.000Z)."""
    if not raw_due:
        return ""
    s = str(raw_due).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def _to_google_due_rfc3339(due_date: str | None) -> str | None:
    if not due_date:
        return None
    s = str(due_date).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[:10]}T00:00:00.000Z"
    return None


def decorate_and_sort_tasks(
    tasks: list[dict[str, Any]],
    now: datetime,
    active_list_id: str = "",
) -> list[dict[str, Any]]:
    """Attach e-ink due-date badges, subtask flags, and smart ordering (Overdue -> Today -> Upcoming -> Undated)."""
    today = now.date()
    tomorrow = today + timedelta(days=1)

    filtered = [
        dict(t)
        for t in tasks
        if not active_list_id or t.get("google_list_id") == active_list_id
    ]

    by_gid: dict[str, dict[str, Any]] = {}
    for t in filtered:
        due_str = (t.get("due_date") or "").strip()
        due_badge = ""
        due_urgent = False
        due_sort = (9, "9999-99-99")
        if due_str:
            try:
                d = date.fromisoformat(due_str[:10])
                if d < today:
                    due_badge = "OVERDUE"
                    due_urgent = not t.get("completed")
                    due_sort = (0, due_str)
                elif d == today:
                    due_badge = "TODAY"
                    due_urgent = not t.get("completed")
                    due_sort = (1, due_str)
                elif d == tomorrow:
                    due_badge = "TMRW"
                    due_sort = (2, due_str)
                else:
                    due_badge = d.strftime("%b %d").upper().replace(" 0", " ")
                    due_sort = (3, due_str)
            except ValueError:
                pass
        t["due_badge"] = due_badge
        t["due_urgent"] = due_urgent
        t["_due_sort"] = due_sort
        t["is_subtask"] = bool(t.get("google_parent_id"))
        if t.get("google_task_id"):
            by_gid[t["google_task_id"]] = t

    # Group subtasks right under their parent when both are present in the view
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for t in filtered:
        parent_id = t.get("google_parent_id") or ""
        if parent_id and parent_id in by_gid:
            children_by_parent.setdefault(parent_id, []).append(t)
        else:
            roots.append(t)

    def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            1 if item.get("completed") else 0,
            item["_due_sort"],
            int(item.get("priority") or 1),
            item.get("google_position") or "",
            int(item.get("position") or 0),
            int(item.get("id") or 0),
        )

    roots.sort(key=_sort_key)

    ordered: list[dict[str, Any]] = []
    for r in roots:
        ordered.append(r)
        gid = r.get("google_task_id") or ""
        if gid and gid in children_by_parent:
            kids = sorted(children_by_parent[gid], key=_sort_key)
            ordered.extend(kids)

    for item in ordered:
        item.pop("_due_sort", None)
    return ordered


async def refresh_task_lists(auto_select_defaults: bool = False) -> list[dict[str, Any]]:
    global _LAST_SYNC_ERROR
    token = await google_cal_svc._ensure_valid_access_token()
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(
            f"{GOOGLE_TASKS_BASE}/users/@me/lists",
            headers={"Authorization": f"Bearer {token}"},
            params={"maxResults": 50},
        )
        if resp.status_code == 403:
            _LAST_SYNC_ERROR = "Google Tasks permission missing or API not enabled — reconnect Google in /app."
            raise RuntimeError(_LAST_SYNC_ERROR)
        resp.raise_for_status()
        items = resp.json().get("items", [])

    task_lists: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        lid = (item.get("id") or "").strip()
        if not lid:
            continue
        task_lists.append(
            {
                "id": lid,
                "title": (item.get("title") or "My Tasks").strip(),
                "is_default": idx == 0,
            }
        )

    oauth = db.get_google_oauth()
    valid_ids = {tl["id"] for tl in task_lists}
    kept_ids = [lid for lid in (oauth.get("selected_task_list_ids") or []) if lid in valid_ids]
    if auto_select_defaults or not kept_ids:
        selected_ids = [tl["id"] for tl in task_lists]
    else:
        selected_ids = kept_ids

    default_list_id = oauth.get("default_task_list_id") or ""
    if default_list_id not in valid_ids:
        default_list_id = selected_ids[0] if selected_ids else (task_lists[0]["id"] if task_lists else "")

    db.update_google_oauth(
        {
            "task_lists": task_lists,
            "selected_task_list_ids": selected_ids,
            "default_task_list_id": default_list_id,
        }
    )
    _LAST_SYNC_ERROR = ""
    clear_google_tasks_cache()
    return task_lists


async def sync_from_google(now: datetime, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Pull remote Google Tasks across selected lists into SQLite mirror."""
    global _LAST_SYNC_TS, _LAST_SYNC_ERROR
    if not force_refresh and (time.time() - _LAST_SYNC_TS) < _TASKS_TTL_SECONDS:
        return db.list_tasks(source="google")

    oauth = db.get_google_oauth()
    if not oauth.get("connected") or not oauth.get("has_tasks_scope"):
        return []

    task_lists = oauth.get("task_lists") or []
    selected_ids = oauth.get("selected_task_list_ids") or []
    if not task_lists:
        task_lists = await refresh_task_lists(auto_select_defaults=True)
        oauth = db.get_google_oauth()
        selected_ids = oauth.get("selected_task_list_ids") or []

    if not selected_ids and task_lists:
        selected_ids = [task_lists[0]["id"]]

    title_by_id = {tl["id"]: tl["title"] for tl in task_lists}
    token = await google_cal_svc._ensure_valid_access_token()
    completed_cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")

    async def fetch_list(client: httpx.AsyncClient, lid: str) -> list[dict[str, Any]]:
        encoded = urllib.parse.quote(lid, safe="")
        resp = await client.get(
            f"{GOOGLE_TASKS_BASE}/lists/{encoded}/tasks",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "showCompleted": "true",
                "showHidden": "false",
                "showDeleted": "false",
                "completedMin": completed_cutoff,
                "maxResults": 100,
            },
        )
        if resp.status_code == 404:
            return []
        if resp.status_code == 403:
            raise RuntimeError("Google Tasks API 403 (enable Google Tasks API & reconnect Google in /app)")
        resp.raise_for_status()
        rows: list[dict[str, Any]] = []
        for item in resp.json().get("items", []):
            if item.get("deleted") or item.get("hidden"):
                continue
            title = (item.get("title") or "").strip()
            tid = (item.get("id") or "").strip()
            if not title or not tid:
                continue
            rows.append(
                {
                    "google_task_id": tid,
                    "google_list_id": lid,
                    "google_list_title": title_by_id.get(lid, "Tasks"),
                    "google_parent_id": (item.get("parent") or "").strip(),
                    "google_position": (item.get("position") or "").strip(),
                    "google_updated": (item.get("updated") or "").strip(),
                    "title": title,
                    "notes": (item.get("notes") or "").strip(),
                    "due_date": _parse_google_due_date(item.get("due")),
                    "completed": (item.get("status") == "completed"),
                }
            )
        return rows

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            batches = await asyncio.gather(*(fetch_list(client, lid) for lid in selected_ids))
        merged = [row for batch in batches for row in batch]
        synced = db.upsert_google_tasks(merged, selected_ids)
        _LAST_SYNC_TS = time.time()
        _LAST_SYNC_ERROR = ""
        return synced
    except Exception as exc:
        logger.warning("Google Tasks sync failed (using cached mirror): %s", exc)
        _LAST_SYNC_ERROR = str(exc)
        return db.list_tasks(source="google")


async def get_tasks_context(
    settings: dict[str, Any],
    now: datetime,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return formatted tasks, source metadata, and Google Task list tabs for Kindle & /app."""
    active = is_google_tasks_active(settings)
    oauth = db.get_google_oauth()

    if active:
        raw_tasks = await sync_from_google(now, force_refresh=force_refresh)
        selected_ids = set(oauth.get("selected_task_list_ids") or [])
        all_lists = [
            tl for tl in (oauth.get("task_lists") or []) if not selected_ids or tl["id"] in selected_ids
        ]
        # Count open tasks per list for Kindle filter tabs
        counts: dict[str, int] = {}
        for t in raw_tasks:
            if not t.get("completed"):
                lid = t.get("google_list_id") or ""
                counts[lid] = counts.get(lid, 0) + 1
        list_tabs = [
            {"id": tl["id"], "title": tl["title"], "open_count": counts.get(tl["id"], 0)}
            for tl in all_lists
        ]
        tasks = decorate_and_sort_tasks(raw_tasks, now)
        return {
            "source": "google",
            "tasks": tasks,
            "lists": list_tabs,
            "default_list_id": oauth.get("default_task_list_id") or (list_tabs[0]["id"] if list_tabs else ""),
            "connected": True,
            "needs_reconnect": False,
            "error": _LAST_SYNC_ERROR,
        }

    raw_local = db.list_tasks(source="local")
    tasks = decorate_and_sort_tasks(raw_local, now)
    return {
        "source": "local",
        "tasks": tasks,
        "lists": [],
        "default_list_id": "",
        "connected": False,
        "needs_reconnect": bool(oauth.get("connected") and not oauth.get("has_tasks_scope")),
        "error": _LAST_SYNC_ERROR,
    }


# ---------- Two-Way Write-Through Mutations (Kindle + Companion -> Google Tasks) ----------


async def create_task_two_way(
    title: str,
    priority: int = 1,
    due_date: str = "",
    notes: str = "",
    list_id: str = "",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean = title.strip()
    if not clean:
        raise ValueError("Task title cannot be empty")
    if settings is None:
        settings = db.get_all_settings()

    if not is_google_tasks_active(settings):
        return db.create_task(clean, priority=priority, due_date=due_date, notes=notes, source="local")

    oauth = db.get_google_oauth()
    target_list = (
        list_id.strip()
        or oauth.get("default_task_list_id")
        or (oauth.get("selected_task_list_ids") or [""])[0]
        or "@default"
    )
    list_title = next(
        (tl["title"] for tl in (oauth.get("task_lists") or []) if tl["id"] == target_list),
        "My Tasks",
    )

    body: dict[str, Any] = {"title": clean, "status": "needsAction"}
    if notes.strip():
        body["notes"] = notes.strip()
    due_rfc = _to_google_due_rfc3339(due_date)
    if due_rfc:
        body["due"] = due_rfc

    token = await google_cal_svc._ensure_valid_access_token()
    encoded = urllib.parse.quote(target_list, safe="")
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(
            f"{GOOGLE_TASKS_BASE}/lists/{encoded}/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        resp.raise_for_status()
        created = resp.json()

    _touch_cache_after_write()
    return db.create_task(
        created.get("title") or clean,
        priority=priority,
        notes=created.get("notes") or notes,
        due_date=_parse_google_due_date(created.get("due")) or due_date,
        source="google",
        google_task_id=created.get("id") or "",
        google_list_id=target_list,
        google_list_title=list_title,
        google_parent_id=created.get("parent") or "",
        google_position=created.get("position") or "",
        google_updated=created.get("updated") or "",
        completed=(created.get("status") == "completed"),
    )


async def set_task_completed_two_way(
    task_id: int,
    completed: bool,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing = db.get_task(task_id)
    if not existing:
        return None
    updated = db.update_task(task_id, completed=completed)
    if existing.get("source") == "google" and existing.get("google_task_id") and is_google_tasks_active(settings):
        await _patch_google_task(
            existing["google_list_id"] or "@default",
            existing["google_task_id"],
            {"status": "completed"} if completed else {"status": "needsAction", "completed": None},
            task_id=task_id,
        )
    return db.get_task(task_id) or updated


async def toggle_task_two_way(
    task_id: int,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing = db.get_task(task_id)
    if not existing:
        return None
    return await set_task_completed_two_way(task_id, not existing["completed"], settings=settings)


async def update_task_two_way(
    task_id: int,
    title: str | None = None,
    completed: bool | None = None,
    priority: int | None = None,
    due_date: str | None = None,
    notes: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing = db.get_task(task_id)
    if not existing:
        return None
    updated = db.update_task(
        task_id,
        title=title,
        completed=completed,
        priority=priority,
        due_date=due_date,
        notes=notes,
    )
    if existing.get("source") == "google" and existing.get("google_task_id") and is_google_tasks_active(settings):
        patch_body: dict[str, Any] = {}
        if title is not None and title.strip():
            patch_body["title"] = title.strip()
        if notes is not None:
            patch_body["notes"] = notes.strip()
        if due_date is not None:
            patch_body["due"] = _to_google_due_rfc3339(due_date)
        if completed is not None:
            patch_body["status"] = "completed" if completed else "needsAction"
            if not completed:
                patch_body["completed"] = None
        if patch_body:
            await _patch_google_task(
                existing["google_list_id"] or "@default",
                existing["google_task_id"],
                patch_body,
                task_id=task_id,
            )
    return db.get_task(task_id) or updated


async def delete_task_two_way(
    task_id: int,
    settings: dict[str, Any] | None = None,
) -> bool:
    existing = db.get_task(task_id)
    if not existing:
        return False
    if existing.get("source") == "google" and existing.get("google_task_id") and is_google_tasks_active(settings):
        try:
            token = await google_cal_svc._ensure_valid_access_token()
            lid = urllib.parse.quote(existing["google_list_id"] or "@default", safe="")
            tid = urllib.parse.quote(existing["google_task_id"], safe="")
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.delete(
                    f"{GOOGLE_TASKS_BASE}/lists/{lid}/tasks/{tid}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code not in (200, 204, 404):
                    resp.raise_for_status()
            _touch_cache_after_write()
        except Exception as exc:
            logger.warning("Failed remote Google Task delete for %s: %s", task_id, exc)
    return db.delete_task(task_id)


async def clear_completed_two_way(
    list_id: str = "",
    settings: dict[str, Any] | None = None,
) -> int:
    if settings is None:
        settings = db.get_all_settings()
    if not is_google_tasks_active(settings):
        return db.clear_completed_tasks(source="local")

    oauth = db.get_google_oauth()
    target_lists = [list_id] if list_id else (oauth.get("selected_task_list_ids") or [])
    if target_lists:
        try:
            token = await google_cal_svc._ensure_valid_access_token()
            async with httpx.AsyncClient(timeout=12.0) as client:
                for lid in target_lists:
                    encoded = urllib.parse.quote(lid, safe="")
                    resp = await client.post(
                        f"{GOOGLE_TASKS_BASE}/lists/{encoded}/clear",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if resp.status_code not in (200, 204, 404):
                        logger.warning("Google Tasks clear(%s) returned HTTP %d", lid, resp.status_code)
            _touch_cache_after_write()
        except Exception as exc:
            logger.warning("Failed remote Google Tasks clear: %s", exc)
    return db.clear_completed_tasks(source="google", google_list_id=list_id or None)


async def _patch_google_task(
    list_id: str,
    google_task_id: str,
    patch_body: dict[str, Any],
    task_id: int | None = None,
) -> None:
    try:
        token = await google_cal_svc._ensure_valid_access_token()
        lid = urllib.parse.quote(list_id, safe="")
        tid = urllib.parse.quote(google_task_id, safe="")
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.patch(
                f"{GOOGLE_TASKS_BASE}/lists/{lid}/tasks/{tid}",
                headers={"Authorization": f"Bearer {token}"},
                json=patch_body,
            )
            if resp.status_code == 404:
                return
            resp.raise_for_status()
            data = resp.json()
            if task_id is not None and data.get("updated"):
                db.update_task(task_id, google_updated=data["updated"])
        _touch_cache_after_write()
    except Exception as exc:
        logger.warning("Google Tasks PATCH failed for %s/%s: %s", list_id, google_task_id, exc)

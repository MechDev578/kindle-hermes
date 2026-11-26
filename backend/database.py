"""SQLite persistence layer for KindleHermes."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.config import DB_PATH, config

DEFAULT_MODULES = ["today", "calendar", "planner", "pomodoro", "weather", "briefing"]
DEFAULT_LOCKSCREEN_SLIDES = ["smart", "calendar", "planning", "weather"]

DEFAULT_TASKS = [
    ("Review project milestones & daily plan", 0, 1),
    ("Review weekly calendar and time-block deep work", 1, 1),
    ("Read 30 pages on Paperwhite before bed", 0, 2),
    ("Evening walk & mobility stretch", 0, 2),
]


class HermesDB:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 1,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pomodoro_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL DEFAULT 'focus',
                    status TEXT NOT NULL DEFAULT 'idle',
                    duration_sec INTEGER NOT NULL DEFAULT 1500,
                    remaining_sec INTEGER NOT NULL DEFAULT 1500,
                    ends_at REAL,
                    completed_sessions INTEGER NOT NULL DEFAULT 0,
                    alert_pending INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS briefing_cache (
                    date_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS google_oauth (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    client_id TEXT NOT NULL DEFAULT '',
                    client_secret TEXT NOT NULL DEFAULT '',
                    access_token TEXT NOT NULL DEFAULT '',
                    refresh_token TEXT NOT NULL DEFAULT '',
                    expires_at REAL NOT NULL DEFAULT 0,
                    account_email TEXT NOT NULL DEFAULT '',
                    calendars_json TEXT NOT NULL DEFAULT '[]',
                    selected_ids_json TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL DEFAULT 0
                );
                """
            )

            conn.execute(
                "INSERT OR IGNORE INTO google_oauth (id, updated_at) VALUES (1, ?)",
                (time.time(),),
            )

            # Seed default settings only if missing
            default_settings = {
                "use_mock_data": "true" if config.use_mock_data else "false",
                "daily_focus": "Build deep focus blocks & keep the evening distraction-free",
                "calendar_ics_url": config.calendar_ics_url,
                "weather_city": config.weather_city,
                "weather_lat": str(config.weather_lat),
                "weather_lon": str(config.weather_lon),
                "timezone": config.timezone,
                "auto_sync_location": "true",
                "location_source": "default",
                "location_updated_at": "",
                "lockscreen_mode": "smart",
                "lockscreen_slides": json.dumps(DEFAULT_LOCKSCREEN_SLIDES),
                "lockscreen_cycle_index": "0",
                "enabled_modules": json.dumps(DEFAULT_MODULES),
            }
            for k, v in default_settings.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (k, v),
                )

            # Seed initial tasks if table is empty
            row = conn.execute("SELECT COUNT(*) AS cnt FROM tasks").fetchone()
            if row["cnt"] == 0:
                now_iso = datetime.now(timezone.utc).isoformat()
                for idx, (title, completed, priority) in enumerate(DEFAULT_TASKS):
                    conn.execute(
                        """
                        INSERT INTO tasks (title, completed, priority, position, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (title, completed, priority, idx, now_iso),
                    )

            # Seed Pomodoro singleton row
            conn.execute(
                """
                INSERT OR IGNORE INTO pomodoro_state
                    (id, mode, status, duration_sec, remaining_sec, ends_at, completed_sessions, alert_pending, updated_at)
                VALUES (1, 'focus', 'idle', 1500, 1500, NULL, 0, 0, ?)
                """,
                (time.time(),),
            )

    # ---------- Settings ----------

    def get_all_settings(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        raw = {r["key"]: r["value"] for r in rows}
        return {
            "use_mock_data": raw.get("use_mock_data", "true").lower() == "true",
            "daily_focus": raw.get("daily_focus", ""),
            "calendar_ics_url": raw.get("calendar_ics_url", ""),
            "weather_city": raw.get("weather_city", config.weather_city),
            "weather_lat": float(raw.get("weather_lat", str(config.weather_lat))),
            "weather_lon": float(raw.get("weather_lon", str(config.weather_lon))),
            "timezone": raw.get("timezone", config.timezone),
            "auto_sync_location": raw.get("auto_sync_location", "true").lower() == "true",
            "location_source": raw.get("location_source", "default"),
            "location_updated_at": raw.get("location_updated_at", ""),
            "lockscreen_mode": raw.get("lockscreen_mode", "smart"),
            "lockscreen_slides": self._parse_json_list(
                raw.get("lockscreen_slides"), DEFAULT_LOCKSCREEN_SLIDES
            ),
            "lockscreen_cycle_index": int(raw.get("lockscreen_cycle_index", "0")),
            "enabled_modules": self._parse_json_list(
                raw.get("enabled_modules"), DEFAULT_MODULES
            ),
        }

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "use_mock_data",
            "daily_focus",
            "calendar_ics_url",
            "weather_city",
            "weather_lat",
            "weather_lon",
            "timezone",
            "auto_sync_location",
            "location_source",
            "location_updated_at",
            "lockscreen_mode",
            "lockscreen_slides",
            "lockscreen_cycle_index",
            "enabled_modules",
        }
        with self._conn() as conn:
            for key, val in updates.items():
                if key not in allowed or val is None:
                    continue
                if isinstance(val, bool):
                    serialized = "true" if val else "false"
                elif isinstance(val, (list, dict)):
                    serialized = json.dumps(val)
                else:
                    serialized = str(val)
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, serialized),
                )
        return self.get_all_settings()

    def advance_lockscreen_cycle(self) -> str:
        settings = self.get_all_settings()
        slides = settings["lockscreen_slides"] or ["smart"]
        idx = settings["lockscreen_cycle_index"] % len(slides)
        selected = slides[idx]
        next_idx = (idx + 1) % len(slides)
        self.update_settings({"lockscreen_cycle_index": next_idx})
        return selected

    @staticmethod
    def _parse_json_list(raw: str | None, fallback: list[str]) -> list[str]:
        if not raw:
            return list(fallback)
        try:
            val = json.loads(raw)
            if isinstance(val, list) and val:
                return [str(x) for x in val]
        except Exception:
            pass
        return list(fallback)

    # ---------- Tasks ----------

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, title, completed, priority, position, created_at "
                "FROM tasks ORDER BY completed ASC, priority ASC, position ASC, id ASC"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "completed": bool(r["completed"]),
                "priority": r["priority"],
                "position": r["position"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def create_task(self, title: str, priority: int = 1) -> dict[str, Any]:
        clean = title.strip()
        if not clean:
            raise ValueError("Task title cannot be empty")
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM tasks").fetchone()
            cur = conn.execute(
                "INSERT INTO tasks (title, completed, priority, position, created_at) VALUES (?, 0, ?, ?, ?)",
                (clean, int(priority), int(row["pos"]), now_iso),
            )
            task_id = cur.lastrowid
            r = conn.execute(
                "SELECT id, title, completed, priority, position, created_at FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return {
            "id": r["id"],
            "title": r["title"],
            "completed": bool(r["completed"]),
            "priority": r["priority"],
            "position": r["position"],
            "created_at": r["created_at"],
        }

    def toggle_task(self, task_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            r = conn.execute("SELECT id, completed FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not r:
                return None
            new_val = 0 if r["completed"] else 1
            conn.execute("UPDATE tasks SET completed = ? WHERE id = ?", (new_val, task_id))
            updated = conn.execute(
                "SELECT id, title, completed, priority, position, created_at FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return {
            "id": updated["id"],
            "title": updated["title"],
            "completed": bool(updated["completed"]),
            "priority": updated["priority"],
            "position": updated["position"],
            "created_at": updated["created_at"],
        }

    def update_task(
        self, task_id: int, title: str | None = None, completed: bool | None = None, priority: int | None = None
    ) -> dict[str, Any] | None:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not r:
                return None
            new_title = title.strip() if title is not None and title.strip() else r["title"]
            new_completed = (1 if completed else 0) if completed is not None else r["completed"]
            new_priority = int(priority) if priority is not None else r["priority"]
            conn.execute(
                "UPDATE tasks SET title = ?, completed = ?, priority = ? WHERE id = ?",
                (new_title, new_completed, new_priority, task_id),
            )
            updated = conn.execute(
                "SELECT id, title, completed, priority, position, created_at FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return {
            "id": updated["id"],
            "title": updated["title"],
            "completed": bool(updated["completed"]),
            "priority": updated["priority"],
            "position": updated["position"],
            "created_at": updated["created_at"],
        }

    def delete_task(self, task_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return cur.rowcount > 0

    def clear_completed_tasks(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE completed = 1")
            return cur.rowcount

    # ---------- Pomodoro ----------

    def get_pomodoro_raw(self) -> dict[str, Any]:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM pomodoro_state WHERE id = 1").fetchone()
        return dict(r)

    def save_pomodoro_raw(self, state: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE pomodoro_state
                SET mode = ?, status = ?, duration_sec = ?, remaining_sec = ?,
                    ends_at = ?, completed_sessions = ?, alert_pending = ?, updated_at = ?
                WHERE id = 1
                """,
                (
                    state["mode"],
                    state["status"],
                    int(state["duration_sec"]),
                    int(state["remaining_sec"]),
                    state["ends_at"],
                    int(state["completed_sessions"]),
                    int(state["alert_pending"]),
                    time.time(),
                ),
            )

    # ---------- Briefing Cache ----------

    def get_briefing_cache(self, date_key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            r = conn.execute(
                "SELECT date_key, payload_json, source, updated_at FROM briefing_cache WHERE date_key = ?",
                (date_key,),
            ).fetchone()
        if not r:
            return None
        payload = json.loads(r["payload_json"])
        payload["source"] = r["source"]
        payload["updated_at"] = r["updated_at"]
        return payload

    def save_briefing_cache(self, date_key: str, payload: dict[str, Any], source: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO briefing_cache (date_key, payload_json, source, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (date_key, json.dumps(payload), source, now_iso),
            )

    # ---------- Google Calendar OAuth ----------

    def get_google_oauth(self) -> dict[str, Any]:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM google_oauth WHERE id = 1").fetchone()
        data = dict(r) if r else {}
        client_id = (data.get("client_id") or "").strip() or config.google_client_id
        client_secret = (data.get("client_secret") or "").strip() or config.google_client_secret
        try:
            calendars = json.loads(data.get("calendars_json") or "[]")
        except Exception:
            calendars = []
        try:
            selected_ids = json.loads(data.get("selected_ids_json") or "[]")
        except Exception:
            selected_ids = []
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "has_credentials": bool(client_id and client_secret),
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": float(data.get("expires_at") or 0),
            "account_email": data.get("account_email", ""),
            "connected": bool(data.get("refresh_token") or data.get("access_token")),
            "calendars": calendars if isinstance(calendars, list) else [],
            "selected_ids": selected_ids if isinstance(selected_ids, list) else [],
            "updated_at": float(data.get("updated_at") or 0),
        }

    def update_google_oauth(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "client_id",
            "client_secret",
            "access_token",
            "refresh_token",
            "expires_at",
            "account_email",
            "calendars",
            "selected_ids",
        }
        col_map = {
            "calendars": "calendars_json",
            "selected_ids": "selected_ids_json",
        }
        sets: list[str] = ["updated_at = ?"]
        vals: list[Any] = [time.time()]
        for k, v in updates.items():
            if k not in allowed or v is None:
                continue
            col = col_map.get(k, k)
            if k in ("calendars", "selected_ids"):
                v = json.dumps(v)
            sets.append(f"{col} = ?")
            vals.append(v)
        vals.append(1)
        with self._conn() as conn:
            conn.execute(f"UPDATE google_oauth SET {', '.join(sets)} WHERE id = ?", vals)
        return self.get_google_oauth()

    def clear_google_tokens(self) -> dict[str, Any]:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE google_oauth
                SET access_token = '', refresh_token = '', expires_at = 0,
                    account_email = '', calendars_json = '[]', selected_ids_json = '[]',
                    updated_at = ?
                WHERE id = 1
                """,
                (time.time(),),
            )
        return self.get_google_oauth()


db = HermesDB()

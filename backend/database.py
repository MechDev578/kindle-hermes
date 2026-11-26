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

DEFAULT_MODULES = ["today", "calendar", "planner", "pomodoro", "weather", "briefing", "deutsch"]
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

                CREATE TABLE IF NOT EXISTS german_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'cloze',
                    cefr TEXT NOT NULL DEFAULT 'A2',
                    category TEXT NOT NULL DEFAULT 'Chunk',
                    prompt_de TEXT NOT NULL,
                    answer_de TEXT NOT NULL,
                    full_de TEXT NOT NULL,
                    hint_en TEXT NOT NULL DEFAULT '',
                    translation_en TEXT NOT NULL DEFAULT '',
                    rule_note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'seed',
                    stability REAL NOT NULL DEFAULT 0.0,
                    difficulty REAL NOT NULL DEFAULT 5.0,
                    reps INTEGER NOT NULL DEFAULT 0,
                    lapses INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'new',
                    due_at REAL NOT NULL DEFAULT 0.0,
                    last_review_at REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS german_classics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT UNIQUE NOT NULL,
                    author TEXT NOT NULL,
                    title TEXT NOT NULL,
                    year TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'Rede',
                    cefr TEXT NOT NULL DEFAULT 'B2',
                    famous_line TEXT NOT NULL DEFAULT '',
                    conversation_tip TEXT NOT NULL DEFAULT '',
                    paragraphs_json TEXT NOT NULL DEFAULT '[]',
                    glosses_json TEXT NOT NULL DEFAULT '[]',
                    xray_json TEXT NOT NULL DEFAULT '[]',
                    mine_cards_json TEXT NOT NULL DEFAULT '[]',
                    word_count INTEGER NOT NULL DEFAULT 60,
                    read_count INTEGER NOT NULL DEFAULT 0,
                    mined INTEGER NOT NULL DEFAULT 0,
                    last_read_at REAL NOT NULL DEFAULT 0.0,
                    is_custom INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS german_daily_cache (
                    date_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    read_done INTEGER NOT NULL DEFAULT 0,
                    quiz_answered INTEGER NOT NULL DEFAULT -1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS german_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    correct INTEGER NOT NULL DEFAULT 1,
                    words_count INTEGER NOT NULL DEFAULT 0,
                    points INTEGER NOT NULL DEFAULT 0,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS german_writing_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt_chunks TEXT NOT NULL DEFAULT '',
                    user_text TEXT NOT NULL,
                    recast_de TEXT NOT NULL,
                    feedback_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
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
                "german_level": "A2",
                "german_daily_goal": "15",
                "german_active_classic": "0",
                "german_active_root": "0",
                "german_drill_flipped": "0",
                "german_drill_choice": "",
            }
            for k, v in default_settings.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (k, v),
                )

            # Smooth upgrade for existing DBs that still have the exact v2.1 default module list
            row_mod = conn.execute("SELECT value FROM settings WHERE key = 'enabled_modules'").fetchone()
            if row_mod:
                try:
                    mods = json.loads(row_mod["value"])
                    if mods == ["today", "calendar", "planner", "pomodoro", "weather", "briefing"]:
                        conn.execute(
                            "UPDATE settings SET value = ? WHERE key = 'enabled_modules'",
                            (json.dumps(DEFAULT_MODULES),),
                        )
                except Exception:
                    pass

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
            "german_level": raw.get("german_level", "A2"),
            "german_daily_goal": int(raw.get("german_daily_goal", "15")),
            "german_active_classic": int(raw.get("german_active_classic", "0")),
            "german_active_root": int(raw.get("german_active_root", "0")),
            "german_drill_flipped": raw.get("german_drill_flipped", "0") == "1",
            "german_drill_choice": raw.get("german_drill_choice", ""),
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
            "german_level",
            "german_daily_goal",
            "german_active_classic",
            "german_active_root",
            "german_drill_flipped",
            "german_drill_choice",
        }
        with self._conn() as conn:
            for key, val in updates.items():
                if key not in allowed or val is None:
                    continue
                if key == "german_drill_flipped":
                    serialized = "1" if bool(val) else "0"
                elif isinstance(val, bool):
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

    # ---------- Hermes Deutsch (A2 -> C2 FSRS, Classics, Daily Reader & Output) ----------

    def seed_german_cards(self, cards: list[dict[str, Any]]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for c in cards:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO german_cards
                        (slug, kind, cefr, category, prompt_de, answer_de, full_de,
                         hint_en, translation_en, rule_note, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c["slug"],
                        c.get("kind", "cloze"),
                        c.get("cefr", "A2"),
                        c.get("category", "Chunk"),
                        c["prompt_de"],
                        c["answer_de"],
                        c["full_de"],
                        c.get("hint_en", ""),
                        c.get("translation_en", ""),
                        c.get("rule_note", ""),
                        c.get("source", "seed"),
                        now_iso,
                    ),
                )

    def seed_german_classics(self, classics: list[dict[str, Any]]) -> None:
        with self._conn() as conn:
            for item in classics:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO german_classics
                        (slug, author, title, year, category, cefr, famous_line,
                         conversation_tip, paragraphs_json, glosses_json, xray_json,
                         mine_cards_json, word_count, is_custom)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["slug"],
                        item["author"],
                        item["title"],
                        item.get("year", ""),
                        item.get("category", "Rede"),
                        item.get("cefr", "B2"),
                        item.get("famous_line", ""),
                        item.get("conversation_tip", ""),
                        json.dumps(item.get("paragraphs", []), ensure_ascii=False),
                        json.dumps(item.get("glosses", []), ensure_ascii=False),
                        json.dumps(item.get("xray", []), ensure_ascii=False),
                        json.dumps(item.get("mine_cards", []), ensure_ascii=False),
                        int(item.get("word_count", 60)),
                        int(item.get("is_custom", 0)),
                    ),
                )

    def list_german_cards(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM german_cards ORDER BY CASE state WHEN 'learning' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, due_at ASC, id ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_german_card(self, card_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM german_cards WHERE id = ?", (card_id,)).fetchone()
        return dict(r) if r else None

    def upsert_german_card(self, card: dict[str, Any]) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO german_cards
                    (slug, kind, cefr, category, prompt_de, answer_de, full_de,
                     hint_en, translation_en, rule_note, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    prompt_de = excluded.prompt_de,
                    answer_de = excluded.answer_de,
                    full_de = excluded.full_de,
                    hint_en = excluded.hint_en,
                    translation_en = excluded.translation_en,
                    rule_note = excluded.rule_note
                """,
                (
                    card["slug"],
                    card.get("kind", "cloze"),
                    card.get("cefr", "B1"),
                    card.get("category", "Chunk"),
                    card["prompt_de"],
                    card["answer_de"],
                    card["full_de"],
                    card.get("hint_en", ""),
                    card.get("translation_en", ""),
                    card.get("rule_note", ""),
                    card.get("source", "custom"),
                    now_iso,
                ),
            )
            r = conn.execute("SELECT * FROM german_cards WHERE slug = ?", (card["slug"],)).fetchone()
        return dict(r)

    def update_german_card_srs(
        self,
        card_id: int,
        *,
        stability: float,
        difficulty: float,
        reps: int,
        lapses: int,
        state: str,
        due_at: float,
        last_review_at: float,
    ) -> dict[str, Any] | None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE german_cards
                SET stability = ?, difficulty = ?, reps = ?, lapses = ?,
                    state = ?, due_at = ?, last_review_at = ?
                WHERE id = ?
                """,
                (stability, difficulty, reps, lapses, state, due_at, last_review_at, card_id),
            )
            r = conn.execute("SELECT * FROM german_cards WHERE id = ?", (card_id,)).fetchone()
        return dict(r) if r else None

    def delete_german_card(self, card_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM german_cards WHERE id = ?", (card_id,))
            return cur.rowcount > 0

    def _hydrate_classic(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        for jcol, key in (
            ("paragraphs_json", "paragraphs"),
            ("glosses_json", "glosses"),
            ("xray_json", "xray"),
            ("mine_cards_json", "mine_cards"),
        ):
            try:
                d[key] = json.loads(d.get(jcol) or "[]")
            except Exception:
                d[key] = []
        d["mined"] = bool(d.get("mined"))
        d["is_custom"] = bool(d.get("is_custom"))
        return d

    def list_german_classics(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM german_classics ORDER BY is_custom DESC, id ASC"
            ).fetchall()
        return [self._hydrate_classic(r) for r in rows]

    def get_german_classic(self, classic_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM german_classics WHERE id = ?", (classic_id,)).fetchone()
        return self._hydrate_classic(r) if r else None

    def insert_custom_german_classic(self, item: dict[str, Any]) -> dict[str, Any]:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO german_classics
                    (slug, author, title, year, category, cefr, famous_line,
                     conversation_tip, paragraphs_json, glosses_json, xray_json,
                     mine_cards_json, word_count, is_custom)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    item["slug"],
                    item["author"],
                    item["title"],
                    item.get("year", ""),
                    item.get("category", "Eigener Text"),
                    item.get("cefr", "B1"),
                    item.get("famous_line", ""),
                    item.get("conversation_tip", ""),
                    json.dumps(item.get("paragraphs", []), ensure_ascii=False),
                    json.dumps(item.get("glosses", []), ensure_ascii=False),
                    json.dumps(item.get("xray", []), ensure_ascii=False),
                    json.dumps(item.get("mine_cards", []), ensure_ascii=False),
                    int(item.get("word_count", 60)),
                ),
            )
            r = conn.execute("SELECT * FROM german_classics WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._hydrate_classic(r)

    def mark_german_classic_read(self, classic_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE german_classics SET read_count = read_count + 1, last_read_at = ? WHERE id = ?",
                (time.time(), classic_id),
            )
            r = conn.execute("SELECT * FROM german_classics WHERE id = ?", (classic_id,)).fetchone()
        return self._hydrate_classic(r) if r else None

    def mark_german_classic_mined(self, classic_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE german_classics SET mined = 1 WHERE id = ?", (classic_id,))

    def get_german_daily_cache(self, date_key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            r = conn.execute(
                "SELECT * FROM german_daily_cache WHERE date_key = ?", (date_key,)
            ).fetchone()
        if not r:
            return None
        payload = json.loads(r["payload_json"])
        payload["date_key"] = r["date_key"]
        payload["source"] = r["source"]
        payload["read_done"] = bool(r["read_done"])
        payload["quiz_answered"] = int(r["quiz_answered"])
        payload["updated_at"] = r["updated_at"]
        return payload

    def save_german_daily_cache(self, date_key: str, payload: dict[str, Any], source: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO german_daily_cache (date_key, payload_json, source, read_done, quiz_answered, updated_at)
                VALUES (?, ?, ?, 0, -1, ?)
                ON CONFLICT(date_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (date_key, json.dumps(payload, ensure_ascii=False), source, now_iso),
            )

    def update_german_daily_progress(
        self, date_key: str, *, read_done: bool | None = None, quiz_answered: int | None = None
    ) -> None:
        sets: list[str] = []
        vals: list[Any] = []
        if read_done is not None:
            sets.append("read_done = ?")
            vals.append(1 if read_done else 0)
        if quiz_answered is not None:
            sets.append("quiz_answered = ?")
            vals.append(int(quiz_answered))
        if not sets:
            return
        vals.append(date_key)
        with self._conn() as conn:
            conn.execute(f"UPDATE german_daily_cache SET {', '.join(sets)} WHERE date_key = ?", vals)

    def log_german_activity(
        self,
        day_key: str,
        kind: str,
        *,
        correct: bool = True,
        words_count: int = 0,
        points: int = 0,
        detail: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO german_activity (day_key, kind, correct, words_count, points, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (day_key, kind, 1 if correct else 0, int(words_count), int(points), detail, time.time()),
            )

    def list_german_activity(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM german_activity ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def save_german_writing(
        self, prompt_chunks: str, user_text: str, recast_de: str, feedback: dict[str, Any]
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO german_writing_log (prompt_chunks, user_text, recast_de, feedback_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (prompt_chunks, user_text, recast_de, json.dumps(feedback, ensure_ascii=False), now_iso),
            )
            r = conn.execute("SELECT * FROM german_writing_log WHERE id = ?", (cur.lastrowid,)).fetchone()
        out = dict(r)
        out["feedback"] = json.loads(out.get("feedback_json") or "{}")
        return out

    def list_german_writing(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM german_writing_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["feedback"] = json.loads(d.get("feedback_json") or "{}")
            except Exception:
                d["feedback"] = {}
            out.append(d)
        return out


db = HermesDB()

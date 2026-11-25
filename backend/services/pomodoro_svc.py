"""Server-authoritative Pomodoro state machine optimized for 1-minute e-ink updates."""

from __future__ import annotations

import math
import time
from typing import Any

from backend.database import db

MODE_DURATIONS: dict[str, tuple[str, int]] = {
    "focus": ("25m Focus", 25 * 60),
    "short_break": ("5m Break", 5 * 60),
    "long_break": ("15m Rest", 15 * 60),
}


def _reconcile_state() -> dict[str, Any]:
    raw = db.get_pomodoro_raw()
    now_ts = time.time()

    if raw["status"] == "running" and raw["ends_at"] is not None:
        rem = int(math.ceil(raw["ends_at"] - now_ts))
        if rem <= 0:
            raw["remaining_sec"] = 0
            raw["status"] = "completed"
            raw["ends_at"] = None
            raw["alert_pending"] = 1
            if raw["mode"] == "focus":
                raw["completed_sessions"] = int(raw["completed_sessions"]) + 1
            db.save_pomodoro_raw(raw)
        else:
            raw["remaining_sec"] = rem
    return raw


def get_pomodoro_view(consume_alert: bool = False) -> dict[str, Any]:
    raw = _reconcile_state()
    duration_sec = max(60, int(raw["duration_sec"]))
    remaining_sec = max(0, int(raw["remaining_sec"]))

    # E-ink friendly minute precision (shows 25m -> 24m -> ... -> 00m)
    remaining_min = int(math.ceil(remaining_sec / 60.0)) if remaining_sec > 0 else 0
    total_min = max(1, duration_sec // 60)
    elapsed_sec = max(0, duration_sec - remaining_sec)
    progress_pct = min(100, max(0, int((elapsed_sec / duration_sec) * 100)))

    # 10 crisp monochrome progress segments
    segments_total = 10
    segments_filled = int(round((progress_pct / 100.0) * segments_total))

    alert_flash = bool(raw["alert_pending"])
    if consume_alert and alert_flash:
        raw["alert_pending"] = 0
        db.save_pomodoro_raw(raw)

    mode_label = MODE_DURATIONS.get(raw["mode"], ("Focus", 1500))[0]
    next_tick_sec = 0
    if raw["status"] == "running" and remaining_sec > 0:
        # Next minute boundary so e-ink updates right when the displayed minute drops
        mod = remaining_sec % 60
        next_tick_sec = mod if mod > 0 else 60

    return {
        "mode": raw["mode"],
        "mode_label": mode_label,
        "status": raw["status"],
        "duration_sec": duration_sec,
        "remaining_sec": remaining_sec,
        "remaining_min": remaining_min,
        "remaining_label": f"{remaining_min:02d}m",
        "total_min": total_min,
        "progress_pct": progress_pct,
        "segments_total": segments_total,
        "segments_filled": segments_filled,
        "completed_sessions": int(raw["completed_sessions"]),
        "alert_flash": alert_flash,
        "next_tick_sec": next_tick_sec,
        "is_running": raw["status"] == "running",
    }


def toggle_start_pause() -> dict[str, Any]:
    raw = _reconcile_state()
    now_ts = time.time()

    if raw["status"] == "running":
        rem = max(0, int(math.ceil((raw["ends_at"] or now_ts) - now_ts)))
        raw["status"] = "paused"
        raw["remaining_sec"] = rem
        raw["ends_at"] = None
        raw["alert_pending"] = 0
    else:
        rem = int(raw["remaining_sec"])
        if raw["status"] == "completed" or rem <= 0:
            rem = int(raw["duration_sec"])
        raw["status"] = "running"
        raw["remaining_sec"] = rem
        raw["ends_at"] = now_ts + rem
        raw["alert_pending"] = 0

    db.save_pomodoro_raw(raw)
    return get_pomodoro_view()


def reset_pomodoro() -> dict[str, Any]:
    raw = _reconcile_state()
    raw["status"] = "idle"
    raw["remaining_sec"] = int(raw["duration_sec"])
    raw["ends_at"] = None
    raw["alert_pending"] = 0
    db.save_pomodoro_raw(raw)
    return get_pomodoro_view()


def set_pomodoro_mode(mode: str) -> dict[str, Any]:
    if mode not in MODE_DURATIONS:
        mode = "focus"
    _, duration_sec = MODE_DURATIONS[mode]
    raw = _reconcile_state()
    raw["mode"] = mode
    raw["status"] = "idle"
    raw["duration_sec"] = duration_sec
    raw["remaining_sec"] = duration_sec
    raw["ends_at"] = None
    raw["alert_pending"] = 0
    db.save_pomodoro_raw(raw)
    return get_pomodoro_view()

"""Configuration loader for KindleHermes (Paperwhite 10th Gen — 1072x1448 @ 300 PPI)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

KINDLE_WIDTH = 1072
KINDLE_HEIGHT = 1448
KINDLE_PPI = 300
DEVICE_NAME = "Kindle Paperwhite 10th Gen (PW4)"

DATA_DIR = ROOT_DIR / "backend" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "hermes.db"

FRONTEND_DIR = ROOT_DIR / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"
STATIC_DIR = FRONTEND_DIR / "static"


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AppConfig:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    timezone: str = os.getenv("TIMEZONE", "Europe/Berlin")
    hermes_token: str = os.getenv("HERMES_TOKEN", "").strip()
    use_mock_data: bool = _env_bool("USE_MOCK_DATA", True)
    calendar_ics_url: str = os.getenv("CALENDAR_ICS_URL", "").strip()
    weather_city: str = os.getenv("WEATHER_CITY", "Berlin").strip()
    weather_lat: float = float(os.getenv("WEATHER_LAT", "52.52"))
    weather_lon: float = float(os.getenv("WEATHER_LON", "13.405"))
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    gemini_use_search_grounding: bool = _env_bool("GEMINI_USE_SEARCH_GROUNDING", False)
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    google_redirect_uri: str = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    chromium_executable_path: str = os.getenv("CHROMIUM_EXECUTABLE_PATH", "").strip()


config = AppConfig()

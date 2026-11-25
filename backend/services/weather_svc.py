"""Weather service using Open-Meteo (no API key required) + high-contrast monochrome SVG icons."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_WEATHER_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_WEATHER_TTL_SECONDS = 600.0

WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear Sky", "sun"),
    1: ("Mainly Clear", "sun"),
    2: ("Partly Cloudy", "partly"),
    3: ("Overcast", "cloud"),
    45: ("Foggy", "fog"),
    48: ("Rime Fog", "fog"),
    51: ("Light Drizzle", "rain"),
    53: ("Drizzle", "rain"),
    55: ("Dense Drizzle", "rain"),
    61: ("Light Rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy Rain", "rain"),
    66: ("Freezing Rain", "rain"),
    67: ("Freezing Rain", "rain"),
    71: ("Light Snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy Snow", "snow"),
    77: ("Snow Grains", "snow"),
    80: ("Rain Showers", "rain"),
    81: ("Mod. Showers", "rain"),
    82: ("Heavy Showers", "rain"),
    85: ("Snow Showers", "snow"),
    86: ("Heavy Snow", "snow"),
    95: ("Thunderstorm", "storm"),
    96: ("Storm + Hail", "storm"),
    99: ("Severe Storm", "storm"),
}

WEATHER_SVG_PATHS: dict[str, str] = {
    "sun": (
        '<circle cx="16" cy="16" r="5.5" fill="none" stroke="currentColor" stroke-width="2.4"/>'
        '<path d="M16 3v4M16 25v4M3 16h4M25 16h4M6.8 6.8l2.8 2.8M22.4 22.4l2.8 2.8'
        'M25.2 6.8l-2.8 2.8M9.6 22.4l-2.8 2.8" stroke="currentColor" stroke-width="2.4" stroke-linecap="square"/>'
    ),
    "partly": (
        '<circle cx="11.5" cy="11.5" r="4" fill="none" stroke="currentColor" stroke-width="2.2"/>'
        '<path d="M11.5 4v2.2M4 11.5h2.2M6.2 6.2l1.6 1.6M16.8 6.2l-1.6 1.6" stroke="currentColor" stroke-width="2.2"/>'
        '<path d="M10 25h13.5a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.5-1.5A4.2 4.2 0 0 0 10 25z" '
        'fill="#FFFFFF" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/>'
    ),
    "cloud": (
        '<path d="M8.5 24h15a5 5 0 0 0 .6-10 6.8 6.8 0 0 0-13.1-1.8A4.8 4.8 0 0 0 8.5 24z" '
        'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/>'
    ),
    "fog": (
        '<path d="M7 14h18M5 19h22M8 24h16M10 9h12" stroke="currentColor" stroke-width="2.4" stroke-linecap="square"/>'
    ),
    "rain": (
        '<path d="M8.5 20h15a4.5 4.5 0 0 0 .5-9 6.2 6.2 0 0 0-12-1.6A4.4 4.4 0 0 0 8.5 20z" '
        'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/>'
        '<path d="M11 23l-1.5 5M16.5 23L15 28M22 23l-1.5 5" stroke="currentColor" stroke-width="2.4" stroke-linecap="square"/>'
    ),
    "snow": (
        '<path d="M8.5 19h15a4.5 4.5 0 0 0 .5-9 6.2 6.2 0 0 0-12-1.6A4.4 4.4 0 0 0 8.5 19z" '
        'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/>'
        '<circle cx="11" cy="24.5" r="1.6" fill="currentColor"/>'
        '<circle cx="16.5" cy="26" r="1.6" fill="currentColor"/>'
        '<circle cx="22" cy="24.5" r="1.6" fill="currentColor"/>'
    ),
    "storm": (
        '<path d="M8.5 19h15a4.5 4.5 0 0 0 .5-9 6.2 6.2 0 0 0-12-1.6A4.4 4.4 0 0 0 8.5 19z" '
        'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/>'
        '<path d="M17.5 19l-3.5 5h3.5l-2 5 6-7h-3.5l2-3z" fill="currentColor"/>'
    ),
}


def weather_svg(icon_name: str, size: int = 32) -> str:
    body = WEATHER_SVG_PATHS.get(icon_name, WEATHER_SVG_PATHS["cloud"])
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 32 32" '
        f'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">{body}</svg>'
    )


def _mock_weather(city: str, now: datetime) -> dict[str, Any]:
    base_hour = now.hour
    hourly = []
    sample_temps = [14, 15, 17, 18, 18, 17, 15, 13]
    sample_rain = [10, 10, 15, 20, 35, 25, 15, 10]
    sample_icons = ["partly", "sun", "sun", "partly", "rain", "partly", "cloud", "cloud"]
    for i in range(8):
        h = (base_hour + i) % 24
        hourly.append(
            {
                "hour_label": f"{h:02d}:00",
                "temp": sample_temps[i],
                "rain_prob": sample_rain[i],
                "icon": sample_icons[i],
                "svg": weather_svg(sample_icons[i], 30),
            }
        )

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    start_wd = now.weekday()
    daily_specs = [
        ("Partly Cloudy", "partly", 19, 11, 20),
        ("Light Rain", "rain", 16, 10, 65),
        ("Clear Sky", "sun", 21, 12, 5),
        ("Mainly Clear", "sun", 22, 13, 10),
        ("Overcast", "cloud", 17, 9, 30),
    ]
    daily = []
    for idx, (cond, icon, hi, lo, rain) in enumerate(daily_specs):
        label = "Today" if idx == 0 else days[(start_wd + idx) % 7]
        daily.append(
            {
                "day_label": label,
                "condition": cond,
                "icon": icon,
                "svg": weather_svg(icon, 32),
                "high": hi,
                "low": lo,
                "rain_prob": rain,
            }
        )

    return {
        "source": "mock",
        "city": city,
        "temp": 18,
        "feels_like": 17,
        "condition": "Partly Cloudy",
        "icon": "partly",
        "svg_sm": weather_svg("partly", 28),
        "svg_lg": weather_svg("partly", 72),
        "rain_prob_6h": 20,
        "wind_kmh": 14,
        "humidity": 58,
        "high": 19,
        "low": 11,
        "hourly": hourly,
        "daily": daily,
    }


def clear_weather_cache() -> None:
    _WEATHER_CACHE.clear()


async def reverse_geocode_city(lat: float, lon: float) -> str:
    """Resolve coordinates from phone/MacBook GPS into a clean human-readable city name."""
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                "https://api.bigdatacloud.net/data/reverse-geocode-client",
                params={"latitude": lat, "longitude": lon, "localityLanguage": "en"},
            )
            resp.raise_for_status()
            data = resp.json()
            city = (
                data.get("city")
                or data.get("locality")
                or data.get("principalSubdivision")
                or ""
            ).strip()
            if city:
                return city
    except Exception as exc:
        logger.debug("BigDataCloud reverse geocode failed: %s", exc)

    try:
        async with httpx.AsyncClient(
            timeout=6.0, headers={"User-Agent": "KindleHermes/1.0"}
        ) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 10},
            )
            resp.raise_for_status()
            addr = resp.json().get("address", {})
            city = (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("municipality")
                or addr.get("state")
                or ""
            ).strip()
            if city:
                return city
    except Exception as exc:
        logger.debug("Nominatim reverse geocode failed: %s", exc)

    return f"{lat:.2f}, {lon:.2f}"


async def detect_ip_location() -> dict[str, Any] | None:
    """Fallback location & timezone lookup via IP (used on HTTP LAN or MacBook startup)."""
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get("https://ipwho.is/")
            resp.raise_for_status()
            data = resp.json()
            if data.get("success") and data.get("latitude") is not None:
                tz_obj = data.get("timezone") or {}
                return {
                    "city": (data.get("city") or data.get("region") or "Local").strip(),
                    "lat": round(float(data["latitude"]), 4),
                    "lon": round(float(data["longitude"]), 4),
                    "timezone": (tz_obj.get("id") or "").strip(),
                    "source": "ip",
                }
    except Exception as exc:
        logger.debug("ipwho.is lookup failed: %s", exc)

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get("http://ip-api.com/json/")
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success" and data.get("lat") is not None:
                return {
                    "city": (data.get("city") or data.get("regionName") or "Local").strip(),
                    "lat": round(float(data["lat"]), 4),
                    "lon": round(float(data["lon"]), 4),
                    "timezone": (data.get("timezone") or "").strip(),
                    "source": "ip",
                }
    except Exception as exc:
        logger.debug("ip-api.com lookup failed: %s", exc)

    return None


async def search_city_geocoding(query: str) -> list[dict[str, Any]]:
    clean = query.strip()
    if len(clean) < 2:
        return []
    url = "https://geocoding-api.open-meteo.com/v1/search"
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(url, params={"name": clean, "count": 5, "language": "en", "format": "json"})
        resp.raise_for_status()
        data = resp.json()
    results = []
    for item in data.get("results") or []:
        parts = [item.get("name", "")]
        if item.get("admin1"):
            parts.append(item["admin1"])
        if item.get("country_code"):
            parts.append(item["country_code"])
        results.append(
            {
                "label": ", ".join(p for p in parts if p),
                "city": item.get("name", clean),
                "lat": round(float(item["latitude"]), 4),
                "lon": round(float(item["longitude"]), 4),
                "timezone": item.get("timezone") or "UTC",
            }
        )
    return results


async def get_weather_context(
    settings: dict[str, Any],
    now: datetime,
    force_refresh: bool = False,
) -> dict[str, Any]:
    city = settings.get("weather_city", "Berlin")
    lat = float(settings.get("weather_lat", 52.52))
    lon = float(settings.get("weather_lon", 13.405))
    use_mock = settings.get("use_mock_data", True)

    if use_mock:
        return _mock_weather(city, now)

    cache_key = f"{lat:.3f},{lon:.3f}"
    now_ts = time.time()
    cached = _WEATHER_CACHE.get(cache_key)
    if not force_refresh and cached and (now_ts - cached[0]) < _WEATHER_TTL_SECONDS:
        return cached[1]

    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": settings.get("timezone", "auto"),
            "forecast_days": 5,
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        cur = data.get("current", {})
        cur_code = int(cur.get("weather_code", 2))
        cur_cond, cur_icon = WMO_CODES.get(cur_code, ("Cloudy", "cloud"))

        hourly_data = data.get("hourly", {})
        h_times = hourly_data.get("time", [])
        h_temps = hourly_data.get("temperature_2m", [])
        h_probs = hourly_data.get("precipitation_probability", [])
        h_codes = hourly_data.get("weather_code", [])

        cur_hour_str = now.strftime("%Y-%m-%dT%H:00")
        start_idx = 0
        for idx, t in enumerate(h_times):
            if t >= cur_hour_str:
                start_idx = idx
                break

        hourly = []
        rain_next_6 = []
        for idx in range(start_idx, min(start_idx + 8, len(h_times))):
            code = int(h_codes[idx]) if idx < len(h_codes) else 2
            _, icon = WMO_CODES.get(code, ("Cloudy", "cloud"))
            prob = int(h_probs[idx] or 0) if idx < len(h_probs) else 0
            if len(rain_next_6) < 6:
                rain_next_6.append(prob)
            hourly.append(
                {
                    "hour_label": h_times[idx][11:16],
                    "temp": round(float(h_temps[idx])),
                    "rain_prob": prob,
                    "icon": icon,
                    "svg": weather_svg(icon, 30),
                }
            )

        daily_data = data.get("daily", {})
        d_times = daily_data.get("time", [])
        d_codes = daily_data.get("weather_code", [])
        d_max = daily_data.get("temperature_2m_max", [])
        d_min = daily_data.get("temperature_2m_min", [])
        d_rain = daily_data.get("precipitation_probability_max", [])

        daily = []
        for idx in range(min(5, len(d_times))):
            dt = datetime.strptime(d_times[idx], "%Y-%m-%d")
            code = int(d_codes[idx]) if idx < len(d_codes) else 2
            cond, icon = WMO_CODES.get(code, ("Cloudy", "cloud"))
            daily.append(
                {
                    "day_label": "Today" if idx == 0 else dt.strftime("%a"),
                    "condition": cond,
                    "icon": icon,
                    "svg": weather_svg(icon, 32),
                    "high": round(float(d_max[idx])),
                    "low": round(float(d_min[idx])),
                    "rain_prob": int(d_rain[idx] or 0),
                }
            )

        result = {
            "source": "open-meteo",
            "city": city,
            "temp": round(float(cur.get("temperature_2m", 18))),
            "feels_like": round(float(cur.get("apparent_temperature", 17))),
            "condition": cur_cond,
            "icon": cur_icon,
            "svg_sm": weather_svg(cur_icon, 28),
            "svg_lg": weather_svg(cur_icon, 72),
            "rain_prob_6h": max(rain_next_6) if rain_next_6 else 0,
            "wind_kmh": round(float(cur.get("wind_speed_10m", 10))),
            "humidity": int(cur.get("relative_humidity_2m", 50)),
            "high": daily[0]["high"] if daily else round(float(cur.get("temperature_2m", 18))),
            "low": daily[0]["low"] if daily else round(float(cur.get("temperature_2m", 12))),
            "hourly": hourly,
            "daily": daily,
        }
        _WEATHER_CACHE[cache_key] = (now_ts, result)
        return result
    except Exception as exc:
        logger.warning("Open-Meteo fetch failed (%s), using mock weather", exc)
        fallback = _mock_weather(city, now)
        fallback["source"] = "mock_fallback"
        return fallback

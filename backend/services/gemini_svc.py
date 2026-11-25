"""Gemini Daily Briefing service using google-genai + live RSS headlines + resilient model fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import httpx

from backend.config import config
from backend.database import db

logger = logging.getLogger(__name__)

RSS_FEEDS: list[tuple[str, str]] = [
    ("MARKETS", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("TECH", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("WORLD", "https://feeds.bbci.co.uk/news/world/rss.xml"),
]

FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]


def _mock_briefing(now: datetime) -> dict[str, Any]:
    return {
        "edition": now.strftime("%A, %B %d, %Y"),
        "headline": "Global Equities Steady as Bond Yields Ease Ahead of Macro Data",
        "market_pulse": [
            {
                "tag": "MARKETS",
                "title": "Large-cap tech and industrials lead mild morning gains",
                "detail": "Major US and European equity indices opened slightly higher as sovereign yields cooled from recent highs, giving growth stocks breathing room.",
            },
            {
                "tag": "MACRO",
                "title": "Central banks signal patience on next rate steps",
                "detail": "Recent inflation prints continue trending gradually toward target, keeping policymakers focused on labor-market resilience rather than sudden cuts.",
            },
            {
                "tag": "TECH & AI",
                "title": "Edge hardware & lightweight models gain developer momentum",
                "detail": "Teams are increasingly pairing fast cloud inference with low-power dedicated devices for calm, single-purpose daily workflows.",
            },
        ],
        "concept_of_day": {
            "term": "Duration Risk",
            "definition": "Sensitivity of a bond's price to interest-rate changes; longer-maturity bonds swing more sharply when yields move by 1%.",
        },
        "reading_note": "Keep long-term allocations boring and automatic; use morning news as context, not a trigger for impulse trades.",
        "source": "mock",
        "updated_at": now.strftime("%H:%M"),
    }


def _extract_json_block(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def _fetch_live_headlines_sync() -> list[str]:
    """Fetch crisp live business/tech/world headlines from public RSS feeds (no API key required)."""
    headlines: list[str] = []
    with httpx.Client(timeout=6.0, follow_redirects=True, headers={"User-Agent": "KindleHermes/1.0"}) as client:
        for tag, url in RSS_FEEDS:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
                count = 0
                for item in root.findall(".//item"):
                    title = (item.findtext("title") or "").strip()
                    desc = (item.findtext("description") or "").strip()
                    if title:
                        snippet = f"[{tag}] {title}"
                        if desc:
                            snippet += f" — {desc[:140]}"
                        headlines.append(snippet)
                        count += 1
                    if count >= 4:
                        break
            except Exception as exc:
                logger.debug("RSS feed %s unavailable: %s", url, exc)
    return headlines


def _call_gemini_sync(now: datetime) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.gemini_api_key)
    date_str = now.strftime("%A, %B %d, %Y")
    live_headlines = _fetch_live_headlines_sync()

    news_context = ""
    if live_headlines:
        joined = "\n".join(f"- {h}" for h in live_headlines[:12])
        news_context = f"\nUse ONLY these live headlines fetched today as factual source material:\n{joined}\n"

    prompt = f"""
You are the editor of a calm, crisp, high-signal morning e-ink newspaper briefing for {date_str}.
{news_context}
Return ONLY valid JSON matching this exact schema (no markdown commentary outside JSON):
{{
  "edition": "{date_str}",
  "headline": "One punchy 8-12 word newspaper headline summarizing today's top market/business tone",
  "market_pulse": [
    {{"tag": "MARKETS", "title": "Short 6-10 word headline", "detail": "2 crisp, beginner-friendly sentences covering key market/business news."}},
    {{"tag": "MACRO", "title": "Short 6-10 word headline", "detail": "2 crisp sentences on macro economy, policy, or global business news."}},
    {{"tag": "TECH", "title": "Short 6-10 word headline", "detail": "2 crisp sentences on notable technology or industry news."}}
  ],
  "concept_of_day": {{
    "term": "One useful financial, economic, or mental-model concept",
    "definition": "Clear 1-sentence explanation under 25 words."
  }},
  "reading_note": "One calm 1-sentence takeaway to start a focused day."
}}
Keep language crisp, objective, factual, and easy to read on an e-ink screen.
""".strip()

    model_candidates: list[str] = []
    for m in [config.gemini_model] + FALLBACK_MODELS:
        if m and m not in model_candidates:
            model_candidates.append(m)

    last_err: Exception | None = None
    raw_text = ""
    used_model = config.gemini_model

    for model_name in model_candidates:
        try:
            tools = (
                [types.Tool(google_search=types.GoogleSearch())]
                if config.gemini_use_search_grounding
                else None
            )
            gen_config = types.GenerateContentConfig(
                temperature=0.4,
                tools=tools,
            )
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=gen_config,
            )
            raw_text = resp.text or ""
            if raw_text.strip():
                used_model = model_name
                break
        except Exception as exc:
            last_err = exc
            logger.warning("Gemini model %s failed (%s); trying next candidate", model_name, exc)
            continue

    if not raw_text.strip():
        raise last_err or RuntimeError("All Gemini model candidates returned empty responses")

    parsed = _extract_json_block(raw_text)
    if not isinstance(parsed.get("market_pulse"), list) or len(parsed["market_pulse"]) == 0:
        raise ValueError("Gemini response missing market_pulse items")

    parsed.setdefault("edition", date_str)
    parsed.setdefault("headline", "Morning Market & Macro Briefing")
    parsed.setdefault(
        "concept_of_day",
        {"term": "Compounding", "definition": "Earning returns on both your original principal and previously accumulated gains."},
    )
    parsed.setdefault("reading_note", "Focus on what you control today.")
    parsed["model"] = used_model
    return parsed


async def get_daily_briefing(
    settings: dict[str, Any],
    now: datetime,
    force_refresh: bool = False,
) -> dict[str, Any]:
    use_mock = settings.get("use_mock_data", True)
    date_key = now.strftime("%Y-%m-%d")

    if use_mock or not config.gemini_api_key:
        data = _mock_briefing(now)
        if not use_mock and not config.gemini_api_key:
            data["source"] = "mock_no_key"
        return data

    if not force_refresh:
        cached = db.get_briefing_cache(date_key)
        if cached and cached.get("source") == "gemini":
            return cached

    try:
        payload = await asyncio.to_thread(_call_gemini_sync, now)
        payload["updated_at"] = now.strftime("%H:%M")
        db.save_briefing_cache(date_key, payload, source="gemini")
        payload["source"] = "gemini"
        return payload
    except Exception as exc:
        logger.warning("Gemini briefing failed (%s), falling back to mock", exc)
        cached = db.get_briefing_cache(date_key)
        if cached:
            return cached
        fallback = _mock_briefing(now)
        fallback["source"] = "mock_fallback"
        return fallback

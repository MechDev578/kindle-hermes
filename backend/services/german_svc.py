"""Hermes Deutsch service — FSRS scheduler, A2->C2 Sprachindex, Famous Texts, Daily i+1 Reader & Gemini Recasts."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import httpx

from backend.config import config
from backend.database import db
from backend.services.german_data import FALLBACK_READERS, ROOT_PACKS, SEED_CARDS, SEED_CLASSICS

logger = logging.getLogger(__name__)

DE_RSS_FEEDS: list[tuple[str, str]] = [
    ("TAGESSCHAU", "https://www.tagesschau.de/infoservices/alle-meldungen-100~rss2.xml"),
    ("DW", "https://rss.dw.com/xml/rss-de-all"),
]

FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

CEFR_ORDER = ["A2", "B1", "B2", "C1", "C2"]
CEFR_WEIGHTS = {"A2": 1.0, "B1": 1.4, "B2": 1.9, "C1": 2.4, "C2": 3.0}

# Sprachindex band boundaries (0..1000)
BANDS = [
    ("A2", 0, 300, "B1", "Master Satzklammer, der/die/das suffix rules & 35 core chunks"),
    ("B1", 300, 500, "B2", "Master Modalpartikeln, Verb+Präp & 15,000+ words read"),
    ("B2", 500, 700, "C1", "Master Nomen-Verb-Verbindungen, Konjunktiv II & unadapted texts"),
    ("C1", 700, 875, "C2", "Master Nominalstil, rhetoric & daily C1/C2 Schreib-Sprints"),
    ("C2", 875, 1001, "C2+", "Near-native fluency — keep reading speeches, essays & literature!"),
]


def ensure_seeded() -> None:
    db.seed_german_cards(SEED_CARDS)
    db.seed_german_classics(SEED_CLASSICS)


# ==============================================================================
# FSRS-4.5 INSPIRED SPACED REPETITION SCHEDULER (4 RATINGS: 1=Again..4=Easy)
# ==============================================================================


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _format_interval(days: float) -> str:
    if days < (1.0 / 24.0):
        mins = max(1, int(round(days * 24 * 60)))
        return f"{mins}m"
    if days < 1.0:
        hrs = max(1, int(round(days * 24)))
        return f"{hrs}h"
    if days < 30.0:
        return f"{int(round(days))}d"
    months = days / 30.0
    if months < 12.0:
        return f"{months:.1f}mo".replace(".0mo", "mo")
    return f"{days / 365.0:.1f}y"


def preview_fsrs_next(card: dict[str, Any], rating: int, now_ts: float | None = None) -> dict[str, Any]:
    """Compute next FSRS stability (days), difficulty (1..10), state & due timestamp for rating 1..4."""
    now = now_ts if now_ts is not None else time.time()
    rating = max(1, min(4, int(rating)))
    state = card.get("state", "new")
    s = float(card.get("stability") or 0.0)
    d = float(card.get("difficulty") or 5.0)
    reps = int(card.get("reps") or 0)
    lapses = int(card.get("lapses") or 0)
    last_ts = float(card.get("last_review_at") or 0.0)

    if state == "new" or s <= 0.0:
        # Initial stability (days) & difficulty per FSRS-4.5 defaults
        init_s = {1: 0.4, 2: 1.2, 3: 3.2, 4: 7.5}[rating]
        init_d = _clamp(5.2 - 0.85 * (rating - 3), 1.0, 10.0)
        new_s = init_s
        new_d = init_d
        if rating == 1:
            interval_days = 10.0 / (24.0 * 60.0)  # 10 min
            new_state = "learning"
            new_lapses = lapses + 1
        elif rating == 2:
            interval_days = 0.5  # 12 hours
            new_state = "learning"
            new_lapses = lapses
        else:
            interval_days = init_s
            new_state = "review"
            new_lapses = lapses
        return {
            "stability": round(new_s, 3),
            "difficulty": round(new_d, 3),
            "reps": reps + 1,
            "lapses": new_lapses,
            "state": new_state,
            "interval_days": interval_days,
            "interval_label": _format_interval(interval_days),
            "due_at": now + interval_days * 86400.0,
            "last_review_at": now,
        }

    # Elapsed days and current retrievability R(t, S) = (1 + t / (9 * S))^-1
    elapsed_days = max(0.01, (now - last_ts) / 86400.0) if last_ts > 0 else s
    r = (1.0 + elapsed_days / (9.0 * max(0.1, s))) ** -1

    # Difficulty mean reversion toward 5.0
    delta_d = -0.65 * (rating - 3)
    new_d = _clamp(0.85 * (d + delta_d) + 0.15 * 5.0, 1.0, 10.0)

    if rating == 1:
        # Lapse: drop stability gracefully instead of resetting to 0
        new_s = max(0.4, 0.35 * (s ** 0.45) * (11.0 - new_d) * 0.25)
        interval_days = 10.0 / (24.0 * 60.0)
        new_state = "learning"
        new_lapses = lapses + 1
    else:
        hard_penalty = 0.72 if rating == 2 else 1.0
        easy_bonus = 1.38 if rating == 4 else 1.0
        gain = (
            math.exp(1.1)
            * (11.0 - new_d)
            * (max(0.1, s) ** -0.18)
            * (math.exp(0.95 * (1.0 - r)) - 1.0 + 0.25)
            * hard_penalty
            * easy_bonus
        )
        new_s = min(365.0, max(s + 0.5, s * (1.0 + gain)))
        interval_days = max(1.0, new_s) if rating >= 3 else max(0.5, new_s * 0.65)
        new_state = "review"
        new_lapses = lapses

    return {
        "stability": round(new_s, 3),
        "difficulty": round(new_d, 3),
        "reps": reps + 1,
        "lapses": new_lapses,
        "state": new_state,
        "interval_days": interval_days,
        "interval_label": _format_interval(interval_days),
        "due_at": now + interval_days * 86400.0,
        "last_review_at": now,
    }


def review_card(card_id: int, rating: int, day_key: str) -> dict[str, Any] | None:
    ensure_seeded()
    card = db.get_german_card(card_id)
    if not card:
        return None
    nxt = preview_fsrs_next(card, rating)
    updated = db.update_german_card_srs(
        card_id,
        stability=nxt["stability"],
        difficulty=nxt["difficulty"],
        reps=nxt["reps"],
        lapses=nxt["lapses"],
        state=nxt["state"],
        due_at=nxt["due_at"],
        last_review_at=nxt["last_review_at"],
    )
    is_ok = rating >= 2
    pts = {1: 1, 2: 3, 3: 5, 4: 6}[max(1, min(4, int(rating)))]
    kind = "gender" if card.get("kind") == "gender" else "review"
    db.log_german_activity(
        day_key,
        kind,
        correct=is_ok,
        points=pts,
        detail=f"card:{card_id}:r{rating}:{card.get('cefr', 'A2')}",
    )
    # Reset online flip state for next card
    db.update_settings({"german_drill_flipped": False, "german_drill_choice": ""})
    return updated


def get_due_deck(settings: dict[str, Any], limit: int = 12, now_ts: float | None = None) -> list[dict[str, Any]]:
    """Return prioritized study queue: due learning/review cards first, then level-matched new cards."""
    ensure_seeded()
    now = now_ts if now_ts is not None else time.time()
    all_cards = db.list_german_cards()
    user_level = _resolve_target_cefr(settings)
    lvl_idx = CEFR_ORDER.index(user_level) if user_level in CEFR_ORDER else 0

    due_cards = [c for c in all_cards if c["state"] != "new" and float(c["due_at"]) <= now]
    due_cards.sort(key=lambda c: (0 if c["state"] == "learning" else 1, float(c["due_at"])))

    # Prioritize new cards at or just above user's level (i+1), mixing slang, gender & cloze
    new_cards = [c for c in all_cards if c["state"] == "new"]

    def _new_sort_key(c: dict[str, Any]) -> tuple[int, int, int]:
        c_idx = CEFR_ORDER.index(c["cefr"]) if c["cefr"] in CEFR_ORDER else 0
        # Prefer current level (0), then i+1 (1), then lower (2), then higher (3+)
        if c_idx == lvl_idx:
            tier_prio = 0
        elif c_idx == lvl_idx + 1:
            tier_prio = 1
        elif c_idx < lvl_idx:
            tier_prio = 2
        else:
            tier_prio = 3 + (c_idx - lvl_idx)
        # Mix custom/mined cards near the top!
        src_prio = 0 if c.get("source") in ("custom", "classic") else 1
        return (src_prio, tier_prio, int(c["id"]))

    new_cards.sort(key=_new_sort_key)

    # Interleave kinds nicely so drills feel crisp and varied (slang / gender / cloze / nvv)
    selected: list[dict[str, Any]] = list(due_cards[:limit])
    if len(selected) < limit:
        remaining = limit - len(selected)
        buckets: dict[str, list[dict[str, Any]]] = {}
        for c in new_cards:
            buckets.setdefault(c["kind"], []).append(c)
        kind_order = ["slang", "gender", "cloze", "nomen_verb"]
        while len(selected) < limit and any(buckets.values()):
            progressed = False
            for k in kind_order + [kk for kk in buckets if kk not in kind_order]:
                b = buckets.get(k)
                if b:
                    selected.append(b.pop(0))
                    progressed = True
                    if len(selected) >= limit:
                        break
            if not progressed:
                break
        _ = remaining

    # Attach live FSRS interval previews for the 4 rating buttons
    enriched: list[dict[str, Any]] = []
    for c in selected:
        item = dict(c)
        item["intervals"] = {
            "1": preview_fsrs_next(c, 1, now)["interval_label"],
            "2": preview_fsrs_next(c, 2, now)["interval_label"],
            "3": preview_fsrs_next(c, 3, now)["interval_label"],
            "4": preview_fsrs_next(c, 4, now)["interval_label"],
        }
        enriched.append(item)
    return enriched


# ==============================================================================
# GRADED CEFR MASTERY SCORE: SPRACHINDEX (0 -> 1000, A2.0 -> C2.0)
# ==============================================================================


def _compute_streak(activities: list[dict[str, Any]], today_str: str) -> int:
    days = sorted({a["day_key"] for a in activities if a.get("day_key")}, reverse=True)
    if not days:
        return 0
    try:
        today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
    except Exception:
        return len(days)
    streak = 0
    cursor = today_dt
    day_set = set(days)
    # Allow continuing yesterday's streak before today's first action
    if cursor.isoformat() not in day_set:
        cursor = cursor - timedelta(days=1)
    while cursor.isoformat() in day_set:
        streak += 1
        cursor = cursor - timedelta(days=1)
    return streak


def compute_mastery_score(settings: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Compute transparent 0..1000 Sprachindex across Nation's 4 Strands (250 pts each)."""
    ensure_seeded()
    cards = db.list_german_cards()
    classics = db.list_german_classics()
    activities = db.list_german_activity(limit=1000)
    writings = db.list_german_writing(limit=100)
    now_ts = time.time()
    today_key = now.strftime("%Y-%m-%d")

    # Baseline calibration so an A2 learner starts honestly at A2.0 (~180/1000) and climbs cleanly toward B1 (300) -> C2 (875+)
    base_level = (settings.get("german_level") or "A2").upper()
    base_floor = {"A2": 180, "B1": 310, "B2": 510, "C1": 705, "C2": 880}.get(base_level, 180)
    strand_floor = base_floor // 4

    # 1. LEXIKON (0..250): FSRS stability across CEFR-weighted chunks
    weighted_mastery = 0.0
    mastered_count = 0
    due_count = 0
    for c in cards:
        if c["state"] != "new" and float(c["due_at"]) <= now_ts:
            due_count += 1
        st = float(c.get("stability") or 0.0)
        if st > 0:
            w = CEFR_WEIGHTS.get(c.get("cefr", "A2"), 1.0)
            # Saturating credit per card as stability grows (1d -> ~0.35, 7d -> ~0.75, 30d -> 1.0)
            credit = min(1.0, math.log1p(st) / math.log1p(21.0))
            weighted_mastery += w * credit
            if st >= 3.0:
                mastered_count += 1
    lexikon = min(250, strand_floor + int(round(weighted_mastery * 3.8)))

    # 2. STRUKTUR (0..250): accuracy & volume on gender/cloze/grammar drills
    drill_acts = [a for a in activities if a["kind"] in ("review", "gender")]
    gender_acts = [a for a in activities if a["kind"] == "gender"]
    total_drills = len(drill_acts)
    correct_drills = sum(1 for a in drill_acts if a["correct"])
    accuracy_pct = int(round((correct_drills / total_drills) * 100)) if total_drills else 0
    gender_acc = (
        int(round((sum(1 for a in gender_acts if a["correct"]) / len(gender_acts)) * 100))
        if gender_acts
        else 0
    )
    struktur_gain = int(round(min(180.0, (correct_drills * 1.9) * (0.55 + 0.45 * (accuracy_pct / 100.0)))))
    struktur = min(250, strand_floor + struktur_gain)

    # 3. LESEVOLUMEN (0..250): German words read across Famous Texts + Daily Readers
    read_acts = [a for a in activities if a["kind"] in ("read", "classic")]
    words_read = sum(int(a.get("words_count") or 0) for a in read_acts)
    classics_read = sum(1 for cl in classics if int(cl.get("read_count") or 0) > 0)
    lesen_gain = int(round(min(205.0, (words_read / 22.0) + classics_read * 6.5)))
    lesen = min(250, strand_floor + lesen_gain)

    # 4. OUTPUT (0..250): Companion /app Schreib-Sprints + audio shadowing
    shadow_count = sum(1 for a in activities if a["kind"] == "shadow")
    writing_count = len(writings)
    output_gain = int(round(min(205.0, writing_count * 12.0 + shadow_count * 4.0)))
    output = min(250, strand_floor + output_gain)

    total = min(1000, lexikon + struktur + lesen + output)

    band = "A2"
    band_lo, band_hi = 0, 300
    next_band = "B1"
    milestone = BANDS[0][4]
    for b_name, lo, hi, nxt, desc in BANDS:
        if lo <= total < hi:
            band, band_lo, band_hi, next_band, milestone = b_name, lo, hi, nxt, desc
            break

    band_span = max(1, band_hi - band_lo)
    band_progress = (total - band_lo) / band_span
    sub_decimal = min(9, int(band_progress * 10))
    cefr_label = f"{band}.{sub_decimal}" if band != "C2" else "C2.0"

    strands = [
        {"id": "lexikon", "label": "LEXIKON", "score": lexikon, "max": 250, "pct": int(round(lexikon / 2.5))},
        {"id": "struktur", "label": "STRUKTUR", "score": struktur, "max": 250, "pct": int(round(struktur / 2.5))},
        {"id": "lesen", "label": "LESEN", "score": lesen, "max": 250, "pct": int(round(lesen / 2.5))},
        {"id": "output", "label": "OUTPUT", "score": output, "max": 250, "pct": int(round(output / 2.5))},
    ]
    weakest = min(strands, key=lambda x: x["score"])

    reviewed_today = sum(1 for a in drill_acts if a["day_key"] == today_key)
    daily_goal = max(5, int(settings.get("german_daily_goal") or 15))
    # Count due reviews + remaining daily new cards up to daily_goal
    new_remaining_today = max(0, daily_goal - reviewed_today)
    new_available = sum(1 for c in cards if c["state"] == "new")
    actionable_due = due_count + min(new_remaining_today, new_available)

    return {
        "total": total,
        "max_total": 1000,
        "band": band,
        "cefr_label": cefr_label,
        "next_band": next_band,
        "next_band_score": min(1000, band_hi),
        "band_pct": min(100, max(0, int(round(band_progress * 100)))),
        "total_pct": min(100, int(round(total / 10.0))),
        "milestone": milestone,
        "strands": strands,
        "weakest_strand": weakest["label"],
        "streak_days": _compute_streak(activities, today_key),
        "reviewed_today": reviewed_today,
        "daily_goal": daily_goal,
        "due_count": actionable_due,
        "strict_due_count": due_count,
        "total_cards": len(cards),
        "mastered_cards": mastered_count,
        "accuracy_pct": accuracy_pct,
        "gender_accuracy_pct": gender_acc,
        "words_read": words_read,
        "classics_read": classics_read,
        "classics_total": len(classics),
        "writing_sessions": writing_count,
    }


def _resolve_target_cefr(settings: dict[str, Any]) -> str:
    lvl = (settings.get("german_level") or "A2").upper()
    if lvl in CEFR_ORDER:
        return lvl
    return "A2"


# ==============================================================================
# DAILY GRADED READER (LIVE TAGESSCHAU / DW RSS + GEMINI i+1 ADAPTATION)
# ==============================================================================


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


def _fetch_german_rss_sync() -> list[str]:
    headlines: list[str] = []
    with httpx.Client(timeout=6.0, follow_redirects=True, headers={"User-Agent": "KindleHermes/2.1"}) as client:
        for tag, url in DE_RSS_FEEDS:
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
                            snippet += f" — {desc[:160]}"
                        headlines.append(snippet)
                        count += 1
                    if count >= 4:
                        break
            except Exception as exc:
                logger.debug("German RSS %s unavailable: %s", url, exc)
    return headlines


def _call_gemini_json_sync(prompt: str) -> tuple[dict[str, Any], str]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.gemini_api_key)
    candidates: list[str] = []
    for m in [config.gemini_model] + FALLBACK_MODELS:
        if m and m not in candidates:
            candidates.append(m)

    last_err: Exception | None = None
    for model_name in candidates:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.5),
            )
            raw = resp.text or ""
            if raw.strip():
                return _extract_json_block(raw), model_name
        except Exception as exc:
            last_err = exc
            logger.warning("Gemini Deutsch call failed on %s: %s", model_name, exc)
    raise last_err or RuntimeError("All Gemini models failed")


def _generate_daily_reader_sync(level: str, woven_terms: list[str], date_str: str) -> dict[str, Any]:
    headlines = _fetch_german_rss_sync()
    news_block = "\n".join(f"- {h}" for h in headlines[:6]) if headlines else "Contemporary German culture, tech, or Berlin daily life"
    woven_str = ", ".join(woven_terms[:4]) if woven_terms else "Feierabend, obwohl, auf dem Schlauch stehen"

    prompt = f"""
You are a master German SLA curriculum author creating a compelling 3-minute e-ink graded reader for {date_str}.
Learner level: {level} (write at {level}+ comprehensible input — 95% clear, 5% stretch).
Source material (pick 1 engaging topic from these live German headlines):
{news_block}

Naturally weave at least 2 of these target words/idioms into the German text: {woven_str}

Return ONLY valid JSON:
{{
  "title": "Punchy German headline (6-11 words)",
  "topic": "Short German category e.g. Gesellschaft · Kultur",
  "cefr": "{level}+",
  "paragraphs": [
    {{"de": "German paragraph 1 (~35-45 words) with superscript numbers ¹ ² for hard words", "en": "Natural English translation of paragraph 1"}},
    {{"de": "German paragraph 2 (~35-45 words) with superscript numbers ³ ⁴", "en": "Natural English translation of paragraph 2"}},
    {{"de": "German paragraph 3 (~30-40 words, crisp conclusion)", "en": "Natural English translation of paragraph 3"}}
  ],
  "glosses": [
    {{"num": 1, "term": "German word/chunk", "en": "concise English gloss + gender/grammar note"}},
    {{"num": 2, "term": "...", "en": "..."}},
    {{"num": 3, "term": "...", "en": "..."}},
    {{"num": 4, "term": "...", "en": "..."}}
  ],
  "woven_words": ["word1", "word2"],
  "quiz": {{
    "question": "1 crisp comprehension question in German about the text",
    "options": ["A) ...", "B) ...", "C) ..."],
    "answer_idx": 0,
    "explanation": "1 short German explanation starting with 'Genau!' or 'Richtig!'"
  }}
}}
""".strip()

    parsed, used_model = _call_gemini_json_sync(prompt)
    paras = parsed.get("paragraphs") or []
    if not paras:
        raise ValueError("Missing paragraphs in generated reader")
    wc = sum(len((p.get("de") or "").split()) for p in paras)
    parsed["word_count"] = max(40, wc)
    parsed["model"] = used_model
    return parsed


async def get_daily_reader(
    settings: dict[str, Any],
    now: datetime,
    due_deck: list[dict[str, Any]],
    force_refresh: bool = False,
) -> dict[str, Any]:
    level = _resolve_target_cefr(settings)
    date_key = f"{now.strftime('%Y-%m-%d')}|{level}"
    use_mock = settings.get("use_mock_data", True)

    cached = db.get_german_daily_cache(date_key)
    if cached and not force_refresh:
        if cached.get("source") == "gemini" or use_mock or not config.gemini_api_key:
            return cached

    if not use_mock and config.gemini_api_key:
        try:
            woven = [c["answer_de"] for c in due_deck[:5] if len(c["answer_de"]) > 3]
            payload = await asyncio.to_thread(
                _generate_daily_reader_sync, level, woven, now.strftime("%A, %d.%m.%Y")
            )
            db.save_german_daily_cache(date_key, payload, source="gemini")
            return db.get_german_daily_cache(date_key) or {**payload, "date_key": date_key, "source": "gemini", "read_done": False, "quiz_answered": -1}
        except Exception as exc:
            logger.warning("Gemini daily German reader failed (%s); using curated fallback", exc)

    fallback_key = level if level in FALLBACK_READERS else ("C1" if level == "C2" else "A2")
    base = dict(FALLBACK_READERS[fallback_key])
    db.save_german_daily_cache(date_key, base, source="curated")
    return db.get_german_daily_cache(date_key) or {**base, "date_key": date_key, "source": "curated", "read_done": False, "quiz_answered": -1}


# ==============================================================================
# COMPANION /APP FEATURES: SCHREIB-SPRINT (RECAST) & CUSTOM TEXT IMPORTER
# ==============================================================================


def _evaluate_writing_sync(user_text: str, target_chunks: list[str], level: str) -> dict[str, Any]:
    prompt = f"""
You are a warm, razor-sharp German C2 coach using SLA 'Recast' feedback for a {level} learner aiming for C2.
Target chunks/words the learner was prompted to use: {', '.join(target_chunks)}
Learner's German writing:
\"\"\"{user_text}\"\"\"

Return ONLY valid JSON:
{{
  "score": 82,
  "praise": "1 crisp encouraging sentence noting what already sounded natural.",
  "recast_de": "The complete natural native-German version of what the learner meant to say (clean B2/C1 spoken or written German).",
  "fixes": [
    {{"original": "exact snippet with error or awkwardness", "better": "natural fix", "why": "short high-ROI grammar/case/word-order rule"}}
  ],
  "c2_upgrade": "Optional 1-sentence C2/idiomatic elevation of their idea so they see the summit!"
}}
Keep fixes to at most 3 high-impact points (don't nitpick tiny typos).
""".strip()
    parsed, _ = _call_gemini_json_sync(prompt)
    return parsed


def _fallback_writing_feedback(user_text: str, target_chunks: list[str]) -> dict[str, Any]:
    used = [c for c in target_chunks if c.lower() in user_text.lower()]
    score = min(92, 68 + len(used) * 10 + (6 if len(user_text.split()) >= 8 else 0))
    return {
        "score": score,
        "praise": (
            f"Stark! You actively produced {len(user_text.split())} words in German"
            + (f" and wove in: {', '.join(used)}." if used else ".")
        ),
        "recast_de": user_text.strip(),
        "fixes": [
            {
                "original": "Offline / No Gemini Key",
                "better": "Add GEMINI_API_KEY in .env for live native C2 recasts",
                "why": "Check your Verb-in-Position-2 (main clause) vs. Verb-at-end (weil/dass/obwohl) and noun capitalization!",
            }
        ],
        "c2_upgrade": "Tip: Try rewriting one sentence using a Modalpartikel (doch/mal/halt) or a Nomen-Verb-Verbindung!",
    }


async def evaluate_writing_sprint(
    user_text: str, target_chunks: list[str], settings: dict[str, Any], now: datetime
) -> dict[str, Any]:
    clean = user_text.strip()
    if len(clean) < 3:
        raise ValueError("Please write at least one short German sentence.")
    level = _resolve_target_cefr(settings)
    use_mock = settings.get("use_mock_data", True)

    if not use_mock and config.gemini_api_key:
        try:
            fb = await asyncio.to_thread(_evaluate_writing_sync, clean, target_chunks, level)
        except Exception as exc:
            logger.warning("Gemini writing recast failed (%s); using offline coach", exc)
            fb = _fallback_writing_feedback(clean, target_chunks)
    else:
        fb = _fallback_writing_feedback(clean, target_chunks)

    saved = db.save_german_writing(
        prompt_chunks=", ".join(target_chunks),
        user_text=clean,
        recast_de=fb.get("recast_de", clean),
        feedback=fb,
    )
    db.log_german_activity(
        now.strftime("%Y-%m-%d"),
        "output",
        correct=True,
        words_count=len(clean.split()),
        points=18,
        detail=f"writing:{saved['id']}",
    )
    return saved


def _build_custom_classic_sync(title: str, author: str, raw_german: str, level: str) -> dict[str, Any]:
    prompt = f"""
Turn this authentic German passage into a KindleHermes study packet for a {level} learner aiming for C2.
Title: {title}
Author: {author}
German text:
\"\"\"{raw_german}\"\"\"

Return ONLY valid JSON:
{{
  "cefr": "estimated CEFR of original e.g. B2 or C1",
  "famous_line": "1 iconic quote from the text",
  "conversation_tip": "How & when to reference or quote this naturally in German conversation",
  "paragraphs": [
    {{"de": "German paragraph with superscript numbers ¹ ² for hard terms", "en": "Natural English translation", "literal": "1 subtle nuance note"}}
  ],
  "glosses": [
    {{"num": 1, "term": "German term", "article_info": "gender/verb type", "en": "concise English gloss"}}
  ],
  "xray": [
    {{"tag": "GRAMMAR TAG", "snippet": "short quote", "note": "concise noticing explanation"}}
  ],
  "mine_cards": [
    {{
      "kind": "cloze",
      "cefr": "{level}",
      "category": "Eigener Text",
      "prompt_de": "Sentence with ________ blank",
      "answer_de": "missing word",
      "full_de": "Full German sentence",
      "hint_en": "English clue",
      "translation_en": "Full English translation",
      "rule_note": "Why this chunk is useful"
    }}
  ]
}}
""".strip()
    parsed, _ = _call_gemini_json_sync(prompt)
    return parsed


async def import_custom_classic(
    title: str, author: str, german_text: str, english_translation: str, settings: dict[str, Any]
) -> dict[str, Any]:
    clean_de = german_text.strip()
    if len(clean_de) < 10:
        raise ValueError("Please paste at least one German sentence.")
    clean_title = title.strip() or "Eigener Text"
    clean_author = author.strip() or "Eigene Sammlung"
    slug = f"custom_{int(time.time())}"
    level = _resolve_target_cefr(settings)
    use_mock = settings.get("use_mock_data", True)

    if not use_mock and config.gemini_api_key:
        try:
            built = await asyncio.to_thread(_build_custom_classic_sync, clean_title, clean_author, clean_de, level)
            for idx, mc in enumerate(built.get("mine_cards") or []):
                mc.setdefault("slug", f"{slug}_card_{idx}")
                mc.setdefault("source", "custom")
            item = {
                "slug": slug,
                "author": clean_author,
                "title": clean_title,
                "year": str(datetime.now().year),
                "category": "Eigener Text",
                "cefr": built.get("cefr", level),
                "famous_line": built.get("famous_line", clean_de[:90]),
                "conversation_tip": built.get("conversation_tip", "Imported into your personal KindleHermes library."),
                "paragraphs": built.get("paragraphs") or [{"de": clean_de, "en": english_translation or "(Add Gemini key for auto-translation)"}],
                "glosses": built.get("glosses") or [],
                "xray": built.get("xray") or [],
                "mine_cards": built.get("mine_cards") or [],
                "word_count": len(clean_de.split()),
            }
            return db.insert_custom_german_classic(item)
        except Exception as exc:
            logger.warning("Gemini custom classic analysis failed (%s); storing directly", exc)

    paras_de = [p.strip() for p in clean_de.splitlines() if p.strip()]
    paras_en = [p.strip() for p in english_translation.strip().splitlines() if p.strip()] if english_translation else []
    paragraphs = [
        {
            "de": p,
            "en": paras_en[i] if i < len(paras_en) else "(Enable live Gemini or add English lines for parallel view)",
            "literal": "",
        }
        for i, p in enumerate(paras_de[:4])
    ]
    item = {
        "slug": slug,
        "author": clean_author,
        "title": clean_title,
        "year": str(datetime.now().year),
        "category": "Eigener Text",
        "cefr": level,
        "famous_line": paras_de[0][:100] if paras_de else clean_title,
        "conversation_tip": "Personal excerpt added via Companion (/app).",
        "paragraphs": paragraphs,
        "glosses": [],
        "xray": [{"tag": "NOTICING", "snippet": paras_de[0][:48], "note": "Spot the Position-2 conjugated verb and any Nebensatz verb-at-end brackets!"}],
        "mine_cards": [],
        "word_count": len(clean_de.split()),
    }
    return db.insert_custom_german_classic(item)


# ==============================================================================
# UNIFIED CONTEXT BUILDER FOR KINDLE SCREENS, LOCK SCREEN & COMPANION /APP
# ==============================================================================


def mine_classic_chunks(classic_id: int) -> int:
    ensure_seeded()
    cl = db.get_german_classic(classic_id)
    if not cl:
        return 0
    added = 0
    for idx, mc in enumerate(cl.get("mine_cards") or []):
        card = dict(mc)
        card.setdefault("slug", f"mine_{cl['slug']}_{idx}")
        card["source"] = "classic"
        db.upsert_german_card(card)
        added += 1
    db.mark_german_classic_mined(classic_id)
    return added


def mark_classic_read(classic_id: int, day_key: str) -> dict[str, Any] | None:
    ensure_seeded()
    cl = db.mark_german_classic_read(classic_id)
    if not cl:
        return None
    db.log_german_activity(
        day_key,
        "classic",
        correct=True,
        words_count=int(cl.get("word_count") or 55),
        points=15,
        detail=f"classic:{classic_id}:{cl['slug']}",
    )
    return cl


async def get_german_context(
    settings: dict[str, Any],
    now: datetime,
    force_refresh: bool = False,
) -> dict[str, Any]:
    ensure_seeded()
    mastery = compute_mastery_score(settings, now)
    deck = get_due_deck(settings, limit=12)
    classics = db.list_german_classics()
    classic_idx = int(settings.get("german_active_classic") or 0) % max(1, len(classics))
    active_classic = classics[classic_idx] if classics else None

    day_ordinal = now.date().toordinal()
    root_idx_setting = int(settings.get("german_active_root") or 0)
    active_root = ROOT_PACKS[(root_idx_setting) % len(ROOT_PACKS)]

    reader = await get_daily_reader(settings, now, deck, force_refresh=force_refresh)

    # Pick today's Word/Chunk of the Day (rotating cleanly across slang + Nomen-Verb + culture cards)
    all_cards = db.list_german_cards()
    spotlight_pool = [c for c in all_cards if c["kind"] in ("slang", "nomen_verb", "cloze")] or all_cards
    word_of_day = spotlight_pool[day_ordinal % len(spotlight_pool)] if spotlight_pool else None

    # Pick a daily quote from classics for the Deutsch lock screen
    daily_quote_classic = classics[day_ordinal % len(classics)] if classics else None

    # Pick a lock-screen cloze teaser card
    cloze_pool = [c for c in all_cards if c["kind"] in ("cloze", "nomen_verb", "slang")]
    teaser_card = cloze_pool[(day_ordinal + 3) % len(cloze_pool)] if cloze_pool else word_of_day

    # Active drill card for online /kindle & /preview views
    active_card = deck[0] if deck else (all_cards[0] if all_cards else None)

    # Prompt chunks for Companion Schreib-Sprint
    sprint_chunks = [c["answer_de"] for c in deck[:3]]
    if active_classic and active_classic.get("famous_line"):
        sprint_chunks.append(active_classic["famous_line"].strip("„“"))

    return {
        "mastery": mastery,
        "deck": deck,
        "active_card": active_card,
        "drill_flipped": bool(settings.get("german_drill_flipped", False)),
        "drill_choice": settings.get("german_drill_choice", ""),
        "classics": classics,
        "classic_idx": classic_idx,
        "classic_total": len(classics),
        "active_classic": active_classic,
        "reader": reader,
        "root_pack": active_root,
        "root_idx": root_idx_setting % len(ROOT_PACKS),
        "root_total": len(ROOT_PACKS),
        "word_of_day": word_of_day,
        "daily_quote": daily_quote_classic,
        "teaser_card": teaser_card,
        "sprint_chunks": sprint_chunks[:4],
        "recent_writings": db.list_german_writing(limit=5),
    }

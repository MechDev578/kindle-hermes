"""Playwright + Pillow 1072x1448 8-bit grayscale PNG renderer & automatic hitbox extractor."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from backend.config import KINDLE_HEIGHT, KINDLE_WIDTH, config

logger = logging.getLogger(__name__)


@dataclass
class RenderedScreen:
    png_bytes: bytes
    hitboxes: list[dict[str, Any]]
    etag: str
    width: int = KINDLE_WIDTH
    height: int = KINDLE_HEIGHT


def _find_chromium_executable() -> str | None:
    if config.chromium_executable_path and Path(config.chromium_executable_path).exists():
        return config.chromium_executable_path

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _quantize_eink_grayscale(raw_png: bytes) -> bytes:
    """Convert RGB screenshot into crisp 8-bit 16-level grayscale PNG optimized for E Ink Carta HD."""
    with Image.open(io.BytesIO(raw_png)) as img:
        gray = img.convert("L")
        if gray.size != (KINDLE_WIDTH, KINDLE_HEIGHT):
            gray = gray.resize((KINDLE_WIDTH, KINDLE_HEIGHT), Image.Resampling.LANCZOS)
        # Snap to 16 crisp e-ink grayscale levels without dithering text
        quantized = gray.point(lambda p: (p // 17) * 17)
        out = io.BytesIO()
        quantized.save(out, format="PNG", optimize=True)
        return out.getvalue()


def _fallback_pillow_png(title: str, subtitle: str) -> bytes:
    img = Image.new("L", (KINDLE_WIDTH, KINDLE_HEIGHT), color=255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([32, 32, KINDLE_WIDTH - 32, KINDLE_HEIGHT - 32], outline=0, width=4)
    draw.text((72, 96), "KINDLEHERMES (FALLBACK RENDERER)", fill=0)
    draw.text((72, 160), title, fill=0)
    draw.text((72, 220), subtitle, fill=85)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


class EinkRenderer:
    """Keeps a single headless Chromium browser warm for fast 1072x1448 e-ink rendering."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._png_cache: dict[str, bytes] = {}

    def get_cached_png(self, etag: str) -> bytes | None:
        return self._png_cache.get(etag)

    def _store_cached_png(self, etag: str, png_bytes: bytes) -> None:
        self._png_cache[etag] = png_bytes
        if len(self._png_cache) > 24:
            oldest = next(iter(self._png_cache))
            self._png_cache.pop(oldest, None)

    async def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--font-render-hinting=none",
            ],
        }
        try:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        except Exception:
            exe = _find_chromium_executable()
            if not exe:
                raise
            logger.info("Using system Chromium executable at %s", exe)
            launch_kwargs["executable_path"] = exe
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        return self._browser

    async def render_html(self, html: str, base_url: str = "http://127.0.0.1:8000") -> RenderedScreen:
        """Render complete HTML at exact 1072x1448 and extract all [data-action] hitboxes."""
        async with self._lock:
            try:
                browser = await self._ensure_browser()
                context = await browser.new_context(
                    viewport={"width": KINDLE_WIDTH, "height": KINDLE_HEIGHT},
                    device_scale_factor=1,
                )
                page = await context.new_page()
                try:
                    # Inline static CSS relative to base_url or file content already inlined
                    await page.set_content(html, wait_until="load")
                    hitboxes = await page.evaluate(
                        """() => {
                            const nodes = document.querySelectorAll('[data-action]');
                            const boxes = [];
                            for (const el of nodes) {
                                const rect = el.getBoundingClientRect();
                                if (rect.width < 1 || rect.height < 1) continue;
                                boxes.push({
                                    action: el.getAttribute('data-action'),
                                    label: el.getAttribute('data-label') || (el.innerText || '').trim().slice(0, 48),
                                    x: Math.max(0, Math.round(rect.left)),
                                    y: Math.max(0, Math.round(rect.top)),
                                    w: Math.round(rect.width),
                                    h: Math.round(rect.height),
                                });
                            }
                            return boxes;
                        }"""
                    )
                    raw_png = await page.screenshot(
                        type="png",
                        clip={"x": 0, "y": 0, "width": KINDLE_WIDTH, "height": KINDLE_HEIGHT},
                    )
                finally:
                    await context.close()

                png_bytes = _quantize_eink_grayscale(raw_png)
                etag = hashlib.sha256(png_bytes).hexdigest()[:16]
                self._store_cached_png(etag, png_bytes)
                return RenderedScreen(png_bytes=png_bytes, hitboxes=hitboxes, etag=etag)
            except Exception as exc:
                logger.exception("Playwright render failed: %s", exc)
                png_bytes = _fallback_pillow_png("Chromium renderer unavailable", str(exc)[:180])
                etag = hashlib.sha256(png_bytes).hexdigest()[:16]
                self._store_cached_png(etag, png_bytes)
                return RenderedScreen(png_bytes=png_bytes, hitboxes=[], etag=etag)

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None


renderer = EinkRenderer()

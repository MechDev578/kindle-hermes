# KindleHermes OS (v1)

> **Looking for the latest Offline-First version?** See the **[`v2` branch](../../tree/v2)** — read below for why we pivoted from v1 to v2.

A modular, interactive e-ink OS, context-aware lock-screen engine, and cross-device web companion built for the **Kindle Paperwhite 10th Gen (PW4 — `1072 × 1448` @ 300 PPI)** — while keeping your Kindle 100% normal for reading books.

---

## Why We Pivoted from v1 to v2 (Problems & Idea Change)

After running v1 on a real Paperwhite 10th Gen (`5.18.1.1.1` / SpiderCat), real-world daily use revealed three core friction points:

1. **Always-On Wi-Fi Dependency:** In v1, the Kindle native client acted as a pure online thin client — every screen switch, task tap, and Pomodoro tick required a live HTTP round-trip. Opening Hermes OS in Airplane Mode or without Wi-Fi failed immediately, and keeping Kindle Wi-Fi on all day drained battery unnecessarily.
2. **Tethered to a Running MacBook:** Hosting the FastAPI backend locally via `./run.sh` meant the Kindle and iPhone companion stopped syncing whenever the MacBook slept, closed its lid, or left the home network.
3. **Static Offline Lock Screen & No Offline Interactivity:** Locking the Kindle offline could only show a single stale lock-screen snapshot rather than transitioning through the day's meetings (`HAPPENING NOW` → `NEXT UP` → `TODAY COMPLETE`), and you couldn't check off tasks or run a Pomodoro timer while offline.

### The v2 Idea Change (`v2` branch)
Instead of requiring constant Wi-Fi, **`v2` flips the architecture to Offline-First (Wi-Fi only for updates & sync)**:
* **Single-Shot Offline Sync Bundle (`GET /api/offline-bundle`):** One tap on `SYNC` (or a silent check on open if Wi-Fi is already connected) downloads a compact ZIP containing all 8 rendered `1072 × 1448` screens, exact touch hitboxes, and **time-sliced Smart Lock Screens** covering today's meeting transitions into `/mnt/us/kindlehermes/cache/`.
* **100% Airplane-Mode Usability:** Instant launch from local cache (<200ms), ~50ms local screen navigation, local offline Pomodoro countdown with 1-minute e-ink updates, offline task checkbox toggling backed by a persistent idempotent sync queue (`outbox.json` → `POST /api/sync/batch`), and automatic clock-driven Smart Lock Screen switching offline.
* **Dual Sync (24/7 Cloud + MacBook + Zero-Wi-Fi USB):** Primary sync against a 24/7 cloud deployment (Railway HTTPS with embedded Mozilla root CAs in the ARMv7 binary), automatic failover to a local MacBook LAN server, plus a 1-command `usb_sync.sh` for syncing over USB with zero Kindle Wi-Fi at all.

---

## v1 Highlights
* **1-Tap Standalone Hermes OS Environment:** Launch via SpiderCat Scriptlet (or KUAL) into a fullscreen `1072 × 1448` app launcher (`Today`, `Calendar`, `Planner`, `Pomodoro`, `Weather`, `Briefing`) and tap `EXIT` anytime to return to your books.
* **Server-Driven Screen + Automatic Hitbox Protocol:** Deploy new layouts or plug-in apps online once — your Kindle immediately gets the new screens and interactive buttons.
* **Smart Context-Aware Lock Screen:** Automatically highlights what's **`HAPPENING NOW`** + **`NEXT UP`**, lists **Rest of Today's Events** and **Top Tasks** on the right, and displays **compact Current Weather** in the header — triggered automatically when pressing the power button inside Hermes OS, while keeping the Kindle 100% stock outside the app.
* **Cross-Device Companion PWA (`/app`):** Sync tasks, daily focus, Pomodoro, Google Calendar OAuth, auto location/timezone, Home Screen apps, and lock-screen modes from **iPhone (iOS), macOS, and Windows**.
* **Paperwhite 10th Gen Simulator (`/preview`):** Test Interactive Web mode, **Native `FBInk` PNG + Hitbox mode**, and **Smart Lock Screen time-of-day states** right in your browser.

## Quick Start (Local)

```bash
cp .env.example .env
./run.sh
```

Then open:
* **PW4 Interactive Simulator:** http://localhost:8000/preview
* **Cross-Device Companion (iOS / macOS / Windows):** http://localhost:8000/app
* **Raw Kindle View (`1072 × 1448`):** http://localhost:8000/kindle
* **Smart Lock Screen PNG (`1072 × 1448` 8-bit grayscale):** http://localhost:8000/lockscreen.png

## Deploy Online (Zero-Touch OTA Updates)

Deploy the included `Dockerfile` to **Railway**, **Fly.io**, **Render**, or any Docker VPS, set `HERMES_TOKEN` in your environment variables for PIN/token protection, and point your Kindle's `config.json` to your server URL.

See [kindle_scripts/README_KINDLE.md](kindle_scripts/README_KINDLE.md) for the complete Paperwhite 10th Gen installation guide.

# KindleHermes OS (v2.1 — Offline-First & Hardened)

An **offline-first**, modular e-ink OS, time-sliced Smart Lock Screen engine, and cross-device web companion built for the **Kindle Paperwhite 10th Gen (PW4 — `1072 × 1448` @ 300 PPI)** — keeping your Kindle 100% stock for normal reading and using **Wi-Fi only for updates and sync**.

> See [`main`](../../tree/main) for the original v1 online thin client and the design notes on why we pivoted to an offline-first architecture.

---

## Highlights (v2.1)
* **100% Airplane-Mode & Offline-First:**
  * Opens **instantly** from persistent local cache (`/mnt/us/kindlehermes/cache/`) without turning Wi-Fi on and never crashes when offline.
  * Browse all **8 screens** (`Home`, `Today`, `Calendar` Today/Tomorrow, `Planner`, `Pomodoro`, `Weather`, `Briefing`) locally in ~50ms via direct `FBInk` rendering.
  * **Offline Task Checkboxes + Idempotent Sync Outbox:** Check off tasks offline — checkboxes flip immediately on screen and queue in `outbox.json` to sync back to your phone/laptop on your next `SYNC`.
  * **Local Offline Pomodoro:** Run *25m Focus / 5m Break / 15m Rest* directly on the Kindle's clock offline with 1-minute e-ink updates and a completion flash.
  * **Time-Sliced Smart Lock Screens Offline:** During `SYNC`, the server pre-renders today's upcoming meeting transitions (`HAPPENING NOW` → `NEXT UP` → `TODAY COMPLETE`). Pressing the power button inside Hermes OS offline automatically paints the exact lock screen for the current time — and tapping **`EXIT`** restores 100% stock Kindle reading with zero lingering hooks.
* **Wi-Fi Only for Sync + Dual Failover + Zero-Wi-Fi USB Sync:**
  * Single-shot `GET /api/offline-bundle` ZIP (~500 KB) + `POST /api/sync/batch` outbox replay.
  * Supports **Cloud first (Railway/Docker HTTPS)** with automatic **local laptop LAN fallback** (`sync_mode: "cloud_first"` or `"local_first"`), plus embedded Mozilla root CAs (`x509roots/fallback`) in the static ARMv7 Go binary for strict TLS verification on older Kindle Linux firmware.
  * Includes `kindle_scripts/usb_sync.sh` to flush checked tasks and refresh the offline cache over USB with zero Kindle Wi-Fi at all.
* **Cross-Device Companion PWA (`/app`) & Simulator (`/preview`):**
  * Manage priorities, daily focus, Pomodoro, Google Calendar OAuth (multi-calendar), auto location/timezone, Open-Meteo weather, Gemini daily briefing, Home Screen apps, and lock-screen modes from **iPhone (iOS), macOS, or Windows**.
  * Test Interactive Web, Native `FBInk` PNG + Hitboxes, and Smart Lock Screen time-of-day states in `/preview`.
* **Self-Hosting Security Hardening (v2.1):**
  * Constant-time `HERMES_TOKEN` PIN authentication (`secrets.compare_digest`), per-IP login brute-force rate limiting (`429` after repeated failures), `HttpOnly` + HTTPS `Secure` cookies, CSRF-protected Google OAuth state tokens, and standard security headers (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, `Permissions-Policy`).

---

## Quick Start (Local Development)

```bash
cp .env.example .env
# Edit .env to add optional GEMINI_API_KEY / GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
./run.sh
```

Open in your browser:
* **Cross-Device Companion (`/app`):** http://localhost:8000/app
* **Paperwhite 10th Gen Simulator (`/preview`):** http://localhost:8000/preview
* **Raw Kindle View (`1072 × 1448`):** http://localhost:8000/kindle
* **Smart Lock Screen PNG (`1072 × 1448` grayscale):** http://localhost:8000/lockscreen.png

---

## Deploy 24/7 to Railway / Docker (Recommended)

Deploy the included `Dockerfile` (includes Python 3.12 + headless Playwright Chromium + Linux serif/sans/mono fonts):

1. Attach a persistent volume mounted at **`/app/backend/data`** (stores SQLite DB, tasks, settings, briefing cache, and Google OAuth tokens).
2. Set environment variables in your host dashboard (never commit `.env`):
   * `HERMES_TOKEN` — strong random personal access PIN/token (**required when public**)
   * `USE_MOCK_DATA=false`
   * `TIMEZONE=Europe/Berlin` *(or your IANA timezone; also auto-syncs from `/app`)*
   * `GEMINI_API_KEY` *(optional — falls back to clean editorial mock briefing if omitted)*
   * `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI=https://<your-domain>/api/google/callback` *(optional — or use `.ics` URLs)*
3. Set healthcheck path to **`/api/health`** and optionally enable **App Sleeping** — since v2.1 is offline-first, your server sleeps when idle and wakes only when you tap `SYNC` or open `/app`.

---

## Installing & Syncing on Kindle Paperwhite 10th Gen (PW4)

```bash
# 1. Cross-compile the static ARMv7 Go binary (macOS/Linux, zero C toolchain needed)
./kindle_client/build_arm.sh

# 2. Install onto USB-mounted Kindle (Cloud primary + local Mac/PC LAN fallback)
./kindle_scripts/install_to_kindle.sh https://your-hermes-server.up.railway.app YOUR_HERMES_TOKEN

# 3. Pre-seed or sync offline cache over USB (works with Kindle in Airplane Mode!)
./kindle_scripts/usb_sync.sh
```

See [kindle_scripts/README_KINDLE.md](kindle_scripts/README_KINDLE.md) for the full Paperwhite 10th Gen / SpiderCat setup guide.

---

## Security & Privacy Checklist Before Self-Hosting
* Always set a strong **`HERMES_TOKEN`** when hosting on a public URL (`python3 -c "import secrets; print('hermes-' + secrets.token_urlsafe(12))"`).
* Keep `.env`, `backend/data/*.db`, and `/mnt/us/kindlehermes/config.json` **private** — all are excluded by `.gitignore`.
* Always serve your cloud instance over **HTTPS** and keep `"insecure_tls": false` in Kindle `config.json`.
* For Google Calendar OAuth, keep your Google Cloud OAuth app in **Testing** mode and add only your own Google account under **Test users**.

---

## Version History
* **`main` (`v1`):** Initial online thin client, server-driven screen/hitbox protocol, companion PWA, and PW4 simulator.
* **`v2`:** Offline-first pivot — single-shot `/api/offline-bundle` ZIP, local cache, offline task outbox (`/api/sync/batch`), local Pomodoro countdown, time-sliced offline Smart Lock Screens, dual Cloud → local failover, and `usb_sync.sh`.
* **`v2.1` (current):** Security hardening (login rate limiting, HTTPS `Secure` cookies, security headers), Airplane-Mode-respecting launcher (no forced Wi-Fi wake), container UTC timezone fix, and full privacy scrub for public open-source release.

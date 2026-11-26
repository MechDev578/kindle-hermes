# KindleHermes OS (v2 — Offline-First)

An **offline-first**, modular e-ink OS, time-sliced Smart Lock Screen engine, and cross-device web companion built for the **Kindle Paperwhite 10th Gen (PW4 — `1072 × 1448` @ 300 PPI)** — keeping your Kindle 100% stock for normal reading and using **Wi-Fi only for updates and sync**.

> See the [`main` branch](../../tree/main) for the original v1 online-only thin client and the writeup of why we pivoted to v2.

## What's New in v2 (Offline-First Architecture)
* **Instant Launch in Airplane Mode:** Opens immediately from persistent local cache (`/mnt/us/kindlehermes/cache/`) with zero Wi-Fi wait and never exits on missing Wi-Fi.
* **Wi-Fi Only for Sync (`GET /api/offline-bundle` + `POST /api/sync/batch`):**
  * Silent background refresh on open *only* if Wi-Fi is already connected, plus a 1-tap manual **`SYNC`** button.
  * Downloads a single compact ZIP (~500 KB) containing all 8 rendered `1072 × 1448` grayscale screens (`home`, `today`, `calendar`, `calendar_tomorrow`, `planner`, `pomodoro`, `weather`, `briefing`), exact touch hitboxes, and time-sliced lock screens.
* **100% Offline Interactivity:**
  * **Instant Local Navigation (~50ms):** Switch freely between all 8 screens offline.
  * **Offline Task Checkboxes + Sync Outbox:** Check off tasks in *Planner* or *Today* offline — checkboxes flip visually right away and queue idempotent updates (`outbox.json`) that flush to your iPhone/Mac on your next sync.
  * **Local Offline Pomodoro:** Run *25m Focus / 5m Break / 15m Rest* directly on the Kindle's own clock offline with 1-minute e-ink updates and a completion invert flash.
  * **Time-Sliced Smart Lock Screens Offline:** During sync, the server pre-renders today's upcoming meeting transitions (`HAPPENING NOW` → `NEXT UP` → `TODAY COMPLETE`). When you press the power button inside Hermes OS offline, the Kindle automatically picks the exact lock screen matching the current time.
* **Dual-Endpoint Failover + Zero-Wi-Fi USB Sync:**
  * Configured with **Cloud first (Railway HTTPS)** → **MacBook LAN fallback** (`sync_mode: "cloud_first"` or `"local_first"`), with Mozilla root CA certificates embedded directly into the static ARMv7 binary (`x509roots/fallback`).
  * Run `./kindle_scripts/usb_sync.sh` over USB anytime to flush your Kindle's offline outbox and refresh the full offline cache without turning Kindle Wi-Fi on at all.
* **100% Stock Kindle Outside Hermes OS:** Tap `EXIT` anytime to release the touchscreen and power-button hooks and return cleanly to your books.

## Quick Start (Local MacBook)

```bash
cp .env.example .env
./run.sh
```

Then open:
* **Cross-Device Companion (iOS / macOS / Windows):** http://localhost:8000/app
* **PW4 Interactive Simulator:** http://localhost:8000/preview
* **Raw Kindle View (`1072 × 1448`):** http://localhost:8000/kindle
* **Smart Lock Screen PNG (`1072 × 1448` 8-bit grayscale):** http://localhost:8000/lockscreen.png

## Deploy Online (24/7 Cloud Sync on Railway / Docker)

Deploy the included `Dockerfile` from branch `v2` with a persistent volume mounted at `/app/backend/data`, set `HERMES_TOKEN` (personal access PIN), `GEMINI_API_KEY`, and Google OAuth credentials (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI=https://<your-domain>/api/google/callback`).

## Installing & Syncing to Paperwhite 10th Gen (PW4)

```bash
# 1. Build the static ARMv7 offline-first binary
./kindle_client/build_arm.sh

# 2. Install onto USB-mounted Kindle (Cloud primary + MacBook LAN fallback)
./kindle_scripts/install_to_kindle.sh https://your-app.up.railway.app YOUR_PIN

# 3. Optional: Pre-seed or sync offline cache over USB (zero Kindle Wi-Fi!)
./kindle_scripts/usb_sync.sh
```

See [kindle_scripts/README_KINDLE.md](kindle_scripts/README_KINDLE.md) for full SpiderCat / Paperwhite 10th Gen details.

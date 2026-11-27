# KindleHermes OS (v3.2.1 — Offline-First E-Ink OS & Hermes Deutsch)

An **offline-first**, modular e-ink OS, **Hermes Deutsch (`A2 → C2`)** language studio, time-sliced Smart Lock Screen engine, and cross-device web companion built for the **Kindle Paperwhite 10th Gen (PW4 — `1072 × 1448` @ 300 PPI)** — keeping your Kindle 100% stock for normal reading and using **Wi-Fi only for updates and sync**.

> **`main` tracks the latest release (`v3.2.1`).** Archived milestones are preserved on branches [`v1`](../../tree/v1) (original online thin client + design notes on why we pivoted), [`v2`](../../tree/v2), [`v2.1`](../../tree/v2.1), [`v3`](../../tree/v3), [`v3.1`](../../tree/v3.1), [`v3.2`](../../tree/v3.2), and [`v3.2.1`](../../tree/v3.2.1).

---

## Highlights (v3)

### 1. Hermes Deutsch (`A2 → C2`) — Built for E-Ink
* **FSRS-4.5 Blitz & Gender Lab:** 46 seeded German chunks, *Der/Die/Das* suffix rules, *Modalpartikeln*, *Verb + Präposition*, and *Nomen-Verb-Verbindungen* scheduled with an FSRS-4.5 algorithm (`1=Again`, `2=Hard`, `3=Good`, `4=Easy`). Front/Back cards are pre-rendered for instant offline drilling and ratings sync back to the server on your next `SYNC`.
* **Famous Texts & Iconic Speeches (`15` built-in + custom import):** Read Goethe, Kafka, Nietzsche, Hesse, Rilke, Einstein, Arendt, Willy Brandt, and more across 3 zero-glare modes — **`DE + GLOSS`**, **`PARALLEL DE|EN`**, and **`X-RAY & TALK`** (grammar breakdown & speaking prompts) — with 1-tap chunk mining directly into your FSRS deck.
* **`Tageslektüre` (Daily `i+1` Reader):** Pulls fresh German news feeds (Tagesschau / DW) and uses Gemini to adapt them into a daily `i+1` reading passage with an interactive comprehension quiz (falls back cleanly to built-in readers when offline or without an API key).
* **`Wurzel & Slang` (`6` Verb-Prefix Packs):** Master high-leverage separable/inseparable verb families and colloquial spoken German — all 6 packs are bundled offline.
* **`Sprachindex` (`0–1000`) & Deutsch Lock Screen:** Tracks weighted CEFR progress across `A2 → B1 → B2 → C1 → C2` and adds an optional **`deutsch`** Smart Lock Screen slide (`lockscreen_deutsch`) for passive daily vocabulary reinforcement.
* **Companion Web Studio (`/app`):** Practice **`de-DE` Audio Shadowing**, run 2-minute **`Schreib-Sprint`** sessions with AI `A2→C2` recasts, and add custom flashcards or speeches from iPhone, macOS, or Windows.

### 2. 100% Offline-First + Online-Update Sync
* **Instant Airplane-Mode Boot:** Opens immediately from local cache (`/mnt/us/kindlehermes/cache/`) without turning Wi-Fi on and never crashes when offline.
* **Parallel Offline Bundle (`/api/offline-bundle`):** A 4-page Playwright Chromium worker pool pre-renders all **12 screens** (`Home`, `Today`, `Calendar` Today/Tomorrow, `Planner`, `Pomodoro`, `Weather`, `Briefing`, `Deutsch Hub`, `FSRS Blitz`, `Tageslektüre`, `Famous Texts`), **10 FSRS flashcard pairs**, **4 rotating Famous Texts (×3 modes)**, all **6 Root Packs**, and time-sliced Smart Lock Screens into a single `1072 × 1448` 8-bit grayscale ZIP (~2 MB, rendered in ~7–12s).
* **Persistent Offline Outbox (`outbox.json`):** Offline task checkboxes, FSRS card ratings, quiz attempts, and Famous Text / Root Pack bookmarks persist across offline restarts and replay idempotently via `POST /api/sync/batch`.
* **Pack Pagination & Content Updates:** Step through your offline FSRS cards or Famous Texts (`END OF PACK · SYNC FOR MORE TEXTS`), then tap **`SYNC`** online — or plug into USB and run `usb_sync.sh` — to commit your progress and download the next batch of due cards, upcoming classics, and today's fresh `Tageslektüre`.
* **Local Offline Pomodoro & Time-Sliced Smart Lock Screens:** Run *25m Focus / 5m Break / 15m Rest* directly on the Kindle's local clock. Pressing power inside Hermes OS paints the time-accurate meeting slide (`HAPPENING NOW` → `NEXT UP` → `TODAY COMPLETE` / `DEUTSCH`), while tapping **`EXIT`** restores 100% stock Kindle reading with zero lingering hooks.
* **Dual Failover & Zero-Wi-Fi USB Sync:** Supports **Cloud primary (Railway/Docker HTTPS)** with automatic **local LAN fallback** (`sync_mode: "cloud_first"` or `"local_first"`), embedded Mozilla root CAs (`x509roots/fallback`) in the static ARMv7 Go binary, and `kindle_scripts/usb_sync.sh` for completely Wi-Fi-free syncing.

### 3. Cross-Device Companion PWA (`/app`), Simulator (`/preview`) & Security
* **Companion PWA (`/app`) & PW4 Simulator (`/preview`):** Manage priorities, daily focus, Pomodoro, Google Calendar OAuth (multi-calendar) or `.ics` feeds, auto location/timezone, Open-Meteo weather, Gemini daily briefing, Home Screen modules, lock-screen modes, and Hermes Deutsch tools from any browser.
* **Self-Hosting Security Hardening:** Constant-time `HERMES_TOKEN` PIN authentication (`secrets.compare_digest`), per-IP login brute-force rate limiting (`429`), `HttpOnly` + HTTPS `Secure` cookies, CSRF-protected Google OAuth state tokens, and standard security headers (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, `Permissions-Policy`).

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
* **Deutsch Lock Screen Slide:** http://localhost:8000/lockscreen.png?slide=deutsch

---

## Deploy 24/7 to Railway / Docker (Recommended)

Deploy the included `Dockerfile` (Python 3.12 + headless Playwright Chromium + Linux serif/sans/mono fonts):

1. Attach a persistent volume mounted at **`/app/backend/data`** (stores SQLite DB, tasks, settings, FSRS progress, custom German cards/texts, briefing cache, and Google OAuth tokens).
2. Set environment variables in your host dashboard (never commit `.env`):
   * `HERMES_TOKEN` — strong random personal access PIN/token (**required when public**)
   * `USE_MOCK_DATA=false`
   * `TIMEZONE=Europe/Berlin` *(or your IANA timezone; also auto-syncs from `/app`)*
   * `GEMINI_API_KEY` *(optional — powers live `Tageslektüre` i+1 adaptation, `Schreib-Sprint` recasts, and Morning Briefing; clean offline fallbacks activate if omitted)*
   * `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI=https://<your-domain>/api/google/callback` *(optional — or use `.ics` URLs)*
3. Set the healthcheck path to **`/api/health`** and optionally enable **App Sleeping** — because v3 is offline-first, your server sleeps when idle and wakes only when you tap `SYNC` or open `/app`.

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
* **[`v1`](../../tree/v1):** Initial online thin client, server-driven screen/hitbox protocol, companion PWA, PW4 simulator, and post-mortem notes on why we pivoted away from an always-online model.
* **[`v2`](../../tree/v2):** Offline-first pivot — single-shot `/api/offline-bundle` ZIP, local cache, offline task outbox (`/api/sync/batch`), local Pomodoro countdown, time-sliced offline Smart Lock Screens, dual Cloud → local failover, and `usb_sync.sh`.
* **[`v2.1`](../../tree/v2.1):** Security hardening (login rate limiting, HTTPS `Secure` cookies, security headers), Airplane-Mode-respecting launcher, container UTC timezone fix, and full privacy scrub for public open-source release.
* **[`v3`](../../tree/v3):** **Hermes Deutsch (`A2 → C2`)** full e-ink & companion language studio (FSRS-4.5 Blitz, 15 Famous Texts in 3 modes, live `Tageslektüre` + quiz, 6 Verb-Prefix Root Packs, `Sprachindex`, `de-DE` Audio Shadowing & `Schreib-Sprint`, Deutsch lock screen), parallel 4-page Playwright bundle renderer, persistent offline FSRS/bookmark outbox replay across restarts, and online pack pagination.
* **[`v3.1`](../../tree/v3.1):** Fixed PW4 lock-screen race & suspend-refresh loop in the native Go client (`lockscreen.go`): waits for Amazon's stock `blanket` wallpaper draw to settle via `/dev/fb0` quiescence before painting once cleanly, watches `/dev/fb0` for 8s to catch any late second-pass stock overwrite, cancels pending lock paints immediately on fast unlock, and turns `readyToSuspend` into a strict no-op when the lock screen is intact so `powerd` enters deep sleep immediately with zero repeated flashes.
* **[`v3.2`](../../tree/v3.2):** Live on-device Kindle clock (accurate to the minute on exact `:00` boundaries with automatic re-alignment after deep sleep), hardware battery gauge & percentage (with charging & low-battery alert pill) composited onto every interactive screen topbar and Smart Lock Screen (`sysinfo.go` + crisp geometric digits in `offline_ui.go`), plus automatic 30-minute silent background Wi-Fi sync (respects Airplane Mode).
* **[`v3.2.1`](../../tree/v3.2.1) / `main` (Latest):** Replaced the static lock-screen wall clock with today's bold date (`FRI 27 NOV`) plus honest **`LAST WAKE HH:MM`** metadata (tracked on launch and every wake) alongside the live hardware battery gauge (`CHG` / low-battery pill) so deep sleep never displays a misleading frozen time, while unlocked screens keep the live minute-by-minute clock.

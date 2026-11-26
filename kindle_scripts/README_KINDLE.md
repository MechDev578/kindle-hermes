# Kindle Paperwhite 10th Gen (PW4) Setup Guide (v2.1 Offline-First)

Target hardware: **Kindle Paperwhite 10th Gen (PW4, 2018)** — `1072 × 1448` @ 300 PPI (E Ink Carta HD).

---

## Modern SpiderCat / `hdnext` Stack (Recommended for `5.16.3 – 5.18.1.1.1+`)

If you jailbroke with **SpiderCat** (or any modern `hdnext` jailbreak from `kindlemodding.org`):
* **OTA updates are already blocked automatically** (no `renameotabin` needed).
* **FBInk is already installed** at `/mnt/us/libkh/bin/fbink`.
* **Scriptlets (`SH_Integration`) are already enabled** — `.sh` files in `/mnt/us/documents/` appear directly in your Kindle Library as **1-tap app tiles** (no legacy KUAL/MRPI needed).
* **KPM (Kindle Package Manager) is already installed** for post-jailbreak utilities right from the Kindle search bar.

### 1. Build Static ARMv7 Binary (Once on macOS / Linux)
```bash
brew install go
./kindle_client/build_arm.sh
```
This builds a single self-contained ARMv7 ELF binary (`kindle_client/hermes-client-arm`) with embedded Mozilla root CA certificates (`x509roots/fallback`) so strict HTTPS works out of the box.

### 2. Install KindleHermes v2.1 to Your Mounted Kindle (1 Command)
Plug your PW4 in via USB (`/Volumes/Kindle`) and run:
```bash
# Cloud primary (24/7) + automatic local laptop LAN fallback:
./kindle_scripts/install_to_kindle.sh https://your-hermes-server.up.railway.app YOUR_HERMES_TOKEN

# Or local laptop Wi-Fi only:
./kindle_scripts/install_to_kindle.sh http://192.168.1.50:8000
```

This installs:
* `/Volumes/Kindle/documents/KindleHermes_OS.sh` — single clean 1-tap Library tile to launch fullscreen Hermes OS
* `/Volumes/Kindle/kindlehermes/` — static `hermes-client-arm`, `config.json`, cover icon, offline `cache/`, and launcher scripts

### 3. Pre-Seed or Sync Offline Cache Over USB (Optional, Zero Kindle Wi-Fi)
```bash
./kindle_scripts/usb_sync.sh
```
This uploads any pending offline task checkboxes (`cache/outbox.json`) to your server and writes a fresh offline bundle (all 8 screens + time-sliced Smart Lock Screens) straight to `/Volumes/Kindle/kindlehermes/cache/`.

### 4. Daily Offline-First Usage
Safely eject your Kindle and tap **`KindleHermes OS`** in your Library:
* **Airplane Mode friendly:** Opens immediately from local cache without forcing Wi-Fi on.
* **Browse all 8 screens offline:** Home, Today, Calendar (Today/Tomorrow), Planner, Pomodoro, Weather, and Morning Briefing load locally in ~50ms.
* **Check off tasks & run Pomodoro offline:** Task changes queue in `outbox.json`; Pomodoro counts down locally on the Kindle clock.
* **Sync when you want:** Turn on Wi-Fi and tap **`SYNC`** (or open Hermes OS while Wi-Fi is already on for a silent background refresh).
* **In-App Smart Lock Screen:** Press the power button while inside Hermes OS to paint the time-accurate Smart Lock Screen for your current meeting slot.
* **100% Stock Kindle Outside Hermes OS:** Tap **`EXIT`** (bottom-right) to release touchscreen and power-button hooks and return cleanly to your books.
* Diagnostic logs are saved at `/mnt/us/kindlehermes/hermes.log`.

---

## Touchscreen Calibration (If Taps Feel Rotated/Offset)
Edit `/mnt/us/kindlehermes/config.json` and toggle `swap_xy`, `invert_x`, or `invert_y` — no rebuild required.

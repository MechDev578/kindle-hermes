#!/usr/bin/env bash
# One-command USB installer for Kindle Paperwhite 10th Gen (PW4).
# Supports BOTH:
#   - Modern SpiderCat / hdnext Scriptlets (1-tap icons right in /Volumes/Kindle/documents/!)
#   - Legacy KUAL extensions (automatically mirrored if /Volumes/Kindle/extensions/ exists)
#
# Usage: ./kindle_scripts/install_to_kindle.sh https://your-app.up.railway.app YOUR_PIN [http://192.168.1.50:8000]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KINDLE_MOUNT="${KINDLE_MOUNT:-/Volumes/Kindle}"
SERVER_URL="${1:-}"
HERMES_TOKEN="${2:-}"
LOCAL_SERVER_URL="${3:-}"
SYNC_MODE="${4:-cloud_first}"

if [ -z "${SERVER_URL}" ]; then
  LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "YOUR_MAC_IP")
  echo "Usage: $0 <SERVER_URL> [HERMES_TOKEN] [LOCAL_MACBOOK_URL] [cloud_first|local_first]"
  echo "Example (cloud + MacBook fallback): $0 https://your-app.up.railway.app my-pin http://${LOCAL_IP}:8000"
  echo "Example (local Wi-Fi only):         $0 http://${LOCAL_IP}:8000"
  exit 1
fi

if [ -z "${LOCAL_SERVER_URL}" ]; then
  DETECTED_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
  if [ -n "${DETECTED_IP}" ] && [[ "${SERVER_URL}" == https://* ]]; then
    LOCAL_SERVER_URL="http://${DETECTED_IP}:8000"
  fi
fi

if [ ! -d "${KINDLE_MOUNT}" ]; then
  echo "Kindle drive not found at ${KINDLE_MOUNT}."
  echo "Plug your Paperwhite 10th Gen in via USB and re-run (or set KINDLE_MOUNT=/path/to/Kindle)."
  exit 1
fi

APP_DIR="${KINDLE_MOUNT}/kindlehermes"
DOCS_DIR="${KINDLE_MOUNT}/documents"
export COPYFILE_DISABLE=1
mkdir -p "${APP_DIR}/bin" "${APP_DIR}/cache/screens" "${APP_DIR}/cache/lockscreens" "${DOCS_DIR}" "${KINDLE_MOUNT}/hermes_screensavers"

cp "${ROOT_DIR}/kindle_scripts/extensions/kindlehermes/bin/launch_hermes.sh" "${APP_DIR}/bin/launch_hermes.sh"
cp "${ROOT_DIR}/kindle_scripts/extensions/kindlehermes/bin/sync_lockscreen.sh" "${APP_DIR}/bin/sync_lockscreen.sh"
cp "${ROOT_DIR}/kindle_client/hermes_native.py" "${APP_DIR}/bin/hermes_native.py"

if [ -f "${ROOT_DIR}/kindle_client/hermes-client-arm" ]; then
  cp "${ROOT_DIR}/kindle_client/hermes-client-arm" "${APP_DIR}/bin/hermes-client-arm"
  echo "Copied static ARMv7 Go binary: hermes-client-arm"
else
  echo "WARNING: hermes-client-arm not built yet! Run ./kindle_client/build_arm.sh first."
fi

# Generate crisp monochrome e-ink cover icons for the Kindle library tiles
"${ROOT_DIR}/.venv/bin/python" - "${APP_DIR}" <<'PYEOF'
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

out = Path(sys.argv[1])

def load_font(size: int, serif: bool = False):
    candidates = (
        ["/System/Library/Fonts/Supplemental/Georgia Bold.ttf", "/System/Library/Fonts/Supplemental/Georgia.ttf"]
        if serif
        else ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
    )
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def make_icon(path: Path, top_tag: str, big_lines: list[str], sub: str, inverted: bool = False):
    w, h = 420, 560
    bg = 0 if inverted else 255
    fg = 255 if inverted else 0
    img = Image.new("L", (w, h), color=bg)
    d = ImageDraw.Draw(img)
    d.rectangle([18, 18, w - 18, h - 18], outline=fg, width=8)
    d.rectangle([40, 48, w - 40, 114], fill=fg)
    f_tag = load_font(26, serif=False)
    f_big = load_font(54, serif=True)
    f_sub = load_font(24, serif=False)
    d.text((62, 66), top_tag, fill=bg, font=f_tag)
    y = 176
    for line in big_lines:
        d.text((44, y), line, fill=fg, font=f_big)
        y += 72
    d.line([40, h - 122, w - 40, h - 122], fill=fg, width=5)
    d.text((44, h - 94), sub, fill=fg, font=f_sub)
    img.save(path, format="PNG", optimize=True)

make_icon(out / "icon_hermes.png", "HERMES OS", ["Kindle", "Hermes"], "1-TAP LAUNCHER", inverted=False)
make_icon(out / "icon_lock.png", "LOCK SCREEN", ["Hermes", "Sync"], "CALENDAR · WX", inverted=True)
PYEOF

cat > "${APP_DIR}/config.json" <<EOF
{
  "server_url": "${SERVER_URL%/}",
  "token": "${HERMES_TOKEN}",
  "local_server_url": "${LOCAL_SERVER_URL%/}",
  "local_token": "",
  "sync_mode": "${SYNC_MODE}",
  "cache_dir": "/mnt/us/kindlehermes/cache",
  "fbink_bin": "/mnt/us/libkh/bin/fbink",
  "touch_device": "",
  "swap_xy": false,
  "invert_x": false,
  "invert_y": false,
  "insecure_tls": false
}
EOF

# Install single clean 1-tap SpiderCat Scriptlet directly into /Volumes/Kindle/documents/
cat > "${DOCS_DIR}/KindleHermes_OS.sh" <<'EOF'
#!/bin/sh
# Name: KindleHermes OS
# Author: Hermes
# Icon: /mnt/us/kindlehermes/icon_hermes.png
# DontUseFBInk
exec /bin/sh /mnt/us/kindlehermes/bin/launch_hermes.sh
EOF

# Remove legacy separate lock-sync scriptlet and macOS AppleDouble files if present
rm -f "${DOCS_DIR}/Hermes_Lock_Sync.sh" "${DOCS_DIR}/._"* "${APP_DIR}/._"* "${APP_DIR}/bin/._"* "${APP_DIR}/cache/._"* "${KINDLE_MOUNT}/hermes_screensavers/._"* 2>/dev/null || true

# Also install legacy KUAL extension only if /Volumes/Kindle/extensions already exists
if [ -d "${KINDLE_MOUNT}/extensions" ]; then
  KUAL_DIR="${KINDLE_MOUNT}/extensions/kindlehermes"
  mkdir -p "${KUAL_DIR}/bin"
  cp -R "${ROOT_DIR}/kindle_scripts/extensions/kindlehermes/"* "${KUAL_DIR}/"
  cp "${APP_DIR}/config.json" "${KUAL_DIR}/config.json"
  cp "${APP_DIR}/bin/hermes_native.py" "${KUAL_DIR}/bin/"
  [ -f "${APP_DIR}/bin/hermes-client-arm" ] && cp "${APP_DIR}/bin/hermes-client-arm" "${KUAL_DIR}/bin/"
fi

echo ""
echo "Installed Offline-First KindleHermes OS to: ${APP_DIR}"
echo "Installed 1-tap Scriptlet: ${DOCS_DIR}/KindleHermes_OS.sh"
echo "Primary server_url:       ${SERVER_URL%/}"
[ -n "${LOCAL_SERVER_URL}" ] && echo "MacBook fallback URL:     ${LOCAL_SERVER_URL%/} (sync_mode: ${SYNC_MODE})"
echo "Tip: Run ./kindle_scripts/usb_sync.sh anytime to pre-seed or sync offline cache over USB!"

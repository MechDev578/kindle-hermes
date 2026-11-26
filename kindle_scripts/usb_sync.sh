#!/usr/bin/env bash
# Zero-Wi-Fi USB Offline Sync for Kindle Paperwhite 10th Gen (PW4).
# 1. Reads your Kindle's /Volumes/Kindle/kindlehermes/config.json (cloud + MacBook fallback).
# 2. Uploads any pending offline task toggles from /Volumes/Kindle/kindlehermes/cache/outbox.json.
# 3. Downloads a fresh /api/offline-bundle (all 8 screens + time-sliced Smart Lock Screens) directly onto the Kindle!
#
# Usage:
#   ./kindle_scripts/usb_sync.sh                     # uses config.json (cloud -> MacBook fallback)
#   ./kindle_scripts/usb_sync.sh http://127.0.0.1:8000   # force local MacBook server
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KINDLE_MOUNT="${KINDLE_MOUNT:-/Volumes/Kindle}"
OVERRIDE_URL="${1:-}"
OVERRIDE_TOKEN="${2:-}"

if [ ! -d "${KINDLE_MOUNT}" ]; then
  echo "Kindle drive not found at ${KINDLE_MOUNT}. Plug your Kindle in via USB first."
  exit 1
fi

export COPYFILE_DISABLE=1

"${ROOT_DIR}/.venv/bin/python" - "${KINDLE_MOUNT}" "${OVERRIDE_URL}" "${OVERRIDE_TOKEN}" <<'PYEOF'
import io
import json
import sys
import zipfile
from pathlib import Path
import httpx

kindle_mount = Path(sys.argv[1])
override_url = sys.argv[2].strip().rstrip("/")
override_token = sys.argv[3].strip()

app_dir = kindle_mount / "kindlehermes"
cache_dir = app_dir / "cache"
cfg_path = app_dir / "config.json"
outbox_path = cache_dir / "outbox.json"

endpoints: list[tuple[str, str, str]] = []
if override_url:
    endpoints.append(("override", override_url, override_token))
else:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cloud = ("cloud", (cfg.get("server_url") or "").strip().rstrip("/"), (cfg.get("token") or "").strip())
    local = ("macbook", (cfg.get("local_server_url") or "http://127.0.0.1:8000").strip().rstrip("/"), (cfg.get("local_token") or "").strip())
    local_loopback = ("macbook-local", "http://127.0.0.1:8000", (cfg.get("local_token") or "").strip())
    order = [local, local_loopback, cloud] if cfg.get("sync_mode") == "local_first" else [cloud, local, local_loopback]
    seen = set()
    for name, url, tok in order:
        if url and url not in seen:
            seen.add(url)
            endpoints.append((name, url, tok))

if not endpoints:
    print("No server_url configured. Pass a URL: ./kindle_scripts/usb_sync.sh http://127.0.0.1:8000")
    sys.exit(1)

outbox = []
if outbox_path.exists():
    try:
        outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    except Exception:
        outbox = []

synced = False
last_err = None
with httpx.Client(timeout=90.0, follow_redirects=True) as client:
    for name, url, tok in endpoints:
        headers = {"X-Hermes-Token": tok} if tok else {}
        try:
            print(f"Trying {name} ({url})...")
            if outbox:
                r_batch = client.post(
                    f"{url}/api/sync/batch",
                    headers=headers,
                    json={"actions": outbox, "refresh_feeds": True},
                )
                r_batch.raise_for_status()
                print(f"  Flushed {len(outbox)} offline action(s) to {name}.")
                outbox_path.write_text("[]", encoding="utf-8")
                outbox = []

            r_zip = client.get(f"{url}/api/offline-bundle?force_refresh=1&include_slots=1", headers=headers)
            r_zip.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(r_zip.content))

            # Clean stale lockscreen slots & deutsch cards
            for sub in ("lockscreens", "deutsch"):
                sub_dir = cache_dir / sub
                sub_dir.mkdir(parents=True, exist_ok=True)
                for old in sub_dir.glob("*.png"):
                    old.unlink(missing_ok=True)

            for member in zf.namelist():
                if member.startswith("/") or ".." in member:
                    continue
                target = cache_dir / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))

            manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
            def_ls = cache_dir / manifest.get("lockscreen_default", "lockscreens/default.png")
            ss_dir = kindle_mount / "hermes_screensavers"
            if def_ls.exists() and ss_dir.exists():
                (ss_dir / "hermes_01.png").write_bytes(def_ls.read_bytes())

            print(
                f"Synced via {name}: {len(manifest.get('screens', {}))} screens + "
                f"{len(manifest.get('lockscreen_slots', []))} Smart Lock Screen time slots "
                f"(updated {manifest.get('synced_at_label', '')})."
            )
            synced = True
            break
        except Exception as exc:
            last_err = f"{name}: {exc}"
            print(f"  Skipped {name}: {exc}")

# Clean any macOS AppleDouble dotfiles
for dot in kindle_mount.rglob("._*"):
    if str(dot).startswith(str(app_dir)) or str(dot).startswith(str(kindle_mount / "hermes_screensavers")):
        dot.unlink(missing_ok=True)

if not synced:
    print(f"USB sync failed ({last_err}). Make sure Railway or ./run.sh is running.")
    sys.exit(1)

print("Offline cache updated cleanly on Kindle — safe to eject and use in Airplane Mode!")
PYEOF

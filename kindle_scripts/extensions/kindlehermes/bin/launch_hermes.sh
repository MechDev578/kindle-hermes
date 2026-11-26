#!/bin/sh
# Launcher for KindleHermes OS on Kindle Paperwhite 10th Gen (PW4 — SpiderCat / hdnext).
# - Respects Airplane Mode (offline-first): never forces Wi-Fi on at launch.
# - Leaves powerd screensaver enabled so pressing the Power button inside Hermes OS
#   triggers your Smart Lock Screen seamlessly!
# - On EXIT, exits cleanly with zero lingering hooks so normal Kindle acts 100% stock.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CFG_FILE="${EXT_DIR}/config.json"
LOG_FILE="${EXT_DIR}/hermes.log"
GO_SRC="${EXT_DIR}/bin/hermes-client-arm"
PY_CLIENT="${EXT_DIR}/bin/hermes_native.py"
TMP_BIN="/tmp/hermes-client-arm"
TMP_FBINK="/tmp/hermes-fbink"

exec >>"${LOG_FILE}" 2>&1
echo "=== Launch Hermes OS: $(date) (EXT_DIR=${EXT_DIR}) ==="

export PATH="/mnt/us/libkh/bin:/mnt/us/python3/bin:/mnt/us/usbnet/bin:/mnt/us/linkss/bin:/usr/local/bin:/usr/bin:/bin:${PATH}"

find_fbink() {
  for cand in \
    /mnt/us/libkh/bin/fbink \
    "${EXT_DIR}/bin/fbink" \
    /mnt/us/extensions/MRInstaller/bin/KHF/fbink \
    /mnt/us/extensions/MRInstaller/bin/PW2/fbink \
    /mnt/us/extensions/MRInstaller/bin/K5/fbink \
    /mnt/us/extensions/MRInstaller/bin/fbink \
    /mnt/us/usbnet/bin/fbink \
    /mnt/us/linkss/bin/fbink \
    /usr/local/bin/fbink \
    /usr/bin/fbink; do
    if [ -f "${cand}" ]; then
      echo "${cand}"
      return
    fi
  done
  command -v fbink 2>/dev/null || echo ""
}

FBINK_SRC=$(find_fbink)
if [ -n "${FBINK_SRC}" ]; then
  case "${FBINK_SRC}" in
    /mnt/us/*)
      cp "${FBINK_SRC}" "${TMP_FBINK}"
      chmod +x "${TMP_FBINK}"
      export HERMES_FBINK="${TMP_FBINK}"
      ;;
    *)
      export HERMES_FBINK="${FBINK_SRC}"
      ;;
  esac
  echo "Using FBInk: ${HERMES_FBINK} (source: ${FBINK_SRC})"
else
  echo "ERROR: fbink not found anywhere on device"
  eips 2 4 "KindleHermes: fbink not found." 2>/dev/null || true
  sleep 4
  exit 1
fi

find_python3() {
  for cand in /usr/bin/python3 /mnt/us/python3/bin/python3; do
    if [ -x "${cand}" ]; then
      echo "${cand}"
      return
    fi
  done
  command -v python3 2>/dev/null || echo ""
}

cleanup() {
  echo "=== Exiting Hermes OS: $(date) ==="
  lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null || true
  lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Respect Airplane Mode (offline-first): do NOT force Wi-Fi on at launch.
# Keep preventScreenSaver=0 so power-button locking works naturally inside Hermes OS.
lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null || true

# Pause briefly so Kindle's scriptlet opener settles, then show loading message
sleep 1
"${HERMES_FBINK}" -q -c -f -m -y 12 "Starting KindleHermes OS..." 2>/dev/null || true

if [ -f "${GO_SRC}" ]; then
  cp "${GO_SRC}" "${TMP_BIN}"
  chmod +x "${TMP_BIN}"
  echo "Launching static binary: ${TMP_BIN}"
  "${TMP_BIN}" "${CFG_FILE}"
else
  PY_BIN=$(find_python3)
  if [ -n "${PY_BIN}" ] && [ -f "${PY_CLIENT}" ]; then
    echo "Launching Python client via ${PY_BIN}"
    "${PY_BIN}" "${PY_CLIENT}" "${CFG_FILE}"
  else
    "${HERMES_FBINK}" -c -f -m -y 6 "KindleHermes: Missing hermes-client-arm binary" 2>/dev/null || true
    sleep 5
  fi
fi

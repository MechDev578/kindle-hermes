#!/bin/sh
# Syncs Context-Aware Smart Lock Screen (or all enabled cycle slides) to Kindle PW4 (1072x1448).
# Stages fbink to /tmp if needed (bypassing /mnt/us noexec) and saves crisp PNGs to:
#   - /mnt/us/hermes_screensavers/ (SpiderCat / 5.18.x KOReader & userspace screensaver mods)
#   - /mnt/us/linkss/screensavers/ (if legacy linkss is installed)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CFG_FILE="${EXT_DIR}/config.json"
LINKSS_DIR="/mnt/us/linkss/screensavers"
HERMES_SS_DIR="/mnt/us/hermes_screensavers"
TMP_JSON="/tmp/hermes_slides.json"
TMP_PNG="/tmp/hermes_lock.png"
TMP_FBINK="/tmp/hermes-fbink"
PAINT_NOW=0

export PATH="/mnt/us/libkh/bin:/mnt/us/usbnet/bin:/mnt/us/linkss/bin:/usr/local/bin:/usr/bin:/bin:${PATH}"

if [ "${1:-}" = "--paint" ]; then
  PAINT_NOW=1
fi

SERVER_URL=$(sed -n 's/.*"server_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${CFG_FILE}" | head -n 1)
TOKEN=$(sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${CFG_FILE}" | head -n 1)

if [ -z "${SERVER_URL}" ]; then
  echo "Missing server_url in ${CFG_FILE}" >&2
  exit 1
fi

lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null || true
sleep 2

fetch_url() {
  URL="$1"
  OUT="$2"
  if command -v curl >/dev/null 2>&1; then
    if [ -n "${TOKEN}" ]; then
      curl -fsSL -H "X-Hermes-Token: ${TOKEN}" "${URL}" -o "${OUT}"
    else
      curl -fsSL "${URL}" -o "${OUT}"
    fi
  else
    if [ -n "${TOKEN}" ]; then
      case "${URL}" in
        *\?*) URL="${URL}&token=${TOKEN}" ;;
        *)    URL="${URL}?token=${TOKEN}" ;;
      esac
    fi
    wget -q -O "${OUT}" "${URL}"
  fi
}

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

fetch_url "${SERVER_URL}/api/lockscreen/slides" "${TMP_JSON}"

mkdir -p "${HERMES_SS_DIR}"
rm -f "${HERMES_SS_DIR}"/hermes_*.png
SLIDE_URLS=$(grep -o '"url"[[:space:]]*:[[:space:]]*"[^"]*"' "${TMP_JSON}" | sed 's/.*"url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
IDX=1
for REL_URL in ${SLIDE_URLS}; do
  DEST="${HERMES_SS_DIR}/hermes_$(printf '%02d' "${IDX}").png"
  fetch_url "${SERVER_URL}${REL_URL}" "${DEST}.part" && mv "${DEST}.part" "${DEST}"
  IDX=$((IDX + 1))
done

if [ -d "${LINKSS_DIR}" ]; then
  rm -f "${LINKSS_DIR}"/hermes_*.png
  cp "${HERMES_SS_DIR}"/hermes_*.png "${LINKSS_DIR}/" 2>/dev/null || true
  touch /mnt/us/linkss/reboot 2>/dev/null || true
fi

if [ "${PAINT_NOW}" = "1" ]; then
  fetch_url "${SERVER_URL}/lockscreen.png?slide=auto&advance=true" "${TMP_PNG}"
  FBINK_SRC=$(find_fbink)
  if [ -n "${FBINK_SRC}" ]; then
    case "${FBINK_SRC}" in
      /mnt/us/*) cp "${FBINK_SRC}" "${TMP_FBINK}" && chmod +x "${TMP_FBINK}" && FBINK_RUN="${TMP_FBINK}" ;;
      *) FBINK_RUN="${FBINK_SRC}" ;;
    esac
    "${FBINK_RUN}" -c -f -g "file=${TMP_PNG},w=1072,h=1448,halign=center,valign=center"
  fi
fi

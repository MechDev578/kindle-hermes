#!/usr/bin/env python3
"""KindleHermes Native FBInk Thin-Client for Kindle Paperwhite 10th Gen (1072x1448).

Zero external dependencies (Python 3 stdlib only).
- Paints server-rendered 1072x1448 8-bit grayscale PNGs directly via FBInk (no browser!).
- Reads raw Linux touchscreen events (/dev/input/event*) with automatic EVIOCGABS calibration.
- Matches finger taps to server-supplied interactive hitboxes and dispatches POST /api/action.
- Respects server refresh_after_sec (1-min Pomodoro updates) and invert_flash alerts.
- Exits cleanly back to Kindle Library when the user taps EXIT (system:exit).
"""

from __future__ import annotations

import fcntl
import glob
import json
import os
import select
import signal
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KINDLE_W = 1072
KINDLE_H = 1448

# Linux input event constants
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

BTN_TOUCH = 0x14A
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39

# ioctl helpers for Linux evdev on 32-bit ARM Linux
# EVIOCGRAB = _IOW('E', 0x90, int) = 0x40044590 — prevents Kindle OS from receiving ghost taps underneath!
EVIOCGRAB = 0x40044590


def _eviocgabs(axis: int) -> int:
    # _IOR('E', 0x40 + axis, struct input_absinfo) (6 * int32 = 24 bytes)
    return (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + axis)


def _get_abs_bounds(fd: int, axis: int) -> tuple[int, int] | None:
    try:
        buf = bytearray(24)
        fcntl.ioctl(fd, _eviocgabs(axis), buf)
        _val, minimum, maximum, _fuzz, _flat, _res = struct.unpack("6i", buf)
        if maximum > minimum:
            return minimum, maximum
    except OSError:
        return None
    return None


def detect_touch_device(preferred: str = "") -> tuple[str, tuple[int, int], tuple[int, int]]:
    candidates = [preferred] if preferred else sorted(glob.glob("/dev/input/event*"))
    for dev in candidates:
        if not dev or not os.path.exists(dev):
            continue
        try:
            fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            mt_x = _get_abs_bounds(fd, ABS_MT_POSITION_X)
            mt_y = _get_abs_bounds(fd, ABS_MT_POSITION_Y)
            if mt_x and mt_y:
                return dev, mt_x, mt_y
            ax = _get_abs_bounds(fd, ABS_X)
            ay = _get_abs_bounds(fd, ABS_Y)
            if ax and ay:
                return dev, ax, ay
        finally:
            os.close(fd)
    raise RuntimeError("No touchscreen input device found under /dev/input/event*")


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"Missing KindleHermes config file: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


class HermesNativeClient:
    def __init__(self, cfg: dict) -> None:
        self.server_url = cfg["server_url"].rstrip("/")
        self.token = cfg.get("token", "").strip()
        self.fbink = os.environ.get("HERMES_FBINK") or cfg.get("fbink_bin", "fbink")
        self.touch_override = cfg.get("touch_device", "")
        self.swap_xy = bool(cfg.get("swap_xy", False))
        self.invert_x = bool(cfg.get("invert_x", False))
        self.invert_y = bool(cfg.get("invert_y", False))
        self.insecure_tls = bool(cfg.get("insecure_tls", False))
        self.tmp_png = "/tmp/hermes_screen.png"
        self.running = True
        self.current_screen = "home"
        self.hitboxes: list[dict] = []
        self.refresh_after = 300

        self.ssl_ctx = None
        if self.insecure_tls:
            self.ssl_ctx = ssl._create_unverified_context()

    def _headers(self) -> dict[str, str]:
        hdrs = {"User-Agent": "KindleHermes-PW4/1.0"}
        if self.token:
            hdrs["X-Hermes-Token"] = self.token
        return hdrs

    def _http_json(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self.server_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15, context=self.ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _download_file(self, path: str, dest: str) -> None:
        url = path if path.startswith("http") else (self.server_url + path)
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=20, context=self.ssl_ctx) as resp:
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                f.write(resp.read())
            os.replace(tmp, dest)

    def _fbink_paint(self, full_flash: bool = False, invert: bool = False) -> None:
        cmd = [self.fbink, "-q", "-g", f"file={self.tmp_png},w={KINDLE_W},h={KINDLE_H},halign=center,valign=center"]
        if full_flash:
            cmd.insert(1, "-f")
        if invert:
            cmd.insert(1, "-h")
        subprocess.run(cmd, check=False)

    def _fbink_status(self, message: str) -> None:
        subprocess.run([self.fbink, "-q", "-y", "1", "-m", message], check=False)

    def load_screen(self, screen_id: str, force_refresh: bool = False, full_flash: bool = True) -> None:
        query = "?force_refresh=1" if force_refresh else ""
        meta = self._http_json("GET", f"/api/screen/{screen_id}{query}")
        self.current_screen = meta.get("screen_id", screen_id)
        self.hitboxes = meta.get("hitboxes", [])
        self.refresh_after = max(15, int(meta.get("refresh_after_sec", 300)))
        self._download_file(meta["image_url"], self.tmp_png)
        if meta.get("invert_flash"):
            self._fbink_paint(full_flash=True, invert=True)
            time.sleep(0.35)
        self._fbink_paint(full_flash=full_flash, invert=False)

    def match_hitbox(self, x: int, y: int) -> dict | None:
        matches = [
            b for b in self.hitboxes
            if b["x"] <= x <= (b["x"] + b["w"]) and b["y"] <= y <= (b["y"] + b["h"])
        ]
        if not matches:
            return None
        matches.sort(key=lambda b: b["w"] * b["h"])
        return matches[0]

    def handle_tap(self, x: int, y: int) -> None:
        box = self.match_hitbox(x, y)
        if not box:
            return
        action = box["action"]
        if action == "system:exit":
            self.running = False
            return
        try:
            res = self._http_json(
                "POST",
                "/api/action",
                {"action": action, "current_screen": self.current_screen},
            )
            if res.get("exit"):
                self.running = False
                return
            next_screen = res.get("next_screen", self.current_screen)
            switched = next_screen != self.current_screen
            self.load_screen(
                next_screen,
                force_refresh=bool(res.get("force_refresh", False)),
                full_flash=switched,
            )
        except Exception as exc:
            self._fbink_status(f"OFFLINE: {str(exc)[:40]}")

    def run(self) -> None:
        def _stop(_sig, _frame):
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        dev_path, x_bounds, y_bounds = detect_touch_device(self.touch_override)
        # 32-bit ARM Linux struct input_event = (tv_sec: long, tv_usec: long, type: H, code: H, value: i)
        ev_fmt = "llHHi"
        ev_size = struct.calcsize(ev_fmt)

        try:
            self.load_screen("home", full_flash=True)
        except Exception as exc:
            self._fbink_status(f"HERMES CONNECT ERROR: {str(exc)[:48]}")

        fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
        grabbed = False
        try:
            fcntl.ioctl(fd, EVIOCGRAB, 1)
            grabbed = True
        except OSError:
            pass

        raw_x = 0
        raw_y = 0
        touching = False
        last_refresh = time.monotonic()

        try:
            while self.running:
                elapsed = time.monotonic() - last_refresh
                wait_sec = max(1.0, self.refresh_after - elapsed)
                readable, _, _ = select.select([fd], [], [], wait_sec)

                if not readable:
                    # Periodic e-ink refresh (1 min when Pomodoro is active, 5 min otherwise)
                    try:
                        self.load_screen(self.current_screen, full_flash=False)
                    except Exception:
                        pass
                    last_refresh = time.monotonic()
                    continue

                while True:
                    try:
                        chunk = os.read(fd, ev_size * 32)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    for offset in range(0, len(chunk) - (len(chunk) % ev_size), ev_size):
                        _sec, _usec, ev_type, code, value = struct.unpack_from(ev_fmt, chunk, offset)
                        if ev_type == EV_ABS:
                            if code in (ABS_MT_POSITION_X, ABS_X):
                                raw_x = value
                            elif code in (ABS_MT_POSITION_Y, ABS_Y):
                                raw_y = value
                            elif code == ABS_MT_TRACKING_ID:
                                if value >= 0:
                                    touching = True
                                elif touching:
                                    touching = False
                                    sx, sy = self._scale_point(raw_x, raw_y, x_bounds, y_bounds)
                                    self.handle_tap(sx, sy)
                                    last_refresh = time.monotonic()
                        elif ev_type == EV_KEY and code == BTN_TOUCH:
                            if value == 1:
                                touching = True
                            elif value == 0 and touching:
                                touching = False
                                sx, sy = self._scale_point(raw_x, raw_y, x_bounds, y_bounds)
                                self.handle_tap(sx, sy)
                                last_refresh = time.monotonic()
        finally:
            if grabbed:
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 0)
                except OSError:
                    pass
            os.close(fd)

    def _scale_point(
        self, raw_x: int, raw_y: int, xb: tuple[int, int], yb: tuple[int, int]
    ) -> tuple[int, int]:
        nx = (raw_x - xb[0]) / max(1, (xb[1] - xb[0]))
        ny = (raw_y - yb[0]) / max(1, (yb[1] - yb[0]))
        if self.swap_xy:
            nx, ny = ny, nx
        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny
        sx = max(0, min(KINDLE_W - 1, int(round(nx * KINDLE_W))))
        sy = max(0, min(KINDLE_H - 1, int(round(ny * KINDLE_H))))
        return sx, sy


def main() -> None:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/mnt/us/extensions/kindlehermes/config.json")
    cfg = load_config(cfg_path)
    client = HermesNativeClient(cfg)
    client.run()


if __name__ == "__main__":
    main()

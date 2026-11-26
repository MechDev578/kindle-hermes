// KindleHermes Offline-First Static ARMv7 Native Client for Kindle Paperwhite 10th Gen (1072x1448).
// Features:
// - Instant boot from persistent local cache (/mnt/us/kindlehermes/cache/) — works 100% in Airplane Mode!
// - Silent opportunistic background sync on launch if Wi-Fi is already on + 1-tap manual SYNC button
// - Dual-endpoint automatic failover: Cloud (Railway HTTPS) -> MacBook LAN (or local_first)
// - Full offline navigation across all 8 screens (<50ms local FBInk paints)
// - Offline task checkbox toggling with persistent outbox.json sync queue
// - Local offline Pomodoro countdown timer (Start/Pause/Reset/Modes + 1-minute e-ink updates + completion flash)
// - Time-sliced in-app Smart Lock Screen (automatically picks the right meeting transition offline!)
// - Zero hooks outside Hermes OS: tapping EXIT returns the Kindle to 100% stock normal reading.
//
// Build on macOS/Linux with zero C toolchain needed:
//   GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -ldflags="-s -w" -o hermes-client-arm ./cmd/hermes-client
package main

import (
	"bufio"
	"crypto/tls"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

	_ "golang.org/x/crypto/x509roots/fallback"
)

const (
	kindleW = 1072
	kindleH = 1448

	evKey = 0x01
	evAbs = 0x03

	btnTouch       = 0x14A
	absX           = 0x00
	absY           = 0x01
	absMTPositionX = 0x35
	absMTPositionY = 0x36
	absMTTrackID   = 0x39
)

type Config struct {
	ServerURL      string `json:"server_url"`
	Token          string `json:"token"`
	LocalServerURL string `json:"local_server_url"`
	LocalToken     string `json:"local_token"`
	SyncMode       string `json:"sync_mode"` // "cloud_first" (default) or "local_first"
	CacheDir       string `json:"cache_dir"` // default "/mnt/us/kindlehermes/cache"
	FBInkBin       string `json:"fbink_bin"`
	TouchDevice    string `json:"touch_device"`
	SwapXY         bool   `json:"swap_xy"`
	InvertX        bool   `json:"invert_x"`
	InvertY        bool   `json:"invert_y"`
	InsecureTLS    bool   `json:"insecure_tls"`
}

type inputAbsInfo struct {
	Value, Minimum, Maximum, Fuzz, Flat, Resolution int32
}

type inputEvent32 struct {
	Sec, Usec int32
	Type      uint16
	Code      uint16
	Value     int32
}

func eviocgabs(axis uintptr) uintptr {
	return (2 << 30) | (24 << 16) | (uintptr('E') << 8) | (0x40 + axis)
}

func getAbsBounds(fd uintptr, axis uintptr) (int32, int32, bool) {
	var info inputAbsInfo
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, eviocgabs(axis), uintptr(unsafe.Pointer(&info)))
	if errno == 0 && info.Maximum > info.Minimum {
		return info.Minimum, info.Maximum, true
	}
	return 0, 0, false
}

func detectTouch(preferred string) (string, [2]int32, [2]int32, error) {
	var paths []string
	if preferred != "" {
		paths = []string{preferred}
	} else {
		paths, _ = filepath.Glob("/dev/input/event*")
		sort.Strings(paths)
	}
	for _, p := range paths {
		f, err := os.Open(p)
		if err != nil {
			continue
		}
		minX, maxX, okX := getAbsBounds(f.Fd(), absMTPositionX)
		minY, maxY, okY := getAbsBounds(f.Fd(), absMTPositionY)
		if okX && okY {
			f.Close()
			return p, [2]int32{minX, maxX}, [2]int32{minY, maxY}, nil
		}
		minX, maxX, okX = getAbsBounds(f.Fd(), absX)
		minY, maxY, okY = getAbsBounds(f.Fd(), absY)
		f.Close()
		if okX && okY {
			return p, [2]int32{minX, maxX}, [2]int32{minY, maxY}, nil
		}
	}
	return "", [2]int32{}, [2]int32{}, fmt.Errorf("no touchscreen found")
}

func main() {
	cfgPath := "/mnt/us/kindlehermes/config.json"
	if len(os.Args) > 1 {
		cfgPath = os.Args[1]
	}
	raw, err := os.ReadFile(cfgPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "config error: %v\n", err)
		os.Exit(1)
	}
	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		fmt.Fprintf(os.Stderr, "invalid config: %v\n", err)
		os.Exit(1)
	}
	if envFB := os.Getenv("HERMES_FBINK"); envFB != "" {
		cfg.FBInkBin = envFB
	} else if cfg.FBInkBin == "" {
		cfg.FBInkBin = "fbink"
	}
	if cfg.CacheDir == "" {
		cfg.CacheDir = "/mnt/us/kindlehermes/cache"
	}

	endpoints := resolveEndpoints(cfg)

	transport := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: cfg.InsecureTLS},
	}
	syncClient := &http.Client{Timeout: 25 * time.Second, Transport: transport}
	bgClient := &http.Client{Timeout: 10 * time.Second, Transport: transport}

	const tmpScreenPNG = "/tmp/hermes_screen.png"
	const fallbackLockPNG = "/mnt/us/hermes_screensavers/hermes_01.png"

	store := NewOfflineStore(cfg.CacheDir, fallbackLockPNG)

	var (
		mu            sync.Mutex
		currentScreen = "home"
		hitboxes      []Hitbox
		isLocked      = false
		pomoDirty     = false
		syncInFlight  = false
		bannerMsg     = ""
		bannerUntil   time.Time
	)

	var paintMu sync.Mutex
	paintFile := func(pngPath string, fullFlash, invert bool) {
		paintMu.Lock()
		defer paintMu.Unlock()
		args := []string{"-q"}
		if fullFlash {
			args = append(args, "-f", "-W", "GC16")
		}
		if invert {
			args = append(args, "-h")
		}
		args = append(args, "-g", fmt.Sprintf("file=%s,w=%d,h=%d,halign=center,valign=center", pngPath, kindleW, kindleH))
		_ = exec.Command(cfg.FBInkBin, args...).Run()
	}

	renderCurrent := func(screenID string, fullFlash, invertFlash bool) bool {
		scr, basePath, ok := store.GetScreen(screenID)
		if !ok {
			return false
		}
		pomoNow, _ := store.ComputePomoNow(time.Now().Unix())
		tasks := store.GetTaskStatesCopy()

		mu.Lock()
		currentScreen = screenID
		hitboxes = scr.Hitboxes
		lockedNow := isLocked
		pDirty := pomoDirty || pomoNow.Status == "running" || pomoNow.Status == "paused" || pomoNow.Status == "completed"
		activeBanner := ""
		if bannerMsg != "" && (bannerUntil.IsZero() || time.Now().Before(bannerUntil)) {
			activeBanner = bannerMsg
		} else {
			bannerMsg = ""
		}
		mu.Unlock()

		if err := ApplyOfflineOverlays(basePath, tmpScreenPNG, screenID, scr, tasks, pomoNow, pDirty, activeBanner); err != nil {
			fmt.Fprintf(os.Stderr, "[render] overlay error: %v\n", err)
			return false
		}
		if !lockedNow {
			if invertFlash {
				paintFile(tmpScreenPNG, true, true)
				time.Sleep(350 * time.Millisecond)
			}
			paintFile(tmpScreenPNG, fullFlash, false)
		}
		return true
	}

	setBannerAndRepaint := func(msg string, duration time.Duration, fullFlash bool) {
		mu.Lock()
		bannerMsg = msg
		if duration > 0 {
			bannerUntil = time.Now().Add(duration)
		} else {
			bannerUntil = time.Time{}
		}
		scr := currentScreen
		mu.Unlock()
		renderCurrent(scr, fullFlash, false)
		if duration > 0 {
			go func(expected string) {
				time.Sleep(duration + 120*time.Millisecond)
				mu.Lock()
				stillSame := (bannerMsg == expected)
				if stillSame {
					bannerMsg = ""
					bannerUntil = time.Time{}
				}
				cur := currentScreen
				locked := isLocked
				mu.Unlock()
				if stillSame && !locked {
					renderCurrent(cur, false, false)
				}
			}(msg)
		}
	}

	runSync := func(manual bool) bool {
		mu.Lock()
		if syncInFlight {
			mu.Unlock()
			return false
		}
		syncInFlight = true
		mu.Unlock()
		defer func() {
			mu.Lock()
			syncInFlight = false
			mu.Unlock()
		}()

		client := bgClient
		if manual {
			client = syncClient
			setBannerAndRepaint("SYNCING...", 0, false)
		}

		srcName, err := store.SyncNow(client, endpoints, manual)
		if err != nil {
			fmt.Fprintf(os.Stderr, "[sync] skipped/failed (manual=%v): %v\n", manual, err)
			if manual {
				pending := store.OutboxCount()
				if pending > 0 {
					setBannerAndRepaint(fmt.Sprintf("NO WI-FI · %d PENDING SYNC", pending), 2600*time.Millisecond, false)
				} else {
					setBannerAndRepaint("NO WI-FI · USING CACHED DATA", 2400*time.Millisecond, false)
				}
			}
			return false
		}

		fmt.Fprintf(os.Stderr, "[sync] updated offline cache via %s\n", srcName)
		mu.Lock()
		if curPomo, _ := store.ComputePomoNow(time.Now().Unix()); curPomo.Status != "running" {
			pomoDirty = false
		}
		cur := currentScreen
		locked := isLocked
		mu.Unlock()

		if manual {
			label := strings.ToUpper(srcName)
			syncedAt := store.LastSyncedLabel()
			if syncedAt != "" {
				setBannerAndRepaint(fmt.Sprintf("SYNCED VIA %s · %s", label, syncedAt), 2200*time.Millisecond, true)
			} else {
				setBannerAndRepaint(fmt.Sprintf("SYNCED VIA %s", label), 2200*time.Millisecond, true)
			}
		} else if !locked {
			// Silent background sync finished while user is viewing app -> gently repaint with fresh data
			renderCurrent(cur, false, false)
		}
		return true
	}

	devPath, xb, yb, err := detectTouch(cfg.TouchDevice)
	if err != nil {
		_ = exec.Command(cfg.FBInkBin, "-c", "-f", "-m", "-y", "6", "Hermes OS: Touch device error: "+err.Error()).Run()
		time.Sleep(4 * time.Second)
		os.Exit(1)
	}

	// Instant Offline-First Startup:
	// 1. If local cache exists -> paint Home immediately (works in Airplane Mode!) + silent background sync if Wi-Fi is up.
	// 2. If cache is empty (very first boot) -> sync once immediately.
	if store.HasCache() {
		renderCurrent("home", true, false)
		go runSync(false)
	} else {
		_ = exec.Command(cfg.FBInkBin, "-c", "-f", "-m", "-y", "6", "KindleHermes OS: Downloading offline cache...").Run()
		if _, err := store.SyncNow(syncClient, endpoints, true); err != nil {
			msg := fmt.Sprintf("Hermes OS: No offline cache yet. Turn on Wi-Fi (or run usb_sync.sh) once. (%v)", err)
			fmt.Fprintln(os.Stderr, msg)
			_ = exec.Command(cfg.FBInkBin, "-c", "-f", "-m", "-y", "6", msg).Run()
			time.Sleep(5 * time.Second)
			os.Exit(1)
		}
		renderCurrent("home", true, false)
	}

	// Listen to Kindle powerd events ONLY while inside Hermes OS.
	// On EXIT, lipc-wait-event is terminated so normal Kindle sleep/lock works 100% stock!
	powerCh := make(chan string, 16)
	lipcCmd := exec.Command(
		"lipc-wait-event", "-m", "com.lab126.powerd",
		"goingToScreenSaver,outOfScreenSaver,readyToSuspend,wakeupFromSuspend",
	)
	if stdout, err := lipcCmd.StdoutPipe(); err == nil {
		if err := lipcCmd.Start(); err == nil {
			defer func() {
				if lipcCmd.Process != nil {
					_ = lipcCmd.Process.Kill()
				}
				_ = lipcCmd.Wait()
			}()
			go func() {
				scanner := bufio.NewScanner(stdout)
				for scanner.Scan() {
					line := strings.TrimSpace(scanner.Text())
					if line != "" {
						fmt.Fprintf(os.Stderr, "[powerd] %s\n", line)
						select {
						case powerCh <- line:
						default:
						}
					}
				}
			}()
		}
	}

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	tapCh := make(chan [2]int, 8)
	touchFile, err := os.Open(devPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "open touch error: %v\n", err)
		os.Exit(1)
	}
	const eviocgrab uintptr = 0x40044590
	_, _, _ = syscall.Syscall(syscall.SYS_IOCTL, touchFile.Fd(), eviocgrab, 1)
	defer func() {
		_, _, _ = syscall.Syscall(syscall.SYS_IOCTL, touchFile.Fd(), eviocgrab, 0)
		touchFile.Close()
	}()

	go func() {
		var ev inputEvent32
		var rx, ry int32
		touching := false
		for {
			if err := binary.Read(touchFile, binary.LittleEndian, &ev); err != nil {
				return
			}
			emit := false
			if ev.Type == evAbs {
				switch ev.Code {
				case absMTPositionX, absX:
					rx = ev.Value
				case absMTPositionY, absY:
					ry = ev.Value
				case absMTTrackID:
					if ev.Value >= 0 {
						touching = true
					} else if touching {
						touching = false
						emit = true
					}
				}
			} else if ev.Type == evKey && ev.Code == btnTouch {
				if ev.Value == 1 {
					touching = true
				} else if ev.Value == 0 && touching {
					touching = false
					emit = true
				}
			}
			if emit {
				nx := float64(rx-xb[0]) / float64(max32(1, xb[1]-xb[0]))
				ny := float64(ry-yb[0]) / float64(max32(1, yb[1]-yb[0]))
				if cfg.SwapXY {
					nx, ny = ny, nx
				}
				if cfg.InvertX {
					nx = 1.0 - nx
				}
				if cfg.InvertY {
					ny = 1.0 - ny
				}
				tapCh <- [2]int{clampInt(int(nx*kindleW), 0, kindleW-1), clampInt(int(ny*kindleH), 0, kindleH-1)}
			}
		}
	}()

	// Local 1-minute ticker for offline Pomodoro countdown (zero network usage!)
	pomoTicker := time.NewTicker(60 * time.Second)
	defer pomoTicker.Stop()

	for {
		select {
		case <-sigCh:
			return

		case evLine := <-powerCh:
			switch {
			case strings.Contains(evLine, "goingToScreenSaver"):
				mu.Lock()
				isLocked = true
				mu.Unlock()
				// Pick the exact time-sliced Smart Lock Screen for right now (works 100% offline!)
				pngPath := store.PickLockScreenPNG(time.Now().Unix(), true)
				// Wait briefly so Amazon's default screensaver finishes its draw, then overwrite cleanly
				time.Sleep(650 * time.Millisecond)
				if pngPath != "" {
					paintFile(pngPath, true, false)
				}

			case strings.Contains(evLine, "readyToSuspend"):
				mu.Lock()
				locked := isLocked
				mu.Unlock()
				if locked {
					if pngPath := store.PickLockScreenPNG(time.Now().Unix(), false); pngPath != "" {
						paintFile(pngPath, true, false)
					}
				}

			case strings.Contains(evLine, "outOfScreenSaver"):
				mu.Lock()
				isLocked = false
				scr := currentScreen
				mu.Unlock()
				// Instant resume from local cache (zero Wi-Fi required)
				time.Sleep(450 * time.Millisecond)
				renderCurrent(scr, true, false)
				// Opportunistically refresh in background only if Wi-Fi reconnects
				go func() {
					time.Sleep(2500 * time.Millisecond)
					mu.Lock()
					stillUnlocked := !isLocked
					mu.Unlock()
					if stillUnlocked {
						runSync(false)
					}
				}()
			}

		case <-pomoTicker.C:
			pNow, completed := store.ComputePomoNow(time.Now().Unix())
			mu.Lock()
			locked := isLocked
			scr := currentScreen
			mu.Unlock()
			if !locked && (pNow.Status == "running" || completed) {
				if scr == "pomodoro" || completed {
					mu.Lock()
					pomoDirty = true
					mu.Unlock()
					renderCurrent("pomodoro", completed, completed)
				}
			}

		case pt := <-tapCh:
			mu.Lock()
			locked := isLocked
			scr := currentScreen
			boxes := hitboxes
			mu.Unlock()
			if locked {
				continue
			}

			var best *Hitbox
			bestArea := 1 << 30
			for i := range boxes {
				b := &boxes[i]
				if pt[0] >= b.X && pt[0] <= b.X+b.W && pt[1] >= b.Y && pt[1] <= b.Y+b.H {
					area := b.W * b.H
					if area < bestArea {
						best = b
						bestArea = area
					}
				}
			}
			if best == nil {
				continue
			}

			act := best.Action
			switch {
			case act == "system:exit":
				return

			case strings.HasPrefix(act, "nav:"):
				target := strings.TrimPrefix(act, "nav:")
				renderCurrent(target, target != scr, false)

			case act == "sync:all":
				go runSync(true)

			case act == "briefing:refresh":
				store.QueueAction("briefing:refresh", scr)
				go runSync(true)

			case strings.HasPrefix(act, "task:toggle:"):
				idStr := strings.TrimPrefix(act, "task:toggle:")
				if id, err := strconv.Atoi(idStr); err == nil {
					store.ToggleTaskOffline(id, scr)
					renderCurrent(scr, false, false)
				}

			case act == "task:clear_done":
				store.ClearDoneOffline(scr)
				setBannerAndRepaint("QUEUED CLEAR · TAP SYNC ON WI-FI", 2200*time.Millisecond, false)

			case act == "pomo:toggle":
				store.PomoToggle(time.Now().Unix())
				mu.Lock()
				pomoDirty = true
				mu.Unlock()
				renderCurrent("pomodoro", false, false)

			case act == "pomo:reset":
				store.PomoReset()
				mu.Lock()
				pomoDirty = true
				mu.Unlock()
				renderCurrent("pomodoro", false, false)

			case strings.HasPrefix(act, "pomo:mode:"):
				mode := strings.TrimPrefix(act, "pomo:mode:")
				store.PomoSetMode(mode)
				mu.Lock()
				pomoDirty = true
				mu.Unlock()
				renderCurrent("pomodoro", false, false)
			}
		}
	}
}

func max32(a, b int32) int32 {
	if a > b {
		return a
	}
	return b
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

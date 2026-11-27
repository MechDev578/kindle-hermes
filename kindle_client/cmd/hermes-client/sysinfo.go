package main

import (
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// DeviceStatus holds live hardware telemetry read directly from the Kindle (100% offline).
type DeviceStatus struct {
	Now         time.Time
	TimeStr     string // "14:54"
	DateStr     string // "FRI 27 NOV"
	LastWakeStr string // "14:50" (populated on launch and outOfScreenSaver)
	Battery     int    // 0..100, or -1 if unavailable
	Charging    bool
}

var (
	sysInfoMu         sync.Mutex
	cachedBatDir      string
	batDirSearched    bool
	cachedSysLocalLoc *time.Location
)

// ParseTZOffsetString parses a numeric timezone offset like "+0100", "+0200", "-0530" into seconds.
func ParseTZOffsetString(s string) (int, bool) {
	s = strings.TrimSpace(s)
	if len(s) != 5 || (s[0] != '+' && s[0] != '-') {
		return 0, false
	}
	hh, err1 := strconv.Atoi(s[1:3])
	mm, err2 := strconv.Atoi(s[3:5])
	if err1 != nil || err2 != nil || hh > 14 || mm >= 60 {
		return 0, false
	}
	sec := hh*3600 + mm*60
	if s[0] == '-' {
		sec = -sec
	}
	return sec, true
}

// DetectKindleLocation resolves the Kindle's own local timezone (preferring Go's /etc/localtime,
// falling back to `date +%z` on quirky Kindle TZ files, and finally manifest tz_offset_sec).
func DetectKindleLocation(fallbackOffsetSec int, forceRefresh bool) *time.Location {
	sysInfoMu.Lock()
	if !forceRefresh && cachedSysLocalLoc != nil {
		loc := cachedSysLocalLoc
		sysInfoMu.Unlock()
		return loc
	}
	sysInfoMu.Unlock()

	now := time.Now()
	name, off := now.Zone()
	loc := time.Local
	// If Go resolved a real local timezone (non-zero offset or explicit non-UTC zone name), trust it directly.
	if off == 0 && (name == "" || name == "UTC" || name == "GMT") {
		if out, err := exec.Command("date", "+%z").Output(); err == nil {
			if sec, ok := ParseTZOffsetString(string(out)); ok && sec != 0 {
				loc = time.FixedZone("LOCAL", sec)
			}
		}
		if loc == time.Local && fallbackOffsetSec != 0 {
			loc = time.FixedZone("LOCAL", fallbackOffsetSec)
		}
	}

	sysInfoMu.Lock()
	cachedSysLocalLoc = loc
	sysInfoMu.Unlock()
	return loc
}

// FormatDeviceStatus formats a time.Time + battery into crisp uppercase e-ink labels.
func FormatDeviceStatus(now time.Time, battery int, charging bool) DeviceStatus {
	day := strings.ToUpper(now.Format("Mon"))
	mon := strings.ToUpper(now.Format("Jan"))
	return DeviceStatus{
		Now:      now,
		TimeStr:  now.Format("15:04"),
		DateStr:  day + " " + strconv.Itoa(now.Day()) + " " + mon,
		Battery:  battery,
		Charging: charging,
	}
}

func readBatteryFromSysfsDir(dir string) (int, bool, bool) {
	rawCap, err := os.ReadFile(filepath.Join(dir, "capacity"))
	if err != nil {
		return -1, false, false
	}
	pct, err := strconv.Atoi(strings.TrimSpace(string(rawCap)))
	if err != nil || pct < 0 || pct > 100 {
		return -1, false, false
	}
	charging := false
	if rawSt, err := os.ReadFile(filepath.Join(dir, "status")); err == nil {
		st := strings.ToLower(strings.TrimSpace(string(rawSt)))
		charging = (st == "charging")
	}
	return pct, charging, true
}

// ReadBatteryStatus reads battery percentage (0..100) and charging flag directly from Kindle hardware.
// Prefers zero-overhead Linux sysfs (/sys/class/power_supply/*/capacity), falling back to lipc/gasgauge-info.
func ReadBatteryStatus() (int, bool) {
	sysInfoMu.Lock()
	dir := cachedBatDir
	searched := batDirSearched
	sysInfoMu.Unlock()

	if dir != "" {
		if pct, chg, ok := readBatteryFromSysfsDir(dir); ok {
			return pct, chg
		}
	}

	if !searched {
		matches, _ := filepath.Glob("/sys/class/power_supply/*/capacity")
		foundDir := ""
		for _, capPath := range matches {
			candDir := filepath.Dir(capPath)
			if pct, chg, ok := readBatteryFromSysfsDir(candDir); ok {
				foundDir = candDir
				sysInfoMu.Lock()
				cachedBatDir = foundDir
				batDirSearched = true
				sysInfoMu.Unlock()
				return pct, chg
			}
		}
		sysInfoMu.Lock()
		batDirSearched = true
		sysInfoMu.Unlock()
	}

	// Fallback 1: Kindle powerd LIPC property
	if out, err := exec.Command("lipc-get-prop", "com.lab126.powerd", "battLevel").Output(); err == nil {
		if pct, err2 := strconv.Atoi(strings.TrimSpace(string(out))); err2 == nil && pct >= 0 && pct <= 100 {
			charging := false
			if chgOut, err3 := exec.Command("lipc-get-prop", "com.lab126.powerd", "isCharging").Output(); err3 == nil {
				charging = strings.TrimSpace(string(chgOut)) == "1"
			}
			return pct, charging
		}
	}

	// Fallback 2: Kindle built-in gasgauge-info utility (outputs e.g. "84%")
	if out, err := exec.Command("gasgauge-info", "-s").Output(); err == nil {
		s := strings.TrimSuffix(strings.TrimSpace(string(out)), "%")
		if pct, err2 := strconv.Atoi(strings.TrimSpace(s)); err2 == nil && pct >= 0 && pct <= 100 {
			return pct, false
		}
	}

	return -1, false
}

// ReadDeviceStatus snapshots live Kindle wall clock + battery telemetry in one call.
func ReadDeviceStatus(loc *time.Location) DeviceStatus {
	now := time.Now()
	if loc != nil {
		now = now.In(loc)
	}
	pct, chg := ReadBatteryStatus()
	return FormatDeviceStatus(now, pct, chg)
}

// NextMinuteDelay returns the duration until the next :00 minute boundary (with a 150ms margin).
func NextMinuteDelay(now time.Time) time.Duration {
	rem := time.Duration(60-now.Second())*time.Second - time.Duration(now.Nanosecond()) + 150*time.Millisecond
	if rem < 200*time.Millisecond {
		rem += 60 * time.Second
	}
	return rem
}

// IsWiFiConnected checks whether the Kindle currently has an active Wi-Fi connection
// WITHOUT waking Airplane Mode or turning Wi-Fi on.
func IsWiFiConnected() bool {
	ifaces, err := net.Interfaces()
	if err != nil {
		return false
	}
	hasWlanIface := false
	for _, iface := range ifaces {
		name := strings.ToLower(iface.Name)
		if strings.HasPrefix(name, "wlan") || strings.HasPrefix(name, "mlan") || strings.HasPrefix(name, "wifi") {
			hasWlanIface = true
			if ifaceHasUsableIPv4(iface) {
				return true
			}
		}
	}
	// On Kindle (which has wlan0), if wlan0 is down or lacks an IPv4 address, Wi-Fi is OFF / Airplane Mode.
	if hasWlanIface {
		return false
	}
	// Non-Kindle host fallback (e.g. macOS/Linux dev tests): check any non-loopback/non-USB active interface.
	for _, iface := range ifaces {
		name := strings.ToLower(iface.Name)
		if strings.HasPrefix(name, "lo") || strings.HasPrefix(name, "usb") || strings.HasPrefix(name, "utun") || strings.HasPrefix(name, "docker") {
			continue
		}
		if ifaceHasUsableIPv4(iface) {
			return true
		}
	}
	return false
}

func ifaceHasUsableIPv4(iface net.Interface) bool {
	if (iface.Flags&net.FlagUp) == 0 || (iface.Flags&net.FlagLoopback) != 0 {
		return false
	}
	addrs, err := iface.Addrs()
	if err != nil {
		return false
	}
	for _, addr := range addrs {
		var ip net.IP
		switch v := addr.(type) {
		case *net.IPNet:
			ip = v.IP
		case *net.IPAddr:
			ip = v.IP
		}
		if ip == nil {
			continue
		}
		ip4 := ip.To4()
		if ip4 != nil && !ip4.IsLoopback() && !ip4.IsLinkLocalUnicast() {
			return true
		}
	}
	return false
}

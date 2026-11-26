package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"image"
	"image/png"
	"io"
	"net/http"
	"path/filepath"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func makeDummyGrayPNG(t *testing.T, fill uint8) []byte {
	t.Helper()
	img := image.NewGray(image.Rect(0, 0, kindleW, kindleH))
	for i := range img.Pix {
		img.Pix[i] = fill
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("encode dummy png: %v", err)
	}
	return buf.Bytes()
}

func makeTestBundleZIP(t *testing.T) []byte {
	t.Helper()
	whitePNG := makeDummyGrayPNG(t, 255)
	manifest := BundleManifest{
		Version:           1,
		SyncedAtUnix:      1764140000,
		SyncedAtLabel:     "08:30",
		Timezone:          "Europe/Berlin",
		LockscreenMode:    "smart",
		LockscreenDefault: "lockscreens/default.png",
		LockscreenSlots: []LockscreenSlot{
			{ID: "slot_0", File: "lockscreens/slot_0.png", StartUnix: 1764140000, EndUnix: 1764143600, Label: "08:30-09:30"},
			{ID: "slot_1", File: "lockscreens/slot_1.png", StartUnix: 1764143600, EndUnix: 1764147200, Label: "09:30-10:30"},
		},
		Screens: map[string]CachedScreen{
			"home": {
				File: "screens/home.png",
				Hitboxes: []Hitbox{
					{Action: "nav:planner", X: 28, Y: 300, W: 496, H: 294},
				},
			},
			"planner": {
				File: "screens/planner.png",
				Hitboxes: []Hitbox{
					{
						Action:       "task:toggle:1",
						X:            50,
						Y:            300,
						W:            900,
						H:            82,
						CheckboxRect: &Rect{X: 66, Y: 321, W: 40, H: 40},
						TextRect:     &Rect{X: 122, Y: 325, W: 700, H: 32},
					},
				},
			},
			"pomodoro": {
				File: "screens/pomodoro.png",
				Layout: ScreenLayout{
					Pomo: &PomoLayout{
						Stage:     &Rect{X: 28, Y: 122, W: 1016, H: 480},
						Badge:     &Rect{X: 350, Y: 166, W: 372, H: 44},
						Digits:    &Rect{X: 200, Y: 230, W: 672, H: 180},
						Status:    &Rect{X: 80, Y: 430, W: 912, H: 36},
						SegCells:  []Rect{{X: 64, Y: 500, W: 80, H: 36}, {X: 150, Y: 500, W: 80, H: 36}},
						ToggleBtn: &Rect{X: 28, Y: 630, W: 496, H: 92},
						ModeFocus: &Rect{X: 52, Y: 820, W: 300, H: 84},
						ModeShort: &Rect{X: 368, Y: 820, W: 300, H: 84},
						ModeLong:  &Rect{X: 684, Y: 820, W: 300, H: 84},
					},
				},
			},
		},
		Tasks: []TaskItem{
			{ID: 1, Title: "Review specs", Completed: false, Priority: 1},
		},
		Pomodoro: PomoState{
			Mode:         "focus",
			Status:       "idle",
			ModeLabel:    "25m Focus",
			DurationSec:  1500,
			RemainingSec: 1500,
		},
	}
	mRaw, _ := json.Marshal(manifest)

	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	add := func(name string, data []byte) {
		w, err := zw.Create(name)
		if err != nil {
			t.Fatalf("zip create %s: %v", name, err)
		}
		_, _ = w.Write(data)
	}
	add("manifest.json", mRaw)
	add("screens/home.png", whitePNG)
	add("screens/planner.png", whitePNG)
	add("screens/pomodoro.png", whitePNG)
	add("lockscreens/default.png", whitePNG)
	add("lockscreens/slot_0.png", whitePNG)
	add("lockscreens/slot_1.png", whitePNG)
	_ = zw.Close()
	return buf.Bytes()
}

func TestOfflineStoreBundleTimeSlotsOutboxAndFailover(t *testing.T) {
	dir := t.TempDir()
	store := NewOfflineStore(dir, filepath.Join(dir, "fallback.png"))

	bundleZip := makeTestBundleZIP(t)
	if _, err := store.ExtractBundleZip(bundleZip); err != nil {
		t.Fatalf("ExtractBundleZip failed: %v", err)
	}
	if !store.HasCache() {
		t.Fatalf("expected HasCache() == true")
	}

	// Verify time-sliced lock screen selection
	slot0 := store.PickLockScreenPNG(1764141000, false)
	if filepath.Base(slot0) != "slot_0.png" {
		t.Fatalf("expected slot_0.png, got %s", slot0)
	}
	slot1 := store.PickLockScreenPNG(1764145000, false)
	if filepath.Base(slot1) != "slot_1.png" {
		t.Fatalf("expected slot_1.png, got %s", slot1)
	}
	late := store.PickLockScreenPNG(1764160000, false)
	if filepath.Base(late) != "slot_1.png" {
		t.Fatalf("expected final slot_1.png late in day, got %s", late)
	}

	// Verify offline task toggle deduplicates outbox entries cleanly
	if done := store.ToggleTaskOffline(1, "planner"); !done {
		t.Fatalf("expected first toggle -> true")
	}
	if done := store.ToggleTaskOffline(1, "planner"); done {
		t.Fatalf("expected second toggle -> false")
	}
	if done := store.ToggleTaskOffline(1, "planner"); !done {
		t.Fatalf("expected third toggle -> true")
	}
	if store.OutboxCount() != 1 {
		t.Fatalf("expected deduplicated outbox count 1, got %d", store.OutboxCount())
	}

	// Verify offline checkbox + Pomodoro overlay rendering produces valid 1072x1448 PNG
	scr, basePath, ok := store.GetScreen("planner")
	if !ok {
		t.Fatalf("planner screen missing")
	}
	outPlanner := filepath.Join(dir, "rendered_planner.png")
	if err := ApplyOfflineOverlays(basePath, outPlanner, "planner", scr, store.GetTaskStatesCopy(), store.pomo, false, "OFFLINE · 1 PENDING"); err != nil {
		t.Fatalf("ApplyOfflineOverlays planner failed: %v", err)
	}
	img, err := loadGrayPNG(outPlanner)
	if err != nil || img.Bounds().Dx() != kindleW || img.Bounds().Dy() != kindleH {
		t.Fatalf("invalid rendered planner png: %v", err)
	}

	// Verify local Pomodoro countdown & overlay
	pomoStart := store.PomoToggle(1000)
	if pomoStart.Status != "running" {
		t.Fatalf("expected running pomodoro, got %s", pomoStart.Status)
	}
	pomoMid, completed := store.ComputePomoNow(1000 + 600)
	if completed || pomoMid.RemainingSec != 900 {
		t.Fatalf("expected 900s remaining, got %d (completed=%v)", pomoMid.RemainingSec, completed)
	}
	pomoScr, pomoBase, _ := store.GetScreen("pomodoro")
	outPomo := filepath.Join(dir, "rendered_pomo.png")
	if err := ApplyOfflineOverlays(pomoBase, outPomo, "pomodoro", pomoScr, nil, pomoMid, true, ""); err != nil {
		t.Fatalf("ApplyOfflineOverlays pomo failed: %v", err)
	}

	// Verify cloud -> macbook automatic failover & outbox flush using in-memory RoundTripper
	var batchReceived int
	mockClient := &http.Client{
		Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
			if r.URL.Host == "cloud.test" {
				return &http.Response{
					StatusCode: 503,
					Body:       io.NopCloser(bytes.NewReader(nil)),
					Header:     make(http.Header),
				}, nil
			}
			switch r.URL.Path {
			case "/api/sync/batch":
				var body struct {
					Actions []OutboxAction `json:"actions"`
				}
				_ = json.NewDecoder(r.Body).Decode(&body)
				batchReceived = len(body.Actions)
				return &http.Response{
					StatusCode: 200,
					Body:       io.NopCloser(bytes.NewReader([]byte(`{"ok":true}`))),
					Header:     make(http.Header),
				}, nil
			case "/api/offline-bundle":
				hdr := make(http.Header)
				hdr.Set("Content-Type", "application/zip")
				return &http.Response{
					StatusCode: 200,
					Body:       io.NopCloser(bytes.NewReader(bundleZip)),
					Header:     hdr,
				}, nil
			default:
				return &http.Response{
					StatusCode: 404,
					Body:       io.NopCloser(bytes.NewReader(nil)),
					Header:     make(http.Header),
				}, nil
			}
		}),
	}

	endpoints := []SyncEndpoint{
		{Name: "cloud", URL: "https://cloud.test", Token: "pin"},
		{Name: "macbook", URL: "http://macbook.local:8000", Token: ""},
	}
	src, err := store.SyncNow(mockClient, endpoints, true)
	if err != nil {
		t.Fatalf("SyncNow failover failed: %v", err)
	}
	if src != "macbook" {
		t.Fatalf("expected failover to macbook, got %s", src)
	}
	if batchReceived != 1 {
		t.Fatalf("expected 1 outbox action flushed, got %d", batchReceived)
	}
	if store.OutboxCount() != 0 {
		t.Fatalf("expected empty outbox after flush, got %d", store.OutboxCount())
	}
}

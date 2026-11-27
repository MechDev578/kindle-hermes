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
	"sync"
	"sync/atomic"
	"testing"
	"time"
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
	sampleStatus := FormatDeviceStatus(time.Date(2026, 11, 27, 14, 54, 0, 0, time.UTC), 84, false)
	if err := ApplyOfflineOverlays(basePath, outPlanner, "planner", scr, store.GetTaskStatesCopy(), store.pomo, false, "OFFLINE · 1 PENDING", sampleStatus); err != nil {
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
	if err := ApplyOfflineOverlays(pomoBase, outPomo, "pomodoro", pomoScr, nil, pomoMid, true, "", sampleStatus); err != nil {
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

func TestOfflineDeutschDeckClassicsRootsAndQuiz(t *testing.T) {
	dir := t.TempDir()
	store := NewOfflineStore(dir, filepath.Join(dir, "fallback.png"))
	whitePNG := makeDummyGrayPNG(t, 255)

	manifest := BundleManifest{
		Version:           2,
		SyncedAtUnix:      1764140000,
		SyncedAtLabel:     "18:30",
		LockscreenMode:    "smart",
		LockscreenDefault: "lockscreens/default.png",
		Screens: map[string]CachedScreen{
			"home":         {File: "screens/home.png"},
			"deutsch":      {File: "screens/deutsch.png"},
			"deutsch_read": {File: "screens/deutsch_read.png"},
		},
		Deutsch: &DeutschBundle{
			CEFRLabel:    "A2.2",
			Sprachindex:  220,
			ClassicTotal: 15,
			Deck: []DeutschCard{
				{
					ID:            10,
					Kind:          "gender",
					CEFR:          "A2",
					AnswerDE:      "die",
					FullDE:        "die Entscheidung",
					FrontFile:     "deutsch/card_10_front.png",
					BackFile:      "deutsch/card_10_back.png",
					FrontHitboxes: []Hitbox{{Action: "deutsch:guess:die", X: 360, Y: 1100, W: 300, H: 110}},
					BackHitboxes:  []Hitbox{{Action: "deutsch:rate:10:3", X: 520, Y: 1100, W: 240, H: 108}},
				},
			},
			DeckDoneFile:     "deutsch/deck_done.png",
			DeckDoneHitboxes: []Hitbox{{Action: "nav:deutsch", X: 40, Y: 800, W: 450, H: 92}},
			Classics: []DeutschClassic{
				{ID: 1, Idx: 0, Author: "Willy Brandt", Title: "Mehr Demokratie wagen", GlossFile: "deutsch/c1_g.png", ParallelFile: "deutsch/c1_p.png", XrayFile: "deutsch/c1_x.png"},
				{ID: 2, Idx: 1, Author: "Loriot", Title: "Lametta", GlossFile: "deutsch/c2_g.png", ParallelFile: "deutsch/c2_p.png", XrayFile: "deutsch/c2_x.png"},
			},
			Roots: []DeutschRoot{
				{Idx: 0, Root: "STEHEN", File: "deutsch/r0.png"},
				{Idx: 1, Root: "KOMMEN", File: "deutsch/r1.png"},
			},
			ReaderQuizAnswerIdx: 0,
		},
	}
	mRaw, _ := json.Marshal(manifest)
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	add := func(name string, data []byte) {
		w, _ := zw.Create(name)
		_, _ = w.Write(data)
	}
	add("manifest.json", mRaw)
	for _, f := range []string{
		"screens/home.png", "screens/deutsch.png", "screens/deutsch_read.png",
		"lockscreens/default.png",
		"deutsch/card_10_front.png", "deutsch/card_10_back.png", "deutsch/deck_done.png",
		"deutsch/c1_g.png", "deutsch/c1_p.png", "deutsch/c1_x.png",
		"deutsch/c2_g.png", "deutsch/c2_p.png", "deutsch/c2_x.png",
		"deutsch/r0.png", "deutsch/r1.png",
	} {
		add(f, whitePNG)
	}
	_ = zw.Close()

	if _, err := store.ExtractBundleZip(buf.Bytes()); err != nil {
		t.Fatalf("ExtractBundleZip deutsch failed: %v", err)
	}

	// Front -> Guess 'die' -> Back -> Rate '3' -> Deck Done
	_, pFront, ok := store.GetScreen("deutsch_drill")
	if !ok || filepath.Base(pFront) != "card_10_front.png" {
		t.Fatalf("expected card_10_front.png, got %s", pFront)
	}
	right, ans := store.DeutschGuess("die")
	if !right || ans != "die" {
		t.Fatalf("expected right=true ans=die, got %v %q", right, ans)
	}
	_, pBack, ok := store.GetScreen("deutsch_drill")
	if !ok || filepath.Base(pBack) != "card_10_back.png" {
		t.Fatalf("expected card_10_back.png, got %s", pBack)
	}
	store.DeutschRateCard(10, 3)
	_, pDone, ok := store.GetScreen("deutsch_drill")
	if !ok || filepath.Base(pDone) != "deck_done.png" {
		t.Fatalf("expected deck_done.png after last card, got %s", pDone)
	}

	// Classics 3-mode swap & stepping (and advancing server bookmark past offline slice)
	_, pPar, ok := store.GetScreen("deutsch_classic_parallel")
	if !ok || filepath.Base(pPar) != "c1_p.png" {
		t.Fatalf("expected c1_p.png, got %s", pPar)
	}
	if wrapped := store.DeutschStepClassic(1); wrapped {
		t.Fatalf("expected first step not to wrap")
	}
	_, pXray2, ok := store.GetScreen("deutsch_classic_xray")
	if !ok || filepath.Base(pXray2) != "c2_x.png" {
		t.Fatalf("expected c2_x.png, got %s", pXray2)
	}
	if wrapped := store.DeutschStepClassic(1); !wrapped {
		t.Fatalf("expected stepping past last bundled classic to signal wrap-for-more")
	}

	// Root stepping & quiz check
	store.DeutschStepRoot(1)
	_, pRoot, ok := store.GetScreen("deutsch_root")
	if !ok || filepath.Base(pRoot) != "r1.png" {
		t.Fatalf("expected r1.png, got %s", pRoot)
	}
	if !store.DeutschCheckQuiz(0) {
		t.Fatalf("expected quiz choice 0 to be correct")
	}
	// Outbox should have 4 deduplicated actions: rate + classic:set:2 + root:set:1 + quiz:0
	if store.OutboxCount() != 4 {
		t.Fatalf("expected 4 queued outbox actions (rate + classic:set + root:set + quiz), got %d", store.OutboxCount())
	}

	// Simulate exiting & relaunching Hermes OS while STILL offline: progress must persist!
	reloaded := NewOfflineStore(dir, filepath.Join(dir, "fallback.png"))
	_, pReloadedDrill, ok := reloaded.GetScreen("deutsch_drill")
	if !ok || filepath.Base(pReloadedDrill) != "deck_done.png" {
		t.Fatalf("expected rated card to remain skipped after offline restart, got %s", pReloadedDrill)
	}
	_, pReloadedRoot, ok := reloaded.GetScreen("deutsch_root")
	if !ok || filepath.Base(pReloadedRoot) != "r1.png" {
		t.Fatalf("expected root pack index to persist after offline restart, got %s", pReloadedRoot)
	}
}

func fastTestLockTimings() LockTimings {
	return LockTimings{
		PollInterval:      10 * time.Millisecond,
		AmazonWaitMax:     220 * time.Millisecond,
		AmazonNoChangeMax: 120 * time.Millisecond,
		SettleDuration:    30 * time.Millisecond,
		FallbackDelay:     60 * time.Millisecond,
		PostPaintSettle:   10 * time.Millisecond,
		WatchDuration:     160 * time.Millisecond,
		WatchPoll:         15 * time.Millisecond,
		MaxWatchRepaints:  2,
	}
}

func TestLockControllerBeatsAmazonWallpaperAndNeverLoopsOnSuspend(t *testing.T) {
	var (
		fbHash   atomic.Uint64
		paintsMu sync.Mutex
		paints   []string
	)
	fbHash.Store(100) // Hermes app screen initially in /dev/fb0

	lc := NewLockController(
		func(nowUnix int64, advanceCycle bool) string {
			if nowUnix >= 2000 {
				return "lockscreens/slot_1.png"
			}
			return "lockscreens/slot_0.png"
		},
		func(pngPath string, fullFlash bool) {
			paintsMu.Lock()
			paints = append(paints, pngPath)
			paintsMu.Unlock()
			if pngPath == "lockscreens/slot_1.png" {
				fbHash.Store(300)
			} else {
				fbHash.Store(200)
			}
		},
		func() (uint64, bool) { return fbHash.Load(), true },
		fastTestLockTimings(),
	)
	lc.logFn = func(string, ...any) {}

	// 1. User presses power button -> Amazon stock blanket draws wallpaper 40ms later
	lc.OnGoingToScreenSaver(1000)
	time.Sleep(40 * time.Millisecond)
	fbHash.Store(999) // Amazon stock wallpaper hits /dev/fb0

	// Wait for Phase A settle + Phase B paint
	time.Sleep(90 * time.Millisecond)
	paintsMu.Lock()
	countAfterInitial := len(paints)
	paintsMu.Unlock()
	if countAfterInitial != 1 || fbHash.Load() != 200 {
		t.Fatalf("expected 1 paint restoring fbHash=200 after Amazon draw, got count=%d fb=%d", countAfterInitial, fbHash.Load())
	}

	// 2. Simulate late second-pass Amazon wallpaper overwrite during Phase C watchdog
	fbHash.Store(888)
	time.Sleep(100 * time.Millisecond)
	paintsMu.Lock()
	countAfterRecovery := len(paints)
	paintsMu.Unlock()
	if countAfterRecovery != 2 || fbHash.Load() != 200 {
		t.Fatalf("expected watchdog to restore fbHash=200 once (count=2), got count=%d fb=%d", countAfterRecovery, fbHash.Load())
	}

	// Wait for watchdog window to close cleanly
	time.Sleep(180 * time.Millisecond)

	// 3. Kindle powerd fires readyToSuspend 10 times (e.g. retries / USB plugged in):
	//    Because framebuffer already matches Hermes lock screen, ZERO extra paints should occur!
	for i := 0; i < 10; i++ {
		lc.OnReadyToSuspend(1060 + int64(i*5))
	}
	paintsMu.Lock()
	countAfterSuspendSpam := len(paints)
	paintsMu.Unlock()
	if countAfterSuspendSpam != 2 {
		t.Fatalf("expected ZERO repaints on repeated readyToSuspend when screen intact, got %d total paints", countAfterSuspendSpam)
	}

	// 4. Meeting time slot rolls over while asleep (slot_0 -> slot_1):
	//    readyToSuspend paints slot_1 AT MOST ONCE even across repeated readyToSuspend retries!
	for i := 0; i < 5; i++ {
		lc.OnReadyToSuspend(2005 + int64(i*5))
	}
	paintsMu.Lock()
	countAfterSlotChange := len(paints)
	lastPainted := paints[len(paints)-1]
	paintsMu.Unlock()
	if countAfterSlotChange != 3 || lastPainted != "lockscreens/slot_1.png" {
		t.Fatalf("expected exactly 1 paint for new slot_1.png (total 3), got total=%d last=%s", countAfterSlotChange, lastPainted)
	}

	// 5. Fast unlock cancels pending lock paint immediately
	lc.OnOutOfScreenSaver()
	lc.OnGoingToScreenSaver(2100)
	time.Sleep(15 * time.Millisecond)
	lc.OnOutOfScreenSaver()
	time.Sleep(120 * time.Millisecond)
	paintsMu.Lock()
	countAfterCancel := len(paints)
	paintsMu.Unlock()
	if countAfterCancel != 3 {
		t.Fatalf("expected fast unlock to cancel lock paint (still 3), got %d", countAfterCancel)
	}
}

func TestLiveClockBatteryOverlaysAndMinuteAlignment(t *testing.T) {
	if sec, ok := ParseTZOffsetString("+0100"); !ok || sec != 3600 {
		t.Fatalf("expected +0100 -> 3600, got %d (%v)", sec, ok)
	}
	if sec, ok := ParseTZOffsetString("-0530"); !ok || sec != -(5*3600+30*60) {
		t.Fatalf("expected -0530 -> -19800, got %d (%v)", sec, ok)
	}
	if _, ok := ParseTZOffsetString("UTC"); ok {
		t.Fatalf("expected invalid offset to fail")
	}

	tm := time.Date(2026, 11, 27, 14, 54, 42, 0, time.UTC)
	d := NextMinuteDelay(tm)
	if d < 18*time.Second || d > 19*time.Second {
		t.Fatalf("expected ~18.15s to next minute boundary, got %v", d)
	}

	dir := t.TempDir()
	basePath := filepath.Join(dir, "base.png")
	if err := atomicWriteFile(basePath, makeDummyGrayPNG(t, 255)); err != nil {
		t.Fatalf("write base png: %v", err)
	}

	status := FormatDeviceStatus(tm, 76, true)
	if status.TimeStr != "14:54" || status.DateStr != "FRI 27 NOV" {
		t.Fatalf("unexpected formatted status: %+v", status)
	}

	// Verify lockscreen live clock + battery overlay renders cleanly inside .ls-header-left without touching right column
	lockOut := filepath.Join(dir, "lock_live.png")
	if err := ApplyLockScreenOverlays(basePath, lockOut, status); err != nil {
		t.Fatalf("ApplyLockScreenOverlays failed: %v", err)
	}
	lockImg, err := loadGrayPNG(lockOut)
	if err != nil {
		t.Fatalf("load lock out: %v", err)
	}
	darkLeft := 0
	for y := 20; y < 105; y++ {
		for x := 30; x < 550; x++ {
			if lockImg.GrayAt(x, y).Y < 128 {
				darkLeft++
			}
		}
	}
	darkRight := 0
	for y := 0; y < 124; y++ {
		for x := 612; x < kindleW; x++ {
			if lockImg.GrayAt(x, y).Y < 128 {
				darkRight++
			}
		}
	}
	if darkLeft < 1500 {
		t.Fatalf("expected visible clock+battery ink in lockscreen left header, got %d dark pixels", darkLeft)
	}
	if darkRight != 0 {
		t.Fatalf("expected lockscreen right header untouched, got %d dark pixels", darkRight)
	}
}

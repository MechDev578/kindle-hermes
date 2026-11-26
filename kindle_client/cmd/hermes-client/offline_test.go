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

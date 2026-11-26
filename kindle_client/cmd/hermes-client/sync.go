package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

type Rect struct {
	X int `json:"x"`
	Y int `json:"y"`
	W int `json:"w"`
	H int `json:"h"`
}

type Hitbox struct {
	Action       string `json:"action"`
	Label        string `json:"label"`
	X            int    `json:"x"`
	Y            int    `json:"y"`
	W            int    `json:"w"`
	H            int    `json:"h"`
	CheckboxRect *Rect  `json:"checkbox_rect,omitempty"`
	TextRect     *Rect  `json:"text_rect,omitempty"`
}

type PomoLayout struct {
	Stage     *Rect  `json:"stage,omitempty"`
	Badge     *Rect  `json:"badge,omitempty"`
	Digits    *Rect  `json:"digits,omitempty"`
	Status    *Rect  `json:"status,omitempty"`
	SegCells  []Rect `json:"seg_cells,omitempty"`
	ToggleBtn *Rect  `json:"toggle_btn,omitempty"`
	ModeFocus *Rect  `json:"mode_focus,omitempty"`
	ModeShort *Rect  `json:"mode_short,omitempty"`
	ModeLong  *Rect  `json:"mode_long,omitempty"`
}

type ScreenLayout struct {
	Topbar *Rect       `json:"topbar,omitempty"`
	Pomo   *PomoLayout `json:"pomo,omitempty"`
}

type CachedScreen struct {
	File     string       `json:"file"`
	Etag     string       `json:"etag"`
	Hitboxes []Hitbox     `json:"hitboxes"`
	Layout   ScreenLayout `json:"layout"`
}

type LockscreenSlot struct {
	ID        string `json:"id"`
	File      string `json:"file"`
	StartUnix int64  `json:"start_unix"`
	EndUnix   int64  `json:"end_unix"`
	Label     string `json:"label"`
}

type TaskItem struct {
	ID        int    `json:"id"`
	Title     string `json:"title"`
	Completed bool   `json:"completed"`
	Priority  int    `json:"priority"`
}

type PomoState struct {
	Mode              string `json:"mode"`
	Status            string `json:"status"`
	ModeLabel         string `json:"mode_label"`
	DurationSec       int    `json:"duration_sec"`
	RemainingSec      int    `json:"remaining_sec"`
	CompletedSessions int    `json:"completed_sessions"`
	StartedAtUnix     int64  `json:"started_at_unix,omitempty"`
}

type DeutschCard struct {
	ID            int      `json:"id"`
	Kind          string   `json:"kind"`
	CEFR          string   `json:"cefr"`
	AnswerDE      string   `json:"answer_de"`
	FullDE        string   `json:"full_de"`
	FrontFile     string   `json:"front_file"`
	BackFile      string   `json:"back_file"`
	FrontHitboxes []Hitbox `json:"front_hitboxes"`
	BackHitboxes  []Hitbox `json:"back_hitboxes"`
}

type DeutschClassic struct {
	ID               int      `json:"id"`
	Idx              int      `json:"idx"`
	Author           string   `json:"author"`
	Title            string   `json:"title"`
	GlossFile        string   `json:"gloss_file"`
	GlossHitboxes    []Hitbox `json:"gloss_hitboxes"`
	ParallelFile     string   `json:"parallel_file"`
	ParallelHitboxes []Hitbox `json:"parallel_hitboxes"`
	XrayFile         string   `json:"xray_file"`
	XrayHitboxes     []Hitbox `json:"xray_hitboxes"`
}

type DeutschRoot struct {
	Idx      int      `json:"idx"`
	Root     string   `json:"root"`
	File     string   `json:"file"`
	Hitboxes []Hitbox `json:"hitboxes"`
}

type DeutschBundle struct {
	CEFRLabel             string           `json:"cefr_label"`
	Sprachindex           int              `json:"sprachindex"`
	ClassicTotal          int              `json:"classic_total"`
	Deck                  []DeutschCard    `json:"deck"`
	DeckDoneFile          string           `json:"deck_done_file"`
	DeckDoneHitboxes      []Hitbox         `json:"deck_done_hitboxes"`
	Classics              []DeutschClassic `json:"classics"`
	Roots                 []DeutschRoot    `json:"roots"`
	ReaderQuizAnswerIdx   int              `json:"reader_quiz_answer_idx"`
	ReaderQuizExplanation string           `json:"reader_quiz_explanation"`
}

type BundleManifest struct {
	Version              int                     `json:"version"`
	SyncedAtUnix         int64                   `json:"synced_at_unix"`
	SyncedAtLabel        string                  `json:"synced_at_label"`
	DateShort            string                  `json:"date_short"`
	Timezone             string                  `json:"timezone"`
	TzOffsetSec          int                     `json:"tz_offset_sec"`
	LockscreenMode       string                  `json:"lockscreen_mode"`
	LockscreenDefault    string                  `json:"lockscreen_default"`
	LockscreenSlots      []LockscreenSlot        `json:"lockscreen_slots"`
	LockscreenCycleFiles []string                `json:"lockscreen_cycle_files"`
	Screens              map[string]CachedScreen `json:"screens"`
	Tasks                []TaskItem              `json:"tasks"`
	Pomodoro             PomoState               `json:"pomodoro"`
	Deutsch              *DeutschBundle          `json:"deutsch,omitempty"`
}

type OutboxAction struct {
	Seq           int64  `json:"-"`
	Action        string `json:"action"`
	CurrentScreen string `json:"current_screen"`
	TS            int64  `json:"ts"`
}

type SyncEndpoint struct {
	Name  string
	URL   string
	Token string
}

type OfflineStore struct {
	mu           sync.Mutex
	cacheDir     string
	fallbackPNG  string
	manifest     *BundleManifest
	outbox       []OutboxAction
	outboxSeq    int64
	taskState    map[int]bool
	pomo         PomoState
	cycleIdx     int
	lastSource   string
	deDeckIdx    int
	deFlipped    bool
	deClassicIdx int
	deRootIdx    int
}

func resolveEndpoints(cfg Config) []SyncEndpoint {
	cloud := SyncEndpoint{
		Name:  "cloud",
		URL:   strings.TrimRight(strings.TrimSpace(cfg.ServerURL), "/"),
		Token: strings.TrimSpace(cfg.Token),
	}
	local := SyncEndpoint{
		Name:  "macbook",
		URL:   strings.TrimRight(strings.TrimSpace(cfg.LocalServerURL), "/"),
		Token: strings.TrimSpace(cfg.LocalToken),
	}
	var list []SyncEndpoint
	if strings.EqualFold(strings.TrimSpace(cfg.SyncMode), "local_first") {
		if local.URL != "" {
			list = append(list, local)
		}
		if cloud.URL != "" && cloud.URL != local.URL {
			list = append(list, cloud)
		}
	} else {
		if cloud.URL != "" {
			list = append(list, cloud)
		}
		if local.URL != "" && local.URL != cloud.URL {
			list = append(list, local)
		}
	}
	return list
}

func NewOfflineStore(cacheDir, fallbackPNG string) *OfflineStore {
	if cacheDir == "" {
		cacheDir = "/mnt/us/kindlehermes/cache"
	}
	_ = os.MkdirAll(filepath.Join(cacheDir, "screens"), 0755)
	_ = os.MkdirAll(filepath.Join(cacheDir, "lockscreens"), 0755)
	_ = os.MkdirAll(filepath.Join(cacheDir, "deutsch"), 0755)
	s := &OfflineStore{
		cacheDir:    cacheDir,
		fallbackPNG: fallbackPNG,
		taskState:   make(map[int]bool),
		pomo: PomoState{
			Mode:         "focus",
			Status:       "idle",
			ModeLabel:    "25m Focus",
			DurationSec:  1500,
			RemainingSec: 1500,
		},
	}
	s.loadFromDiskLocked()
	return s
}

func atomicWriteFile(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	part := path + ".part"
	if err := os.WriteFile(part, data, 0644); err != nil {
		return err
	}
	return os.Rename(part, path)
}

func (s *OfflineStore) loadFromDiskLocked() {
	if raw, err := os.ReadFile(filepath.Join(s.cacheDir, "manifest.json")); err == nil {
		var m BundleManifest
		if err := json.Unmarshal(raw, &m); err == nil && len(m.Screens) > 0 {
			s.manifest = &m
			s.taskState = make(map[int]bool, len(m.Tasks))
			for _, t := range m.Tasks {
				s.taskState[t.ID] = t.Completed
			}
			if m.Pomodoro.DurationSec > 0 {
				s.pomo = m.Pomodoro
			}
		}
	}
	if raw, err := os.ReadFile(filepath.Join(s.cacheDir, "outbox.json")); err == nil {
		var acts []OutboxAction
		if err := json.Unmarshal(raw, &acts); err == nil {
			for i := range acts {
				s.outboxSeq++
				acts[i].Seq = s.outboxSeq
			}
			s.outbox = acts
			// Re-apply pending task:set overrides on top of cached manifest state
			for _, a := range acts {
				if id, done, ok := parseTaskSetAction(a.Action); ok {
					s.taskState[id] = done
				}
			}
			s.applyPendingDeutschOutboxLocked()
		}
	}
}

func parseTaskSetAction(act string) (int, bool, bool) {
	if !strings.HasPrefix(act, "task:set:") {
		return 0, false, false
	}
	parts := strings.Split(act, ":")
	if len(parts) < 4 {
		return 0, false, false
	}
	id, err := strconv.Atoi(parts[2])
	if err != nil {
		return 0, false, false
	}
	done := parts[3] == "1" || parts[3] == "true"
	return id, done, true
}

func parseDeutschRateAction(act string) (int, bool) {
	if !strings.HasPrefix(act, "deutsch:rate:") {
		return 0, false
	}
	parts := strings.Split(act, ":")
	if len(parts) < 4 {
		return 0, false
	}
	id, err := strconv.Atoi(parts[2])
	if err != nil {
		return 0, false
	}
	return id, true
}

func parseDeutschSetAction(act, prefix string) (int, bool) {
	if !strings.HasPrefix(act, prefix) {
		return 0, false
	}
	idx, err := strconv.Atoi(strings.TrimPrefix(act, prefix))
	if err != nil {
		return 0, false
	}
	return idx, true
}

func (s *OfflineStore) applyPendingDeutschOutboxLocked() {
	if s.manifest == nil || s.manifest.Deutsch == nil {
		return
	}
	de := s.manifest.Deutsch
	rated := make(map[int]bool)
	lastClassicIdx := -1
	lastRootIdx := -1
	for _, a := range s.outbox {
		if id, ok := parseDeutschRateAction(a.Action); ok {
			rated[id] = true
		} else if idx, ok := parseDeutschSetAction(a.Action, "deutsch:classic:set:"); ok {
			lastClassicIdx = idx
		} else if idx, ok := parseDeutschSetAction(a.Action, "deutsch:root:set:"); ok {
			lastRootIdx = idx
		}
	}
	for s.deDeckIdx < len(de.Deck) && rated[de.Deck[s.deDeckIdx].ID] {
		s.deDeckIdx++
	}
	if lastClassicIdx >= 0 {
		for i, cl := range de.Classics {
			if cl.Idx == lastClassicIdx {
				s.deClassicIdx = i
				break
			}
		}
	}
	if lastRootIdx >= 0 {
		for i, rt := range de.Roots {
			if rt.Idx == lastRootIdx {
				s.deRootIdx = i
				break
			}
		}
	}
}

func (s *OfflineStore) HasCache() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.manifest == nil {
		return false
	}
	home, ok := s.manifest.Screens["home"]
	if !ok {
		return false
	}
	st, err := os.Stat(filepath.Join(s.cacheDir, home.File))
	return err == nil && st.Size() > 256
}

func (s *OfflineStore) OutboxCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.outbox)
}

func (s *OfflineStore) LastSyncedLabel() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.manifest == nil {
		return ""
	}
	return s.manifest.SyncedAtLabel
}

func (s *OfflineStore) GetScreen(screenID string) (CachedScreen, string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.manifest == nil {
		return CachedScreen{}, "", false
	}

	resolveFile := func(rel string, boxes []Hitbox) (CachedScreen, string, bool) {
		if rel == "" {
			return CachedScreen{}, "", false
		}
		fullPath := filepath.Join(s.cacheDir, rel)
		st, err := os.Stat(fullPath)
		if err != nil || st.Size() < 256 {
			return CachedScreen{}, "", false
		}
		return CachedScreen{File: rel, Hitboxes: boxes}, fullPath, true
	}

	if de := s.manifest.Deutsch; de != nil {
		switch screenID {
		case "deutsch_drill":
			if s.deDeckIdx >= len(de.Deck) || len(de.Deck) == 0 {
				if scr, p, ok := resolveFile(de.DeckDoneFile, de.DeckDoneHitboxes); ok {
					return scr, p, true
				}
			} else {
				card := de.Deck[s.deDeckIdx]
				if s.deFlipped {
					if scr, p, ok := resolveFile(card.BackFile, card.BackHitboxes); ok {
						return scr, p, true
					}
				} else {
					if scr, p, ok := resolveFile(card.FrontFile, card.FrontHitboxes); ok {
						return scr, p, true
					}
				}
			}
		case "deutsch_classic", "deutsch_classic_parallel", "deutsch_classic_xray":
			if len(de.Classics) > 0 {
				cl := de.Classics[s.deClassicIdx%len(de.Classics)]
				var rel string
				var boxes []Hitbox
				switch screenID {
				case "deutsch_classic_parallel":
					rel, boxes = cl.ParallelFile, cl.ParallelHitboxes
				case "deutsch_classic_xray":
					rel, boxes = cl.XrayFile, cl.XrayHitboxes
				default:
					rel, boxes = cl.GlossFile, cl.GlossHitboxes
				}
				if scr, p, ok := resolveFile(rel, boxes); ok {
					return scr, p, true
				}
			}
		case "deutsch_root":
			if len(de.Roots) > 0 {
				rt := de.Roots[s.deRootIdx%len(de.Roots)]
				if scr, p, ok := resolveFile(rt.File, rt.Hitboxes); ok {
					return scr, p, true
				}
			}
		}
	}

	scr, ok := s.manifest.Screens[screenID]
	if !ok {
		scr, ok = s.manifest.Screens["home"]
		if !ok {
			return CachedScreen{}, "", false
		}
	}
	fullPath := filepath.Join(s.cacheDir, scr.File)
	st, err := os.Stat(fullPath)
	if err != nil || st.Size() < 256 {
		return CachedScreen{}, "", false
	}
	return scr, fullPath, true
}

func (s *OfflineStore) GetTaskStatesCopy() map[int]bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := make(map[int]bool, len(s.taskState))
	for k, v := range s.taskState {
		cp[k] = v
	}
	return cp
}

func (s *OfflineStore) saveOutboxLocked() {
	raw, err := json.MarshalIndent(s.outbox, "", "  ")
	if err == nil {
		_ = atomicWriteFile(filepath.Join(s.cacheDir, "outbox.json"), raw)
	}
}

func (s *OfflineStore) QueueAction(action, currentScreen string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.queueActionLocked(action, currentScreen)
}

func (s *OfflineStore) queueActionLocked(action, currentScreen string) {
	now := time.Now().Unix()
	// Deduplicate idempotent overrides so repeated offline taps emit one clean final state
	if id, _, ok := parseTaskSetAction(action); ok {
		filtered := s.outbox[:0]
		for _, item := range s.outbox {
			if prevID, _, prevOK := parseTaskSetAction(item.Action); prevOK && prevID == id {
				continue
			}
			filtered = append(filtered, item)
		}
		s.outbox = filtered
	} else if cid, ok := parseDeutschRateAction(action); ok {
		filtered := s.outbox[:0]
		for _, item := range s.outbox {
			if prevID, prevOK := parseDeutschRateAction(item.Action); prevOK && prevID == cid {
				continue
			}
			filtered = append(filtered, item)
		}
		s.outbox = filtered
	} else if strings.HasPrefix(action, "deutsch:classic:set:") {
		filtered := s.outbox[:0]
		for _, item := range s.outbox {
			if strings.HasPrefix(item.Action, "deutsch:classic:set:") {
				continue
			}
			filtered = append(filtered, item)
		}
		s.outbox = filtered
	} else if strings.HasPrefix(action, "deutsch:root:set:") {
		filtered := s.outbox[:0]
		for _, item := range s.outbox {
			if strings.HasPrefix(item.Action, "deutsch:root:set:") {
				continue
			}
			filtered = append(filtered, item)
		}
		s.outbox = filtered
	} else if strings.HasPrefix(action, "deutsch:quiz:") {
		filtered := s.outbox[:0]
		for _, item := range s.outbox {
			if strings.HasPrefix(item.Action, "deutsch:quiz:") {
				continue
			}
			filtered = append(filtered, item)
		}
		s.outbox = filtered
	}
	s.outboxSeq++
	s.outbox = append(s.outbox, OutboxAction{
		Seq:           s.outboxSeq,
		Action:        action,
		CurrentScreen: currentScreen,
		TS:            now,
	})
	s.saveOutboxLocked()
}

// ---------- Local Offline Hermes Deutsch Engine ----------

func (s *OfflineStore) DeutschResetFlip() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.deFlipped = false
}

func (s *OfflineStore) DeutschReveal() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.deFlipped = true
}

func (s *OfflineStore) DeutschGuess(choice string) (bool, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.deFlipped = true
	if s.manifest == nil || s.manifest.Deutsch == nil || s.deDeckIdx >= len(s.manifest.Deutsch.Deck) {
		return false, ""
	}
	ans := strings.TrimSpace(s.manifest.Deutsch.Deck[s.deDeckIdx].AnswerDE)
	return strings.EqualFold(strings.TrimSpace(choice), ans), ans
}

func (s *OfflineStore) DeutschRateCard(cardID, rating int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.queueActionLocked(fmt.Sprintf("deutsch:rate:%d:%d", cardID, rating), "deutsch_drill")
	if s.manifest != nil && s.manifest.Deutsch != nil && s.deDeckIdx < len(s.manifest.Deutsch.Deck) {
		s.deDeckIdx++
	}
	s.deFlipped = false
}

func (s *OfflineStore) DeutschStepClassic(delta int) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.manifest == nil || s.manifest.Deutsch == nil || len(s.manifest.Deutsch.Classics) == 0 {
		return false
	}
	de := s.manifest.Deutsch
	n := len(de.Classics)
	total := de.ClassicTotal
	if total < n {
		total = n
	}
	// If stepping forward past the last offline-bundled classic when more exist on server,
	// queue the NEXT server batch index so the upcoming online SYNC pulls brand-new texts!
	if delta > 0 && s.deClassicIdx == n-1 && total > n {
		nextGlobal := (de.Classics[n-1].Idx + 1) % total
		s.queueActionLocked(fmt.Sprintf("deutsch:classic:set:%d", nextGlobal), "deutsch_classic")
		s.deClassicIdx = 0
		return true
	}
	s.deClassicIdx = (s.deClassicIdx + delta%n + n) % n
	s.queueActionLocked(fmt.Sprintf("deutsch:classic:set:%d", de.Classics[s.deClassicIdx].Idx), "deutsch_classic")
	return false
}

func (s *OfflineStore) DeutschStepRoot(delta int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.manifest == nil || s.manifest.Deutsch == nil || len(s.manifest.Deutsch.Roots) == 0 {
		return
	}
	n := len(s.manifest.Deutsch.Roots)
	s.deRootIdx = (s.deRootIdx + delta%n + n) % n
	s.queueActionLocked(fmt.Sprintf("deutsch:root:set:%d", s.manifest.Deutsch.Roots[s.deRootIdx].Idx), "deutsch_root")
}

func (s *OfflineStore) DeutschCheckQuiz(choiceIdx int) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.queueActionLocked(fmt.Sprintf("deutsch:quiz:%d", choiceIdx), "deutsch_read")
	if s.manifest == nil || s.manifest.Deutsch == nil {
		return false
	}
	return choiceIdx == s.manifest.Deutsch.ReaderQuizAnswerIdx
}

func (s *OfflineStore) ToggleTaskOffline(taskID int, currentScreen string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	next := !s.taskState[taskID]
	s.taskState[taskID] = next
	flag := "0"
	if next {
		flag = "1"
	}
	s.queueActionLocked(fmt.Sprintf("task:set:%d:%s", taskID, flag), currentScreen)
	return next
}

func (s *OfflineStore) ClearDoneOffline(currentScreen string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.queueActionLocked("task:clear_done", currentScreen)
}

// ---------- Local Offline Pomodoro Engine ----------

func pomoModeDefaults(mode string) (string, int) {
	switch mode {
	case "short_break":
		return "5m Break", 5 * 60
	case "long_break":
		return "15m Rest", 15 * 60
	default:
		return "25m Focus", 25 * 60
	}
}

func (s *OfflineStore) ComputePomoNow(nowUnix int64) (PomoState, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	justCompleted := false
	p := s.pomo
	if p.DurationSec <= 0 {
		p.Mode = "focus"
		p.ModeLabel, p.DurationSec = pomoModeDefaults("focus")
		p.RemainingSec = p.DurationSec
	}
	if p.Status == "running" && p.StartedAtUnix > 0 {
		elapsed := int(nowUnix - p.StartedAtUnix)
		if elapsed < 0 {
			elapsed = 0
		}
		rem := p.RemainingSec - elapsed
		if rem <= 0 {
			p.RemainingSec = 0
			p.Status = "completed"
			p.StartedAtUnix = 0
			if p.Mode == "focus" {
				p.CompletedSessions++
			}
			s.pomo = p
			justCompleted = true
		} else {
			p.RemainingSec = rem
		}
	}
	return p, justCompleted
}

func (s *OfflineStore) PomoToggle(nowUnix int64) PomoState {
	s.mu.Lock()
	defer s.mu.Unlock()
	p := s.pomo
	if p.DurationSec <= 0 {
		p.Mode = "focus"
		p.ModeLabel, p.DurationSec = pomoModeDefaults("focus")
		p.RemainingSec = p.DurationSec
	}
	switch p.Status {
	case "running":
		elapsed := int(nowUnix - p.StartedAtUnix)
		if elapsed < 0 {
			elapsed = 0
		}
		p.RemainingSec = maxInt(0, p.RemainingSec-elapsed)
		p.StartedAtUnix = 0
		if p.RemainingSec == 0 {
			p.Status = "completed"
		} else {
			p.Status = "paused"
		}
	case "completed":
		p.RemainingSec = p.DurationSec
		p.Status = "running"
		p.StartedAtUnix = nowUnix
	default: // idle or paused
		if p.RemainingSec <= 0 {
			p.RemainingSec = p.DurationSec
		}
		p.Status = "running"
		p.StartedAtUnix = nowUnix
	}
	s.pomo = p
	return p
}

func (s *OfflineStore) PomoReset() PomoState {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.pomo.DurationSec <= 0 {
		s.pomo.Mode = "focus"
		s.pomo.ModeLabel, s.pomo.DurationSec = pomoModeDefaults("focus")
	}
	s.pomo.Status = "idle"
	s.pomo.RemainingSec = s.pomo.DurationSec
	s.pomo.StartedAtUnix = 0
	return s.pomo
}

func (s *OfflineStore) PomoSetMode(mode string) PomoState {
	s.mu.Lock()
	defer s.mu.Unlock()
	label, dur := pomoModeDefaults(mode)
	if mode != "short_break" && mode != "long_break" {
		mode = "focus"
	}
	s.pomo.Mode = mode
	s.pomo.ModeLabel = label
	s.pomo.DurationSec = dur
	s.pomo.RemainingSec = dur
	s.pomo.Status = "idle"
	s.pomo.StartedAtUnix = 0
	return s.pomo
}

// ---------- Time-Sliced Offline Lock Screen Selection ----------

func (s *OfflineStore) PickLockScreenPNG(nowUnix int64, advanceCycle bool) string {
	s.mu.Lock()
	defer s.mu.Unlock()

	checkFile := func(rel string) string {
		if rel == "" {
			return ""
		}
		p := filepath.Join(s.cacheDir, rel)
		if st, err := os.Stat(p); err == nil && st.Size() > 256 {
			return p
		}
		return ""
	}

	if s.manifest != nil {
		if s.manifest.LockscreenMode == "cycle" && len(s.manifest.LockscreenCycleFiles) > 0 {
			idx := s.cycleIdx % len(s.manifest.LockscreenCycleFiles)
			chosen := checkFile(s.manifest.LockscreenCycleFiles[idx])
			if advanceCycle {
				s.cycleIdx = (idx + 1) % len(s.manifest.LockscreenCycleFiles)
			}
			if chosen != "" {
				return chosen
			}
		}

		// Time-sliced Smart Lock Screen lookup (only if clock looks sane relative to sync time)
		if len(s.manifest.LockscreenSlots) > 0 && nowUnix >= s.manifest.SyncedAtUnix-3600 {
			for _, slot := range s.manifest.LockscreenSlots {
				if nowUnix >= slot.StartUnix && nowUnix < slot.EndUnix {
					if p := checkFile(slot.File); p != "" {
						return p
					}
				}
			}
			// If past the start of the final slot (e.g. later in evening), use the final slot
			last := s.manifest.LockscreenSlots[len(s.manifest.LockscreenSlots)-1]
			if nowUnix >= last.StartUnix {
				if p := checkFile(last.File); p != "" {
					return p
				}
			}
		}

		if p := checkFile(s.manifest.LockscreenDefault); p != "" {
			return p
		}
	}

	if s.fallbackPNG != "" {
		if st, err := os.Stat(s.fallbackPNG); err == nil && st.Size() > 256 {
			return s.fallbackPNG
		}
	}
	return ""
}

// ---------- Bundle Extraction & Dual-Endpoint Sync ----------

func (s *OfflineStore) ExtractBundleZip(zipBytes []byte) (*BundleManifest, error) {
	zr, err := zip.NewReader(bytes.NewReader(zipBytes), int64(len(zipBytes)))
	if err != nil {
		return nil, fmt.Errorf("invalid bundle zip: %w", err)
	}

	var manifestRaw []byte
	filesToWrite := make(map[string][]byte, len(zr.File))

	for _, f := range zr.File {
		clean := filepath.Clean(f.Name)
		if strings.HasPrefix(clean, "..") || filepath.IsAbs(clean) {
			continue
		}
		rc, err := f.Open()
		if err != nil {
			return nil, err
		}
		data, err := io.ReadAll(rc)
		rc.Close()
		if err != nil {
			return nil, err
		}
		if clean == "manifest.json" {
			manifestRaw = data
		} else {
			filesToWrite[clean] = data
		}
	}

	if len(manifestRaw) == 0 {
		return nil, fmt.Errorf("bundle missing manifest.json")
	}
	var manifest BundleManifest
	if err := json.Unmarshal(manifestRaw, &manifest); err != nil {
		return nil, fmt.Errorf("invalid manifest.json: %w", err)
	}
	if len(manifest.Screens) == 0 {
		return nil, fmt.Errorf("manifest contains zero screens")
	}

	// Clean stale slot & deutsch PNGs before writing fresh ones
	for _, pat := range []string{
		filepath.Join(s.cacheDir, "lockscreens", "*.png"),
		filepath.Join(s.cacheDir, "deutsch", "*.png"),
	} {
		if oldFiles, err := filepath.Glob(pat); err == nil {
			for _, old := range oldFiles {
				_ = os.Remove(old)
			}
		}
	}

	for rel, data := range filesToWrite {
		if err := atomicWriteFile(filepath.Join(s.cacheDir, rel), data); err != nil {
			return nil, err
		}
	}
	if err := atomicWriteFile(filepath.Join(s.cacheDir, "manifest.json"), manifestRaw); err != nil {
		return nil, err
	}

	// Also mirror default lockscreen to fallbackPNG if parent directory exists
	if s.fallbackPNG != "" {
		if defData, ok := filesToWrite[manifest.LockscreenDefault]; ok && len(defData) > 256 {
			if _, err := os.Stat(filepath.Dir(s.fallbackPNG)); err == nil {
				_ = atomicWriteFile(s.fallbackPNG, defData)
			}
		}
	}

	s.mu.Lock()
	s.manifest = &manifest
	s.deDeckIdx = 0
	s.deFlipped = false
	s.deClassicIdx = 0
	s.deRootIdx = 0
	s.taskState = make(map[int]bool, len(manifest.Tasks))
	for _, t := range manifest.Tasks {
		s.taskState[t.ID] = t.Completed
	}
	// Preserve locally running Pomodoro timer if user is mid-session on Kindle
	if s.pomo.Status != "running" && manifest.Pomodoro.DurationSec > 0 {
		s.pomo = manifest.Pomodoro
	}
	// Re-apply any outbox actions added concurrently while download was in flight
	for _, a := range s.outbox {
		if id, done, ok := parseTaskSetAction(a.Action); ok {
			s.taskState[id] = done
		}
	}
	s.applyPendingDeutschOutboxLocked()
	s.mu.Unlock()
	return &manifest, nil
}

func (s *OfflineStore) SyncNow(client *http.Client, endpoints []SyncEndpoint, forceRefresh bool) (string, error) {
	if len(endpoints) == 0 {
		return "", fmt.Errorf("no sync endpoints configured")
	}

	var lastErr error
	for _, ep := range endpoints {
		if err := s.syncWithEndpoint(client, ep, forceRefresh); err == nil {
			s.mu.Lock()
			s.lastSource = ep.Name
			s.mu.Unlock()
			return ep.Name, nil
		} else {
			lastErr = fmt.Errorf("%s: %w", ep.Name, err)
		}
	}
	return "", lastErr
}

func (s *OfflineStore) syncWithEndpoint(client *http.Client, ep SyncEndpoint, forceRefresh bool) error {
	// Step 1: Flush pending outbox actions first (idempotent)
	s.mu.Lock()
	pending := make([]OutboxAction, len(s.outbox))
	copy(pending, s.outbox)
	s.mu.Unlock()

	if len(pending) > 0 {
		payload := map[string]any{
			"actions":       pending,
			"refresh_feeds": forceRefresh,
		}
		body, _ := json.Marshal(payload)
		req, err := http.NewRequest("POST", ep.URL+"/api/sync/batch", bytes.NewReader(body))
		if err != nil {
			return err
		}
		req.Header.Set("Content-Type", "application/json")
		if ep.Token != "" {
			req.Header.Set("X-Hermes-Token", ep.Token)
		}
		resp, err := client.Do(req)
		if err != nil {
			return err
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		if resp.StatusCode != 200 {
			return fmt.Errorf("batch sync HTTP %d", resp.StatusCode)
		}

		// Remove only the exact flushed sequence numbers so concurrent offline taps during sync are never dropped
		var maxFlushedSeq int64
		for _, p := range pending {
			if p.Seq > maxFlushedSeq {
				maxFlushedSeq = p.Seq
			}
		}
		s.mu.Lock()
		kept := s.outbox[:0]
		for _, item := range s.outbox {
			if item.Seq > maxFlushedSeq {
				kept = append(kept, item)
			}
		}
		s.outbox = kept
		s.saveOutboxLocked()
		s.mu.Unlock()
	}

	// Step 2: Download fresh offline ZIP bundle
	q := "force_refresh=0&include_slots=1"
	if forceRefresh {
		q = "force_refresh=1&include_slots=1"
	}
	req, err := http.NewRequest("GET", ep.URL+"/api/offline-bundle?"+q, nil)
	if err != nil {
		return err
	}
	if ep.Token != "" {
		req.Header.Set("X-Hermes-Token", ep.Token)
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("offline bundle HTTP %d", resp.StatusCode)
	}
	zipData, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	_, err = s.ExtractBundleZip(zipData)
	return err
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

package main

import (
	"fmt"
	"hash/fnv"
	"io"
	"os"
	"sync"
	"time"
)

// fbReadBytes covers the entire visible 1072x1448 8-bit grayscale PW4 framebuffer
// (including scanline stride padding, e.g. 1088*1448 = 1,575,424 bytes).
const fbReadBytes = 1600 * 1024

// hashFramebuffer reads the visible portion of /dev/fb0 into a fast FNV-64a hash.
// Returns (0, false) if the framebuffer cannot be read (e.g. non-Linux tests).
func hashFramebuffer(fbPath string) (uint64, bool) {
	f, err := os.Open(fbPath)
	if err != nil {
		return 0, false
	}
	defer f.Close()

	h := fnv.New64a()
	buf := make([]byte, 64*1024)
	n, err := io.CopyBuffer(h, io.LimitReader(f, fbReadBytes), buf)
	if err != nil || n < 1024 {
		return 0, false
	}
	sum := h.Sum64()
	if sum == 0 {
		sum = 1
	}
	return sum, true
}

// LockTimings controls all wait/poll intervals for LockController so production uses
// PW4-calibrated durations while unit tests run in milliseconds.
type LockTimings struct {
	PollInterval      time.Duration
	AmazonWaitMax     time.Duration
	AmazonNoChangeMax time.Duration
	SettleDuration    time.Duration
	FallbackDelay     time.Duration
	PostPaintSettle   time.Duration
	WatchDuration     time.Duration
	WatchPoll         time.Duration
	MaxWatchRepaints  int
}

func defaultLockTimings() LockTimings {
	return LockTimings{
		PollInterval:      150 * time.Millisecond,
		AmazonWaitMax:     4200 * time.Millisecond,
		AmazonNoChangeMax: 2800 * time.Millisecond,
		SettleDuration:    600 * time.Millisecond,
		FallbackDelay:     2800 * time.Millisecond,
		PostPaintSettle:   180 * time.Millisecond,
		WatchDuration:     8 * time.Second,
		WatchPoll:         400 * time.Millisecond,
		MaxWatchRepaints:  2,
	}
}

// LockController coordinates Kindle powerd lock/suspend/unlock transitions without:
//  1. Painting too early and getting overwritten by Amazon's stock blanket wallpaper (~2-3s after lock).
//  2. Unconditionally flashing the e-ink display on every readyToSuspend event (which defers suspend
//     and creates a multi-minute repeated-refresh loop until powerd hits its retry limit).
type LockController struct {
	mu                sync.Mutex
	locked            bool
	gen               uint64
	lastPNG           string
	expectedFBHash    uint64
	initialPaintDone  bool
	suspendPaintedPNG string

	timings   LockTimings
	pickPNGFn func(nowUnix int64, advanceCycle bool) string
	paintFn   func(pngPath string, fullFlash bool)
	fbHashFn  func() (uint64, bool)
	logFn     func(format string, args ...any)
}

func NewLockController(
	pickPNGFn func(nowUnix int64, advanceCycle bool) string,
	paintFn func(pngPath string, fullFlash bool),
	fbHashFn func() (uint64, bool),
	timings LockTimings,
) *LockController {
	if fbHashFn == nil {
		fbHashFn = func() (uint64, bool) { return hashFramebuffer("/dev/fb0") }
	}
	return &LockController{
		timings:   timings,
		pickPNGFn: pickPNGFn,
		paintFn:   paintFn,
		fbHashFn:  fbHashFn,
		logFn: func(format string, args ...any) {
			fmt.Fprintf(os.Stderr, format+"\n", args...)
		},
	}
}

func (lc *LockController) IsLocked() bool {
	lc.mu.Lock()
	defer lc.mu.Unlock()
	return lc.locked
}

func (lc *LockController) IsUnlockedGen(gen uint64) bool {
	lc.mu.Lock()
	defer lc.mu.Unlock()
	return !lc.locked && lc.gen == gen
}

func (lc *LockController) isLockedGen(gen uint64) bool {
	lc.mu.Lock()
	defer lc.mu.Unlock()
	return lc.locked && lc.gen == gen
}

// sleepWhileGen sleeps up to d, waking early if lock generation changes.
func (lc *LockController) sleepWhileGen(gen uint64, d time.Duration) bool {
	if d <= 0 {
		return lc.isLockedGen(gen)
	}
	step := lc.timings.PollInterval
	if step <= 0 || step > d {
		step = d
	}
	deadline := time.Now().Add(d)
	for {
		if !lc.isLockedGen(gen) {
			return false
		}
		rem := time.Until(deadline)
		if rem <= 0 {
			return true
		}
		if rem < step {
			time.Sleep(rem)
		} else {
			time.Sleep(step)
		}
	}
}

// waitForFBQuiescence waits until /dev/fb0 stops changing for SettleDuration (or maxWait elapses).
func (lc *LockController) waitForFBQuiescence(gen uint64, startHash uint64, maxWait time.Duration) bool {
	deadline := time.Now().Add(maxWait)
	lastHash := startHash
	stableSince := time.Now()
	for time.Now().Before(deadline) {
		if !lc.sleepWhileGen(gen, lc.timings.PollInterval) {
			return false
		}
		cur, ok := lc.fbHashFn()
		if !ok {
			return lc.isLockedGen(gen)
		}
		if cur != lastHash {
			lastHash = cur
			stableSince = time.Now()
		} else if time.Since(stableSince) >= lc.timings.SettleDuration {
			return lc.isLockedGen(gen)
		}
	}
	return lc.isLockedGen(gen)
}

// OnGoingToScreenSaver handles powerd goingToScreenSaver asynchronously without blocking the event loop.
func (lc *LockController) OnGoingToScreenSaver(nowUnix int64) {
	lc.mu.Lock()
	wasLocked := lc.locked
	lc.locked = true
	pngPath := ""
	if lc.pickPNGFn != nil {
		pngPath = lc.pickPNGFn(nowUnix, !wasLocked)
	}
	// Guard duplicate goingToScreenSaver ticks when already locked on the same image
	if wasLocked && pngPath == lc.lastPNG {
		if !lc.initialPaintDone {
			lc.mu.Unlock()
			return
		}
		expected := lc.expectedFBHash
		lc.mu.Unlock()
		if expected != 0 {
			if cur, ok := lc.fbHashFn(); ok && cur == expected {
				lc.logFn("[lockscreen] duplicate goingToScreenSaver: framebuffer intact, skipping")
				return
			}
		} else {
			return
		}
		lc.mu.Lock()
		if !lc.locked {
			lc.mu.Unlock()
			return
		}
	}
	lc.gen++
	gen := lc.gen
	lc.lastPNG = pngPath
	lc.expectedFBHash = 0
	lc.initialPaintDone = false
	lc.suspendPaintedPNG = ""
	lc.mu.Unlock()

	if pngPath == "" {
		return
	}
	go lc.runLockPaintSequence(gen, pngPath)
}

func (lc *LockController) runLockPaintSequence(gen uint64, pngPath string) {
	start := time.Now()
	beforeHash, fbOK := lc.fbHashFn()

	// Phase A: Wait for Amazon's blanket screensaver to finish drawing its stock wallpaper.
	if fbOK {
		amazonDrew := false
		lastHash := beforeHash
		stableSince := start
		for {
			elapsed := time.Since(start)
			if !amazonDrew && elapsed >= lc.timings.AmazonNoChangeMax {
				break
			}
			if elapsed >= lc.timings.AmazonWaitMax {
				break
			}
			if !lc.sleepWhileGen(gen, lc.timings.PollInterval) {
				return
			}
			cur, ok := lc.fbHashFn()
			if !ok {
				fbOK = false
				break
			}
			if !amazonDrew {
				if cur != beforeHash {
					amazonDrew = true
					lastHash = cur
					stableSince = time.Now()
					lc.logFn("[lockscreen] stock screensaver draw detected after %dms", time.Since(start).Milliseconds())
				}
			} else {
				if cur != lastHash {
					lastHash = cur
					stableSince = time.Now()
				} else if time.Since(stableSince) >= lc.timings.SettleDuration {
					break
				}
			}
		}
	} else {
		if !lc.sleepWhileGen(gen, lc.timings.FallbackDelay) {
			return
		}
	}

	if !lc.isLockedGen(gen) {
		return
	}

	// Phase B: Paint Hermes Smart Lock Screen cleanly once.
	lc.paintFn(pngPath, true)
	lc.logFn("[lockscreen] painted %s at +%dms", pngPath, time.Since(start).Milliseconds())

	if !lc.sleepWhileGen(gen, lc.timings.PostPaintSettle) {
		return
	}

	hermesHash, hashOK := lc.fbHashFn()
	lc.mu.Lock()
	if !lc.locked || lc.gen != gen {
		lc.mu.Unlock()
		return
	}
	if hashOK {
		lc.expectedFBHash = hermesHash
	}
	lc.initialPaintDone = true
	lc.mu.Unlock()

	// Phase C: Watch /dev/fb0 briefly to catch any late second-pass stock wallpaper or overlay.
	if !hashOK || lc.timings.WatchDuration <= 0 || lc.timings.MaxWatchRepaints <= 0 {
		return
	}
	watchEnd := time.Now().Add(lc.timings.WatchDuration)
	repaints := 0
	for repaints < lc.timings.MaxWatchRepaints && time.Now().Before(watchEnd) {
		if !lc.sleepWhileGen(gen, lc.timings.WatchPoll) {
			return
		}
		cur, ok := lc.fbHashFn()
		if !ok {
			return
		}
		if cur == hermesHash {
			continue
		}
		// Stock framework stomped our lock screen — wait for it to finish, then restore once.
		if !lc.waitForFBQuiescence(gen, cur, 2*time.Second) {
			return
		}
		if !lc.isLockedGen(gen) {
			return
		}
		repaints++
		lc.paintFn(pngPath, true)
		lc.logFn("[lockscreen] overwrite detected (#%d) — restored %s", repaints, pngPath)
		if !lc.sleepWhileGen(gen, lc.timings.PostPaintSettle) {
			return
		}
		if newHash, ok2 := lc.fbHashFn(); ok2 {
			hermesHash = newHash
			lc.mu.Lock()
			if lc.locked && lc.gen == gen {
				lc.expectedFBHash = newHash
			}
			lc.mu.Unlock()
		}
	}
}

// OnReadyToSuspend is a strict NO-OP when the lock screen is already intact, preventing
// the Kindle powerd suspend-deferral / repeated-refresh loop. At most one recovery paint
// is ever permitted per distinct PNG path (e.g. if a meeting slot rolled over).
func (lc *LockController) OnReadyToSuspend(nowUnix int64) {
	lc.mu.Lock()
	if !lc.locked {
		lc.mu.Unlock()
		return
	}
	if !lc.initialPaintDone {
		lc.mu.Unlock()
		return
	}
	gen := lc.gen
	prevPNG := lc.lastPNG
	expectedHash := lc.expectedFBHash
	alreadyPaintedPNG := lc.suspendPaintedPNG
	lc.mu.Unlock()

	pngPath := ""
	if lc.pickPNGFn != nil {
		pngPath = lc.pickPNGFn(nowUnix, false)
	}
	if pngPath == "" {
		return
	}
	if alreadyPaintedPNG == pngPath {
		return
	}

	slotChanged := (pngPath != prevPNG)
	fbClobbered := false
	if !slotChanged && expectedHash != 0 {
		if cur, ok := lc.fbHashFn(); ok && cur != expectedHash {
			fbClobbered = true
		}
	}

	if !slotChanged && !fbClobbered {
		lc.logFn("[lockscreen] readyToSuspend: already clean, skipping paint so powerd suspends immediately")
		return
	}

	lc.mu.Lock()
	if !lc.locked || lc.gen != gen || lc.suspendPaintedPNG == pngPath {
		lc.mu.Unlock()
		return
	}
	lc.suspendPaintedPNG = pngPath
	lc.lastPNG = pngPath
	lc.mu.Unlock()

	lc.logFn("[lockscreen] readyToSuspend one-shot paint (slotChanged=%v fbClobbered=%v): %s", slotChanged, fbClobbered, pngPath)
	lc.paintFn(pngPath, true)

	if lc.sleepWhileGen(gen, lc.timings.PostPaintSettle) {
		if newHash, ok := lc.fbHashFn(); ok {
			lc.mu.Lock()
			if lc.locked && lc.gen == gen {
				lc.expectedFBHash = newHash
			}
			lc.mu.Unlock()
		}
	}
}

// OnOutOfScreenSaver immediately cancels any pending lock-screen work and returns the new generation.
func (lc *LockController) OnOutOfScreenSaver() uint64 {
	lc.mu.Lock()
	lc.locked = false
	lc.gen++
	gen := lc.gen
	lc.expectedFBHash = 0
	lc.initialPaintDone = false
	lc.suspendPaintedPNG = ""
	lc.mu.Unlock()
	return gen
}

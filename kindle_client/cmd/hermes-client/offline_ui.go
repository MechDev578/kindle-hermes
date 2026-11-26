package main

import (
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"os"
	"strconv"
	"strings"
)

// Crisp 5x7 monochrome bitmap font for razor-sharp E-Ink offline overlays (digits, uppercase, symbols).
var font5x7 = map[rune][7]uint8{
	'0': {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E},
	'1': {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E},
	'2': {0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F},
	'3': {0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E},
	'4': {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02},
	'5': {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E},
	'6': {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E},
	'7': {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08},
	'8': {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E},
	'9': {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C},
	'A': {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},
	'B': {0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E},
	'C': {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E},
	'D': {0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C},
	'E': {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F},
	'F': {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10},
	'G': {0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F},
	'H': {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},
	'I': {0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E},
	'J': {0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C},
	'K': {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11},
	'L': {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F},
	'M': {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11},
	'N': {0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11},
	'O': {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},
	'P': {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10},
	'Q': {0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D},
	'R': {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11},
	'S': {0x0E, 0x11, 0x10, 0x0E, 0x01, 0x11, 0x0E},
	'T': {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04},
	'U': {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},
	'V': {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04},
	'W': {0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11},
	'X': {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11},
	'Y': {0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04},
	'Z': {0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F},
	'm': {0x00, 0x00, 0x1A, 0x15, 0x15, 0x15, 0x15},
	':': {0x00, 0x04, 0x04, 0x00, 0x04, 0x04, 0x00},
	'-': {0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00},
	'.': {0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C},
	'·': {0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00},
	'/': {0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10},
	'!': {0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04},
	'+': {0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00},
	'?': {0x0E, 0x11, 0x01, 0x06, 0x04, 0x00, 0x04},
	' ': {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},
}

func loadGrayPNG(path string) (*image.Gray, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	img, err := png.Decode(f)
	if err != nil {
		return nil, err
	}
	b := img.Bounds()
	gray := image.NewGray(image.Rect(0, 0, b.Dx(), b.Dy()))
	draw.Draw(gray, gray.Bounds(), img, b.Min, draw.Src)
	return gray, nil
}

func saveGrayPNG(path string, img *image.Gray) error {
	part := path + ".part"
	f, err := os.Create(part)
	if err != nil {
		return err
	}
	if err := png.Encode(f, img); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(part, path)
}

func fillRect(img *image.Gray, x, y, w, h int, v uint8) {
	b := img.Bounds()
	x0 := clampInt(x, b.Min.X, b.Max.X)
	y0 := clampInt(y, b.Min.Y, b.Max.Y)
	x1 := clampInt(x+w, b.Min.X, b.Max.X)
	y1 := clampInt(y+h, b.Min.Y, b.Max.Y)
	c := color.Gray{Y: v}
	for py := y0; py < y1; py++ {
		for px := x0; px < x1; px++ {
			img.SetGray(px, py, c)
		}
	}
}

func strokeRect(img *image.Gray, x, y, w, h, thickness int, v uint8) {
	fillRect(img, x, y, w, thickness, v)
	fillRect(img, x, y+h-thickness, w, thickness, v)
	fillRect(img, x, y, thickness, h, v)
	fillRect(img, x+w-thickness, y, thickness, h, v)
}

func drawThickLine(img *image.Gray, x0, y0, x1, y1, radius int, v uint8) {
	dx := x1 - x0
	dy := y1 - y0
	steps := maxInt(absInt(dx), absInt(dy))
	if steps == 0 {
		fillRect(img, x0-radius, y0-radius, radius*2+1, radius*2+1, v)
		return
	}
	for i := 0; i <= steps; i++ {
		px := x0 + (dx*i)/steps
		py := y0 + (dy*i)/steps
		fillRect(img, px-radius, py-radius, radius*2+1, radius*2+1, v)
	}
}

func absInt(x int) int {
	if x < 0 {
		return -x
	}
	return x
}

func measureText5x7(text string, scale int) (int, int) {
	runes := []rune(text)
	if len(runes) == 0 {
		return 0, 0
	}
	w := len(runes)*(6*scale) - scale
	h := 7 * scale
	return w, h
}

func drawCenteredText5x7(img *image.Gray, box Rect, text string, scale int, v uint8) {
	// Preserve lowercase 'm' for Pomodoro "25m", uppercase everything else
	var sb strings.Builder
	for _, r := range text {
		if r == 'm' {
			sb.WriteRune('m')
		} else if r >= 'a' && r <= 'z' {
			sb.WriteRune(r - 32)
		} else {
			sb.WriteRune(r)
		}
	}
	clean := sb.String()
	tw, th := measureText5x7(clean, scale)
	startX := box.X + (box.W-tw)/2
	startY := box.Y + (box.H-th)/2

	curX := startX
	for _, r := range clean {
		glyph, ok := font5x7[r]
		if !ok {
			glyph = font5x7[' ']
		}
		for row := 0; row < 7; row++ {
			bits := glyph[row]
			for col := 0; col < 5; col++ {
				if (bits & (1 << uint(4-col))) != 0 {
					fillRect(img, curX+col*scale, startY+row*scale, scale, scale, v)
				}
			}
		}
		curX += 6 * scale
	}
}

// ApplyOfflineOverlays composites local task checkbox toggles, live offline Pomodoro state,
// and an optional status banner onto a cached 1072x1448 grayscale PNG.
func ApplyOfflineOverlays(
	basePNGPath string,
	outPNGPath string,
	screenID string,
	scr CachedScreen,
	taskStates map[int]bool,
	pomo PomoState,
	pomoDirty bool,
	bannerMsg string,
) error {
	needsTaskOverlay := (screenID == "planner" || screenID == "today") && len(taskStates) > 0
	needsPomoOverlay := (screenID == "pomodoro") && pomoDirty && scr.Layout.Pomo != nil
	needsBanner := strings.TrimSpace(bannerMsg) != ""

	if !needsTaskOverlay && !needsPomoOverlay && !needsBanner {
		data, err := os.ReadFile(basePNGPath)
		if err != nil {
			return err
		}
		return atomicWriteFile(outPNGPath, data)
	}

	img, err := loadGrayPNG(basePNGPath)
	if err != nil {
		return err
	}

	if needsTaskOverlay {
		for _, box := range scr.Hitboxes {
			if !strings.HasPrefix(box.Action, "task:toggle:") || box.CheckboxRect == nil {
				continue
			}
			idStr := strings.TrimPrefix(box.Action, "task:toggle:")
			id, err := strconv.Atoi(idStr)
			if err != nil {
				continue
			}
			done, exists := taskStates[id]
			if !exists {
				continue
			}
			cb := *box.CheckboxRect
			if done {
				fillRect(img, cb.X, cb.Y, cb.W, cb.H, 0)
				// Crisp white checkmark inside the black box
				x1 := cb.X + cb.W/5
				y1 := cb.Y + cb.H/2
				x2 := cb.X + (cb.W*42)/100
				y2 := cb.Y + (cb.H*72)/100
				x3 := cb.X + (cb.W*82)/100
				y3 := cb.Y + (cb.H*28)/100
				drawThickLine(img, x1, y1, x2, y2, 2, 255)
				drawThickLine(img, x2, y2, x3, y3, 2, 255)
				if box.TextRect != nil {
					tr := *box.TextRect
					fillRect(img, tr.X, tr.Y+tr.H/2-1, tr.W-8, 3, 68)
				}
			} else {
				fillRect(img, cb.X, cb.Y, cb.W, cb.H, 255)
				strokeRect(img, cb.X, cb.Y, cb.W, cb.H, 3, 0)
			}
		}
	}

	if needsPomoOverlay {
		pl := scr.Layout.Pomo
		if pl.Digits != nil {
			fillRect(img, pl.Digits.X-8, pl.Digits.Y-6, pl.Digits.W+16, pl.Digits.H+12, 255)
			remMin := (pomo.RemainingSec + 59) / 60
			if remMin == 0 && pomo.Status != "completed" {
				remMin = 1
			}
			label := fmt.Sprintf("%dm", remMin)
			if pomo.Status == "completed" {
				label = "DONE"
			}
			scale := 22
			if len(label) >= 4 {
				scale = 18
			}
			drawCenteredText5x7(img, *pl.Digits, label, scale, 0)
		}

		if pl.Badge != nil {
			badgeBox := Rect{X: pl.Badge.X - 60, Y: pl.Badge.Y, W: pl.Badge.W + 120, H: pl.Badge.H}
			if pl.Stage != nil {
				badgeBox.X = pl.Stage.X + 40
				badgeBox.W = pl.Stage.W - 80
			}
			fillRect(img, badgeBox.X, badgeBox.Y-2, badgeBox.W, badgeBox.H+4, 255)
			pillText := strings.ToUpper(pomo.ModeLabel) + " · " + strings.ToUpper(pomo.Status)
			tw, _ := measureText5x7(pillText, 3)
			pw := tw + 48
			px := badgeBox.X + (badgeBox.W-pw)/2
			pillRect := Rect{X: px, Y: badgeBox.Y, W: pw, H: badgeBox.H}
			if pomo.Status == "running" {
				fillRect(img, pillRect.X, pillRect.Y, pillRect.W, pillRect.H, 0)
				drawCenteredText5x7(img, pillRect, pillText, 3, 255)
			} else {
				fillRect(img, pillRect.X, pillRect.Y, pillRect.W, pillRect.H, 255)
				strokeRect(img, pillRect.X, pillRect.Y, pillRect.W, pillRect.H, 3, 0)
				drawCenteredText5x7(img, pillRect, pillText, 3, 0)
			}
		}

		if pl.Status != nil {
			fillRect(img, pl.Status.X, pl.Status.Y-4, pl.Status.W, pl.Status.H+8, 255)
			statusLine := fmt.Sprintf("%d FOCUS SESSIONS TODAY", pomo.CompletedSessions)
			switch pomo.Status {
			case "running":
				statusLine = "E-INK OFFLINE TIMER ACTIVE"
			case "paused":
				statusLine = "PAUSED · TAP START TO RESUME"
			case "completed":
				statusLine = "SESSION COMPLETE · TAKE A BREATH"
			}
			drawCenteredText5x7(img, *pl.Status, statusLine, 3, 51)
		}

		if len(pl.SegCells) > 0 && pomo.DurationSec > 0 {
			elapsed := pomo.DurationSec - pomo.RemainingSec
			if elapsed < 0 {
				elapsed = 0
			}
			filled := (elapsed * len(pl.SegCells)) / pomo.DurationSec
			if pomo.Status == "completed" {
				filled = len(pl.SegCells)
			}
			for i, cell := range pl.SegCells {
				if i < filled {
					fillRect(img, cell.X, cell.Y, cell.W, cell.H, 0)
				} else {
					fillRect(img, cell.X, cell.Y, cell.W, cell.H, 255)
					strokeRect(img, cell.X, cell.Y, cell.W, cell.H, 3, 0)
				}
			}
		}

		if pl.ToggleBtn != nil {
			tb := *pl.ToggleBtn
			if pomo.Status == "running" {
				fillRect(img, tb.X, tb.Y, tb.W, tb.H, 0)
				drawCenteredText5x7(img, tb, "PAUSE TIMER", 4, 255)
			} else {
				fillRect(img, tb.X, tb.Y, tb.W, tb.H, 255)
				strokeRect(img, tb.X, tb.Y, tb.W, tb.H, 4, 0)
				drawCenteredText5x7(img, tb, "START FOCUS", 4, 0)
			}
		}

		drawModeBtn := func(r *Rect, label string, active bool) {
			if r == nil {
				return
			}
			if active {
				fillRect(img, r.X, r.Y, r.W, r.H, 0)
				drawCenteredText5x7(img, *r, label, 3, 255)
			} else {
				fillRect(img, r.X, r.Y, r.W, r.H, 255)
				strokeRect(img, r.X, r.Y, r.W, r.H, 4, 0)
				drawCenteredText5x7(img, *r, label, 3, 0)
			}
		}
		drawModeBtn(pl.ModeFocus, "25M FOCUS", pomo.Mode == "focus")
		drawModeBtn(pl.ModeShort, "5M BREAK", pomo.Mode == "short_break")
		drawModeBtn(pl.ModeLong, "15M REST", pomo.Mode == "long_break")
	}

	if needsBanner {
		barW := 760
		barH := 40
		barX := (kindleW - barW) / 2
		barY := 98 // sits cleanly flush under the top status bar
		fillRect(img, barX-3, barY, barW+6, barH+3, 255)
		fillRect(img, barX, barY, barW, barH, 0)
		drawCenteredText5x7(img, Rect{X: barX, Y: barY, W: barW, H: barH}, bannerMsg, 3, 255)
	}

	return saveGrayPNG(outPNGPath, img)
}

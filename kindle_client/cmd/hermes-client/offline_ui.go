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
	',': {0x00, 0x00, 0x00, 0x00, 0x06, 0x04, 0x08},
	'·': {0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00},
	'/': {0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10},
	'%': {0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03},
	'!': {0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04},
	'+': {0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00},
	'?': {0x0E, 0x11, 0x01, 0x06, 0x04, 0x00, 0x04},
	' ': {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},
}

// Fast PNG encoder for transient /tmp/hermes_*.png framebuffer files (~5-6x faster on ARMv7).
var fastPNGEncoder = png.Encoder{CompressionLevel: png.BestSpeed}

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
	if err := fastPNGEncoder.Encode(f, img); err != nil {
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

func normalizeText5x7(text string) string {
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
	return sb.String()
}

func measureText5x7(text string, scale int) (int, int) {
	runes := []rune(normalizeText5x7(text))
	if len(runes) == 0 {
		return 0, 0
	}
	w := len(runes)*(6*scale) - scale
	h := 7 * scale
	return w, h
}

func drawText5x7At(img *image.Gray, startX, startY int, text string, scale int, v uint8) {
	clean := normalizeText5x7(text)
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

func drawCenteredText5x7(img *image.Gray, box Rect, text string, scale int, v uint8) {
	tw, th := measureText5x7(text, scale)
	startX := box.X + (box.W-tw)/2
	startY := box.Y + (box.H-th)/2
	drawText5x7At(img, startX, startY, text, scale, v)
}

// drawDisk fills a crisp circle of radius r centered at (cx, cy) for smooth geometric vector strokes.
func drawDisk(img *image.Gray, cx, cy, r int, v uint8) {
	b := img.Bounds()
	c := color.Gray{Y: v}
	r2 := r * r
	for dy := -r; dy <= r; dy++ {
		py := cy + dy
		if py < b.Min.Y || py >= b.Max.Y {
			continue
		}
		for dx := -r; dx <= r; dx++ {
			if dx*dx+dy*dy <= r2 {
				px := cx + dx
				if px >= b.Min.X && px < b.Max.X {
					img.SetGray(px, py, c)
				}
			}
		}
	}
}

func drawRoundLine(img *image.Gray, x0, y0, x1, y1, r int, v uint8) {
	dx := x1 - x0
	dy := y1 - y0
	steps := maxInt(absInt(dx), absInt(dy))
	if steps == 0 {
		drawDisk(img, x0, y0, r, v)
		return
	}
	for i := 0; i <= steps; i++ {
		px := x0 + (dx*i)/steps
		py := y0 + (dy*i)/steps
		drawDisk(img, px, py, r, v)
	}
}

// measureCrispClock computes exact pixel width of an "HH:MM" string drawn with drawCrispClock.
func measureCrispClock(text string, h int) int {
	digitW := (h * 60) / 100
	colonW := (h * 26) / 100
	gap := maxInt(4, (h*16)/100)
	total := 0
	for i, ch := range text {
		if i > 0 {
			total += gap
		}
		if ch == ':' {
			total += colonW
		} else {
			total += digitW
		}
	}
	return total
}

// drawCrispClock renders high-DPI smooth Swiss-grotesque digits ('0'-'9' and ':') at any height h
// with zero pixel-blockiness on 300 PPI E-Ink.
func drawCrispClock(img *image.Gray, x, y, h int, text string, v uint8) int {
	digitW := (h * 60) / 100
	colonW := (h * 26) / 100
	gap := maxInt(4, (h*16)/100)
	r := maxInt(2, (h*85)/1000) // stroke half-width (~17% of cap height => clean bold sans-serif)
	curX := x

	// Map normalized [0..100] x [0..100] glyph coordinates into pixel space inside (gx, gy, digitW, h)
	pt := func(gx, nx, ny int) (int, int) {
		ix := gx + r + ((digitW-2*r)*nx)/100
		iy := y + r + ((h-2*r)*ny)/100
		return ix, iy
	}
	poly := func(gx int, coords ...[2]int) {
		for i := 0; i < len(coords)-1; i++ {
			x0, y0 := pt(gx, coords[i][0], coords[i][1])
			x1, y1 := pt(gx, coords[i+1][0], coords[i+1][1])
			drawRoundLine(img, x0, y0, x1, y1, r, v)
		}
	}

	for i, ch := range text {
		if i > 0 {
			curX += gap
		}
		if ch == ':' {
			cx := curX + colonW/2
			cr := maxInt(2, r+1)
			drawDisk(img, cx, y+(h*32)/100, cr, v)
			drawDisk(img, cx, y+(h*70)/100, cr, v)
			curX += colonW
			continue
		}
		switch ch {
		case '0':
			poly(curX, [2]int{20, 0}, [2]int{80, 0}, [2]int{100, 18}, [2]int{100, 82}, [2]int{80, 100}, [2]int{20, 100}, [2]int{0, 82}, [2]int{0, 18}, [2]int{20, 0})
		case '1':
			poly(curX, [2]int{20, 18}, [2]int{56, 0}, [2]int{56, 100})
			poly(curX, [2]int{18, 100}, [2]int{90, 100})
		case '2':
			poly(curX, [2]int{4, 22}, [2]int{22, 0}, [2]int{78, 0}, [2]int{98, 18}, [2]int{98, 36}, [2]int{80, 52}, [2]int{4, 100}, [2]int{100, 100})
		case '3':
			poly(curX, [2]int{8, 14}, [2]int{26, 0}, [2]int{78, 0}, [2]int{96, 16}, [2]int{96, 36}, [2]int{76, 48}, [2]int{36, 48})
			poly(curX, [2]int{76, 48}, [2]int{100, 62}, [2]int{100, 84}, [2]int{78, 100}, [2]int{24, 100}, [2]int{4, 86})
		case '4':
			poly(curX, [2]int{74, 100}, [2]int{74, 0}, [2]int{2, 68}, [2]int{100, 68})
		case '5':
			poly(curX, [2]int{94, 0}, [2]int{10, 0}, [2]int{6, 46}, [2]int{26, 38}, [2]int{78, 38}, [2]int{98, 54}, [2]int{98, 82}, [2]int{78, 100}, [2]int{22, 100}, [2]int{4, 86})
		case '6':
			poly(curX, [2]int{88, 12}, [2]int{72, 0}, [2]int{28, 0}, [2]int{4, 22}, [2]int{2, 82}, [2]int{24, 100}, [2]int{78, 100}, [2]int{98, 82}, [2]int{98, 58}, [2]int{78, 44}, [2]int{24, 44}, [2]int{4, 58})
		case '7':
			poly(curX, [2]int{2, 0}, [2]int{98, 0}, [2]int{42, 100})
		case '8':
			poly(curX, [2]int{24, 48}, [2]int{6, 36}, [2]int{6, 16}, [2]int{24, 0}, [2]int{76, 0}, [2]int{94, 16}, [2]int{94, 36}, [2]int{76, 48}, [2]int{24, 48})
			poly(curX, [2]int{24, 48}, [2]int{2, 62}, [2]int{2, 84}, [2]int{24, 100}, [2]int{76, 100}, [2]int{98, 84}, [2]int{98, 62}, [2]int{76, 48})
		case '9':
			poly(curX, [2]int{12, 88}, [2]int{28, 100}, [2]int{72, 100}, [2]int{96, 78}, [2]int{98, 18}, [2]int{76, 0}, [2]int{22, 0}, [2]int{2, 18}, [2]int{2, 42}, [2]int{22, 56}, [2]int{76, 56}, [2]int{96, 42})
		}
		curX += digitW
	}
	return curX - x
}

// drawBatteryIcon renders a crisp monochrome e-ink battery gauge with proportional fill & right terminal nub.
func drawBatteryIcon(img *image.Gray, x, y, w, h, pct int, charging bool) {
	nubW := 4
	bodyW := w - nubW
	if bodyW < 14 {
		bodyW = 14
	}
	pct = clampInt(pct, 0, 100)

	// Pure white body + 3px black border
	fillRect(img, x, y, bodyW, h, 255)
	strokeRect(img, x, y, bodyW, h, 3, 0)

	// Right positive terminal nub
	nubH := h / 2
	if nubH < 8 {
		nubH = 8
	}
	fillRect(img, x+bodyW, y+(h-nubH)/2, nubW, nubH, 0)

	// Proportional inner charge bar
	pad := 5
	innerW := bodyW - 2*pad
	innerH := h - 2*pad
	if innerW > 0 && innerH > 0 {
		fillW := (innerW * pct) / 100
		if pct > 0 && fillW < 3 {
			fillW = 3
		}
		if fillW > 0 {
			fillRect(img, x+pad, y+pad, fillW, innerH, 0)
		}
	}

	// Small high-contrast charging cross/bolt indicator centered on the body
	if charging {
		cx := x + bodyW/2
		cy := y + h/2
		fillRect(img, cx-4, cy-4, 9, 9, 255)
		fillRect(img, cx-3, cy-1, 7, 2, 0)
		fillRect(img, cx-1, cy-3, 2, 7, 0)
	}
}

// drawLiveTopClock stamps live Kindle date, bold current HH:MM time, and hardware battery % into the topbar.
func drawLiveTopClock(img *image.Gray, box Rect, status DeviceStatus) {
	if status.TimeStr == "" {
		return
	}
	// Clear only the top-clock row, keeping the weather submeta line cleanly below it.
	clearX := box.X - 6
	clearY := box.Y - 4
	clearW := box.W + 12
	clearH := box.H + 6
	if clearY < 4 {
		clearY = 4
	}
	if clearY+clearH > 52 {
		clearH = 52 - clearY
	}
	if clearH < 28 {
		clearH = 34
	}
	fillRect(img, clearX, clearY, clearW, clearH, 255)

	dateScale := 3
	timeH := 28
	battScale := 3

	dateW, dateH := measureText5x7(status.DateStr, dateScale)
	timeW := measureCrispClock(status.TimeStr, timeH)
	sepW := 28 // dot separator + clean breathing room

	hasBatt := status.Battery >= 0 && status.Battery <= 100
	battText := ""
	battIconW := 40
	battIconH := 20
	battTextW := 0
	battBlockW := 0
	lowBatt := hasBatt && status.Battery <= 15 && !status.Charging
	if hasBatt {
		if status.Charging {
			battText = fmt.Sprintf("%d%%+", status.Battery)
		} else {
			battText = fmt.Sprintf("%d%%", status.Battery)
		}
		battTextW, _ = measureText5x7(battText, battScale)
		battBlockW = battIconW + 10 + battTextW
		if lowBatt {
			battBlockW += 16 // pill padding
		}
	}

	totalW := dateW + sepW + timeW
	if hasBatt {
		totalW += sepW + battBlockW
	}

	curX := box.X + (box.W-totalW)/2
	centerY := clearY + clearH/2

	// 1. Date (charcoal)
	drawText5x7At(img, curX, centerY-dateH/2+1, status.DateStr, dateScale, 51)
	curX += dateW

	// Separator dot
	fillRect(img, curX+sepW/2-2, centerY-2, 4, 4, 102)
	curX += sepW

	// 2. Crisp Bold Live Kindle Time (smooth geometric digits, pure black)
	drawCrispClock(img, curX, centerY-timeH/2, timeH, status.TimeStr, 0)
	curX += timeW

	// 3. Hardware Battery Gauge + Percent
	if hasBatt {
		fillRect(img, curX+sepW/2-2, centerY-2, 4, 4, 102)
		curX += sepW

		if lowBatt {
			// High-visibility inverted pill when battery <= 15%
			pillY := centerY - 14
			fillRect(img, curX, pillY, battBlockW, 28, 0)
			drawCenteredText5x7(img, Rect{X: curX, Y: pillY, W: battBlockW, H: 28}, fmt.Sprintf("! %d%%", status.Battery), battScale, 255)
		} else {
			drawBatteryIcon(img, curX, centerY-battIconH/2, battIconW, battIconH, status.Battery, status.Charging)
			curX += battIconW + 10
			drawText5x7At(img, curX, centerY-dateH/2+1, battText, battScale, 0)
		}
	}
}

// ApplyLockScreenOverlays stamps today's date, LAST WAKE HH:MM, and live hardware battery % onto any Smart Lock Screen PNG
// (so a static deep-sleep lock screen never displays a misleading frozen wall clock).
func ApplyLockScreenOverlays(basePNGPath, outPNGPath string, status DeviceStatus) error {
	if status.DateStr == "" && status.TimeStr == "" {
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

	// All 5 lockscreen templates share identical .ls-header-left geometry: x=32..608, y=0..118 (border at y=119..123).
	fillRect(img, 28, 10, 574, 104, 255)

	leftX := 34

	// Line 1: Today's date as bold primary anchor (stays accurate all day during deep sleep)
	if status.DateStr != "" {
		drawText5x7At(img, leftX, 22, status.DateStr, 5, 0)
	}

	// Line 2: Honest "LAST WAKE HH:MM" metadata + live hardware battery gauge & %
	wakeStr := status.LastWakeStr
	if wakeStr == "" {
		wakeStr = status.TimeStr
	}
	subY := 71
	curX := leftX
	subScale := 3
	_, subH := measureText5x7("A", subScale)
	centerY := subY + subH/2

	if wakeStr != "" {
		prefix := "LAST WAKE "
		pw, _ := measureText5x7(prefix, subScale)
		drawText5x7At(img, curX, subY, prefix, subScale, 68)
		curX += pw + 3*subScale/3
		// measureText5x7 trims trailing inter-char space; add explicit space before HH:MM
		tw, _ := measureText5x7(wakeStr, subScale)
		drawText5x7At(img, curX, subY, wakeStr, subScale, 0)
		curX += tw
	}

	hasBatt := status.Battery >= 0 && status.Battery <= 100
	if hasBatt {
		if wakeStr != "" {
			sepW := 24
			fillRect(img, curX+sepW/2-2, centerY-2, 4, 4, 102)
			curX += sepW
		}
		lowBatt := status.Battery <= 15 && !status.Charging
		if lowBatt {
			label := fmt.Sprintf("! %d%% LOW", status.Battery)
			lw, _ := measureText5x7(label, subScale)
			pillW := lw + 18
			pillY := centerY - 14
			fillRect(img, curX, pillY, pillW, 28, 0)
			drawCenteredText5x7(img, Rect{X: curX, Y: pillY, W: pillW, H: 28}, label, subScale, 255)
		} else {
			battIconW := 40
			battIconH := 20
			drawBatteryIcon(img, curX, centerY-battIconH/2, battIconW, battIconH, status.Battery, status.Charging)
			curX += battIconW + 10
			battLabel := fmt.Sprintf("%d%%", status.Battery)
			if status.Charging {
				battLabel = fmt.Sprintf("%d%% CHG", status.Battery)
			}
			drawText5x7At(img, curX, subY, battLabel, subScale, 0)
		}
	}

	return saveGrayPNG(outPNGPath, img)
}

// ApplyOfflineOverlays composites live Kindle clock + battery %, local task checkbox toggles,
// live offline Pomodoro state, and an optional status banner onto a cached 1072x1448 grayscale PNG.
func ApplyOfflineOverlays(
	basePNGPath string,
	outPNGPath string,
	screenID string,
	scr CachedScreen,
	taskStates map[int]bool,
	pomo PomoState,
	pomoDirty bool,
	bannerMsg string,
	status DeviceStatus,
) error {
	needsClock := status.TimeStr != ""
	needsTaskOverlay := (screenID == "planner" || screenID == "today" || strings.HasPrefix(screenID, "planner_list_")) && len(taskStates) > 0
	needsPomoOverlay := (screenID == "pomodoro") && pomoDirty && scr.Layout.Pomo != nil
	needsBanner := strings.TrimSpace(bannerMsg) != ""

	if !needsClock && !needsTaskOverlay && !needsPomoOverlay && !needsBanner {
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

	if needsClock {
		clockBox := Rect{X: 290, Y: 12, W: 542, H: 38}
		if scr.Layout.TopClock != nil && scr.Layout.TopClock.W >= 300 {
			clockBox = *scr.Layout.TopClock
		}
		drawLiveTopClock(img, clockBox, status)
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

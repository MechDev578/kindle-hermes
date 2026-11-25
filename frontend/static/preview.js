/* Paperwhite 10th Gen (1072x1448) Interactive Simulator
 * Supports 3 modes:
 * 1. Interactive Web (/kindle HTML)
 * 2. Native FBInk Thin-Client Simulator (renders 1072x1448 grayscale PNG + clicks real hitboxes!)
 * 3. Smart Lock Screen Tester (with Simulated Time-of-Day clock)
 */
(function () {
  const KINDLE_W = 1072;
  const KINDLE_H = 1448;

  let mode = 'web'; // 'web' | 'native' | 'lockscreen'
  let currentScreen = 'home';
  let currentHitboxes = [];
  let showHitboxes = false;
  let flashEnabled = true;

  const $ = (id) => document.getElementById(id);

  function computeScale() {
    const availH = Math.max(460, window.innerHeight - 200);
    const availW = Math.max(300, window.innerWidth - 88);
    const scale = Math.min(availH / KINDLE_H, availW / KINDLE_W, 0.62);
    $('screen-viewport').style.width = Math.round(KINDLE_W * scale) + 'px';
    $('screen-viewport').style.height = Math.round(KINDLE_H * scale) + 'px';
    $('screen-scaler').style.transform = `scale(${scale})`;
    $('screen-scaler').dataset.scale = String(scale);
  }

  function triggerEinkFlash() {
    if (!flashEnabled) return;
    const ov = $('flash-overlay');
    ov.classList.add('flashing');
    setTimeout(() => ov.classList.remove('flashing'), 140);
  }

  function getCommonParams() {
    const params = new URLSearchParams();
    const sim = $('sim-time').value;
    const mockVal = $('mock-select').value;
    if (sim) params.set('sim_time', sim);
    if (mockVal !== '') params.set('mock', mockVal);
    return params;
  }

  function setModeButtons() {
    ['web', 'native', 'lockscreen'].forEach((m) => {
      $('mode-' + m).classList.toggle('active', mode === m);
    });
    $('hitbox-toggle').style.display = mode === 'native' ? 'inline-flex' : 'none';
    $('slide-select').style.display = mode === 'lockscreen' ? 'inline-flex' : 'none';
  }

  function updateExportLink() {
    const params = getCommonParams();
    if (mode === 'lockscreen') {
      params.set('slide', $('slide-select').value);
      $('export-png').href = '/lockscreen.png?' + params.toString();
      $('export-png').download = `hermes_lockscreen_${$('slide-select').value}_1072x1448.png`;
    } else {
      $('export-png').href = `/api/screen/${currentScreen}.png?` + params.toString();
      $('export-png').download = `hermes_${currentScreen}_1072x1448.png`;
    }
  }

  async function renderNativeScreen(screenId, forceRefresh = false) {
    currentScreen = screenId;
    const params = getCommonParams();
    if (forceRefresh) params.set('force_refresh', '1');
    $('sim-status').innerHTML = `Rendering <strong>${screenId}</strong> (1072&times;1448 8-bit grayscale PNG + hitboxes)...`;

    const meta = await fetch(`/api/screen/${screenId}?${params.toString()}`).then((r) => r.json());
    currentHitboxes = meta.hitboxes || [];
    triggerEinkFlash();
    $('native-img').src = meta.image_url;
    drawHitboxes();
    updateExportLink();
    $('sim-status').innerHTML = `<strong>Native FBInk Mode:</strong> Screen <strong>${meta.screen_id}</strong> &middot; ${currentHitboxes.length} interactive hitboxes &middot; Auto-refresh ${meta.refresh_after_sec}s &middot; Click directly on the PNG!`;
  }

  function drawHitboxes() {
    const layer = $('hitbox-layer');
    layer.innerHTML = '';
    if (!showHitboxes) return;
    currentHitboxes.forEach((box) => {
      const div = document.createElement('div');
      div.className = 'hitbox-rect';
      div.style.left = box.x + 'px';
      div.style.top = box.y + 'px';
      div.style.width = box.w + 'px';
      div.style.height = box.h + 'px';
      div.textContent = box.action;
      layer.appendChild(div);
    });
  }

  function refreshView() {
    setModeButtons();
    const params = getCommonParams();
    updateExportLink();

    if (mode === 'web') {
      $('native-stage').style.display = 'none';
      $('kindle-iframe').style.display = 'block';
      params.set('screen', currentScreen);
      triggerEinkFlash();
      $('kindle-iframe').src = '/kindle?' + params.toString();
      $('sim-status').innerHTML = `<strong>Interactive Web Mode:</strong> 1072&times;1448 ES5-safe Hermes OS &middot; Click any app tile, task checkbox, or Pomodoro control.`;
    } else if (mode === 'native') {
      $('kindle-iframe').style.display = 'none';
      $('native-stage').style.display = 'block';
      renderNativeScreen(currentScreen);
    } else {
      $('kindle-iframe').style.display = 'none';
      $('native-stage').style.display = 'block';
      currentHitboxes = [];
      drawHitboxes();
      const slide = $('slide-select').value;
      params.set('slide', slide);
      params.set('t', String(Date.now()));
      triggerEinkFlash();
      $('native-img').src = '/lockscreen.png?' + params.toString();
      $('sim-status').innerHTML = `<strong>Lock Screen Mode (${slide.toUpperCase()}):</strong> Exact 1072&times;1448 8-bit grayscale PNG sent to Kindle when locked. Try changing the simulated clock!`;
    }
  }

  async function handleNativeClick(evt) {
    if (mode !== 'native') return;
    const rect = $('native-img').getBoundingClientRect();
    const scaleX = KINDLE_W / rect.width;
    const scaleY = KINDLE_H / rect.height;
    const x = Math.round((evt.clientX - rect.left) * scaleX);
    const y = Math.round((evt.clientY - rect.top) * scaleY);

    // Find topmost (smallest area) hitbox containing (x, y)
    const matches = currentHitboxes.filter((b) => x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h);
    if (!matches.length) {
      $('sim-status').innerHTML = `Tapped (${x}, ${y}) — no hitbox at this point.`;
      return;
    }
    matches.sort((a, b) => (a.w * a.h) - (b.w * b.h));
    const hit = matches[0];

    $('sim-status').innerHTML = `Native tap (${x}, ${y}) &rarr; <strong>${hit.action}</strong>...`;
    const body = { action: hit.action, current_screen: currentScreen };
    if ($('sim-time').value) body.sim_time = $('sim-time').value;
    if ($('mock-select').value === '1') body.mock = true;
    if ($('mock-select').value === '0') body.mock = false;

    const res = await fetch('/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => r.json());

    if (res.exit) {
      alert('Native Kindle Client exited cleanly — returning to your Kindle Library.');
      await renderNativeScreen('home');
      return;
    }
    await renderNativeScreen(res.next_screen || currentScreen, Boolean(res.force_refresh));
  }

  window.addEventListener('message', (evt) => {
    if (evt.data && evt.data.type === 'hermes_nav' && evt.data.screen) {
      currentScreen = evt.data.screen;
      triggerEinkFlash();
      updateExportLink();
    }
  });

  $('mode-web').addEventListener('click', () => { mode = 'web'; refreshView(); });
  $('mode-native').addEventListener('click', () => { mode = 'native'; refreshView(); });
  $('mode-lockscreen').addEventListener('click', () => { mode = 'lockscreen'; refreshView(); });
  $('sim-time').addEventListener('change', refreshView);
  $('mock-select').addEventListener('change', refreshView);
  $('slide-select').addEventListener('change', refreshView);
  $('flash-toggle').addEventListener('click', () => {
    flashEnabled = !flashEnabled;
    $('flash-toggle').classList.toggle('active', flashEnabled);
  });
  $('hitbox-toggle').addEventListener('click', () => {
    showHitboxes = !showHitboxes;
    $('hitbox-toggle').classList.toggle('active', showHitboxes);
    drawHitboxes();
  });
  $('native-stage').addEventListener('click', handleNativeClick);
  window.addEventListener('resize', computeScale);

  computeScale();
  // Ensure user is authenticated via cookie if HERMES_TOKEN is enabled
  fetch('/api/auth/status')
    .then((r) => r.json())
    .then((auth) => {
      if (auth.auth_enabled && !auth.authenticated) {
        location.href = '/app';
        return;
      }
      try {
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (tz) {
          fetch('/api/location/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ timezone: tz, source: 'browser-tz' }),
          }).catch(() => {});
        }
      } catch (_) {}
      refreshView();
    })
    .catch(() => refreshView());
})();

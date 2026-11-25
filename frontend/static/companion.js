/* KindleHermes Cross-Device Companion PWA (iOS / macOS / Windows) */
(function () {
  let appState = null;

  const $ = (id) => document.getElementById(id);

  function toast(msg) {
    const el = $('toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 2200);
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (res.status === 401) {
      $('auth-overlay').classList.add('visible');
      throw new Error('Unauthorized');
    }
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(errText || 'Request failed');
    }
    return res.json();
  }

  function switchTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach((b) => {
      b.classList.toggle('active', b.dataset.tab === tabId);
    });
    document.querySelectorAll('.panel').forEach((p) => {
      p.classList.toggle('active', p.id === 'panel-' + tabId);
    });
    if (tabId === 'lockscreen') {
      refreshLockPreview();
    }
  }

  function renderTasks(tasks) {
    const list = $('task-list');
    if (!list) return;
    if (!tasks.length) {
      list.innerHTML = '<div style="color:#5c5b57;font-size:14px;padding:8px 0;">No priorities yet. Add one above to sync to your Kindle.</div>';
      return;
    }
    list.innerHTML = '';
    tasks.forEach((t) => {
      const row = document.createElement('div');
      row.className = 'task-item' + (t.completed ? ' done' : '');
      row.innerHTML = `
        <input type="checkbox" ${t.completed ? 'checked' : ''} style="width:20px;height:20px;cursor:pointer;" />
        <div class="task-title"></div>
        <span class="prio-pill">P${t.priority}</span>
        <button class="btn btn-sm btn-danger" type="button">Delete</button>
      `;
      row.querySelector('.task-title').textContent = t.title;
      row.querySelector('input').addEventListener('change', async () => {
        const data = await api(`/api/tasks/${t.id}/toggle`, { method: 'POST' });
        appState.tasks = data.tasks;
        renderTasks(data.tasks);
        toast('Synced to Kindle');
      });
      row.querySelector('button').addEventListener('click', async () => {
        const data = await api(`/api/tasks/${t.id}`, { method: 'DELETE' });
        appState.tasks = data.tasks;
        renderTasks(data.tasks);
        toast('Task removed');
      });
      list.appendChild(row);
    });
  }

  function renderPomodoro(pomo) {
    $('pomo-digits').textContent = pomo.remaining_label;
    $('pomo-meta').textContent = `${pomo.mode_label} · ${pomo.status.toUpperCase()} · ${pomo.completed_sessions} sessions today`;
    $('pomo-toggle-btn').textContent = pomo.is_running ? 'Pause' : 'Start';
    document.querySelectorAll('[data-pomo-mode]').forEach((btn) => {
      btn.classList.toggle('btn-primary', btn.dataset.pomoMode === pomo.mode);
    });
  }

  function renderAgendaPreview(cal) {
    const el = $('agenda-summary');
    if (!el) return;
    let html = '';
    if (cal.current_event) {
      html += `<div style="padding:10px 12px;background:#111;color:#fff;border-radius:8px;margin-bottom:10px;">
        <div style="font-size:11px;letter-spacing:1.2px;font-weight:800;">HAPPENING NOW · ${cal.current_event.remaining_label.toUpperCase()}</div>
        <div style="font-size:16px;font-weight:700;margin-top:3px;">${cal.current_event.title}</div>
        <div style="font-size:13px;opacity:0.85;">${cal.current_event.time_range}</div>
      </div>`;
    }
    const items = cal.upcoming_today.slice(0, 5);
    if (!items.length) {
      html += '<div style="font-size:14px;color:#5c5b57;">No remaining events today.</div>';
    } else {
      items.forEach((ev) => {
        html += `<div style="display:flex;gap:12px;padding:8px 0;border-bottom:1px solid #e5e2d9;font-size:14px;">
          <strong style="min-width:56px;font-family:monospace;">${ev.start_label}</strong>
          <div><strong>${ev.title}</strong>${ev.location ? `<div style="font-size:12px;color:#5c5b57;">${ev.location}</div>` : ''}</div>
        </div>`;
      });
    }
    el.innerHTML = html;
  }

  function renderLockscreenControls(settings) {
    $('ls-mode-select').value = settings.lockscreen_mode || 'smart';
    const enabledSet = new Set(settings.lockscreen_slides || ['smart']);
    const slideDefs = [
      { id: 'smart', title: 'Smart Context-Aware', desc: 'NOW + NEXT UP + Rest of Today + Tasks + Weather' },
      { id: 'calendar', title: 'Full Calendar', desc: 'Today timeline + Tomorrow preview' },
      { id: 'planning', title: 'Planning & Briefing', desc: 'Daily focus + Checklist + Morning Market Pulse' },
      { id: 'weather', title: 'Detailed Weather', desc: 'Current + Next 8 Hours + 5-Day Outlook' },
    ];
    const container = $('ls-slide-list');
    container.innerHTML = '';
    slideDefs.forEach((s) => {
      const checked = enabledSet.has(s.id);
      const div = document.createElement('label');
      div.className = 'slide-chip' + (checked ? ' enabled' : '');
      div.innerHTML = `
        <div>
          <div style="font-weight:700;font-size:14px;">${s.title}</div>
          <div style="font-size:12px;color:#5c5b57;">${s.desc}</div>
        </div>
        <input type="checkbox" value="${s.id}" ${checked ? 'checked' : ''} style="width:20px;height:20px;" />
      `;
      div.querySelector('input').addEventListener('change', saveLockscreenSettings);
      container.appendChild(div);
    });
  }

  function renderModules(modules) {
    const container = $('module-list');
    container.innerHTML = '';
    modules.forEach((m) => {
      const div = document.createElement('label');
      div.className = 'module-chip' + (m.enabled ? ' enabled' : '');
      div.innerHTML = `
        <div>
          <div style="font-weight:700;font-size:15px;">${m.title}</div>
          <div style="font-size:13px;color:#5c5b57;">${m.tagline}</div>
        </div>
        <input type="checkbox" value="${m.id}" ${m.enabled ? 'checked' : ''} style="width:20px;height:20px;" />
      `;
      div.querySelector('input').addEventListener('change', async () => {
        const chosen = Array.from(container.querySelectorAll('input:checked')).map((i) => i.value);
        if (!chosen.length) {
          toast('Keep at least one app enabled');
          await loadState();
          return;
        }
        await api('/api/settings', {
          method: 'PATCH',
          body: JSON.stringify({ enabled_modules: chosen }),
        });
        toast('Home Screen apps updated OTA');
        await loadState();
      });
      container.appendChild(div);
    });
  }

  function getBrowserTimezone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (_) {
      return '';
    }
  }

  function detectDeviceLabel() {
    const ua = navigator.userAgent || '';
    if (/iPhone|iPad|iPod/i.test(ua)) return 'iphone';
    if (/Android/i.test(ua)) return 'android';
    if (/Macintosh|Mac OS X/i.test(ua)) return 'macbook';
    if (/Windows/i.test(ua)) return 'windows';
    return 'device';
  }

  async function syncDeviceLocationAndTime(explicit = false) {
    const tz = getBrowserTimezone();
    const device = detectDeviceLabel();
    const canGeo = Boolean(navigator.geolocation && (window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1'));

    if (explicit) toast('Detecting location & time...');

    const fallbackToIp = async () => {
      const res = await api('/api/location/auto-detect', {
        method: 'POST',
        body: JSON.stringify({ timezone: tz, source: `${device}-ip` }),
      });
      if (explicit && res.settings) {
        toast(`Synced: ${res.settings.weather_city} · ${res.settings.timezone}`);
      }
      await loadState(false);
    };

    if (!canGeo) {
      await fallbackToIp();
      return;
    }

    await new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(
        async (pos) => {
          try {
            const res = await api('/api/location/sync', {
              method: 'POST',
              body: JSON.stringify({
                lat: pos.coords.latitude,
                lon: pos.coords.longitude,
                timezone: tz,
                source: `${device}-gps`,
              }),
            });
            if (explicit && res.settings) {
              toast(`GPS synced: ${res.settings.weather_city} · ${res.settings.timezone}`);
            }
            await loadState(false);
          } catch (_) {
            await fallbackToIp();
          }
          resolve();
        },
        async () => {
          try {
            await fallbackToIp();
          } catch (_) {}
          resolve();
        },
        { enableHighAccuracy: false, timeout: 6000, maximumAge: 300000 }
      );
    });
  }

  function renderSettingsForm(settings) {
    $('daily-focus-input').value = settings.daily_focus || '';
    $('setting-mock').checked = Boolean(settings.use_mock_data);
    $('setting-auto-loc').checked = settings.auto_sync_location !== false;
    $('setting-ics').value = settings.calendar_ics_url || '';
    $('setting-tz').value = settings.timezone || 'Europe/Berlin';
    $('setting-city').value = settings.weather_city || 'Berlin';
    $('setting-lat').value = settings.weather_lat;
    $('setting-lon').value = settings.weather_lon;
    const meta = $('location-sync-meta');
    if (meta) {
      const src = settings.location_source || 'default';
      const when = settings.location_updated_at ? ` · updated ${settings.location_updated_at}` : '';
      meta.textContent = `Current: ${settings.weather_city} (${settings.timezone}) · source: ${src}${when}`;
    }
  }

  function refreshLockPreview() {
    const slide = $('ls-preview-slide').value || 'auto';
    const sim = $('ls-preview-time').value || '';
    const params = new URLSearchParams({ slide, t: String(Date.now()) });
    if (sim) params.set('sim_time', sim);
    const url = '/lockscreen.png?' + params.toString();
    $('ls-preview-img').src = url;
    $('ls-download-link').href = url;
  }

  async function saveLockscreenSettings() {
    const mode = $('ls-mode-select').value;
    const slides = Array.from($('ls-slide-list').querySelectorAll('input:checked')).map((i) => i.value);
    if (!slides.length) slides.push('smart');
    const updated = await api('/api/settings', {
      method: 'PATCH',
      body: JSON.stringify({ lockscreen_mode: mode, lockscreen_slides: slides }),
    });
    appState.settings = updated;
    renderLockscreenControls(updated);
    refreshLockPreview();
    toast('Lock-screen config saved');
  }

  function renderGoogleCalendar(gcal) {
    if (!gcal) return;
    $('gcal-redirect-uri').textContent = gcal.redirect_uri || (location.origin + '/api/google/callback');
    const badge = $('gcal-badge');
    if (gcal.connected) {
      badge.textContent = 'CONNECTED';
      badge.style.background = '#111';
      badge.style.color = '#fff';
      $('gcal-connected-view').style.display = 'block';
      $('gcal-setup-view').style.display = 'none';
      $('gcal-account-email').textContent = gcal.account_email || 'Google Account Connected';

      const list = $('gcal-calendar-list');
      list.innerHTML = '';
      const selectedSet = new Set(gcal.selected_ids || []);
      (gcal.calendars || []).forEach((cal) => {
        const checked = selectedSet.has(cal.id);
        const label = document.createElement('label');
        label.className = 'slide-chip' + (checked ? ' enabled' : '');
        label.innerHTML = `
          <div>
            <div style="font-weight:700;font-size:14px;"></div>
            <div style="font-size:12px;color:#5c5b57;">${cal.primary ? 'Primary calendar' : cal.access_role}</div>
          </div>
          <input type="checkbox" value="${cal.id}" ${checked ? 'checked' : ''} style="width:20px;height:20px;" />
        `;
        label.querySelector('div > div').textContent = cal.summary;
        label.querySelector('input').addEventListener('change', async () => {
          const ids = Array.from(list.querySelectorAll('input:checked')).map((i) => i.value);
          const updated = await api('/api/google/calendars', {
            method: 'PATCH',
            body: JSON.stringify({ selected_ids: ids }),
          });
          renderGoogleCalendar(updated);
          toast(`Syncing ${ids.length} Google Calendar(s)`);
          await loadState(false);
        });
        list.appendChild(label);
      });
    } else {
      badge.textContent = gcal.has_credentials ? 'READY TO CONNECT' : 'NOT CONNECTED';
      badge.style.background = '';
      badge.style.color = '';
      $('gcal-connected-view').style.display = 'none';
      $('gcal-setup-view').style.display = 'block';
      $('gcal-connect-ready').style.display = gcal.has_credentials ? 'block' : 'none';
      $('gcal-creds-box').style.display = gcal.has_credentials ? 'none' : 'block';
    }
  }

  async function loadGoogleStatus() {
    try {
      const gcal = await api('/api/google/status');
      renderGoogleCalendar(gcal);
    } catch (_) {}
  }

  async function loadState(runAutoSync = true) {
    const data = await api('/api/state');
    appState = data;
    $('status-meta').textContent = `${data.date_long} · ${data.time_str} · ${data.weather.city} ${data.weather.temp}°C`;
    renderTasks(data.tasks);
    renderPomodoro(data.pomodoro);
    renderAgendaPreview(data.calendar);
    renderLockscreenControls(data.settings);
    renderModules(data.modules);
    renderSettingsForm(data.settings);
    loadGoogleStatus();

    if (runAutoSync && data.settings.auto_sync_location !== false) {
      const tz = getBrowserTimezone();
      const alreadySynced = sessionStorage.getItem('hermes_loc_synced') === '1';
      if (!alreadySynced || (tz && tz !== data.settings.timezone)) {
        sessionStorage.setItem('hermes_loc_synced', '1');
        syncDeviceLocationAndTime(false).catch(() => {});
      }
    }
  }

  function wireEvents() {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    $('add-task-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const title = $('new-task-title').value.trim();
      const priority = parseInt($('new-task-priority').value, 10);
      if (!title) return;
      const data = await api('/api/tasks', {
        method: 'POST',
        body: JSON.stringify({ title, priority }),
      });
      $('new-task-title').value = '';
      renderTasks(data.tasks);
      toast('Added & synced to Kindle');
    });

    $('clear-done-btn').addEventListener('click', async () => {
      const data = await api('/api/tasks/clear-completed', { method: 'POST' });
      renderTasks(data.tasks);
      toast('Cleared completed tasks');
    });

    $('save-focus-btn').addEventListener('click', async () => {
      await api('/api/settings', {
        method: 'PATCH',
        body: JSON.stringify({ daily_focus: $('daily-focus-input').value.trim() }),
      });
      toast('Daily focus saved');
    });

    $('pomo-toggle-btn').addEventListener('click', async () => {
      renderPomodoro(await api('/api/pomodoro/toggle', { method: 'POST' }));
    });

    $('pomo-reset-btn').addEventListener('click', async () => {
      renderPomodoro(await api('/api/pomodoro/reset', { method: 'POST' }));
    });

    document.querySelectorAll('[data-pomo-mode]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        renderPomodoro(await api('/api/pomodoro/mode', {
          method: 'POST',
          body: JSON.stringify({ mode: btn.dataset.pomoMode }),
        }));
      });
    });

    $('ls-mode-select').addEventListener('change', saveLockscreenSettings);
    $('ls-preview-slide').addEventListener('change', refreshLockPreview);
    $('ls-preview-time').addEventListener('change', refreshLockPreview);
    $('ls-refresh-btn').addEventListener('click', refreshLockPreview);

    $('header-sync-loc-btn').addEventListener('click', () => syncDeviceLocationAndTime(true));
    $('sync-loc-btn').addEventListener('click', () => syncDeviceLocationAndTime(true));
    $('setting-auto-loc').addEventListener('change', async () => {
      await api('/api/settings', {
        method: 'PATCH',
        body: JSON.stringify({ auto_sync_location: $('setting-auto-loc').checked }),
      });
      toast($('setting-auto-loc').checked ? 'Auto location & time ON' : 'Auto location & time OFF');
    });

    $('save-settings-btn').addEventListener('click', async () => {
      await api('/api/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          use_mock_data: $('setting-mock').checked,
          auto_sync_location: $('setting-auto-loc').checked,
          calendar_ics_url: $('setting-ics').value.trim(),
          timezone: $('setting-tz').value.trim() || 'Europe/Berlin',
          weather_city: $('setting-city').value.trim() || 'Berlin',
          weather_lat: parseFloat($('setting-lat').value) || 52.52,
          weather_lon: parseFloat($('setting-lon').value) || 13.405,
        }),
      });
      toast('Settings updated OTA');
      await loadState(false);
    });

    $('city-search-btn').addEventListener('click', async () => {
      const q = $('setting-city').value.trim();
      if (!q) return;
      const data = await api('/api/weather/search?q=' + encodeURIComponent(q));
      const box = $('city-results');
      if (!data.results || !data.results.length) {
        box.innerHTML = '<div class="city-option">No matching cities found (check internet or enter lat/lon).</div>';
        return;
      }
      box.innerHTML = '';
      data.results.forEach((item) => {
        const opt = document.createElement('div');
        opt.className = 'city-option';
        opt.textContent = `${item.label} (${item.lat}, ${item.lon})`;
        opt.addEventListener('click', () => {
          $('setting-city').value = item.city;
          $('setting-lat').value = item.lat;
          $('setting-lon').value = item.lon;
          if (item.timezone) $('setting-tz').value = item.timezone;
          box.innerHTML = '';
          toast(`Selected ${item.city} — click Save Settings`);
        });
        box.appendChild(opt);
      });
    });

    $('refresh-briefing-btn').addEventListener('click', async () => {
      toast('Refreshing daily briefing...');
      await api('/api/briefing/refresh', { method: 'POST' });
      toast('Briefing updated');
    });

    $('gcal-save-creds-btn').addEventListener('click', async () => {
      const client_id = $('gcal-client-id').value.trim();
      const client_secret = $('gcal-client-secret').value.trim();
      if (!client_id || !client_secret) {
        toast('Enter both Client ID and Client Secret');
        return;
      }
      const gcal = await api('/api/google/credentials', {
        method: 'POST',
        body: JSON.stringify({ client_id, client_secret }),
      });
      renderGoogleCalendar(gcal);
      toast('Credentials saved — click Connect Google Calendar');
    });

    $('gcal-edit-creds-btn').addEventListener('click', () => {
      $('gcal-creds-box').style.display = 'block';
    });

    $('gcal-refresh-btn').addEventListener('click', async () => {
      toast('Refreshing Google Calendars...');
      const gcal = await api('/api/google/calendars/refresh', { method: 'POST' });
      renderGoogleCalendar(gcal);
      await loadState(false);
      toast('Google Calendars refreshed');
    });

    $('gcal-disconnect-btn').addEventListener('click', async () => {
      const gcal = await api('/api/google/disconnect', { method: 'POST' });
      renderGoogleCalendar(gcal);
      await loadState(false);
      toast('Google Calendar disconnected');
    });

    $('auth-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const token = $('auth-pin-input').value.trim();
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      if (res.ok) {
        $('auth-overlay').classList.remove('visible');
        await loadState();
      } else {
        $('auth-error').textContent = 'Incorrect access PIN / token.';
      }
    });
  }

  async function init() {
    wireEvents();
    const params = new URLSearchParams(location.search);
    if (params.get('google') === 'connected') {
      switchTab('apps');
      toast('Google Calendar connected!');
      history.replaceState({}, '', '/app');
    } else if (params.get('google') === 'error') {
      switchTab('apps');
      toast('Google connect error: ' + (params.get('reason') || 'check credentials'));
      history.replaceState({}, '', '/app');
    }
    const auth = await fetch('/api/auth/status').then((r) => r.json());
    if (auth.auth_enabled && !auth.authenticated) {
      $('auth-overlay').classList.add('visible');
      return;
    }
    await loadState();
  }

  init().catch((err) => console.error(err));
})();

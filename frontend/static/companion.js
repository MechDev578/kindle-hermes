/* KindleHermes Cross-Device Companion PWA (iOS / macOS / Windows) */
(function () {
  let appState = null;
  let activeTaskListFilter = '';

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

  function renderTasks(tasks, tasksMeta) {
    const list = $('task-list');
    if (!list) return;
    const meta = tasksMeta || (appState && appState.tasks_meta) || { source: 'local', lists: [], needs_reconnect: false };
    const isGoogle = meta.source === 'google';
    const googleLists = meta.lists || [];

    const sourceBadge = $('tasks-source-badge');
    if (sourceBadge) {
      sourceBadge.textContent = isGoogle ? 'GOOGLE TASKS · 2-WAY' : 'LOCAL';
      sourceBadge.style.background = isGoogle ? '#111' : '';
      sourceBadge.style.color = isGoogle ? '#fff' : '';
    }
    const syncBtn = $('sync-tasks-btn');
    if (syncBtn) syncBtn.style.display = isGoogle ? 'inline-block' : 'none';

    const reconnectBanner = $('tasks-reconnect-banner');
    if (reconnectBanner) {
      reconnectBanner.style.display = meta.needs_reconnect ? 'block' : 'none';
    }

    // List dropdown for adding new tasks in Google Tasks mode
    const listSelect = $('new-task-list');
    const prioSelect = $('new-task-priority');
    if (listSelect) {
      if (isGoogle && googleLists.length) {
        const prevVal = listSelect.value;
        listSelect.innerHTML = '';
        googleLists.forEach((tl) => {
          const opt = document.createElement('option');
          opt.value = tl.id;
          opt.textContent = tl.title;
          listSelect.appendChild(opt);
        });
        const preferred =
          (activeTaskListFilter && googleLists.some((tl) => tl.id === activeTaskListFilter) && activeTaskListFilter) ||
          (prevVal && googleLists.some((tl) => tl.id === prevVal) && prevVal) ||
          meta.default_list_id ||
          googleLists[0].id;
        listSelect.value = preferred;
        listSelect.style.display = 'inline-block';
      } else {
        listSelect.style.display = 'none';
      }
    }
    if (prioSelect) {
      prioSelect.style.display = isGoogle ? 'none' : 'inline-block';
    }

    // Reset active filter if list no longer exists
    if (activeTaskListFilter && !googleLists.some((tl) => tl.id === activeTaskListFilter)) {
      activeTaskListFilter = '';
    }

    // Filter pills when >= 2 Google Task Lists are active
    const tabsEl = $('task-list-tabs');
    if (tabsEl) {
      if (isGoogle && googleLists.length >= 2) {
        tabsEl.style.display = 'flex';
        tabsEl.innerHTML = '';
        const openTotal = (tasks || []).filter((t) => !t.completed).length;
        const makePill = (label, listId) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-sm' + (activeTaskListFilter === listId ? ' btn-primary' : '');
          btn.textContent = label;
          btn.addEventListener('click', () => {
            activeTaskListFilter = listId;
            if (listSelect && listId) listSelect.value = listId;
            renderTasks(appState ? appState.tasks : tasks, meta);
          });
          tabsEl.appendChild(btn);
        };
        makePill(`All (${openTotal})`, '');
        googleLists.forEach((tl) => {
          makePill(`${tl.title} (${tl.open_count || 0})`, tl.id);
        });
      } else {
        tabsEl.style.display = 'none';
      }
    }

    const visibleTasks = (tasks || []).filter((t) => {
      if (!isGoogle || !activeTaskListFilter) return true;
      return t.google_list_id === activeTaskListFilter;
    });

    if (!visibleTasks.length) {
      const emptyMsg = isGoogle
        ? 'No tasks in this Google Tasks list. Add one above to sync to Google Tasks & Kindle.'
        : 'No priorities yet. Add one above to sync to your Kindle.';
      list.innerHTML = `<div style="color:#5c5b57;font-size:14px;padding:8px 0;">${emptyMsg}</div>`;
      return;
    }

    list.innerHTML = '';
    visibleTasks.forEach((t) => {
      const row = document.createElement('div');
      row.className = 'task-item' + (t.completed ? ' done' : '');
      if (t.is_subtask) {
        row.style.marginLeft = '24px';
        row.style.borderLeft = '2px solid #d7d4cb';
        row.style.paddingLeft = '10px';
      }

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = Boolean(t.completed);
      cb.style.cssText = 'width:20px;height:20px;cursor:pointer;flex-shrink:0;';
      cb.addEventListener('change', async () => {
        try {
          const data = await api(`/api/tasks/${t.id}/toggle`, { method: 'POST' });
          if (appState) {
            appState.tasks = data.tasks;
            appState.tasks_meta = data.tasks_meta;
          }
          renderTasks(data.tasks, data.tasks_meta);
          toast(isGoogle ? 'Synced with Google Tasks & Kindle' : 'Synced to Kindle');
        } catch (err) {
          cb.checked = Boolean(t.completed);
          toast('Sync error: ' + err.message);
        }
      });

      const body = document.createElement('div');
      body.className = 'task-title';
      body.style.cssText = 'flex:1;min-width:0;';

      const titleLine = document.createElement('div');
      titleLine.textContent = (t.is_subtask ? '↳ ' : '') + (t.title || '');
      body.appendChild(titleLine);

      if (t.notes) {
        const noteEl = document.createElement('div');
        noteEl.style.cssText = 'font-size:12px;color:#5c5b57;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
        noteEl.textContent = t.notes;
        body.appendChild(noteEl);
      }

      const badges = document.createElement('div');
      badges.style.cssText = 'display:flex;align-items:center;gap:6px;flex-shrink:0;';

      if (t.due_badge) {
        const duePill = document.createElement('span');
        duePill.className = 'prio-pill';
        duePill.textContent = t.due_badge;
        if (t.due_urgent) {
          duePill.style.background = '#111';
          duePill.style.color = '#fff';
        }
        badges.appendChild(duePill);
      }

      if (isGoogle) {
        if (!activeTaskListFilter && t.google_list_title && googleLists.length >= 2) {
          const listPill = document.createElement('span');
          listPill.className = 'prio-pill';
          listPill.textContent = t.google_list_title.slice(0, 14);
          badges.appendChild(listPill);
        }
      } else {
        const prioPill = document.createElement('span');
        prioPill.className = 'prio-pill';
        prioPill.textContent = `P${t.priority}`;
        badges.appendChild(prioPill);
      }

      const delBtn = document.createElement('button');
      delBtn.className = 'btn btn-sm btn-danger';
      delBtn.type = 'button';
      delBtn.textContent = 'Delete';
      delBtn.addEventListener('click', async () => {
        try {
          const data = await api(`/api/tasks/${t.id}`, { method: 'DELETE' });
          if (appState) {
            appState.tasks = data.tasks;
            appState.tasks_meta = data.tasks_meta;
          }
          renderTasks(data.tasks, data.tasks_meta);
          toast(isGoogle ? 'Deleted from Google Tasks' : 'Task removed');
        } catch (err) {
          toast('Delete failed: ' + err.message);
        }
      });

      row.append(cb, body, badges, delBtn);
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
    el.innerHTML = '';
    if (cal.current_event) {
      const hero = document.createElement('div');
      hero.style.cssText = 'padding:10px 12px;background:#111;color:#fff;border-radius:8px;margin-bottom:10px;';
      const lbl = document.createElement('div');
      lbl.style.cssText = 'font-size:11px;letter-spacing:1.2px;font-weight:800;';
      lbl.textContent = `HAPPENING NOW · ${(cal.current_event.remaining_label || '').toUpperCase()}`;
      const title = document.createElement('div');
      title.style.cssText = 'font-size:16px;font-weight:700;margin-top:3px;';
      title.textContent = cal.current_event.title || '';
      const rng = document.createElement('div');
      rng.style.cssText = 'font-size:13px;opacity:0.85;';
      rng.textContent = cal.current_event.time_range || '';
      hero.append(lbl, title, rng);
      el.appendChild(hero);
    }
    const items = (cal.upcoming_today || []).slice(0, 5);
    if (!items.length) {
      const empty = document.createElement('div');
      empty.style.cssText = 'font-size:14px;color:#5c5b57;';
      empty.textContent = 'No remaining events today.';
      el.appendChild(empty);
      return;
    }
    items.forEach((ev) => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;gap:12px;padding:8px 0;border-bottom:1px solid #e5e2d9;font-size:14px;';
      const timeEl = document.createElement('strong');
      timeEl.style.cssText = 'min-width:56px;font-family:monospace;';
      timeEl.textContent = ev.start_label || '';
      const info = document.createElement('div');
      const t = document.createElement('strong');
      t.textContent = ev.title || '';
      info.appendChild(t);
      if (ev.location) {
        const loc = document.createElement('div');
        loc.style.cssText = 'font-size:12px;color:#5c5b57;';
        loc.textContent = ev.location;
        info.appendChild(loc);
      }
      row.append(timeEl, info);
      el.appendChild(row);
    });
  }

  function renderLockscreenControls(settings) {
    $('ls-mode-select').value = settings.lockscreen_mode || 'smart';
    const enabledSet = new Set(settings.lockscreen_slides || ['smart']);
    const slideDefs = [
      { id: 'smart', title: 'Smart Context-Aware', desc: 'NOW + NEXT UP + Rest of Today + Tasks + Weather' },
      { id: 'deutsch', title: 'Deutsch Immersion (A2→C2)', desc: 'Chunk/Slang of the Day + Famous Quote + Cloze Teaser + Sprachindex' },
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

      // Google Tasks 2-way list picker & scope upgrade banner
      const reconnectBtn = $('gtasks-reconnect-btn');
      const reconnectHint = $('gtasks-reconnect-hint');
      const refreshTasksBtn = $('gtasks-refresh-btn');
      const taskPicker = $('gtasks-list-picker');
      const defaultRow = $('gtasks-default-row');
      const defaultSelect = $('gtasks-default-select');

      if (taskPicker) {
        taskPicker.innerHTML = '';
        if (!gcal.has_tasks_scope) {
          if (reconnectBtn) reconnectBtn.style.display = 'inline-block';
          if (reconnectHint) reconnectHint.style.display = 'block';
          if (refreshTasksBtn) refreshTasksBtn.style.display = 'none';
          if (defaultRow) defaultRow.style.display = 'none';
        } else {
          if (reconnectBtn) reconnectBtn.style.display = 'none';
          if (reconnectHint) reconnectHint.style.display = 'none';
          if (refreshTasksBtn) refreshTasksBtn.style.display = 'inline-block';

          const taskLists = gcal.task_lists || [];
          const selectedTaskSet = new Set(gcal.selected_task_list_ids || []);
          if (!taskLists.length) {
            const msg = document.createElement('div');
            msg.style.cssText = 'font-size:13px;color:#5c5b57;';
            msg.textContent = 'No Google Task lists loaded yet — click Refresh Task Lists above (ensure Google Tasks API is enabled in Google Cloud Console).';
            taskPicker.appendChild(msg);
          } else {
            taskLists.forEach((tl) => {
              const checked = selectedTaskSet.has(tl.id);
              const label = document.createElement('label');
              label.className = 'slide-chip' + (checked ? ' enabled' : '');
              label.innerHTML = `
                <div>
                  <div style="font-weight:700;font-size:14px;"></div>
                  <div style="font-size:12px;color:#5c5b57;">${tl.is_default ? 'Primary task list' : 'Google Task list'}</div>
                </div>
                <input type="checkbox" value="${tl.id}" ${checked ? 'checked' : ''} style="width:20px;height:20px;" />
              `;
              label.querySelector('div > div').textContent = tl.title;
              label.querySelector('input').addEventListener('change', async () => {
                const ids = Array.from(taskPicker.querySelectorAll('input:checked')).map((i) => i.value);
                if (!ids.length) {
                  toast('Keep at least one Google Task list selected');
                  renderGoogleCalendar(gcal);
                  return;
                }
                const updated = await api('/api/google/task-lists', {
                  method: 'PATCH',
                  body: JSON.stringify({ selected_ids: ids }),
                });
                renderGoogleCalendar(updated);
                toast(`Syncing ${ids.length} Google Task list(s)`);
                await loadState(false);
              });
              taskPicker.appendChild(label);
            });
          }

          if (defaultRow && defaultSelect) {
            const activeLists = taskLists.filter((tl) => selectedTaskSet.has(tl.id));
            if (activeLists.length) {
              defaultRow.style.display = 'flex';
              defaultSelect.innerHTML = '';
              activeLists.forEach((tl) => {
                const opt = document.createElement('option');
                opt.value = tl.id;
                opt.textContent = tl.title;
                defaultSelect.appendChild(opt);
              });
              defaultSelect.value = gcal.default_task_list_id || activeLists[0].id;
            } else {
              defaultRow.style.display = 'none';
            }
          }
        }
      }
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

  function speakGerman(text, rate = 1.0) {
    if (!('speechSynthesis' in window)) {
      toast('Browser speech synthesis unavailable on this device');
      return;
    }
    const clean = (text || '').replace(/[¹²³⁴⁵⁶⁷⁸⁹⁰]/g, '').trim();
    if (!clean) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(clean);
    utter.lang = 'de-DE';
    utter.rate = rate;
    const voices = window.speechSynthesis.getVoices() || [];
    const deVoice = voices.find((v) => /^de/i.test(v.lang));
    if (deVoice) utter.voice = deVoice;
    const status = $('de-audio-status');
    utter.onstart = () => { if (status) status.textContent = `SPEAKING ${rate}×`; };
    utter.onend = () => { if (status) status.textContent = 'READY'; };
    utter.onerror = () => { if (status) status.textContent = 'READY'; };
    window.speechSynthesis.speak(utter);
  }

  function renderWritingResult(entry) {
    const box = $('de-writing-result');
    if (!box || !entry) return;
    const fb = entry.feedback || {};
    box.innerHTML = '';
    const card = document.createElement('div');
    card.style.cssText = 'background:#f5f4ef;border:1.5px solid #1a1a1a;border-radius:10px;padding:12px 14px;font-size:14px;line-height:1.45;';

    const top = document.createElement('div');
    top.style.cssText = 'font-weight:800;margin-bottom:6px;';
    top.textContent = `Score: ${fb.score || 80}/100 — ${fb.praise || ''}`;
    card.appendChild(top);

    const recast = document.createElement('div');
    recast.style.cssText = 'background:#111;color:#fff;padding:8px 10px;border-radius:6px;margin-bottom:8px;';
    recast.textContent = `Native Recast: ${entry.recast_de || fb.recast_de || ''}`;
    card.appendChild(recast);

    (fb.fixes || []).forEach((f) => {
      const fixEl = document.createElement('div');
      fixEl.style.cssText = 'font-size:13px;margin-bottom:5px;';
      fixEl.textContent = `• ${f.original || ''} → ${f.better || ''} (${f.why || ''})`;
      card.appendChild(fixEl);
    });

    if (fb.c2_upgrade) {
      const up = document.createElement('div');
      up.style.cssText = 'font-size:13px;color:#444;border-top:1px solid #d7d4cb;padding-top:6px;margin-top:6px;';
      up.textContent = `C2 Elevation: ${fb.c2_upgrade}`;
      card.appendChild(up);
    }

    const listenBtn = document.createElement('button');
    listenBtn.type = 'button';
    listenBtn.className = 'btn btn-sm';
    listenBtn.style.marginTop = '8px';
    listenBtn.textContent = 'Listen to Native Recast';
    listenBtn.addEventListener('click', () => speakGerman(entry.recast_de || fb.recast_de || '', 0.95));
    card.appendChild(listenBtn);
    box.appendChild(card);
  }

  function renderDeutsch(german, settings) {
    if (!german || !$('de-cefr-badge')) return;
    const m = german.mastery || {};
    $('de-cefr-badge').textContent = `${m.cefr_label || 'A2.0'} · ${m.total || 180}/1000`;
    $('de-milestone-line').textContent = `Streak: ${m.streak_days || 0}d · Due: ${m.due_count || 0} cards · Accuracy: ${m.accuracy_pct || 0}% · Next ${m.next_band || 'B1'}: ${m.milestone || ''}`;

    const grid = $('de-strands-grid');
    grid.innerHTML = '';
    (m.strands || []).forEach((st) => {
      const cell = document.createElement('div');
      cell.style.cssText = 'border:1px solid #d7d4cb;border-radius:8px;padding:8px 10px;background:#faf9f5;';
      const hdr = document.createElement('div');
      hdr.style.cssText = 'display:flex;justify-content:space-between;font-size:12px;font-weight:800;margin-bottom:5px;';
      const l = document.createElement('span');
      l.textContent = st.label;
      const r = document.createElement('span');
      r.textContent = `${st.score} / ${st.max}`;
      hdr.append(l, r);
      const bar = document.createElement('div');
      bar.style.cssText = 'height:8px;background:#e5e2d9;border-radius:4px;overflow:hidden;';
      const fill = document.createElement('div');
      fill.style.cssText = `height:100%;width:${st.pct || 0}%;background:#111;`;
      bar.appendChild(fill);
      cell.append(hdr, bar);
      grid.appendChild(cell);
    });

    if (settings) {
      $('de-level-select').value = (settings.german_level || 'A2').toUpperCase();
      $('de-daily-goal').value = String(settings.german_daily_goal || 15);
    }

    const cl = german.active_classic;
    const audioBox = $('de-audio-preview');
    if (audioBox && cl) {
      audioBox.innerHTML = '';
      const h = document.createElement('div');
      h.style.fontWeight = '800';
      h.textContent = `${cl.author} (${cl.year}) — ${cl.title}`;
      const q = document.createElement('div');
      q.style.cssText = 'margin:4px 0;font-style:italic;';
      q.textContent = cl.famous_line || '';
      const tip = document.createElement('div');
      tip.style.cssText = 'font-size:12px;color:#5c5b57;';
      tip.textContent = `Drop in conversation: ${cl.conversation_tip || ''}`;
      audioBox.append(h, q, tip);
    }

    const chips = $('de-sprint-chips');
    if (chips) {
      chips.innerHTML = '';
      (german.sprint_chunks || []).forEach((ch) => {
        const pill = document.createElement('button');
        pill.type = 'button';
        pill.className = 'btn btn-sm';
        pill.textContent = ch;
        pill.addEventListener('click', () => {
          const ta = $('de-writing-input');
          ta.value = (ta.value ? ta.value + ' ' : '') + ch;
          ta.focus();
        });
        chips.appendChild(pill);
      });
    }

    if (german.recent_writings && german.recent_writings.length && !$('de-writing-result').hasChildNodes()) {
      renderWritingResult(german.recent_writings[0]);
    }

    const classicsList = $('de-classics-list');
    const classics = german.classics || [];
    $('de-classics-count').textContent = `${classics.length} TEXTS`;
    if (classicsList) {
      classicsList.innerHTML = '';
      classics.forEach((item) => {
        const row = document.createElement('div');
        row.className = 'task-item';
        row.style.alignItems = 'flex-start';

        const info = document.createElement('div');
        info.style.flex = '1';
        const title = document.createElement('div');
        title.style.fontWeight = '700';
        title.textContent = `${item.author} (${item.year}) · ${item.title} [${item.cefr}]`;
        const quote = document.createElement('div');
        quote.style.cssText = 'font-size:13px;margin:3px 0;';
        quote.textContent = item.famous_line || '';
        const tip = document.createElement('div');
        tip.style.cssText = 'font-size:12px;color:#5c5b57;';
        tip.textContent = item.conversation_tip || '';
        info.append(title, quote, tip);

        const acts = document.createElement('div');
        acts.className = 'row-wrap';
        const playBtn = document.createElement('button');
        playBtn.type = 'button';
        playBtn.className = 'btn btn-sm';
        playBtn.textContent = 'Listen';
        playBtn.addEventListener('click', () => {
          const txt = (item.paragraphs || []).map((p) => p.de).join(' ');
          speakGerman(txt, 0.95);
        });
        const mineBtn = document.createElement('button');
        mineBtn.type = 'button';
        mineBtn.className = 'btn btn-sm';
        mineBtn.textContent = item.mined ? 'Mined ✓' : '+ FSRS';
        mineBtn.addEventListener('click', async () => {
          const res = await api(`/api/german/classics/${item.id}/mine`, { method: 'POST' });
          renderDeutsch(res.german, appState.settings);
          toast(`Added ${res.added} chunk(s) to Kindle FSRS`);
        });
        acts.append(playBtn, mineBtn);
        row.append(info, acts);
        classicsList.appendChild(row);
      });
    }
  }

  async function loadState(runAutoSync = true) {
    const data = await api('/api/state');
    appState = data;
    $('status-meta').textContent = `${data.date_long} · ${data.time_str} · ${data.weather.city} ${data.weather.temp}°C`;
    renderTasks(data.tasks, data.tasks_meta);
    renderPomodoro(data.pomodoro);
    renderAgendaPreview(data.calendar);
    renderDeutsch(data.german, data.settings);
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
      const priority = parseInt($('new-task-priority').value, 10) || 1;
      const due_date = $('new-task-due') ? $('new-task-due').value.trim() : '';
      const notes = $('new-task-notes') ? $('new-task-notes').value.trim() : '';
      const listEl = $('new-task-list');
      const list_id = listEl && listEl.style.display !== 'none' ? listEl.value : '';
      if (!title) return;
      try {
        const data = await api('/api/tasks', {
          method: 'POST',
          body: JSON.stringify({ title, priority, due_date, notes, list_id }),
        });
        $('new-task-title').value = '';
        if ($('new-task-due')) $('new-task-due').value = '';
        if ($('new-task-notes')) $('new-task-notes').value = '';
        if (appState) {
          appState.tasks = data.tasks;
          appState.tasks_meta = data.tasks_meta;
        }
        renderTasks(data.tasks, data.tasks_meta);
        const isGoogle = data.tasks_meta && data.tasks_meta.source === 'google';
        toast(isGoogle ? 'Added to Google Tasks & Kindle' : 'Added & synced to Kindle');
      } catch (err) {
        toast('Could not add task: ' + err.message);
      }
    });

    if ($('sync-tasks-btn')) {
      $('sync-tasks-btn').addEventListener('click', async () => {
        toast('Syncing with Google Tasks...');
        try {
          const data = await api('/api/tasks/sync', { method: 'POST' });
          if (appState) {
            appState.tasks = data.tasks;
            appState.tasks_meta = data.tasks_meta;
          }
          renderTasks(data.tasks, data.tasks_meta);
          toast('Google Tasks synced');
        } catch (err) {
          toast('Sync error: ' + err.message);
        }
      });
    }

    $('clear-done-btn').addEventListener('click', async () => {
      const qs = activeTaskListFilter ? ('?list_id=' + encodeURIComponent(activeTaskListFilter)) : '';
      const data = await api('/api/tasks/clear-completed' + qs, { method: 'POST' });
      if (appState) {
        appState.tasks = data.tasks;
        appState.tasks_meta = data.tasks_meta;
      }
      renderTasks(data.tasks, data.tasks_meta);
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

    // ---------- Hermes Deutsch Events ----------
    $('de-save-cfg-btn').addEventListener('click', async () => {
      const level = $('de-level-select').value;
      const goal = Math.max(5, Math.min(50, parseInt($('de-daily-goal').value, 10) || 15));
      const updated = await api('/api/settings', {
        method: 'PATCH',
        body: JSON.stringify({ german_level: level, german_daily_goal: goal }),
      });
      appState.settings = updated;
      toast(`Deutsch calibrated to ${level} (${goal} cards/day)`);
      await loadState(false);
    });

    $('de-refresh-reader-btn').addEventListener('click', async () => {
      toast('Generating fresh i+1 German reader...');
      const german = await api('/api/german/reader/refresh', { method: 'POST' });
      appState.german = german;
      renderDeutsch(german, appState.settings);
      toast('Daily graded reader refreshed');
    });

    const playActiveClassic = (rate) => {
      const cl = appState && appState.german && appState.german.active_classic;
      if (!cl) return;
      const txt = (cl.paragraphs || []).map((p) => p.de).join(' ');
      speakGerman(txt, rate);
    };
    $('de-play-classic-btn').addEventListener('click', () => playActiveClassic(1.0));
    $('de-play-classic-slow-btn').addEventListener('click', () => playActiveClassic(0.8));
    $('de-play-reader-btn').addEventListener('click', () => {
      const rd = appState && appState.german && appState.german.reader;
      if (!rd) return;
      const txt = (rd.paragraphs || []).map((p) => p.de).join(' ');
      speakGerman(txt, 0.95);
    });
    $('de-stop-audio-btn').addEventListener('click', () => {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      $('de-audio-status').textContent = 'READY';
    });
    $('de-log-shadow-btn').addEventListener('click', async () => {
      const res = await api('/api/german/shadow', { method: 'POST' });
      appState.german = res.german;
      renderDeutsch(res.german, appState.settings);
      toast('Shadowing logged (+8 Output pts)');
    });

    $('de-submit-writing-btn').addEventListener('click', async () => {
      const user_text = $('de-writing-input').value.trim();
      if (user_text.length < 3) {
        toast('Write at least one short German sentence first');
        return;
      }
      toast('Evaluating & crafting native recast...');
      const chunks = (appState && appState.german && appState.german.sprint_chunks) || [];
      const res = await api('/api/german/writing', {
        method: 'POST',
        body: JSON.stringify({ user_text, target_chunks: chunks }),
      });
      appState.german = res.german;
      renderWritingResult(res.entry);
      renderDeutsch(res.german, appState.settings);
      toast('Native recast ready (+18 Output pts)');
    });

    $('de-add-card-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const payload = {
        kind: $('de-card-kind').value,
        cefr: $('de-card-cefr').value,
        category: $('de-card-kind').value === 'slang' ? 'Umgangssprache' : 'Eigene Karte',
        prompt_de: $('de-card-prompt').value.trim(),
        answer_de: $('de-card-answer').value.trim(),
        full_de: $('de-card-full').value.trim(),
        hint_en: $('de-card-hint').value.trim(),
        translation_en: $('de-card-en').value.trim(),
        rule_note: $('de-card-rule').value.trim(),
      };
      const res = await api('/api/german/cards', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      appState.german = res.german;
      e.target.reset();
      renderDeutsch(res.german, appState.settings);
      toast('Card added to Kindle FSRS deck');
    });

    $('de-import-classic-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      toast('Building Kindle study packet...');
      const res = await api('/api/german/classics/import', {
        method: 'POST',
        body: JSON.stringify({
          title: $('de-imp-title').value.trim(),
          author: $('de-imp-author').value.trim() || 'Eigene Sammlung',
          german_text: $('de-imp-de').value.trim(),
          english_translation: $('de-imp-en').value.trim(),
        }),
      });
      appState.german = res.german;
      e.target.reset();
      renderDeutsch(res.german, appState.settings);
      toast('Study packet added — tap SYNC on Kindle!');
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

    if ($('gtasks-refresh-btn')) {
      $('gtasks-refresh-btn').addEventListener('click', async () => {
        toast('Refreshing Google Task Lists...');
        try {
          const gcal = await api('/api/google/task-lists/refresh', { method: 'POST' });
          renderGoogleCalendar(gcal);
          await loadState(false);
          toast('Google Task Lists synced');
        } catch (err) {
          toast('Task list refresh failed: ' + err.message);
        }
      });
    }

    if ($('gtasks-default-select')) {
      $('gtasks-default-select').addEventListener('change', async () => {
        const picker = $('gtasks-list-picker');
        const ids = picker ? Array.from(picker.querySelectorAll('input:checked')).map((i) => i.value) : [];
        const updated = await api('/api/google/task-lists', {
          method: 'PATCH',
          body: JSON.stringify({ selected_ids: ids, default_list_id: $('gtasks-default-select').value }),
        });
        renderGoogleCalendar(updated);
        toast('Default Google Task list updated');
        await loadState(false);
      });
    }

    $('gcal-disconnect-btn').addEventListener('click', async () => {
      const gcal = await api('/api/google/disconnect', { method: 'POST' });
      renderGoogleCalendar(gcal);
      await loadState(false);
      toast('Google Calendar & Tasks disconnected');
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
      toast('Google Calendar & Tasks connected!');
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

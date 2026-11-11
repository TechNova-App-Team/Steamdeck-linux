/* NovaDeck hub UI. Vanilla JS, no build step.
   Works with D-pad/keys (Steam Deck desktop controls send arrow keys, Enter
   and Escape), a raw gamepad, the touchscreen and the trackpads. */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const TOKEN = $('meta[name="nova-token"]').content;

  let ICONS = {};
  let S = null; // full state from /api/state
  let STATUS = null; // live system status
  const APPS = new Map();
  const ui = {
    focus: null,
    layers: [],
    rowMemory: new Map(),
    heroKey: null,
    heroTimer: 0,
    lastKeyAt: -1e9,
    padMirrored: false,
    padVerified: false,
    launching: false,
    dragging: null,
    statusFails: 0,
    pollTimer: 0,
    jobsSeen: {},
    updRunning: false,
    focusAt: 0,
    lastScrollAt: -1e9,
    pointerType: 'mouse',
    titleTimer: 0,
    updTimer: 0,
    updFocusMoved: false,
    selfUpdating: false,
  };

  // ---------------------------------------------------------------- utils
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const smooth = () => (S && S.settings.reducedEffects ? 'auto' : 'smooth');
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function icon(name, cls = '') {
    const body = ICONS[name] || ICONS.globe || '';
    return `<svg class="i ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }
  function hydrateIcons(root = document) {
    $$('[data-icon]', root).forEach((el) => {
      if (!el.firstElementChild) el.innerHTML = icon(el.dataset.icon);
    });
  }
  function bytes(n) {
    if (n >= 1e12) return `${(n / 1e12).toLocaleString('de-DE', { maximumFractionDigits: 1 })} TB`;
    return `${(n / 1e9).toLocaleString('de-DE', { maximumFractionDigits: n < 1e10 ? 1 : 0 })} GB`;
  }

  function ago(ts) {
    if (!ts) return 'noch nie';
    const min = Math.round((Date.now() / 1000 - ts) / 60);
    if (min < 1) return 'gerade eben';
    if (min < 60) return `vor ${min} Min.`;
    const h = Math.round(min / 60);
    return h < 24 ? `vor ${h} Std.` : `vor ${Math.round(h / 24)} Tg.`;
  }
  const updatesNow = () => (STATUS && STATUS.updates) || (S && S.updates) || null;
  function updatesText() {
    const u = updatesNow();
    if (!u) return { sub: 'Apps & NovaDeck aktuell halten', count: 0 };
    if (u.running) return { sub: u.running.step || 'Wird aktualisiert …', count: 0, busy: true };
    if (u.checking && !u.checkedAt) return { sub: 'Suche nach Updates …', count: 0, busy: true };
    if (u.count) return { sub: u.count === 1 ? '1 Update bereit' : `${u.count} Updates bereit`, count: u.count };
    if (u.error) return { sub: 'Gerade offline', count: 0 };
    return { sub: u.checkedAt ? 'Alles aktuell' : 'Apps & NovaDeck aktuell halten', count: 0 };
  }

  // ---------------------------------------------------------------- api
  async function request(method, path, body) {
    const headers = { 'X-Nova-Token': TOKEN };
    if (body) headers['Content-Type'] = 'application/json';
    const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined, cache: 'no-store' });
    let data = null;
    try { data = await res.json(); } catch { /* empty */ }
    if (!res.ok) {
      const err = new Error((data && data.message) || `Fehler ${res.status}`);
      err.code = data && data.error;
      throw err;
    }
    return data;
  }
  const api = { get: (p) => request('GET', p), post: (p, b = {}) => request('POST', p, b) };

  // ---------------------------------------------------------------- model
  function indexApps() {
    APPS.clear();
    S.apps.forEach((a) => APPS.set(a.id, a));
  }
  const catName = (id) => (S.categories.find((c) => c.id === id) || {}).name || '';
  const jobFor = (app) => (S.jobs || {})[app.installId || app.flatpak] || null;
  const isFav = (id) => S.favorites.includes(id);

  function rowsModel() {
    const hidden = new Set(S.hidden);
    const visible = S.apps.filter((a) => !hidden.has(a.id));
    const byId = new Map(visible.map((a) => [a.id, a]));
    const rows = [{ id: 'fav', name: 'Favoriten', apps: S.favorites.map((id) => byId.get(id)).filter(Boolean), add: true }];
    S.categories.forEach((c) => rows.push({ id: c.id, name: c.name, apps: visible.filter((a) => a.category === c.id) }));
    return rows.filter((r) => r.apps.length || r.add);
  }

  function kindLabel(app) {
    const job = jobFor(app);
    if (job && job.status === 'running') return 'Wird installiert';
    if (!app.available) return app.reason || 'Nicht verfügbar';
    switch (app.kind) {
      case 'flatpak': return 'App';
      case 'web': return 'Web-App';
      case 'browser': return S.browser ? S.browser.name : 'Browser';
      case 'uri': return 'Steam';
      default: return 'System';
    }
  }

  // ---------------------------------------------------------------- render
  function tileHTML(app, rowId) {
    if (app.kind === 'updates') return updatesTileHTML(app, rowId);
    const job = jobFor(app);
    const busy = job && job.status === 'running';
    let badges = '';
    if (busy) badges += `<span class="badge">${icon('download')}Lädt</span>`;
    else if (!app.available && app.installId) badges += `<span class="badge">${icon('download')}Installieren</span>`;
    else if (!app.available) badges += '<span class="badge">Nicht da</span>';
    if (rowId !== 'fav' && isFav(app.id)) badges += `<span class="badge fav" title="Favorit">${icon('star')}</span>`;
    return `<button class="tile${app.available ? '' : ' is-missing'}${busy ? ' is-busy' : ''}" data-nav data-tile data-id="${esc(app.id)}" data-row="${rowId}" aria-label="${esc(app.name)}">
      <span class="ico">${icon(app.icon)}</span>
      <span class="badges">${badges}</span>
      <span class="label"><span class="name">${esc(app.name)}</span><span class="sub">${esc(app.subtitle)}</span></span>
    </button>`;
  }

  function updatesTileHTML(app, rowId) {
    const t = updatesText();
    const badges = t.count ? `<span class="badge count">${t.count}</span>` : '';
    return `<button class="tile${t.busy ? ' is-busy' : ''}" data-nav data-tile data-id="${esc(app.id)}" data-row="${rowId}" aria-label="${esc(app.name)}">
      <span class="ico">${icon(app.icon)}</span>
      <span class="badges">${badges}</span>
      <span class="label"><span class="name">${esc(app.name)}</span><span class="sub">${esc(t.sub)}</span></span>
    </button>`;
  }

  function refreshUpdatesTiles() {
    const app = APPS.get('updates');
    if (!app) return;
    const t = updatesText();
    $$('.tile[data-id="updates"]').forEach((tile) => {
      $('.sub', tile).textContent = t.sub;
      $('.badges', tile).innerHTML = t.count ? `<span class="badge count">${t.count}</span>` : '';
      tile.classList.toggle('is-busy', !!t.busy);
      if (tile === ui.focus) updateHero(tile);
    });
    const label = $('#updates-label');
    if (label) label.textContent = t.count ? `${t.count} Updates` : 'Updates';
  }

  function render() {
    const keep = ui.focus && ui.focus.matches('[data-tile]') ? { id: ui.focus.dataset.id || ui.focus.dataset.action, row: ui.focus.dataset.row } : null;
    const rows = rowsModel();
    $('#rows').innerHTML = rows.map((r) => `
      <section class="row" data-row-id="${r.id}">
        <div class="row-head">${esc(r.name)}<span class="count">${r.apps.length}</span></div>
        <div class="track">${r.apps.map((a) => tileHTML(a, r.id)).join('')}${r.add ? `
          <button class="tile add" data-nav data-tile data-action="add" data-row="${r.id}" aria-label="Kachel hinzufügen">
            <span class="ico">${icon('plus')}</span><span class="name">Kachel hinzufügen</span>
          </button>` : ''}</div>
      </section>`).join('');
    $('#tabs').innerHTML = rows.map((r) => `<button class="tab" data-nav data-tab="${r.id}">${esc(r.name)}</button>`).join('');
    if (keep && !ui.layers.length) {
      const again = findTile(keep.id, keep.row) || findTile(keep.id);
      setFocus(again || rowDefault($('.row')), { noScroll: !!again });
    } else if (ui.focus && !document.contains(ui.focus)) {
      ui.focus = null;
    }
    const active = ui.focus && ui.focus.closest('.row');
    markActiveRow(active || $('.row'));
  }

  function findTile(id, rowId) {
    return $$('.tile').find((t) => (t.dataset.id || t.dataset.action) === id && (!rowId || t.dataset.row === rowId)) || null;
  }

  function markActiveRow(row) {
    if (!row) return;
    $$('.row.is-active').forEach((r) => r !== row && r.classList.remove('is-active'));
    row.classList.add('is-active');
    $$('.tab').forEach((t) => t.classList.toggle('is-current', t.dataset.tab === row.dataset.rowId));
  }

  // ---------------------------------------------------------------- hero
  function heroModel(tile) {
    if (tile.dataset.action === 'add') {
      return {
        key: 'add', icon: 'plus', name: 'Kachel hinzufügen', eyebrow: 'Eigene Web-App',
        info: 'Mach aus jeder Website eine App-Kachel: ARD Mediathek, Joyn, WhatsApp Web und alles andere.',
        chips: `<button class="chip solid" data-hero-start><kbd>A</kbd>Hinzufügen</button>`,
      };
    }
    const app = APPS.get(tile.dataset.id);
    if (!app) return null;
    if (app.kind === 'updates') {
      const u = updatesNow();
      const t = updatesText();
      let info = app.info;
      if (u && u.running) info = `${u.running.step} Du kannst währenddessen weiter alles benutzen.`;
      else if (u && u.count) {
        const names = u.apps.map((a) => a.name);
        if (u.self.available) names.unshift(`NovaDeck ${u.self.latest}`);
        info = `Bereit: ${names.slice(0, 4).join(', ')}${u.others ? `${names.length ? ' und ' : ''}${u.others} Systemkomponente${u.others === 1 ? '' : 'n'}` : ''}.`;
      } else if (u && u.checkedAt) info = `Alles ist aktuell. Zuletzt geprüft ${ago(u.checkedAt)}.`;
      return {
        key: `updates|${t.sub}|${info}`, icon: app.icon, name: app.name, eyebrow: `Werkzeuge · NovaDeck ${S.version}`, info,
        chips: `<button class="chip solid" data-hero-start><kbd>A</kbd>${u && u.count ? 'Ansehen & aktualisieren' : 'Öffnen'}</button>`,
      };
    }
    const job = jobFor(app);
    const eyebrow = app.id === S.last && app.available ? `Zuletzt gestartet · ${kindLabel(app)}` : `${catName(app.category)} · ${kindLabel(app)}`;
    let chips = '';
    if (job && job.status === 'running') chips += `<span class="chip plain">${icon('download')}Wird installiert …</span>`;
    else if (app.available) chips += `<button class="chip solid" data-hero-start><kbd>A</kbd>Starten</button>`;
    else if (app.installId) chips += `<button class="chip solid" data-hero-start><kbd>A</kbd>${app.kind === 'flatpak' ? 'Installieren' : 'Chrome installieren'}</button>`;
    chips += `<span class="chip"><kbd class="pad-trigger">L2</kbd>Optionen</span>`;
    if (isFav(app.id)) chips += `<span class="chip plain">${icon('star')}Favorit</span>`;
    return { key: `${app.id}|${eyebrow}|${chips.length}`, icon: app.icon, name: app.name, eyebrow, info: app.info || app.subtitle, chips };
  }

  function updateHero(tile, force = false) {
    const m = heroModel(tile);
    if (!m || (!force && m.key === ui.heroKey)) return;
    ui.heroKey = m.key;
    // Update right away: any delay here feels like input lag on the Deck.
    $('#hero-icon').innerHTML = icon(m.icon);
    $('#hero-eyebrow').textContent = m.eyebrow;
    $('#hero-title').textContent = m.name;
    $('#hero-info').textContent = m.info;
    $('#hero-chips').innerHTML = m.chips;
  }

  // ---------------------------------------------------------------- focus
  function setFocus(el, opts = {}) {
    if (!el) return;
    if (ui.focus && ui.focus !== el) ui.focus.classList.remove('is-focus');
    if (ui.focus !== el) ui.focusAt = performance.now();
    ui.focus = el;
    el.classList.add('is-focus');
    if (document.activeElement !== el) el.focus({ preventScroll: true });
    if (el.matches('[data-tile]')) {
      const row = el.closest('.row');
      ui.rowMemory.set(row.dataset.rowId, el.dataset.id || el.dataset.action);
      markActiveRow(row);
      if (!opts.noScroll) scrollTileIntoView(el);
      updateHero(el);
    } else if (el.matches('[data-tab]')) {
      const row = $(`.row[data-row-id="${el.dataset.tab}"]`);
      if (row && !opts.noScroll) {
        markActiveRow(row);
        $('#rows').scrollTo({ top: row.offsetTop - 4, behavior: smooth() });
      }
    } else if (!opts.noScroll && el.closest('.drawer-body')) {
      el.scrollIntoView({ block: 'nearest', behavior: smooth() });
    }
    if (!el.matches('[data-power]')) $$('.power-btn.is-armed').forEach(disarm);
  }

  // Smooth scrolling only for single steps; while a direction is held (or the
  // moves come quickly) jump directly so the focus never lags behind the input.
  function scrollMode() {
    const now = performance.now();
    const quick = now - (ui.lastMoveAt || 0) < 260;
    ui.lastMoveAt = now;
    return quick || (S && S.settings.reducedEffects) ? 'auto' : 'smooth';
  }

  function scrollTileIntoView(tile) {
    const behavior = scrollMode();
    const track = tile.parentElement;
    const pad = parseFloat(getComputedStyle(track).paddingLeft) || 40;
    const left = tile.offsetLeft;
    const right = left + tile.offsetWidth;
    let target = track.scrollLeft;
    if (left - pad < track.scrollLeft) target = left - pad;
    else if (right + pad > track.scrollLeft + track.clientWidth) target = right + pad - track.clientWidth;
    if (Math.abs(target - track.scrollLeft) > 1) track.scrollTo({ left: target, behavior });
    const rows = $('#rows');
    const top = tile.closest('.row').offsetTop - 4;
    if (Math.abs(rows.scrollTop - top) > 2) rows.scrollTo({ top, behavior });
  }

  const visible = (el) => !el.disabled && el.getClientRects().length > 0 && !el.closest('[hidden]');
  function scope() {
    const top = ui.layers[ui.layers.length - 1];
    return top ? top.el : null;
  }
  function focusables(root) {
    if (root) return $$('[data-nav]', root).filter(visible);
    return $$('[data-nav]').filter((el) => visible(el) && !el.closest('.layer, .offline'));
  }

  function rowDefault(row, from) {
    if (!row) return null;
    const tiles = $$('.tile', row);
    const remembered = ui.rowMemory.get(row.dataset.rowId);
    const hit = remembered && tiles.find((t) => (t.dataset.id || t.dataset.action) === remembered);
    if (hit) return hit;
    if (from) return nearestBy(from, tiles, (a, b) => Math.abs(a.left + a.width / 2 - (b.left + b.width / 2)));
    return tiles[0] || null;
  }

  function nearestBy(from, cands, metric) {
    const a = from.getBoundingClientRect();
    let best = null;
    let bestScore = Infinity;
    cands.forEach((el) => {
      const score = metric(a, el.getBoundingClientRect());
      if (score < bestScore) { bestScore = score; best = el; }
    });
    return best;
  }

  function nearestInDirection(from, dir, cands) {
    const a = from.getBoundingClientRect();
    const ax = a.left + a.width / 2;
    const ay = a.top + a.height / 2;
    const horizontal = dir === 'left' || dir === 'right';
    let best = null;
    let bestScore = Infinity;
    cands.forEach((el) => {
      if (el === from) return;
      const b = el.getBoundingClientRect();
      const bx = b.left + b.width / 2;
      const by = b.top + b.height / 2;
      const primary = { left: ax - bx, right: bx - ax, up: ay - by, down: by - ay }[dir];
      if (primary <= 2) return;
      const overlaps = horizontal ? b.top < a.bottom && b.bottom > a.top : b.left < a.right && b.right > a.left;
      const secondary = overlaps ? 0 : horizontal ? Math.abs(ay - by) : Math.abs(ax - bx);
      const score = primary + secondary * (horizontal ? 3 : 1.4);
      if (score < bestScore) { bestScore = score; best = el; }
    });
    return best;
  }

  function bump(el, dir) {
    if (!el.matches('.tile')) return;
    const cls = dir === 'left' ? 'bump-l' : 'bump-r';
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
    setTimeout(() => el.classList.remove(cls), 300);
  }

  function move(dir) {
    const layer = scope();
    let cur = ui.focus;
    if (!cur || !document.contains(cur) || !visible(cur) || (layer ? !layer.contains(cur) : cur.closest('.layer'))) {
      return focusDefault();
    }
    if (!layer && cur.matches('[data-tile]')) {
      if (dir === 'left' || dir === 'right') {
        const sib = dir === 'left' ? cur.previousElementSibling : cur.nextElementSibling;
        return sib ? setFocus(sib) : bump(cur, dir);
      }
      const rows = $$('.row');
      const next = rows[rows.indexOf(cur.closest('.row')) + (dir === 'down' ? 1 : -1)];
      if (next) return setFocus(rowDefault(next, cur));
      if (dir === 'up') return setFocus($('.tab.is-current') || focusables().find((el) => el.closest('.topbar')));
      return undefined;
    }
    if (!layer && cur.closest('.topbar') && dir === 'down') {
      const rowId = cur.matches('[data-tab]') ? cur.dataset.tab : ($('.tab.is-current') || {}).dataset?.tab;
      const row = $(`.row[data-row-id="${rowId}"]`) || $('.row');
      return setFocus(rowDefault(row));
    }
    const target = nearestInDirection(cur, dir, focusables(layer));
    if (target) setFocus(target);
    return undefined;
  }

  function focusDefault() {
    const layer = scope();
    if (layer) return setFocus(focusables(layer)[0]);
    const active = $('.row.is-active') || $('.row');
    return setFocus(rowDefault(active));
  }

  function jumpRow(delta) {
    if (scope()) return;
    const rows = $$('.row');
    const cur = (ui.focus && ui.focus.closest('.row')) || $('.row.is-active') || rows[0];
    const next = rows[Math.max(0, Math.min(rows.length - 1, rows.indexOf(cur) + delta))];
    if (next) setFocus(rowDefault(next));
  }

  function goRow(rowId) {
    const row = $(`.row[data-row-id="${rowId}"]`);
    if (row) setFocus(rowDefault(row));
  }

  // ---------------------------------------------------------------- layers
  function openLayer(el, opts = {}) {
    if (ui.layers.some((l) => l.el === el)) return;
    el.hidden = false;
    hydrateIcons(el);
    ui.layers.push({ el, returnTo: ui.focus, onClose: opts.onClose });
    const first = (opts.focus && visible(opts.focus) && opts.focus) || focusables(el)[0];
    if (first) setFocus(first, { noScroll: true });
  }

  function closeLayer() {
    const layer = ui.layers.pop();
    if (!layer) return false;
    layer.el.hidden = true;
    if (layer.onClose) layer.onClose();
    const back = layer.returnTo;
    if (back && document.contains(back) && visible(back)) setFocus(back, { noScroll: true });
    else focusDefault();
    return true;
  }

  function closeAllLayers() {
    while (ui.layers.length) closeLayer();
  }

  function back() {
    if (closeLayer()) return;
    if (ui.focus && ui.focus.closest('.topbar')) return focusDefault();
    const first = $('.row');
    if (first && ui.focus && ui.focus.closest('.row') !== first) return setFocus(rowDefault(first));
    return undefined;
  }

  // ---------------------------------------------------------------- toasts & overlays
  function toast(iconName, text, ms = 3600) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.innerHTML = `${icon(iconName)}<span>${esc(text)}</span>`;
    $('#toasts').appendChild(el);
    while ($('#toasts').children.length > 3) $('#toasts').firstElementChild.remove();
    setTimeout(() => { el.classList.add('is-out'); setTimeout(() => el.remove(), 320); }, ms);
  }

  function showLaunch(iconName, title, sub) {
    ui.launching = true;
    $('#launch-icon').innerHTML = icon(iconName);
    $('#launch-title').textContent = title;
    $('#launch-sub').textContent = sub;
    $('#launch').hidden = false;
  }
  function hideLaunch() {
    ui.launching = false;
    $('#launch').hidden = true;
  }

  // ---------------------------------------------------------------- actions
  function press(tile) {
    if (!tile || !tile.matches('.tile')) return;
    tile.classList.add('is-pressed');
    setTimeout(() => tile.classList.remove('is-pressed'), 140);
  }

  async function launchApp(id, tile) {
    const app = APPS.get(id);
    if (!app) return;
    if (app.kind === 'updates') { press(tile); return openUpdates(); }
    const job = jobFor(app);
    if (job && job.status === 'running') return toast('download', `${job.label} wird noch installiert …`);
    if (!app.available) {
      if (app.installId) return installApp(app);
      return toast('info', `${app.name}: ${app.reason || 'nicht verfügbar'}`);
    }
    press(tile);
    if (app.keepHub) {
      try {
        await api.post('/api/launch', { id });
        toast(app.icon, app.id === 'keyboard' ? 'Tastatur eingeblendet. Klappt auch mit Steam + X.' : `${app.name} geöffnet`);
      } catch (e) { toast('info', e.message); }
      return;
    }
    showLaunch(app.icon, app.name, app.kind === 'web' ? 'Öffnet als App-Fenster …' : 'Wird gestartet …');
    markLaunching();
    try {
      const res = await api.post('/api/launch', { id });
      S.last = id;
      if (res.closing) {
        $('#launch-sub').textContent = 'Viel Spaß! Der Hub macht Platz.';
        setTimeout(hideLaunch, 8000);
      } else {
        if (res.returns) $('#launch-sub').textContent = 'Schließ die App, und du bist sofort wieder hier.';
        setTimeout(hideLaunch, 2200);
      }
    } catch (e) {
      hideLaunch();
      toast('info', e.message || 'Start fehlgeschlagen');
    }
  }

  // The KWin helper watches our window title: "NovaDeck · Start" means "an app is
  // starting from the hub", so the hub steps aside and comes back when it closes.
  function markLaunching() {
    document.title = 'NovaDeck · Start';
    clearTimeout(ui.titleTimer);
    ui.titleTimer = setTimeout(() => { document.title = 'NovaDeck'; }, 12000);
  }

  function activateTile(tile) {
    return tile.dataset.action === 'add' ? openAdd() : launchApp(tile.dataset.id, tile);
  }

  async function installApp(app) {
    const label = app.kind === 'flatpak' ? app.name : 'Google Chrome';
    try {
      const res = await api.post('/api/install', { id: app.id });
      S.jobs = S.jobs || {};
      S.jobs[res.installId] = res.job;
      ui.jobsSeen[res.installId] = 'running';
      toast('download', `${label} wird installiert. Das dauert meist 1–3 Minuten.`, 5000);
      render();
      schedulePoll(1200);
    } catch (e) {
      toast('info', e.message);
    }
  }

  async function setFav(app, value) {
    try {
      const res = await api.post('/api/favorite', { id: app.id, value });
      S.favorites = res.favorites;
      render();
      toast('star', value ? `${app.name} ist jetzt ein Favorit` : `${app.name} aus Favoriten entfernt`, 2400);
      if (ui.focus && ui.focus.matches('[data-tile]')) updateHero(ui.focus, true);
    } catch (e) { toast('info', e.message); }
  }

  async function hideApp(app) {
    try {
      const res = await api.post('/api/hide', { id: app.id, value: true });
      S.hidden = res.hidden;
      S.favorites = res.favorites;
      render();
      updateUnhideLabel();
      toast('eye-off', `${app.name} ausgeblendet. Wieder einblenden im Schnellmenü.`);
    } catch (e) { toast('info', e.message); }
  }

  async function removeCustom(app) {
    try {
      await api.post('/api/custom/remove', { id: app.id });
      await reloadState();
      toast('trash', `${app.name} gelöscht`);
    } catch (e) { toast('info', e.message); }
  }

  async function addToSteam(app) {
    try {
      await api.post('/api/steam/add', { id: app.id });
      toast('steam-add', `Steam öffnet die Liste: „${app.steamName || app.name}“ anhaken und hinzufügen.`, 6000);
    } catch (e) { toast('info', e.message); }
  }

  async function reloadState() {
    S = await api.get('/api/state');
    indexApps();
    render();
    updateUnhideLabel();
  }

  // ---------------------------------------------------------------- context menu
  function openCtx(tile, point) {
    if (!tile || !tile.matches('[data-tile]')) return;
    if (tile.dataset.action === 'add') return openAdd();
    const app = APPS.get(tile.dataset.id);
    if (!app) return;
    closeAllLayers();
    setFocus(tile, { noScroll: true });
    const items = [];
    if (app.available) items.push(['play', 'Starten', () => launchApp(app.id, tile)]);
    else if (app.installId) items.push(['download', app.kind === 'flatpak' ? 'Installieren' : 'Chrome installieren', () => installApp(app)]);
    items.push(isFav(app.id) ? ['star', 'Aus Favoriten entfernen', () => setFav(app, false)] : ['star', 'Zu Favoriten', () => setFav(app, true)]);
    if (app.available && (app.kind === 'web' || app.kind === 'flatpak') && S.capabilities.steam) {
      items.push(['steam-add', 'Zu Steam hinzufügen', () => addToSteam(app)]);
    }
    items.push(app.custom ? ['trash', 'Kachel löschen', () => removeCustom(app)] : ['eye-off', 'Ausblenden', () => hideApp(app)]);
    const ctx = $('#ctx');
    ctx.innerHTML = `<div class="ctx-title">${esc(app.name)}</div>` + items.map((it, i) => `<button data-nav data-ctx="${i}">${icon(it[0])}${esc(it[1])}</button>`).join('');
    ctx._items = items;
    ctx.hidden = false;
    const r = tile.getBoundingClientRect();
    const w = ctx.offsetWidth;
    const h = ctx.offsetHeight;
    let x = point ? point.x : r.left;
    let y = point ? point.y : r.bottom + 10;
    if (y + h > innerHeight - 12) y = Math.max(12, (point ? point.y : r.top - 10) - h);
    x = Math.max(12, Math.min(x, innerWidth - w - 12));
    ctx.style.left = `${x}px`;
    ctx.style.top = `${y}px`;
    openLayer(ctx);
  }

  // ---------------------------------------------------------------- drawer
  function setRange(input, value) {
    input.value = value;
    input.style.setProperty('--p', `${((value - input.min) / (input.max - input.min)) * 100}%`);
  }

  function updateUnhideLabel() {
    const n = S.hidden.length;
    $('#unhide-label').textContent = n ? `${n} ausgeblendet zeigen` : 'Nichts ausgeblendet';
    $('#btn-unhide').disabled = !n;
  }

  function renderDrawer() {
    $$('.toggle[data-setting]').forEach((t) => t.setAttribute('aria-checked', String(!!S.settings[t.dataset.setting])));
    const browsers = S.browsers || [];
    $('#group-browser').hidden = browsers.length < 2;
    $('#seg-browser').innerHTML = browsers.map((b) => `<button data-nav data-browser="${esc(b.id)}" aria-pressed="${S.browser && S.browser.id === b.id}">${esc(b.name)}</button>`).join('');
    updateUnhideLabel();
    $('#drawer-foot').textContent = `NovaDeck ${S.version} · ${S.browser ? S.browser.name : 'kein Browser'}${S.demo ? ' · Demo' : ''}`;
    renderStatus();
  }

  function openDrawer() {
    renderDrawer();
    openLayer($('#drawer'));
    const vol = $('#range-volume');
    setFocus(visible(vol) ? vol : $('#drawer .toggle'), { noScroll: true });
  }

  function openPower() {
    $('#gamemode-label').textContent = S.capabilities.gamemodeLabel || 'Gaming-Modus';
    openLayer($('#power'));
  }

  function disarm(btn) {
    btn.classList.remove('is-armed');
    if (btn._label) $('b', btn).textContent = btn._label;
  }

  async function powerAction(btn) {
    const action = btn.dataset.power;
    if (btn.hasAttribute('data-confirm') && !btn.classList.contains('is-armed')) {
      btn._label = btn._label || $('b', btn).textContent;
      btn.classList.add('is-armed');
      $('b', btn).textContent = 'Nochmal drücken';
      return;
    }
    try {
      if (action === 'desktop') {
        closeAllLayers();
        showLaunch('desktop', 'Bis gleich', 'Den Hub findest du unten in der Leiste.');
        await api.post('/api/hub/close');
        return;
      }
      const titles = { gamemode: [ 'device', S.capabilities.gamemodeLabel || 'Gaming-Modus', 'Einen Moment …' ], suspend: ['moon', 'Gute Nacht', 'Das Deck geht in den Standby.'], reboot: ['restart', 'Neustart', 'Bis gleich!'], poweroff: ['power', 'Ausschalten', 'Bis zum nächsten Mal.'] };
      closeAllLayers();
      const [ic, title, sub] = titles[action];
      showLaunch(ic, title, sub);
      await api.post('/api/power', { action });
      if (action === 'suspend') setTimeout(hideLaunch, 2500);
    } catch (e) {
      hideLaunch();
      toast('info', e.message);
    }
  }

  // ---------------------------------------------------------------- updates
  const FLATPAK_ICONS = { 'com.google.Chrome': 'globe', 'org.prismlauncher.PrismLauncher': 'cube', 'com.spotify.Client': 'bars', 'com.discordapp.Discord': 'headset', 'com.heroicgameslauncher.hgl': 'shield', 'org.videolan.VLC': 'play' };

  function renderUpdates() {
    const u = updatesNow();
    if (!u) return;
    const rows = [];
    rows.push(`<div class="upd-row${u.self.available ? '' : ' is-ok'}"><span>${icon('nova')}</span>
      <div><b>NovaDeck</b><small>${u.self.available ? `${esc(u.self.current)} → ${esc(u.self.latest)}${u.self.notes.length ? ` · ${esc(u.self.notes[0])}` : ''}` : `Version ${esc(u.self.current)}`}</small></div>
      <span class="tag${u.self.available ? ' new' : ''}">${u.self.available ? 'Neu' : 'Aktuell'}</span></div>`);
    u.apps.forEach((a) => rows.push(`<div class="upd-row"><span>${icon(FLATPAK_ICONS[a.id] || 'bag')}</span>
      <div><b>${esc(a.name)}</b><small>${esc(a.id)}</small></div><span class="tag new">Update</span></div>`));
    if (u.others) rows.push(`<div class="upd-row"><span>${icon('sliders')}</span><div><b>Systemkomponenten</b><small>Laufzeiten &amp; Treiber für deine Apps</small></div><span class="tag new">${u.others}</span></div>`);
    if (!u.apps.length && !u.others) rows.push(`<div class="upd-row is-ok"><span>${icon('check')}</span><div><b>Apps</b><small>${u.checking ? 'Suche läuft …' : 'Alle Apps sind aktuell'}</small></div><span class="tag">${u.checking ? '…' : 'Aktuell'}</span></div>`);
    $('#upd-list').innerHTML = rows.join('');

    let sub = u.checking ? 'Suche nach Updates …' : `Zuletzt geprüft ${ago(u.checkedAt)}`;
    if (u.error && !u.checking) sub = u.error;
    if (u.last && !u.running && !u.checking) sub += ` · ${u.last.message}`;
    $('#upd-sub').textContent = sub;
    $('#upd-title').textContent = u.count ? (u.count === 1 ? '1 Update bereit' : `${u.count} Updates bereit`)
      : u.checking ? 'Suche nach Updates …' : u.error ? 'Offline' : 'Alles aktuell';

    const running = !!u.running;
    $('#upd-progress').hidden = !running;
    if (running) $('#upd-step').textContent = u.running.step;
    const run = $('#upd-run');
    // Always allowed: even if the check finds nothing, a real `flatpak update` may.
    run.disabled = running;
    run.textContent = running ? 'Läuft …' : u.count ? 'Alles aktualisieren' : 'Jetzt aktualisieren';
    $('#upd-check').disabled = running || u.checking;
    $$('#updates .toggle[data-setting]').forEach((t) => t.setAttribute('aria-checked', String(!!S.settings[t.dataset.setting])));
    if (scope() && scope().id === 'updates') {
      const closeBtn = $('#updates [data-close]');
      if (ui.focus && ui.focus.disabled) setFocus(run.disabled ? closeBtn : run, { noScroll: true });
      else if (ui.focus === closeBtn && !run.disabled && !ui.updFocusMoved) { ui.updFocusMoved = true; setFocus(run, { noScroll: true }); }
    }
  }

  // Fast polling only while the updates window is open or something is running.
  function ensureUpdatePolling() {
    const u = updatesNow();
    const need = (scope() && scope().id === 'updates') || ui.updRunning || !!(u && (u.checking || u.running));
    if (need && !ui.updTimer) {
      ui.updTimer = setInterval(async () => {
        try {
          STATUS = await api.get('/api/status');
          handleUpdates(STATUS.updates);
        } catch { /* the regular poll shows connection problems */ }
      }, 1000);
    } else if (!need && ui.updTimer) {
      clearInterval(ui.updTimer);
      ui.updTimer = 0;
    }
  }

  function openUpdates() {
    closeAllLayers();
    ui.updFocusMoved = false;
    renderUpdates();
    openLayer($('#updates'), { onClose: () => setTimeout(ensureUpdatePolling, 0) });
    const u = updatesNow();
    setFocus(u && !u.running ? $('#upd-run') : $('#updates [data-close]'), { noScroll: true });
    ensureUpdatePolling();
    if (!u || (!u.checking && !u.running && (!u.checkedAt || Date.now() / 1000 - u.checkedAt > 600))) checkUpdates();
  }

  async function checkUpdates() {
    try {
      await api.post('/api/updates/check');
      if (STATUS && STATUS.updates) STATUS.updates.checking = true;
      renderUpdates();
      ensureUpdatePolling();
    } catch (e) { toast('info', e.message); }
  }

  async function runUpdates() {
    try {
      const res = await api.post('/api/updates/run', { self: true });
      if (STATUS) STATUS.updates = res.updates;
      ui.updRunning = true;
      renderUpdates();
      refreshUpdatesTiles();
      ensureUpdatePolling();
    } catch (e) { toast('info', e.message); }
  }

  function handleUpdates(u) {
    if (!u) return;
    if (u.running && u.running.selfUpdate && !ui.selfUpdating) {
      ui.selfUpdating = true;
      closeAllLayers();
      showLaunch('update', `NovaDeck ${u.self.latest}`, 'Wird installiert. Der Hub startet gleich neu …');
      setTimeout(() => { if (ui.selfUpdating) $('#launch-sub').textContent = 'Dauert länger als gedacht. Du kannst NovaDeck auch neu öffnen.'; }, 90000);
    }
    if (ui.updRunning && !u.running) {
      ui.updRunning = false;
      if (u.last) toast(u.last.ok ? 'check' : 'info', u.last.message, 5000);
    }
    if (u.running) ui.updRunning = true;
    refreshUpdatesTiles();
    if (scope() && scope().id === 'updates') renderUpdates();
    ensureUpdatePolling();
  }

  function showWhatsNew(info) {
    $('#wn-title').textContent = `Neu in NovaDeck ${info.version}`;
    $('#wn-list').innerHTML = (info.notes || []).map((n) => `<li>${esc(n)}</li>`).join('');
    openLayer($('#whatsnew'), { focus: $('#wn-ok') });
  }

  // ---------------------------------------------------------------- add tile
  let addCategory = 'watch';
  function openAdd() {
    closeAllLayers();
    $('#add-error').hidden = true;
    $('#add-name').value = '';
    $('#add-url').value = '';
    addCategory = 'watch';
    $('#add-cat').innerHTML = S.categories.map((c) => `<button type="button" data-nav data-cat="${c.id}" aria-pressed="${c.id === addCategory}">${esc(c.name)}</button>`).join('');
    openLayer($('#add'), { focus: $('#add-name') });
  }

  async function submitAdd() {
    const name = $('#add-name').value.trim();
    const url = $('#add-url').value.trim();
    const err = $('#add-error');
    if (!name || !url) {
      err.textContent = 'Bitte Name und Adresse eingeben.';
      err.hidden = false;
      return;
    }
    try {
      const res = await api.post('/api/custom/add', { name, url, category: addCategory });
      closeLayer();
      await reloadState();
      const tile = findTile(res.id, 'fav');
      if (tile) setFocus(tile);
      toast('check', `${name} ist jetzt eine Kachel`);
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  // ---------------------------------------------------------------- status
  function renderStatus() {
    const st = STATUS;
    if (!st) return;
    const net = st.network;
    const elNet = $('#stat-net');
    elNet.hidden = !net;
    if (net) {
      let ic = 'wifi-off';
      let label = 'Offline';
      if (net.connected) {
        ic = net.type === 'ethernet' ? 'ethernet' : 'wifi';
        label = net.type === 'ethernet' ? 'LAN' : net.name || 'WLAN';
      }
      elNet.innerHTML = `${icon(ic)}<span class="label">${esc(label)}</span>`;
    }
    const vol = st.volume;
    const elVol = $('#stat-vol');
    elVol.hidden = !vol;
    if (vol) elVol.innerHTML = `${icon(vol.muted || vol.percent === 0 ? 'mute' : 'volume')}<span>${vol.muted ? 'Stumm' : `${vol.percent}%`}</span>`;
    const bat = st.battery;
    const elBat = $('#stat-bat');
    elBat.hidden = !bat;
    if (bat) {
      elBat.classList.toggle('is-low', bat.percent <= 15 && !bat.plugged);
      elBat.innerHTML = `<span class="bat"><i style="width:${bat.percent}%"></i></span><span>${bat.percent}%</span>${bat.plugged ? icon('bolt') : ''}`;
    }

    $('#row-volume').hidden = !vol;
    if (vol && ui.dragging !== 'volume') {
      setRange($('#range-volume'), vol.percent);
      $('#out-volume').textContent = vol.muted ? 'Stumm' : `${vol.percent}%`;
      $('#btn-mute').innerHTML = icon(vol.muted ? 'mute' : 'volume');
    }
    const br = st.brightness;
    $('#row-brightness').hidden = !br;
    if (br && ui.dragging !== 'brightness') {
      setRange($('#range-brightness'), br.percent);
      $('#out-brightness').textContent = `${br.percent}%`;
    }

    const storage = st.storage || [];
    const icons = { internal: 'drive', sd: 'sd', usb: 'drive' };
    $('#storage-line').innerHTML = storage.map((s) => `<span>${icon(icons[s.id] || 'drive')}${esc(s.label)} · ${bytes(s.free)} frei</span>`).join('');
    $('#storage-list').innerHTML = storage.map((s) => {
      const used = Math.round(((s.total - s.free) / s.total) * 100);
      return `<div class="storage-item">${icon(icons[s.id] || 'drive')}<b>${esc(s.label)}</b><span>${bytes(s.free)} frei von ${bytes(s.total)}</span><div class="meter"><i style="width:${used}%"></i></div></div>`;
    }).join('') || '<span class="muted">Keine Daten</span>';
  }

  function handleJobs(jobs) {
    let changed = false;
    let running = false;
    Object.entries(jobs || {}).forEach(([fid, job]) => {
      const before = ui.jobsSeen[fid];
      if (job.status === 'running') running = true;
      if (before === 'running' && job.status !== 'running') {
        changed = true;
        if (job.status === 'done') toast('check', `${job.label} ist bereit. Drück A zum Starten.`, 5000);
        else toast('info', job.message || `${job.label} konnte nicht installiert werden.`, 6000);
      }
      ui.jobsSeen[fid] = job.status;
    });
    S.jobs = jobs || {};
    return { changed, running };
  }

  async function pollStatus() {
    clearTimeout(ui.pollTimer);
    let next = 8000;
    try {
      STATUS = await api.get('/api/status');
      ui.statusFails = 0;
      $('#offline').hidden = true;
      renderStatus();
      handleUpdates(STATUS.updates);
      const { changed, running } = handleJobs(STATUS.jobs);
      if (changed) await reloadState();
      if (running || (STATUS.updates && (STATUS.updates.running || STATUS.updates.checking))) next = 1500;
    } catch (e) {
      ui.statusFails += 1;
      if (ui.statusFails >= 2 && !ui.selfUpdating) {
        $('#offline').hidden = false;
        setFocus($('#btn-retry'), { noScroll: true });
      }
      next = 3000;
    }
    if (document.visibilityState === 'visible') ui.pollTimer = setTimeout(pollStatus, next);
  }
  function schedulePoll(ms) {
    clearTimeout(ui.pollTimer);
    ui.pollTimer = setTimeout(pollStatus, ms);
  }

  // ---------------------------------------------------------------- clock
  function tickClock() {
    const now = new Date();
    $('#clock').textContent = now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
    $('#date').textContent = now.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' });
    const h = now.getHours();
    $('#greet').textContent = h < 5 ? 'Gute Nacht.' : h < 11 ? 'Guten Morgen.' : h < 18 ? 'Guten Tag.' : h < 23 ? 'Guten Abend.' : 'Gute Nacht.';
  }

  // ---------------------------------------------------------------- input: keys
  function activate(el) {
    if (!el) return;
    if (el.matches('input:not([type="range"])')) return;
    el.click();
  }

  function contextForFocus() {
    if (ui.focus && ui.focus.matches('[data-tile]') && !scope()) openCtx(ui.focus);
  }

  function toggleFavFocus() {
    if (!ui.focus || !ui.focus.matches('[data-tile]') || scope()) return;
    const app = APPS.get(ui.focus.dataset.id);
    if (app) setFav(app, !isFav(app.id));
  }

  function runAction(action) {
    if (ui.launching) return;
    switch (action) {
      case 'up': case 'down': case 'left': case 'right': move(action); break;
      case 'a': activate(ui.focus); break;
      case 'b': back(); break;
      case 'x': contextForFocus(); break;
      case 'y': toggleFavFocus(); break;
      case 'lb': jumpRow(-1); break;
      case 'rb': jumpRow(1); break;
      case 'start': if (scope() && scope().id === 'drawer') closeLayer(); else { closeAllLayers(); openDrawer(); } break;
      case 'select': closeAllLayers(); openPower(); break;
      default: break;
    }
  }

  document.addEventListener('keydown', (e) => {
    ui.lastKeyAt = performance.now();
    if (ui.launching) { if (e.key !== 'F5') e.preventDefault(); return; }
    const t = e.target;
    const typing = t.matches && t.matches('input:not([type="range"])');
    const range = t.matches && t.matches('input[type="range"]');
    const key = e.key;
    const map = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' };
    if (map[key]) {
      if ((typing || range) && (key === 'ArrowLeft' || key === 'ArrowRight')) return;
      e.preventDefault();
      runAction(map[key]);
      return;
    }
    if (key === 'Enter') {
      e.preventDefault();
      if (typing) {
        if (t.id === 'add-name') setFocus($('#add-url'));
        else if (t.id === 'add-url') submitAdd();
        return;
      }
      runAction('a');
      return;
    }
    if (typing) {
      if (key === 'Escape') { e.preventDefault(); runAction('b'); }
      return;
    }
    switch (key) {
      case ' ': e.preventDefault(); runAction('a'); break;
      case 'Escape': case 'Backspace': case 'BrowserBack': e.preventDefault(); runAction('b'); break;
      case 'ContextMenu': case 'm': case 'x': e.preventDefault(); runAction('x'); break;
      case 'f': case 'y': e.preventDefault(); runAction('y'); break;
      case 'Tab': e.preventDefault(); runAction(e.shiftKey ? 'lb' : 'rb'); break;
      case 'PageUp': case 'q': e.preventDefault(); runAction('lb'); break;
      case 'PageDown': case 'e': e.preventDefault(); runAction('rb'); break;
      case 'Home': e.preventDefault(); goRow('fav'); break;
      case 'o': e.preventDefault(); runAction('start'); break;
      case 'p': e.preventDefault(); runAction('select'); break;
      default: break;
    }
  });

  // ---------------------------------------------------------------- input: gamepad
  // In the desktop mode Steam usually turns the Deck's controls into keys
  // already. If a gamepad *also* shows up here we would act twice, so the
  // first press waits a moment: if a key event arrives meanwhile we know Steam
  // mirrors the pad and ignore the raw pad from then on.
  const BTN = { 0: 'a', 1: 'b', 2: 'x', 3: 'y', 4: 'lb', 5: 'rb', 8: 'select', 9: 'start', 12: 'up', 13: 'down', 14: 'left', 15: 'right' };
  const REPEATABLE = new Set(['up', 'down', 'left', 'right']);
  const padState = new Map();
  let padLoop = 0;

  function padAction(action) {
    if (ui.padMirrored) return;
    if (ui.padVerified) return runAction(action);
    const at = performance.now();
    setTimeout(() => {
      if (ui.lastKeyAt >= at - 30) { ui.padMirrored = true; return; }
      ui.padVerified = true;
      runAction(action);
    }, 110);
    return undefined;
  }

  function readPad(gp) {
    const pressed = new Set();
    Object.entries(BTN).forEach(([i, name]) => {
      const b = gp.buttons[i];
      if (b && (b.pressed || b.value > 0.6)) pressed.add(name);
    });
    const [x = 0, y = 0] = gp.axes;
    if (x < -0.6) pressed.add('left');
    if (x > 0.6) pressed.add('right');
    if (y < -0.6) pressed.add('up');
    if (y > 0.6) pressed.add('down');
    return pressed;
  }

  function pollPads(now) {
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    let any = false;
    for (const gp of pads) {
      if (!gp || !gp.connected) continue;
      any = true;
      const prev = padState.get(gp.index) || new Map();
      const pressed = readPad(gp);
      pressed.forEach((name) => {
        const since = prev.get(name);
        if (since === undefined) {
          prev.set(name, { t: now, next: now + 380 });
          padAction(name);
        } else if (REPEATABLE.has(name) && now >= since.next) {
          since.next = now + 110;
          padAction(name);
        }
      });
      [...prev.keys()].forEach((name) => { if (!pressed.has(name)) prev.delete(name); });
      padState.set(gp.index, prev);
    }
    padLoop = any ? requestAnimationFrame(pollPads) : 0;
  }

  window.addEventListener('gamepadconnected', () => {
    if (!padLoop) padLoop = requestAnimationFrame(pollPads);
  });

  // ---------------------------------------------------------------- input: pointer
  let hoverTarget = null;
  document.addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'mouse' || ui.launching) return;
    const el = e.target.closest('[data-nav]');
    if (!el || el === ui.focus || el === hoverTarget) return;
    if (!hoverTarget) {
      requestAnimationFrame(() => {
        const target = hoverTarget;
        hoverTarget = null;
        if (!target || target === ui.focus || !visible(target)) return;
        const layer = scope();
        if (layer ? !layer.contains(target) : target.closest('.layer, .offline')) return;
        setFocus(target, { noScroll: true });
      });
    }
    hoverTarget = el;
  }, { passive: true });

  document.addEventListener('scroll', () => { ui.lastScrollAt = performance.now(); }, { capture: true, passive: true });

  document.addEventListener('pointerdown', (e) => {
    ui.pointerType = e.pointerType || 'mouse';
    const layer = scope();
    if (!layer) return;
    if ((layer.id === 'ctx' || layer.id === 'drawer') && !layer.contains(e.target)) {
      e.preventDefault();
      closeLayer();
    } else if (layer.classList.contains('modal') && e.target === layer) {
      closeLayer();
    }
  });

  document.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    if (scope() || ui.launching) return;
    const tile = e.target.closest('[data-tile]');
    if (tile) openCtx(tile, { x: e.clientX, y: e.clientY });
    else contextForFocus();
  });

  document.addEventListener('click', (e) => {
    const el = e.target.closest('button');
    if (!el || ui.launching) return;
    if (el.matches('[data-tile]')) {
      // Taps and clicks: the first one only selects (no more accidental launches while
      // scrolling), a tap on the already selected tile starts it. A/Enter always start.
      if (e.detail > 0) {
        const now = performance.now();
        const settled = el === ui.focus && now - ui.focusAt > (ui.pointerType === 'mouse' ? 120 : 0);
        if (!settled || now - ui.lastScrollAt < 350) {
          setFocus(el, { noScroll: true });
          return undefined;
        }
      }
      setFocus(el, { noScroll: true });
      return activateTile(el);
    }
    if (el.matches('[data-hero-start]')) {
      return ui.focus && ui.focus.matches('[data-tile]') ? activateTile(ui.focus) : undefined;
    }
    if (el.matches('[data-tab]')) return goRow(el.dataset.tab);
    if (el.matches('[data-close]')) return closeLayer();
    if (el.matches('[data-ctx]')) {
      const item = $('#ctx')._items[Number(el.dataset.ctx)];
      closeLayer();
      return item && item[2]();
    }
    if (el.matches('[data-power]')) return powerAction(el);
    if (el.matches('[data-setting]')) return toggleSetting(el);
    if (el.matches('[data-browser]')) return chooseBrowser(el.dataset.browser);
    if (el.matches('[data-launch]')) { closeAllLayers(); return launchApp(el.dataset.launch); }
    if (el.matches('[data-cat]')) {
      addCategory = el.dataset.cat;
      $$('#add-cat button').forEach((b) => b.setAttribute('aria-pressed', String(b === el)));
      return undefined;
    }
    switch (el.id) {
      case 'btn-menu': return openDrawer();
      case 'btn-power': return openPower();
      case 'btn-keyboard': return launchApp('keyboard');
      case 'btn-add': return openAdd();
      case 'btn-unhide': return unhideAll();
      case 'btn-mute': return setVolume({ mute: 'toggle' });
      case 'add-keyboard': return launchApp('keyboard');
      case 'btn-retry': return location.reload();
      case 'btn-updates': return openUpdates();
      case 'upd-run': return runUpdates();
      case 'upd-check': return checkUpdates();
      case 'wn-ok':
        closeLayer();
        api.post('/api/whatsnew/seen').catch(() => {});
        return undefined;
      default: return undefined;
    }
  });

  $('#add-form').addEventListener('submit', (e) => {
    e.preventDefault();
    submitAdd();
  });

  // ---------------------------------------------------------------- settings
  async function toggleSetting(el) {
    const key = el.dataset.setting;
    const value = el.getAttribute('aria-checked') !== 'true';
    el.setAttribute('aria-checked', String(value));
    try {
      const res = await api.post('/api/settings', { [key]: value });
      S.settings = res.settings;
      $$(`.toggle[data-setting="${key}"]`).forEach((t) => t.setAttribute('aria-checked', String(!!S.settings[key])));
      applySettings();
    } catch (e) {
      el.setAttribute('aria-checked', String(!value));
      toast('info', e.message);
    }
  }

  async function chooseBrowser(id) {
    try {
      const res = await api.post('/api/settings', { browser: id });
      S.settings = res.settings;
      S.browser = (S.browsers || []).find((b) => b.id === id) || S.browser;
      renderDrawer();
      toast('globe', `Web-Apps öffnen jetzt mit ${S.browser.name}`);
    } catch (e) { toast('info', e.message); }
  }

  async function unhideAll() {
    try {
      await api.post('/api/unhide-all');
      await reloadState();
      toast('eye', 'Alle Kacheln sind wieder sichtbar');
    } catch (e) { toast('info', e.message); }
  }

  function applySettings() {
    document.body.classList.toggle('lite', !!S.settings.reducedEffects);
  }

  let volTimer = 0;
  async function setVolume(body) {
    try {
      const res = await api.post('/api/volume', body);
      if (STATUS) STATUS.volume = res.volume;
      renderStatus();
    } catch (e) { toast('info', e.message); }
  }

  function bindRange(input, kind) {
    input.addEventListener('input', () => {
      ui.dragging = kind;
      setRange(input, Number(input.value));
      $(`#out-${kind}`).textContent = `${input.value}%`;
      clearTimeout(volTimer);
      volTimer = setTimeout(async () => {
        try {
          if (kind === 'volume') await setVolume({ percent: Number(input.value) });
          else await api.post('/api/brightness', { percent: Number(input.value) });
        } finally {
          setTimeout(() => { if (ui.dragging === kind) ui.dragging = null; }, 600);
        }
      }, 120);
    });
  }

  // ---------------------------------------------------------------- boot
  async function boot() {
    try {
      const [icons, state] = await Promise.all([
        fetch('icons.json', { cache: 'no-cache' }).then((r) => r.json()),
        api.get('/api/state'),
      ]);
      ICONS = icons;
      S = state;
    } catch (e) {
      document.body.classList.remove('is-booting');
      $('#offline').hidden = false;
      return;
    }
    indexApps();
    hydrateIcons();
    applySettings();
    Object.entries(S.jobs || {}).forEach(([fid, job]) => { ui.jobsSeen[fid] = job.status; });
    render();
    bindRange($('#range-volume'), 'volume');
    bindRange($('#range-brightness'), 'brightness');
    tickClock();
    setInterval(tickClock, 1000);
    pollStatus();
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      document.title = 'NovaDeck';
      if (ui.launching && !ui.selfUpdating) hideLaunch();
      pollStatus();
    });

    const start = (S.last && (findTile(S.last, 'fav') || findTile(S.last))) || rowDefault($('.row'));
    setFocus(start);
    requestAnimationFrame(() => document.body.classList.remove('is-booting'));
    if (S.whatsNew) setTimeout(() => showWhatsNew(S.whatsNew), 700);
    else if (S.demo) toast('info', 'Demo-Modus: Es wird nichts wirklich gestartet.', 4200);
    else if (!S.browser) toast('info', 'Für Web-Apps fehlt ein Browser. Wähl eine Kachel und drück A, um Chrome zu installieren.', 7000);
  }

  boot();
})();

// ── Display-Konfiguration ───────────────────────────────────────────────────

const _DSP_KEY = 'mave_display_cfg';

const _TILES = [
  { id: 'battery',  label: 'Batterie',     sizes: ['hidden','normal','half','wide'] },
  { id: 'tanks',    label: 'Tanks',        sizes: ['hidden','normal','half']        },
  { id: 'lights',   label: 'Beleuchtung',  sizes: ['hidden','normal','half','wide'] },
  { id: 'inverter', label: '230V',         sizes: ['hidden','normal','half']        },
  { id: 'wl',       label: 'Wasserstand',  sizes: ['hidden','normal','half']        },
  { id: 'wartung',  label: 'Wartungsplan', sizes: ['hidden','normal','half','wide'] },
];

// Selector for each tile element
const _TILE_SEL = {
  battery: '#battCard',
  tanks:   '#tankCard',
  lights:  '#lightsCard',
  inverter:'#inverterCard',
  wl:      '#wlCard',
  wartung: '#wartungCard',
};

const _PROFILES = [
  { id: 'kiosk',  label: 'Kiosk (Touch-Display)' },
  { id: 'laptop', label: 'Laptop / Desktop' },
  { id: 'mobile', label: 'Mobil' },
];

// Tile values: 'normal' | 'half' | 'wide' | 'hidden'
// 'wide' = 2 columns, 'half' = reduced height, 'hidden' = not shown
const _DSP_DEFAULTS = {
  activeProfile: 'auto',
  tiles: {
    kiosk:  { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wl:'normal',  wartung:'hidden' },
    laptop: { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wl:'normal',  wartung:'normal' },
    mobile: { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wl:'hidden',  wartung:'hidden' },
  },
};

let _dsp = null;

function _dspLoad() {
  try {
    const raw = localStorage.getItem(_DSP_KEY);
    const saved = raw ? JSON.parse(raw) : {};
    _dsp = { activeProfile: saved.activeProfile ?? _DSP_DEFAULTS.activeProfile, tiles: {} };
    for (const p of _PROFILES) {
      const savedTiles = saved.tiles?.[p.id] ?? {};
      const defTiles   = _DSP_DEFAULTS.tiles[p.id];
      _dsp.tiles[p.id] = {};
      for (const t of _TILES) {
        const sv = savedTiles[t.id];
        if (sv === true)              _dsp.tiles[p.id][t.id] = 'normal'; // migrate old bool
        else if (sv === false)        _dsp.tiles[p.id][t.id] = 'hidden'; // migrate old bool
        else if (typeof sv === 'string') _dsp.tiles[p.id][t.id] = sv;
        else                          _dsp.tiles[p.id][t.id] = defTiles[t.id];
      }
    }
  } catch (_) {
    _dsp = JSON.parse(JSON.stringify(_DSP_DEFAULTS));
  }
}

function _dspSave() {
  localStorage.setItem(_DSP_KEY, JSON.stringify(_dsp));
}

// ── Benannte Konfigurationen ────────────────────────────────────────────────
const _DSP_CFG_KEY = 'mave_display_configs';

function _dspConfigsLoad() {
  try { return JSON.parse(localStorage.getItem(_DSP_CFG_KEY)) || {}; }
  catch (_) { return {}; }
}
function _dspConfigsSave(obj) {
  localStorage.setItem(_DSP_CFG_KEY, JSON.stringify(obj));
}
function _dspFeedback(msg) {
  const fb = $('dspFeedback');
  if (fb) { fb.textContent = msg; setTimeout(() => { if (fb.textContent === msg) fb.textContent = ''; }, 2500); }
}
function saveDisplayConfig() {
  const inp  = $('dspCfgName');
  const name = (inp?.value || '').trim();
  if (!name) { _dspFeedback('Bitte Namen eingeben'); return; }
  saveDisplaySettings(true);                 // aktuelle Auswahl übernehmen
  const cfgs = _dspConfigsLoad();
  cfgs[name] = JSON.parse(JSON.stringify({ activeProfile: _dsp.activeProfile, tiles: _dsp.tiles }));
  _dspConfigsSave(cfgs);
  openDisplaySettings();
  _dspFeedback('„' + name + '" gespeichert ✓');
}
function loadDisplayConfig(name) {
  const cfg = _dspConfigsLoad()[name];
  if (!cfg) return;
  _dsp = JSON.parse(JSON.stringify(cfg));
  _dspSave();
  applyDisplayConfig();
  openDisplaySettings();
  _dspFeedback('„' + name + '" geladen ✓');
}
function deleteDisplayConfig(name) {
  const cfgs = _dspConfigsLoad();
  delete cfgs[name];
  _dspConfigsSave(cfgs);
  openDisplaySettings();
}

function _dspActiveProfile() {
  if (_dsp.activeProfile !== 'auto') return _dsp.activeProfile;
  return window.innerWidth < 640 ? 'mobile' : 'laptop';
}

// ── Grid-Engine ──────────────────────────────────────────────────────────────
// Eine Basis-Einheit: normale Kachel = Quadrat (Breite == Höhe).
//   normal = 1 Spalte × 2 Zeilen   half = 1 Spalte × 1 Zeile (halbe Höhe)
//   wide   = 2 Spalten × 2 Zeilen (doppelte Breite, gleiche Höhe)
// Zeilenhöhe = (Spaltenbreite − gap) / 2, damit 2 Zeilen exakt 1 Spaltenbreite
// ergeben (Quadrat). grid-auto-flow:dense packt automatisch ohne Lücken oben.

function _cols() {
  const w = window.innerWidth;
  if (w < 640)  return 1;
  if (w < 1024) return 2;
  if (w < 1600) return 3;
  return 4;
}

function _applyGrid() {
  if (!_dsp) _dspLoad();
  const main = document.querySelector('main');
  if (!main) return;

  const profile = _dspActiveProfile();
  const tileCfg = _dsp.tiles[profile] ?? {};
  const cols    = _cols();

  main.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;

  if (cols === 1) {
    main.style.gridAutoRows = 'auto';
  } else {
    const cs    = getComputedStyle(main);
    const gap   = parseFloat(cs.columnGap)    || 16;
    const padL  = parseFloat(cs.paddingLeft)  || 0;
    const padR  = parseFloat(cs.paddingRight) || 0;
    const inner = main.clientWidth - padL - padR;
    const colW  = (inner - (cols - 1) * gap) / cols;
    const rowH  = Math.max(70, (colW - gap) / 2);
    main.style.gridAutoRows = rowH + 'px';
  }

  for (const t of _TILES) {
    const el = document.querySelector(_TILE_SEL[t.id]);
    if (!el) continue;
    const sz = tileCfg[t.id] ?? 'normal';
    if (sz === 'hidden') {
      // Klasse mit !important — schlägt inline display:'' aus Daten-Updates
      // (updateBattery/updateTanks), damit ausgeblendet wirklich weg bleibt.
      el.classList.add('tile-hidden');
      el.style.gridColumn = el.style.gridRow = el.style.order = '';
      continue;
    }
    el.classList.remove('tile-hidden');
    el.style.display = '';
    if (cols === 1) {
      el.style.gridColumn = el.style.gridRow = el.style.order = '';
    } else {
      el.style.gridColumn = `span ${(sz === 'wide') ? Math.min(2, cols) : 1}`;
      el.style.gridRow    = `span ${(sz === 'half') ? 1 : 2}`;
      // Volle Höhe (normal/wide) zuerst, halbe Kacheln danach -> saubere Bänder
      // statt halber Kacheln, die sich oben zwischen die vollen mischen.
      el.style.order      = (sz === 'half') ? '1' : '0';
    }
  }
}

let _resizeRaf = null;
window.addEventListener('resize', () => {
  if (_resizeRaf) cancelAnimationFrame(_resizeRaf);
  _resizeRaf = requestAnimationFrame(() => {
    _applyGrid();
    if (typeof updateWartungHomeTile === 'function') updateWartungHomeTile();
  });
});

// ── Kachelsichtbarkeit & Größe anwenden ────────────────────────────────────

function applyDisplayConfig() {
  if (!_dsp) _dspLoad();
  const profile = _dspActiveProfile();
  const tileCfg = _dsp.tiles[profile] ?? {};

  for (const t of _TILES) {
    const el = document.querySelector(_TILE_SEL[t.id]);
    if (!el) continue;
    const sz = tileCfg[t.id] ?? 'normal';
    el.classList.toggle('tile--half', sz === 'half');
    el.classList.toggle('tile--wide', sz === 'wide');
  }

  _applyGrid();

  if (profile === 'kiosk') {
    document.body.classList.add('kiosk-mode');
    _kioskNavInit();
  } else {
    document.body.classList.remove('kiosk-mode');
  }

  // Größenabhängige Inhalte neu rendern
  requestAnimationFrame(() => {
    if (typeof updateWartungHomeTile === 'function') updateWartungHomeTile();
  });
}

// ── Kiosk Bottom-Nav ────────────────────────────────────────────────────────

const _KIOSK_TABS = [
  {
    id: 'home', label: 'Übersicht',
    icon: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    action() { _closeAllOverlays(); _kioskSetActive('home'); },
  },
  {
    id: 'energie', label: 'Energie',
    icon: '<rect x="2" y="7" width="18" height="11" rx="2"/><path d="M22 11v3"/><path d="M7 7V4"/><path d="M11 7V4"/>',
    action() { openBattDetail(); _kioskSetActive('energie'); },
  },
  {
    id: 'licht', label: 'Licht',
    icon: '<circle cx="12" cy="12" r="5"/><path d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>',
    action() { openLightDetail(); _kioskSetActive('licht'); },
  },
  {
    id: 'wasser', label: 'Wasser',
    icon: '<path d="M12 2C6 9 4 13 4 16a8 8 0 0 0 16 0c0-3-2-7-8-14z"/>',
    action() { openWaterLevel(); _kioskSetActive('wasser'); },
  },
  {
    id: 'system', label: 'System',
    icon: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    action() { openSettings(); _kioskSetActive('system'); },
  },
];

let _kioskReady = false;

function _kioskNavInit() {
  if (_kioskReady) return;
  _kioskReady = true;
  const side = $('kioskSidebar');
  if (side) {
    side.innerHTML = _KIOSK_TABS.map(t => `
      <button class="kiosk-side-btn" data-kt="${t.id}" onclick="_kioskTab('${t.id}')">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${t.icon}</svg>
        <span>${t.label}</span>
      </button>`).join('');
  }
  _kioskSetActive('home');
  _kioskQuickInit();
}

function _kioskTab(id) {
  const tab = _KIOSK_TABS.find(t => t.id === id);
  if (tab) tab.action();
}

function _kioskSetActive(id) {
  document.querySelectorAll('.kiosk-side-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.kt === id)
  );
}

// ── Slide-down Topbar mit Quick-Einstellungen ───────────────────────────────

function _kioskToggleTop() {
  const t = $('kioskTop');
  if (!t) return;
  t.classList.toggle('open');
  if (t.classList.contains('open')) _kioskInvSync();
}

async function _kioskQuickInit() {
  const q = $('ktpQuick');
  if (!q) return;
  let cap = { power_available: false, brightness_available: false, brightness: 50 };
  try { cap = await fetch('/api/display/state').then(r => r.json()); } catch (_) {}
  const bri = cap.brightness ?? 50;

  q.innerHTML = `
    <div class="ktp-tile">
      <span class="ktp-tile-lbl">Inverter</span>
      <button class="ktp-toggle" id="ktpInvBtn" onclick="_kioskQuickInverter()">–</button>
    </div>
    <div class="ktp-tile${cap.power_available ? '' : ' disabled'}">
      <span class="ktp-tile-lbl">Display</span>
      <button class="ktp-toggle" id="ktpDispBtn" ${cap.power_available ? '' : 'disabled'} onclick="_kioskDisplayOff()">Ausschalten</button>
    </div>
    <div class="ktp-tile${cap.brightness_available ? '' : ' disabled'}">
      <span class="ktp-tile-lbl">Helligkeit${cap.brightness_available ? '' : ' · n/v'}</span>
      <input type="range" class="ktp-range" min="10" max="100" value="${bri}"
        ${cap.brightness_available ? '' : 'disabled'} oninput="_kioskBrightness(this.value)">
    </div>
    <div class="ktp-tile disabled">
      <span class="ktp-tile-lbl">Gas · bald</span>
      <button class="ktp-toggle" disabled>—</button>
    </div>
    <div class="ktp-tile disabled">
      <span class="ktp-tile-lbl">Starlink · bald</span>
      <button class="ktp-toggle" disabled>—</button>
    </div>
  `;
  _kioskInvSync();
}

function _kioskInvSync() {
  const btn = $('ktpInvBtn');
  if (!btn) return;
  const on = (typeof _invCurrentState !== 'undefined') &&
             (_invCurrentState === 'Aktiv' || _invCurrentState === 'Eco');
  btn.textContent = on ? 'An' : 'Aus';
  btn.classList.toggle('on', on);
}

function _kioskQuickInverter() {
  if (typeof toggleInverter === 'function') toggleInverter();
  setTimeout(_kioskInvSync, 60);
}

function _kioskDisplayOff() {
  fetch('/api/display/power', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on: false }),
  }).then(r => { if (!r.ok) _toast('Display-Steuerung (noch) nicht verfügbar'); })
    .catch(() => _toast('Display-Steuerung (noch) nicht verfügbar'));
}

let _briTimer = null;
function _kioskBrightness(v) {
  clearTimeout(_briTimer);
  _briTimer = setTimeout(() => {
    fetch('/api/display/brightness', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: +v }),
    }).catch(() => {});
  }, 140);
}

// Aktualisiert die Mini-Batterie in der Slide-down Topbar (vom Batterie-Update).
function _kioskUpdateBatt(soc) {
  const fill = $('ktpBattFill'), pct = $('ktpBattPct'), wrap = $('ktpBatt');
  if (!fill) return;
  if (soc == null) { fill.setAttribute('width', '0'); if (pct) pct.textContent = '--%'; return; }
  const v = Math.max(0, Math.min(100, soc));
  fill.setAttribute('width', String((v / 100 * 16).toFixed(1)));
  const color = v >= 50 ? 'var(--green)' : v >= 20 ? 'var(--yellow)' : 'var(--red)';
  if (wrap) wrap.style.color = color;
  if (pct)  pct.textContent = Math.round(v) + '%';
}

// Mini-Toast
let _toastTimer = null;
function _toast(msg) {
  let el = $('miniToast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'miniToast'; el.className = 'mini-toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  requestAnimationFrame(() => el.classList.add('show'));
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

// ── Display-Einstellungen ───────────────────────────────────────────────────

const _SIZE_OPTS = [
  { value: 'hidden', label: 'Ausgeblendet' },
  { value: 'normal', label: 'Normal'       },
  { value: 'half',   label: 'Halbe Höhe'  },
  { value: 'wide',   label: 'Doppelte Breite' },
];

function openDisplaySettings() {
  if (!_dsp) _dspLoad();
  const pane = $('setPane-display');
  if (!pane) return;

  const sizeSelect = (pid, t, cur) => {
    const opts = _SIZE_OPTS.filter(o => (t.sizes ?? _SIZE_OPTS.map(x => x.value)).includes(o.value));
    return `<select class="settings-input" id="dsp_${pid}_${t.id}" style="max-width:180px;cursor:pointer">
      ${opts.map(o => `<option value="${o.value}"${cur === o.value ? ' selected' : ''}>${o.label}</option>`).join('')}
    </select>`;
  };

  const cfgs  = _dspConfigsLoad();
  const names = Object.keys(cfgs);
  const esc   = s => s.replace(/'/g, "\\'");
  const cfgList = names.length
    ? names.map(n => `
        <div class="settings-row" style="align-items:center">
          <label class="settings-label" style="min-width:0;flex:1">${n}</label>
          <span style="display:flex;gap:8px">
            <button class="btn-secondary" onclick="loadDisplayConfig('${esc(n)}')">Laden</button>
            <button class="btn-secondary" onclick="deleteDisplayConfig('${esc(n)}')" style="color:var(--red)">Löschen</button>
          </span>
        </div>`).join('')
    : '<div style="font-size:12px;color:var(--text3);padding:4px 0">Noch keine Konfiguration gespeichert.</div>';

  pane.innerHTML = `
    <div class="set-card">
      <div class="set-card-hd">Konfigurationen</div>
      ${cfgList}
      <div class="settings-row" style="align-items:center;gap:10px;border-bottom:none;padding-top:12px">
        <input class="settings-input" id="dspCfgName" placeholder="Name der Konfiguration…" style="flex:1;max-width:none">
        <button class="btn-secondary" onclick="saveDisplayConfig()">Aktuelle speichern</button>
      </div>
    </div>

    <div class="set-card">
      <div class="set-card-hd">Geräte-Profil</div>
      <div class="settings-row">
        <label class="settings-label">Aktives Profil</label>
        <select class="settings-input" id="dspProfileSel" style="max-width:220px;cursor:pointer">
          <option value="auto">Automatisch</option>
          <option value="kiosk">Kiosk (Touch-Display)</option>
          <option value="laptop">Laptop / Desktop</option>
          <option value="mobile">Mobil</option>
        </select>
      </div>
      <div style="font-size:12px;color:var(--text3);margin-top:4px">
        Automatisch: bei Breite &lt;640 px → Mobil, sonst Laptop.
        Kiosk aktiviert die Bottom-Navigation.
      </div>
    </div>

    ${_PROFILES.map(p => `
      <div class="set-card">
        <div class="set-card-hd">Kacheln — ${p.label}</div>
        ${_TILES.map(t => `
          <div class="settings-row" style="align-items:center">
            <label class="settings-label" for="dsp_${p.id}_${t.id}">${t.label}</label>
            ${sizeSelect(p.id, t, _dsp.tiles[p.id]?.[t.id] ?? 'normal')}
          </div>
        `).join('')}
      </div>
    `).join('')}

    <div class="settings-actions">
      <span class="settings-feedback" id="dspFeedback"></span>
      <button class="btn-primary" onclick="saveDisplaySettings()">Speichern &amp; anwenden</button>
    </div>
  `;

  $('dspProfileSel').value = _dsp.activeProfile;
}

function saveDisplaySettings(silent) {
  _dsp.activeProfile = $('dspProfileSel').value;
  for (const p of _PROFILES) {
    for (const t of _TILES) {
      const el = $(`dsp_${p.id}_${t.id}`);
      if (el) _dsp.tiles[p.id][t.id] = el.value;
    }
  }
  _dspSave();
  applyDisplayConfig();
  if (!silent) _dspFeedback('Gespeichert ✓');
}

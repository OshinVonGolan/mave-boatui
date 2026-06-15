// ── Display-Konfiguration ───────────────────────────────────────────────────

const _DSP_KEY = 'mave_display_cfg';

const _TILES = [
  { id: 'battery',  label: 'Batterie'    },
  { id: 'tanks',    label: 'Tanks'       },
  { id: 'lights',   label: 'Beleuchtung' },
  { id: 'inverter', label: '230V'        },
  { id: 'wartung',  label: 'Wartungsplan'},
];

// Selector for each tile element
const _TILE_SEL = {
  battery: '#battCard',
  tanks:   '#tankCard',
  lights:  '#lightsCard',
  inverter:'#inverterCard',
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
    kiosk:  { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wartung:'hidden' },
    laptop: { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wartung:'wide'   },
    mobile: { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wartung:'hidden' },
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

function _dspActiveProfile() {
  if (_dsp.activeProfile !== 'auto') return _dsp.activeProfile;
  return window.innerWidth < 640 ? 'mobile' : 'laptop';
}

// ── Grid dynamisch aufbauen ─────────────────────────────────────────────────
// Packt sichtbare Kacheln in Zeilen, berücksichtigt 'wide' (span=2).
// Erzeugt grid-template-areas direkt auf <main> – keine festen CSS-Bereiche nötig.

function _rebuildGrid() {
  if (!_dsp) _dspLoad();
  const main = document.querySelector('main');
  if (!main) return;

  const profile  = _dspActiveProfile();
  const tileCfg  = _dsp.tiles[profile] ?? {};
  const w        = window.innerWidth;
  const cols     = w < 640 ? 1 : w < 1024 ? 2 : 3;

  const vis = _TILES.filter(t => (tileCfg[t.id] ?? 'normal') !== 'hidden');
  if (!vis.length) { main.style.gridTemplateAreas = ''; return; }

  if (cols === 1) {
    main.style.gridTemplateAreas = vis.map(t => `"${t.id}"`).join(' ');
    return;
  }

  const rows   = [];
  let   row    = [];
  let   used   = 0;

  for (const t of vis) {
    const span = (tileCfg[t.id] === 'wide') ? Math.min(2, cols) : 1;

    if (used + span > cols) {
      while (row.length < cols) row.push('.');
      rows.push(row);
      row  = [];
      used = 0;
    }

    for (let i = 0; i < span; i++) row.push(t.id);
    used += span;
  }

  if (row.length > 0) {
    while (row.length < cols) row.push('.');
    rows.push(row);
  }

  main.style.gridTemplateAreas = rows.map(r => `"${r.join(' ')}"`).join(' ');
}

window.addEventListener('resize', _rebuildGrid);

// ── Kachelsichtbarkeit & Größe anwenden ────────────────────────────────────

function applyDisplayConfig() {
  if (!_dsp) _dspLoad();
  const profile = _dspActiveProfile();
  const tileCfg = _dsp.tiles[profile] ?? {};

  for (const t of _TILES) {
    const el = document.querySelector(_TILE_SEL[t.id]);
    if (!el) continue;
    const sz = tileCfg[t.id] ?? 'normal';
    el.style.display = sz === 'hidden' ? 'none' : '';
    el.classList.toggle('tile--half', sz === 'half');
  }

  _rebuildGrid();

  if (profile === 'kiosk') {
    document.body.classList.add('kiosk-mode');
    _kioskNavInit();
  } else {
    document.body.classList.remove('kiosk-mode');
  }

  // Höhen-Sync nach Layout-Neuberechnung
  requestAnimationFrame(() => {
    if (typeof _syncWartungHeight === 'function') _syncWartungHeight();
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
  const nav = $('kioskNav');
  if (!nav || _kioskReady) return;
  _kioskReady = true;
  nav.innerHTML = _KIOSK_TABS.map(t => `
    <button class="kiosk-tab" id="kTab-${t.id}" onclick="_kioskTab('${t.id}')">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${t.icon}</svg>
      <span>${t.label}</span>
    </button>
  `).join('');
  _kioskSetActive('home');
}

function _kioskTab(id) {
  const tab = _KIOSK_TABS.find(t => t.id === id);
  if (tab) tab.action();
}

function _kioskSetActive(id) {
  document.querySelectorAll('.kiosk-tab').forEach(b =>
    b.classList.toggle('active', b.id === `kTab-${id}`)
  );
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

  const sizeSelect = (pid, tid, cur) =>
    `<select class="settings-input" id="dsp_${pid}_${tid}" style="max-width:180px;cursor:pointer">
      ${_SIZE_OPTS.map(o => `<option value="${o.value}"${cur === o.value ? ' selected' : ''}>${o.label}</option>`).join('')}
    </select>`;

  pane.innerHTML = `
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
            ${sizeSelect(p.id, t.id, _dsp.tiles[p.id]?.[t.id] ?? 'normal')}
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

function saveDisplaySettings() {
  _dsp.activeProfile = $('dspProfileSel').value;
  for (const p of _PROFILES) {
    for (const t of _TILES) {
      const el = $(`dsp_${p.id}_${t.id}`);
      if (el) _dsp.tiles[p.id][t.id] = el.value;
    }
  }
  _dspSave();
  applyDisplayConfig();
  const fb = $('dspFeedback');
  if (fb) { fb.textContent = 'Gespeichert ✓'; setTimeout(() => fb.textContent = '', 2000); }
}

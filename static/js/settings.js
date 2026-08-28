// ── Settings ───────────────────────────────────────────────────────────────

var _settingsNetTimer = null;
let _versionInfo = null;   // gecachte Versions-Info (periodisch aktualisiert)

// ── Eingabe-Hilfen ─────────────────────────────────────────────────────────
// Eine eingegebene 0 ist ein gültiger Wert (Batterie-Instanz 0, Solar-Offset 0,
// Rampe 0) und darf NICHT stillschweigend zum Vorgabewert werden. Deshalb kein
// `parseInt(...) || vorgabe` und kein `parseFloat(...) ?? vorgabe` — parseFloat
// liefert bei leerem Feld NaN und nicht null, `??` greift dort also nie.
// Nur ein nicht lesbarer Wert (leeres Feld, Buchstaben) fällt auf die Vorgabe
// zurück; die physikalischen Grenzen prüft der Server.
function _intOr(value, fallback) {
  const n = parseInt(String(value ?? '').trim(), 10);
  return Number.isFinite(n) ? n : fallback;
}

function _floatOr(value, fallback) {
  const n = parseFloat(String(value ?? '').trim());
  return Number.isFinite(n) ? n : fallback;
}

// fetch wirft bei 4xx/5xx nicht von selbst. Ohne diese Prüfung meldete die
// Oberfläche "Gespeichert", obwohl der Server die Werte abgelehnt hatte
// (z. B. Ladespannung außerhalb der Grenzen) — bei einer Ladesteuerung ein
// gefährlicher Trugschluss.
async function _jsonOrThrow(res) {
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`);
  }
  return data;
}

// Rückmeldung "gespeichert" mit Haken-Icon (Projektregel: keine Emojis).
function _fbOk(fb) {
  fb.className = 'settings-feedback show';
  fb.innerHTML = `Gespeichert ${icon('check', { size: 13 })}`;
  setTimeout(() => fb.classList.remove('show'), 2500);
}

function _fbError(fb, msg) {
  fb.className = 'settings-feedback error show';
  fb.textContent = msg || 'Fehler beim Speichern';
}

async function refreshVersion() {
  try {
    _versionInfo = await fetch('/api/system/version').then(r => r.json());
  } catch(_) { return; }
  renderVersionInfo();
}

function renderVersionInfo() {
  const vi = $('updateVersionInfo');
  if (!vi) return;
  const d = _versionInfo;
  if (!d) { vi.textContent = 'Version wird geprüft…'; return; }
  let html = `Installiert: <strong>v${d.version}</strong>`;
  if (d.up_to_date === false && d.remote_version)
    html += ` &nbsp;→&nbsp; <span style="color:var(--yellow)">Update verfügbar: v${d.remote_version}</span>`;
  else if (d.up_to_date === true)
    html += ` &nbsp;<span style="color:var(--green)">${icon('check', { size: 13 })} aktuell</span>`;
  else
    html += ` &nbsp;<span style="color:var(--text3)">(Update-Status wird geprüft…)</span>`;
  vi.innerHTML = html;
}

function switchSettingsCat(cat) {
  ['tanks', 'batt', 'laden', 'heizung', 'wartung', 'netz', 'system', 'display'].forEach(c =>
    $(`setPane-${c}`)?.classList.toggle('active', c === cat)
  );
  document.querySelectorAll('.set-nav-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.cat === cat)
  );
  if (cat === 'heizung') hzEinstellungenLaden();
  if (cat === 'netz') {
    fetchSettingsNetwork();
    if (!_settingsNetTimer) _settingsNetTimer = setInterval(fetchSettingsNetwork, 5000);
  } else {
    clearInterval(_settingsNetTimer); _settingsNetTimer = null;
  }
  if (cat === 'system')  refreshVersion();
  if (cat === 'laden')   refreshChargerStatus();
  if (cat === 'display') openDisplaySettings();
}

async function fetchSettingsNetwork() {
  const el = $('settingsNetContent');
  if (!el) return;
  try {
    const [netData, connData] = await Promise.all([
      fetch('/api/network').then(r => r.json()).catch(() => []),
      fetch('/api/connectivity').then(r => r.json()).catch(() => null),
    ]);
    renderSettingsNetwork(el, netData, connData);
  } catch(_) {
    el.innerHTML = '<div class="net-empty">Keine Verbindung</div>';
  }
}

function renderSettingsNetwork(el, netData, connData) {
  let html = '';

  // ── Internet-Sektion (kompakt — Details im Verbindungs-Overlay) ───────────
  if (connData?.router) {
    const r   = connData.router;
    const sl  = connData.starlink ?? {};
    const mob = r.mobile ?? {};
    const isWan  = r.active_type === 'wired';
    const sigPct = mob.signal_pct ?? 0;
    const slOk   = sl.state === 'CONNECTED';
    const dotCls = isWan ? (slOk ? 'ok' : 'warn') : (r.mobile_up ? (sigPct >= 40 ? 'ok' : 'warn') : 'old');
    const sigColor = sigPct >= 60 ? 'var(--green)' : sigPct >= 30 ? 'var(--yellow)' : 'var(--red)';

    const slDot    = sl.state === 'CONNECTED' ? 'ok' : r.wired_up ? 'warn' : 'old';
    const mobDot   = r.mobile_up ? (sigPct >= 40 ? 'ok' : 'warn') : 'old';
    const clientCt = r.wifi_client_count ?? r.wifi_clients?.length ?? 0;
    const slUptime = sl.uptime_s != null ? fmtUptime(sl.uptime_s) : '—';
    const routerUrl = connData.router_url || 'https://192.168.1.1';
    const routerIp  = routerUrl.replace(/^https?:\/\//, '');
    html += `
    <div class="settings-section-title" style="margin-bottom:12px">WLAN</div>
    <div class="net-grid" style="margin-bottom:20px"><div class="net-device-card">
      <div class="net-device-header" style="cursor:pointer" onclick="window.open('${routerUrl}','_blank')">
        <div class="net-device-dot ${dotCls}"></div>
        <div class="net-device-name">RUTX50 Router</div>
        <div class="net-device-src" style="text-decoration:underline;color:var(--accent)">${routerIp}</div>
      </div>
      <div class="net-pgn-list">
        <div class="net-pgn-row">
          <div class="net-pgn-dot-wrap"><div class="net-pgn-dot ok"></div></div>
          <div class="net-pgn-info"><span class="net-pgn-desc">Verbundene Geräte</span></div>
          <div class="net-pgn-iv">${clientCt}</div>
        </div>
        <div class="net-pgn-row">
          <div class="net-pgn-dot-wrap"><div class="net-pgn-dot ${mobDot}"></div></div>
          <div class="net-pgn-info"><span class="net-pgn-desc">Mobilfunk${mob.operator ? ` · ${mob.operator}` : ''}</span></div>
          <div class="net-pgn-iv" style="color:${sigColor}">${r.mobile_up ? `${sigPct} %${mob.ntype ? ` · ${mob.ntype}` : ''}` : 'Getrennt'}</div>
        </div>
        <div class="net-pgn-row">
          <div class="net-pgn-dot-wrap"><div class="net-pgn-dot ${slDot}"></div></div>
          <div class="net-pgn-info"><span class="net-pgn-desc">Starlink Qualität</span></div>
          <div class="net-pgn-iv" style="color:${slDot==='ok'?'var(--green)':slDot==='warn'?'var(--yellow)':'var(--text3)'}">
            ${sl.ping_ms != null ? `${sl.ping_ms.toFixed(0)} ms` : '—'}${sl.downlink_bps != null ? ` · ${fmtBps(sl.downlink_bps)} ↓` : ''}
          </div>
        </div>
        <div class="net-pgn-row">
          <div class="net-pgn-dot-wrap"><div class="net-pgn-dot ${slDot}"></div></div>
          <div class="net-pgn-info"><span class="net-pgn-desc">Starlink Uptime</span></div>
          <div class="net-pgn-iv">${slUptime}</div>
        </div>
        <div class="net-pgn-row">
          <div class="net-pgn-dot-wrap"><div class="net-pgn-dot ok"></div></div>
          <div class="net-pgn-info"><span class="net-pgn-desc">Router verbunden seit</span></div>
          <div class="net-pgn-iv">${fmtUptime(r.active_uptime)}</div>
        </div>
      </div>
    </div></div>`;
  }

  // ── CAN-Bus Geräte ────────────────────────────────────────────────────────
  html += `<div class="settings-section-title" style="margin-bottom:12px">CAN-Bus Geräte</div>`;
  el.innerHTML = html;
  const canWrap = document.createElement('div');
  el.appendChild(canWrap);
  renderNetworkInto(canWrap, netData);
}

function _showSettingsPanel(tab) {
  $('sTank1Name').value   = tanksConfig.tank1?.name ?? '';
  $('sTank1Cap').value    = tanksConfig.tank1?.capacity_l ?? '';
  $('sTank1Color').value  = tanksConfig.tank1?.color ?? '#22c55e';
  $('sTank2Name').value   = tanksConfig.tank2?.name ?? '';
  $('sTank2Cap').value    = tanksConfig.tank2?.capacity_l ?? '';
  $('sTank2Color').value  = tanksConfig.tank2?.color ?? '#3b82f6';
  $('sBattService').value  = batteriesConfig.service_instance ?? 0;
  $('sBattStarter').value  = batteriesConfig.starter_instance ?? 1;
  $('sBattCapacity').value = batteriesConfig.capacity_ah ?? '';
  const psEl = $('sBattPrimary'); if (psEl) psEl.value = batteriesConfig.primary_source ?? 'shunt';
  const dueDays = wartungConfig.due_soon_days ?? 7;
  const slEl = $('sWartDueSoon'); if (slEl) slEl.value = dueDays;
  const valEl = $('sWartDueSoonVal'); if (valEl) valEl.textContent = dueDays;
  $('settingsFeedback').className = 'settings-feedback';
  $('settingsFeedback').textContent = '';
  $('settingsFeedbackWartung').className = 'settings-feedback';
  $('settingsFeedbackWartung').textContent = '';
  $('settingsFeedbackLaden').className = 'settings-feedback';
  $('settingsFeedbackLaden').textContent = '';
  $('updateFeedback').textContent = '';
  $('updateBtn').disabled = false;
  renderVersionInfo();   // sofort aus Cache anzeigen
  refreshVersion();      // im Hintergrund aktualisieren
  switchSettingsCat(tab || 'tanks');
  $('settingsOverlay').classList.remove('hidden');
}

function openSettings(tab) {
  _closeAllOverlays();
  history.pushState({ overlay: 'settings' }, '', '#settings');
  _showSettingsPanel(tab);
}

function closeSettings() {
  clearInterval(_settingsNetTimer); _settingsNetTimer = null;
  $('settingsOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

async function saveWartungSettings() {
  const fb  = $('settingsFeedbackWartung');
  const days = Math.max(1, Math.min(14, _intOr($('sWartDueSoon')?.value, 7)));
  try {
    const data = await fetch('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wartung: { due_soon_days: days } }),
    }).then(_jsonOrThrow);
    if (data.wartung) wartungConfig = { ...wartungConfig, ...data.wartung };
    updateWartungTopbar();
    updateWartungHomeTile();
    _fbOk(fb);
  } catch(e) {
    _fbError(fb, e.message);
  }
}

async function saveSettings() {
  const btn = $('saveSettingsBtn');
  const fb  = $('settingsFeedback');
  btn.disabled = true;

  const body = {
    tanks: {
      tank1: { name: $('sTank1Name').value.trim() || 'Tank 1',
               capacity_l: _intOr($('sTank1Cap').value, 200),
               color: $('sTank1Color').value },
      tank2: { name: $('sTank2Name').value.trim() || 'Tank 2',
               capacity_l: _intOr($('sTank2Cap').value, 120),
               color: $('sTank2Color').value },
    },
    batteries: {
      // Instanz 0 ist eine gültige CAN-Instanz — früher wurde eine getippte 0
      // bei der Starterbatterie stillschweigend zu 1.
      service_instance: _intOr($('sBattService').value, 0),
      starter_instance: _intOr($('sBattStarter').value, 1),
      primary_source:   $('sBattPrimary')?.value ?? 'shunt',
      // leeres Feld = keine Kapazität hinterlegt
      capacity_ah:      _floatOr($('sBattCapacity').value, null),
    },
  };

  try {
    const data = await fetch('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(_jsonOrThrow);

    if (data.tanks)     tanksConfig     = data.tanks;
    if (data.batteries) batteriesConfig = data.batteries;
    $('tank1Name').textContent = tanksConfig.tank1?.name ?? 'Tank 1';
    $('tank2Name').textContent = tanksConfig.tank2?.name ?? 'Tank 2';
    renderTank(1, tankLast[1]);
    renderTank(2, tankLast[2]);
    updateDualTiles();

    _fbOk(fb);
  } catch(e) {
    _fbError(fb, e.message);
  }
  btn.disabled = false;
}

// ── Ladesteuerung ─────────────────────────────────────────────────────────────

let _chargerStatus = null;

// Modus-Beschriftung mit SVG-Icon (Projektregel: keine Emojis im UI).
// Rückgabe ist HTML — nur über innerHTML einsetzen, nie über textContent.
const _CHG_MODE_TEXT = { harbor: 'Hafen', full: 'Vollladung', balance: 'Balancing' };
const _CHG_MODE_ICON = { harbor: 'anchor', full: 'bolt',      balance: 'scale'     };

function _chgModeLabel(mode) {
  const text = _CHG_MODE_TEXT[mode];
  if (!text) return _esc(String(mode ?? '—'));
  return `<span style="display:inline-flex;align-items:center;gap:5px;vertical-align:-3px">`
       + `${icon(_CHG_MODE_ICON[mode], { size: 14 })}${text}</span>`;
}

async function refreshChargerStatus() {
  try {
    _chargerStatus = await fetch('/api/charger').then(r => r.json());
  } catch(_) { return; }
  _renderChargerStatus();
  _populateChargerInputs();
}

function _renderChargerStatus() {
  const d = _chargerStatus;
  if (!d) return;

  // Badge auf der Batterie-Kachel
  const badge = $('chgModeBadge');
  if (badge) {
    const modeColor = { harbor: 'var(--text3)', full: 'var(--yellow)', balance: 'var(--green)' }[d.mode] || 'var(--text2)';
    badge.innerHTML = _chgModeLabel(d.mode);
    badge.style.color = modeColor;
    badge.style.display = '';
  }

  // Status-Karte im Settings-Tab
  const body = $('chgStatusBody');
  if (!body) return;

  const fmtV = v => v != null ? v.toFixed(2) + ' V' : '—';
  const av = d.actual_absorption_v;
  const fv = d.actual_float_v;
  const lastRead = d.actual_last_read ? new Date(d.actual_last_read).toLocaleString('de-DE') : null;

  let matchHtml = '';
  if (d.preset_match === null) {
    matchHtml = '<span style="color:var(--text3)">Noch nicht abgefragt</span>';
  } else if (d.preset_match === 'custom') {
    matchHtml = `<span style="color:#f59e0b;font-weight:600">${icon('warning', { size: 13 })} Extern geändert</span>`;
  } else {
    const presetLabel = { harbor: 'Hafen', full: 'Vollladung', balance: 'Balancing' }[d.preset_match] || d.preset_match;
    matchHtml = `<span style="color:var(--green);font-weight:600">${icon('check', { size: 13 })} ${presetLabel}</span>`;
  }

  let modeLabel = _chgModeLabel(d.mode);
  if (d.mode === 'harbor' && d.harbor_voltage != null) {
    const hv = d.harbor_voltage.toFixed(2);
    const offV = d.settings?.harbor?.off_voltage ?? 11.5;
    modeLabel += d.harbor_voltage <= offV + 0.05 ? ` · Halten (${hv} V)` : ` · Laden (${hv} V)`;
  }
  const modeColor = { harbor: 'var(--text2)', full: 'var(--yellow)', balance: 'var(--green)' }[d.mode] || 'var(--text2)';

  const balDue  = d.balance_due;
  const balDays = d.days_until_balance;
  const balInfo = d.last_balance
    ? `Letztes Balancing: ${d.last_balance} · ${balDue ? '<span style="color:#ef4444;font-weight:600">Jetzt fällig</span>' : `in ${balDays} T`}`
    : '<span style="color:#ef4444">Noch nie balanciert</span>';

  // Per-Gerät Soll-Werte
  let devRows = '';
  if (d.device_setpoints && d.device_setpoints.length > 0) {
    devRows = d.device_setpoints.map(dev =>
      `<div style="display:flex;justify-content:space-between;align-items:center">
        <span style="color:var(--text3)">${_esc(dev.label)}${dev.is_solar ? ' ' + icon('solar', { size: 13 }) : ''}</span>
        <span style="font-size:12px">${dev.absorption_v.toFixed(2)} / ${dev.float_v.toFixed(2)} V</span>
      </div>`
    ).join('');
  } else {
    devRows = `<div style="color:var(--text3);font-size:12px">Keine Geräte ausgewählt</div>`;
  }

  body.innerHTML = `
    <div style="display:grid;gap:7px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="color:var(--text3)">Aktiver Modus</span>
        <span style="color:${modeColor};font-weight:600">${modeLabel}</span>
      </div>
      <div style="font-size:11px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-top:2px">Soll-Werte (Abs / Float)</div>
      ${devRows}
      <div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid var(--border);padding-top:6px;margin-top:2px">
        <span style="color:var(--text3)">IP43 Ist-Werte</span>
        <span style="font-size:12px">${fmtV(av)} / ${fmtV(fv)} · ${matchHtml}</span>
      </div>
      <div style="font-size:12px;color:var(--text3)">
        ${balInfo}
        ${lastRead ? `<br>Zuletzt abgefragt: ${lastRead}` : ''}
      </div>
    </div>`;
}

function _populateChargerInputs() {
  const s = _chargerStatus?.settings;
  if (!s) return;
  if ($('sChgHarborAbs'))    $('sChgHarborAbs').value   = s.harbor?.absorption_v  ?? 13.8;
  if ($('sChgHarborFloat'))  $('sChgHarborFloat').value = s.harbor?.float_v        ?? 13.3;
  if ($('sChgTargetSoc'))    $('sChgTargetSoc').value   = s.harbor?.target_soc    ?? 80;
  if ($('sChgHoldV'))        $('sChgHoldV').value       = s.harbor?.hold_voltage  ?? 13.2;
  if ($('sChgSocRamp'))      $('sChgSocRamp').value     = s.harbor?.soc_ramp_pct  ?? 15;
  if ($('sChgFullAbs'))      $('sChgFullAbs').value     = s.full?.absorption_v     ?? 14.4;
  if ($('sChgFullFloat'))    $('sChgFullFloat').value   = s.full?.float_v           ?? 13.5;
  if ($('sChgBalInterval'))  $('sChgBalInterval').value = s.balance_interval_days  ?? 30;
  if ($('sChgBalMinHours'))  $('sChgBalMinHours').value = s.balance_min_hours       ?? 2;
  if ($('sChgBalEndA'))      $('sChgBalEndA').value     = s.balance_end_current_a  ?? 1.0;
  if ($('sChgDevIp43'))      $('sChgDevIp43').checked  = s.devices?.ip43?.enabled  ?? true;
  if ($('sChgDevMppt'))      $('sChgDevMppt').checked  = s.devices?.mppt?.enabled  ?? true;
  if ($('sChgDevOrion'))     $('sChgDevOrion').checked = s.devices?.orion?.enabled ?? false;
  if ($('sChgSolarOffset'))  $('sChgSolarOffset').value = s.solar_priority_offset_v ?? 0.3;
  // live preview — einmalig Listener anhängen
  ['sChgSolarOffset', 'sChgHarborAbs'].forEach(id => {
    const el = $(id);
    if (el && !el.dataset.previewBound) {
      el.dataset.previewBound = '1';
      el.addEventListener('input', _updateOffsetPreview);
    }
  });
  _updateOffsetPreview();
}

function _updateOffsetPreview() {
  const el = $('sChgOffsetPreview');
  if (!el) return;
  // parseFloat liefert bei leerem Feld NaN (nicht null) — `?? 0.3` griff hier
  // nie und die Vorschau zeigte "NaN V".
  const harborAbs = _floatOr($('sChgHarborAbs')?.value, 13.8);
  const offset    = _floatOr($('sChgSolarOffset')?.value, 0.3);
  el.textContent  = `Solar: ${harborAbs.toFixed(2)} V · Nicht-Solar: ${(harborAbs - offset).toFixed(2)} V (nur Hafen-Modus)`;
}

async function saveChargerSettings() {
  const fb = $('settingsFeedbackLaden');
  const body = {
    harbor:  {
      absorption_v: _floatOr($('sChgHarborAbs').value,   13.8),
      float_v:      _floatOr($('sChgHarborFloat').value, 13.3),
      target_soc:   _intOr($('sChgTargetSoc')?.value,    80),
      hold_voltage: _floatOr($('sChgHoldV')?.value,      13.2),
      // Rampe 0 = kein Anstieg, ein gueltiger Wunsch
      soc_ramp_pct: _intOr($('sChgSocRamp')?.value,      15),
    },
    full:    {
      absorption_v: _floatOr($('sChgFullAbs').value,   14.4),
      float_v:      _floatOr($('sChgFullFloat').value, 13.5),
    },
    balance: {
      absorption_v: _floatOr($('sChgFullAbs').value,   14.4),
      float_v:      _floatOr($('sChgFullFloat').value, 13.5),
    },
    balance_interval_days:  _intOr($('sChgBalInterval').value,     30),
    balance_min_hours:      _floatOr($('sChgBalMinHours').value,   2),
    balance_end_current_a:  _floatOr($('sChgBalEndA').value,       1.0),
    // Offset 0 = Solar und Nicht-Solar gleich hoch, ebenfalls gueltig
    solar_priority_offset_v: _floatOr($('sChgSolarOffset')?.value, 0.3),
    devices: {
      ip43:  { enabled: $('sChgDevIp43')?.checked  ?? true  },
      mppt:  { enabled: $('sChgDevMppt')?.checked  ?? true  },
      orion: { enabled: $('sChgDevOrion')?.checked ?? false },
    },
  };
  try {
    const data = await fetch('/api/charger/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(_jsonOrThrow);
    _chargerStatus = data;
    _renderChargerStatus();
    _populateChargerInputs();
    _fbOk(fb);
  } catch(e) {
    _fbError(fb, e.message);
  }
}

async function setChargerMode(mode) {
  try {
    _chargerStatus = await fetch('/api/charger/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }).then(_jsonOrThrow);
    _renderChargerStatus();
  } catch(e) {
    const fb = $('settingsFeedbackLaden');
    if (fb) _fbError(fb, e.message || 'Fehler');
  }
}

// ── Settings ───────────────────────────────────────────────────────────────

let _settingsNetTimer = null;
let _versionInfo = null;   // gecachte Versions-Info (periodisch aktualisiert)

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
    html += ` &nbsp;<span style="color:var(--green)">✓ aktuell</span>`;
  else
    html += ` &nbsp;<span style="color:var(--text3)">(Update-Status wird geprüft…)</span>`;
  vi.innerHTML = html;
}

function switchSettingsCat(cat) {
  ['tanks', 'batt', 'netz', 'system'].forEach(c =>
    $(`setPane-${c}`)?.classList.toggle('active', c === cat)
  );
  document.querySelectorAll('.set-nav-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.cat === cat)
  );
  if (cat === 'netz') {
    fetchSettingsNetwork();
    if (!_settingsNetTimer) _settingsNetTimer = setInterval(fetchSettingsNetwork, 5000);
  } else {
    clearInterval(_settingsNetTimer); _settingsNetTimer = null;
  }
  if (cat === 'system') refreshVersion();
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

    html += `
    <div class="settings-section-title" style="margin-bottom:12px">Internet</div>
    <div class="net-grid" style="margin-bottom:20px"><div class="net-device-card">
      <div class="net-device-header">
        <div class="net-device-dot ${dotCls}"></div>
        <div class="net-device-name">RUTX50 Router</div>
        <div class="net-device-src">${isWan ? '🛰 Starlink' : `📶 ${mob.operator || 'Mobilfunk'}`}</div>
      </div>
      <div class="net-pgn-list">
        <div class="net-pgn-row">
          <div class="net-pgn-desc">Aktiver Uplink</div>
          <div class="net-pgn-iv">${isWan ? 'WAN (Starlink)' : `Mobilfunk · ${mob.ntype || '?'}`}</div>
        </div>
        <div class="net-pgn-row">
          <div class="net-pgn-desc">WAN / Mobilfunk</div>
          <div class="net-pgn-iv">${r.wired_up ? '✓' : '–'} / ${r.mobile_up ? '✓' : '–'}</div>
        </div>
        ${r.mobile_up ? `<div class="net-pgn-row">
          <div class="net-pgn-desc">Signalqualität</div>
          <div class="net-pgn-iv" style="color:${sigColor}">${sigPct} %</div>
        </div>` : ''}
        <div class="net-pgn-row">
          <div class="net-pgn-desc">Uptime</div>
          <div class="net-pgn-iv">${fmtUptime(r.active_uptime)}</div>
        </div>
        ${sl.ping_ms != null ? `<div class="net-pgn-row">
          <div class="net-pgn-desc">Starlink Latenz</div>
          <div class="net-pgn-iv">${sl.ping_ms.toFixed(0)} ms · ${fmtBps(sl.downlink_bps)} ↓</div>
        </div>` : ''}
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
  $('sBattService').value = batteriesConfig.service_instance ?? 0;
  $('sBattStarter').value = batteriesConfig.starter_instance ?? 1;
  const psEl = $('sBattPrimary'); if (psEl) psEl.value = batteriesConfig.primary_source ?? 'shunt';
  $('settingsFeedback').className = 'settings-feedback';
  $('settingsFeedback').textContent = '';
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

async function saveSettings() {
  const btn = $('saveSettingsBtn');
  const fb  = $('settingsFeedback');
  btn.disabled = true;

  const body = {
    tanks: {
      tank1: { name: $('sTank1Name').value.trim() || 'Tank 1',
               capacity_l: parseInt($('sTank1Cap').value) || 200,
               color: $('sTank1Color').value },
      tank2: { name: $('sTank2Name').value.trim() || 'Tank 2',
               capacity_l: parseInt($('sTank2Cap').value) || 120,
               color: $('sTank2Color').value },
    },
    batteries: {
      service_instance: parseInt($('sBattService').value) || 0,
      starter_instance: parseInt($('sBattStarter').value) || 1,
      primary_source:   $('sBattPrimary')?.value ?? 'shunt',
    },
  };

  try {
    const data = await fetch('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => r.json());

    if (data.tanks)     tanksConfig     = data.tanks;
    if (data.batteries) batteriesConfig = data.batteries;
    $('tank1Name').textContent = tanksConfig.tank1?.name ?? 'Tank 1';
    $('tank2Name').textContent = tanksConfig.tank2?.name ?? 'Tank 2';
    renderTank(1, tankLast[1]);
    renderTank(2, tankLast[2]);
    updateDualTiles();

    fb.className = 'settings-feedback show';
    fb.textContent = 'Gespeichert ✓';
    setTimeout(() => fb.classList.remove('show'), 2500);
  } catch(e) {
    fb.className = 'settings-feedback error show';
    fb.textContent = 'Fehler beim Speichern';
  }
  btn.disabled = false;
}

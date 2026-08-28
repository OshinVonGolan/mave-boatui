// ── Connectivity ───────────────────────────────────────────────────────────

let _connData = {};
// Bleibt als null bestehen: _closeAllOverlays() in charts.js räumt diese
// Variable noch auf. Das Overlay hat KEINEN eigenen Timer mehr — der globale
// Poller in init.js rendert es mit (siehe fetchConnectivity).
var _connOverlayTimer = null;

// Hier stand starlinkSleep(): sie schickte einen Schlaf-/Weckbefehl an die
// Antenne. Auf Anweisung des Eigners entfernt — Verdacht, dass die
// Steuerbefehle der Starlink schaden. Der Status wird weiter angezeigt.

function fmtUptime(s) {
  if (!s) return '--';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h > 24) return `${Math.floor(h/24)} T ${h % 24} h`;
  if (h > 0)  return `${h} h ${m} min`;
  return `${m} min`;
}
function fmtBps(bps) {
  if (bps == null || bps <= 0) return '0 bps';
  if (bps >= 1e6) return (bps/1e6).toFixed(1) + ' Mbps';
  if (bps >= 1e3) return (bps/1e3).toFixed(0) + ' kbps';
  return Math.round(bps) + ' bps';
}

async function fetchConnectivity() {
  try {
    _connData = await fetch('/api/connectivity').then(r => r.json());
    updateConnectivityIcon(_connData);
    if (!$('connInetOverlay').classList.contains('hidden')) renderConnectivity(_connData);
  } catch(_) {}
}

function openConnectivity() {
  _closeAllOverlays();
  history.pushState({ overlay: 'connectivity' }, '', '#connectivity');
  $('connInetOverlay').classList.remove('hidden');
  _navActive('connInetBtn');
  renderConnectivity(_connData);
  // Kein eigener Intervall-Timer: der globale Poller (init.js) ruft
  // fetchConnectivity() auf und rendert das offene Overlay gleich mit.
  fetchConnectivity();
}
function closeConnectivity() {
  $('connInetOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

const _SVG_SATELLITE = `<path d="M3.5 18.5l-2-2L13 5l2 2L3.5 18.5z" stroke-width="1.5"/><path d="M8 20l4-4"/><path d="M20 8l-4 4"/><circle cx="19" cy="5" r="2" fill="currentColor" stroke="none"/><path d="M15 5a4 4 0 0 1 4 4" stroke-width="1.5"/>`;
const _SVG_MOBILE    = `<rect x="2" y="15" width="3.5" height="6" rx=".7" fill="currentColor" stroke="none"/><rect x="8" y="10" width="3.5" height="11" rx=".7" fill="currentColor" stroke="none"/><rect x="14" y="5" width="3.5" height="16" rx=".7" fill="currentColor" stroke="none"/><rect x="20" y="1" width="3.5" height="20" rx=".7" fill="currentColor" stroke="none" opacity=".3"/>`;
const _SVG_WIFI      = `<path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1.5" fill="currentColor" stroke="none"/>`;

// Baut ein Inline-SVG aus den Pfaden oben — gleiche Form wie icon() im
// Icon-System, nur mit den Connectivity-eigenen Motiven (Schüssel/Antenne).
function _connSvg(paths, size = 22) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" `
       + `stroke="currentColor" stroke-width="2" stroke-linecap="round" `
       + `stroke-linejoin="round" aria-hidden="true" focusable="false">${paths}</svg>`;
}

function updateConnectivityIcon(d) {
  const dot   = $('connInetDot');
  const svgEl = document.querySelector('#connInetBtn svg');
  if (!dot || !svgEl) return;

  if (!d.router) {
    svgEl.innerHTML = _SVG_WIFI;
    svgEl.style.color = '';
    dot.style.background = 'var(--text3)';
    return;
  }
  const r  = d.router;
  const sl = d.starlink ?? {};

  if (r.active_type === 'wired') {
    const color = sl.state === 'CONNECTED' ? 'var(--green)' : 'var(--yellow)';
    svgEl.innerHTML = _SVG_SATELLITE;
    svgEl.style.color = color;
    dot.style.background = color;
  } else if (r.mobile_up) {
    const sig   = r.mobile?.signal_pct ?? 0;
    const color = sig >= 60 ? 'var(--green)' : sig >= 30 ? 'var(--yellow)' : 'var(--red)';
    svgEl.innerHTML = _SVG_MOBILE;
    svgEl.style.color = color;
    dot.style.background = color;
  } else {
    svgEl.innerHTML = _SVG_WIFI;
    svgEl.style.color = 'var(--red)';
    dot.style.background = 'var(--red)';
  }
}

function renderConnectivity(d) {
  const body = $('connInetBody');
  if (!d.router) {
    body.innerHTML = '<div style="color:var(--text3);text-align:center;padding:60px 20px">Warte auf ersten Poll…</div>';
    return;
  }
  const r   = d.router;
  const sl  = d.starlink ?? {};
  const mob = r.mobile ?? {};

  const isWan    = r.active_type === 'wired';
  const slOk     = sl.state === 'CONNECTED';
  const slColor  = slOk ? 'var(--green)' : (sl.reachable === false ? 'var(--red)' : 'var(--yellow)');
  const slDotCls = slOk ? 'ok' : (sl.reachable === false ? '' : 'warn');
  const pingColor = !sl.ping_ms ? 'var(--text2)' : sl.ping_ms < 30 ? 'var(--green)' : sl.ping_ms < 80 ? 'var(--yellow)' : 'var(--red)';
  const sigPct   = mob.signal_pct ?? 0;
  const sigColor = sigPct >= 60 ? 'var(--green)' : sigPct >= 30 ? 'var(--yellow)' : 'var(--red)';
  const mobDotCls = r.mobile_up ? (sigPct >= 40 ? 'ok' : 'warn') : '';

  const activeLabel = isWan ? 'Starlink (WAN)' : (r.mobile_up ? `Mobilfunk — ${mob.operator || ''}` : 'Kein aktiver Uplink');
  const activeIcon  = isWan ? _connSvg(_SVG_SATELLITE)
                            : (r.mobile_up ? _connSvg(_SVG_MOBILE) : icon('warning', { size: 22 }));
  const activeColor = isWan ? slColor : (r.mobile_up ? sigColor : 'var(--red)');
  const fallback    = isWan && r.mobile_up
    ? `Fallback verfügbar: Mobilfunk — ${mob.operator || ''} ${mob.ntype || ''}`
    : (!isWan && r.wired_up ? 'Failover verfügbar: Starlink (WAN)' : '');

  const slObs  = sl.obstructed ? `${((sl.fraction_obstructed ?? 0) * 100).toFixed(1)} %` : 'keine';
  const slDrop = sl.drop_rate != null ? (sl.drop_rate * 100).toFixed(2) + ' %' : '--';
  const alertEntries = Object.keys(sl.alerts ?? {});
  const alertHtml = alertEntries.length
    ? alertEntries.map(k => `<div class="bms-flag warn">! ${k.replace(/_/g,' ')}</div>`).join('')
    : `<div class="bms-flag ok">${icon('check', { size: 13 })} Keine Alarme</div>`;

  body.innerHTML = `
    <div class="conn-active-banner">
      <div style="font-size:11px;color:var(--text3);letter-spacing:.1em;text-transform:uppercase;margin-bottom:2px">Aktiver Uplink</div>
      <div class="conn-active-type">
        <span class="conn-active-icon" style="display:inline-flex;align-items:center;color:${activeColor}">${activeIcon}</span>
        <span class="conn-active-label" style="color:${activeColor}">${activeLabel}</span>
      </div>
      ${fallback ? `<div class="conn-fallback">${fallback}</div>` : ''}
    </div>

    <div class="conn-cards-grid">

      <!-- Starlink -->
      <div class="net-device-card">
        <div class="net-device-header">
          <div class="net-device-dot ${slDotCls}"></div>
          <div class="net-device-name">Starlink Mini</div>
          <div class="net-device-src" style="color:${slColor}">${sl.state ?? '--'}</div>
        </div>
        <div class="conn-card-body">
          <div class="bd-sec-grid" style="margin-bottom:10px">
            <div class="bd-sec-item">
              <div class="bd-label">Latenz</div>
              <div class="bd-sec-val" style="color:${pingColor}">${sl.ping_ms != null ? sl.ping_ms.toFixed(0) + ' ms' : '--'}</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">Paketverlust</div>
              <div class="bd-sec-val">${slDrop}</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">Download</div>
              <div class="bd-sec-val">${fmtBps(sl.downlink_bps)}</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">Upload</div>
              <div class="bd-sec-val">${fmtBps(sl.uplink_bps)}</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">Obstruktion</div>
              <div class="bd-sec-val">${slObs}</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">Uptime</div>
              <div class="bd-sec-val">${fmtUptime(sl.uptime_s)}</div>
            </div>
          </div>
          <div class="bms-flags-row">${alertHtml}</div>
        </div>
      </div>

      <!-- Mobilfunk -->
      <div class="net-device-card">
        <div class="net-device-header">
          <div class="net-device-dot ${mobDotCls}"></div>
          <div class="net-device-name">Mobilfunk</div>
          <div class="net-device-src">${mob.operator || '--'} ${mob.ntype || ''}</div>
        </div>
        <div class="conn-card-body">
          <div class="bd-sec-grid" style="margin-bottom:10px">
            <div class="bd-sec-item">
              <div class="bd-label">Qualität</div>
              <div class="bd-sec-val" style="color:${sigColor}">${sigPct} %</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">Verbindung</div>
              <div class="bd-sec-val" style="font-size:13px">${mob.conntype || '--'}</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">RSSI</div>
              <div class="bd-sec-val" style="font-size:13px">${mob.rssi ?? '--'} dBm</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">RSRP</div>
              <div class="bd-sec-val" style="font-size:13px">${mob.rsrp ?? '--'} dBm</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">RSRQ</div>
              <div class="bd-sec-val" style="font-size:13px">${mob.rsrq ?? '--'} dB</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">SINR</div>
              <div class="bd-sec-val" style="font-size:13px">${mob.sinr ?? '--'} dB</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">Band</div>
              <div class="bd-sec-val" style="font-size:13px">${mob.band || '--'}</div>
            </div>
            <div class="bd-sec-item">
              <div class="bd-label">Temperatur</div>
              <div class="bd-sec-val">${mob.temperature != null ? mob.temperature + ' °C' : '--'}</div>
            </div>
          </div>
          <div class="conn-signal-bar-wrap">
            <div class="conn-signal-bar-fill" style="width:${sigPct}%;background:${sigColor}"></div>
          </div>
          <div style="font-size:11px;color:var(--text3);text-align:right;margin-top:4px">${sigPct} % Signalqualität</div>
        </div>
      </div>

    </div>

    <!-- WLAN-Geräte -->
    ${(() => {
      const clients = r.wifi_clients ?? [];
      if (!clients.length) return '';
      // stärkstes Signal zuerst
      const sorted = [...clients].sort((a, b) => (b.signal ?? -999) - (a.signal ?? -999));
      const rows = sorted.map(c => {
        const sigColor = c.signal == null ? 'var(--text3)' : c.signal > -60 ? 'var(--green)' : c.signal > -75 ? 'var(--yellow)' : 'var(--red)';
        const name = c.hostname || c.mac || '?';
        const band = c.band ? `<span style="font-size:10px;color:var(--text3);border:1px solid var(--border);border-radius:4px;padding:1px 5px">${_esc(c.band)}</span>` : '';
        return `<div class="wifi-client-row">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="${sigColor}" stroke-width="2" stroke-linecap="round" style="flex-shrink:0">
            <path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M5 12.55a11 11 0 0 1 14.08 0"/>
            <path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1.5" fill="${sigColor}" stroke="none"/>
          </svg>
          <span class="wifi-client-name" title="${_esc(c.mac || '')}">${_esc(name)}</span>
          ${band}
          <span class="wifi-client-ip">${c.ip || ''}</span>
          <span class="wifi-client-sig" style="color:${sigColor}">${c.signal != null ? c.signal + ' dBm' : ''}</span>
        </div>`;
      }).join('');
      return `<div class="net-device-card" style="margin-top:14px">
        <div class="net-device-header">
          <div class="net-device-dot ok"></div>
          <div class="net-device-name">WLAN — verbundene Geräte</div>
          <div class="net-device-src">${clients.length} online</div>
        </div>
        <div class="conn-card-body">${rows}</div>
      </div>`;
    })()}

    <div style="font-size:11px;color:var(--text3);text-align:right;margin-top:8px">
      Aktualisiert: ${d.ts ? new Date(d.ts * 1000).toLocaleTimeString('de-DE') : '--'}
    </div>`;
}

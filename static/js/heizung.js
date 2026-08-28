// ── Heizung (Stoker) ───────────────────────────────────────────────────────
// Kachel auf der Startseite mit dem Noetigsten, alles Weitere auf der
// Detailseite. Gesprochen wird ausschliesslich mit dem Pi (/api/heizung/*),
// nie direkt mit dem Hub: der vertraegt laut Doku hoechstens 1 Abfrage pro
// Sekunde und vier WebSockets im ganzen Netz.
//
// Der Relaisbetrieb aus der Geraetedoku ist bewusst nicht abgebildet.

let _hzDaten = null;          // letzter Schnappschuss von /api/heizung
let _hzFehler = null;         // Meldung des letzten Schaltbefehls
let _hzBusy = new Set();      // laufende Befehle, damit Knoepfe nicht doppeln

const HZ_PRESETS = ['Frostwacht', 'Nacht', 'Tag', 'Boiler'];

const HZ_ZUSTAND = {
  off:      { text: 'Aus',        farbe: 'var(--text2)' },
  starting: { text: 'Startet',    farbe: 'var(--yellow)' },
  running:  { text: 'Läuft',      farbe: 'var(--green)' },
  stopping: { text: 'Stoppt',     farbe: 'var(--yellow)' },
  cooldown: { text: 'Nachlauf',   farbe: 'var(--accent)' },
  fault:    { text: 'Störung',    farbe: 'var(--red)' },
};

const HZ_MODUS = { off: 'Aus', auto: 'Automatik', manual: 'Hand' };

const HZ_CONN = {
  online:     { text: 'verbunden', farbe: 'var(--green)' },
  connecting: { text: 'verbindet', farbe: 'var(--yellow)' },
  stale:      { text: 'veraltet',  farbe: 'var(--yellow)' },
  offline:    { text: 'offline',   farbe: 'var(--text3)' },
  unknown:    { text: 'unbekannt', farbe: 'var(--text3)' },
};

const _hzT = v => v == null ? '--' : Number(v).toFixed(1);

/** Laufzeit in Stunden und Minuten. */
function _hzDauer(s) {
  if (s == null) return '--';
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

// ── Abfrage ─────────────────────────────────────────────────────────────────

async function ladeHeizung(fuerDetail) {
  try {
    const r = await fetch('/api/heizung');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    _hzDaten = await r.json();
  } catch (e) {
    // Nicht laut werden: der Hub ist derzeit ohnehin nicht im Netz, und die
    // Kachel soll deswegen nicht nach Defekt aussehen.
    console.debug('Heizung nicht abrufbar:', e);
    _hzDaten = null;
  }
  updateHeizungKachel();
  if (fuerDetail || !$('heizungOverlay')?.classList.contains('hidden')) renderHeizungDetail();
}

const hzPoller = createPoller(() => ladeHeizung(false), 6000);
const hzDetailPoller = createPoller(() => ladeHeizung(true), 3000);

// ── Kachel ──────────────────────────────────────────────────────────────────

function updateHeizungKachel() {
  const body = $('heizungBody');
  if (!body) return;
  const d = _hzDaten;
  const st = d?.state;

  if (!st) {
    // Kein Gerät erreichbar: dezent bleiben, nicht als Störung darstellen.
    body.innerHTML = `<div class="hz-leer">
      ${d && !d.configured
        ? 'Keine Heizung eingerichtet — unter Einstellungen › Heizung eintragen.'
        : 'Heizung derzeit nicht erreichbar.'}</div>`;
    return;
  }

  const h = st.heater || {};
  const z = HZ_ZUSTAND[h.state] || { text: h.state || '--', farbe: 'var(--text2)' };
  const presetIdx = st.preset?.index;

  const presets = HZ_PRESETS.map((name, i) => `
    <button class="hz-preset${presetIdx === i ? ' an' : ''}"
      onclick="event.stopPropagation();hzPreset(${i})"
      ${_hzBusy.has('preset') ? 'disabled' : ''}>${name}</button>`).join('')
    + `<button class="hz-preset${presetIdx == null ? ' an' : ''}"
        onclick="event.stopPropagation();hzPreset('none')"
        ${_hzBusy.has('preset') ? 'disabled' : ''} title="Kein Preset">Aus</button>`;

  const raeume = (st.rooms || []).map(r => {
    const aus = r.enabled === false;
    const kalt = r.conn !== 'online';
    return `<div class="hz-raum${aus ? ' hz-raum-aus' : ''}">
      <span class="hz-raum-name">${_esc(r.name)}</span>
      <span class="hz-raum-ist">${_hzT(r.roomTemp)}<i>°</i></span>
      <span class="hz-raum-pfeil">→</span>
      <span class="hz-raum-soll">${_hzT(r.target)}<i>°</i></span>
      <span class="hz-raum-flag">${
        kalt ? '<span class="hz-punkt" style="background:var(--text3)" title="nicht verbunden"></span>'
        : aus ? '<span class="hz-aus-txt">aus</span>'
        : r.wantsHeat ? '<span class="hz-punkt" style="background:var(--orange)" title="fordert Wärme"></span>'
        : ''}</span>
    </div>`;
  }).join('');

  // Ein Schaltbefehl steht 60 s an, bevor er wirkt — das gehoert sichtbar auf
  // die Kachel, sonst wirkt die Anlage traege statt bedacht.
  const warten = h.pendingCommand?.remainingS > 0
    ? `<div class="hz-pending">Schaltbefehl in ${h.pendingCommand.remainingS} s
        <button class="hz-mini" onclick="event.stopPropagation();hzAbbrechen()">Abbrechen</button></div>`
    : '';

  body.innerHTML = `
    <div class="hz-kopf">
      <span class="hz-zustand" style="color:${z.farbe}">${z.text}</span>
      ${h.powerLevel != null && h.state === 'running'
        ? `<span class="hz-leistung">${h.powerLevel} %</span>` : ''}
      <span class="hz-vorlauf">${h.flowTemp != null
        ? `Vorlauf ${_hzT(h.flowTemp)} °C` : HZ_MODUS[h.mode] || ''}</span>
    </div>
    ${h.available === false && h.availabilityText
      ? `<div class="hz-hinweis">${_esc(h.availabilityText)}</div>` : ''}
    ${warten}
    <div class="hz-presets">${presets}</div>
    <div class="hz-raeume">${raeume}</div>
    ${d.demo ? '<div class="hz-hinweis hz-demo">Testdaten — kein Gerät verbunden</div>' : ''}
    ${_hzFehler ? `<div class="hz-hinweis hz-fehler">${_esc(_hzFehler)}</div>` : ''}`;
}

// ── Detailseite ─────────────────────────────────────────────────────────────

function openHeizung() {
  _closeAllOverlays();
  history.pushState({ overlay: 'heizung' }, '', '#heizung');
  $('heizungOverlay').classList.remove('hidden');
  hzDetailPoller.start();
  renderHeizungDetail();
}

function closeHeizung() {
  $('heizungOverlay').classList.add('hidden');
  hzDetailPoller.stop();
  history.replaceState(null, '', location.pathname);
}

function renderHeizungDetail() {
  const box = $('heizungDetail');
  if (!box) return;
  const d = _hzDaten, st = d?.state;
  if (!st) {
    box.innerHTML = `<div class="sb-card"><div class="hz-leer">${
      d && !d.configured
        ? 'Es ist keine Heizung eingerichtet. Unter Einstellungen › Heizung die Adresse eintragen.'
        : 'Die Heizung ist derzeit nicht erreichbar.'}</div></div>`;
    return;
  }
  const h = st.heater || {};
  const z = HZ_ZUSTAND[h.state] || { text: h.state || '--', farbe: 'var(--text2)' };

  const modusKnoepfe = ['off', 'auto', 'manual'].map(m => `
    <button class="hz-preset${h.mode === m ? ' an' : ''}"
      onclick="hzModus('${m}')" ${_hzBusy.has('heater') ? 'disabled' : ''}>${HZ_MODUS[m]}</button>`).join('');

  const handSchalter = h.mode === 'manual' ? `
    <div class="hz-presets" style="margin-top:8px">
      <button class="hz-preset${h.command === 'on' ? ' an' : ''}" onclick="hzHand('on')">Ein</button>
      <button class="hz-preset${h.command === 'off' ? ' an' : ''}" onclick="hzHand('off')">Aus</button>
    </div>` : '';

  const kennzahl = (l, v) => `<div class="st"><span class="st-l">${l}</span>
    <span class="st-v">${v}</span></div>`;

  const heizKarte = `<div class="sb-card">
    <div class="sb-hd">${icon('thermometer', {size: 14})} Heizgerät
      <span class="chip ${h.available === false ? 'err' : 'on'}">${
        _esc(h.availabilityText || (h.available === false ? 'nicht verfügbar' : 'verfügbar'))}</span>
    </div>
    <div class="sb-stats">
      ${kennzahl('Zustand', `<span style="color:${z.farbe}">${z.text}</span>`)}
      ${kennzahl('Modus', HZ_MODUS[h.mode] || '--')}
      ${kennzahl('Leistung', h.powerLevel != null ? h.powerLevel + '<small>%</small>' : '--')}
      ${kennzahl('Vorlauf', h.flowTemp != null ? _hzT(h.flowTemp) + '<small>°C</small>' : '--')}
      ${kennzahl('Räume mit Bedarf', h.demandingRooms ?? '--')}
      ${kennzahl('Laufzeit heute', _hzDauer(h.runtimeTodayS))}
      ${kennzahl('Starts heute', h.startsToday ?? '--')}
      ${kennzahl('Zustand seit', _hzDauer(h.stateForS))}
    </div>
    ${h.errorCode ? `<div class="hz-hinweis hz-fehler">Fehlercode ${h.errorCode}</div>` : ''}
    ${h.reason ? `<div class="vl-hinweis">Grund: ${_esc(h.reason)}${
      h.confirmed === false ? ' · vom Heizgerät noch nicht bestätigt' : ''}</div>` : ''}
    <div class="hz-presets" style="margin-top:12px">${modusKnoepfe}</div>
    ${handSchalter}
    ${h.pendingCommand?.remainingS > 0
      ? `<div class="hz-pending" style="margin-top:10px">Schaltbefehl wirkt in
          ${h.pendingCommand.remainingS} s
          <button class="hz-mini" onclick="hzAbbrechen()">Abbrechen</button></div>` : ''}
  </div>`;

  const raumKarten = (st.rooms || []).map(r => {
    const c = HZ_CONN[r.conn] || HZ_CONN.unknown;
    const gesperrt = _hzBusy.has('room' + r.id) ? 'disabled' : '';
    return `<div class="sb-card hz-raumkarte${r.enabled === false ? ' hz-raum-aus' : ''}">
      <div class="sb-hd">${_esc(r.name)}
        <span class="chip" style="color:${c.farbe}">${c.text}</span>
      </div>
      <div class="hz-soll">
        <button class="hz-rund" onclick="hzSoll(${r.id}, -0.5)" ${gesperrt}>−</button>
        <div class="hz-soll-mitte">
          <span class="hz-soll-wert">${_hzT(r.target)}<i>°C</i></span>
          <span class="hz-soll-ist">ist ${_hzT(r.roomTemp)} °C</span>
        </div>
        <button class="hz-rund" onclick="hzSoll(${r.id}, 0.5)" ${gesperrt}>+</button>
      </div>
      <div class="sb-stats" style="margin-top:12px">
        ${kennzahl('Gebläse', r.fanPercent != null ? r.fanPercent + '<small>%</small>' : '--')}
        ${kennzahl('Vorlauf', r.flowTemp != null ? _hzT(r.flowTemp) + '<small>°C</small>' : '--')}
        ${kennzahl('Zuletzt', r.lastSeenS != null ? r.lastSeenS + '<small>s</small>' : '--')}
        ${kennzahl('Signal', r.rssi != null ? r.rssi + '<small>dBm</small>' : '--')}
      </div>
      <div class="hz-presets" style="margin-top:12px">
        ${['off', 'auto', 'manual'].map(m => `<button class="hz-preset${
          r.fanMode === m ? ' an' : ''}" onclick="hzLuefter(${r.id},'${m}')" ${gesperrt}>${
          { off: 'Gebläse aus', auto: 'Automatik', manual: 'Hand' }[m]}</button>`).join('')}
        <button class="hz-preset${r.enabled ? ' an' : ''}"
          onclick="hzRaumAn(${r.id}, ${r.enabled ? 'false' : 'true'})" ${gesperrt}>${
          r.enabled ? 'heizt mit' : 'heizt nicht mit'}</button>
      </div>
      ${r.fault && r.fault !== 'none'
        ? `<div class="hz-hinweis hz-fehler">Störung: ${_esc(r.fault)}</div>` : ''}
    </div>`;
  }).join('');

  const info = d.info || {};
  const fuss = `<div class="sb-card">
    <div class="sb-hd">${icon('info', {size: 14})} Gerät</div>
    <div class="sb-stats">
      ${kennzahl('Name', _esc(info.deviceName || '--'))}
      ${kennzahl('Firmware', _esc(info.firmware || '--'))}
      ${kennzahl('Schnittstelle', info.apiVersion != null ? 'v' + info.apiVersion : '--')}
      ${kennzahl('Adresse', _esc(info.wifi?.ip || '--'))}
      ${kennzahl('Signal', info.wifi?.rssi != null ? info.wifi.rssi + '<small>dBm</small>' : '--')}
      ${kennzahl('Laufzeit', _hzDauer(info.uptimeS))}
    </div>
    ${st.time?.uncertain
      ? '<div class="vl-hinweis">Die Uhr des Geräts ist nicht gestellt — Zeitangaben im Verlauf sind wertlos.</div>'
      : ''}
    ${d.demo ? '<div class="hz-hinweis">Testdaten — es ist kein Gerät verbunden.</div>' : ''}
  </div>`;

  box.innerHTML = heizKarte
    + `<div class="hz-raeume-grid">${raumKarten}</div>`
    + fuss;
}

// ── Bedienen ────────────────────────────────────────────────────────────────

async function _hzSenden(schluessel, pfad, rumpf) {
  if (_hzBusy.has(schluessel)) return;
  _hzBusy.add(schluessel);
  _hzFehler = null;
  updateHeizungKachel();
  try {
    const r = await fetch(pfad, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rumpf || {}),
    });
    if (!r.ok) {
      let text = 'Befehl fehlgeschlagen';
      try {
        const j = await r.json();
        text = j?.detail?.message || j?.detail || text;
      } catch (_) { /* Antwort ohne JSON */ }
      _hzFehler = typeof text === 'string' ? text : 'Befehl fehlgeschlagen';
    }
  } catch (e) {
    _hzFehler = 'Die Heizung ist nicht erreichbar.';
  } finally {
    _hzBusy.delete(schluessel);
    await ladeHeizung(false);
  }
}

function hzPreset(i)          { _hzSenden('preset', `/api/heizung/preset/${i}`); }
function hzModus(m)           { _hzSenden('heater', '/api/heizung/heater', { mode: m }); }
function hzHand(befehl)       { _hzSenden('heater', '/api/heizung/heater', { mode: 'manual', command: befehl }); }
function hzAbbrechen()        { _hzSenden('heater', '/api/heizung/heater', { cancelPending: true }); }
function hzLuefter(id, modus) { _hzSenden('room' + id, `/api/heizung/room/${id}`, { fanMode: modus }); }
function hzRaumAn(id, an)     { _hzSenden('room' + id, `/api/heizung/room/${id}`, { enabled: an }); }

function hzSoll(id, delta) {
  const raum = (_hzDaten?.state?.rooms || []).find(r => r.id === id);
  if (!raum || raum.target == null) return;
  const ziel = Math.max(0, Math.min(40, Math.round((raum.target + delta) * 2) / 2));
  _hzSenden('room' + id, `/api/heizung/room/${id}`, { target: ziel });
}

// ── Einstellungen ───────────────────────────────────────────────────────────

async function hzEinstellungenLaden() {
  try {
    const r = await fetch('/api/heizung/settings');
    if (!r.ok) return;
    const c = await r.json();
    if ($('sHzHost'))    $('sHzHost').value = c.host || '';
    if ($('sHzEnabled')) $('sHzEnabled').checked = !!c.enabled;
    if ($('sHzTime'))    $('sHzTime').checked = !!c.set_time;
    if ($('sHzDemo'))    $('sHzDemo').checked = !!c.demo;
    if ($('sHzPass'))    $('sHzPass').placeholder = c.password_set
      ? 'gespeichert — leer lassen behält es' : 'nur bei eingeschaltetem Schreibschutz';
    _hzStatus(c.host ? '' : 'Noch keine Adresse hinterlegt.');
  } catch (e) {
    _hzStatus('Einstellungen konnten nicht geladen werden.');
  }
}

function _hzStatus(text, farbe) {
  const el = $('sHzStatus');
  if (!el) return;
  el.textContent = text || '';
  el.style.color = farbe || 'var(--text3)';
}

async function hzEinstellungenSpeichern() {
  const patch = {
    host:     $('sHzHost')?.value.trim() ?? '',
    enabled:  !!$('sHzEnabled')?.checked,
    set_time: !!$('sHzTime')?.checked,
    demo:     !!$('sHzDemo')?.checked,
  };
  // Ein leeres Feld soll ein gespeichertes Passwort NICHT loeschen — sonst
  // waere die Anbindung nach jedem Speichern der uebrigen Werte kaputt.
  const pw = $('sHzPass')?.value ?? '';
  if (pw) patch.password = pw;

  _hzStatus('Speichern …');
  try {
    const r = await fetch('/api/heizung/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if ($('sHzPass')) $('sHzPass').value = '';
    _hzStatus('Gespeichert.', 'var(--green)');
    await hzEinstellungenLaden();
    ladeHeizung(false);
  } catch (e) {
    _hzStatus('Speichern fehlgeschlagen.', 'var(--red)');
  }
}

async function hzPruefen() {
  const host = $('sHzHost')?.value.trim();
  if (!host) { _hzStatus('Erst eine Adresse eintragen.', 'var(--yellow)'); return; }
  _hzStatus('Prüfe …');
  try {
    const r = await fetch('/api/heizung/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host }),
    });
    const j = await r.json();
    if (!r.ok) {
      _hzStatus(j?.detail?.message || 'Keine Antwort von dieser Adresse.', 'var(--red)');
      return;
    }
    _hzStatus(`${j.deviceName || 'Stoker'} · Firmware ${j.firmware} · Schnittstelle v${j.apiVersion}`,
      'var(--green)');
  } catch (e) {
    _hzStatus('Keine Antwort von dieser Adresse.', 'var(--red)');
  }
}

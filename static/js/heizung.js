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

/**
 * Erwarteter Zustand nach einem Schaltbefehl.
 *
 * Zwischen Tippen und Rueckmeldung liegen mehrere Sekunden: der Befehl geht an
 * den Hub, der Hub an das Geraet, und erst der naechste Abruf bringt den neuen
 * Zustand zurueck. Ohne Vorgriff wirkt die Bedienung tot, und man tippt ein
 * zweites Mal.
 *
 * Deshalb wird der geschaltete Zustand SOFORT angezeigt und nur so lange
 * gehalten, bis entweder die Heizung ihn bestaetigt oder die Frist ablaeuft.
 * Danach zaehlt wieder ausschliesslich, was das Geraet meldet — eine
 * Anzeige, die eine nicht angekommene Schaltung dauerhaft behauptet, waere
 * schlimmer als gar keine Rueckmeldung.
 */
const HZ_ERWARTET_MS = 3000;
let _hzErwartet = null;       // { art, wert, bis }
let _hzErwartetTimer = null;

function _hzErwarte(art, wert) {
  _hzErwartet = { art, wert, bis: Date.now() + HZ_ERWARTET_MS };
  clearTimeout(_hzErwartetTimer);
  // Nach Ablauf einmal neu zeichnen, damit der Vorgriff sichtbar zurueckfaellt.
  _hzErwartetTimer = setTimeout(() => {
    _hzErwartet = null;
    updateHeizungKachel();
    if (!$('heizungOverlay')?.classList.contains('hidden')) renderHeizungDetail();
  }, HZ_ERWARTET_MS + 50);
}

/** Gilt der Vorgriff noch? */
function _hzErwartetWert(art) {
  if (!_hzErwartet || _hzErwartet.art !== art) return undefined;
  if (Date.now() > _hzErwartet.bis) { _hzErwartet = null; return undefined; }
  return _hzErwartet.wert;
}

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
    // Bestaetigt die Heizung den erwarteten Zustand, endet der Vorgriff sofort
    // — dann zeigt die Kachel wieder echte Werte statt einer Annahme.
    if (_hzErwartet) {
      const st = _hzDaten?.state;
      const ist = _hzErwartet.art === 'preset' ? (st?.preset?.index ?? 'none')
                : _hzErwartet.art === 'mode'   ? st?.heater?.mode
                : undefined;
      if (ist !== undefined && ist === _hzErwartet.wert) {
        _hzErwartet = null;
        clearTimeout(_hzErwartetTimer);
      }
    }
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
    body.innerHTML = `<div class="hz-leer">
      ${d && !d.configured
        ? 'Keine Heizung eingerichtet — unter Einstellungen › Heizung eintragen.'
        : 'Heizung derzeit nicht erreichbar.'}</div>`;
    return;
  }

  // Nicht erreichbar heisst NICHT, dass keine Daten da sind: der Server haelt
  // den letzten Zustand vor. Ohne Hinweis sah die Kachel deshalb normal aus,
  // obwohl das Geraet seit Minuten weg war. Jetzt sagt sie es — und markiert
  // die Werte als alt, statt sie als aktuell auszugeben.
  const weg = d && d.enabled && d.configured && d.reachable === false;

  const h = st.heater || {};
  const z = HZ_ZUSTAND[h.state] || { text: h.state || '--', farbe: 'var(--text2)' };
  const erwPreset = _hzErwartetWert('preset');
  const presetIdx = erwPreset !== undefined
    ? (erwPreset === 'none' ? null : erwPreset)
    : st.preset?.index;

  const presets = HZ_PRESETS.map((name, i) => `
    <button class="hz-preset${presetIdx === i ? ' an' : ''}"
      onclick="event.stopPropagation();hzPreset(${i})"
      ${_hzBusy.has('preset') ? 'disabled' : ''}>${name}</button>`).join('')
    + `<button class="hz-preset${presetIdx == null ? ' an' : ''}"
        onclick="event.stopPropagation();hzPreset('none')"
        ${_hzBusy.has('preset') ? 'disabled' : ''} title="Kein Preset">Aus</button>`;

  // Die Kachel zeigt nur die ersten Raeume: bei zehn Stueck lief die Liste
  // sonst unten aus der Kachel heraus. Vollstaendig steht sie auf der
  // Detailseite, die ein Tipp auf die Kachel oeffnet.
  const alleRaeume = st.rooms || [];
  const raeume = alleRaeume.map(r => {
    const aus = r.enabled === false;
    const kalt = r.conn !== 'online';
    return `<div class="hz-raum${aus ? ' hz-raum-aus' : ''}">
      <span class="hz-raum-name">${_esc(r.name)}</span>
      <span class="hz-raum-ist">${_hzT(r.roomTemp)}<i>°</i></span>
      <span class="hz-raum-pfeil">→</span>
      <span class="hz-raum-soll">${_hzT(_hzSollAnzeige(r))}<i>°</i></span>
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
    ${weg ? `<div class="hz-hinweis hz-weg" title="Angezeigt wird der zuletzt bekannte Stand.">Keine Verbindung${
      d.age_s != null ? ` seit ${_hzDauer(d.age_s)}` : ''}</div>` : ''}
    <div class="hz-kopf${weg ? ' hz-alt' : ''}">
      <span class="hz-zustand" style="color:${z.farbe}">${z.text}</span>
      ${h.powerLevel != null && h.state === 'running'
        ? `<span class="hz-leistung">${h.powerLevel} %</span>` : ''}
      <span class="hz-vorlauf">${h.flowTemp != null
        ? `Vorlauf ${_hzT(h.flowTemp)} °C` : HZ_MODUS[h.mode] || ''}</span>
    </div>
    ${warten}
    <div class="hz-presets">${presets}</div>
    <div class="hz-raeume${weg ? ' hz-alt' : ''}">${raeume}</div>
    <div class="hz-mehr" hidden></div>
    <div class="hz-sub${weg ? ' hz-alt' : ''}">
      <div class="hz-sub-item">Starts heute <b>${h.startsToday ?? '--'}</b></div>
      <div class="hz-sub-item">Vorlauf <b>${_hzT(h.flowTemp)}</b> °C</div>
      <div class="hz-sub-item">Betriebsart <b>${_esc(_hzGeraetemodus(h))}</b></div>
    </div>
    ${_hzFehler ? `<div class="hz-hinweis hz-fehler">${_esc(_hzFehler)}</div>` : ''}`;

  _hzKachelKuerzen();
}

/**
 * Raumliste auf das kuerzen, was wirklich in die Kachel passt.
 *
 * Eine feste Zahl reicht nicht: die Kachelhoehe haengt von der Spaltenbreite
 * ab (bei drei Spalten ist sie niedriger als bei zweien), und der
 * Verbindungshinweis kostet zusaetzlich Platz. Bei zehn Raeumen lief die Liste
 * deshalb je nach Fensterbreite unten heraus. Also nach dem Zeichnen messen
 * und ausblenden, was nicht mehr hineinpasst — die Zeile darunter sagt, wie
 * viele fehlen. Vollstaendig steht die Liste auf der Detailseite.
 */
function _hzKachelKuerzen() {
  const karte = $('heizungCard');
  const mehr  = karte?.querySelector('.hz-mehr');
  if (!karte || !mehr) return;
  const zeilen = [...karte.querySelectorAll('.hz-raum')];
  if (!zeilen.length) { mehr.hidden = true; return; }

  // Einspaltig ist die Kachel inhaltsgross — dort passt alles, es gibt nichts
  // zu kuerzen. Ohne diese Ausnahme haette die Messung gegen eine Kachelhoehe
  // gerechnet, die sich erst aus dem Inhalt ergibt.
  if (!document.body.classList.contains('raster-mehrspaltig')) {
    zeilen.forEach(z => { z.hidden = false; });
    mehr.hidden = true;
    return;
  }

  zeilen.forEach(z => { z.hidden = false; });
  mehr.hidden = false;
  mehr.textContent = '';               // erst messen, dann beschriften

  // Untergrenze vom KACHELRAND aus rechnen, nicht von der Fusszeile:
  // .hz-sub hat margin-top:auto und wird bei ueberlaufendem Inhalt selbst
  // unter den Rand geschoben — ihre Position waere dann als Bezug wertlos.
  // Ihre HOEHE ist dagegen zuverlaessig und wird als Reserve abgezogen.
  const fuss = karte.querySelector('.hz-sub');
  const unten = karte.getBoundingClientRect().bottom
              - parseFloat(getComputedStyle(karte).paddingBottom || 0)
              - (fuss ? fuss.offsetHeight + 10 : 0);
  let sichtbar = zeilen.length;
  for (let i = 0; i < zeilen.length; i++) {
    // Platz fuer die Hinweiszeile freihalten, sobald ueberhaupt gekuerzt wird.
    const reserve = (i < zeilen.length - 1) ? mehr.offsetHeight + 22 : 0;
    if (zeilen[i].getBoundingClientRect().bottom > unten - reserve) { sichtbar = i; break; }
  }
  // Passen weniger als drei Zeilen, ist eine Stummelliste ("1 Raum,
  // + 9 weitere") nutzlos. Dann lieber eine Zusammenfassung: sie sagt in einer
  // Zeile mehr als zwei abgeschnittene Raumnamen. Die Kachelhoehe haengt an
  // der Spaltenzahl, dieser Fall tritt also je nach Fensterbreite auf.
  const MINDEST_ZEILEN = 3;
  if (sichtbar < MINDEST_ZEILEN && zeilen.length > sichtbar) {
    zeilen.forEach(z => { z.hidden = true; });
    mehr.hidden = false;
    mehr.textContent = _hzZusammenfassung(zeilen.length);
  } else {
    sichtbar = Math.max(1, sichtbar);
    zeilen.forEach((z, i) => { z.hidden = i >= sichtbar; });
    const fehlend = zeilen.length - sichtbar;
    mehr.hidden = fehlend <= 0;
    if (fehlend > 0) mehr.textContent = `+ ${fehlend} weitere${fehlend === 1 ? 'r Raum' : ' Räume'}`;
  }

  // Letzte Sicherung, die IMMER laeuft: passt die Fusszeile trotzdem nicht
  // (sehr flache Kachel mit Verbindungshinweis), faellt auch die Zaehlzeile
  // weg. Vorlauf, Starts und Betriebsart wiegen schwerer als "+ n weitere".
  if (fuss && fuss.getBoundingClientRect().bottom
              > karte.getBoundingClientRect().bottom) {
    zeilen.forEach(z => { z.hidden = true; });
    mehr.hidden = true;
  }
}

/**
 * Betriebsart, wie sie das HEIZGERAET selbst zurueckmeldet.
 *
 * Der Hub liefert sie derzeit noch nicht: heater.mode ist die Vorgabe DES HUBS
 * ("auto"/"manual"), heater.state sein daraus abgeleiteter Zustand, und
 * heater.link beschreibt nur die mitgeschnittene Verbindung zum Geraet.
 * Das Feld ist hier schon angelegt und liest der Reihe nach mehrere plausible
 * Schluessel — sobald die API einen davon fuellt, erscheint der Wert ohne
 * weitere Aenderung. Bis dahin steht "--" statt einer erfundenen Angabe.
 */
function _hzGeraetemodus(h) {
  const v = h?.deviceMode ?? h?.heaterMode ?? h?.device?.mode ?? h?.link?.deviceMode;
  return (v == null || v === '') ? '--' : String(v);
}

/** Einzeiler statt Liste, wenn die Kachel zu flach ist. */
function _hzZusammenfassung(anzahl) {
  const r = _hzDaten?.state?.rooms || [];
  const waerme = r.filter(x => x.wantsHeat).length;
  const aus    = r.filter(x => x.enabled === false).length;
  const teile  = [`${anzahl} Räume`];
  if (waerme) teile.push(`${waerme} fordern Wärme`);
  if (aus)    teile.push(`${aus} aus`);
  return teile.join(' · ');
}

// Die Kachelhoehe aendert sich mit der Spaltenzahl — nach jedem
// Groessenwechsel neu abmessen.
let _hzKuerzTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(_hzKuerzTimer);
  _hzKuerzTimer = setTimeout(_hzKachelKuerzen, 150);
});

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
  // Finger am Staerkeregler: nicht neu aufbauen, sonst reisst das Ziehen ab.
  if (_hzReglerRaum !== null) return;
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
        <button class="hz-rund" onclick="hzSoll(${r.id}, -0.5)">−</button>
        <div class="hz-soll-mitte">
          <span class="hz-soll-wert">${_hzT(_hzSollAnzeige(r))}<i>°C</i></span>
          <span class="hz-soll-ist">ist ${_hzT(r.roomTemp)} °C</span>
        </div>
        <button class="hz-rund" onclick="hzSoll(${r.id}, 0.5)">+</button>
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
      ${r.fanMode === 'manual' ? (() => {
        // Sollwert der Handstufe. Nicht fanPercent nehmen: das ist der Istwert,
        // der beim Anlaufimpuls und beim Auslaufen daneben liegt (Firmware
        // room_control.cpp) — der Regler zeigt, was gestellt IST.
        const tempo = _hzErwartetWert('tempo' + r.id) ?? (r.manualSpeed ?? 50);
        return `<div class="hz-tempo">
          <label class="hz-tempo-kopf" for="hzTempo${r.id}">Gebläsestärke
            <span><b id="hzTempoWert${r.id}">${tempo}</b> %</span></label>
          <input type="range" class="hz-tempo-regler" id="hzTempo${r.id}"
            min="0" max="100" step="5" value="${tempo}" ${gesperrt}
            oninput="hzTempoZeigen(${r.id}, this.value)"
            onchange="hzTempo(${r.id}, this.value)"
            onpointerdown="_hzReglerHalten(${r.id})">
        </div>`;
      })() : ''}
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
    // Bei einem Fehler gilt der Vorgriff nicht weiter — sonst behauptet die
    // Anzeige drei Sekunden lang etwas, das nachweislich nicht passiert ist.
    if (_hzFehler) { _hzErwartet = null; clearTimeout(_hzErwartetTimer); }
    await ladeHeizung(false);
  }
}

function hzPreset(i)          { _hzErwarte('preset', i);
                               _hzSenden('preset', `/api/heizung/preset/${i}`); }
function hzModus(m)           { _hzErwarte('mode', m);
                               _hzSenden('heater', '/api/heizung/heater', { mode: m }); }
function hzHand(befehl)       { _hzSenden('heater', '/api/heizung/heater', { mode: 'manual', command: befehl }); }
function hzAbbrechen()        { _hzSenden('heater', '/api/heizung/heater', { cancelPending: true }); }
function hzLuefter(id, modus) { _hzSenden('room' + id, `/api/heizung/room/${id}`, { fanMode: modus }); }

// ── Geblaesestaerke im Handbetrieb ──────────────────────────────────────────
// Der Regler steckt in einem Block, den die Detailseite alle 3 s per innerHTML
// neu aufbaut (hzDetailPoller). Waehrend des Ziehens wuerde er dabei aus dem
// DOM fliegen und der Finger den Faden verlieren. Solange angefasst wird,
// bleibt das Neuzeichnen deshalb aus.
let _hzReglerRaum  = null;   // id des Raums, dessen Regler gerade angefasst wird
let _hzReglerTimer = null;

function _hzReglerHalten(id) {
  _hzReglerRaum = id;
  clearTimeout(_hzReglerTimer);
  // Notbremse: bleibt 'change' aus (Finger verlaesst den Bildschirm, Zeiger
  // geht verloren), darf die Anzeige nicht dauerhaft einfrieren.
  _hzReglerTimer = setTimeout(() => { _hzReglerRaum = null; }, 8000);
}

function _hzReglerLoslassen() {
  clearTimeout(_hzReglerTimer);
  _hzReglerRaum = null;
}

/** Waehrend des Ziehens nur die Zahl daneben mitfuehren — nichts senden. */
function hzTempoZeigen(id, wert) {
  _hzReglerHalten(id);
  const feld = $('hzTempoWert' + id);
  if (feld) feld.textContent = wert;
}

/** Losgelassen: einmal senden. 'change' feuert beim Loslassen, nicht waehrend
 *  des Ziehens — damit geht genau EIN Befehl an den Hub statt hundert. */
function hzTempo(id, wert) {
  _hzReglerLoslassen();
  const v = Math.max(0, Math.min(100, Math.round(+wert)));
  // Vorgriff: der Hub antwortet traege, und der naechste Abruf brachte sonst
  // kurz den alten Wert zurueck — der Regler waere zurueckgesprungen.
  _hzErwarte('tempo' + id, v);
  _hzSenden('room' + id, `/api/heizung/room/${id}`, { manualSpeed: v });
}
function hzRaumAn(id, an)     { _hzSenden('room' + id, `/api/heizung/room/${id}`, { enabled: an }); }

// ── Solltemperatur: Vorgriff und Sammeln ────────────────────────────────────
// Der Hub antwortet traege. Vorher hatte das zwei Folgen, die schlimmer waren
// als die reine Verzoegerung:
//   1. hzSoll rechnete jedes Mal vom zuletzt GEMELDETEN Wert. Der zweite
//      schnelle Druck rechnete also wieder vom alten Stand — der Schritt ging
//      verloren.
//   2. _hzSenden bricht ab, solange schon ein Befehl laeuft. Jeder Druck
//      waehrend eines laufenden Befehls verschwand ersatzlos.
// Jetzt zaehlt die Oberflaeche selbst mit, zeigt das sofort an und schickt
// erst, wenn das Tippen aufhoert.
const HZ_SOLL_SAMMEL_MS = 600;    // so lange wird auf weitere Druecke gewartet
const HZ_SOLL_HALT_MS   = 8000;   // so lange gilt der Vorgriff hoechstens
const _hzSollWunsch = new Map();  // raum-id -> { wert, bis }
const _hzSollTimer  = new Map();  // raum-id -> Timeout

/** Solltemperatur fuer die Anzeige: der Wunsch des Bedieners schlaegt den
 *  gemeldeten Wert, solange er frisch und unbestaetigt ist. */
function _hzSollAnzeige(r) {
  const w = _hzSollWunsch.get(r.id);
  if (!w) return r.target;
  if (Date.now() > w.bis) { _hzSollWunsch.delete(r.id); return r.target; }
  // Die Heizung meldet den gewuenschten Wert — Vorgriff hat sich erledigt.
  if (r.target != null && Math.abs(r.target - w.wert) < 0.05) {
    _hzSollWunsch.delete(r.id);
    return r.target;
  }
  return w.wert;
}

function _hzNeuZeichnen() {
  updateHeizungKachel();
  if (!$('heizungOverlay')?.classList.contains('hidden')) renderHeizungDetail();
}

function hzSoll(id, delta) {
  const raum = (_hzDaten?.state?.rooms || []).find(r => r.id === id);
  if (!raum) return;
  // Basis ist der zuletzt gewuenschte Wert, nicht der gemeldete.
  const w = _hzSollWunsch.get(id);
  const basis = (w && Date.now() <= w.bis) ? w.wert : raum.target;
  if (basis == null) return;
  const ziel = Math.max(0, Math.min(40, Math.round((basis + delta) * 2) / 2));
  if (ziel === basis) return;   // an der Grenze: nichts tut sich, nichts senden
  _hzSollWunsch.set(id, { wert: ziel, bis: Date.now() + HZ_SOLL_HALT_MS });
  _hzNeuZeichnen();             // sofort sichtbar, ohne auf die Heizung zu warten
  // Fuenf schnelle Druecke werden EIN Befehl an den Hub, nicht fuenf.
  clearTimeout(_hzSollTimer.get(id));
  const senden = () => {
    // Laeuft noch ein Befehl fuer diesen Raum, wuerde _hzSenden stillschweigend
    // abbrechen und der Wunsch waere weg. Also lieber gleich nochmal schauen.
    if (_hzBusy.has('room' + id)) {
      _hzSollTimer.set(id, setTimeout(senden, 200));
      return;
    }
    _hzSollTimer.delete(id);
    const akt = _hzSollWunsch.get(id);
    if (akt) _hzSenden('room' + id, `/api/heizung/room/${id}`, { target: akt.wert });
  };
  _hzSollTimer.set(id, setTimeout(senden, HZ_SOLL_SAMMEL_MS));
}

// ── Einstellungen ───────────────────────────────────────────────────────────

// ── Frostwacht je Raum ──────────────────────────────────────────────────────
// Eigener Schalter im Bord-Monitor, ausdruecklich unabhaengig von allem, was
// an der Heizung eingestellt ist. Ein Raum, der nicht in der Konfiguration
// steht, gilt als bewacht (Vorgabe im Backend) — deshalb wird die Karte beim
// Zeichnen mit ausdruecklichen Werten fuer alle sichtbaren Raeume gefuellt.
let _hzFrostEinst = {};

function hzFrostwachtRendern() {
  const box = $('sHzFrostListe');
  if (!box) return;
  const raeume = _hzDaten?.state?.rooms || [];
  if (!raeume.length) {
    box.innerHTML = `<div style="font-size:13px;color:var(--text3)">Räume erscheinen hier,
      sobald die Heizung erreichbar ist und Fühler angelernt sind.</div>`;
    return;
  }
  box.innerHTML = raeume.map(r => {
    const an = _hzFrostEinst[String(r.id)] !== false;
    const stumm = r.conn !== 'online';
    return `<div class="settings-row" style="align-items:center;gap:12px">
      <label class="settings-label" for="sHzFrost${r.id}" style="flex:1;min-width:0">${_esc(r.name)}</label>
      <input type="checkbox" id="sHzFrost${r.id}" ${an ? 'checked' : ''}
        style="width:18px;height:18px;accent-color:var(--accent)"
        onchange="hzFrostwacht(${r.id}, this.checked)" />
      <span style="font-size:13px;color:var(--text2);min-width:120px">${
        stumm ? 'meldet gerade nicht' : (an ? 'wird bewacht' : 'darf kalt werden')}</span>
    </div>`;
  }).join('');
}

async function hzFrostwacht(id, an) {
  // Immer die VOLLSTAENDIGE Karte schicken: das Backend ersetzt sie, ein
  // Teilstueck wuerde die uebrigen Raeume auf die Vorgabe zuruecksetzen.
  const vorher = { ..._hzFrostEinst };
  (_hzDaten?.state?.rooms || []).forEach(r => {
    if (_hzFrostEinst[String(r.id)] === undefined) _hzFrostEinst[String(r.id)] = true;
  });
  _hzFrostEinst[String(id)] = !!an;
  hzFrostwachtRendern();
  const melde = (t, farbe) => { const e = $('sHzFrostStatus'); if (e) { e.textContent = t; e.style.color = farbe || 'var(--text3)'; } };
  melde('Speichern …');
  try {
    const r = await fetch('/api/heizung/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frostwacht: _hzFrostEinst }),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    melde('Gespeichert.', 'var(--green)');
  } catch (e) {
    _hzFrostEinst = vorher;          // nicht behaupten, was nicht gespeichert ist
    hzFrostwachtRendern();
    melde('Speichern fehlgeschlagen.', 'var(--red)');
  }
}

async function hzEinstellungenLaden() {
  try {
    const r = await fetch('/api/heizung/settings');
    if (!r.ok) return;
    const c = await r.json();
    _hzFrostEinst = { ...(c.frostwacht || {}) };
    // Frische Raumliste holen, sonst steht die Karte leer da, wenn die
    // Einstellungen vor dem ersten Abruf geoeffnet werden.
    await ladeHeizung(false);
    hzFrostwachtRendern();
    if ($('sHzHost'))    $('sHzHost').value = c.host || '';
    if ($('sHzEnabled')) $('sHzEnabled').checked = !!c.enabled;
    if ($('sHzTime'))    $('sHzTime').checked = !!c.set_time;
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

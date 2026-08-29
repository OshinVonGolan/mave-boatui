// ── Geräteübersicht ────────────────────────────────────────────────────────
// Eine Seite, die drei Fragen beantwortet: Was hängt an Bord? Lebt es? Wie
// komme ich ran? Die Daten kommen fertig aus /api/devices — hier wird nichts
// zusammengerechnet, nur angezeigt.
//
// Alter IMMER aus den Serverfeldern (age_s), nie aus Date.now(): der Pi hat
// keine Echtzeituhr, seine Uhr steht nach einem Stromausfall falsch.

const GER_KAT_ICON = {
  netzwerk: 'wifi',   energie: 'bolt',      heizung: 'flame',   n2k: 'chip',
  navigation: 'compass', wasser: 'droplet', licht: 'bulb',      motor: 'propeller',
  sicherheit: 'shield',  sonstiges: 'box',
};
const GER_KAT_FARBE = {
  netzwerk: '#38bdf8', energie: '#fbbf24',  heizung: '#fb923c', n2k: '#06b6d4',
  navigation: '#a78bfa', wasser: '#22d3ee', licht: '#facc15',   motor: '#94a3b8',
  sicherheit: '#f87171', sonstiges: '#64748b',
};
// Fachseite je Gerät. Fehlt der Eintrag, wird der Knopf nicht angeboten —
// lieber kein Sprung als ein Sprung ins Leere.
const GER_SPRUNG = {
  heizung:      { fn: 'openHeizung',      text: 'Zur Heizung' },
  connectivity: { fn: 'openConnectivity', text: 'Zur Verbindung' },
  netzwerk:     { fn: 'openNetwork',      text: 'Zur Bus-Ansicht' },
  battery:      { fn: 'openBattDetail',   text: 'Zur Batterie' },
  lights:       { fn: 'openLightDetail',  text: 'Zum Licht' },
};
const GER_STATUS_REIHE = ['offline', 'unbekannt', 'traege', 'online', 'fremdnetz', 'stumm'];

// Alle Kategorien, auch die gerade leeren — die Zusammenfassung vom Server
// enthält nur benutzte. Reihenfolge und Namen wie im Backend.
const GER_KAT_ALLE = [
  ['netzwerk', 'Netzwerk'], ['energie', 'Energie'], ['heizung', 'Heizung & Lüftung'],
  ['n2k', 'NMEA-2000-Bus'], ['navigation', 'Navigation'], ['wasser', 'Wasser'],
  ['licht', 'Licht'], ['motor', 'Motor & Antrieb'], ['sicherheit', 'Sicherheit'],
  ['sonstiges', 'Sonstiges'],
];

let _gerDaten     = null;     // letzter Snapshot von /api/devices
let _gerGruppe    = null;     // gewählte Kachel (Kategorie- oder Netz-Schlüssel)
let _gerModus     = 'kategorie';
let _gerSuche     = '';
let _gerProbleme  = false;
let _gerDetailId  = null;     // offenes Popup
let _gerBearbeiten = false;
let _gerFehler    = '';
let _gerPoller    = null;

// ── Öffnen und Schließen ───────────────────────────────────────────────────

function openGeraete() {
  _closeAllOverlays();
  history.pushState({ overlay: 'geraete' }, '', '#geraete');
  _gerOverlayAnzeigen();
}

// Getrennt vom Öffnen, weil die Zurück-Geste (popstate in lightdetail.js) die
// Ansicht ohne neuen History-Eintrag wiederherstellen muss.
function _gerOverlayAnzeigen() {
  _gerBauen();
  $('geraeteOverlay').classList.remove('hidden');
  _navActive('geraeteBtn');
  if (!_gerPoller) {
    // Über createPoller, damit die Abfrage ruht, sobald die App im Hintergrund
    // liegt oder das Overlay zu ist — der Pi Zero soll nicht für eine
    // unsichtbare Seite arbeiten.
    _gerPoller = createPoller(gerLaden, 10000, {
      shouldRun: () => !$('geraeteOverlay')?.classList.contains('hidden'),
    });
  }
  _gerPoller.start();
}

function closeGeraete() {
  _gerPoller?.stop();
  gerDetailZu();
  $('geraeteOverlay')?.classList.add('hidden');
  _navActive(null);
  history.replaceState(null, '', location.pathname);
}

function _gerBauen() {
  if ($('geraeteOverlay')) return;
  const el = document.createElement('div');
  el.id = 'geraeteOverlay';
  el.className = 'overlay hidden';
  el.innerHTML = `
    <div class="ov-header">
      <button class="ov-close-arrow" onclick="closeGeraete()">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2.5" stroke-linecap="round"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
      </button>
      <div class="ov-title">${icon('chip', { size: 14 })} Geräte an Bord</div>
      <button class="monday-refresh" onclick="gerLaden()" style="margin-left:auto">
        ${icon('refresh', { size: 13 })} Aktualisieren
      </button>
    </div>
    <div class="ov-body" style="padding:0">
      <div class="ger-wrap" id="gerInhalt">
        <div class="ger-leer">Lade Geräte…</div>
      </div>
    </div>`;
  document.body.appendChild(el);
}

// ── Daten holen ────────────────────────────────────────────────────────────

async function gerLaden() {
  try {
    const r = await fetch('/api/devices');
    if (!r.ok) throw new Error(r.status);
    _gerDaten = await r.json();
    _gerRender();
  } catch (e) {
    if (!_gerDaten) {
      const el = $('gerInhalt');
      if (el) el.innerHTML = '<div class="ger-leer">Keine Verbindung zum Boot-Monitor.</div>';
    }
  }
}

// ── Hilfen ─────────────────────────────────────────────────────────────────

function _gerGruppeVon(g) { return _gerModus === 'netz' ? (g.netz || 'keins') : g.kategorie; }

function _gerGruppenName(key) {
  if (_gerModus === 'netz') {
    return (_gerDaten?.netze || []).find(n => n.key === key)?.name || key;
  }
  return (_gerDaten?.kategorien || []).find(k => k.key === key)?.name || key;
}

function _gerGefiltert() {
  const q = _gerSuche.trim().toLowerCase();
  return (_gerDaten?.geraete || []).filter(g => {
    if (_gerProbleme && g.status !== 'offline' && g.status !== 'unbekannt') return false;
    if (!q) return true;
    return [g.name, g.hersteller, g.modell, g.notiz, g.id, g.netz_name, ortName(g.ort),
            ...(g.kennzahlen || []).map(k => k.v)]
      .some(t => (t || '').toLowerCase().includes(q));
  });
}

function _gerSortiert(liste) {
  return [...liste].sort((a, b) => {
    const d = GER_STATUS_REIHE.indexOf(a.status) - GER_STATUS_REIHE.indexOf(b.status);
    return d !== 0 ? d : a.name.localeCompare(b.name, 'de');
  });
}

/** Eltern-Kind-Ordnung innerhalb einer Liste: Kinder direkt unter ihr Gerät. */
function _gerVerschachtelt(liste) {
  const ids = new Set(liste.map(g => g.id));
  const wurzeln = _gerSortiert(liste.filter(g => !g.verbunden_an || !ids.has(g.verbunden_an)));
  const kinder = {};
  liste.forEach(g => {
    if (g.verbunden_an && ids.has(g.verbunden_an)) {
      (kinder[g.verbunden_an] ||= []).push(g);
    }
  });
  const raus = [];
  const anhaengen = (g, tiefe) => {
    raus.push({ g, tiefe });
    _gerSortiert(kinder[g.id] || []).forEach(k => anhaengen(k, tiefe + 1));
  };
  wurzeln.forEach(g => anhaengen(g, 0));
  return raus;
}

function _gerGeraet(id) { return (_gerDaten?.geraete || []).find(g => g.id === id) || null; }

// ── Rendern ────────────────────────────────────────────────────────────────

function _gerRender() {
  const el = $('gerInhalt');
  if (!el || !_gerDaten) return;
  // Das Suchfeld wird beim Neuzeichnen neu gebaut. Ohne diese beiden Zeilen
  // verliert es alle zehn Sekunden — mitten im Tippen — Fokus und Schreibmarke.
  const suchfeld = $('gerSuche');
  const marke = (document.activeElement === suchfeld) ? suchfeld.selectionStart : null;
  const alle = _gerDaten.geraete || [];
  const zaehl = s => alle.filter(g => g.status === s).length;
  const ohneMelder = alle.filter(g => g.status === 'stumm' || g.status === 'fremdnetz').length;

  const q = _gerDaten.quellen || {};
  const quelle = (name, an, text) => `
    <div class="ger-quelle"><div class="ger-dot ${an ? 's-online' : 's-unbekannt'}"></div>
      ${_esc(name)} <span style="color:var(--text3)">${_esc(text)}</span></div>`;

  // Suche und Problemfilter arbeiten immer über alle Geräte — wer sucht, will
  // nicht erst die richtige Kachel finden.
  const flach = _gerSuche.trim() !== '' || _gerProbleme;
  const gefiltert = _gerGefiltert();

  el.innerHTML = `
    <div class="ger-summary">
      <div class="ger-stat" style="--stat-farbe:var(--accent)"><b>${alle.length}</b><span>Geräte erfasst</span></div>
      <div class="ger-stat" style="--stat-farbe:var(--green)"><b>${zaehl('online')}</b><span>online</span></div>
      <div class="ger-stat" style="--stat-farbe:var(--red)"><b>${zaehl('offline') + zaehl('unbekannt')}</b><span>offline oder unklar</span></div>
      <div class="ger-stat" style="--stat-farbe:#475569"><b>${ohneMelder}</b><span>ohne eigene Meldung</span></div>
    </div>
    <div class="ger-quellen">
      ${quelle('Bordbus', q.n2k?.verfuegbar, `${q.n2k?.geraete ?? 0} Geräte`)}
      ${quelle('WLAN', q.lan?.verfuegbar, q.lan?.verfuegbar ? `${q.lan.clients} Clients` : 'Router antwortet nicht')}
      ${quelle('Heizung', q.stoker?.verfuegbar && q.stoker?.hub === 'online', q.stoker?.verfuegbar ? `Hub ${q.stoker.hub}` : 'nicht eingerichtet')}
    </div>

    <div class="ger-toolbar">
      <div class="ger-suche">
        ${icon('search', { size: 15 })}
        <input id="gerSuche" type="search" placeholder="Gerät, Hersteller, IP…"
               value="${_esc(_gerSuche)}" oninput="gerSuchen(this.value)">
      </div>
      <div class="ger-seg">
        <button class="${_gerModus === 'kategorie' ? 'active' : ''}" onclick="gerModus('kategorie')">Kategorie</button>
        <button class="${_gerModus === 'netz' ? 'active' : ''}" onclick="gerModus('netz')">Netz</button>
      </div>
      <button class="ger-toggle ${_gerProbleme ? 'active' : ''}" onclick="gerProbleme()">
        ${icon('warning', { size: 14 })} nur Probleme
      </button>
    </div>

    ${flach ? _gerListeHtml(gefiltert, _gerProbleme ? 'Geräte mit Problem' : 'Treffer', true)
            : _gerGruppe === '*' ? _gerListeHtml(gefiltert, 'Alle Geräte', false)
            : _gerGruppe ? _gerListeHtml(gefiltert.filter(g => _gerGruppeVon(g) === _gerGruppe),
                                         _gerGruppenName(_gerGruppe), false)
            : _gerKachelnHtml(gefiltert)}
  `;

  // Ein laufender Poll darf ein offenes Bearbeiten-Formular NICHT neu zeichnen —
  // sonst waeren alle Eingaben nach zehn Sekunden weg.
  if (marke !== null) {
    const neuesFeld = $('gerSuche');
    neuesFeld?.focus();
    neuesFeld?.setSelectionRange(marke, marke);
  }

  if (_gerDetailId && !_gerBearbeiten) _gerModalRender();
}

function _gerKachelnHtml(liste) {
  const gruppen = {};
  liste.forEach(g => { (gruppen[_gerGruppeVon(g)] ||= []).push(g); });

  // Reihenfolge: im Kategoriemodus die vom Server, im Netzmodus die der
  // Netzliste — beides ist eine bewusste Sortierung, kein Zufall.
  const reihenfolge = _gerModus === 'netz'
    ? (_gerDaten.netze || []).map(n => n.key)
    : (_gerDaten.kategorien || []).map(k => k.key);

  const kacheln = reihenfolge.filter(k => gruppen[k]).map(key => {
    const teil = gruppen[key];
    const zaehl = s => teil.filter(g => g.status === s).length;
    const anteil = n => (n / teil.length * 100).toFixed(2) + '%';
    const kat = _gerModus === 'netz' ? null : key;
    const farbe = GER_KAT_FARBE[kat] || 'var(--accent)';
    const symbol = GER_KAT_ICON[kat] || 'chip';
    const online = zaehl('online');
    const problem = zaehl('offline') + zaehl('unbekannt');
    const neutral = zaehl('stumm') + zaehl('fremdnetz');
    const sub = problem ? `${problem} offline oder unklar`
              : neutral === teil.length ? 'ohne eigene Meldung'
              : `${online} von ${teil.length - neutral} online`;
    return `
      <button class="ger-tile" style="--kachel-farbe:${farbe}" onclick="gerGruppe(${_jsAttr(key)})">
        <div class="ger-tile-head">${icon(symbol, { size: 17 })} <span>${_esc(_gerGruppenName(key))}</span></div>
        <div class="ger-tile-zahl">${teil.length}<small>${teil.length === 1 ? 'Gerät' : 'Geräte'}</small></div>
        <div class="ger-tile-sub">${_esc(sub)}</div>
        <div class="ger-balken">
          <i style="width:${anteil(online)};background:var(--green)"></i>
          <i style="width:${anteil(zaehl('traege'))};background:var(--yellow)"></i>
          <i style="width:${anteil(problem)};background:var(--red)"></i>
          <i style="width:${anteil(neutral)};background:#475569"></i>
        </div>
      </button>`;
  }).join('');

  return `<div class="ger-grid">${kacheln}</div>
    <div style="text-align:center;margin-top:16px">
      <button class="ger-zurueck" onclick="gerGruppe('*')">
        Alle ${liste.length} Geräte in einer Liste
      </button>
    </div>`;
}

function _gerListeHtml(liste, titel, flach) {
  if (!liste.length) {
    return `${flach ? '' : _gerKopfHtml(titel, 0)}<div class="ger-leer">Nichts gefunden.</div>`;
  }
  const zeilen = _gerVerschachtelt(liste).map(({ g, tiefe }) => {
    const kennzahlen = (g.kennzahlen || []).slice(0, 2).map((k, i) =>
      `<div class="ger-kennzahl ${i ? 'ger-weg-mobil' : ''}">${_esc(k.l)} <b>${_esc(k.v)}</b></div>`).join('');
    const name_klein = (g.name || '').toLowerCase();
    const untertitel = [
      [g.hersteller, g.modell].filter(Boolean).join(' '),
      ortName(g.ort),
      flach ? _gerGruppenName(_gerGruppeVon(g)) : '',
    ]
      // "Raymarine ITC-5" unter "Raymarine ITC-5" sagt nichts — weg damit.
      .filter(t => t && !name_klein.includes(t.toLowerCase()))
      .join(' · ');
    return `
      <button class="ger-row ${tiefe ? 'ger-kind' : ''}"
              ${tiefe ? `style="margin-left:${tiefe * 22}px"` : ''}
              onclick="gerDetail(${_jsAttr(g.id)})">
        <div class="ger-dot s-${_esc(g.status)}"></div>
        <div class="ger-row-mitte">
          <div class="ger-row-name">${_esc(g.name)}${g.gepflegt ? '' :
            ' <span class="ger-tag">neu erkannt</span>'}</div>
          <div class="ger-row-sub">${_esc(untertitel || g.netz_name || '')}</div>
        </div>
        <div class="ger-row-rechts">
          ${kennzahlen}
          <div class="ger-pill s-${_esc(g.status)}">${_esc(g.status_text)}</div>
        </div>
      </button>`;
  }).join('');
  return `${flach ? '' : _gerKopfHtml(titel, liste.length)}<div class="ger-liste">${zeilen}</div>`;
}

function _gerKopfHtml(titel, anzahl) {
  return `
    <div class="ger-kopf">
      <button class="ger-zurueck" onclick="gerGruppe(null)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2.5" stroke-linecap="round"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
        Alle Gruppen
      </button>
      <h3>${_esc(titel)}</h3>
      <div class="ger-kopf-sub">${anzahl} ${anzahl === 1 ? 'Gerät' : 'Geräte'}</div>
    </div>`;
}

// ── Bedienung ──────────────────────────────────────────────────────────────

function gerSuchen(wert)  { _gerSuche = wert; _gerRender(); }
function gerProbleme()    { _gerProbleme = !_gerProbleme; _gerRender(); }
function gerGruppe(key)   { _gerGruppe = key; _gerRender(); }
function gerModus(modus)  { _gerModus = modus; _gerGruppe = null; _gerRender(); }

// ── Detail-Popup ───────────────────────────────────────────────────────────

function gerDetail(id) {
  _gerDetailId = id;
  _gerBearbeiten = false;
  _gerFehler = '';
  _gerModalRender();
}

function gerDetailZu() {
  _gerDetailId = null;
  _gerBearbeiten = false;
  document.getElementById('gerModalBg')?.remove();
}

function _gerModalRender() {
  const g = _gerGeraet(_gerDetailId);
  if (!g) { gerDetailZu(); return; }

  let bg = document.getElementById('gerModalBg');
  if (!bg) {
    bg = document.createElement('div');
    bg.id = 'gerModalBg';
    bg.className = 'ger-modal-bg';
    // Klick auf den Hintergrund schließt, Klick im Popup nicht.
    bg.onclick = e => { if (e.target === bg) gerDetailZu(); };
    document.body.appendChild(bg);
  }
  bg.innerHTML = `<div class="ger-modal">${_gerBearbeiten ? _gerFormHtml(g) : _gerDetailHtml(g)}</div>`;
}

function _gerDetailHtml(g) {
  const eltern = g.verbunden_an ? _gerGeraet(g.verbunden_an) : null;
  const kinder = (_gerDaten?.geraete || []).filter(k => k.verbunden_an === g.id);
  const sprung = GER_SPRUNG[g.sprung];

  const stamm = [
    ['Hersteller', g.hersteller], ['Modell', g.modell],
    ['Ort', ortName(g.ort)], ['Netz', g.netz_name],
    ['Versorgung', g.versorgung], ['Sicherung', g.sicherung],
    ['Serien-Nr.', g.seriennr], ['Baujahr', g.baujahr],
  ].filter(([, v]) => v);

  const kennzahlen = (g.kennzahlen || []).map(k =>
    `<div class="ger-chip">${_esc(k.l)}<b>${_esc(k.v)}</b></div>`).join('');

  const pgnZeilen = (g.live?.pgns || []).map(p => {
    const klick = typeof openPgnDetail === 'function' && g.live?.src != null;
    return `
      <tr class="${klick ? 'klickbar' : ''}" ${klick ?
        `onclick="gerPgn(${p.pgn},${g.live.src},${p.instanz ?? 'null'})"` : ''}>
        <td><b>${_esc(p.pgn)}</b></td>
        <td>${_esc(p.beschreibung || '')}</td>
        <td style="color:var(--text3)">${p.intervall_ms ? _esc(p.intervall_ms) + ' ms' : ''}</td>
        <td style="color:var(--text3)">${p.age_s != null ? 'vor ' + Math.round(p.age_s) + ' s' : ''}</td>
      </tr>`;
  }).join('');

  const verbindung = (eltern || kinder.length) ? `
    <div class="ger-block">
      <h4>Verbindungen</h4>
      <div class="ger-chips">
        ${eltern ? `<button class="ger-chip" style="cursor:pointer" onclick="gerDetail(${_jsAttr(eltern.id)})">
            ${icon('link', { size: 13 })} hängt an <b>${_esc(eltern.name)}</b></button>` : ''}
        ${kinder.map(k => `<button class="ger-chip" style="cursor:pointer" onclick="gerDetail(${_jsAttr(k.id)})">
            <span class="ger-dot s-${_esc(k.status)}" style="display:inline-block;margin-right:5px"></span>
            ${_esc(k.name)}</button>`).join('')}
      </div>
    </div>` : '';

  const hinweis = !g.gepflegt
    ? `<div class="ger-block"><div class="ger-hinweis">Dieses Gerät meldet sich am Bus oder im WLAN,
         steht aber in keiner Liste. Über <b>Übernehmen</b> wird ein Eintrag daraus, den du benennen
         und einordnen kannst.</div></div>`
    : g.status === 'fremdnetz'
    ? `<div class="ger-block"><div class="ger-hinweis">Sitzt im ${_esc(g.netz_name)}. Der Pi hört dort
         nicht mit — deshalb steht hier kein Zustand, und das ist kein Ausfall.</div></div>`
    : g.status === 'stumm'
    ? `<div class="ger-block"><div class="ger-hinweis">Für dieses Gerät ist keine Rückmeldung
         vorgesehen. Es steht hier, damit die Liste vollständig ist.</div></div>`
    : '';

  return `
    <div class="ger-modal-kopf">
      <div style="color:${GER_KAT_FARBE[g.kategorie] || 'var(--accent)'};margin-top:2px">
        ${icon(GER_KAT_ICON[g.kategorie] || 'chip', { size: 20 })}
      </div>
      <div style="min-width:0">
        <h3>${_esc(g.name)}</h3>
        <div class="ger-modal-sub">${_esc(g.netz_name || '')}${g.ort ? ' · ' + _esc(ortName(g.ort)) : ''}</div>
      </div>
      <div class="ger-pill s-${_esc(g.status)}" style="margin-left:auto;align-self:center">${_esc(g.status_text)}</div>
      <button class="ger-modal-zu" onclick="gerDetailZu()">${icon('close', { size: 15 })}</button>
    </div>
    <div class="ger-modal-koerper">
      <div>
        ${stamm.length ? `<div class="ger-block"><h4>Stammdaten</h4>
          <div class="ger-daten">${stamm.map(([l, v]) =>
            `<div><span>${_esc(l)}</span><b>${_esc(v)}</b></div>`).join('')}</div></div>` : ''}

        ${kennzahlen ? `<div class="ger-block"><h4>Zustand</h4>
          <div class="ger-chips">${kennzahlen}</div></div>` : ''}

        ${pgnZeilen ? `<div class="ger-block"><h4>Nachrichten am Bus</h4>
          <table class="ger-pgn"><thead><tr><th>PGN</th><th>Inhalt</th><th>Takt</th><th>zuletzt</th></tr></thead>
          <tbody>${pgnZeilen}</tbody></table></div>` : ''}

        ${verbindung}

        ${g.notiz ? `<div class="ger-block"><h4>Notiz</h4>
          <div class="ger-hinweis">${_esc(g.notiz)}</div></div>` : ''}

        ${hinweis}

        <div class="ger-aktionen">
          ${g.gepflegt
            ? `<button class="ger-btn" onclick="gerBearbeiten()">${icon('pencil', { size: 14 })} Bearbeiten</button>`
            : `<button class="ger-btn primaer" onclick="gerUebernehmen()">${icon('plus', { size: 14 })} Übernehmen</button>`}
          ${sprung ? `<button class="ger-btn" onclick="gerSprung(${_jsAttr(g.sprung)})">
              ${icon('arrowRight', { size: 14 })} ${_esc(sprung.text)}</button>` : ''}
          ${_gerLink(g.doku) ? `<a class="ger-btn" href="${_esc(g.doku)}" target="_blank" rel="noopener">
              ${icon('info', { size: 14 })} Handbuch</a>` : ''}
        </div>
      </div>
      <div class="ger-riss">
        <div style="opacity:${g.ort ? 1 : .45}">${orteMiniRiss(g.ort, { hoehe: 168 })}</div>
        <div>${g.ort ? _esc(ortName(g.ort)) + ' <span style="opacity:.6">· Bug oben</span>'
                     : 'Einbauort nicht hinterlegt'}</div>
      </div>
    </div>`;
}

/** Nur echte Web-Adressen werden verlinkt — 'javascript:' im Feld bliebe sonst
    ein Knopf, der fremden Code ausführt. */
function _gerLink(wert) {
  return /^https?:\/\//i.test(String(wert || '').trim());
}

function gerPgn(pgn, src, instanz) {
  // Die Bus-Ansicht hat für Rohdaten längst ein eigenes Popup — kein zweites bauen.
  if (typeof openPgnDetail === 'function') openPgnDetail(pgn, src, instanz);
}

function gerSprung(ziel) {
  const eintrag = GER_SPRUNG[ziel];
  if (!eintrag) return;
  const fn = window[eintrag.fn];
  if (typeof fn !== 'function') return;
  gerDetailZu();
  _gerPoller?.stop();
  fn();
}

// ── Bearbeiten ─────────────────────────────────────────────────────────────

function gerBearbeiten() { _gerBearbeiten = true; _gerFehler = ''; _gerModalRender(); }

function gerUebernehmen() {
  const g = _gerGeraet(_gerDetailId);
  if (!g) return;
  _gerBearbeiten = true;
  _gerFehler = '';
  _gerModalRender();
}

function _gerFormHtml(g) {
  const feld = (name, label, wert, opt = {}) => `
    <div class="${opt.breit ? 'ger-breit' : ''}"><label>${_esc(label)}</label>
      <input id="gerF_${name}" type="${opt.typ || 'text'}" value="${_esc(wert ?? '')}"></div>`;
  const auswahl = (name, label, wert, eintraege, leer) => `
    <div><label>${_esc(label)}</label>
      <select id="gerF_${name}">
        <option value="">${_esc(leer)}</option>
        ${eintraege.map(([k, n]) =>
          `<option value="${_esc(k)}"${k === wert ? ' selected' : ''}>${_esc(n)}</option>`).join('')}
      </select></div>`;

  const kategorien = (_gerDaten?.kategorien || []).map(k => [k.key, k.name]);
  // Kategorien, die gerade leer sind, fehlen in der Zusammenfassung — für das
  // Formular braucht es aber alle, sonst lässt sich nie eine leere befüllen.
  GER_KAT_ALLE.forEach(([k, n]) => { if (!kategorien.some(x => x[0] === k)) kategorien.push([k, n]); });
  const netze = (_gerDaten?.netze || []).map(n => [n.key, n.name]);
  const orte  = Object.entries(ORTE).map(([k, o]) => [k, o.name]);
  const andere = (_gerDaten?.geraete || [])
    .filter(x => x.gepflegt && x.id !== g.id).map(x => [x.id, x.name]);

  return `
    <div class="ger-modal-kopf">
      <div style="min-width:0"><h3>${g.gepflegt ? 'Gerät bearbeiten' : 'Gerät übernehmen'}</h3>
        <div class="ger-modal-sub">${_esc(g.name)}</div></div>
      <button class="ger-modal-zu" onclick="gerAbbrechen()">${icon('close', { size: 15 })}</button>
    </div>
    <div class="ger-modal-koerper" style="grid-template-columns:1fr">
      <div class="ger-form">
        ${feld('name', 'Name', g.name, { breit: true })}
        ${auswahl('kategorie', 'Kategorie', g.kategorie, kategorien, 'Sonstiges')}
        ${auswahl('netz', 'Netz', g.netz, netze, 'ohne Anschluss')}
        ${auswahl('ort', 'Einbauort', g.ort, orte, 'nicht hinterlegt')}
        ${auswahl('verbunden_an', 'Hängt an', g.verbunden_an, andere, 'nichts')}
        ${feld('hersteller', 'Hersteller', g.hersteller)}
        ${feld('modell', 'Modell', g.modell)}
        ${feld('seriennr', 'Serien-Nr.', g.seriennr)}
        ${feld('baujahr', 'Baujahr', g.baujahr)}
        ${feld('versorgung', 'Versorgung', g.versorgung)}
        ${feld('sicherung', 'Sicherung', g.sicherung)}
        ${feld('doku', 'Handbuch (Link)', g.doku, { breit: true })}
        <div class="ger-breit"><label>Notiz</label>
          <textarea id="gerF_notiz" rows="3">${_esc(g.notiz ?? '')}</textarea></div>
      </div>
      ${_gerFehler ? `<div class="ger-fehler">${_esc(_gerFehler)}</div>` : ''}
      <div class="ger-aktionen">
        <button class="ger-btn primaer" onclick="gerSpeichern()">${icon('check', { size: 14 })} Speichern</button>
        <button class="ger-btn" onclick="gerAbbrechen()">Abbrechen</button>
        ${g.gepflegt ? `<button class="ger-btn" style="margin-left:auto;color:var(--red)"
            onclick="gerEntfernenFragen(this)">Aus der Liste entfernen</button>` : ''}
      </div>
    </div>`;
}

function gerAbbrechen() {
  _gerBearbeiten = false;
  _gerFehler = '';
  _gerModalRender();
}

function gerEntfernenFragen(btn) {
  // Zweistufig statt window.confirm: am Touchscreen im Kiosk ist ein
  // Browserdialog nicht bedienbar.
  btn.textContent = 'Wirklich entfernen?';
  btn.style.borderColor = 'var(--red)';
  btn.onclick = () => gerEntfernen();
}

async function _gerRegistrySchreiben(aendern) {
  // Immer frisch lesen, ändern, zurückschreiben: die Registry kann sich
  // zwischenzeitlich geändert haben (zweiter Browser, anderer Nutzer).
  const r = await fetch('/api/devices/registry');
  if (!r.ok) throw new Error('Liste nicht lesbar');
  const liste = await r.json();
  const neu = aendern(Array.isArray(liste) ? liste : []);
  const w = await fetch('/api/devices/registry', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(neu),
  });
  if (!w.ok) {
    const text = await w.json().catch(() => null);
    throw new Error(text?.detail || 'Speichern fehlgeschlagen');
  }
}

async function gerSpeichern() {
  const g = _gerGeraet(_gerDetailId);
  if (!g) return;
  const wert = name => ($(`gerF_${name}`)?.value ?? '').trim();
  const eintrag = {
    id: g.id, name: wert('name') || g.name,
    kategorie: wert('kategorie') || 'sonstiges',
    netz: wert('netz') || 'keins',
    ort: wert('ort'), verbunden_an: wert('verbunden_an'),
    hersteller: wert('hersteller'), modell: wert('modell'),
    seriennr: wert('seriennr'), baujahr: wert('baujahr'),
    versorgung: wert('versorgung'), sicherung: wert('sicherung'),
    doku: wert('doku'), notiz: wert('notiz'),
    sprung: g.sprung || '',
  };
  if (g.vorschlag) eintrag.match = g.vorschlag;      // frisch übernommenes Gerät

  try {
    await _gerRegistrySchreiben(liste => {
      const i = liste.findIndex(e => e.id === g.id);
      if (i >= 0) liste[i] = { ...liste[i], ...eintrag };
      else liste.push(eintrag);
      return liste;
    });
    _gerBearbeiten = false;
    _gerFehler = '';
    await gerLaden();
    _gerModalRender();
  } catch (e) {
    _gerFehler = String(e.message || e);
    _gerModalRender();
  }
}

async function gerEntfernen() {
  const g = _gerGeraet(_gerDetailId);
  if (!g) return;
  try {
    await _gerRegistrySchreiben(liste => liste.filter(e => e.id !== g.id));
    gerDetailZu();
    await gerLaden();
  } catch (e) {
    _gerFehler = String(e.message || e);
    _gerModalRender();
  }
}

// Escape schließt erst das Popup, dann die Seite — wie überall sonst auch.
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (_gerDetailId) { gerDetailZu(); return; }
  if (!$('geraeteOverlay')?.classList.contains('hidden')) closeGeraete();
});

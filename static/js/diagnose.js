// ── Logbuch ────────────────────────────────────────────────────────────────
// Das Diagnosewerkzeug auf dem Server. Eigenstaendig gebaut und nicht Teil des
// Bordbundles: es laeuft nur auf dem Server, wird selten geoeffnet, und die
// Bordansicht soll seinetwegen nicht groesser werden — auf dem Pi Zero zaehlt
// jedes Kilobyte im Startpaket.
//
// Es beantwortet eine andere Frage als die Bordansicht. Die zeigt, wie es dem
// Boot jetzt geht; hier will jemand wissen, wie es ihm ergangen ist.

const $ = id => document.getElementById(id);

let _konto = null;
let _tage = 7;
let _alleAusfaelle = false;
let _daten = { verbindung: null, diagnose: null, konten: null };

// ── Formate ────────────────────────────────────────────────────────────────

function zeitpunkt(s) {
  if (!s) return '—';
  const d = new Date(s * 1000);
  return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit' });
}

function dauer(s) {
  if (s == null) return '—';
  if (s < 90) return `${Math.round(s)} s`;
  const m = s / 60;
  if (m < 90) return `${Math.round(m)} min`;
  const h = m / 60;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} Tage`;
}

function esc(t) {
  const d = document.createElement('div');
  d.textContent = t == null ? '' : String(t);
  return d.innerHTML;
}

// Die Woerter fuer die Ausfallarten. Sie stehen hier und nicht im Server:
// der liefert die Art, die Formulierung ist Sache der Anzeige.
const ART = {
  funkloch:  { wort: 'kein Netz',    satz: 'Das Boot war da, aber ohne Verbindung.' },
  stromlos:  { wort: 'stromlos',     satz: 'Der Rechner war aus — Strom weg oder abgeschaltet.' },
  neustart:  { wort: 'Neustart',     satz: 'Der Rechner ist neu gestartet.' },
  dienst:    { wort: 'nur Dienst',   satz: 'Nur die Anwendung wurde neu gestartet, der Rechner lief durch.' },
  erststart: { wort: 'erster Start', satz: 'Davor wurde noch nicht Buch geführt.' },
  unbekannt: { wort: 'unklar',       satz: 'Was in dieser Lücke geschah, lässt sich nicht mehr sagen.' },
};

// ── Laden ──────────────────────────────────────────────────────────────────

async function hole(pfad) {
  const r = await fetch(pfad, { cache: 'no-store' });
  if (r.status === 401) { anmeldungZeigen(); return null; }
  if (!r.ok) return null;
  return r.json();
}

async function start() {
  const z = await (await fetch('/api/zugang', { cache: 'no-store' })).json().catch(() => null);
  if (!z) return;
  if (!z.angemeldet) { anmeldungZeigen(); return; }
  _konto = z.konto;

  if (!(_konto.oberflaechen || []).includes('diagnose')) {
    // Das Tor steht am Endpunkt; hier wird es nur erklaert. Wer die Adresse
    // kennt, aber das Recht nicht hat, bekommt von den Daten nichts zu sehen.
    $('sperre').hidden = false;
    $('sperreText').textContent =
      `Angemeldet als ${_konto.rolle_name}. Das Logbuch ist dem Eigner und `
      + 'Technikern vorbehalten — die Bordansicht steht dir offen.';
    return;
  }

  $('wer').textContent = _konto.rolle_name;
  $('inhalt').hidden = false;
  const darf = _konto.handlungen || [];
  $('kontenTafel').hidden = !darf.includes('verwalten');
  $('wartungTafel').hidden = !darf.includes('fernwarten');

  $('zeitwahl').addEventListener('click', e => {
    const k = e.target.closest('.zw-knopf');
    if (!k) return;
    _tage = Number(k.dataset.tage);
    // Nur die Knöpfe DIESER Auswahl umschalten. Ein
    // document.querySelectorAll('.zw-knopf') träfe auch die Zeitwahl der
    // Messwerte weiter unten und würde sie stillschweigend mit zurücksetzen.
    $('zeitwahl').querySelectorAll('.zw-knopf').forEach(b => b.classList.toggle('an', b === k));
    zeichneStreifen();
    zeichneZahlen();
    zeichneAusfaelle();
  });

  $('messZeit').addEventListener('click', e => {
    const k = e.target.closest('.zw-knopf');
    if (!k) return;
    _messStd = Number(k.dataset.std);
    $('messZeit').querySelectorAll('.zw-knopf').forEach(b => b.classList.toggle('an', b === k));
    messwerteLaden();
  });
  // Beim Drehen oder Größenändern neu zeichnen: Canvas skaliert nicht mit.
  let umbau;
  window.addEventListener('resize', () => {
    clearTimeout(umbau);
    umbau = setTimeout(() => _messDaten && zeichneMesswerte(), 250);
  });

  await laden();
  await messwerteLaden();
  setInterval(laden, 30000);
  // Messwerte seltener: sie ändern sich langsam und kosten mehr.
  setInterval(messwerteLaden, 120000);
}

async function laden() {
  const [v, d] = await Promise.all([hole('/api/verbindung'), hole('/api/diagnose/uebersicht')]);
  if (v) _daten.verbindung = v;
  if (d) _daten.diagnose = d;
  zeichneZustand();
  zeichneStreifen();
  zeichneZahlen();
  zeichneAusfaelle();
  zeichneEreignisse();
  if (!$('kontenTafel').hidden) {
    const k = await hole('/api/konten');
    if (k) { _daten.konten = k; zeichneKonten(); }
  }
  if (!$('wartungTafel').hidden) zeichneWartung();
}

// ── Kopfzeile ──────────────────────────────────────────────────────────────

function zeichneZustand() {
  const v = _daten.verbindung;
  if (!v) return;
  $('zustPunkt').className = 'zust-punkt ' + (v.verbunden ? 'an' : 'weg');
  $('zustText').textContent = v.verbunden
    ? `verbunden seit ${zeitpunkt(v.seit)} · ${v.betriebsart === 'gedrosselt' ? 'gedrosselt (Mobilfunk)' : 'volle Übertragung'}`
    : 'Das Boot ist gerade nicht verbunden';
}

// ── Der Streifen ───────────────────────────────────────────────────────────
// Eine durchgehende Linie, in die die Ausfaelle Loecher schlagen. Bewusst
// nicht ein Balken je Tag: ein Ausfall von zwanzig Minuten soll auch wie
// zwanzig Minuten aussehen und nicht wie ein ganzer Tag.

function zeichneStreifen() {
  const v = _daten.verbindung;
  const feld = $('streifen');
  if (!v) return;
  const jetzt = Date.now() / 1000;
  const von = jetzt - _tage * 86400;
  const spanne = jetzt - von;

  const luecken = (v.luecken || [])
    .filter(l => l.bis > von)
    .map(l => ({ ...l, ab: Math.max(l.ab, von) }))
    .sort((a, b) => a.ab - b.ab);

  let html = '';
  let stand = von;
  for (const l of luecken) {
    if (l.ab > stand) {
      html += `<div class="st-teil an" style="width:${((l.ab - stand) / spanne * 100).toFixed(3)}%"
                    title="verbunden — ${dauer(l.ab - stand)}"></div>`;
    }
    const breite = Math.max((l.bis - l.ab) / spanne * 100, 0.35);   // sonst unsichtbar
    const a = ART[l.art] || ART.unbekannt;
    html += `<div class="st-teil ${esc(l.art)}" style="width:${breite.toFixed(3)}%"
                  title="${esc(a.wort)} · ${esc(dauer(l.dauer_s))} · ab ${esc(zeitpunkt(l.ab))}"></div>`;
    stand = Math.max(stand, l.bis);
  }
  if (stand < jetzt) {
    html += `<div class="st-teil an" style="width:${((jetzt - stand) / spanne * 100).toFixed(3)}%"
                  title="verbunden — ${dauer(jetzt - stand)}"></div>`;
  }
  feld.innerHTML = html || '<div class="st-teil leer" style="width:100%"></div>';

  const achse = $('streifenAchse');
  const schritte = _tage <= 7 ? 7 : (_tage <= 30 ? 6 : 6);
  let a = '';
  for (let i = 0; i <= schritte; i++) {
    const t = new Date((von + spanne * i / schritte) * 1000);
    a += `<span>${t.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' })}</span>`;
  }
  achse.innerHTML = a;
}

// ── Kennzahlen ─────────────────────────────────────────────────────────────

function zeichneZahlen() {
  const v = _daten.verbindung, d = _daten.diagnose;
  if (!v) return;
  const jetzt = Date.now() / 1000;
  const von = jetzt - _tage * 86400;
  const luecken = (v.luecken || []).filter(l => l.bis > von)
    .map(l => ({ ...l, ab: Math.max(l.ab, von) }));
  const weg = luecken.reduce((s, l) => s + (l.bis - l.ab), 0);
  const spanne = jetzt - von;
  const quote = spanne > 0 ? (1 - weg / spanne) * 100 : 100;
  const laengste = luecken.reduce((m, l) => Math.max(m, l.bis - l.ab), 0);
  const stromlos = luecken.filter(l => l.art === 'stromlos').length;

  const zahlen = [
    { wert: quote.toFixed(1) + ' %', lbl: 'verbunden', neben: `in ${_tage} Tagen`,
      art: quote > 95 ? 'gut' : (quote > 80 ? 'mau' : '') },
    { wert: String(luecken.length), lbl: 'Unterbrechungen',
      neben: luecken.length ? `längste ${dauer(laengste)}` : 'keine' },
    { wert: String(stromlos), lbl: 'davon stromlos',
      neben: stromlos ? 'Rechner war aus' : 'kein Stromausfall', art: stromlos ? 'mau' : 'gut' },
    { wert: d ? String(d.verlauf_stand) : '—', lbl: 'Messwerte im Verlauf',
      neben: d && d.geparkt ? `${d.geparkt} ohne sichere Zeit` : 'alle zeitlich eingeordnet' },
  ];
  $('zahlen').innerHTML = zahlen.map(z => `
    <div class="zahl">
      <div class="zahl-wert ${z.art || ''}">${esc(z.wert)}</div>
      <div class="zahl-lbl">${esc(z.lbl)}</div>
      <div class="zahl-neben">${esc(z.neben)}</div>
    </div>`).join('');
}

// ── Ausfälle ───────────────────────────────────────────────────────────────

function zeichneAusfaelle() {
  const v = _daten.verbindung;
  if (!v) return;
  const jetzt = Date.now() / 1000;
  const von = jetzt - _tage * 86400;
  const luecken = (v.luecken || []).filter(l => l.bis > von)
    .sort((a, b) => b.ab - a.ab);
  $('ausfaelleZahl').textContent = luecken.length
    ? `${luecken.length} in ${_tage} Tagen` : '';
  if (!luecken.length) {
    $('ausfaelle').innerHTML =
      `<div class="leerlauf">Keine Unterbrechung in den letzten ${_tage} Tagen.</div>`;
    return;
  }
  // Nur die jüngsten zeigen. Bei einem Update-Abend stehen hier schnell zwanzig
  // Einträge, und die drängen alles Wichtige aus dem Blick — gesucht wird meist
  // der letzte Ausfall, nicht der vorletzte Monat.
  const zeigen = _alleAusfaelle ? luecken : luecken.slice(0, 6);
  const mehr = $('ausfaelleMehr');
  if (mehr) {
    mehr.hidden = luecken.length <= 6 || _alleAusfaelle;
    mehr.textContent = `Alle ${luecken.length} zeigen`;
  }
  $('ausfaelle').innerHTML = zeigen.map(l => {
    const a = ART[l.art] || ART.unbekannt;
    return `<div class="zeile">
      <div class="z-zeit">${esc(zeitpunkt(l.ab))}</div>
      <div>
        <div class="z-text"><span class="z-marke ${esc(l.art)}">${esc(a.wort)}</span>${esc(l.grund || a.satz)}</div>
        <div class="z-neben">bis ${esc(zeitpunkt(l.bis))}</div>
      </div>
      <div class="z-dauer">${esc(dauer(l.dauer_s))}</div>
    </div>`;
  }).join('');
}

function ausfaelleAlle() {
  _alleAusfaelle = true;
  zeichneAusfaelle();
}

function zeichneEreignisse() {
  const d = _daten.diagnose;
  if (!d) return;
  const e = (d.ereignisse || []).slice(0, 40);
  if (!e.length) {
    $('ereignisse').innerHTML = '<div class="leerlauf">Noch nichts gemeldet.</div>';
    return;
  }
  $('ereignisse').innerHTML = e.map(x => `
    <div class="zeile">
      <div class="z-zeit">${esc(zeitpunkt(x.zeit || x.wand))}</div>
      <div><div class="z-text">${esc(ereignisText(x))}</div></div>
      <div class="z-dauer"></div>
    </div>`).join('');
}

function ereignisText(x) {
  const d = x.daten || x;
  switch (x.art) {
    case 'betriebsart':
      return d.betriebsart === 'gedrosselt'
        ? 'Übertragung gedrosselt — das Boot hängt am Mobilfunk'
        : 'Volle Übertragung — das Boot hat wieder festes Netz';
    case 'start':   return 'Das Boot hat sich angemeldet';
    case 'alarm':   return `Alarm: ${d.text || d.name || 'ohne Angabe'}`;
    default:        return x.art || 'Ereignis';
  }
}

// ── Konten ─────────────────────────────────────────────────────────────────

function zeichneKonten() {
  const k = _daten.konten;
  if (!k) return;
  const rollen = Object.fromEntries((k.rollen || []).map(r => [r.schluessel, r]));
  $('konten').innerHTML = (k.konten || []).map(c => {
    const r = rollen[c.rolle] || {};
    const ich = c.name === (_konto && _konto.name);
    return `<div class="konto">
      <div>
        <div class="k-name ${c.gesperrt ? 'gesperrt' : ''}">${esc(c.name)}${ich ? ' <span class="hinweis">(du)</span>' : ''}</div>
        <div class="k-rolle">${esc(r.name || c.rolle)}${c.gesperrt ? ' · gesperrt' : ''}</div>
        <div class="k-darf">${esc((r.handlungen || []).join(' · '))}</div>
      </div>
      <div class="k-tat">
        ${ich ? '' : `<button class="knopf stumm" onclick="kontoSperren('${esc(c.name)}', ${!c.gesperrt})">${c.gesperrt ? 'Entsperren' : 'Sperren'}</button>`}
        ${ich ? '' : `<button class="knopf warn" onclick="kontoLoeschen('${esc(c.name)}')">Löschen</button>`}
      </div>
    </div>`;
  }).join('') || '<div class="leerlauf">Noch kein Konto.</div>';
}

function kontoNeuOeffnen() {
  const rollen = (_daten.konten && _daten.konten.rollen) || [];
  popZeigen(`
    <div class="pop-titel">Konto anlegen</div>
    <label class="pop-feld"><span>Name</span>
      <input id="nkName" type="text" autocapitalize="none" autocorrect="off" spellcheck="false"></label>
    <label class="pop-feld"><span>Passwort (mindestens 8 Zeichen)</span>
      <input id="nkPw" type="text"></label>
    <label class="pop-feld"><span>Rolle</span>
      <select id="nkRolle">
        ${rollen.map(r => `<option value="${esc(r.schluessel)}">${esc(r.name)} — ${esc(r.handlungen.join(', '))}</option>`).join('')}
      </select></label>
    <div class="pop-fehler hidden" id="nkFehler"></div>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="kontoAnlegen()">Anlegen</button>
    </div>`);
}

async function kontoAnlegen() {
  const name = $('nkName').value.trim(), pw = $('nkPw').value, rolle = $('nkRolle').value;
  const fehler = $('nkFehler');
  const r = await fetch('/api/konten', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, passwort: pw, rolle }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    fehler.textContent = d.detail || 'Das hat nicht geklappt.';
    fehler.classList.remove('hidden');
    return;
  }
  popSchliessen();
  laden();
}

async function kontoSperren(name, sperren) {
  await fetch('/api/konten/' + encodeURIComponent(name), {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gesperrt: sperren }),
  });
  laden();
}

function kontoLoeschen(name) {
  popZeigen(`
    <div class="pop-titel">Konto ${esc(name)} löschen?</div>
    <p style="color:var(--text2);font-size:13px;margin:0 0 18px">
      Laufende Sitzungen dieses Kontos enden sofort — auch an Bord.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf warn" onclick="kontoLoeschenJa('${esc(name)}')">Löschen</button>
    </div>`);
}

async function kontoLoeschenJa(name) {
  await fetch('/api/konten/' + encodeURIComponent(name), { method: 'DELETE' });
  popSchliessen();
  laden();
}

// ── Fernwartung ────────────────────────────────────────────────────────────

function zeichneWartung() {
  const v = _daten.verbindung;
  const da = v && v.verbunden;
  $('wartung').innerHTML = `
    <div class="w-zeile">
      <div>
        <div class="w-text">Bordrechner aktualisieren</div>
        <div class="w-neben">Holt den neuesten Stand und startet die Anwendung neu.
          Dauert etwa eine halbe Minute, in der das Boot nicht antwortet.</div>
      </div>
      <button class="knopf" ${da ? '' : 'disabled'} onclick="fernUpdate()">Aktualisieren</button>
    </div>
    <div class="w-zeile">
      <div>
        <div class="w-text">Uhr des Bordrechners stellen</div>
        <div class="w-neben">Der Pi hat keine gepufferte Uhr. Nach einem Stromausfall
          geht sie falsch, bis er Netz hat — dann lassen sich Messwerte nicht
          zeitlich einordnen.</div>
      </div>
      <button class="knopf stumm" ${da ? '' : 'disabled'} onclick="fernZeit()">Stellen</button>
    </div>
    ${da ? '' : '<div class="leerlauf">Das Boot ist nicht verbunden — Fernwartung geht erst wieder, wenn es sich meldet.</div>'}`;
}

async function fernUpdate() {
  popZeigen(`
    <div class="pop-titel">Bordrechner aktualisieren?</div>
    <p style="color:var(--text2);font-size:13px;margin:0 0 18px">
      Die Anwendung an Bord startet dabei neu. Wer gerade davor sitzt, sieht
      für etwa eine halbe Minute nichts.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="fernUpdateJa()">Aktualisieren</button>
    </div>`);
}

async function fernUpdateJa() {
  const k = $('popKarte');
  k.innerHTML = '<div class="pop-titel">Läuft…</div><p style="color:var(--text2);font-size:13px">Das Boot arbeitet.</p>';
  const r = await fetch('/api/system/update', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  const d = await r.json().catch(() => ({}));
  k.innerHTML = `<div class="pop-titel">${r.ok ? 'Angestoßen' : 'Nicht möglich'}</div>
    <p style="color:var(--text2);font-size:13px;white-space:pre-wrap;max-height:220px;overflow:auto">${
      esc(r.ok ? (d.output || 'Der Bordrechner aktualisiert sich.') : (d.detail || 'Unbekannter Fehler'))}</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Schließen</button></div>`;
}

async function fernZeit() {
  const r = await fetch('/api/system/time-sync', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ zeit: Date.now() / 1000 }) });
  popZeigen(`<div class="pop-titel">${r.ok ? 'Uhr gestellt' : 'Nicht möglich'}</div>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Schließen</button></div>`);
}

// ── Popup ──────────────────────────────────────────────────────────────────

function popZeigen(html) {
  $('popKarte').innerHTML = html;
  $('pop').classList.remove('hidden');
}
function popSchliessen() { $('pop').classList.add('hidden'); }

// ── Anmeldung ──────────────────────────────────────────────────────────────
// Eigene, kleine Fassung: die Bordansicht bringt ihre eigene mit, und diese
// Seite soll deren Bundle nicht laden muessen.

function anmeldungZeigen(meldung) {
  let f = $('anmeldung');
  if (!f) {
    f = document.createElement('div');
    f.id = 'anmeldung'; f.className = 'anmeldung';
    document.body.appendChild(f);
  }
  f.innerHTML = `
    <form class="anm-karte" onsubmit="return anmelden(event)">
      <div class="anm-marke">MAVE <span>Logbuch</span></div>
      <div class="anm-wohin">Diagnose und Fernwartung</div>
      <label class="anm-feld"><span>Name</span>
        <input id="anmName" type="text" autocomplete="username" autocapitalize="none"
               autocorrect="off" spellcheck="false" required></label>
      <label class="anm-feld"><span>Passwort</span>
        <input id="anmPw" type="password" autocomplete="current-password" required></label>
      <div class="anm-fehler${meldung ? '' : ' hidden'}" id="anmFehler">${esc(meldung || '')}</div>
      <button class="anm-knopf" type="submit" id="anmKnopf">Anmelden</button>
    </form>`;
  f.classList.remove('hidden');
  setTimeout(() => $('anmName') && $('anmName').focus(), 60);
}

async function anmelden(ev) {
  ev.preventDefault();
  const knopf = $('anmKnopf'), fehler = $('anmFehler');
  knopf.disabled = true; knopf.textContent = 'Einen Moment…';
  try {
    const r = await fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('anmName').value, passwort: $('anmPw').value }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      fehler.textContent = d.detail || 'Anmeldung nicht möglich.';
      fehler.classList.remove('hidden');
      $('anmPw').value = '';
      return false;
    }
    location.reload();
  } catch (_) {
    fehler.textContent = 'Keine Verbindung.';
    fehler.classList.remove('hidden');
  } finally {
    knopf.disabled = false; knopf.textContent = 'Anmelden';
  }
  return false;
}

async function abmelden() {
  try { await fetch('/api/logout', { method: 'POST' }); } catch (_) {}
  location.reload();
}

start();

// ── Messwerte ──────────────────────────────────────────────────────────────
// Mehrere Graphen untereinander an EINER Zeitachse. Das ist der Unterschied zur
// Bordansicht: dort liest man einen Wert im Vorbeigehen ab, hier sucht man
// Zusammenhänge — dass die Spannung einbricht, WÄHREND der Strom steigt, sieht
// man nur, wenn beide übereinanderliegen und dieselbe Achse teilen.
//
// Gezeichnet wird je Reihe ein Band zwischen kleinstem und größtem Wert des
// Zeitfensters und die Mittellinie hinein. Nur den Mittelwert zu zeichnen wäre
// glatter, würde aber genau das verschlucken, weswegen man hier hinschaut: die
// kurze Spitze, den Einbruch, den Ausreißer.

const GRUPPEN = [
  { schluessel: 'ladung',   name: 'Ladestand',  einheit: '%',
    felder: [{ f: 'soc', n: 'Ladestand', farbe: '#34d399' }] },
  { schluessel: 'spannung', name: 'Spannung',   einheit: 'V',
    felder: [{ f: 'voltage', n: 'Bordspannung', farbe: '#60a5fa' }] },
  { schluessel: 'strom',    name: 'Strom',      einheit: 'A',
    felder: [{ f: 'current', n: 'Bilanz', farbe: '#22d3ee' },
             { f: 'current_charge', n: 'Zufluss', farbe: '#34d399' },
             { f: 'current_discharge', n: 'Verbrauch', farbe: '#f87171' }] },
  { schluessel: 'quellen',  name: 'Ladequellen', einheit: 'W',
    felder: [{ f: 'charger', n: 'Ladegerät', farbe: '#fbbf24' },
             { f: 'solar1', n: 'Solar', farbe: '#a78bfa' },
             { f: 'orion', n: 'Lichtmaschine', farbe: '#fb923c' }] },
  { schluessel: 'tanks',    name: 'Tanks',      einheit: '%',
    felder: [{ f: 'tank1', n: 'Wasser', farbe: '#60a5fa' },
             { f: 'tank2', n: 'Diesel', farbe: '#fbbf24' }] },
  { schluessel: 'zellen',   name: 'Zellabweichung', einheit: 'V',
    felder: [{ f: 'zelldiff', n: 'Größte Abweichung', farbe: '#a78bfa' }] },
];

let _messStd = 24;
let _messDaten = null;
let _aus = new Set();          // abgewählte Gruppen

async function messwerteLaden() {
  const bis = Date.now() / 1000;
  const von = bis - _messStd * 3600;
  const feld = $('reihen');
  feld.innerHTML = '<div class="mess-leer">wird geladen…</div>';
  const d = await hole(`/api/verlauf/reihen?von=${Math.floor(von)}&bis=${Math.ceil(bis)}&punkte=700`);
  if (!d) { feld.innerHTML = '<div class="mess-leer">Keine Daten abrufbar.</div>'; return; }
  _messDaten = d;
  zeichneMesswerte();
}

function zeichneMesswerte() {
  const d = _messDaten;
  const feld = $('reihen');
  if (!d || !d.punkte.length) {
    feld.innerHTML = '<div class="mess-leer">Für diesen Zeitraum liegt nichts vor.</div>';
    $('messStand').textContent = '';
    $('zeitachse').innerHTML = '';
    $('gruppenwahl').innerHTML = '';
    return;
  }

  const stunden = (d.bis - d.von) / 3600;
  $('messStand').textContent =
    `${d.roh_anzahl.toLocaleString('de-DE')} Messwerte, zusammengefasst zu ${d.punkte.length} `
    + `Punkten à ${dauer(d.eimer_s)}`;

  // Nur Gruppen zeigen, für die es Daten gibt
  const da = GRUPPEN.filter(g => g.felder.some(x => d.felder.includes(x.f)));
  $('gruppenwahl').innerHTML = da.map(g => `
    <button class="gw-knopf${_aus.has(g.schluessel) ? '' : ' an'}"
            onclick="gruppeUmschalten('${g.schluessel}')">
      <span class="gw-punkt" style="background:${g.felder[0].farbe}"></span>${esc(g.name)}
    </button>`).join('');

  const sichtbar = da.filter(g => !_aus.has(g.schluessel));
  feld.innerHTML = sichtbar.map(g => `
    <div class="reihe" data-gruppe="${g.schluessel}">
      <div class="reihe-kopf">
        <span class="reihe-name">${esc(g.name)}</span>
        <span class="reihe-jetzt" id="jetzt-${g.schluessel}"></span>
      </div>
      <div class="reihe-bild">
        <canvas id="cv-${g.schluessel}"></canvas>
        <span class="reihe-spanne" id="oben-${g.schluessel}"></span>
        <span class="reihe-spanne unten" id="unten-${g.schluessel}"></span>
      </div>
    </div>`).join('') + '<div class="faden" id="faden"><span class="faden-zeit" id="fadenZeit"></span></div>';

  for (const g of sichtbar) zeichneReihe(g, d);
  zeichneZeitachse(d);
  fadenVerdrahten(d, sichtbar);
}

function zeichneReihe(g, d) {
  const cv = $('cv-' + g.schluessel);
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const b = cv.getBoundingClientRect();
  cv.width = Math.max(1, Math.round(b.width * dpr));
  cv.height = Math.max(1, Math.round(b.height * dpr));
  const c = cv.getContext('2d');
  c.scale(dpr, dpr);
  const B = b.width, H = b.height;

  const felder = g.felder.filter(x => d.felder.includes(x.f));
  // Gemeinsame Skala je Gruppe: nur so sind Zufluss und Verbrauch vergleichbar.
  let lo = Infinity, hi = -Infinity;
  for (const p of d.punkte) {
    for (const x of felder) {
      const w = p[x.f];
      if (!w) continue;
      lo = Math.min(lo, w[1]); hi = Math.max(hi, w[2]);
    }
  }
  if (!isFinite(lo)) return;
  if (hi - lo < 1e-9) { hi = lo + 1; lo -= 0; }
  const luft = (hi - lo) * 0.08;
  lo -= luft; hi += luft;

  const x = t => (t - d.von) / (d.bis - d.von) * B;
  const y = w => H - (w - lo) / (hi - lo) * H;

  // Nulllinie, wo sie in den Ausschnitt fällt — bei Strömen die wichtigste
  // Orientierung: darüber fließt hinein, darunter hinaus.
  if (lo < 0 && hi > 0) {
    c.strokeStyle = 'rgba(148,163,184,.35)';
    c.lineWidth = 1;
    c.setLineDash([3, 3]);
    c.beginPath(); c.moveTo(0, y(0)); c.lineTo(B, y(0)); c.stroke();
    c.setLineDash([]);
  }

  for (const x_ of felder) {
    // Erst das Band zwischen kleinstem und größtem Wert…
    c.fillStyle = x_.farbe + '26';
    c.beginPath();
    let auf = false;
    for (const p of d.punkte) {
      const w = p[x_.f]; if (!w) continue;
      const px = x(p.t);
      if (!auf) { c.moveTo(px, y(w[2])); auf = true; } else c.lineTo(px, y(w[2]));
    }
    for (let i = d.punkte.length - 1; i >= 0; i--) {
      const w = d.punkte[i][x_.f]; if (!w) continue;
      c.lineTo(x(d.punkte[i].t), y(w[1]));
    }
    if (auf) { c.closePath(); c.fill(); }

    // …dann die Mittellinie hinein
    c.strokeStyle = x_.farbe;
    c.lineWidth = 1.6;
    c.lineJoin = 'round';
    c.beginPath();
    auf = false;
    for (const p of d.punkte) {
      const w = p[x_.f]; if (!w) continue;
      const px = x(p.t), py = y(w[0]);
      if (!auf) { c.moveTo(px, py); auf = true; } else c.lineTo(px, py);
    }
    c.stroke();
  }

  const k = (hi - lo) < 2 ? 2 : ((hi - lo) < 20 ? 1 : 0);
  $('oben-' + g.schluessel).textContent = hi.toFixed(k) + ' ' + g.einheit;
  $('unten-' + g.schluessel).textContent = lo.toFixed(k) + ' ' + g.einheit;
  werteZeigen(g, d, d.punkte.length - 1);
}

function werteZeigen(g, d, i) {
  const ziel = $('jetzt-' + g.schluessel);
  if (!ziel) return;
  const p = d.punkte[i];
  if (!p) return;
  const felder = g.felder.filter(x => d.felder.includes(x.f) && p[x.f]);
  const k = g.einheit === 'V' ? 2 : (g.einheit === 'W' ? 0 : 1);
  ziel.innerHTML = felder.map(x => `
    <span class="rj-wert">
      <span class="rj-punkt" style="background:${x.farbe}"></span>
      <span class="rj-zahl">${p[x.f][0].toFixed(k)}</span>
      <span class="rj-einheit">${esc(g.einheit)} ${esc(x.n)}</span>
    </span>`).join('');
}

function zeichneZeitachse(d) {
  const n = 6;
  let h = '';
  for (let i = 0; i <= n; i++) {
    const t = new Date((d.von + (d.bis - d.von) * i / n) * 1000);
    const lang = (d.bis - d.von) > 3 * 86400;
    h += `<span>${t.toLocaleString('de-DE', lang
      ? { day: '2-digit', month: '2-digit' }
      : { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>`;
  }
  $('zeitachse').innerHTML = h;
}

function fadenVerdrahten(d, sichtbar) {
  const feld = $('reihen'), faden = $('faden');
  if (!feld || !faden) return;
  feld.onmousemove = e => {
    const b = feld.getBoundingClientRect();
    const rel = (e.clientX - b.left) / b.width;
    if (rel < 0 || rel > 1) return;
    const i = Math.min(d.punkte.length - 1, Math.max(0, Math.round(rel * (d.punkte.length - 1))));
    faden.classList.add('an');
    faden.style.left = (d.punkte[i].t - d.von) / (d.bis - d.von) * b.width + 'px';
    $('fadenZeit').textContent = new Date(d.punkte[i].t * 1000)
      .toLocaleString('de-DE', { day: '2-digit', month: '2-digit',
                                 hour: '2-digit', minute: '2-digit' });
    for (const g of sichtbar) werteZeigen(g, d, i);
  };
  feld.onmouseleave = () => {
    faden.classList.remove('an');
    for (const g of sichtbar) werteZeigen(g, d, d.punkte.length - 1);
  };
}

function gruppeUmschalten(schluessel) {
  if (_aus.has(schluessel)) _aus.delete(schluessel); else _aus.add(schluessel);
  zeichneMesswerte();
}

function exportOeffnen() {
  const bis = Math.ceil(Date.now() / 1000);
  const von = Math.floor(bis - _messStd * 3600);
  popZeigen(`
    <div class="pop-titel">Daten holen</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 16px">
      Der angezeigte Zeitraum als CSV — <b>ungekürzt</b>, also jeder einzelne
      Messwert und nicht die zusammengefassten Punkte des Graphen.
      Semikolon als Trenner, Komma als Dezimalzeichen: so öffnet eine deutsche
      Tabellenkalkulation die Datei ohne Nachfragen.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <a class="knopf" href="/api/verlauf/export?von=${von}&bis=${bis}"
         onclick="setTimeout(popSchliessen, 400)">Herunterladen</a>
    </div>`);
}

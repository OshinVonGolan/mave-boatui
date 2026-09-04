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
let _daten = { verbindung: null, diagnose: null, konten: null,
               zustand: null, anwesend: null };

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

/** Wo die Bordansicht liegt — von hier aus gesehen.
 *
 *  Unter dem Logbuch-Namen fuehrt "/" wieder hierher. Ein Verweis darauf
 *  drehte sich im Kreis, und von der Abweisung aus landete man wieder auf der
 *  Abweisung. Deshalb EINE Stelle, die das ausrechnet — vorher stand sie
 *  zweimal da, und die zweite war falsch.
 */
function _bordansicht() {
  const h = location.hostname;
  if (h.startsWith('logbuch.')) {
    return location.protocol + '//' + h.replace(/^logbuch\./, 'mave.') + '/';
  }
  return '/';
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
    // Dieselbe Rechnung wie beim Verweis in der Kopfzeile — sonst landet man
    // von der Abweisung aus wieder auf der Abweisung.
    $('sperreZurueck').href = _bordansicht();
    $('sperreText').textContent =
      `Angemeldet als ${_konto.rolle_name}. Das Logbuch ist dem Eigner und `
      + 'Technikern vorbehalten — die Bordansicht steht dir offen.';
    return;
  }

  $('zurBordansicht').href = _bordansicht();
  $('wer').textContent = (_konto.anzeigename || '') + ' · ' + _konto.rolle_name;
  $('inhalt').hidden = false;
  const darf = _konto.handlungen || [];
  $('slKonten').hidden = !darf.includes('verwalten');
  $('slWartung').hidden = !darf.includes('fernwarten');
  $('slAenderungen').hidden = !darf.includes('fernwarten');
  $('slMitschnitt').hidden = !darf.includes('fernwarten');
  // Eine Gruppenueberschrift ohne Punkte darunter ist eine Ueberschrift ueber
  // nichts.
  $('slGruppeSystem').hidden = !darf.includes('fernwarten');
  $('slGruppeVerwaltung').hidden = !darf.includes('verwalten');

  $('seitenleiste').addEventListener('click', e => {
    const k = e.target.closest('.sl-knopf');
    if (k) seiteZeigen(k.dataset.seite);
  });
  // Die Seite steht in der Adresse: ein Lesezeichen auf die Messwerte soll
  // dort landen, und der Zurück-Knopf soll tun, was er verspricht.
  window.addEventListener('hashchange', () => seiteZeigen(location.hash.slice(1) || 'ueberblick'));
  seiteZeigen(location.hash.slice(1) || 'ueberblick');

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

let _seite = 'ueberblick';

function seiteZeigen(name) {
  const knopf = document.querySelector(`.sl-knopf[data-seite="${name}"]`);
  if (!knopf || knopf.hidden) name = 'ueberblick';
  _seite = name;
  document.querySelectorAll('.sl-knopf').forEach(k =>
    k.classList.toggle('an', k.dataset.seite === name));
  document.querySelectorAll('.seite').forEach(s => { s.hidden = s.dataset.seite !== name; });
  if (location.hash.slice(1) !== name) history.replaceState(null, '', '#' + name);
  // Canvas-Groessen stimmen nur, wenn das Element sichtbar IST — auf einer
  // verborgenen Seite ist die Breite null und der Graph bliebe leer.
  if (name === 'messwerte' && _messDaten) requestAnimationFrame(zeichneMesswerte);
  if (name === 'verfuegbarkeit') zeichneStreifen();
  // Das Abfragen kostet ein git fetch am Boot — also nur beim Öffnen der
  // Seite und nicht im Dauertakt.
  if (name === 'aenderungen') staendeLaden();
  if (name === 'mitschnitt') mitschnittLaden();
  window.scrollTo(0, 0);
}

// ── Überblick ──────────────────────────────────────────────────────────────
// Die Startseite beantwortet drei Fragen auf einen Blick: Wie steht es gerade?
// Wer ist da? Und war zuletzt etwas los?

// Ein Symbol je Kennzahl. Aus dem eigenen SVG-Satz, keine Schriftzeichen:
// die Oberflaeche fuehrt keine Bildzeichen als Symbolersatz.
const _svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ZAHL_ICON = {
  'Ladestand': _svg('<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M22 11v2"/><rect x="4" y="9" width="9" height="6" rx="1" fill="currentColor" stroke="none"/>'),
  'Bilanz':    _svg('<path d="M13 2L4.5 13H11l-1 9 8.5-11H12l1-9z"/>'),
  'Wasser':    _svg('<path d="M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z"/>'),
  'Diesel':    _svg('<path d="M4 20V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v14"/><path d="M3 20h13"/><path d="M17 9l3 2v7a1.5 1.5 0 0 1-3 0"/>'),
  'Innen':     _svg('<path d="M10 13.5V5a2 2 0 1 1 4 0v8.5a4 4 0 1 1-4 0z"/>'),
  standard:    _svg('<circle cx="12" cy="12" r="9"/>'),
};

function zeichneUeberblick() {
  const z = _daten.zustand;
  const v = _daten.verbindung;
  if (z) {
    const b = z.battery || {}, t = z.tanks || {}, h = z.heizung || {};
    const alter = z.alter_s;
    $('ueberblickStand').textContent = (z.quelle === 'server' && alter != null)
      ? `Stand: ${alter < 90 ? 'gerade eben' : 'vor ' + dauer(alter)}`
      : 'Stand: laufend';
    const raeume = Object.values(h.rooms || h.raeume || {});
    const temp = raeume.map(r => r.temp ?? r.temperatur)
                       .filter(x => typeof x === 'number');
    const zahlen = [
      { wert: b.soc != null ? Math.round(b.soc) + ' %' : '—', lbl: 'Ladestand',
        neben: b.voltage != null ? b.voltage.toFixed(2) + ' V' : '',
        art: b.soc == null ? '' : (b.soc > 50 ? 'gut' : (b.soc > 25 ? 'mau' : '')) },
      { wert: b.current != null ? b.current.toFixed(1) + ' A' : '—', lbl: 'Bilanz',
        neben: b.current == null ? '' : (b.current >= 0 ? 'wird geladen' : 'wird entnommen') },
      // Die Tanks kommen flach und in Prozent; die Liter rechnet die
      // Bordansicht aus der Tankgröße. 200 L je Tank, wie dort.
      { wert: typeof t.tank1 === 'number' ? Math.round(t.tank1) + ' %' : '—',
        lbl: 'Wasser', neben: typeof t.tank1 === 'number' ? Math.round(t.tank1 * 2) + ' von 200 L' : '',
        art: typeof t.tank1 === 'number' && t.tank1 < 15 ? 'mau' : '' },
      { wert: typeof t.tank2 === 'number' ? Math.round(t.tank2) + ' %' : '—',
        lbl: 'Diesel', neben: typeof t.tank2 === 'number' ? Math.round(t.tank2 * 2) + ' von 200 L' : '',
        art: typeof t.tank2 === 'number' && t.tank2 < 15 ? 'mau' : '' },
      { wert: temp.length ? (temp.reduce((a, c) => a + c, 0) / temp.length).toFixed(1) + ' °C' : '—',
        lbl: 'Innen',
        neben: temp.length ? (h.command === 'on' || h.running ? 'Heizung läuft' : 'Heizung aus')
                           : 'kein Raumfühler online' },
    ];
    $('jetztZahlen').innerHTML = zahlen.map(x => `
      <div class="zahl ${x.art || ''}">
        <span class="zahl-icon">${ZAHL_ICON[x.lbl] || ZAHL_ICON.standard}</span>
        <div class="zahl-wert ${x.art || ''}">${esc(x.wert)}</div>
        <div class="zahl-lbl">${esc(x.lbl)}</div>
        <div class="zahl-neben">${esc(x.neben)}</div>
      </div>`).join('');
  }

  if (v) {
    $('verbKurz').textContent = v.verbunden
      ? 'seit ' + zeitpunkt(v.seit) : 'zurzeit unterbrochen';
    _streifenBauen('streifenKurz', 'streifenKurzAchse', 1);
  }

  // Was zuletzt los war: Ausfälle und Ereignisse in EINER Liste, damit man
  // nicht zwei Zeitachsen im Kopf zusammenfügen muss.
  const eintraege = [];
  for (const l of (v?.luecken || [])) {
    const a = ART[l.art] || ART.unbekannt;
    eintraege.push({ t: l.ab, marke: l.art, wort: a.wort, text: l.grund || a.satz,
                     rechts: dauer(l.dauer_s) });
  }
  for (const e of (_daten.diagnose?.ereignisse || [])) {
    eintraege.push({ t: e.zeit || e.wand, marke: '', wort: '', text: ereignisText(e), rechts: '' });
  }
  eintraege.sort((a, b) => (b.t || 0) - (a.t || 0));
  const zeigen = eintraege.slice(0, 8);
  $('letztes').innerHTML = zeigen.length ? zeigen.map(x => `
    <div class="zeile">
      <div class="z-zeit">${esc(zeitpunkt(x.t))}</div>
      <div><div class="z-text">${x.marke ? `<span class="z-marke ${esc(x.marke)}">${esc(x.wort)}</span>` : ''}${esc(x.text)}</div></div>
      <div class="z-dauer">${esc(x.rechts)}</div>
    </div>`).join('') : '<div class="leerlauf">Nichts Auffälliges.</div>';
}

function zeichneAnwesend() {
  const a = _daten.anwesend;
  const feld = $('anwesend');
  if (!a) { feld.innerHTML = '<div class="leerlauf">wird geladen…</div>'; return; }
  const jetzt = Date.now() / 1000;

  // Mehrere Sitzungen desselben Kontos auf demselben Gerät sind KEINE
  // mehreren Anwesenden — sie entstehen bei jedem neuen Browserfenster und bei
  // jedem Werkzeugaufruf. Zusammengefasst wird nach Konto und Gerät, gezeigt
  // wird die jüngste; die Anzahl steht daneben, damit vergessene Anmeldungen
  // trotzdem auffallen.
  const buendeln = (liste) => {
    const m = new Map();
    for (const s of liste || []) {
      const schluessel = (s.konto || '') + '|' + (s.geraet || '') + '|' + (s.herkunft || '');
      const da = m.get(schluessel);
      if (!da) m.set(schluessel, { ...s, anzahl: 1 });
      else {
        da.anzahl++;
        if ((s.zuletzt || 0) > (da.zuletzt || 0)) { da.zuletzt = s.zuletzt; }
        if ((s.seit || 0) < (da.seit || 0)) { da.seit = s.seit; }
      }
    }
    return [...m.values()].sort((a, b) => (b.zuletzt || 0) - (a.zuletzt || 0));
  };

  const zeile = (s, art) => {
    const seit = jetzt - (s.zuletzt || 0);
    const still = seit > 300;
    return `<div class="aw-zeile">
      <span class="aw-punkt ${still ? 'still' : art}"></span>
      <span>
        <div class="aw-wer">${esc(s.anzeigename || s.konto)}${s.kiosk ? ' <span class="hinweis">(Kiosk)</span>' : ''}</div>
        <div class="aw-was">${esc(s.geraet || 'unbekanntes Gerät')}${s.herkunft ? ' · ' + esc(s.herkunft) : ''}${
          s.anzahl > 1 ? ' · ' + s.anzahl + ' Anmeldungen' : ''}</div>
      </span>
      <span class="aw-wann">${still ? 'zuletzt vor ' + dauer(seit) : 'gerade aktiv'}</span>
    </div>`;
  };

  // "Wer ist da" heisst: wer JETZT da ist. Eine Sitzung, die seit einer
  // Stunde nichts mehr getan hat, gehoert nicht in diese Liste — das Gerät
  // liegt in der Schublade, der Browser ist zu, oder jemand ist von Bord. Die
  // Sitzung bleibt gültig, sie wird nur nicht mehr als Anwesenheit gezählt.
  const ANWESEND_S = 30 * 60;
  const frisch = (liste) => (liste || []).filter(s => jetzt - (s.zuletzt || 0) < ANWESEND_S);
  const stille = (liste) => (liste || []).filter(s => jetzt - (s.zuletzt || 0) >= ANWESEND_S);

  let h = '';
  const bordDa = buendeln(frisch(a.an_bord));
  const fernDa = buendeln(frisch(a.ueber_server));
  const ruhend = buendeln([...stille(a.an_bord), ...stille(a.ueber_server)]);

  if (bordDa.length) {
    h += '<div class="aw-gruppe">An Bord</div>' + bordDa.map(s => zeile(s, '')).join('');
  } else if (a.boot_erreichbar) {
    h += '<div class="aw-gruppe">An Bord</div><div class="leerlauf">Niemand am Bordrechner angemeldet.</div>';
  } else {
    h += '<div class="aw-gruppe">An Bord</div><div class="leerlauf">Das Boot ist nicht verbunden — von dort ist nichts zu erfahren.</div>';
  }
  if (fernDa.length) {
    h += '<div class="aw-gruppe">Aus der Ferne</div>'
       + fernDa.map(s => zeile(s, 'fern')).join('');
  }
  if (ruhend.length) {
    // Nicht verschweigen, aber auch nicht als anwesend zeigen: eine
    // vergessene Anmeldung auf einem fremden Gerät soll auffallen.
    h += `<div class="aw-gruppe">Angemeldet, aber still (${ruhend.length})</div>`
       + ruhend.slice(0, 5).map(s => zeile(s, 'still')).join('');
  }
  feld.innerHTML = h;
}

async function laden() {
  const [v, d, z, a] = await Promise.all([
    hole('/api/verbindung'), hole('/api/diagnose/uebersicht'),
    hole('/api/status'), hole('/api/anwesend'),
  ]);
  if (v) _daten.verbindung = v;
  if (d) _daten.diagnose = d;
  if (z) _daten.zustand = z;
  if (a) _daten.anwesend = a;
  zeichneZustand();
  zeichneUeberblick();
  zeichneAnwesend();
  zeichneStreifen();
  zeichneZahlen();
  zeichneAusfaelle();
  zeichneEreignisse();
  if (!$('slKonten').hidden) {
    const k = await hole('/api/konten');
    if (k) { _daten.konten = k; zeichneKonten(); }
  }
  if (!$('slWartung').hidden) zeichneWartung();
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
  _streifenBauen('streifen', 'streifenAchse', _tage);
}

/** Der Streifen, einmal gebaut und zweimal benutzt: auf der Überblicksseite
 *  für den letzten Tag, auf der Verfügbarkeitsseite für den gewählten
 *  Zeitraum. Zwei Fassungen desselben Codes wären zwei Fassungen desselben
 *  Fehlers. */
function _streifenBauen(feldId, achseId, tage) {
  const v = _daten.verbindung;
  const feld = $(feldId);
  if (!v || !feld) return;
  const jetzt = Date.now() / 1000;
  const von = jetzt - tage * 86400;
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

  const achse = $(achseId);
  if (!achse) return;
  const schritte = 6;
  const kurz = tage <= 2;      // beim Tagesstreifen die Uhrzeit, sonst das Datum
  let a = '';
  for (let i = 0; i <= schritte; i++) {
    const t = new Date((von + spanne * i / schritte) * 1000);
    a += `<span>${t.toLocaleString('de-DE', kurz
      ? { hour: '2-digit', minute: '2-digit' }
      : { day: '2-digit', month: '2-digit' })}</span>`;
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
    // Angezeigt wird der Spitzname; der Anmeldename steht klein daneben. An
    // Bord ruft niemand die Steuerfrau bei ihrem Anmeldenamen — beim Anmelden
    // braucht man ihn aber, deshalb verschwindet er nicht.
    const zeigen = c.anzeigename || c.name;
    return `<div class="konto">
      <div>
        <div class="k-name ${c.gesperrt ? 'gesperrt' : ''}">${esc(zeigen)}${ich ? ' <span class="hinweis">(du)</span>' : ''}</div>
        <div class="k-rolle">${esc(r.name || c.rolle)}${c.gesperrt ? ' · gesperrt' : ''}${
          c.eingeladen ? ' · <span style="color:var(--yellow)">eingeladen, Passwort noch nicht gesetzt</span>' : ''}
          ${zeigen !== c.name ? '· meldet sich an als <b>' + esc(c.name) + '</b>' : ''}
          ${
          c.gueltig_bis ? ' · <span style="color:var(--yellow)">befristet bis '
            + esc(new Date(c.gueltig_bis * 1000).toLocaleDateString('de-DE')) + '</span>' : ''}</div>
        <div class="k-darf">${esc((r.handlungen || []).join(' · '))}</div>
      </div>
      <div class="k-tat">
        <button class="knopf stumm" onclick="kontoBearbeiten('${esc(c.name)}')">Bearbeiten</button>
        ${ich ? '' : `<button class="knopf stumm" onclick="kontoSperren('${esc(c.name)}', ${!c.gesperrt})">${c.gesperrt ? 'Entsperren' : 'Sperren'}</button>`}
        ${ich ? '' : `<button class="knopf warn" onclick="kontoLoeschen('${esc(c.name)}')">Löschen</button>`}
      </div>
    </div>`;
  }).join('') || '<div class="leerlauf">Noch kein Konto.</div>';
}

function _rollenWahl(gewaehlt) {
  const rollen = (_daten.konten && _daten.konten.rollen) || [];
  return rollen.map(r =>
    `<option value="${esc(r.schluessel)}"${r.schluessel === gewaehlt ? ' selected' : ''}>`
    + `${esc(r.name)} — ${esc(r.handlungen.join(', '))}</option>`).join('');
}

function kontoNeuOeffnen() {
  popZeigen(`
    <div class="pop-titel">Konto anlegen</div>
    <!-- EIN Name. Vorher standen hier Anmeldename UND Name der Person — für
         eine Handvoll Leute auf einem Boot ist das eine Unterscheidung ohne
         Unterschied. Der Spitzname bleibt: er ist das, was in der Anzeige
         steht, und der ist oft ein anderer als der zum Anmelden. -->
    <label class="pop-feld"><span>Name</span>
      <input id="nkName" type="text" autocapitalize="none" autocorrect="off" spellcheck="false"
             placeholder="damit meldet sich die Person an"></label>
    <label class="pop-feld"><span>Anzeigename (wenn abweichend)</span>
      <input id="nkSpitz" type="text" placeholder="optional"></label>
    <label class="pop-feld"><span>Rolle</span>
      <select id="nkRolle">${_rollenWahl('crew')}</select></label>

    <div class="pop-feld"><span>Passwort</span>
      <div class="wahl-zeile">
        <label class="wahl"><input type="radio" name="pwArt" value="einladung" checked
               onchange="_pwArtWechsel()"> Einladungslink schicken</label>
        <label class="wahl"><input type="radio" name="pwArt" value="selbst"
               onchange="_pwArtWechsel()"> selbst vergeben</label>
      </div>
    </div>
    <div class="pop-hinweis" id="nkErklaerung">Die eingeladene Person setzt ihr
      Passwort selbst — es wandert dann nie per Nachricht durch die Gegend, und
      niemand sonst kennt es. Der Link gilt sieben Tage und lässt sich nur
      einmal einlösen.</div>
    <label class="pop-feld hidden" id="nkPwFeld"><span>Passwort (mindestens 8 Zeichen)</span>
      <input id="nkPw" type="text"></label>

    <div class="pop-fehler hidden" id="nkFehler"></div>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="kontoAnlegen()">Anlegen</button>
    </div>`);
}

function _pwArtWechsel() {
  const einladen = document.querySelector('input[name="pwArt"]:checked').value === 'einladung';
  $('nkPwFeld').classList.toggle('hidden', einladen);
  $('nkErklaerung').textContent = einladen
    ? 'Die eingeladene Person setzt ihr Passwort selbst — es wandert dann nie '
      + 'per Nachricht durch die Gegend, und niemand sonst kennt es. Der Link '
      + 'gilt sieben Tage und lässt sich nur einmal einlösen.'
    : 'Du vergibst das Passwort und musst es der Person mitteilen. Das ist der '
      + 'schnellere, aber schwächere Weg — teile es nicht über einen Kanal, der '
      + 'irgendwo mitgeschrieben wird.';
}

function _einladungZeigen(name, pfad) {
  const link = location.origin + pfad;
  popZeigen(`
    <div class="pop-titel">Einladung für ${esc(name)}</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 14px">
      Schick diesen Link an die Person. Sie setzt darüber ihr eigenes Passwort
      und findet dort auch eine kurze Anleitung.</p>
    <div class="link-kasten" id="einlLink">${esc(link)}</div>
    <p style="color:var(--text3);font-size:11px;line-height:1.5;margin:12px 0 16px">
      Der Link gilt sieben Tage und lässt sich nur einmal einlösen. Er wird
      <b>nicht gespeichert</b> — geht er verloren, lade die Person neu ein.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Schließen</button>
      <button class="knopf" onclick="_linkKopieren()" id="kopierKnopf">Link kopieren</button>
    </div>`);
}

async function _linkKopieren() {
  const t = $('einlLink').textContent;
  try {
    await navigator.clipboard.writeText(t);
    $('kopierKnopf').textContent = 'Kopiert';
  } catch (_) {
    // Ohne Zwischenablage (unsicherer Kontext, alte Browser): markieren, dann
    // kann man von Hand kopieren.
    const r = document.createRange();
    r.selectNodeContents($('einlLink'));
    getSelection().removeAllRanges();
    getSelection().addRange(r);
    $('kopierKnopf').textContent = 'Markiert — jetzt kopieren';
  }
}

function kontoBearbeiten(name) {
  const c = ((_daten.konten && _daten.konten.konten) || []).find(x => x.name === name);
  if (!c) return;
  const ich = c.name === (_konto && _konto.name);
  popZeigen(`
    <div class="pop-titel">${esc(c.anzeigename || c.name)} bearbeiten</div>
    <label class="pop-feld"><span>Anzeigename (wenn abweichend)</span>
      <input id="bkSpitz" type="text" value="${esc(c.spitzname || '')}"
             placeholder="${esc(c.name)}"></label>
    <label class="pop-feld"><span>Rolle</span>
      <select id="bkRolle"${ich ? ' disabled' : ''}>${_rollenWahl(c.rolle)}</select></label>
    <label class="pop-feld"><span>Zugang befristen (leer = unbefristet)</span>
      <input id="bkBis" type="date" value="${c.gueltig_bis
        ? new Date(c.gueltig_bis * 1000).toISOString().slice(0, 10) : ''}"></label>
    ${c.rolle === 'techniker' && !c.gueltig_bis
      ? '<div class="pop-hinweis">Ein Technikerzugang sollte befristet sein — er ist der '
        + 'einzige Fremde im System, und einer, den niemand zurückzieht, bleibt für immer offen.</div>'
      : ''}
    <label class="pop-feld"><span>Neues Passwort (leer lassen = unverändert)</span>
      <input id="bkPw" type="text" placeholder="oder unten einen Link schicken"></label>
    ${ich ? '<div class="pop-hinweis">Die eigene Rolle lässt sich nicht ändern — sonst könnte man sich '
          + 'selbst aussperren, und hier ist niemand, der es wieder geradezieht.</div>' : ''}
    <div class="pop-fehler hidden" id="bkFehler"></div>
    <div class="konto-weitere">
      <button class="knopf stumm" onclick="linkErneuern('${esc(name)}')">
        ${c.eingeladen ? 'Neuen Einladungslink' : 'Link zum Passwort-Zurücksetzen'}</button>
      ${c.eingeladen
        ? `<button class="knopf stumm" onclick="einladungZurueck('${esc(name)}')">Einladung zurücknehmen</button>`
        : `<button class="knopf stumm" onclick="ueberallAbmelden('${esc(name)}')">Überall abmelden</button>`}
    </div>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="kontoSpeichern('${esc(name)}', ${ich})">Speichern</button>
    </div>`);
}

async function kontoSpeichern(name, ich) {
  const rumpf = { spitzname: $('bkSpitz').value };
  const bis = $('bkBis').value;
  // Ende des gewählten Tages, nicht sein Anfang: wer "bis 5. September" wählt,
  // meint diesen Tag noch mit.
  rumpf.laeuft_ab = bis ? (new Date(bis + 'T23:59:59').getTime() / 1000) : 0;
  if (!ich) rumpf.rolle = $('bkRolle').value;
  const pw = $('bkPw').value.trim();
  if (pw) rumpf.passwort = pw;
  const r = await fetch('/api/konten/' + encodeURIComponent(name), {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rumpf),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    const f = $('bkFehler');
    f.textContent = d.detail || 'Das hat nicht geklappt.';
    f.classList.remove('hidden');
    return;
  }
  popSchliessen();
  laden();
}

async function kontoAnlegen() {
  const name = $('nkName').value.trim(), rolle = $('nkRolle').value;
  const einladen = document.querySelector('input[name="pwArt"]:checked').value === 'einladung';
  const fehler = $('nkFehler');
  const r = await fetch('/api/konten', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, rolle, einladen,
                           passwort: einladen ? '' : $('nkPw').value,
                           spitzname: $('nkSpitz').value }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    fehler.textContent = d.detail || 'Das hat nicht geklappt.';
    fehler.classList.remove('hidden');
    return;
  }
  laden();
  if (d.einladungslink) _einladungZeigen(d.anzeigename || name, d.einladungslink);
  else popSchliessen();
}

async function linkErneuern(name) {
  const r = await fetch(`/api/konten/${encodeURIComponent(name)}/einladung`, { method: 'POST' });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { popZeigen(`<div class="pop-titel">Nicht möglich</div>
    <p style="color:var(--text2);font-size:13px">${esc(d.detail || 'Unbekannter Fehler')}</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Schließen</button></div>`); return; }
  laden();
  _einladungZeigen(name, d.einladungslink);
}

async function einladungZurueck(name) {
  await fetch(`/api/konten/${encodeURIComponent(name)}/einladung`, { method: 'DELETE' });
  popSchliessen();
  laden();
}

async function ueberallAbmelden(name) {
  const r = await fetch(`/api/konten/${encodeURIComponent(name)}/abmelden`, { method: 'POST' });
  const d = await r.json().catch(() => ({}));
  popZeigen(`<div class="pop-titel">Abgemeldet</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5">
      ${d.beendet || 0} Anmeldung${d.beendet === 1 ? '' : 'en'} beendet. Das Passwort
      gilt weiter — es muss also niemand ein neues suchen.</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Schließen</button></div>`);
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

// ── Änderungen ─────────────────────────────────────────────────────────────
// Was sich geändert hat, in der Sprache des Eigners. Die ausführliche
// Begründung steht in derselben Commit-Nachricht, aber sie gehört nicht
// hierher — hier zählt, was für IHN anders wird.

let _staende = null;

async function staendeLaden() {
  const feld = $('verlaufStaende');
  if (!_staende) feld.innerHTML = '<div class="leerlauf">wird abgefragt…</div>';
  const d = await hole('/api/system/versionen');
  if (!d) { feld.innerHTML = '<div class="leerlauf">Das Boot antwortet nicht.</div>'; return; }
  _staende = d;
  zeichneStaende();
}

function _standBlock(e, art, tat) {
  const zeit = e.zeit ? zeitpunkt(e.zeit) : '';
  // Die Bereiche sagen, ob einen die Änderung überhaupt angeht: wer nur die
  // Bordansicht benutzt, muss eine Änderung am Logbuch nicht lesen.
  const bereiche = (e.bereiche || []).map(b =>
    `<span class="ae-bereich ${esc(b.toLowerCase())}">${esc(b)}</span>`).join('');
  return `<div class="ae-stand">
    <div>
      <div class="ae-kopf">
        ${art ? `<span class="ae-marke ${art === 'neu' ? 'neu' : ''}">${art === 'neu' ? 'neu' : 'läuft'}</span>` : ''}
        ${e.version ? `<span class="ae-version">v${esc(e.version)}</span>` : ''}
        <span class="ae-zeit">${esc(zeit)}</span>
        ${bereiche}
        <span class="ae-hash">${esc(e.hash)}</span>
      </div>
      <div class="ae-titel">${esc(e.titel)}</div>
      ${e.punkte && e.punkte.length
        ? '<ul class="ae-punkte">' + e.punkte.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>'
        : '<div class="ae-ohne">Ohne Kurzfassung — nur die Überschrift.</div>'}
    </div>
    <div class="ae-tat">${tat || ''}</div>
  </div>`;
}

function zeichneStaende() {
  const d = _staende;
  if (!d) return;
  $('standJetzt').textContent = `installiert: ${d.version || ''} (${d.installiert || '—'})`;

  const bereit = d.bereit || [];
  $('bereitTafel').hidden = !bereit.length;
  if (bereit.length) {
    $('bereit').innerHTML = bereit.map(e => _standBlock(e, 'neu', '')).join('');
  }

  const verlauf = d.verlauf || [];
  $('verlaufStaende').innerHTML = verlauf.length
    ? verlauf.map((e, i) => _standBlock(e, i === 0 ? 'jetzt' : '',
        i === 0 ? '' : `<button class="knopf stumm" onclick="zurueckFragen('${esc(e.hash)}')">Hierhin zurück</button>`
      )).join('')
    : '<div class="leerlauf">Kein Verlauf abrufbar.</div>';
}

function einspielenFragen() {
  const anzahl = (_staende?.bereit || []).length;
  popZeigen(`
    <div class="pop-titel">${anzahl} Änderung${anzahl === 1 ? '' : 'en'} einspielen?</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 18px">
      Der Bordrechner holt den neuen Stand und startet die Anwendung neu. Wer
      gerade davorsitzt, sieht für etwa eine halbe Minute nichts.
      Zurückgehen ist danach jederzeit möglich.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="fernUpdateJa()">Einspielen</button>
    </div>`);
}

function zurueckFragen(hash) {
  const e = (_staende?.verlauf || []).find(x => x.hash === hash);
  popZeigen(`
    <div class="pop-titel">Auf diesen Stand zurückgehen?</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 8px">
      <b>${esc(e?.titel || hash)}</b></p>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 18px">
      Alles, was danach kam, ist anschließend nicht mehr aktiv. Die Daten auf
      dem Boot bleiben unberührt — nur der Programmstand geht zurück. Über
      "Einspielen" kommst du jederzeit wieder nach vorn.</p>
    <div class="pop-fehler hidden" id="zrFehler"></div>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf warn" onclick="zurueckJa('${esc(hash)}')">Zurückgehen</button>
    </div>`);
}

async function zurueckJa(hash) {
  const k = $('popKarte');
  k.innerHTML = '<div class="pop-titel">Läuft…</div><p style="color:var(--text2);font-size:13px">Das Boot arbeitet.</p>';
  const r = await fetch('/api/system/zurueck', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hash }),
  });
  const d = await r.json().catch(() => ({}));
  k.innerHTML = `<div class="pop-titel">${r.ok ? 'Zurückgegangen' : 'Nicht möglich'}</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5">${
      esc(r.ok ? `Der Bordrechner läuft jetzt auf "${d.titel}" und startet neu.`
               : (d.detail || 'Unbekannter Fehler'))}</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen();setTimeout(staendeLaden, 20000)">Schließen</button></div>`;
}

// ── Mitschnitt ─────────────────────────────────────────────────────────────
// Der Anlass: "die Schaltflächen sind ausgegraut" — und eine Stunde später ist
// der Fehler weg. Ohne Mitschnitt bleibt nur Raten.

let _msSuchUhr = null;

function mitschnittSuchen() {
  // Nicht bei jedem Tastendruck fragen: das läuft über die Verbindung zum Boot.
  clearTimeout(_msSuchUhr);
  _msSuchUhr = setTimeout(mitschnittLaden, 400);
}

async function mitschnittLaden() {
  const feld = $('mitschnitt');
  const art = $('msArt').value, quelle = $('msQuelle').value, suche = $('msSuche').value.trim();
  const p = new URLSearchParams({ stunden: '24', grenze: '400' });
  if (art) p.set('art', art);
  if (quelle) p.set('quelle', quelle);
  if (suche) p.set('suche', suche);
  const d = await hole('/api/debug/log?' + p);
  if (!d) { feld.innerHTML = '<div class="leerlauf">Nicht abrufbar.</div>'; return; }

  // Die Quellenauswahl aus dem füllen, was tatsächlich vorkommt — eine feste
  // Liste liefe sonst der Wirklichkeit hinterher.
  const wahl = $('msQuelle');
  const jetzige = wahl.value;
  wahl.innerHTML = '<option value="">alle Quellen</option>'
    + (d.quellen || []).map(q => `<option value="${esc(q)}"${q === jetzige ? ' selected' : ''}>${esc(q)}</option>`).join('');

  const e = d.eintraege || [];
  $('msStand').textContent = `${e.length} von ${d.gesamt} Einträgen der letzten 24 Stunden`;
  feld.innerHTML = e.length ? e.map(x => {
    const t = new Date(x.zeit * 1000);
    return `<div class="ms-zeile ${esc(x.art)}">
      <span class="ms-zeit">${t.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
      <span class="ms-quelle">${esc(x.quelle)}</span>
      <span>
        <div class="ms-text">${esc(x.text)}</div>
        ${x.daten ? `<div class="ms-daten">${esc(JSON.stringify(x.daten))}</div>` : ''}
      </span>
    </div>`;
  }).join('') : '<div class="leerlauf">Nichts gefunden — was in diesem Fall eine gute Nachricht ist.</div>';
}

// ── Fernwartung ────────────────────────────────────────────────────────────

function zeichneWartung() {
  const v = _daten.verbindung;
  const da = v && v.verbunden;
  $('wartung').innerHTML = `
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

// Denselben Service Worker wie die Bordansicht anmelden. Nicht wegen des
// Zwischenspeichers — das Logbuch braucht ohnehin frische Daten —, sondern
// wegen der Installierbarkeit: Chrome bietet "Installieren" (eigenes Fenster,
// eigenes Symbol) nur an, wenn ein Service Worker mit fetch-Handler laeuft.
// Ohne ihn bleibt es bei einer Verknuepfung, die im Browser aufgeht.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
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

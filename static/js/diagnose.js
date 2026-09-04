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

  $('spurZeit').addEventListener('click', e => {
    const k = e.target.closest('.zw-knopf');
    if (!k) return;
    _spurTage = Number(k.dataset.tage);
    $('spurZeit').querySelectorAll('.zw-knopf').forEach(b => b.classList.toggle('an', b === k));
    _spur = null;
    zeichnePosition();
    spurLaden().then(() => { if (_karte) karteZeichnen(); });
  });

  $('messZeit').addEventListener('click', e => {
    const k = e.target.closest('.zw-knopf');
    if (!k) return;
    _messStd = Number(k.dataset.std);
    $('messZeit').querySelectorAll('.zw-knopf').forEach(b => b.classList.toggle('an', b === k));
    messwerteLaden();
  });
  // Beim Drehen oder Größenändern neu zeichnen: Canvas skaliert nicht mit.
  //
  // Der resize-Horcher allein reicht seit dem Umbau nicht mehr. Die Breite des
  // Inhalts haengt jetzt nicht nur am Fenster: die Schiene faehrt im Schmalbild
  // herein und heraus, und ein Rollbalken kann auftauchen oder verschwinden.
  // Beides aendert die Breite, ohne dass das Fenster sich ruehrt — der Graph
  // bliebe dann in der alten Breite stehen und waere gestaucht oder abgeschnitten.
  // Deshalb zusaetzlich ein Beobachter am Behaelter selbst.
  let umbau;
  const neuZeichnen = () => {
    clearTimeout(umbau);
    umbau = setTimeout(() => _messDaten && zeichneMesswerte(), 250);
  };
  window.addEventListener('resize', neuZeichnen);
  if (window.ResizeObserver) {
    const behaelter = $('reihen');
    if (behaelter) {
      let ersteMeldung = true;
      new ResizeObserver(() => {
        // Die erste Meldung kommt sofort beim Beobachten und beschreibt den
        // Zustand, den wir gerade gezeichnet haben. Ein Neuzeichnen darauf
        // waere Arbeit fuer nichts.
        if (ersteMeldung) { ersteMeldung = false; return; }
        neuZeichnen();
      }).observe(behaelter);
    }
  }

  await laden();
  await dashboardVerlaufLaden();
  await messwerteLaden();
  setInterval(laden, 30000);
  // Der Verlauf fuers Dashboard aendert sich langsam — ein Punkt je neun
  // Minuten. Alle fuenf Minuten nachzuladen reicht und kostet den Pi wenig.
  setInterval(dashboardVerlaufLaden, 300000);
  // Messwerte seltener: sie ändern sich langsam und kosten mehr.
  setInterval(messwerteLaden, 120000);
}

// ── Die Schiene im Schmalbild ──────────────────────────────────────────────
// Auf dem Schreibtisch steht sie immer da und dieser Schalter ist unsichtbar.
// Schmal faehrt sie ueber den Inhalt und geht wieder zu, sobald man eine Seite
// gewaehlt hat — sonst muesste man nach jeder Wahl noch einmal danebentippen.

function schieneUmschalten() {
  document.documentElement.classList.toggle('schiene-offen');
}

function schieneZu() {
  document.documentElement.classList.remove('schiene-offen');
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
  if (name === 'verfuegbarkeit') { zeichneLegende(); zeichneStreifen(); }
  // Die Karte braucht ein sichtbares Feld, um ihre Groesse zu bestimmen —
  // auf einer verborgenen Seite ist sie null Pixel breit und bliebe grau.
  if (name === 'position') positionOeffnen();
  // Das Abfragen kostet ein git fetch am Boot — also nur beim Öffnen der
  // Seite und nicht im Dauertakt.
  if (name === 'aenderungen') staendeLaden();
  if (name === 'mitschnitt') mitschnittLaden();
  schieneZu();
  window.scrollTo(0, 0);
}

// ── Überblick: das Dashboard ───────────────────────────────────────────────
// Die Startseite beantwortet der Reihe nach die Fragen, mit denen jemand sie
// öffnet, der auf dem Boot LEBT:
//
//   Strom   — reicht er, und wie lange noch?
//   Bord    — ist es warm, reichen die Vorräte, brennt noch Licht?
//   Anlage  — läuft sie überhaupt, und sind die Zahlen oben von jetzt?
//
// Jede Karte beantwortet genau EINE davon, und zwar zuerst als Satz: aus zwei
// Metern Entfernung liest man "Reicht rund 5 Tage", nicht "90,9 %". Die Zahlen
// stehen darunter für den, der genauer hinsieht.
//
// Die Anordnung liegt fest. Eine Karte, die heute Sorgen macht, bekommt eine
// farbige Kante — sie wandert aber nicht nach oben. Eine Oberfläche, die ihre
// Reihenfolge ändert, muss man jedes Mal neu lesen.

// Ein eigener, kurzer Verlauf fürs Dashboard. Getrennt von der Messwerte-Seite:
// die hat ihren eigenen Zeitraum, den der Betrachter einstellt, und der soll
// sich nicht dadurch verstellen, dass jemand kurz auf den Überblick schaut.
let _dbVerlauf = null;

async function dashboardVerlaufLaden() {
  const bis = Date.now() / 1000;
  const von = bis - 24 * 3600;
  // 160 Punkte auf 24 Stunden: ein Punkt je neun Minuten. Für eine Linie ohne
  // Achsen ist mehr nicht sichtbar und kostet den Pi nur Arbeit.
  const d = await hole(`/api/verlauf/reihen?von=${Math.floor(von)}&bis=${Math.ceil(bis)}&punkte=160`);
  if (d) { _dbVerlauf = d; zeichneUeberblick(); }
}

/** Die Mittelwerte einer Reihe aus dem Dashboard-Verlauf, oder null. */
function _reihe(feld) {
  const p = _dbVerlauf && _dbVerlauf.punkte;
  if (!p || !p.length) return null;
  const w = p.map(x => Array.isArray(x[feld]) ? x[feld][0] : x[feld])
             .filter(v => typeof v === 'number');
  return w.length >= 3 ? w : null;
}

/** Der zuletzt gemessene Wert einer Reihe. */
function _reiheLetzt(feld) {
  const w = _reihe(feld);
  return w ? w[w.length - 1] : null;
}

/**
 * Eine Linie ohne Achsen — als SVG und nicht als Canvas.
 *
 * Ein SVG skaliert von selbst mit der Karte mit. Ein Canvas müsste bei jeder
 * Breitenänderung neu gerechnet und neu gezeichnet werden; genau dafür braucht
 * die Messwerte-Seite ihren Größenbeobachter. Für eine Linie ohne Beschriftung
 * lohnt dieser Aufwand nicht.
 */
function _funke(werte, farbe, hoch) {
  if (!werte) return '<div class="dk-funke-leer">kein Verlauf für die letzten 24 Stunden</div>';
  const min = Math.min(...werte), max = Math.max(...werte);
  const spanne = (max - min) || 1;
  const n = werte.length;
  const pkt = werte.map((v, i) =>
    `${(i / (n - 1) * 100).toFixed(2)},${(100 - (v - min) / spanne * 100).toFixed(2)}`);
  return `<svg class="dk-funke${hoch ? ' hoch' : ''}" viewBox="0 0 100 100"
      preserveAspectRatio="none" aria-hidden="true">
    <polyline points="${pkt.join(' ')}" fill="none" stroke="${farbe}" stroke-width="1.7"
      vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

// Ab wann ein Wert nicht mehr "von jetzt" ist. Der Bus meldet im Sekundentakt;
// zwei Minuten Stille sind kein Rauschen mehr, sondern eine Aussage.
const FRISCH_S = 120;

/**
 * Wie alt ein Messwert WIRKLICH ist.
 *
 * Die Falle: jede Gruppe trägt ihr eigenes `_age_s`, aber das ist die Zeit seit
 * dem letzten Bus-Rahmen AUF DEM BOOT, gemessen zum Zeitpunkt der Aufnahme.
 * Schweigt das Boot seit sechs Stunden, steht in dieser Zahl trotzdem "0,3" —
 * sie ist mit dem Schnappschuss eingefroren. Wer sie ungeprüft anzeigt,
 * behauptet Aktualität, die es nicht gibt. Deshalb kommt das Alter der
 * Übertragung obendrauf.
 *
 * `null` heißt etwas ganz anderes als "alt": das Gerät war seit dem Start nie
 * am Bus. Ladegerät und Lichtmaschine sind nicht verbaut — das ist Absicht und
 * kein Fehler, und es muss auch so dastehen.
 */
function _frische(gruppe) {
  const z = _daten.zustand;
  const g = (z && z[gruppe]) || {};
  const eigen = g._age_s !== undefined ? g._age_s
              : (g.age_s !== undefined ? g.age_s : null);
  if (eigen === null || eigen === undefined) return { art: 'aus', alt: null, text: 'nicht am Bus' };
  const versatz = (z && z.quelle === 'server' && !z.boot_verbunden) ? (z.alter_s || 0) : 0;
  const alt = eigen + versatz;
  return alt < FRISCH_S
    ? { art: 'frisch', alt, text: 'jetzt' }
    : { art: 'alt', alt, text: 'vor ' + dauer(alt) };
}

function _kopf(titel, gruppe) {
  const f = _frische(gruppe);
  const kl = f.art === 'frisch' ? '' : (f.art === 'aus' ? 'aus' : 'alt');
  return `<div class="dk-kopf"><h2>${esc(titel)}</h2>
    <span class="dk-frische"><i class="dk-punkt ${kl}"></i>${esc(f.text)}</span></div>`;
}

function _zeile(name, wert, art, grund) {
  return `<div class="dk-zeile"><span class="dk-name">${esc(name)}</span>
      <span class="dk-wert ${art || ''}">${esc(wert)}</span></div>`
    + (grund ? `<div class="dk-grund">${esc(grund)}</div>` : '');
}

/** Eine Dauer, wie man sie sagt: "rund 5 Tage", nicht "119,4 Stunden". */
function _grob(stunden) {
  if (stunden == null || !isFinite(stunden) || stunden <= 0) return null;
  if (stunden < 1.5) return `${Math.round(stunden * 60)} Minuten`;
  if (stunden < 36) return `rund ${Math.round(stunden)} Stunden`;
  const t = stunden / 24;
  return t < 10 ? `rund ${t.toFixed(t < 3 ? 1 : 0)} Tage` : `über eine Woche`;
}

// ── Die einzelnen Karten ───────────────────────────────────────────────────

function _karteBatterie(z) {
  const b = z.battery || {}, bms = z.bms || {};
  const soc = b.soc;
  const stufe = soc == null ? '' : (soc >= 50 ? 'gut' : soc >= 25 ? 'mau' : 'schlecht');

  // Zwei unabhängige Wege zur selben Frage — und das ist die eigentliche
  // Nachricht. Der Shunt rechnet aus entnommenen Ah, das BMS aus Restenergie.
  // Stimmen sie überein, kann man der Zahl glauben. Weichen sie stark ab, wird
  // das GESAGT, statt stillschweigend eine der beiden zu bevorzugen.
  //
  // `ttg` steht in MINUTEN (can_reader.py). Die Gegenprobe bestätigt es: 7210
  // ergibt 120 Stunden, und remaining_kwh / Verbrauch ergibt 119.
  //
  // Gerechnet wird gegen den VERBRAUCH, nicht gegen den Nettofluss — am Steg
  // ist netto nahe null und ergäbe absurde Zahlen. Die nützliche Frage ist:
  // wie lange komme ich hin, wenn nichts mehr nachkommt.
  const verbrauchW = (b.power != null && b.power < 0) ? -b.power : null;
  const ttgStd = b.ttg != null ? b.ttg / 60 : null;
  const bmsStd = (bms.remaining_kwh != null && verbrauchW)
    ? bms.remaining_kwh * 1000 / verbrauchW : null;

  let satz, satzArt = stufe;
  if (b.power != null && b.power > 5) {
    satz = 'Wird gerade geladen';
    satzArt = 'gut';
  } else if (ttgStd != null && bmsStd != null
             && (ttgStd / bmsStd > 2 || bmsStd / ttgStd > 2)) {
    const a = Math.min(ttgStd, bmsStd), c = Math.max(ttgStd, bmsStd);
    satz = `Reicht ${_grob(a)} bis ${_grob(c)}`;
    satzArt = 'mau';
  } else {
    const std = ttgStd != null ? ttgStd : bmsStd;
    satz = std != null ? `Reicht ${_grob(std)}` : 'Restlaufzeit nicht bestimmbar';
    if (std == null) satzArt = 'leer';
    else if (std < 24) satzArt = 'mau';
    else if (std < 8) satzArt = 'schlecht';
  }

  const uneins = (ttgStd != null && bmsStd != null
                  && (ttgStd / bmsStd > 2 || bmsStd / ttgStd > 2));
  return _kopf('Bordbatterie', 'battery')
    + `<div class="dk-satz ${satzArt}">${esc(satz)}</div>`
    + `<div class="dk-gross ${stufe}">${soc != null ? soc.toFixed(1) : '—'}<span class="dk-einheit">%</span></div>`
    + `<div class="dk-neben">Ladestand nach Shunt${bms.soc != null ? ` · BMS meldet ${Math.round(bms.soc)} %` : ''}</div>`
    + _funke(_reihe('soc'), 'var(--green)', true)
    + '<div class="dk-liste">'
    + _zeile('Bilanz', b.current != null
        ? `${b.current > 0 ? '+' : ''}${b.current.toFixed(1)} A${b.power != null ? ` · ${Math.round(Math.abs(b.power))} W` : ''}`
        : '—',
        b.current == null ? 'still' : (b.current >= 0 ? 'gut' : ''),
        b.current == null ? '' : (b.current >= 0 ? 'es kommt mehr herein als heraus' : 'es wird entnommen'))
    + _zeile('Spannung', b.voltage != null ? b.voltage.toFixed(2) + ' V' : '—')
    + _zeile('Starterbatterie', b.starter_voltage != null ? b.starter_voltage.toFixed(2) + ' V' : '—',
             b.starter_voltage != null && b.starter_voltage < 12.2 ? 'mau' : '')
    + _zeile('Zuletzt voll', b.time_since_full != null ? 'vor ' + dauer(b.time_since_full) : '—',
             b.time_since_full != null && b.time_since_full > 7 * 86400 ? 'mau' : '')
    + (uneins ? `<div class="dk-grund">Shunt und BMS sind sich über die Restlaufzeit nicht einig — die Spanne oben nennt beide.</div>` : '')
    + '</div>';
}

function _karteZufluss(z) {
  const s = z.solar || {}, o = z.orion || {}, c = z.charger || {}, i = z.inverter || {};
  const rein = (s.power || 0) + (o.output_power || 0);
  const satz = rein > 5 ? `Es kommen ${Math.round(rein)} W herein` : 'Zurzeit kommt nichts nach';
  return _kopf('Zufluss', 'solar')
    + `<div class="dk-satz ${rein > 5 ? 'gut' : 'leer'}">${esc(satz)}</div>`
    + `<div class="dk-gross">${s.power != null ? Math.round(s.power) : '—'}<span class="dk-einheit">W Solar</span></div>`
    + _funke(_reihe('solar1'), 'var(--violet)')
    + '<div class="dk-liste">'
    + _zeile('Solar heute', s.yield_today_wh != null ? Math.round(s.yield_today_wh) + ' Wh' : '—',
             '', s.charge_state_n2k ? 'Regler: ' + s.charge_state_n2k : '')
    + _zeile('Lichtmaschine', o.output_power != null ? Math.round(o.output_power) + ' W' : '—',
             (o.output_power || 0) > 5 ? 'gut' : 'still',
             o.off_reason_label && !(o.output_power > 5) ? 'aus: ' + o.off_reason_label : '')
    // Ausdrücklich NICHT "0 W": das Gerät ist nicht verbaut, und eine Null
    // sähe aus wie eine Messung.
    + _zeile('Landstrom', c._age_s == null ? 'nicht am Bus'
        : (c.power != null ? Math.round(c.power) + ' W' : '—'),
        c._age_s == null ? 'still' : '')
    + _zeile('230 V', i.state || '—', i.state === 'Aus' ? 'still' : 'gut',
             i.ac_power ? Math.round(i.ac_power) + ' W am Ausgang' : '')
    + '</div>';
}

function _karteZellen(z) {
  const bms = z.bms || {}, b = z.battery || {};
  const drift = (bms.highest_cell_v != null && bms.lowest_cell_v != null)
    ? (bms.highest_cell_v - bms.lowest_cell_v) * 1000 : null;
  // 30 mV: darunter arbeiten die Zellen zusammen, darüber läuft eine davon weg.
  // 60 mV ist der Punkt, an dem der Balancer nicht mehr hinterherkommt.
  const stufe = drift == null ? '' : (drift < 30 ? 'gut' : drift < 60 ? 'mau' : 'schlecht');
  const satz = drift == null ? 'Das BMS meldet keine Zellspannungen'
    : drift < 30 ? 'Die Zellen laufen im Gleichschritt'
    : drift < 60 ? 'Eine Zelle läuft etwas weg' : 'Eine Zelle läuft deutlich weg';
  const socDiff = (bms.soc != null && b.soc != null) ? Math.abs(bms.soc - b.soc) : null;
  return _kopf('Zellen', 'bms')
    + `<div class="dk-satz ${drift == null ? 'leer' : stufe}">${esc(satz)}</div>`
    + `<div class="dk-gross ${stufe}">${drift != null ? Math.round(drift) : '—'}<span class="dk-einheit">mV Abstand</span></div>`
    + _funke(_reihe('zelldiff'), 'var(--violet)')
    + '<div class="dk-liste">'
    + _zeile('Höchste / niedrigste',
        (bms.highest_cell_v != null && bms.lowest_cell_v != null)
          ? `${bms.highest_cell_v.toFixed(3)} / ${bms.lowest_cell_v.toFixed(3)} V` : '—',
        '', bms.lowest_cell_nr != null ? `niedrigste ist Zelle ${bms.lowest_cell_nr} von ${bms.cell_count || '?'}` : '')
    + _zeile('Zelltemperatur',
        (bms.lowest_temp != null && bms.highest_temp != null)
          ? `${bms.lowest_temp.toFixed(1)} – ${bms.highest_temp.toFixed(1)} °C` : '—',
        bms.lowest_temp != null && bms.lowest_temp < 2 ? 'mau' : '',
        bms.lowest_temp != null && bms.lowest_temp < 2 ? 'unter 2 °C darf nicht geladen werden' : '')
    // Die beiden Ladestände weichen bauartbedingt ab — das ist keine Störung,
    // sondern zwei Messverfahren. Erst ein grosser Abstand ist eine Aussage.
    + _zeile('Ladestand BMS / Shunt',
        (bms.soc != null && b.soc != null) ? `${Math.round(bms.soc)} / ${b.soc.toFixed(1)} %` : '—',
        socDiff != null && socDiff > 15 ? 'mau' : '',
        socDiff != null && socDiff > 15 ? 'die beiden messen sehr verschieden' : '')
    + _zeile('Ladezyklen', b.cycles != null ? String(b.cycles) : '—')
    + '</div>';
}

function _karteWaerme(z) {
  const h = z.heizung || {};
  const st = h.state || {};
  const raeume = st.rooms || h.rooms || [];
  const temps = raeume.map(r => r.roomTemp).filter(x => typeof x === 'number');
  const heizer = st.heater || {};
  const verbaut = heizer.availability !== 'not_wired';

  const mittel = temps.length ? temps.reduce((a, c) => a + c, 0) / temps.length : null;
  let satz, art;
  if (mittel != null) {
    // Der Satz sagt, WIE es ist; die Zahl darunter sagt, wie viel. Beides
    // dasselbe zu sagen waere verschenkter Platz.
    satz = mittel >= 20 ? 'Es ist warm an Bord'
         : mittel >= 15 ? 'Angenehm kühl'
         : mittel >= 8 ? 'Es ist kalt an Bord' : 'Nahe am Frost';
    art = mittel >= 15 ? 'gut' : mittel >= 8 ? 'mau' : 'schlecht';
  } else if (!h.reachable) {
    satz = 'Die Heizungssteuerung meldet sich nicht';
    art = 'mau';
  } else {
    satz = 'Kein Raumfühler meldet eine Temperatur';
    art = 'mau';
  }
  // Wo keine Zahl ist, steht auch kein grosser Gedankenstrich: der sieht aus
  // wie ein Messwert und ist keiner. Dann tragen die Raumzeilen die Karte.
  const gross = mittel != null
    ? `<div class="dk-gross ${art}">${mittel.toFixed(1)}<span class="dk-einheit">°C</span></div>` : '';
  let zeilen = '';
  for (const r of raeume) {
    zeilen += _zeile(r.name || 'Raum',
      typeof r.roomTemp === 'number' ? r.roomTemp.toFixed(1) + ' °C'
        : (r.conn === 'offline' ? 'meldet sich nicht' : 'kein Wert'),
      typeof r.roomTemp === 'number' ? '' : 'mau',
      r.target != null ? `Ziel ${r.target} °C` : '');
  }
  return _kopf('Wärme', 'heizung')
    + `<div class="dk-satz ${art}">${esc(satz)}</div>`
    + gross
    + `<div class="dk-neben">${esc(st.preset && st.preset.name ? 'Vorwahl: ' + st.preset.name : 'keine Vorwahl gemeldet')}</div>`
    + '<div class="dk-liste">'
    + zeilen
    + _zeile('Heizgerät', verbaut ? (heizer.state || heizer.mode || '—') : 'nicht angeschlossen',
             verbaut ? '' : 'still',
             !verbaut && heizer.availabilityText ? heizer.availabilityText : '')
    + _zeile('Steuergerät', h.reachable ? 'antwortet' : 'meldet sich nicht',
             h.reachable ? 'gut' : 'mau',
             h.info && h.info.wifi ? `WLAN ${h.info.wifi.rssi} dBm` : '')
    + '</div>';
}

function _karteVorrat(z) {
  const t = z.tanks || {};
  // 200 L je Tank, wie in der Bordansicht. Die Prozente kommen flach vom Bus;
  // die Liter rechnet die Anzeige, nicht das Boot.
  const LITER = 200;
  const tanks = [
    { name: 'Wasser', wert: t.tank1, reihe: 'tank1', farbe: 'var(--blau)' },
    { name: 'Diesel', wert: t.tank2, reihe: 'tank2', farbe: 'var(--yellow)' },
  ];
  const knapp = tanks.filter(x => typeof x.wert === 'number' && x.wert < 25);
  const satz = knapp.length
    ? (knapp.length === 1 ? `${knapp[0].name} wird knapp` : 'Wasser und Diesel werden knapp')
    : 'Die Vorräte reichen';
  let inhalt = '';
  for (const x of tanks) {
    const p = typeof x.wert === 'number' ? x.wert : null;
    const stufe = p == null ? '' : p < 12 ? 'schlecht' : p < 25 ? 'mau' : '';
    // Tempo aus dem Verlauf: 24 Stunden zurück gegen jetzt. Erst damit wird
    // aus "19,6 %" ein "reicht noch etwa vier Tage".
    const w = _reihe(x.reihe);
    let tempo = '';
    if (w && p != null && w.length > 5) {
      const proTag = w[0] - w[w.length - 1];
      if (proTag > 0.8) {
        const tage = p / proTag;
        tempo = `${proTag.toFixed(1)} Punkte in 24 Std · reicht noch ${_grob(tage * 24)}`;
      } else if (proTag < -0.8) {
        tempo = `${Math.abs(proTag).toFixed(1)} Punkte aufgefüllt`;
      } else {
        tempo = 'seit gestern unverändert';
      }
    }
    inhalt += `<div class="dk-tank">
      <div class="dk-tank-kopf"><b class="${stufe}">${p != null ? Math.round(p) : '—'} %</b>
        <span>${esc(x.name)}</span>
        <span class="dk-tank-neben">${p != null ? Math.round(p / 100 * LITER) + ' von ' + LITER + ' L' : ''}</span></div>
      <div class="dk-balken"><i class="${stufe}" style="width:${p != null ? Math.max(1, Math.min(100, p)) : 0}%"></i></div>
      ${tempo ? `<div class="dk-grund">${esc(tempo)}</div>` : ''}
    </div>`;
  }
  return _kopf('Vorräte', 'tanks')
    + `<div class="dk-satz ${knapp.length ? 'mau' : 'gut'}">${esc(satz)}</div>`
    + inhalt;
}

function _karteLicht(z) {
  const l = z.lights || {};
  const k = l.channels || [];
  const an = k.filter(x => x > 0).length;
  return _kopf('Licht', 'lights')
    + `<div class="dk-satz ${an ? '' : 'leer'}">${an ? `${an} von ${k.length} Kreisen brennen` : 'Alles aus'}</div>`
    + `<div class="dk-kreise">${k.map((x, i) =>
        `<span class="dk-kreis ${x > 0 ? 'an' : ''}" title="Kreis ${i + 1}: ${x} %">${i + 1}</span>`).join('')}</div>`
    + `<div class="dk-neben">Die Lichtkreise stehen nicht im Verlauf — hier gibt es deshalb keine Linie.</div>`;
}

function zeichneUeberblick() {
  const z = _daten.zustand;
  const v = _daten.verbindung;

  if (z) {
    const alter = z.alter_s;
    $('ueberblickStand').textContent = (z.quelle === 'server' && alter != null)
      ? `Stand: ${alter < 90 ? 'gerade eben' : 'vor ' + dauer(alter)}`
      : 'Stand: laufend';

    // Der Balken ganz oben. Er sagt das, was sonst niemand sagt: dass die
    // ganze Seite alt ist. Die Gruppenalter in den Karten könnten das nicht —
    // sie sind mit dem Schnappschuss eingefroren.
    const w = $('dbWarnung');
    if (z.quelle === 'server' && !z.boot_verbunden) {
      const lange = (alter || 0) > 3600;
      w.className = 'db-warnung' + (lange ? ' schwer' : '');
      w.innerHTML = '<span class="dbw-mark">Gespeichert</span>'
        + `<span>Das Boot ist nicht verbunden. Alle Werte auf dieser Seite sind der letzte
           übertragene Stand — ${alter != null ? 'vor ' + esc(dauer(alter)) : 'Alter unbekannt'}.
           Was sich seitdem geändert hat, steht hier nicht.</span>`;
      w.hidden = false;
    } else {
      w.hidden = true;
    }

    $('kBatterie').innerHTML = _karteBatterie(z);
    $('kZufluss').innerHTML  = _karteZufluss(z);
    $('kZellen').innerHTML   = _karteZellen(z);
    $('kWaerme').innerHTML   = _karteWaerme(z);
    $('kVorrat').innerHTML   = _karteVorrat(z);
    $('kLicht').innerHTML    = _karteLicht(z);

    // Die farbige Kante: nur da, wo wirklich etwas zu tun ist.
    const b = z.battery || {}, bms = z.bms || {}, t = z.tanks || {};
    const drift = (bms.highest_cell_v != null && bms.lowest_cell_v != null)
      ? (bms.highest_cell_v - bms.lowest_cell_v) * 1000 : null;
    const kante = (id, warnt, schlimm) => {
      const e = $(id);
      e.classList.toggle('warnt', !!warnt && !schlimm);
      e.classList.toggle('schlimm', !!schlimm);
    };
    kante('kBatterie', b.soc != null && b.soc < 50, b.soc != null && b.soc < 25);
    kante('kZellen', drift != null && drift >= 30, drift != null && drift >= 60);
    kante('kVorrat',
      [t.tank1, t.tank2].some(x => typeof x === 'number' && x < 25),
      [t.tank1, t.tank2].some(x => typeof x === 'number' && x < 12));
    const raeume = (z.heizung && z.heizung.state && z.heizung.state.rooms) || [];
    kante('kWaerme', !raeume.some(r => typeof r.roomTemp === 'number'), false);
  }

  if (v) {
    $('verbKurz').textContent = v.verbunden
      ? 'seit ' + zeitpunkt(v.seit) : 'zurzeit unterbrochen';
    _streifenBauen('streifenKurz', 'streifenKurzAchse', 1);
    const ping = _reiheLetzt('ping_ms'), down = _reiheLetzt('down_mbit');
    // Der Streifen darüber sagt, OB die Verbindung stand. Diese beiden Linien
    // sagen, wie gut sie war — ein Boot kann durchgehend "verbunden" sein und
    // trotzdem die halbe Nacht auf jede Antwort eine Sekunde warten.
    $('dbNetz').innerHTML =
      `<span>Antwortzeit <b>${ping != null ? Math.round(ping) + ' ms' : '—'}</b></span>`
      + `<span>Durchsatz <b>${down != null ? down.toFixed(1) + ' Mbit/s' : '—'}</b></span>`
      + `<span>Messwerte <b>${(_daten.diagnose && _daten.diagnose.verlauf_stand != null)
            ? _daten.diagnose.verlauf_stand.toLocaleString('de-DE') : '—'}</b></span>`;
    $('dbNetzVerlauf').innerHTML =
      '<div class="dk-zeile"><span class="dk-name">Antwortzeit, 24 Std</span></div>'
      + _funke(_reihe('ping_ms'), 'var(--yellow)')
      + '<div class="dk-zeile"><span class="dk-name">Durchsatz, 24 Std</span></div>'
      + _funke(_reihe('down_mbit'), 'var(--blau)');
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
  // Sechs statt acht, und mit eigener Rolle: sonst bestimmt allein die Laenge
  // dieser Liste die Hoehe des ganzen Bandes, und die Verbindungskarte daneben
  // steht zur Haelfte leer.
  const zeigen = eintraege.slice(0, 6);
  $('letztes').innerHTML = zeigen.length ? zeigen.map(x => `
    <div class="zeile">
      <div class="z-zeit">${esc(zeitpunkt(x.t))}</div>
      <div><div class="z-text">${x.marke ? `<span class="z-marke ${esc(x.marke)}">${esc(x.wort)}</span>` : ''}${esc(x.text)}</div></div>
      <div class="z-dauer">${esc(x.rechts)}</div>
    </div>`).join('') : '<div class="leerlauf">Nichts Auffälliges.</div>';
}

// ── Position ───────────────────────────────────────────────────────────────
// Wo das Boot liegt und wo es lag — mehr nicht. Ausdrücklich KEINE Navigation:
// kein Kurs, keine Geschwindigkeit, kein Besteck. Die Quelle ist das GNSS des
// Routers; am NMEA-2000-Bus sendet niemand eine Position (am laufenden Bus
// nachgesehen), es gibt also keine zweite Meinung.

let _spurTage = 30;
let _spur = null;
let _karte = null, _karteSpur = null, _karteBoot = null, _karteLiegen = null;
let _leaflet = null;

/**
 * Fuehrt --ueberblick-h an der Hoehe, die dem Ueberblick tatsaechlich bleibt.
 *
 * Nicht gerechnet, sondern gemessen: die Kopfzeile bricht auf schmalen
 * Schirmen um, und der Innenabstand von <main> steht in der Gestaltung, nicht
 * hier. Eine feste Zahl waere genau dann falsch, wenn es darauf ankommt.
 */
function _ueberblickHoeheFuehren() {
  const haupt = document.querySelector('.haupt');
  const kopf  = document.querySelector('header.kopf');
  const haupt_ = document.querySelector('main');
  if (!haupt || !kopf || !haupt_) return;
  let letzte = -1;
  const setzen = () => {
    const cs = getComputedStyle(haupt_);
    const innen = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
    const h = Math.round(window.innerHeight - kopf.getBoundingClientRect().height - innen);
    if (h === letzte) return;
    letzte = h;
    document.documentElement.style.setProperty('--ueberblick-h', h + 'px');
  };
  setzen();
  if (typeof ResizeObserver === 'function') {
    const beo = new ResizeObserver(setzen);
    beo.observe(kopf); beo.observe(haupt_);
  }
  window.addEventListener('resize', setzen);
  window.addEventListener('orientationchange', setzen);
}

/**
 * Die Kartenbibliothek erst holen, wenn jemand die Karte auch sehen will.
 *
 * 145 kB bei jedem Öffnen des Logbuchs mitzuschleppen wäre Verschwendung —
 * die meisten Besuche gelten dem Ladestand, nicht der Karte. Sie liegt bei
 * uns und nicht in einem Verteilnetz: die Seite soll nicht davon abhängen,
 * dass ein fremder Server erreichbar ist.
 */
function leafletLaden() {
  if (_leaflet) return _leaflet;
  _leaflet = new Promise((fertig, schiefgegangen) => {
    const stil = document.createElement('link');
    stil.rel = 'stylesheet';
    stil.href = '/static/karte/leaflet.css';
    document.head.appendChild(stil);
    const js = document.createElement('script');
    js.src = '/static/karte/leaflet.js';
    js.onload = () => fertig(window.L);
    js.onerror = () => schiefgegangen(new Error('Kartenbibliothek nicht ladbar'));
    document.head.appendChild(js);
  });
  return _leaflet;
}

/** Grad und Dezimalminuten — so steht es in jedem Logbuch und auf jedem Plotter. */
function _gradMinuten(wert, achse) {
  if (typeof wert !== 'number') return '—';
  const richtung = achse === 'lat' ? (wert >= 0 ? 'N' : 'S') : (wert >= 0 ? 'E' : 'W');
  const abs = Math.abs(wert);
  const grad = Math.floor(abs);
  const minuten = (abs - grad) * 60;
  const stellen = achse === 'lat' ? 2 : 3;
  return `${String(grad).padStart(stellen, '0')}° ${minuten.toFixed(3).padStart(6, '0')}' ${richtung}`;
}

/**
 * Der Router nennt seinen Gütewert "accuracy". Er ist KEINE Meterangabe: 0,6
 * Meter kann ein Empfänger dieser Klasse nicht, und der Wert sinkt, wenn mehr
 * Satelliten in Sicht sind — er verhält sich also wie ein HDOP. Deshalb steht
 * hier ein Wort und nicht eine erfundene Meterzahl.
 */
function _guete(wert) {
  if (typeof wert !== 'number') return { wort: '—', art: '' };
  if (wert < 1) return { wort: 'sehr gut', art: 'gut' };
  if (wert < 2) return { wort: 'gut', art: 'gut' };
  if (wert < 5) return { wort: 'brauchbar', art: '' };
  if (wert < 10) return { wort: 'mäßig', art: 'mau' };
  return { wort: 'schlecht', art: 'schlecht' };
}

async function spurLaden() {
  const d = await hole(`/api/verlauf/spur?tage=${_spurTage}`);
  if (!d) return;
  _spur = d;
  zeichnePosition();
  // Und die Karte auch. Sie wird beim Oeffnen gezeichnet, sobald die
  // Bibliothek da ist — das ist meist FRUEHER, als die Spur vom Server
  // zurueckkommt. Ohne diese Zeile blieb die Linie deshalb aus, waehrend die
  // Fusszeile darunter brav "558 Punkte" meldete.
  if (_karte) karteZeichnen();
}

function positionOeffnen() {
  zeichnePosition();
  spurLaden();
  leafletLaden().then(karteZeichnen).catch(() => {
    const f = $('karte');
    if (f) f.innerHTML = '<div class="karte-warte">Die Kartenbibliothek ließ sich '
      + 'nicht laden. Die Koordinaten daneben stimmen trotzdem.</div>';
  });
}

function karteZeichnen() {
  const L = window.L;
  const feld = $('karte');
  if (!L || !feld) return;

  if (!_karte) {
    feld.innerHTML = '';
    _karte = L.map(feld, { zoomControl: true, attributionControl: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18, className: 'karte-grund',
      attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(_karte);
    // Die Seezeichen liegen als durchsichtige Schicht darüber und bleiben
    // ungedimmt — sie sind der Grund, warum es OpenSeaMap und nicht irgendeine
    // Karte ist.
    L.tileLayer('https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png', {
      maxZoom: 18, opacity: 1,
      attribution: '&copy; <a href="https://openseamap.org">OpenSeaMap</a>',
    }).addTo(_karte);
    _karte.setView([54.0, 10.5], 8);
  }

  // Abschnitte statt einer durchgezogenen Linie. Ein Punkt mit `neu` folgt auf
  // eine Lücke im Aufschrieb — eine Linie darüber wäre gelogen, denn wo das
  // Boot dazwischen war, weiss niemand.
  const roh = (_spur && _spur.punkte) || [];
  const abschnitte = [];
  for (const p of roh) {
    if (!abschnitte.length || p.neu) abschnitte.push([]);
    abschnitte[abschnitte.length - 1].push([p.lat, p.lon]);
  }
  const punkte = roh.map(p => [p.lat, p.lon]);

  if (_karteSpur) { _karte.removeLayer(_karteSpur); _karteSpur = null; }
  const mitLinie = abschnitte.filter(a => a.length > 1);
  if (mitLinie.length) {
    _karteSpur = L.polyline(mitLinie, {
      color: '#22d3ee', weight: 2.5, opacity: .8, lineJoin: 'round',
    }).addTo(_karte);
  }

  // Wo das Boot länger lag, steht ein Ring — statt hundert Punkte übereinander.
  // Wie lange, sagt die Beschriftung beim Darauffahren.
  if (_karteLiegen) { _karte.removeLayer(_karteLiegen); _karteLiegen = null; }
  const liegen = roh.filter(p => (p.bis - p.t) >= 3600);
  if (liegen.length) {
    _karteLiegen = L.layerGroup(liegen.map(p => L.circleMarker([p.lat, p.lon], {
      // Der Ring wächst mit der Liegezeit, aber nur langsam und gedeckelt:
      // sonst verdeckt ein Winterlager die halbe Karte.
      radius: Math.min(11, 4 + Math.log10((p.bis - p.t) / 3600) * 3),
      color: '#22d3ee', weight: 1.6, opacity: .75,
      fillColor: '#22d3ee', fillOpacity: .16,
    }).bindTooltip(`lag hier ${dauer(p.bis - p.t)}<br>ab ${zeitpunkt(p.t)}`,
                   { direction: 'top' }))).addTo(_karte);
  }

  // Der aktuelle Standort: bevorzugt der gemessene aus dem Zustand, sonst der
  // letzte Punkt der Spur. Beides fehlt nur, wenn es nie einen Fix gab.
  const pos = (_daten.zustand || {}).position;
  const jetzt = (pos && typeof pos.lat === 'number')
    ? [pos.lat, pos.lon]
    : (punkte.length ? punkte[punkte.length - 1] : null);
  if (_karteBoot) { _karte.removeLayer(_karteBoot); _karteBoot = null; }
  if (jetzt) {
    _karteBoot = L.circleMarker(jetzt, {
      radius: 7, color: '#0b1120', weight: 2,
      fillColor: '#22d3ee', fillOpacity: 1,
    }).addTo(_karte).bindTooltip('Mave', { direction: 'top', offset: [0, -8] });
  }

  // Zuschnitt über ALLE Punkte, nicht über die Linie: lag das Boot die ganze
  // Zeit an einem Fleck, gibt es überhaupt keine Linie — und früher wäre die
  // Karte dann auf die Ostsee statt auf den Liegeplatz gesprungen.
  const alle = punkte.slice();
  if (jetzt) alle.push(jetzt);
  if (alle.length > 1) {
    _karte.fitBounds(L.latLngBounds(alle), { padding: [34, 34], maxZoom: 16 });
  } else if (alle.length === 1) {
    _karte.setView(alle[0], 15);
  }
  // Nach dem Einblenden stimmt die Größe erst, wenn das Feld wirklich steht.
  setTimeout(() => _karte && _karte.invalidateSize(), 60);
}

function zeichnePosition() {
  const feld = $('posJetzt');
  if (!feld) return;
  const z = _daten.zustand || {};
  const pos = z.position;
  const f = _frische('position');

  if (!pos || typeof pos.lat !== 'number') {
    feld.innerHTML = '<div class="dk-kopf"><h2>Standort</h2></div>'
      + '<div class="dk-satz leer">Zurzeit keine Position</div>'
      + '<div class="dk-neben">Der Router meldet keinen gültigen Fix. Das kann an '
      + 'der Antenne liegen, an der Umgebung — oder daran, dass der Router aus ist.</div>';
  } else {
    const g = _guete(pos.genauigkeit);
    const alt = pos.zeit ? (Date.now() / 1000 - pos.zeit) : null;
    feld.innerHTML = _kopf('Standort', 'position')
      + `<div class="pos-koord">${esc(_gradMinuten(pos.lat, 'lat'))}<br>`
      + `${esc(_gradMinuten(pos.lon, 'lon'))}</div>`
      + `<div class="dk-neben" style="margin-top:6px">${pos.lat.toFixed(6)}, ${pos.lon.toFixed(6)}</div>`
      + `<button class="pos-kopieren" onclick="positionKopieren(${pos.lat},${pos.lon})">Koordinaten kopieren</button>`
      + '<div class="dk-liste">'
      + _zeile('Satelliten', pos.satelliten != null ? String(pos.satelliten) : '—',
               pos.satelliten >= 6 ? 'gut' : pos.satelliten >= 4 ? 'mau' : 'schlecht')
      + _zeile('Güte', `${g.wort}${pos.genauigkeit != null ? ' (' + pos.genauigkeit + ')' : ''}`,
               g.art, 'kleiner ist besser — es ist kein Meterwert')
      + _zeile('Höhe', pos.hoehe_m != null ? pos.hoehe_m.toFixed(1) + ' m' : '—')
      + _zeile('Letzter Fix', alt != null ? 'vor ' + dauer(alt) : '—',
               alt != null && alt > 600 ? 'mau' : '')
      + '</div>';
  }

  const stand = $('spurStand');
  if (stand) {
    if (!_spur) {
      stand.innerHTML = '<span>Spur wird geladen…</span>';
    } else if (!_spur.punkte.length) {
      // Ehrlich statt leer: nach dem Einbau gibt es die Spur erst, wenn sie
      // aufgeschrieben wurde. Dreissig Tage brauchen dreissig Tage.
      stand.innerHTML = '<span>Für diesen Zeitraum liegt keine Position vor. '
        + 'Aufgeschrieben wird sie erst, seit der Bordrechner sie vom Router holt.</span>';
    } else {
      const von = _spur.punkte[0].t;
      const bis = _spur.punkte[_spur.punkte.length - 1].bis;
      const abschnitte = _spur.punkte.filter(p => p.neu).length + 1;
      stand.innerHTML =
        `<span>Spur <b>${_spurTage} Tage</b></span>`
        + `<span><b>${_spur.punkte.length}</b> Punkte</span>`
        + `<span>von <b>${esc(zeitpunkt(von))}</b> bis <b>${esc(zeitpunkt(bis))}</b></span>`
        + `<span>neuer Punkt ab <b>${_spur.abstand_m} m</b> Bewegung</span>`
        + (abschnitte > 1 ? `<span><b>${abschnitte}</b> Abschnitte — dazwischen wurde nichts aufgeschrieben</span>` : '');
    }
  }
}

async function positionKopieren(lat, lon) {
  const text = `${_gradMinuten(lat, 'lat')} ${_gradMinuten(lon, 'lon')}`;
  try {
    await navigator.clipboard.writeText(text);
    popZeigen(`<div class="pop-titel">Kopiert</div>
      <p style="color:var(--text2);font-size:13px;margin:0 0 16px">
        <span class="pos-koord">${esc(text)}</span></p>
      <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Gut</button></div>`);
  } catch (_) {
    // Ohne Zwischenablage (alter Browser, kein sicherer Kontext) wenigstens
    // zum Abschreiben hinlegen.
    popZeigen(`<div class="pop-titel">Koordinaten</div>
      <p style="color:var(--text2);font-size:13px;margin:0 0 16px">
        <span class="pos-koord">${esc(text)}</span></p>
      <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Gut</button></div>`);
  }
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
  if (_seite === 'position') { zeichnePosition(); if (_karte) karteZeichnen(); }
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

/** Die Legende zum Verfügbarkeitsstreifen — aus derselben Tabelle wie die
 *  Farben selbst. "verbunden" ist kein Eintrag in ART: es ist der Normalfall
 *  und keine Ausfallart, steht aber im Streifen und gehört deshalb davor. */
function zeichneLegende() {
  const feld = $('legende');
  if (!feld) return;
  feld.innerHTML = '<span class="lg"><i class="lg-farbe an"></i>verbunden</span>'
    + Object.entries(ART).map(([schluessel, a]) =>
        `<span class="lg" title="${esc(a.satz)}"><i class="lg-farbe ${esc(schluessel)}"></i>${esc(a.wort)}</span>`
      ).join('');
}

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
    case 'alarm': {
      // Der Wert gehört dazu: "Alarm: Ladestand" sagt nicht, ob es knapp oder
      // dramatisch war. Mit Wert und Schwelle liest man es in einer Zeile.
      const name = d.name || d.text || 'ohne Angabe';
      const zusatz = (d.wert != null && d.schwelle != null)
        ? ` (${d.wert} statt ${d.schwelle})`
        : (d.wert != null ? ` (${d.wert})` : '');
      return `Alarm: ${name}${zusatz}`
        + (d.schwere === 'critical' ? ' — kritisch' : '');
    }
    case 'alarm_weg': return 'Alarm vorbei';
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
      <div class="anm-marke"><span class="marke-mave">Mave</span> <span>Logbuch</span></div>
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

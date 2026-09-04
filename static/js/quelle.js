// ── Datenquelle ────────────────────────────────────────────────────────────
// Zeigt in der Kopfzeile, ob die Oberflaeche gerade DIREKT mit dem Pi an Bord
// spricht oder ueber den Server im Internet — und ob dann noch echte Daten vom
// Boot kommen oder nur eine gespeicherte Kopie.
//
// Erkannt wird das NICHT am Hostnamen. Der taeuscht: derselbe Name kann per
// DNS im Bordnetz auf den Pi und unterwegs auf den Server zeigen, und genau
// dieser Wechsel ist ja der Sinn der Sache. Massgeblich ist, was die Antwort
// selbst sagt — der Server stempelt jede Antwort mit `quelle: 'server'` und
// legt `alter_s` (Alter der Kopie) und `boot_verbunden` dazu. Der Pi liefert
// diese Felder nicht, weil er die Quelle IST.

let _quelle = { art: 'unbekannt', alter_s: null, boot: null, stand: 0 };
let _quelleTimer = null;

const _QUELLE_ART = {
  // Drei Zustaende. Der Unterschied zwischen den letzten beiden ist der
  // wichtige: ueber den Server kann das Boot ANGEMELDET sein (dann sind die
  // Werte live, sie nehmen nur einen Umweg) oder eben nicht (dann sieht man
  // einen gespeicherten Stand).
  //
  // Die Kurzform im Chip traegt nur ein Wort — sie steht in einer Kopfzeile
  // neben sieben anderen Schaltflaechen. Die vollstaendige Aussage steht im
  // Fenster darunter, und die Farbe sagt schon das Wichtigste: gruen heisst
  // direkt, gelb heisst "diese Zahlen sind nicht von jetzt".
  direkt: {
    kurz: 'Bord', ton: 'ok',
    titel: 'Direkte Verbindung zum Bordsystem',
    text: 'Dein Gerät spricht ohne Umweg mit dem Rechner an Bord. Alle Werte sind live, Schalter wirken sofort.',
    weg: ['Dein Browser', 'Bordnetz', 'Pi an Bord'],
  },
  server_live: {
    kurz: 'Server', ton: 'neutral',
    titel: 'Über den Server, Daten live vom Boot',
    text: 'Du bist nicht im Bordnetz. Die Daten nehmen den Umweg über den Server, kommen aber live vom Boot — es ist dort gerade angemeldet. Schalten geht.',
    weg: ['Dein Browser', 'Server', 'Pi an Bord'],
  },
  server_kopie: {
    kurz: 'Gespeichert', ton: 'warn',
    titel: 'Über den Server, gespeicherte Daten',
    text: 'Das Boot ist beim Server gerade nicht angemeldet. Ladestand, Tanks, '
        + 'Strom und Heizung zeigen den zuletzt übertragenen Stand. Alles, was '
        + 'der Server nur durchreicht — Internet, Wartung, Geräteliste — gibt es '
        + 'ohne Boot gar nicht und bleibt leer. Schalten ist nicht möglich.',
    weg: ['Dein Browser', 'Server', 'Boot ist weg'],
  },
  unbekannt: {
    kurz: '…', ton: 'neutral',
    titel: 'Verbindung wird ermittelt',
    text: 'Noch sind keine Daten eingetroffen.',
    weg: ['Dein Browser', '…', '…'],
  },
};

// Zwei unverwechselbare Zeichen: ein Chip fuer "das Geraet selbst", eine Wolke
// fuer "der Weg geht durchs Internet". Bewusst NICHT das Funkwellen-Symbol —
// das sitzt zwei Knoepfe weiter schon fuer die Internet-Verbindung.
const _QUELLE_SVG = {
  chip: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
      + '<rect x="7" y="7" width="10" height="10" rx="1.5"/>'
      + '<path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/></svg>',
  wolke: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
       + '<path d="M17.5 18H7a4.5 4.5 0 0 1-.6-8.96 6 6 0 0 1 11.5 1.55A3.75 3.75 0 0 1 17.5 18z"/></svg>',
};

function _quelleAlterText(s) {
  if (s == null) return null;
  if (s < 90) return 'gerade eben';
  const m = Math.round(s / 60);
  if (m < 60) return `vor ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `vor ${h} Std`;
  return `vor ${Math.floor(h / 24)} Tagen`;
}

/** Aus jeder Antwort/jedem Frame ableiten, woher die Daten kamen. */
function quelleAusDaten(d) {
  if (!d || typeof d !== 'object') return;
  let art;
  if (d.quelle === 'server') {
    // Bei WebSocket-Frames fehlt `boot_verbunden` — die kommen aber nur
    // zustande, WEIL das Boot gerade sendet. Nur ein ausdrueckliches false
    // bedeutet also "Kopie".
    art = d.boot_verbunden === false ? 'server_kopie' : 'server_live';
  } else if (d.quelle) {
    return;  // fremdes Feld, nicht raten
  } else {
    art = 'direkt';
  }
  _quelle = {
    art,
    alter_s: typeof d.alter_s === 'number' ? d.alter_s : (art === 'direkt' ? 0 : _quelle.alter_s),
    boot: typeof d.boot_verbunden === 'boolean' ? d.boot_verbunden : null,
    stand: Date.now(),
  };
  renderQuelle();
}

// Wie gut die Leitung nach draussen ist. Kommt aus connectivity.js, wo der
// Router seinen Uplink meldet. Frueher trug das ein eigenes Antennensymbol
// mit eigenem Punkt in der Kopfzeile — drei Zeichen fuer eine Frage. Jetzt
// sagt EIN Zeichen woher die Daten kommen, und sein Punkt wie gut die Leitung
// dafuer ist.
let _guete = { farbe: 'var(--text3)', wort: 'Uplink unbekannt' };

function quelleGuete(farbe, wort) {
  _guete = { farbe, wort };
  renderQuelle();
}

function renderQuelle() {
  const chip = $('quelleChip');
  if (!chip) return;
  const a = _QUELLE_ART[_quelle.art] || _QUELLE_ART.unbekannt;
  chip.className = 'quelle-chip ' + a.ton;
  chip.title = a.titel + ' — ' + _guete.wort;
  chip.setAttribute('aria-label', chip.title);
  const sym = _quelle.art === 'direkt' ? _QUELLE_SVG.chip : _QUELLE_SVG.wolke;
  chip.innerHTML = sym + '<span class="quelle-txt">' + a.kurz + '</span>'
    + `<i class="quelle-guete" style="background:${_guete.farbe}"></i>`;
  const s = _quelle.alter_s == null ? null
          : _quelle.alter_s + (Date.now() - _quelle.stand) / 1000;
  _kopieSetzen(_quelle.art === 'server_kopie', _quelleAlterText(s));
  if (!$('quellePop')?.classList.contains('hidden')) renderQuellePop();
}

function renderQuellePop() {
  const pop = $('quellePop');
  if (!pop) return;
  const a = _QUELLE_ART[_quelle.art] || _QUELLE_ART.unbekannt;
  // Das Alter waechst weiter, auch wenn nichts Neues eintrifft.
  const s = _quelle.alter_s == null ? null
          : _quelle.alter_s + (Date.now() - _quelle.stand) / 1000;
  const alter = _quelleAlterText(s);
  const letzterKnoten = _quelle.art === 'direkt' ? 2 : (_quelle.art === 'server_kopie' ? 1 : 2);
  const weg = a.weg.map((n, i) =>
    `<div class="qw-knoten${i <= letzterKnoten ? ' an' : ''}${i === 2 && _quelle.art === 'server_kopie' ? ' aus' : ''}">${n}</div>`
  ).join('<div class="qw-strich"></div>');
  pop.innerHTML =
    `<div class="qp-kopf">${a.titel}</div>` +
    `<div class="qp-guete"><i style="background:${_guete.farbe}"></i>${_guete.wort}` +
    `<button class="qp-mehr" onclick="closeQuelle();openConnectivity()">Einzelheiten</button></div>` +
    // Ueberschrift, damit die drei Kaesten als WEG lesbar sind. Ohne sie sahen
    // sie aus wie weitere Zustaende neben "Direkt" und "Server" — der erste
    // Kasten wurde fuer eine dritte Quellenart gehalten.
    `<div class="qp-wegtitel">Weg der Daten</div>` +
    `<div class="qp-weg">${weg}</div>` +
    `<div class="qp-text">${a.text}</div>` +
    (alter && _quelle.art !== 'direkt'
      ? `<div class="qp-alter">Stand der Daten: <b>${alter}</b></div>` : '');
}

function toggleQuelle(ev) {
  ev?.stopPropagation();
  const pop = $('quellePop');
  if (!pop) return;
  const zu = pop.classList.contains('hidden');
  if (zu) {
    // Nur EIN Feld darf offen stehen — sonst liegen sie uebereinander, und
    // genau das war der gemeldete Fehler. Das Menue macht es andersherum
    // ebenso (core.js, toggleBurger).
    if (typeof closeBurger === 'function') closeBurger();
    renderQuellePop();
    pop.classList.remove('hidden');
  } else pop.classList.add('hidden');
}

function closeQuelle() { $('quellePop')?.classList.add('hidden'); }

document.addEventListener('click', e => {
  const pop = $('quellePop');
  if (!pop || pop.classList.contains('hidden')) return;
  if (!pop.contains(e.target) && e.target.closest('#quelleChip') === null) closeQuelle();
});

// Ohne neue Daten altert die Kopie trotzdem — einmal pro Minute nachziehen,
// damit "vor 3 min" nicht bei "gerade eben" stehen bleibt.
function _quelleUhr() {
  clearInterval(_quelleTimer);
  _quelleTimer = setInterval(() => {
    if (!$('quellePop')?.classList.contains('hidden')) renderQuellePop();
  }, 30000);
}
_quelleUhr();

// ── Kopie-Modus ────────────────────────────────────────────────────────────
// Steht nur noch die gespeicherte Kopie zur Verfuegung, darf die Oberflaeche
// nicht so tun, als koenne sie noch etwas bewirken. Ein Schalter, der ins
// Leere greift, ist auf einem Boot schlimmer als gar keiner: man glaubt, die
// Heizung laufe jetzt.
//
// Deshalb drei Ebenen, absichtlich uebereinander:
//   1. sichtbar  — ein Banner, das sagt, dass es keine Live-Daten sind
//   2. sichtbar  — alle Schaltflaechen ausgegraut und nicht anklickbar
//   3. hart      — ein Waechter direkt an fetch(). Ebene 1 und 2 sind CSS und
//                  koennen luecken haben (neuer Schalter, Tastaturbedienung,
//                  Konsole); Ebene 3 kann es nicht, weil JEDER schreibende
//                  Aufruf durch sie hindurch muss.

let _kopieAn = null;

function _kopieSetzen(an, alterText) {
  const banner = $('kopieBanner');
  if (an !== _kopieAn) {
    document.documentElement.classList.toggle('nur-kopie', an);
    _kopieAn = an;
  }
  if (!banner) return;
  banner.classList.toggle('hidden', !an);
  if (an) {
    // Der Hinweis nennt BEIDES: dass die Werte alt sind, und dass manche
    // ueberhaupt fehlen. Der Server hebt nur auf, was ihm das Boot laufend
    // schickt — Zustand und Verlauf. Was er auf Nachfrage durchreicht
    // (Internet, Wartung, Geräteliste, Tageswerte), gibt es ohne Boot gar
    // nicht. Nur "die Daten sind alt" zu sagen waere die halbe Wahrheit, und
    // die falsche Haelfte: man wuerde die leeren Kacheln fuer einen Fehler
    // halten.
    banner.innerHTML =
      '<span class="kb-mark">Keine Live-Daten</span>'
      + '<span class="kb-txt">Das Boot ist beim Server nicht angemeldet. Ladestand, Tanks, Strom und Heizung '
      + 'zeigen den zuletzt übertragenen Stand'
      + (alterText ? ' von <b>' + alterText + '</b>' : '')
      + '. Internet, Wartung und Geräteliste werden nicht gespeichert und '
      + 'bleiben leer. Schalten ist nicht möglich.</span>';
  }
}

// Schreibende Anfragen ans Boot. /api/jserror geht an den Server selbst und
// darf durch — sonst verlieren wir im Kopie-Modus ausgerechnet die Fehler.
const _KOPIE_FREI = ['/api/jserror'];
const _SCHREIBT = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

const _fetchEcht = window.fetch.bind(window);
window.fetch = function (eingabe, optionen) {
  try {
    if (_quelle.art === 'server_kopie') {
      const m = String(optionen?.method || (eingabe && eingabe.method) || 'GET').toUpperCase();
      const url = String(typeof eingabe === 'string' ? eingabe : (eingabe?.url || ''));
      const pfad = url.startsWith('http') ? new URL(url).pathname : url.split('?')[0];
      if (_SCHREIBT.has(m) && pfad.startsWith('/api/') && !_KOPIE_FREI.includes(pfad)) {
        if (typeof _toast === 'function') _toast('Boot offline — Schalten nicht möglich');
        console.warn('[quelle] blockiert, weil nur Kopie:', m, pfad);
        return Promise.resolve(new Response(
          JSON.stringify({ error: 'Nur gespeicherte Kopie — das Boot ist nicht verbunden.' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }));
      }
    }
  } catch (e) { console.warn('[quelle] Wächter:', e); }

  // 401 heisst: die Sitzung traegt nicht mehr. Das hier ist die einzige
  // Stelle, an der das auffallen MUSS — sonst zeigt die Oberflaeche leere
  // Kacheln und sagt nicht, warum.
  return _fetchEcht(eingabe, optionen).then(antwort => {
    // 502/503/504 heisst hier fast immer: die Anlage startet gerade neu (ein
    // Update dauert eine halbe Minute). Das als "Fehler" zu zeigen ist
    // irrefuehrend — es ist ein Zustand, der von selbst vorbeigeht.
    if (antwort && (antwort.status === 502 || antwort.status === 503 || antwort.status === 504)) {
      _neustartVermuten();
    } else if (antwort && antwort.ok) {
      _neustartVorbei();
    }
    if (antwort && antwort.status === 401 && typeof _sitzungVerloren === 'function') {
      try {
        const url = String(typeof eingabe === 'string' ? eingabe : (eingabe?.url || ''));
        // Die Anmeldung selbst darf 401 sagen, ohne dass die Maske neu aufgebaut wird.
        if (!url.includes('/api/login')) _sitzungVerloren();
      } catch (_) {}
    }
    return antwort;
  });
};

// ── Veraltete Oberfläche erkennen ──────────────────────────────────────────
// Der Anlass: Die Oberfläche zeigte graue Schaltflächen und leere Kacheln, und
// zwar NUR beim Eigner — im Browser lief eine alte Fassung, während der Server
// längst eine neue auslieferte. Das ist von außen nicht zu unterscheiden von
// einem echten Fehler, und man sucht an der falschen Stelle.
//
// Erkannt wird es über den Stand, der beim Ausliefern in die Seite eingesetzt
// wurde. Die Seite kann ihn nicht selbst erfragen — dann bekäme sie ja den
// neuen und hielte sich für aktuell.

const _EIGENER_STAND = document.querySelector('meta[name="mave-stand"]')?.content || '';
let _standGemeldet = false;

async function _standPruefen() {
  if (_standGemeldet || !_EIGENER_STAND || _EIGENER_STAND === '__STAND__') return;
  try {
    const r = await fetch('/api/stand', { cache: 'no-store' });
    if (!r.ok) return;
    const d = await r.json();
    if (!d.stand || d.stand === _EIGENER_STAND) return;
    _standGemeldet = true;
    _veraltetZeigen(d.stand);
  } catch (_) { /* kein Netz: dann eben beim nächsten Mal */ }
}

function _veraltetZeigen(neuerStand) {
  const b = document.createElement('div');
  b.className = 'veraltet';
  b.innerHTML =
    '<span class="kb-mark">Veraltet</span>'
    + '<span class="kb-txt">Diese Seite läuft auf einem älteren Stand. '
    + 'Bis sie neu geladen ist, können Werte fehlen und Schaltflächen gesperrt aussehen.</span>'
    + '<button class="veraltet-knopf" onclick="_neuLadenHart()">Jetzt neu laden</button>';
  document.body.appendChild(b);
  console.warn('[stand] Seite ist', _EIGENER_STAND, '— ausgeliefert wird', neuerStand);
}

async function _neuLadenHart() {
  // Erst den Zwischenspeicher des Service Workers räumen, sonst holt das
  // Neuladen dieselbe alte Fassung wieder hervor.
  try {
    const namen = await caches.keys();
    await Promise.all(namen.map(n => caches.delete(n)));
  } catch (_) {}
  location.reload();
}

setTimeout(_standPruefen, 4000);
setInterval(_standPruefen, 120000);


// ── Neustart der Anlage ────────────────────────────────────────────────────
// Ein Update dauert eine halbe Minute, in der der Dienst weg ist und nur der
// Webserver mit 502 antwortet. Ohne Hinweis sieht das aus wie ein Ausfall, und
// man laedt neu, sucht den Fehler oder ruft an — dabei ist in dreissig
// Sekunden alles wieder da.

let _neustartSeit = 0;
let _neustartUhr = null;

function _neustartVermuten() {
  if (_neustartSeit) return;
  _neustartSeit = Date.now();
  // Erst nach ein paar Sekunden zeigen: ein einzelner Fehlschlag beim
  // Umschalten des Netzes soll keinen Hinweis aufblenden lassen.
  _neustartUhr = setTimeout(_neustartZeigen, 4000);
}

function _neustartVorbei() {
  if (!_neustartSeit) return;
  clearTimeout(_neustartUhr);
  const balken = document.getElementById('neustartHinweis');
  if (balken) {
    balken.classList.add('fertig');
    balken.querySelector('.nh-txt').textContent = 'Wieder da. Die Anzeige lädt neu…';
    // Die Oberflaeche hat waehrenddessen Antworten verpasst — nach einem
    // Update ist ausserdem eine neue Fassung da. Ein Neuladen ist beides.
    setTimeout(() => location.reload(), 1200);
  }
  _neustartSeit = 0;
}

function _neustartZeigen() {
  if (!_neustartSeit || document.getElementById('neustartHinweis')) return;
  const b = document.createElement('div');
  b.id = 'neustartHinweis';
  b.className = 'neustart-hinweis';
  b.innerHTML = '<span class="nh-kreis"></span>'
    + '<span class="nh-txt">Die Anlage startet gerade neu — meist nach einer '
    + 'Aktualisierung. Das dauert etwa eine halbe Minute.</span>';
  document.body.appendChild(b);
}

// ── Anmeldung ──────────────────────────────────────────────────────────────
// Die Maske erscheint aus zwei Gruenden: beim Start, wenn niemand angemeldet
// ist, und mitten im Betrieb, wenn eine Antwort 401 sagt (Sitzung abgelaufen,
// Konto gesperrt, Server neu aufgesetzt).
//
// Der zweite Fall ist der wichtigere und der Grund, warum die Erkennung am
// Waechter um fetch() haengt und nicht an einzelnen Aufrufen: sonst muesste
// jeder der rund fuenfzig Aufrufe selbst daran denken, und der einundfuenfzigste
// wuerde es vergessen. Dann stuende die Oberflaeche mit leeren Kacheln da,
// ohne zu sagen, warum.

let _angemeldet = null;       // null = noch nicht gefragt
let _zugangStand = null;
let _anmeldungLaeuft = false;

/** Beim Start: wer sind wir, und muessen wir uns anmelden? */
async function zugangPruefen() {
  try {
    const r = await fetch('/api/zugang', { cache: 'no-store' });
    if (!r.ok) return null;
    _zugangStand = await r.json();
    _angemeldet = !!_zugangStand.angemeldet;
    // `offen` (Pi ohne Kontenkopie) und `ersteinrichtung` (Server ohne Konto)
    // sind derselbe Zustand aus zwei Blickwinkeln: es gibt noch kein Konto.
    const ohneKonten = !!(_zugangStand.offen || _zugangStand.ersteinrichtung);
    if (!_angemeldet && !ohneKonten) anmeldungZeigen();
    else anmeldungSchliessen();
    if (ohneKonten) _hinweisOhneKonten();
    _rechteAnwenden(_zugangStand.konto);
    // Das Menü zeigt oben, wer angemeldet ist. Ändert sich das, muss es neu
    // gebaut werden — sonst steht dort der Stand von vor der Anmeldung.
    if (typeof burgerBauen === 'function') burgerBauen();
    return _zugangStand;
  } catch (_) {
    return null;   // kein Netz: die Oberflaeche laeuft mit dem, was sie hat
  }
}

function _hinweisOhneKonten() {
  const b = $('kopieBanner');
  if (!b || !b.classList.contains('hidden')) return;   // Kopie-Banner hat Vorrang
  b.classList.remove('hidden');
  b.classList.add('offen-hinweis');
  b.innerHTML = '<span class="kb-mark">Offen</span>'
    + '<span class="kb-txt">Es ist noch kein Konto angelegt — jeder im Netz kann '
    + 'diese Anlage bedienen. Das ändert sich, sobald das erste Konto besteht.</span>';
}

/** Was jemand nicht darf, wird ausgeblendet — als Bequemlichkeit, nicht als Schutz. */
function _rechteAnwenden(konto) {
  const wurzel = document.documentElement;
  const handlungen = (konto && konto.handlungen) || [];
  const flaechen = (konto && konto.oberflaechen) || [];
  // Die Entscheidung faellt am Endpunkt; hier geht es nur darum, niemandem
  // Knoepfe hinzustellen, die ihn anschliessend abweisen.
  wurzel.classList.toggle('darf-schalten', handlungen.includes('schalten'));
  wurzel.classList.toggle('darf-einstellen', handlungen.includes('einstellen'));
  wurzel.classList.toggle('darf-verwalten', handlungen.includes('verwalten'));
  wurzel.classList.toggle('darf-fernwarten', handlungen.includes('fernwarten'));
  wurzel.classList.toggle('darf-diagnose', flaechen.includes('diagnose'));
  wurzel.classList.toggle('kein-konto', !konto);
}

function anmeldungZeigen(meldung) {
  if (_angemeldet) return;
  let feld = $('anmeldung');
  if (!feld) {
    feld = document.createElement('div');
    feld.id = 'anmeldung';
    feld.className = 'anmeldung';
    document.body.appendChild(feld);
  }
  const wohin = (typeof _quelle !== 'undefined' && _quelle.art === 'direkt')
    ? 'am Boot' : 'über den Server';
  feld.innerHTML = `
    <form class="anm-karte" onsubmit="return anmelden(event)">
      <div class="anm-marke">MAVE <span>Bord Monitor</span></div>
      <div class="anm-wohin">Anmeldung ${wohin}</div>
      <label class="anm-feld">
        <span>Name</span>
        <input id="anmName" type="text" autocomplete="username" autocapitalize="none"
               autocorrect="off" spellcheck="false" required>
      </label>
      <label class="anm-feld">
        <span>Passwort</span>
        <input id="anmPw" type="password" autocomplete="current-password" required>
      </label>
      <div class="anm-fehler${meldung ? '' : ' hidden'}" id="anmFehler">${meldung || ''}</div>
      <button class="anm-knopf" type="submit" id="anmKnopf">Anmelden</button>
    </form>`;
  feld.classList.remove('hidden');
  document.documentElement.classList.add('anmeldung-offen');
  setTimeout(() => $('anmName')?.focus(), 60);
}

function anmeldungSchliessen() {
  $('anmeldung')?.classList.add('hidden');
  document.documentElement.classList.remove('anmeldung-offen');
}

async function anmelden(ev) {
  ev.preventDefault();
  if (_anmeldungLaeuft) return false;
  const name = $('anmName')?.value || '';
  const pw = $('anmPw')?.value || '';
  const knopf = $('anmKnopf'), fehler = $('anmFehler');
  _anmeldungLaeuft = true;
  if (knopf) { knopf.disabled = true; knopf.textContent = 'Einen Moment…'; }
  try {
    // Bewusst der echte fetch: der Waechter wuerde eine 401-Antwort auf die
    // Anmeldung selbst als "melde dich an" deuten und die Maske neu aufbauen —
    // mitten in der Eingabe.
    const r = await (typeof _fetchEcht === 'function' ? _fetchEcht : fetch)('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, passwort: pw }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (fehler) { fehler.textContent = d.detail || 'Anmeldung nicht möglich.'; fehler.classList.remove('hidden'); }
      $('anmPw') && ($('anmPw').value = '');
      return false;
    }
    _angemeldet = true;
    _rechteAnwenden(d);
    anmeldungSchliessen();
    // Alles neu holen: bis eben kam auf jede Frage eine Abweisung.
    if (typeof _neuLaden === 'function') _neuLaden();
    else location.reload();
    return false;
  } catch (e) {
    if (fehler) { fehler.textContent = 'Keine Verbindung.'; fehler.classList.remove('hidden'); }
    return false;
  } finally {
    _anmeldungLaeuft = false;
    if (knopf) { knopf.disabled = false; knopf.textContent = 'Anmelden'; }
  }
}

async function abmelden() {
  try { await fetch('/api/logout', { method: 'POST' }); } catch (_) {}
  _angemeldet = false;
  location.reload();
}

/** Vom fetch-Waechter gerufen, sobald irgendeine Antwort 401 sagt. */
function _sitzungVerloren() {
  if (_angemeldet === false) return;      // Maske steht schon
  _angemeldet = false;
  anmeldungZeigen('Die Sitzung ist abgelaufen. Bitte erneut anmelden.');
}

// ── Konto ──────────────────────────────────────────────────────────────────
// Das Kontomenü war ein eigenes Klappfeld neben dem Burger. Beide konnten
// gleichzeitig offen sein und lagen dann übereinander — genau der Fehler, den
// der Eigner gemeldet hat. Sein Inhalt ist jetzt die zweite ANSICHT des einen
// Menüs (core.js, _burgerKonto). Was hier bleibt, ist der Weg zum Logbuch und
// das Ändern des eigenen Passworts.

/** Wo das Logbuch liegt — von hier aus gesehen.
 *
 *  Es hat einen EIGENEN Namen und liegt immer auf dem Server: im Bordnetz zeigt
 *  `mave.…` auf den Pi, und dort gibt es kein Logbuch. Ein Verweis auf "/diagnose"
 *  liefe deshalb genau dann ins Leere, wenn man an Bord ist.
 */
function _logbuchAdresse() {
  const h = location.hostname;
  if (h.startsWith('mave.')) return location.protocol + '//' + h.replace(/^mave\./, 'logbuch.') + '/';
  if (h.startsWith('pi.mave.')) return location.protocol + '//' + h.replace(/^pi\.mave\./, 'logbuch.') + '/';
  return '/diagnose';      // eigenständige Aufstellung ohne die drei Namen
}

// Dieselben Regeln wie auf dem Server (sync/konten.py) und auf der
// Einladungsseite. Sie stehen hier ein drittes Mal, weil die Rückmeldung beim
// Tippen im Browser entstehen muss — geprüft wird trotzdem serverseitig.
const _PW_MIN = 10;

function _pwRegeln(p, name) {
  return [
    { text: `mindestens ${_PW_MIN} Zeichen`, ok: p.length >= _PW_MIN },
    { text: 'Groß- und Kleinbuchstaben', ok: /\p{Ll}/u.test(p) && /\p{Lu}/u.test(p) },
    { text: 'eine Ziffer', ok: /\p{Nd}/u.test(p) },
    { text: 'ein Sonderzeichen', ok: /[^\p{L}\p{N}]/u.test(p) },
    { text: 'nicht dein Anmeldename',
      ok: p.length > 0 && (!name || !p.toLowerCase().includes(name.toLowerCase())) },
  ];
}

function passwortAendernOeffnen() {
  let feld = $('pwDialog');
  if (!feld) {
    feld = document.createElement('div');
    feld.id = 'pwDialog';
    feld.className = 'anmeldung';
    document.body.appendChild(feld);
  }
  feld.innerHTML = `
    <form class="anm-karte" onsubmit="return passwortAendern(event)">
      <div class="anm-marke">Passwort ändern</div>
      <label class="anm-feld"><span>Bisheriges Passwort</span>
        <input id="pwAlt" type="password" autocomplete="current-password" required></label>
      <label class="anm-feld"><span>Neues Passwort</span>
        <input id="pwNeu" type="password" autocomplete="new-password" required
               oninput="_pwRegelnZeigen()"></label>
      <ul class="pw-regeln" id="pwRegeln"></ul>
      <label class="anm-feld"><span>Noch einmal</span>
        <input id="pwNeu2" type="password" autocomplete="new-password" required
               oninput="_pwRegelnZeigen()"></label>
      <div class="anm-fehler hidden" id="pwFehler"></div>
      <button class="anm-knopf" type="submit" id="pwKnopf" disabled>Ändern</button>
      <button type="button" class="anm-knopf" style="background:none;color:var(--text2);
              border:1px solid var(--border);margin-top:2px"
              onclick="$('pwDialog').remove()">Abbrechen</button>
    </form>`;
  _pwRegelnZeigen();
  setTimeout(() => $('pwAlt')?.focus(), 60);
}

function _pwRegelnZeigen() {
  const p = $('pwNeu')?.value || '', p2 = $('pwNeu2')?.value || '';
  const name = (_zugangStand?.konto?.name) || '';
  const liste = _pwRegeln(p, name);
  $('pwRegeln').innerHTML = liste.map(r =>
    `<li class="${r.ok ? 'ok' : ''}${r.hinweis ? ' hinweis' : ''}">
       <span class="regel-punkt">${r.ok ? '&#10003;' : '&#183;'}</span>${r.text}</li>`).join('')
    + (p2 ? `<li class="${p === p2 ? 'ok' : ''}"><span class="regel-punkt">${
        p === p2 ? '&#10003;' : '&#183;'}</span>beide Eingaben gleich</li>` : '');
  const fertig = liste.every(r => r.ok || r.hinweis) && p === p2 && p2.length > 0;
  if ($('pwKnopf')) $('pwKnopf').disabled = !fertig;
}

async function passwortAendern(ev) {
  ev.preventDefault();
  const fehler = $('pwFehler'), knopf = $('pwKnopf');
  knopf.disabled = true; knopf.textContent = 'Einen Moment…';
  try {
    const r = await fetch('/api/mein/passwort', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ altes: $('pwAlt').value, neues: $('pwNeu').value }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      fehler.textContent = d.detail || 'Das hat nicht geklappt.';
      fehler.classList.remove('hidden');
      return false;
    }
    // Alle anderen Sitzungen sind beendet; diese läuft mit dem neuen Cookie
    // weiter. Ein Neuladen macht das sichtbar und stellt sicher, dass nichts
    // Altes hängen bleibt.
    $('pwDialog').innerHTML = '<div class="anm-karte" style="text-align:center">'
      + '<div class="anm-marke">Geändert</div>'
      + '<div class="anm-wohin" style="border:none;padding:0">Alle anderen Anmeldungen '
      + 'wurden dabei beendet.</div></div>';
    setTimeout(() => location.reload(), 1600);
  } catch (_) {
    fehler.textContent = 'Keine Verbindung.';
    fehler.classList.remove('hidden');
  } finally {
    knopf.disabled = false; knopf.textContent = 'Ändern';
  }
  return false;
}

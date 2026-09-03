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

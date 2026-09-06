// ── Gäste-WLAN: ein QR-Code zum Scannen ─────────────────────────────────────
//
// Wer an Bord kommt, fragt als Erstes nach dem WLAN. Statt das Passwort
// vorzulesen, zeigt das Wandtablet einen QR-Code; iOS-Kamera und Android
// bieten daraufhin von selbst „Netzwerk beitreten" an.
//
// Ein frei schwebendes Popup und keine eigene Seite (Eignerwunsch): man tippt
// es auf, hält jemandem das Tablet hin, tippt daneben, und es ist wieder weg.
// Eine Seite müsste man verlassen.
//
// Der Code kommt fertig vom Pi (`/api/wlan/qr.svg`) und wird nicht im Browser
// gerechnet — ein falscher QR scheitert still, und dann sucht man den Fehler
// beim WLAN statt beim Code.

let _wlanStand = null;      // {ssid, passwort, art, eingerichtet}
let _wlanNurAnBord = false; // der Server kennt die Endpunkte nicht

/** Wer den Eintrag sehen soll: NUR das Wandtablet.
 *
 *  Das ist eine Frage der PLATZIERUNG, nicht der Sicherheit: das Passwort
 *  hängt ohnehin für jeden lesbar an der Wand im Salon, und der Endpunkt gibt
 *  es jedem angemeldeten Konto. Es soll nur nicht auf jedem Telefon im Menü
 *  stehen, wo es niemand braucht.
 *
 *  Erkannt wird die installierte WANDFASSUNG und nicht die Rolle. Der Grund
 *  ist unangenehm einfach: ein Kiosk-Konto gibt es auf diesem Boot (noch)
 *  nicht — das Tablet meldet sich mit einem Eignerkonto an. Über die Rolle
 *  wäre der Eintrag also entweder überall oder nirgends. Sobald das Tablet
 *  sein eigenes Konto hat, greift die zweite Bedingung von selbst.
 *
 *  Der Eigner kommt trotzdem heran: in den Einstellungen steht neben den
 *  Feldern ein Knopf „Vorschau".
 */
function wlanImMenue() {
  if (typeof _wandVollbild === 'function' && _wandVollbild()) return true;
  const k = (typeof _zugangStand !== 'undefined' && _zugangStand)
    ? _zugangStand.konto : null;
  return !!k && k.rolle === 'kiosk';
}

async function wlanLaden() {
  try {
    const r = await fetch('/api/wlan');
    // 404 heisst hier nicht „kaputt", sondern „falsche Seite": das Gaeste-WLAN
    // liegt auf dem Pi. Auf dem Server gibt es die Endpunkte bewusst nicht —
    // ein Gaestepasswort hat in einem Rechenzentrum nichts verloren.
    _wlanNurAnBord = (r.status === 404);
    if (!r.ok) return null;
    _wlanStand = await r.json();
    return _wlanStand;
  } catch (_) {
    return null;                       // ohne Netz eben nicht
  }
}

async function wlanPopAuf() {
  const pop = document.getElementById('wlanPop');
  if (!pop) return;
  pop.classList.remove('hidden');
  document.body.classList.add('wlan-offen');
  const stand = await wlanLaden();
  const setzen = (id, wert) => {
    const el = document.getElementById(id);
    if (el) el.textContent = wert;
  };
  const bild = document.getElementById('wlanQr');
  const leer = document.getElementById('wlanLeer');
  if (!stand || !stand.eingerichtet) {
    if (bild) bild.removeAttribute('src');
    if (leer) {
      leer.hidden = false;
      leer.textContent = _wlanNurAnBord
        ? 'Das Gäste-WLAN steht nur an Bord zur Verfügung — dort liegt es, '
          + 'und dort hängt auch das Tablet.'
        : 'Noch nicht eingerichtet — Einstellungen › Netzwerk › Gäste-WLAN.';
    }
    setzen('wlanSsid', '--');
    setzen('wlanPw', '--');
    return;
  }
  if (leer) leer.hidden = true;
  setzen('wlanSsid', stand.ssid);
  setzen('wlanPw', stand.art === 'nopass' ? 'ohne Passwort' : stand.passwort);
  // Mit Zeitstempel: sonst zeigt der Browser nach einer Änderung den alten
  // Code weiter, und der führt in ein Netz, das es nicht mehr gibt.
  if (bild) bild.src = '/api/wlan/qr.svg?t=' + Date.now();
}

function wlanPopZu(ereignis) {
  // Ein Tipp auf die Karte selbst schließt nicht — sonst geht das Popup zu,
  // während jemand den QR scannt und dabei das Tablet berührt.
  if (ereignis && ereignis.target && ereignis.target.closest
      && ereignis.target.closest('.wlan-karte')) return;
  const pop = document.getElementById('wlanPop');
  if (pop) pop.classList.add('hidden');
  document.body.classList.remove('wlan-offen');
}

// ── Einstellungen ───────────────────────────────────────────────────────────

async function wlanEinstZeichnen() {
  const stand = await wlanLaden();
  const w = stand || { ssid: '', passwort: '', art: 'WPA', versteckt: false };
  const setzen = (id, wert) => {
    const el = document.getElementById(id);
    if (el) el.value = wert;
  };
  setzen('sWlanSsid', w.ssid);
  setzen('sWlanPw', w.passwort);
  wlanRandhinweis();
  const art = document.getElementById('sWlanArt');
  if (art) art.value = w.art;
  const versteckt = document.getElementById('sWlanVersteckt');
  if (versteckt) versteckt.checked = !!w.versteckt;
}

/** Leerzeichen am Rand sichtbar machen.
 *
 *  Sie sind erlaubt und manchmal Absicht — aber man sieht sie nicht, und ein
 *  versehentlich mitkopiertes Leerzeichen ergibt einen QR-Code, der gueltig
 *  ist und trotzdem in kein Netz fuehrt. Also weder wegschneiden noch
 *  verschweigen: hinschreiben.
 */
function wlanRandhinweis() {
  const feld = document.getElementById('sWlanSsid');
  const hinweis = document.getElementById('sWlanRand');
  if (!feld || !hinweis) return;
  const v = feld.value;
  const vorn = v !== v.replace(/^\s+/, '');
  const hinten = v !== v.replace(/\s+$/, '');
  hinweis.textContent = (vorn && hinten)
    ? 'Der Name beginnt und endet mit einem Leerzeichen.'
    : vorn ? 'Der Name beginnt mit einem Leerzeichen.'
    : hinten ? 'Der Name endet mit einem Leerzeichen.' : '';
  hinweis.hidden = !hinweis.textContent;
}

async function wlanSpeichern() {
  const wert = id => (document.getElementById(id) || {}).value || '';
  const meldung = document.getElementById('sWlanMeldung');
  const koerper = {
    // Kein trim: siehe put_wlan in main.py — der Name darf auf ein Leerzeichen
    // enden, und wegzuschneiden hiesse, ihn ohne Ansage zu aendern.
    ssid: wert('sWlanSsid'),
    passwort: wert('sWlanPw'),
    art: wert('sWlanArt') || 'WPA',
    versteckt: !!(document.getElementById('sWlanVersteckt') || {}).checked,
  };
  try {
    const r = await fetch('/api/wlan', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(koerper),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (meldung) meldung.textContent = d.detail || 'Speichern fehlgeschlagen';
      return;
    }
    _wlanStand = d;
    if (meldung) meldung.textContent = koerper.ssid ? 'Gespeichert.' : 'Geleert.';
    setTimeout(() => { if (meldung) meldung.textContent = ''; }, 2500);
    wlanRandhinweis();
  } catch (_) {
    if (meldung) meldung.textContent = 'Keine Verbindung';
  }
}

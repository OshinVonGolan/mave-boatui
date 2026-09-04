// ── Wandbetrieb: Bildschirm anlassen und Nachtmodus ────────────────────────
// Zwei Dinge, die dasselbe Gerät betreffen: den Bildschirm, auf den man gerade
// schaut. Beide gehören in die Kopfzeile, nicht in die Einstellungen — nachts
// sucht niemand in einem Menü, und ein Bildschirm, der gerade eingeschlafen
// ist, wird nicht durch drei Klicks wachgehalten.
//
// Beide Schalter gelten NUR für dieses Gerät. Das Wandtablet soll wach bleiben
// und nachts rot werden, ein Telefon in der Hosentasche nicht.
//
// Diese Datei steht in BEIDEN Bündeln — Bordansicht und Logbuch. Deshalb setzt
// sie nichts voraus, was nur eine der beiden Seiten mitbringt: jeder Zugriff
// nach draußen ist abgesichert, und den Ort nimmt sie entgegen, statt ihn sich
// zu holen.

const _WAND_KEY = 'mave_wandbetrieb';
const _WAND_VORGABE = {
  wachhalten: false,      // Bildschirm anlassen
  nacht: 'aus',           // 'aus' | 'an' | 'auto'
};

let _wand = null;
let _wachSperre = null;
let _nachtAktiv = false;
let _nachtUhr = null;
let _wandOrt = null;      // {lat, lon} — allein für die Sonnenzeiten

function _wandLaden() {
  try { _wand = { ..._WAND_VORGABE, ...(JSON.parse(localStorage.getItem(_WAND_KEY)) || {}) }; }
  catch (_) { _wand = { ..._WAND_VORGABE }; }
  return _wand;
}
function _wandSichern() {
  try { localStorage.setItem(_WAND_KEY, JSON.stringify(_wand)); } catch (_) {}
}
function _wandSagen(text) {
  if (typeof _toast === 'function') _toast(text);
}

/**
 * Der Ort — nur zum Rechnen.
 *
 * Die Bordansicht bekommt ihn im Datenstrom mitgeliefert, das Logbuch reicht
 * ihn von der Karte herein. Angezeigt wird er hier nirgends: er dient allein
 * dem Sonnenuntergang.
 */
function wandOrtSetzen(lat, lon) {
  if (typeof lat !== 'number' || typeof lon !== 'number') return;
  _wandOrt = { lat, lon };
  if (_wand && _wand.nacht === 'auto') _nachtPruefen();
}

function _ortHolen() {
  if (_wandOrt) return _wandOrt;
  const p = (typeof _lastData !== 'undefined' && _lastData) ? _lastData.position : null;
  return (p && typeof p.lat === 'number') ? p : null;
}

/**
 * Ob dieses Gerät fest montiert ist — soweit sich das sagen lässt.
 *
 * Es lässt sich NICHT sagen. Am laufenden System gemessen: `userAgentData.mobile`
 * meldet auf Telefon UND Tablet `false`, Zeigerart und Berührpunkte sind bei
 * beiden gleich. Ein Tablet ist vom Telefon nicht zu unterscheiden.
 *
 * Was sich unterscheidet, ist die Breite — und das ist zufällig genau das,
 * worauf es ankommt: der Schalter soll dort stehen, wo ein Gerät groß genug ist
 * und fest hängt. Dieselbe Schwelle wie bei der Zwei-Finger-Geste. Wer es
 * anders will, stellt es in „Anzeige" um; die Vermutung hat nicht das letzte
 * Wort.
 */
function _istWandgeraet() {
  const dsp = (typeof _dsp !== 'undefined') ? _dsp : null;
  if (dsp && dsp.wandschalter === 'immer') return true;
  if (dsp && dsp.wandschalter === 'nie') return false;
  if (typeof _dspActiveProfile === 'function' && _dspActiveProfile() === 'kiosk') return true;
  const schwelle = (typeof _GESTE_MIN_BREITE !== 'undefined') ? _GESTE_MIN_BREITE : 480;
  return matchMedia('(pointer: coarse)').matches && window.innerWidth >= schwelle;
}

// ── Bildschirm anlassen ────────────────────────────────────────────────────

async function _wachHolen() {
  if (!('wakeLock' in navigator)) return false;
  try {
    _wachSperre = await navigator.wakeLock.request('screen');
    // Der Browser gibt die Sperre von sich aus frei, wenn die Seite in den
    // Hintergrund geht. Ohne diesen Horcher stünde der Schalter danach auf
    // „an", während nichts mehr gehalten wird — die schlimmste Sorte Anzeige.
    _wachSperre.addEventListener('release', () => { _wachSperre = null; _wandKnopfSetzen(); });
    return true;
  } catch (e) {
    console.debug('Wach halten nicht möglich:', e && e.message);
    _wachSperre = null;
    return false;
  }
}

async function _wachLoesen() {
  try { if (_wachSperre) await _wachSperre.release(); } catch (_) {}
  _wachSperre = null;
}

async function wachUmschalten() {
  if (!_wand) _wandLaden();
  _wand.wachhalten = !_wand.wachhalten;
  _wandSichern();
  if (_wand.wachhalten) {
    const ok = await _wachHolen();
    if (ok) {
      _wandSagen('Bildschirm bleibt an');
    } else {
      // Nicht so tun, als hätte es geklappt. Android kann die Sperre im
      // strengen Energiesparmodus schlicht verweigern.
      _wand.wachhalten = false; _wandSichern();
      _wandSagen(('wakeLock' in navigator)
        ? 'Das Gerät lässt den Bildschirm gerade nicht anbleiben'
        : 'Dieser Browser kann den Bildschirm nicht anlassen');
    }
  } else {
    await _wachLoesen();
    _wandSagen('Bildschirm darf wieder ausgehen');
  }
  _wandKnopfSetzen();
}

// Zurückholen, sobald die Seite wieder sichtbar ist. Die Sperre geht bei JEDEM
// Wechsel in den Hintergrund verloren — ohne das schläft das Tablet nach dem
// ersten Wegdrücken wieder ein, und der Schalter wirkt kaputt.
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState !== 'visible') return;
  if (!_wand) _wandLaden();
  if (_wand.wachhalten && !_wachSperre) await _wachHolen();
  _wandKnopfSetzen();
  if (_wand.nacht === 'auto') _nachtPruefen();
});

// ── Nachtmodus ─────────────────────────────────────────────────────────────
// Rot auf fast Schwarz, wie auf einem Kartenplotter. Die Stäbchen im Auge —
// zuständig fürs Sehen bei Nacht — sprechen auf langwelliges Rot kaum an, die
// Dunkeladaption bleibt also erhalten. Der übliche Akzent Cyan liegt fast im
// Empfindlichkeitsmaximum der Stäbchen und ist damit das Schlechteste, was
// nachts auf einem Bildschirm stehen kann.

function nachtSetzen(an) {
  _nachtAktiv = !!an;
  document.documentElement.classList.toggle('nachtmodus', _nachtAktiv);
  _wandKnopfSetzen();
}

function nachtUmschalten() {
  if (!_wand) _wandLaden();
  // Von Hand geschaltet heißt: von Hand geschaltet. Die Automatik tritt
  // zurück, bis sie ausdrücklich wieder gewählt wird — sonst dreht sie die
  // Wahl beim nächsten Takt wieder um, und man hält den Schalter für kaputt.
  _wand.nacht = _nachtAktiv ? 'aus' : 'an';
  _wandSichern();
  nachtSetzen(_wand.nacht === 'an');
  _wandSagen(_nachtAktiv ? 'Nachtmodus an' : 'Nachtmodus aus');
}

/** Automatik an oder aus. Aus heißt: der zuletzt gesehene Stand bleibt stehen. */
function nachtAutomatik(an) {
  if (!_wand) _wandLaden();
  _wand.nacht = an ? 'auto' : (_nachtAktiv ? 'an' : 'aus');
  _wandSichern();
  if (an) _nachtPruefen(); else _wandKnopfSetzen();
}

/**
 * Sonnenauf- und -untergang für einen Ort, in Stunden UTC.
 *
 * Das übliche Verfahren aus dem Nautical Almanac: mittlere Anomalie, wahre
 * Länge, Rektaszension, Deklination, Stundenwinkel. Genauigkeit gut eine
 * Minute — mehr braucht niemand, der wissen will, ob er das Rotlicht anmachen
 * soll. Gibt null zurück, wo die Sonne gar nicht auf- oder untergeht.
 */
function _sonnenzeiten(lat, lon, datum) {
  const rad = Math.PI / 180;
  const tag = Math.floor((datum - Date.UTC(datum.getUTCFullYear(), 0, 0)) / 86400000);
  const zenit = 90.833 * rad;                 // Sonnenmitte plus Refraktion
  const erg = {};
  for (const [name, richtung] of [['auf', 1], ['unter', -1]]) {
    const t = tag + ((6 - richtung * 6) / 24 - lon / 360);
    const M = (0.9856 * t - 3.289) * rad;                       // mittlere Anomalie
    let L = M + (1.916 * rad) * Math.sin(M) + (0.020 * rad) * Math.sin(2 * M) + 282.634 * rad;
    L = ((L % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
    let RA = Math.atan(0.91764 * Math.tan(L));
    RA = ((RA % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
    // Die Rektaszension muss im selben Quadranten liegen wie die Länge —
    // der Arkustangens verliert diese Zuordnung.
    RA += (Math.floor(L / (Math.PI / 2)) - Math.floor(RA / (Math.PI / 2))) * (Math.PI / 2);
    RA /= 15 * rad;                                             // in Stunden
    const sinDek = 0.39782 * Math.sin(L);
    const cosDek = Math.cos(Math.asin(sinDek));
    const cosH = (Math.cos(zenit) - sinDek * Math.sin(lat * rad)) / (cosDek * Math.cos(lat * rad));
    if (cosH > 1 || cosH < -1) return null;    // Polarnacht oder Mitternachtssonne
    let H = Math.acos(cosH) / (15 * rad);
    if (richtung === 1) H = 24 - H;
    let UT = H + RA - 0.06571 * t - 6.622 - lon / 15;
    erg[name] = ((UT % 24) + 24) % 24;
  }
  return erg;
}

/** Notnagel ohne Ort: eine schlichte Uhrzeit. Ehrlicher als eine erfundene Dämmerung. */
function _dunkelNachUhr(jetzt) {
  const h = jetzt.getHours();
  return h >= 21 || h < 6;
}

/** Ist es an DIESEM Ort gerade dunkel? */
function _istDunkel(lat, lon, jetzt) {
  jetzt = jetzt || new Date();
  const z = _sonnenzeiten(lat, lon, jetzt);
  if (!z) return _dunkelNachUhr(jetzt);
  const utc = jetzt.getUTCHours() + jetzt.getUTCMinutes() / 60;
  // Liegt der Untergang nach dem Aufgang (der Normalfall), ist es dunkel
  // außerhalb des Tagfensters — sonst innerhalb der Lücke dazwischen.
  return z.unter > z.auf ? (utc >= z.unter || utc < z.auf)
                         : (utc >= z.unter && utc < z.auf);
}

function _nachtPruefen() {
  if (!_wand) _wandLaden();
  if (_wand.nacht !== 'auto') return;
  const ort = _ortHolen();
  nachtSetzen(ort ? _istDunkel(ort.lat, ort.lon) : _dunkelNachUhr(new Date()));
}

// ── Die Knöpfe in der Kopfzeile ────────────────────────────────────────────

const _WAND_SVG = {
  wach: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  mond: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
};

function _wandKnopf(name, klasse, titel, pfad) {
  return `<button class="icon-btn wand-knopf ${klasse}" onclick="${name}()"
          title="${titel}" aria-label="${titel}" aria-pressed="${klasse.includes('an')}">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${pfad}</svg>
  </button>`;
}

function _wandKnopfSetzen() {
  if (!_wand) _wandLaden();
  const feld = document.getElementById('wandKnoepfe');
  if (!feld) return;
  let html = '';

  // Wach halten: nur da, wo ein Gerät fest hängt. Auf dem Telefon in der
  // Tasche wäre der Schalter bestenfalls sinnlos.
  if (_istWandgeraet()) {
    // Der Knopf zeigt, ob die Sperre WIRKLICH steht — nicht nur, ob der
    // Schalter an ist. Genau dieser Unterschied führt sonst zu „der Schalter
    // ist an, aber das Ding schläft trotzdem ein".
    const steht = !!_wachSperre, gewollt = !!_wand.wachhalten;
    html += _wandKnopf('wachUmschalten',
      steht ? 'an' : (gewollt ? 'wartet' : ''),
      steht ? 'Bildschirm bleibt an'
        : gewollt ? 'Bildschirm soll anbleiben — die Sperre steht gerade nicht'
        : 'Bildschirm anlassen',
      _WAND_SVG.wach);
  }

  // Der Nachtmodus gilt überall: nachts sitzt man genauso am Laptop am
  // Kartentisch wie vor dem Tablet.
  html += _wandKnopf('nachtUmschalten', _nachtAktiv ? 'an' : '',
    _wand.nacht === 'auto'
      ? `Nachtmodus — automatisch, gerade ${_nachtAktiv ? 'an' : 'aus'}`
      : (_nachtAktiv ? 'Nachtmodus an' : 'Nachtmodus aus'),
    _WAND_SVG.mond);

  feld.innerHTML = html;
}

function wandStart() {
  _wandLaden();
  if (_wand.nacht === 'an') nachtSetzen(true);
  else if (_wand.nacht === 'auto') _nachtPruefen();
  // Alle fünf Minuten nachsehen. Der Sonnenuntergang wandert um Minuten am
  // Tag — häufiger nachzurechnen wäre Arbeit ohne Ergebnis.
  clearInterval(_nachtUhr);
  _nachtUhr = setInterval(_nachtPruefen, 300000);
  if (_wand.wachhalten) _wachHolen();
  _wandKnopfSetzen();
  window.addEventListener('resize', _wandKnopfSetzen);
}

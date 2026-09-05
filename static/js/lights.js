// ── Channel bars ───────────────────────────────────────────────────────────

const VISIBLE_CH = [0, 1, 2, 3, 8]; // PWM 1–4 + relay

// Wie schmal ein Balken hoechstens sein darf, damit senkrechte Schrift darin
// ueberhaupt Sinn ergibt. Darunter ist die Zeile schmaler als die Schrift hoch
// ist.
const _CH_NAME_MIN_BREITE_PX = 22;

function buildChannelBars() {
  const row = $('channelsRow'), lbl = $('channelsLabel');
  row.innerHTML = '';
  if (lbl) lbl.innerHTML = '';     // die Nummernzeile ist entfallen, s.u.
  VISIBLE_CH.forEach(i => {
    const wrap = document.createElement('div');
    wrap.className = 'ch-bar-wrap' + (i === 8 ? ' ist-relais' : '');
    wrap.dataset.ch = i;
    const bar = document.createElement('div');
    bar.className = 'ch-bar' + (i === 8 ? ' relay' : '');
    bar.id = `chBar${i}`; bar.style.height = '0%';
    // Der Name steht IM Balken, nicht darunter. Vorher stand dort die
    // Kanalnummer — "3" sagt niemandem, welches Licht das ist. Er liegt ueber
    // der Fuellung und bleibt damit bei jedem Stand lesbar.
    const name = document.createElement('span');
    name.className = 'ch-name'; name.id = `chName${i}`;
    wrap.append(bar, name);
    row.appendChild(wrap);
  });
  _chGesteBinden(row);
  chNamenPassend();
}
buildChannelBars();

/**
 * Die Namen so schreiben, dass sie in ihren Balken PASSEN.
 *
 * Nicht auf eine Zeichenzahl raten: wie viel hineingeht, haengt an der Breite
 * der Kachel, der Zahl der Kanaele und der Schriftgroesse — und die Kachel
 * aendert ihre Groesse (Spaltenwahl, Drehen des Tablets, Kiosk-Raster). Also
 * hinschreiben, nachmessen, und wenn es nicht passt, kuerzen.
 *
 * Drei Buchstaben sind die letzte Stufe vor gar nichts: "Kom" laesst sich noch
 * zuordnen, ein abgeschnittenes "Kombüs" sieht nach Fehler aus.
 */
/**
 * Die Namen dorthin schreiben, wo sie GANZ hinpassen.
 *
 * Zuerst der Versuch im Balken, senkrecht — das sieht gut aus, solange der
 * ganze Name hineingeht. Muesste auch nur EINER davon auf drei Buchstaben
 * gekuerzt werden, wandern ALLE unter die Balken: dort stehen sie waagerecht
 * und werden vom Stylesheet mit Auslassungszeichen gekuerzt, was sich normal
 * liest. Am Telefon ist die Kachel breit und flach, dort trifft das fast immer
 * zu; auf dem Wandtablet sind die Balken hoch, dort steht der Name innen.
 *
 * Alle oder keiner — eine Reihe, in der zwei Namen innen und drei darunter
 * stehen, sieht nach Fehler aus.
 *
 * Gemessen statt geraten: wie viel hineingeht, haengt an Kachelbreite,
 * Kanalzahl und Schriftgroesse, und die Kachel aendert ihre Groesse
 * (Spaltenwahl, Drehen, Kiosk-Raster).
 */
function chNamenPassend() {
  const reihe = $('channelsRow');
  const unten = $('channelsLabel');
  if (!reihe) return;

  // Erst hineinschreiben und nachmessen.
  let innen = true;
  VISIBLE_CH.forEach(i => {
    const el = $(`chName${i}`), wrap = el?.parentElement;
    if (!el || !wrap) return;
    const voll = chName(i);
    el.textContent = voll;
    if (!voll) return;                       // ohne Namen nichts zu pruefen
    if (wrap.clientWidth < _CH_NAME_MIN_BREITE_PX) { innen = false; return; }
    // Senkrechte Schrift: die Laenge des Textes liegt in der Hoehe.
    if (el.scrollHeight > wrap.clientHeight) innen = false;
  });

  reihe.classList.toggle('namen-innen', innen);
  if (unten) unten.innerHTML = '';
  if (innen) return;

  // Passt nicht: alle nach unten.
  VISIBLE_CH.forEach(i => {
    const el = $(`chName${i}`);
    if (el) el.textContent = '';
    if (!unten) return;
    const feld = document.createElement('div');
    feld.className = 'ch-name-unten';
    feld.textContent = chName(i);
    unten.appendChild(feld);
  });
}

/** Namen neu schreiben, ohne die Balken neu zu bauen (nach dem Speichern). */
function chNamenAuffrischen() {
  chNamenPassend();
  if (typeof buildWideSliders === 'function') buildWideSliders();
  if (typeof _lastData !== 'undefined' && _lastData?.lights)
    updateChannels(_lastData.lights.channels ?? []);
}

// Die Kachel aendert ihre Groesse: Spaltenwahl, Drehen, Kiosk-Raster. Passt
// der Name dann nicht mehr, muss er kuerzer werden — und andersherum.
window.addEventListener('resize', chNamenPassend);

// ── Die Balken sind Regler ──────────────────────────────────────────────────
// Ein Balken zeigt die Helligkeit ohnehin an — dann kann er sie auch stellen.
//
// Zwei Dinge sind dabei wichtig und beide ausdruecklich gewuenscht:
//
//   * Es wird die BEWEGUNG gerechnet, nicht die Position. Wer unten auf den
//     Balken tippt, will nicht, dass das Licht auf 5 % springt — er will
//     vielleicht nur die Detailseite oeffnen. Der Wert aendert sich um das,
//     was der Finger zurueckgelegt hat, ausgehend vom Stand beim Aufsetzen.
//   * Der Finger wird ueber die Kachel hinaus verfolgt (Pointer Capture). Ein
//     Balken ist ein paar Zentimeter hoch; wer von ganz unten nach ganz oben
//     will, verlaesst ihn dabei zwangslaeufig.
//
// Ein kurzer Tipp bleibt, was er war: er oeffnet die Detailseite. Dafuer wird
// hier nichts abgefangen — nur nach einem Zug oder einem Halten wird der
// folgende Klick geschluckt, damit nicht beides zugleich passiert.

const _CH_WEG_PX      = 150;   // Fingerweg fuer den ganzen Bereich 0..255
const _CH_ZUG_AB_PX   = 4;     // darunter ist es ein Tipp, kein Zug
// Relais: so lange halten zum Schalten. Erst zwei Sekunden, dann eine, jetzt
// eine Drittel — dieselbe Dauer wie in der Statusleiste. So kurz vertretbar,
// weil der Balken vom ersten Moment an mitlaeuft: man SIEHT, dass gerade
// geschaltet wird, und kann den Finger noch wegnehmen.
const _CH_HALTEN_MS   = 330;

let _chZug = null;             // {ch, startY, startWert, gezogen}
let _chKlickSchlucken = false;

function _chGesteBinden(row) {
  row.addEventListener('pointerdown', e => {
    const wrap = e.target.closest('.ch-bar-wrap');
    if (!wrap) return;
    const ch = +wrap.dataset.ch;
    _chZug = { ch, startY: e.clientY, startWert: _wideCh[ch] ?? 0, gezogen: false,
               halteUhr: null, wrap };
    wrap.setPointerCapture?.(e.pointerId);
    row.classList.add('zieht');

    if (ch >= 8) {
      // Relais: erst nach dem Halten. Ohne die Wartezeit schaltete es jedes
      // Mal mit, wenn jemand die Detailseite oeffnen wollte.
      //
      // Die Fuellung laeuft in die Richtung, in die es GEHT: ist das Relais
      // an, faehrt der Balken herunter; ist es aus, herauf. Man sieht damit
      // waehrend des Haltens, was gleich passiert, statt nur dass etwas
      // passiert — und beim Loslassen steht der Balken schon dort, wo der neue
      // Zustand ihn haben will.
      const an = (_wideCh[ch] ?? 0) > 0;
      const bar = $(`chBar${ch}`);
      if (bar) {
        bar.style.transition = `height ${_CH_HALTEN_MS}ms linear`;
        bar.style.height = an ? '0%' : '100%';
      }
      _chZug.relaisZiel = !an;
      _chZug.halteUhr = setTimeout(() => {
        _chZug && (_chZug.gezogen = true);        // Klick danach schlucken
        if (_chZug) _chZug.relaisGeschaltet = true;
        if (typeof _toggleRelayFromWide === 'function') _toggleRelayFromWide();
        if (navigator.vibrate) navigator.vibrate(20);
      }, _CH_HALTEN_MS);
    }
  });

  const beenden = e => {
    if (!_chZug) return;
    clearTimeout(_chZug.halteUhr);
    if (_chZug.ch >= 8) {
      const bar = $(`chBar${_chZug.ch}`);
      if (bar) {
        if (_chZug.relaisGeschaltet) {
          // Geschaltet: der Balken steht schon richtig, er bleibt einfach.
          bar.style.transition = '';
        } else {
          // Losgelassen, bevor es soweit war — zurueck auf den echten Stand.
          bar.style.transition = 'height .15s ease';
          const an = (_wideCh[_chZug.ch] ?? 0) > 0;
          bar.style.height = an ? '100%' : '0%';
        }
      }
    }
    _chKlickSchlucken = _chZug.gezogen;
    _chZug = null;
    row.classList.remove('zieht');
    // Der Klick kommt unmittelbar nach pointerup; das Fenster darf nur so
    // lange offen sein, dass genau dieser eine hineinfaellt.
    if (_chKlickSchlucken) setTimeout(() => { _chKlickSchlucken = false; }, 300);
  };
  row.addEventListener('pointerup', beenden);
  row.addEventListener('pointercancel', beenden);

  row.addEventListener('pointermove', e => {
    if (!_chZug) return;
    const weg = _chZug.startY - e.clientY;          // nach oben ist heller
    if (!_chZug.gezogen && Math.abs(weg) < _CH_ZUG_AB_PX) return;
    if (!_chZug.gezogen) {
      _chZug.gezogen = true;
      clearTimeout(_chZug.halteUhr);                // aus Halten wird Ziehen
      if (_chZug.ch >= 8) {
        // Das Relais bleibt, wo es war: der angefangene Lauf war eine
        // Ankuendigung, keine Schaltung.
        const bar = $(`chBar${_chZug.ch}`);
        if (bar) {
          bar.style.transition = 'height .15s ease';
          bar.style.height = (_wideCh[8] ?? 0) > 0 ? '100%' : '0%';
        }
      }
    }
    if (_chZug.ch >= 8) return;                     // das Relais kennt kein Dazwischen
    const wert = Math.max(0, Math.min(255,
      Math.round(_chZug.startWert + weg / _CH_WEG_PX * 255)));
    if (wert === _wideCh[_chZug.ch]) return;
    // Sofort sichtbar, gesendet wird gedrosselt (_setChannelFromSlider).
    _wideCh[_chZug.ch] = wert;
    const bar = $(`chBar${_chZug.ch}`);
    if (bar) { bar.style.height = wert / 255 * 100 + '%';
               bar.style.opacity = wert > 0 ? '0.9' : '0.2'; }
    _setChannelFromSlider(_chZug.ch, wert);
    if (typeof updateWideSliders === 'function') updateWideSliders(_wideCh);
  });

  // Nach einem Zug oder einem Halten NICHT auch noch die Detailseite oeffnen.
  row.addEventListener('click', e => {
    if (!_chKlickSchlucken) return;
    e.stopPropagation();
    e.preventDefault();
    _chKlickSchlucken = false;
  }, true);
}

// _chHaltenZuruecksetzen und ein eigener Fuellstreifen standen hier. Beide sind
// entfallen: das Relais benutzt jetzt SEINEN EIGENEN Balken als Anzeige, und
// der zeigt dabei gleich die Richtung, in die geschaltet wird.

// Aktueller Kanal-Zustand für die Wide-Slider (mit echtem Gerätezustand synchron,
// damit ein Slider-Move nicht das Relais oder andere Kanäle überschreibt).
let _wideCh = Array(9).fill(0);

function updateChannels(channels) {
  // Der Kanal, an dem gerade ein Finger haengt, gehoert dem Finger.
  //
  // Ohne diese Ausnahme schrieb jeder hereinkommende Datensatz den Balken auf
  // den Stand zurueck, den das Geraet vor 200 ms gemeldet hatte — der Balken
  // sprang dann waehrend des Ziehens hoch und runter, weil zwei Quellen um
  // dieselbe Hoehe stritten.
  const gezogen = _chZug ? _chZug.ch : -1;
  VISIBLE_CH.forEach(i => {
    if (i === gezogen) return;
    const v = channels[i] ?? 0, bar = $(`chBar${i}`);
    if (!bar) return;
    bar.style.height  = (i < 8 ? v/255*100 : v>0?100:0) + '%';
    bar.style.opacity = v > 0 ? '0.9' : '0.2';
  });
  // _wideCh mit echtem Zustand abgleichen — außer dem gerade gezogenen Slider
  for (let i = 0; i < 9; i++) {
    if (channels[i] == null || i === gezogen) continue;
    const sl = $(`wideSlider${i}`);
    if (sl && document.activeElement === sl) continue;
    _wideCh[i] = channels[i];
  }
  updateWideSliders(channels);
}

// ── Wide tile: vertical zone sliders ────────────────────────────────────────

// _WIDE_LABELS stand hier als zweite, kürzere Namensliste. Gekürzt wird jetzt
// gerechnet (chKurz in core.js) statt ein zweites Mal gepflegt.

function buildWideSliders() {
  const wrap = $('lightsWideSliders');
  if (!wrap) return;
  wrap.innerHTML = '';
  VISIBLE_CH.forEach(i => {
    const isRelay = i >= 8;
    // Volle Bezeichnung; kuerzen macht der Browser, wenn die Spalte zu schmal
    // ist. Auf neun Zeichen zu raten ergab "Ankerlic." — die tatsaechliche
    // Breite kennt nur das Stylesheet.
    const lbl = _esc(chBezeichnung(i));
    const item = document.createElement('div');
    item.className = 'lights-slider-item';
    if (isRelay) {
      item.innerHTML = `<button class="lights-relay-btn" id="wideRelayBtn"
        onclick="event.stopPropagation();_toggleRelayFromWide()" title="Relais">⏻</button>
        <span class="lights-slider-lbl">${lbl}</span>`;
    } else {
      item.innerHTML = `<input type="range" class="lights-vslider" id="wideSlider${i}"
        min="0" max="255" value="0"
        oninput="event.stopPropagation();_setChannelFromSlider(${i},+this.value)"
        onclick="event.stopPropagation()">
        <span class="lights-slider-lbl">${lbl}</span>`;
    }
    wrap.appendChild(item);
  });
}
buildWideSliders();

function updateWideSliders(channels) {
  VISIBLE_CH.forEach(i => {
    if (i >= 8) {
      const btn = $('wideRelayBtn');
      if (btn) btn.classList.toggle('active', (channels[i] ?? 0) > 0);
    } else {
      const sl = $(`wideSlider${i}`);
      if (sl && document.activeElement !== sl) sl.value = channels[i] ?? 0;
    }
  });
}

// Gesendet wird GETAKTET, nicht entprellt.
//
// Vorher stand hier ein clearTimeout/setTimeout-Paar: jede Bewegung schob den
// Timer nach hinten. Solange der Finger lief, ging deshalb GAR NICHTS hinaus —
// das Licht sprang erst, wenn man losliess. Genau das war der spuerbare
// Nachlauf; ein Preset fuehlte sich schneller an, weil es ein einziger Aufruf
// ohne Timer ist.
//
// Jetzt: der erste Wert geht sofort raus, danach hoechstens alle 60 ms einer,
// und der letzte kommt am Ende in jedem Fall nach. So sieht man beim Ziehen,
// was man tut, und der Bus bekommt trotzdem nicht jede Fingerregung.
const _LICHT_TAKT_MS = 60;
let _lichtLetzteSendung = 0;
let _lichtNachzuegler = null;

function _lichtSenden() {
  _lichtLetzteSendung = Date.now();
  fetch('/api/lights/channels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values: _wideCh }),
  }).catch(() => {});
}

function _setChannelFromSlider(ch, val) {
  _wideCh[ch] = val;
  if (typeof checkPresetMatch === 'function') checkPresetMatch(_wideCh);
  const seit = Date.now() - _lichtLetzteSendung;
  if (seit >= _LICHT_TAKT_MS) {
    clearTimeout(_lichtNachzuegler);
    _lichtNachzuegler = null;
    _lichtSenden();
    return;
  }
  if (_lichtNachzuegler) return;           // einer wartet schon
  _lichtNachzuegler = setTimeout(() => {
    _lichtNachzuegler = null;
    _lichtSenden();
  }, _LICHT_TAKT_MS - seit);
}

function _toggleRelayFromWide() {
  _wideCh[8] = _wideCh[8] > 0 ? 0 : 1;
  if (typeof checkPresetMatch === 'function') checkPresetMatch(_wideCh);
  updateChannels(_wideCh);
  fetch('/api/lights/channels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values: _wideCh }),
  }).catch(() => {});
}

// ── Presets ────────────────────────────────────────────────────────────────

let presets = [], activePreset = null;

async function loadPresets() {
  try {
    const data = await fetch('/api/presets').then(r => r.json());
    presets = data.presets ?? [];
    if (data.tanks)     tanksConfig     = data.tanks;
    if (data.devices)   devicesConfig   = data.devices;
    if (data.batteries) batteriesConfig = data.batteries;
    if (data.wartung)   wartungConfig   = { ...wartungConfig, ...data.wartung };
    if (data.lights)    lightsConfig    = data.lights;
    // Apply tank names from config
    $('tank1Name').textContent = tanksConfig.tank1?.name ?? 'Tank 1';
    $('tank2Name').textContent = tanksConfig.tank2?.name ?? 'Tank 2';
    renderPresets();
  } catch(e) { console.error('Presets nicht geladen:', e); }
}


// Presets tragen historisch ein Emoji-Feld (presets.json liegt als Datendatei
// auf dem Pi und ist nicht versioniert — wir koennen sie hier nicht umschreiben).
// Deshalb wird das Emoji beim Rendern auf ein Icon aus dem SVG-Satz abgebildet.
// Ein spaeter ergaenztes Feld `icon` hat Vorrang.
const PRESET_ICON = {
  '🌙': 'moon', '☀️': 'sun', '☀': 'sun', '💡': 'bulb',
  '🔆': 'sun', '🌞': 'sun', '🕯️': 'bulb', '🔦': 'bulb',
};
function presetIcon(p, size) {
  const name = p.icon || PRESET_ICON[(p.emoji || '').trim()] || 'bulb';
  return icon(name, { size: size || 22 });
}

function renderPresets() {
  const grid = $('presetsGrid'); grid.innerHTML = '';
  presets.forEach((p, i) => {
    const btn = document.createElement('button');
    btn.className = 'preset-btn' + (p.values==null?' disabled':'') + (activePreset===i?' active':'');
    // Preset-Namen sind frei eingebbar (Lichter-Detail) und landen im innerHTML.
    btn.innerHTML = `<span class="preset-emoji">${presetIcon(p)}</span><span class="preset-name">${_esc(p.name)}</span>`;
    if (p.values != null) btn.addEventListener('click', e => { e.stopPropagation(); applyPreset(i); });
    grid.appendChild(btn);
  });
}

async function applyPreset(id) {
  if (!presets[id] || presets[id].values == null) return;
  activePreset = id; renderPresets();
  try { await fetch(`/api/lights/preset/${id}`, { method: 'POST' }); }
  catch(e) { activePreset = null; renderPresets(); }
}

function checkPresetMatch(channels) {
  const ch = channels ?? liveChannels;
  const match = presets.findIndex(p => {
    if (!p.values) return false;
    return p.values.every((v, i) => Math.abs(v - (ch[i] ?? 0)) <= 2);
  });
  const newActive = match >= 0 ? match : null;
  if (newActive !== activePreset) {
    activePreset = newActive;
    renderPresets();
  }
}

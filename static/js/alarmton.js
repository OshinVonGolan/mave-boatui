// ── Akustische Alarme ──────────────────────────────────────────────────────
// Ein Alarm, den niemand sieht, ist keiner. Das Wandtablet am Kartentisch hängt
// oft eine Kajüte weiter — es muss sich melden können.
//
// Zwei Entscheidungen vorab, beide bewusst:
//
// KEINE Tondatei. Der Ton wird im Browser erzeugt (zwei Sinustöne im Wechsel,
// wie eine Schiffsglocke). Das lädt nichts nach, funktioniert ohne Internet und
// klingt auf jedem Gerät gleich. Eine mitgelieferte MP3 wäre größer als das
// halbe Bundle und müsste erst geladen sein, wenn es darauf ankommt.
//
// JE GERÄT eingestellt. Das Tablet an der Wand soll Krach machen, das Telefon
// des Eigners nachts vielleicht nicht. Die Einstellungen liegen deshalb im
// Browserspeicher des jeweiligen Geräts — wie die Anzeige-Einstellungen auch,
// und aus demselben Grund.

const _ALARM_KEY = 'mave_alarmton';
const _ALARM_VORGABE = {
  ton: true,              // überhaupt Ton
  nurKritisch: false,     // nur bei severity 'critical'
  lautstaerke: 0.6,       // 0…1
  wiederholen: true,      // bis quittiert
  takt_s: 20,             // Abstand der Wiederholungen
};

let _alarmCfg = null;
let _tonKontext = null;
let _tonBereit = false;         // hat der Browser Ton schon erlaubt?
let _gemeldet = new Set();      // Alarm-Ids, die schon Ton hatten
let _wiederholUhr = null;
let _wartetAufGriff = false;

function _alarmCfgLaden() {
  try {
    _alarmCfg = { ..._ALARM_VORGABE, ...(JSON.parse(localStorage.getItem(_ALARM_KEY)) || {}) };
  } catch (_) {
    _alarmCfg = { ..._ALARM_VORGABE };
  }
  return _alarmCfg;
}

function _alarmCfgSichern() {
  try { localStorage.setItem(_ALARM_KEY, JSON.stringify(_alarmCfg)); } catch (_) {}
}

/**
 * Der Tongeber.
 *
 * Android (und jeder andere Browser) lässt Ton erst zu, NACHDEM jemand die
 * Seite berührt hat. Nach einem Neuladen ist das nicht der Fall — und genau
 * dann könnte der erste Alarm stumm bleiben. Deshalb wird der Kontext beim
 * ersten Griff geweckt, egal wofür der Griff gedacht war, und ein Alarm, der
 * vorher eintrifft, wartet sichtbar statt zu verschwinden.
 */
function _kontext() {
  if (!_tonKontext) {
    const K = window.AudioContext || window.webkitAudioContext;
    if (!K) return null;
    _tonKontext = new K();
  }
  return _tonKontext;
}

function _tonFreigeben() {
  const k = _kontext();
  if (!k) return;
  if (k.state === 'suspended') k.resume().then(() => { _tonBereit = true; }).catch(() => {});
  else _tonBereit = true;
  if (_tonBereit && _wartetAufGriff) {
    _wartetAufGriff = false;
    _hinweisWeg();
    alarmTonSpielen();
  }
}

// Jeder Griff zählt — Tippen, Klicken, Taste. Einmal reicht für die Sitzung.
for (const art of ['pointerdown', 'keydown', 'touchstart']) {
  document.addEventListener(art, _tonFreigeben, { passive: true, once: false });
}

/**
 * Zwei Töne im Wechsel, viermal. Kein Dauerton: ein gleichmäßiger Pfeifton
 * wird nach Sekunden ausgeblendet und man sucht dann den Schalter, nicht die
 * Ursache. Ein Wechsel bleibt aufdringlich, ohne wehzutun.
 */
function alarmTonSpielen() {
  if (!_alarmCfg) _alarmCfgLaden();
  const k = _kontext();
  if (!k || k.state !== 'running') { _wartetAufGriff = true; _hinweisZeigen(); return; }
  const laut = Math.max(0, Math.min(1, _alarmCfg.lautstaerke));
  if (laut <= 0) return;
  const t0 = k.currentTime;
  for (let i = 0; i < 4; i++) {
    const o = k.createOscillator();
    const g = k.createGain();
    o.type = 'sine';
    o.frequency.value = i % 2 === 0 ? 880 : 660;
    // Weich ein und aus: ein hart geschalteter Sinus knackt auf kleinen
    // Lautsprechern hörbar.
    const a = t0 + i * 0.34;
    g.gain.setValueAtTime(0.0001, a);
    g.gain.exponentialRampToValueAtTime(laut * 0.5, a + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, a + 0.28);
    o.connect(g).connect(k.destination);
    o.start(a);
    o.stop(a + 0.3);
  }
}

// ── Der Hinweis, wenn der Browser den Ton noch nicht erlaubt ──────────────
function _hinweisZeigen() {
  if (document.getElementById('tonHinweis')) return;
  const b = document.createElement('div');
  b.id = 'tonHinweis';
  b.className = 'ton-hinweis';
  b.innerHTML = '<span class="th-mark">Alarm</span>'
    + '<span class="th-txt">Ein Alarm liegt an. Der Browser lässt Ton erst zu, '
    + 'wenn die Seite einmal berührt wurde — tippe irgendwohin.</span>';
  b.onclick = _tonFreigeben;
  document.body.appendChild(b);
}

function _hinweisWeg() { document.getElementById('tonHinweis')?.remove(); }

// ── Was das Frontend bei jedem Zustand aufruft ────────────────────────────

/**
 * Prüft, ob ein Alarm NEU ist, und macht dann Krach.
 *
 * "Neu" heisst: unquittiert, ungelöst, und wir haben für diese Alarm-Id noch
 * keinen Ton gegeben. Die Id bleibt über die Lebensdauer des Alarms gleich —
 * dadurch klingelt es nicht bei jedem Zustandspaket erneut, sondern einmal,
 * und danach nur noch im eingestellten Takt.
 */
function alarmTonPruefen(alarme) {
  if (!_alarmCfg) _alarmCfgLaden();
  const offen = (alarme || []).filter(a => !a.resolved && !a.acknowledged
    && (!_alarmCfg.nurKritisch || a.severity === 'critical'));

  // Was nicht mehr offen ist, darf beim nächsten Mal wieder Ton geben.
  const offeneIds = new Set(offen.map(a => a.id));
  for (const id of [..._gemeldet]) if (!offeneIds.has(id)) _gemeldet.delete(id);

  const neu = offen.filter(a => !_gemeldet.has(a.id));
  for (const a of neu) _gemeldet.add(a.id);

  if (!_alarmCfg.ton) { _wiederholStoppen(); return; }
  if (neu.length) alarmTonSpielen();

  // Wiederholen, solange etwas offen ist. Ein Alarm, der einmal piept und
  // dann schweigt, ist nach fünf Minuten vergessen — und genau das soll er
  // nicht sein.
  if (offen.length && _alarmCfg.wiederholen) _wiederholStarten();
  else _wiederholStoppen();
}

function _wiederholStarten() {
  if (_wiederholUhr) return;
  _wiederholUhr = setInterval(alarmTonSpielen,
                              Math.max(5, _alarmCfg.takt_s) * 1000);
}

function _wiederholStoppen() {
  if (!_wiederholUhr) return;
  clearInterval(_wiederholUhr);
  _wiederholUhr = null;
}

// ── Einstellungen ─────────────────────────────────────────────────────────

function openAlarmSettings() {
  if (!_alarmCfg) _alarmCfgLaden();
  const pane = document.getElementById('setPane-alarme');
  if (!pane) return;
  const an = (b) => b ? ' checked' : '';
  pane.innerHTML = `
    <div class="set-card">
      <div class="set-card-hd">Ton auf diesem Gerät</div>
      <div style="font-size:12px;color:var(--text3);margin:-4px 0 10px">
        Gilt nur für dieses Gerät. Das Tablet an der Wand soll sich melden,
        ein Telefon nachts vielleicht nicht.
      </div>
      <div class="settings-row">
        <label class="settings-label" for="alTon">Bei Alarm einen Ton geben</label>
        <input type="checkbox" id="alTon"${an(_alarmCfg.ton)}>
      </div>
      <div class="settings-row">
        <label class="settings-label" for="alNurKrit">Nur bei kritischen Alarmen</label>
        <input type="checkbox" id="alNurKrit"${an(_alarmCfg.nurKritisch)}>
      </div>
      <div class="settings-row">
        <label class="settings-label" for="alLaut">Lautstärke</label>
        <input type="range" id="alLaut" min="0" max="100" step="5"
               value="${Math.round(_alarmCfg.lautstaerke * 100)}"
               class="settings-input" style="max-width:200px">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="alWdh">Wiederholen, bis quittiert</label>
        <input type="checkbox" id="alWdh"${an(_alarmCfg.wiederholen)}>
      </div>
      <div class="settings-row">
        <label class="settings-label" for="alTakt">Abstand der Wiederholung</label>
        <select class="settings-input" id="alTakt" style="max-width:160px;cursor:pointer">
          ${[10, 20, 30, 60, 120].map(s =>
            `<option value="${s}"${_alarmCfg.takt_s === s ? ' selected' : ''}>${
              s < 60 ? s + ' Sekunden' : (s / 60) + ' Minute' + (s > 60 ? 'n' : '')}</option>`).join('')}
        </select>
      </div>
      <div class="settings-row" style="border-bottom:none">
        <label class="settings-label">Ausprobieren</label>
        <button class="btn-secondary" onclick="alarmTonSpielen()">Ton abspielen</button>
      </div>
    </div>

    <div class="set-card">
      <div class="set-card-hd">Wenn der Ton stumm bleibt</div>
      <div style="font-size:12px;color:var(--text2);line-height:1.55">
        Browser lassen Ton erst zu, nachdem jemand die Seite berührt hat — nach
        einem Neuladen also nicht sofort. Kommt in dieser Zeit ein Alarm, wartet
        er sichtbar am unteren Rand und geht beim ersten Antippen los.
        Auf einem fest montierten Gerät ist das nach dem ersten Griff erledigt.
      </div>
    </div>

    <div class="settings-actions">
      <span class="settings-feedback" id="alFeedback"></span>
      <button class="btn-primary" onclick="saveAlarmSettings()">Speichern &amp; anwenden</button>
    </div>`;
}

function saveAlarmSettings() {
  const w = (id) => document.getElementById(id);
  _alarmCfg.ton = w('alTon').checked;
  _alarmCfg.nurKritisch = w('alNurKrit').checked;
  _alarmCfg.lautstaerke = Number(w('alLaut').value) / 100;
  _alarmCfg.wiederholen = w('alWdh').checked;
  _alarmCfg.takt_s = Number(w('alTakt').value);
  _alarmCfgSichern();
  // Der Takt kann sich geändert haben — die laufende Uhr neu stellen.
  _wiederholStoppen();
  const fb = w('alFeedback');
  if (fb) { fb.textContent = 'Gespeichert ✓'; setTimeout(() => { fb.textContent = ''; }, 2000); }
}

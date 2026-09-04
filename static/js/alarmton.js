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

// Wie sich ein Gerät meldet. Zwei Wege, und sie unterscheiden sich in genau
// dem Punkt, nach dem der Eigner gefragt hat — welcher Lautstärkeregler gilt:
//
//   'ton'     Der Browser erzeugt den Ton selbst. Klingt wie eine Glocke und
//             ist unverwechselbar, hängt aber an der MEDIENLAUTSTÄRKE. Anders
//             geht es nicht: eine Webseite kann den Alarm- oder
//             Benachrichtigungskanal von Android nicht wählen, das kann nur
//             eine native App.
//   'hinweis' Eine echte Systembenachrichtigung. Sie läuft über die
//             BENACHRICHTIGUNGSLAUTSTÄRKE und erscheint auch im Schirm des
//             Geräts. Der Klang ist dafür der des Systems, nicht unserer.
//
// Beides gleichzeitig geht auch. Vorgabe ist AUS: ein Gerät, das ungefragt
// Krach macht, wird stummgeschaltet und meldet sich dann nie wieder.
const _ALARM_VORGABE = {
  melden: 'aus',          // 'aus' | 'ton' | 'hinweis' | 'beides'
  nurKritisch: false,     // nur bei severity 'critical'
  lautstaerke: 0.6,       // 0…1, gilt nur für den eigenen Ton
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
  let gespeichert = {};
  try { gespeichert = JSON.parse(localStorage.getItem(_ALARM_KEY)) || {}; } catch (_) {}
  // Der Schalter hiess einmal `ton: true/false`. Wer ihn schon gesetzt hatte,
  // soll ihn behalten — ein Update darf eine getroffene Wahl nicht stillschweigend
  // zurücksetzen.
  if (gespeichert.melden === undefined && gespeichert.ton !== undefined) {
    gespeichert.melden = gespeichert.ton ? 'ton' : 'aus';
  }
  _alarmCfg = { ..._ALARM_VORGABE, ...gespeichert };
  return _alarmCfg;
}

function _willTon()     { return _alarmCfg.melden === 'ton' || _alarmCfg.melden === 'beides'; }
function _willHinweis() { return _alarmCfg.melden === 'hinweis' || _alarmCfg.melden === 'beides'; }

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
  if (!_willTon()) return;
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

// ── Systembenachrichtigung ────────────────────────────────────────────────
// Der Weg über die BENACHRICHTIGUNGSLAUTSTÄRKE. Sie kommt vom Service Worker
// und nicht über `new Notification(...)`: auf Android verweigert Chrome den
// direkten Weg in einer installierten App, der Service Worker ist dort der
// einzige, der eine Benachrichtigung zeigen darf. Er läuft, solange die Seite
// offen ist — dafür braucht es also KEIN Push.
//
// Einen eigenen Klang kann eine Web-App dabei nicht mitgeben; das Feld `sound`
// setzt Chrome nicht um. Es klingt deshalb wie eine Benachrichtigung.

async function alarmHinweisZeigen(alarm) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    const reg = await navigator.serviceWorker?.ready;
    if (!reg) return;
    const kritisch = alarm?.severity === 'critical';
    await reg.showNotification(kritisch ? 'Alarm an Bord' : 'Hinweis an Bord', {
      body: alarm?.name
        ? alarm.name + (alarm.value != null ? ` — ${alarm.value}` : '')
        : 'Ein Alarm liegt an.',
      icon: '/static/icon-192.png',
      badge: '/static/favicon-32.png',
      // Gleiche Kennung je Alarm: eine erneute Meldung ersetzt die alte,
      // statt den Schirm zuzupflastern.
      tag: 'mave-alarm-' + (alarm?.id || 'x'),
      renotify: true,
      requireInteraction: kritisch,
      data: { url: '/#alarme' },
    });
  } catch (e) {
    console.debug('Benachrichtigung nicht möglich:', e);
  }
}

/** Erlaubnis erfragen. Muss aus einem Nutzergriff kommen, sonst lehnt der
 *  Browser ohne Rückfrage ab. */
async function alarmHinweisErlauben() {
  if (!('Notification' in window)) { _toast('Dieses Gerät kennt keine Benachrichtigungen'); return; }
  const antwort = await Notification.requestPermission();
  _toast(antwort === 'granted' ? 'Benachrichtigungen erlaubt'
        : antwort === 'denied' ? 'Benachrichtigungen abgelehnt — im Browser umstellbar'
        : 'Keine Entscheidung getroffen');
  openAlarmSettings();
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

  if (_alarmCfg.melden === 'aus') { _wiederholStoppen(); return; }
  if (neu.length) {
    if (_willTon()) alarmTonSpielen();
    if (_willHinweis()) for (const a of neu) alarmHinweisZeigen(a);
  }

  // Wiederholen, solange etwas offen ist. Ein Alarm, der einmal piept und
  // dann schweigt, ist nach fünf Minuten vergessen — und genau das soll er
  // nicht sein.
  if (offen.length && _alarmCfg.wiederholen) _wiederholStarten();
  else _wiederholStoppen();
}

function _wiederholStarten() {
  if (_wiederholUhr) return;
  // Wiederholt wird nur der TON. Eine Benachrichtigung alle zwanzig Sekunden
  // erneut zu zeigen wäre Belästigung — sie bleibt ohnehin stehen, bis man sie
  // wegwischt, und übernimmt damit das Erinnern von selbst.
  _wiederholUhr = setInterval(() => { if (_willTon()) alarmTonSpielen(); },
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
  const erlaubt = ('Notification' in window) ? Notification.permission : 'unmoeglich';
  const wege = [
    ['aus', 'Aus', 'Dieses Gerät bleibt still.'],
    ['hinweis', 'Benachrichtigung des Geräts',
     'Landet in der Benachrichtigungsleiste, mit Ton und Lautstärke des '
     + '<b>Benachrichtigungskanals</b> — also dort, wo alle anderen Meldungen '
     + 'des Geräts auch auflaufen. Antippen öffnet die Alarme.'],
    ['ton', 'Eigener Ton',
     'Eine Glocke, im Browser erzeugt. Unverwechselbar, hängt aber an der '
     + '<b>Medienlautstärke</b>: eine Web-App kann den Alarmkanal des Geräts '
     + 'nicht wählen, das kann nur eine native App.'],
    ['beides', 'Beides', 'Benachrichtigung und Glocke.'],
  ];

  pane.innerHTML = `
    <div class="set-card">
      <div class="set-card-hd">Wie sich dieses Gerät meldet</div>
      <div style="font-size:12px;color:var(--text3);margin:-4px 0 12px">
        Gilt nur für dieses Gerät. Das Tablet an der Wand soll sich melden,
        ein Telefon nachts vielleicht nicht.
      </div>
      ${wege.map(([wert, titel, text]) => `
        <label class="al-weg${_alarmCfg.melden === wert ? ' an' : ''}">
          <input type="radio" name="alMelden" value="${wert}"${_alarmCfg.melden === wert ? ' checked' : ''}
                 onchange="_alarmWegGewaehlt(this.value)">
          <span class="al-weg-text"><b>${titel}</b><span>${text}</span></span>
        </label>`).join('')}
    </div>

    ${(_alarmCfg.melden === 'hinweis' || _alarmCfg.melden === 'beides') ? `
    <div class="set-card">
      <div class="set-card-hd">Benachrichtigungen</div>
      <div class="settings-row" style="border-bottom:none">
        <label class="settings-label">Erlaubnis dieses Geräts</label>
        ${erlaubt === 'granted'
          ? '<span style="color:var(--green);font-size:13px;font-weight:600">erteilt</span>'
          : erlaubt === 'denied'
          ? '<span style="color:var(--red);font-size:13px">abgelehnt — nur im Browser umstellbar</span>'
          : erlaubt === 'unmoeglich'
          ? '<span style="color:var(--text3);font-size:13px">kennt dieses Gerät nicht</span>'
          : '<button class="btn-secondary" onclick="alarmHinweisErlauben()">Jetzt erlauben</button>'}
      </div>
    </div>` : ''}

    <div class="set-card">
      <div class="set-card-hd">Wann und wie oft</div>
      <div class="settings-row">
        <label class="settings-label" for="alNurKrit">Nur bei kritischen Alarmen</label>
        <input type="checkbox" id="alNurKrit"${an(_alarmCfg.nurKritisch)}>
      </div>
      <div class="settings-row">
        <label class="settings-label" for="alLaut">Lautstärke des eigenen Tons</label>
        <input type="range" id="alLaut" min="0" max="100" step="5"
               value="${Math.round(_alarmCfg.lautstaerke * 100)}"
               class="settings-input" style="max-width:200px">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="alWdh">Ton wiederholen, bis quittiert</label>
        <input type="checkbox" id="alWdh"${an(_alarmCfg.wiederholen)}>
      </div>
      <div class="settings-row">
        <label class="settings-label" for="alTakt">Abstand der Wiederholung</label>
        <select class="settings-input" id="alTakt" style="max-width:160px;cursor:pointer">
          ${[10, 20, 30, 60, 120].map(x =>
            `<option value="${x}"${_alarmCfg.takt_s === x ? ' selected' : ''}>${
              x < 60 ? x + ' Sekunden' : (x / 60) + ' Minute' + (x > 60 ? 'n' : '')}</option>`).join('')}
        </select>
      </div>
      <div class="settings-row" style="border-bottom:none">
        <label class="settings-label">Ausprobieren</label>
        <span style="display:flex;gap:8px">
          <button class="btn-secondary" onclick="alarmTonSpielen()">Ton</button>
          <button class="btn-secondary" onclick="alarmHinweisZeigen({name:'Probe',severity:'warning',id:'probe'})">Benachrichtigung</button>
        </span>
      </div>
    </div>

    <div class="set-card">
      <div class="set-card-hd">Was das Gerät kann und was nicht</div>
      <div style="font-size:12px;color:var(--text2);line-height:1.55">
        Der eigene Ton geht erst, nachdem jemand die Seite berührt hat — nach
        einem Neuladen also nicht sofort. Kommt in dieser Zeit ein Alarm, wartet
        er sichtbar am unteren Rand und geht beim ersten Antippen los.<br><br>
        Bei geschlossener App meldet sich nur die Benachrichtigung, und dafür
        braucht es Internet auf beiden Seiten. Im Bordnetz ohne Uplink bleibt
        der Ton bei offener App der einzige Weg — dafür ist er der einzige, der
        immer funktioniert.
      </div>
    </div>

    <div class="settings-actions">
      <span class="settings-feedback" id="alFeedback"></span>
      <button class="btn-primary" onclick="saveAlarmSettings()">Speichern &amp; anwenden</button>
    </div>`;
}

/** Sofort merken und neu zeichnen: der Erlaubnis-Kasten hängt an der Wahl. */
function _alarmWegGewaehlt(wert) {
  _alarmCfg.melden = wert;
  _alarmCfgSichern();
  _wiederholStoppen();
  openAlarmSettings();
}

function saveAlarmSettings() {
  const w = (id) => document.getElementById(id);
  const gewaehlt = document.querySelector('input[name="alMelden"]:checked');
  if (gewaehlt) _alarmCfg.melden = gewaehlt.value;
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

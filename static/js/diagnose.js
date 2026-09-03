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
let _daten = { verbindung: null, diagnose: null, konten: null };

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

async function start() {
  const z = await (await fetch('/api/zugang', { cache: 'no-store' })).json().catch(() => null);
  if (!z) return;
  if (!z.angemeldet) { anmeldungZeigen(); return; }
  _konto = z.konto;

  if (!(_konto.oberflaechen || []).includes('diagnose')) {
    // Das Tor steht am Endpunkt; hier wird es nur erklaert. Wer die Adresse
    // kennt, aber das Recht nicht hat, bekommt von den Daten nichts zu sehen.
    $('sperre').hidden = false;
    $('sperreText').textContent =
      `Angemeldet als ${_konto.rolle_name}. Das Logbuch ist dem Eigner und `
      + 'Technikern vorbehalten — die Bordansicht steht dir offen.';
    return;
  }

  $('wer').textContent = _konto.rolle_name;
  $('inhalt').hidden = false;
  const darf = _konto.handlungen || [];
  $('kontenTafel').hidden = !darf.includes('verwalten');
  $('wartungTafel').hidden = !darf.includes('fernwarten');

  $('zeitwahl').addEventListener('click', e => {
    const k = e.target.closest('.zw-knopf');
    if (!k) return;
    _tage = Number(k.dataset.tage);
    document.querySelectorAll('.zw-knopf').forEach(b => b.classList.toggle('an', b === k));
    zeichneStreifen();
  });

  await laden();
  setInterval(laden, 30000);
}

async function laden() {
  const [v, d] = await Promise.all([hole('/api/verbindung'), hole('/api/diagnose/uebersicht')]);
  if (v) _daten.verbindung = v;
  if (d) _daten.diagnose = d;
  zeichneZustand();
  zeichneStreifen();
  zeichneZahlen();
  zeichneAusfaelle();
  zeichneEreignisse();
  if (!$('kontenTafel').hidden) {
    const k = await hole('/api/konten');
    if (k) { _daten.konten = k; zeichneKonten(); }
  }
  if (!$('wartungTafel').hidden) zeichneWartung();
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
  const v = _daten.verbindung;
  const feld = $('streifen');
  if (!v) return;
  const jetzt = Date.now() / 1000;
  const von = jetzt - _tage * 86400;
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

  const achse = $('streifenAchse');
  const schritte = _tage <= 7 ? 7 : (_tage <= 30 ? 6 : 6);
  let a = '';
  for (let i = 0; i <= schritte; i++) {
    const t = new Date((von + spanne * i / schritte) * 1000);
    a += `<span>${t.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' })}</span>`;
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
  $('ausfaelle').innerHTML = luecken.map(l => {
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
    return `<div class="konto">
      <div>
        <div class="k-name ${c.gesperrt ? 'gesperrt' : ''}">${esc(c.name)}${ich ? ' <span class="hinweis">(du)</span>' : ''}</div>
        <div class="k-rolle">${esc(r.name || c.rolle)}${c.gesperrt ? ' · gesperrt' : ''}</div>
        <div class="k-darf">${esc((r.handlungen || []).join(' · '))}</div>
      </div>
      <div class="k-tat">
        ${ich ? '' : `<button class="knopf stumm" onclick="kontoSperren('${esc(c.name)}', ${!c.gesperrt})">${c.gesperrt ? 'Entsperren' : 'Sperren'}</button>`}
        ${ich ? '' : `<button class="knopf warn" onclick="kontoLoeschen('${esc(c.name)}')">Löschen</button>`}
      </div>
    </div>`;
  }).join('') || '<div class="leerlauf">Noch kein Konto.</div>';
}

function kontoNeuOeffnen() {
  const rollen = (_daten.konten && _daten.konten.rollen) || [];
  popZeigen(`
    <div class="pop-titel">Konto anlegen</div>
    <label class="pop-feld"><span>Name</span>
      <input id="nkName" type="text" autocapitalize="none" autocorrect="off" spellcheck="false"></label>
    <label class="pop-feld"><span>Passwort (mindestens 8 Zeichen)</span>
      <input id="nkPw" type="text"></label>
    <label class="pop-feld"><span>Rolle</span>
      <select id="nkRolle">
        ${rollen.map(r => `<option value="${esc(r.schluessel)}">${esc(r.name)} — ${esc(r.handlungen.join(', '))}</option>`).join('')}
      </select></label>
    <div class="pop-fehler hidden" id="nkFehler"></div>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="kontoAnlegen()">Anlegen</button>
    </div>`);
}

async function kontoAnlegen() {
  const name = $('nkName').value.trim(), pw = $('nkPw').value, rolle = $('nkRolle').value;
  const fehler = $('nkFehler');
  const r = await fetch('/api/konten', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, passwort: pw, rolle }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    fehler.textContent = d.detail || 'Das hat nicht geklappt.';
    fehler.classList.remove('hidden');
    return;
  }
  popSchliessen();
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

// ── Fernwartung ────────────────────────────────────────────────────────────

function zeichneWartung() {
  const v = _daten.verbindung;
  const da = v && v.verbunden;
  $('wartung').innerHTML = `
    <div class="w-zeile">
      <div>
        <div class="w-text">Bordrechner aktualisieren</div>
        <div class="w-neben">Holt den neuesten Stand und startet die Anwendung neu.
          Dauert etwa eine halbe Minute, in der das Boot nicht antwortet.</div>
      </div>
      <button class="knopf" ${da ? '' : 'disabled'} onclick="fernUpdate()">Aktualisieren</button>
    </div>
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

start();

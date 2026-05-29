// ── Network overlay ────────────────────────────────────────────────────────

let netTimer = null;

function openNetwork() {
  _closeAllOverlays();
  history.pushState({ overlay: 'network' }, '', '#network');
  $('networkOverlay').classList.remove('hidden');
  fetchNetwork();
  netTimer = setInterval(fetchNetwork, 5000);
}
function closeNetwork() {
  $('networkOverlay').classList.add('hidden');
  clearInterval(netTimer); netTimer = null;
  history.replaceState(null, '', location.pathname);
}

async function fetchNetwork() {
  try {
    const data = await fetch('/api/network').then(r => r.json());
    renderNetworkInto($('netContent'), data);
  } catch(_) {
    const el = $('netContent');
    if (el) el.innerHTML = '<div class="net-empty">Keine Verbindung</div>';
  }
}

function renderNetworkInto(el, entries) {
  if (!el) return;
  if (!entries.length) {
    el.innerHTML = '<div class="net-empty">Noch keine Geräte erkannt — warte auf CAN-Daten…</div>';
    return;
  }

  const bySource = {};
  entries.forEach(e => {
    if (!bySource[e.src]) bySource[e.src] = [];
    bySource[e.src].push(e);
  });

  const cards = Object.entries(bySource).map(([src, pgns]) => {
    const srcNum  = parseInt(src);
    const srcHex  = '0x' + srcNum.toString(16).toUpperCase().padStart(2,'0');
    const name    = devicesConfig[src] ?? `Gerät ${srcHex}`;
    const minAge  = Math.min(...pgns.map(p => p.age_s));
    const dotCls  = minAge < 3 ? 'ok' : minAge < 15 ? 'warn' : 'old';

    const rows = pgns.map(p => {
      const iv  = p.interval_ms != null ? `${p.interval_ms} ms` : '—';
      const ageCls = p.age_s < 3 ? 'age-ok' : p.age_s < 15 ? 'age-warn' : 'age-old';
      const ageStr = p.age_s < 2 ? 'aktiv' : `${p.age_s} s`;
      return `<div class="net-pgn-row">
        <div>
          <div class="net-pgn-desc">${p.description}</div>
          <div class="net-pgn-num">${p.pgn} · 0x${p.pgn.toString(16).toUpperCase()}</div>
        </div>
        <div class="net-pgn-iv">${iv}</div>
        <div class="net-pgn-age ${ageCls}">${ageStr}</div>
      </div>`;
    }).join('');

    return `<div class="net-device-card">
      <div class="net-device-header">
        <div class="net-device-dot ${dotCls}"></div>
        <div class="net-device-name">${name}</div>
        <div class="net-device-src">${srcHex} (${srcNum})</div>
      </div>
      <div class="net-pgn-list">${rows}</div>
    </div>`;
  }).join('');

  el.innerHTML = `<div class="net-grid">${cards}</div>`;
}

// ── Alarme ─────────────────────────────────────────────────────────────────

let _rulesCache = {};

function openAlarms() {
  _closeAllOverlays();
  history.pushState({ overlay: 'alarms' }, '', '#alarms');
  $('alarmOverlay').classList.remove('hidden');
  _navActive('alarmBtn');
  switchTab('aktiv');
}
function closeAlarms() {
  $('alarmOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

function switchTab(name) {
  ['aktiv', 'regeln'].forEach(t => {
    $(`tab${t.charAt(0).toUpperCase()+t.slice(1)}`).classList.toggle('active', t === name);
    $(`tab${t.charAt(0).toUpperCase()+t.slice(1)}Btn`).classList.toggle('active', t === name);
  });
  if (name === 'regeln') loadRules();
}

function updateAlarmBadge(count) {
  const badge = $('alarmBadge');
  badge.textContent = count;
  badge.classList.toggle('hidden', count === 0);
}

function timeAgo(ts) {
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 60)  return `${s} s`;
  if (s < 3600) return `${Math.floor(s/60)} min`;
  return `${Math.floor(s/3600)} h`;
}

function renderAlarms(list) {
  const el = $('alarmList');
  const visible = list.filter(a => !a._deleted);
  if (!visible.length) {
    el.innerHTML = '<div class="alarm-empty">Keine Alarme ✓</div>';
    $('ackAllBtn').style.display = 'none';
    return;
  }
  const unacked = visible.filter(a => !a.acknowledged && !a.resolved).length;
  $('ackAllBtn').style.display = unacked ? '' : 'none';

  el.innerHTML = visible.map(a => {
    const cls = [a.severity, a.acknowledged ? 'acknowledged' : '', a.resolved ? 'resolved' : ''].filter(Boolean).join(' ');
    const opLabel = a.op === '<' ? 'unter' : 'über';
    const resolvedBadge = a.resolved ? '<div class="alarm-resolved-badge">✓ Behoben</div>' : '';
    const ackBtn = !a.acknowledged && !a.resolved
      ? `<button class="alarm-ack-btn" onclick="ackAlarm('${a.id}')">Bestätigen</button>` : '';
    return `<div class="alarm-card ${cls}" id="alarm-${a.id}">
      <div class="alarm-header">
        <div class="alarm-severity-dot"></div>
        <div class="alarm-name">${a.name}</div>
        <div class="alarm-time">${timeAgo(a.timestamp)}</div>
      </div>
      <div class="alarm-detail">
        Wert: <b>${a.value}</b> — Grenzwert ${opLabel} <b>${a.threshold}</b>
      </div>
      ${resolvedBadge}
      <div class="alarm-actions">
        ${ackBtn}
        <button class="alarm-del-btn" onclick="deleteAlarm('${a.id}')">Löschen</button>
      </div>
    </div>`;
  }).join('');
}

async function ackAlarm(id) {
  await fetch(`/api/alarms/${id}/ack`, { method: 'POST' });
  const card = $(`alarm-${id}`);
  if (card) card.classList.add('acknowledged');
}

async function deleteAlarm(id) {
  await fetch(`/api/alarms/${id}`, { method: 'DELETE' });
  const card = $(`alarm-${id}`);
  if (card) card.remove();
  if (!$('alarmList').querySelector('.alarm-card')) {
    $('alarmList').innerHTML = '<div class="alarm-empty">Keine Alarme ✓</div>';
  }
}

async function ackAllAlarms() {
  await fetch('/api/alarms/ack-all', { method: 'POST' });
  document.querySelectorAll('.alarm-card:not(.resolved)').forEach(c => c.classList.add('acknowledged'));
  $('ackAllBtn').style.display = 'none';
}

async function loadRules() {
  try {
    _rulesCache = await fetch('/api/alarms/rules').then(r => r.json());
    renderRules(_rulesCache);
  } catch(_) { $('ruleList').textContent = 'Fehler beim Laden'; }
}

let _ruleCat = null;

function _ruleCategory(key, r) {
  const f = r.field || '';
  if (key.startsWith('bms')     || f.startsWith('bms'))     return 'BMS';
  if (key.startsWith('tank')    || f.startsWith('tanks'))   return 'Tank';
  if (key.startsWith('network') || f.includes('network') || f.includes('can')) return 'System';
  if (key.startsWith('bat') || key.startsWith('starter') || f.startsWith('battery')) return 'Batterie';
  return 'Sonstige';
}

const _RULE_CAT_ORDER = ['Batterie', 'BMS', 'Tank', 'System', 'Sonstige'];

// Sichtbare Regel-Edits ins Cache übernehmen (vor Kategoriewechsel)
function _commitVisibleRules() {
  Object.keys(_rulesCache).forEach(key => {
    const r = _rulesCache[key];
    const en = $(`r_${key}_en`);
    if (en) r.enabled = en.checked;
    if (r.op === 'range') {
      const track = $(`dr_${key}`);
      if (track) { r.min = parseFloat(track.dataset.min); r.max = parseFloat(track.dataset.max); }
    } else {
      const thr = $(`r_${key}_thr`);
      if (thr && thr.value !== '') r.threshold = parseFloat(thr.value);
    }
  });
}

function switchRuleCat(cat) {
  _commitVisibleRules();
  _ruleCat = cat;
  renderRules(_rulesCache);
}

function renderRules(rules) {
  const entries = Object.entries(rules);
  const availableAll = entries.filter(([, r]) =>  r.data_available);
  const unavailable  = entries.filter(([, r]) => !r.data_available);

  // Kategorien aus verfügbaren Regeln bestimmen
  const catCounts = {};
  availableAll.forEach(([key, r]) => {
    const c = _ruleCategory(key, r);
    catCounts[c] = (catCounts[c] || 0) + 1;
  });
  const cats = _RULE_CAT_ORDER.filter(c => catCounts[c]);
  if (!_ruleCat || !catCounts[_ruleCat]) _ruleCat = cats[0] || null;

  // Kategorie-Navigation
  $('ruleCatNav').innerHTML = cats.map(c =>
    `<button class="rule-cat-btn ${c === _ruleCat ? 'active' : ''}" onclick="switchRuleCat('${c}')">
      ${c}<span class="rc-count">${catCounts[c]}</span>
    </button>`
  ).join('');

  const available = availableAll.filter(([key, r]) => _ruleCategory(key, r) === _ruleCat);

  $('ruleList').innerHTML = available.map(([key, r]) => {
    const noData   = !r.data_available;
    const sevLabel = r.severity === 'critical' ? 'Kritisch' : 'Warnung';
    const header   = `
      <div class="rule-header">
        <label class="rule-toggle">
          <input type="checkbox" id="r_${key}_en" ${r.enabled ? 'checked' : ''}>
          <span class="rule-slider"></span>
        </label>
        <span class="rule-name">${r.name}</span>
        <span class="rule-severity ${r.severity}">${sevLabel}</span>
      </div>`;
    const noDataRow = noData
      ? `<div class="rule-no-data">(keine Daten empfangen)</div>` : '';

    let control;
    if (r.op === 'range') {
      const unit = r.unit || '';
      const dec  = (r.step ?? 0.1) < 0.1 ? 2 : 1;
      control = `
        <div class="dr-wrap">
          <div class="dr-vals">
            <span id="dr_${key}_lo">${Number(r.min).toFixed(dec)} ${unit}</span>
            <span id="dr_${key}_hi">${Number(r.max).toFixed(dec)} ${unit}</span>
          </div>
          <div class="dr-track" id="dr_${key}"
               data-min="${r.min}" data-max="${r.max}"
               data-low="${r.bounds[0]}" data-high="${r.bounds[1]}"
               data-step="${r.step ?? 0.1}" data-unit="${unit}" data-dec="${dec}">
            <div class="dr-track-bg"></div>
            <div class="dr-fill" id="dr_${key}_fill"></div>
            <div class="dr-thumb dr-thumb-low" id="dr_${key}_tlo"></div>
            <div class="dr-thumb dr-thumb-high" id="dr_${key}_thi"></div>
          </div>
          <div class="dr-bounds">
            <span>${Number(r.bounds[0]).toFixed(dec)}</span>
            <span>${Number(r.bounds[1]).toFixed(dec)}</span>
          </div>
        </div>`;
    } else {
      const opLabel = r.op === '<' ? 'unter' : 'über';
      control = `
        <div class="rule-threshold-wrap" style="padding-left:46px">
          <span class="rule-op">${opLabel}</span>
          <input class="rule-input" id="r_${key}_thr" type="number"
                 step="${r.step ?? 1}" value="${r.threshold}">
        </div>`;
    }

    return `<div class="rule-row">${header}${control}</div>`;
  }).join('') + (unavailable.length ? `
    <div style="margin-top:12px;font-size:12px;color:var(--text3)">
      ${unavailable.length} Regel${unavailable.length > 1 ? 'n' : ''} ohne Daten ausgeblendet
      (${unavailable.map(([,r]) => r.name).join(', ')})
    </div>` : '');

  // init dual-range sliders after DOM insertion
  available.forEach(([key, r]) => {
    if (r.op === 'range') initDualRange(key, r);
  });
}

function initDualRange(key, r) {
  const track = $(`dr_${key}`);
  const fill  = $(`dr_${key}_fill`);
  const tlo   = $(`dr_${key}_tlo`);
  const thi   = $(`dr_${key}_thi`);
  const lblLo = $(`dr_${key}_lo`);
  const lblHi = $(`dr_${key}_hi`);
  if (!track) return;

  const low  = parseFloat(track.dataset.low);
  const high = parseFloat(track.dataset.high);
  const step = parseFloat(track.dataset.step);
  const unit = track.dataset.unit;
  const dec  = parseInt(track.dataset.dec);
  let minVal = parseFloat(track.dataset.min);
  let maxVal = parseFloat(track.dataset.max);

  // Each thumb is 44px wide with transform:translateX(-50%).
  // Setting left=X puts the thumb CENTER at X.
  // Center travels: 22px (f=0) → W-22px (f=1)  → left = calc(f*100% + 22*(1-2f)px)
  // Fill left edge = tlo center, fill right edge = thi center.
  // CSS right property = W - right_edge = W - (22 + fhi*(W-44)) = calc((1-fhi)*100% + (44*fhi-22)px)
  function toFrac(v)    { return (v - low) / (high - low); }
  function snap(v)      { return parseFloat((Math.round(v / step) * step).toFixed(dec)); }
  function thumbCSS(f)  { return `calc(${(f*100).toFixed(4)}% + ${(22*(1-2*f)).toFixed(2)}px)`; }
  function fillRightCSS(f){ return `calc(${((1-f)*100).toFixed(4)}% + ${(44*f-22).toFixed(2)}px)`; }

  function update() {
    const flo = toFrac(minVal);
    const fhi = toFrac(maxVal);
    tlo.style.left  = thumbCSS(flo);
    thi.style.left  = thumbCSS(fhi);
    fill.style.left  = thumbCSS(flo);
    fill.style.right = fillRightCSS(fhi);
    fill.style.width = '';          // use left+right instead of width
    lblLo.textContent = minVal.toFixed(dec) + ' ' + unit;
    lblHi.textContent = maxVal.toFixed(dec) + ' ' + unit;
    track.dataset.min = minVal;
    track.dataset.max = maxVal;
  }

  function getPct(clientX) {
    const rect = track.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - rect.left - 22) / (rect.width - 44)));
  }

  function drag(thumb, isLow) {
    function onMove(cx) {
      let val = snap(low + getPct(cx) * (high - low));
      val = Math.max(low, Math.min(high, val));
      if (isLow) minVal = Math.min(val, maxVal - step);
      else       maxVal = Math.max(val, minVal + step);
      update();
    }
    thumb.addEventListener('touchstart', e => {
      e.preventDefault();
      const mv = e2 => onMove(e2.touches[0].clientX);
      const up = () => { document.removeEventListener('touchmove', mv); document.removeEventListener('touchend', up); };
      document.addEventListener('touchmove', mv, { passive: false });
      document.addEventListener('touchend', up);
    }, { passive: false });
    thumb.addEventListener('mousedown', e => {
      e.preventDefault();
      const mv = e2 => onMove(e2.clientX);
      const up = () => { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); };
      document.addEventListener('mousemove', mv);
      document.addEventListener('mouseup', up);
    });
  }

  drag(tlo, true);
  drag(thi, false);
  update();
}

async function saveRules() {
  const fb = $('rulesFeedback');
  _commitVisibleRules();  // sichtbare Eingaben übernehmen (auch aus anderen Kategorien bereits committet)
  const updates = {};
  Object.keys(_rulesCache).forEach(key => {
    const r = _rulesCache[key];
    if (r.op === 'range') {
      updates[key] = { enabled: r.enabled, min: r.min, max: r.max };
    } else {
      updates[key] = { enabled: r.enabled, threshold: r.threshold };
    }
  });
  try {
    const data = await fetch('/api/alarms/rules', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    }).then(r => r.json());
    _rulesCache = data;
    fb.className = 'settings-feedback show';
    fb.textContent = 'Gespeichert ✓';
    setTimeout(() => fb.classList.remove('show'), 2500);
  } catch(_) {
    fb.className = 'settings-feedback error show';
    fb.textContent = 'Fehler beim Speichern';
  }
}

// ── Wartungsplan ───────────────────────────────────────────────────────────

const WARTUNG_DEFAULTS = [
  { id: 'motor', name: 'Motor', color: '#f97316', tasks: [
    { id: 'oelwechsel',      name: 'Ölwechsel',                  interval_days: 365, interval_label: 'jährlich',            material: 'Motoröl, Ölfilter',    last_done: null, history: [] },
    { id: 'dieselvorfilter', name: 'Dieselvorfilter wechseln',   interval_days: 365, interval_label: 'jährlich',            material: 'Dieselvorfilter',      last_done: null, history: [] },
    { id: 'dieselfilter',    name: 'Dieselfilter wechseln',      interval_days: 365, interval_label: 'jährlich',            material: 'Dieselfilter',         last_done: null, history: [] },
    { id: 'welle-fetten',    name: 'Welle fetten',               interval_days: 0,   interval_label: 'nach jeder Ausfahrt', material: 'Wellenfett',           last_done: null, history: [] },
    { id: 'luftfilter',      name: 'Luftfilter tauschen',        interval_days: 365, interval_label: 'jährlich',            material: 'Luftfilter',           last_done: null, history: [] },
    { id: 'impeller',        name: 'Impeller tauschen',          interval_days: 365, interval_label: 'jährlich',            material: 'Impeller',             last_done: null, history: [] },
  ]},
  { id: 'rigg', name: 'Rigg', color: '#3b82f6', tasks: [
    { id: 'winschen',       name: 'Winschen warten',         interval_days: 730, interval_label: 'alle 2 Jahre',   material: 'Winschwartungsöl', last_done: null, history: [] },
    { id: 'fallen',         name: 'Fallen checken',          interval_days: 180, interval_label: 'halbjährlich',   material: '',                 last_done: null, history: [] },
    { id: 'stehendes-gut',  name: 'Stehendes Gut checken',   interval_days: 180, interval_label: 'halbjährlich',   material: '',                 last_done: null, history: [] },
  ]},
  { id: 'ruder', name: 'Ruder', color: '#22c55e', tasks: [
    { id: 'ruderlager', name: 'Ruderlager fetten', interval_days: 0, interval_label: 'nach Fahrstunden', material: 'Fett', last_done: null, history: [] },
  ]},
  { id: 'wasser', name: 'Wassersystem', color: '#06b6d4', tasks: [
    { id: 'tank-check',   name: 'Tank checken',                     interval_days: 180, interval_label: 'halbjährlich', material: '',   last_done: null, history: [] },
    { id: 'wasserfilter', name: 'Filter vor Wasserpumpe säubern',   interval_days: 30,  interval_label: 'monatlich',    material: '',   last_done: null, history: [] },
  ]},
];

let WARTUNG_DATA = JSON.parse(JSON.stringify(WARTUNG_DEFAULTS));
let _wEditCatId  = null;
let _wEditTaskId = null;

function getWartungStatus(task) {
  if (task.interval_days === 0)
    return { status: 'manual', color: '#94a3b8', label: task.last_done ? fmtDate(task.last_done) : 'Noch nie', days: null };
  if (!task.last_done)
    return { status: 'overdue', color: '#ef4444', label: 'Noch nie erledigt', days: Infinity };
  const next = new Date(task.last_done);
  next.setDate(next.getDate() + task.interval_days);
  const today  = new Date(); today.setHours(0,0,0,0);
  const diffMs = next - today;
  const days   = Math.round(diffMs / 86400000);
  if (days < 0)   return { status: 'overdue',  color: '#ef4444', label: `${Math.abs(days)} T überfällig`, days };
  if (days <= (wartungConfig.due_soon_days ?? 7)) return { status: 'due_soon', color: '#f59e0b', label: `in ${days} T`, days };
  return              { status: 'ok',       color: '#22c55e', label: `in ${days} T`,                   days };
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

async function _wartungLoad() {
  try {
    const r = await fetch('/api/wartung');
    if (r.ok) {
      const d = await r.json();
      if (Array.isArray(d) && d.length) WARTUNG_DATA = d;
    }
  } catch(_) {}
  updateWartungTopbar();
  updateWartungHomeTile();
}

async function _wartungSave() {
  try {
    await fetch('/api/wartung', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(WARTUNG_DATA),
    });
  } catch(e) { console.error('Wartung speichern:', e); }
  updateWartungTopbar();
  updateWartungHomeTile();
}

function updateWartungTopbar() {
  const btn = $('wartungTopBtn');
  if (!btn) return;
  const allTasks = WARTUNG_DATA.flatMap(c => c.tasks);
  const overdue  = allTasks.filter(t => getWartungStatus(t).status === 'overdue').length;
  const dueSoon  = allTasks.filter(t => getWartungStatus(t).status === 'due_soon').length;
  if (overdue > 0) {
    btn.style.color = '#ef4444';
    btn.classList.add('w-blink');
  } else if (dueSoon > 0) {
    btn.style.color = '#f59e0b';
    btn.classList.remove('w-blink');
  } else {
    btn.style.color = 'var(--text3)';
    btn.classList.remove('w-blink');
  }
}

// Gerade Fortschrittsleiste: grün = aktuell, gelb = demnächst, rot = überfällig.
function _renderWartungBar(ok, dueSoon, overdue) {
  const bar = $('wartungProgress');
  if (!bar) return;
  const total = ok + dueSoon + overdue;
  if (total === 0) { bar.innerHTML = '<div class="w-prog-seg" style="flex:1;background:var(--surface2)"></div>'; return; }
  const seg = (n, c) => n > 0 ? `<div class="w-prog-seg" style="flex:${n};background:${c}"></div>` : '';
  bar.innerHTML = seg(ok, '#22c55e') + seg(dueSoon, '#f59e0b') + seg(overdue, '#ef4444');
}

function updateWartungHomeTile() {
  const body = $('wartungHomeBody');
  if (!body) return;

  const tasks = [];
  WARTUNG_DATA.forEach(cat => {
    cat.tasks.forEach(t => tasks.push({ ...t, catColor: cat.color, catName: cat.name }));
  });

  const withStatus = tasks.map(t => ({ t, s: getWartungStatus(t) }));
  const scheduled  = withStatus.filter(x => x.s.status !== 'manual');
  const overdue    = scheduled.filter(x => x.s.status === 'overdue').length;
  const dueSoon    = scheduled.filter(x => x.s.status === 'due_soon').length;
  const okCount    = scheduled.filter(x => x.s.status === 'ok').length;
  const pending    = scheduled
    .filter(x => x.s.status === 'overdue' || x.s.status === 'due_soon')
    .sort((a, b) => (a.s.days ?? Infinity) - (b.s.days ?? Infinity));

  _renderWartungBar(okCount, dueSoon, overdue);

  const card = $('wartungCard');

  if (!pending.length) {
    if (card) card.style.borderColor = '';
    body.innerHTML = `<div style="display:flex;align-items:center;gap:8px;color:#22c55e;font-size:13px;font-weight:600">
      <span style="width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block"></span>
      Alle Wartungen aktuell
    </div>`;
    if (typeof setStatusWartung === 'function') setStatusWartung(0, 0);
    return;
  }

  if (typeof setStatusWartung === 'function') setStatusWartung(overdue, pending.length);

  let html = '<div class="w-home-badge">';
  if (overdue > 0) html += `<span class="w-badge-pill" style="background:#ef44441a;color:#ef4444">${overdue} überfällig</span>`;
  if (dueSoon > 0) html += `<span class="w-badge-pill" style="background:#f59e0b1a;color:#f59e0b">${dueSoon} demnächst</span>`;
  html += '</div>';

  pending.forEach(({ t, s }) => {
    html += `<div class="w-home-row">
      <span style="display:flex;align-items:center;gap:6px;min-width:0;overflow:hidden">
        <span style="width:7px;height:7px;border-radius:50%;background:${s.color};display:inline-block;flex-shrink:0"></span>
        <span class="w-home-cat" style="background:${t.catColor}28">${t.catName}</span>
        <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${t.name}</span>
      </span>
      <span style="color:${s.color};font-size:12px;font-weight:600;flex-shrink:0;margin-left:8px">${s.label}</span>
    </div>`;
  });

  if (card) card.style.borderColor = overdue > 0 ? '#ef4444' : '#f59e0b';
  const burgerWart = $('burgerWartungBtn');
  if (burgerWart) burgerWart.style.color = overdue > 0 ? 'var(--red)' : '';
  body.innerHTML = html;
  // Doppeltes rAF: sicherstellt dass Layout vollständig berechnet ist
  requestAnimationFrame(() => requestAnimationFrame(() => _trimWartungRows(body, card)));
}

async function openWartung() {
  _closeAllOverlays();
  history.pushState({ overlay: 'wartung' }, '', '#wartung');
  $('wartungOverlay').classList.remove('hidden');
  _navActive('wartungTopBtn');
  await _wartungLoad();
  renderWartung();
}

function closeWartung() {
  $('wartungOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

function renderWartung() {
  const body = $('wartungBody');
  let html = '';
  WARTUNG_DATA.forEach(cat => {
    html += `<div class="w-cat">
      <div class="w-cat-hd" style="border-color:${cat.color};color:${cat.color}">
        <span class="w-dot" style="background:${cat.color}"></span>${cat.name}
        <button onclick="event.stopPropagation();deleteWartungCat('${cat.id}')" title="Kategorie löschen"
          style="margin-left:auto;background:none;border:none;color:var(--text3);cursor:pointer;padding:2px 6px;font-size:13px;line-height:1">✕</button>
      </div>`;
    cat.tasks.forEach(task => {
      const s = getWartungStatus(task);
      const lastStr = task.last_done ? fmtDate(task.last_done) : 'Noch nie';
      html += `<div class="w-task" onclick="openWartungTask('${cat.id}','${task.id}')">
        <span class="w-status-dot" style="background:${s.color}"></span>
        <span class="w-task-name">${task.name}</span>
        <span class="w-task-interval">${task.interval_label}</span>
        <span class="w-task-due" style="color:${s.color}">${s.label}</span>
      </div>`;
    });
    html += '</div>';
  });
  body.innerHTML = html;
}

function exportWartungCsv() {
  const sep = ';';
  const esc = v => {
    const s = String(v ?? '');
    return /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const rows = [['Kategorie','Aufgabe','Intervall','Intervall (Tage)','Material','Zuletzt erledigt','Nächste Fälligkeit','Status','Notiz (letzte)']];
  WARTUNG_DATA.forEach(cat => {
    cat.tasks.forEach(t => {
      const s = getWartungStatus(t);
      let next = '';
      if (t.interval_days > 0 && t.last_done) {
        const d = new Date(t.last_done); d.setDate(d.getDate() + t.interval_days);
        next = d.toISOString().slice(0,10);
      }
      const statusTxt = { overdue:'überfällig', due_soon:'bald fällig', ok:'ok', manual:'manuell' }[s.status] || s.status;
      const lastNote = (t.history && t.history.length) ? t.history[t.history.length-1].notes : '';
      rows.push([cat.name, t.name, t.interval_label, t.interval_days || '', t.material || '',
                 t.last_done || '', next, statusTxt, lastNote]);
    });
  });
  const csv = '﻿' + rows.map(r => r.map(esc).join(sep)).join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url;
  a.download = `wartungsplan_${todayISO()}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

function _wEditPopulateCats(selectedId) {
  $('wEditCat').innerHTML = WARTUNG_DATA.map(c =>
    `<option value="${c.id}"${c.id === selectedId ? ' selected' : ''}>${c.name}</option>`
  ).join('');
}

function openWartungTask(catId, taskId) {
  const cat  = WARTUNG_DATA.find(c => c.id === catId);
  const task = cat?.tasks.find(t => t.id === taskId);
  if (!task) return;
  _wEditCatId  = catId;
  _wEditTaskId = taskId;

  $('wEditDot').style.background = cat.color;
  $('wEditTitle').textContent     = task.name;
  $('wEditInterval').textContent  = task.interval_label + (task.material ? ` · ${task.material}` : '');
  $('wEditDate').value   = todayISO();
  $('wEditNotes').value  = '';
  _wEditPopulateCats(catId);
  $('wEditName').value     = task.name;
  $('wEditMaterial').value = task.material || '';
  _setWartungIntervalUI(task.interval_days, task.interval_label);
  $('wEditDoneBlock').style.display = '';
  $('wEditDetails').open = true;   // Bearbeiten/Löschen direkt sichtbar
  $('wDeleteBtn') && ($('wDeleteBtn').style.display = '');

  // Logbuch — alle Einträge, neueste zuerst
  const hist = (task.history || []).slice().reverse();
  $('wEditHistory').innerHTML = hist.length
    ? `<div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px;color:var(--text3);display:flex;justify-content:space-between">
         <span>Logbuch</span><span>${hist.length} Einträge</span>
       </div>
       <div style="max-height:200px;overflow-y:auto">` +
      hist.map(h => `<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--surface2)">
        <span style="color:var(--text2);white-space:nowrap;font-variant-numeric:tabular-nums">${fmtDate(h.date)}</span>
        <span style="color:var(--text3);flex:1">${h.notes ? _esc(h.notes) : '<span style=\"opacity:.5\">—</span>'}</span>
      </div>`).join('') +
      `</div>`
    : '<div style="color:var(--text3);font-size:12px">Noch kein Logbuch-Eintrag</div>';

  $('wartungEditBg').style.display    = 'block';
  $('wartungEditSheet').style.display = 'block';
}

function _setWartungIntervalUI(days, label) {
  const sel = $('wEditIntervalPreset');
  const custom = $('wEditIntervalCustom');
  if (!sel) return;
  const match = Array.from(sel.options).find(o => o.value === `${days}|${label}`);
  if (match) {
    sel.value = match.value;
    if (custom) custom.style.display = 'none';
  } else {
    sel.value = 'custom';
    if (custom) custom.style.display = 'grid';
    if ($('wEditDays'))          $('wEditDays').value = days;
    if ($('wEditIntervalLabel')) $('wEditIntervalLabel').value = label;
  }
}

function applyWartungPreset() {
  const val = $('wEditIntervalPreset')?.value;
  const custom = $('wEditIntervalCustom');
  if (!val) return;
  if (val === 'custom') {
    if (custom) custom.style.display = 'grid';
  } else {
    if (custom) custom.style.display = 'none';
  }
}

function _getWartungIntervalValues() {
  const val = $('wEditIntervalPreset')?.value || 'custom';
  if (val !== 'custom') {
    const [days, ...labelParts] = val.split('|');
    return { days: parseInt(days), label: labelParts.join('|') };
  }
  return {
    days:  parseInt($('wEditDays')?.value) || 0,
    label: $('wEditIntervalLabel')?.value.trim() || 'manuell',
  };
}

function openWartungNew() {
  _wEditCatId  = WARTUNG_DATA[0]?.id || null;
  _wEditTaskId = null;
  $('wEditDot').style.background = WARTUNG_DATA[0]?.color || '#888';
  $('wEditTitle').textContent    = 'Neue Aufgabe';
  $('wEditInterval').textContent = '';
  _wEditPopulateCats(_wEditCatId);
  $('wEditName').value          = '';
  $('wEditMaterial').value      = '';
  _setWartungIntervalUI(365, 'jährlich');
  $('wEditDoneBlock').style.display = 'none';  // erst nach Anlegen erledigbar
  $('wEditDetails').open = true;
  $('wDeleteBtn') && ($('wDeleteBtn').style.display = 'none');
  $('wEditHistory').innerHTML = '';
  $('wartungEditBg').style.display    = 'block';
  $('wartungEditSheet').style.display = 'block';
  setTimeout(() => $('wEditName').focus(), 80);
}

function closeWartungEdit() {
  $('wartungEditBg').style.display    = 'none';
  $('wartungEditSheet').style.display = 'none';
}

function saveWartungDone() {
  const cat  = WARTUNG_DATA.find(c => c.id === _wEditCatId);
  const task = cat?.tasks.find(t => t.id === _wEditTaskId);
  if (!task) return;
  const date  = $('wEditDate').value || todayISO();
  const notes = $('wEditNotes').value.trim();
  task.last_done = date;
  if (!task.history) task.history = [];
  task.history.push({ date, notes });
  closeWartungEdit();
  _wartungSave();
  renderWartung();
}

function _slugify(s) {
  return s.toLowerCase().replace(/[äöü]/g, m => ({'ä':'ae','ö':'oe','ü':'ue'}[m]))
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'task';
}

function saveWartungTask() {
  const name = $('wEditName').value.trim();
  if (!name) { $('wEditName').focus(); return; }
  const targetCatId   = $('wEditCat').value;
  const targetCat     = WARTUNG_DATA.find(c => c.id === targetCatId);
  if (!targetCat) return;
  const material      = $('wEditMaterial').value.trim();
  const { days: intervalDays, label: intervalLabel } = _getWartungIntervalValues();

  if (_wEditTaskId === null) {
    // Neue Aufgabe
    let id = _slugify(name);
    const existing = new Set(WARTUNG_DATA.flatMap(c => c.tasks.map(t => t.id)));
    while (existing.has(id)) id += '-2';
    targetCat.tasks.push({
      id, name, interval_days: intervalDays, interval_label: intervalLabel,
      material, last_done: null, history: [],
    });
  } else {
    // Bestehende Aufgabe bearbeiten (ggf. Kategorie wechseln)
    const srcCat = WARTUNG_DATA.find(c => c.id === _wEditCatId);
    const task   = srcCat?.tasks.find(t => t.id === _wEditTaskId);
    if (!task) return;
    task.name = name; task.material = material;
    task.interval_days = intervalDays; task.interval_label = intervalLabel;
    if (targetCatId !== _wEditCatId) {
      srcCat.tasks = srcCat.tasks.filter(t => t.id !== _wEditTaskId);
      targetCat.tasks.push(task);
    }
  }
  closeWartungEdit();
  _wartungSave();
  renderWartung();
}

function deleteWartungTask() {
  if (_wEditTaskId === null) { closeWartungEdit(); return; }
  const cat = WARTUNG_DATA.find(c => c.id === _wEditCatId);
  if (!cat) return;
  const task = cat.tasks.find(t => t.id === _wEditTaskId);
  if (!confirm(`"${task?.name}" löschen?`)) return;
  cat.tasks = cat.tasks.filter(t => t.id !== _wEditTaskId);
  closeWartungEdit();
  _wartungSave();
  renderWartung();
}

const _WARTUNG_COLORS = ['#f97316','#3b82f6','#22c55e','#06b6d4','#a78bfa','#ec4899','#eab308','#14b8a6'];

function addWartungCat() {
  const name = (prompt('Name der neuen Kategorie:') || '').trim();
  if (!name) return;
  let id = _slugify(name);
  const existing = new Set(WARTUNG_DATA.map(c => c.id));
  while (existing.has(id)) id += '-2';
  const color = _WARTUNG_COLORS[WARTUNG_DATA.length % _WARTUNG_COLORS.length];
  WARTUNG_DATA.push({ id, name, color, tasks: [] });
  _wartungSave();
  renderWartung();
}

function deleteWartungCat(catId) {
  const cat = WARTUNG_DATA.find(c => c.id === catId);
  if (!cat) return;
  const n = cat.tasks.length;
  const msg = n
    ? `Kategorie "${cat.name}" mit ${n} Aufgabe(n) löschen?`
    : `Kategorie "${cat.name}" löschen?`;
  if (!confirm(msg)) return;
  WARTUNG_DATA = WARTUNG_DATA.filter(c => c.id !== catId);
  _wartungSave();
  renderWartung();
}

function _trimWartungRows(body, card) {
  if (!body || !card) return;
  if (card.classList.contains('tile--half')) return;  // half: CSS begrenzt auf 1 Zeile
  // clientHeight ist relativ zur Karte selbst — kein Viewport-/Scroll-Problem
  const cardH = card.clientHeight;
  if (cardH < 20) return;  // noch nicht gerendert
  const rows = [...body.querySelectorAll('.w-home-row')];
  if (!rows.length) return;

  const cardTop = card.getBoundingClientRect().top;
  // 6px Buffer: entfernt Zeilen die auch nur minimal aus dem sichtbaren Bereich ragen
  const maxBottom = cardH - 6;

  let cutFrom = rows.length;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i].getBoundingClientRect();
    // Position relativ zur Kartenkante (unabhängig vom Scroll)
    if ((r.top - cardTop) + r.height > maxBottom) {
      cutFrom = i;
      break;
    }
  }

  if (cutFrom < rows.length) {
    const hidden = rows.length - cutFrom;
    for (let i = rows.length - 1; i >= cutFrom; i--) rows[i].remove();
    const el = document.createElement('div');
    el.style.cssText = 'font-size:11px;color:var(--text3);padding:4px 0 0;text-align:right';
    el.textContent = `+${hidden} weitere →`;
    body.appendChild(el);
  }
}

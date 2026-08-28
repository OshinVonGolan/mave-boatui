// ── Monday ─────────────────────────────────────────────────────────────────

let _mondayData        = null;
let _mondayFilter      = '';
let _mondayLabelFilter = {};   // { columnId: index }
let _mondayOnlyOpen    = false;

function _mondayItemDone(item) {
  return Object.values(item.columns || {}).some(cv =>
    /fertig|erledigt|done|abgeschlossen|completed|fixed/i.test(cv.text || ''));
}

function _mondayFiltersActive() {
  return !!_mondayFilter || _mondayOnlyOpen || Object.keys(_mondayLabelFilter).length > 0;
}

function toggleMondayOnlyOpen() {
  _mondayOnlyOpen = !_mondayOnlyOpen;
  $('mondayOnlyOpenBtn').classList.toggle('active', _mondayOnlyOpen);
  if (_mondayData) renderMondayBoard(_mondayData);
}

function resetMondayFilters() {
  _mondayFilter = '';
  _mondayLabelFilter = {};
  _mondayOnlyOpen = false;
  const si = $('mondaySearchInput'); if (si) si.value = '';
  $('mondayOnlyOpenBtn')?.classList.remove('active');
  renderMondayLabelFilters();
  if (_mondayData) renderMondayBoard(_mondayData);
}

function openMonday() {
  _closeAllOverlays();
  history.pushState({ overlay: 'monday' }, '', '#monday');
  $('mondayOverlay').classList.remove('hidden');
  _navActive('mondayBtn');
  if (!_mondayData) loadMondayBoard();
  else renderMondayBoard(_mondayData);
}

function closeMonday() {
  $('mondayOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

async function loadMondayBoard() {
  const btn  = $('mondayRefreshBtn');
  const body = $('mondayBody');
  if (btn) { btn.classList.add('spinning'); btn.disabled = true; }
  body.innerHTML = '<div class="monday-empty">Lade Aufgaben…</div>';
  let daten;
  // Nur das Holen der Daten fällt unter "Verbindung fehlgeschlagen". Vorher lag
  // auch das Rendern im selben try — ein Fehler beim Zeichnen meldete dann eine
  // Verbindungsstörung, obwohl die Daten längst da waren.
  try {
    const r = await fetch('/api/monday/board');
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      body.innerHTML = `<div class="monday-empty" style="color:var(--red)">${_esc(err.detail || 'Fehler beim Laden')}</div>`;
      return;
    }
    daten = await r.json();
  } catch (e) {
    body.innerHTML = `<div class="monday-empty" style="color:var(--red)">Verbindung fehlgeschlagen</div>`;
    return;
  } finally {
    if (btn) { btn.classList.remove('spinning'); btn.disabled = false; }
  }

  _mondayData = daten;
  try {
    renderMondayLabelFilters();
    renderMondayBoard(_mondayData);
    const titleEl = $('mondayBoardTitle');
    const last    = titleEl?.childNodes[titleEl.childNodes.length - 1];
    if (last && _mondayData.name) last.textContent = ' ' + _mondayData.name;
  } catch (e) {
    console.error('Monday-Board zeichnen:', e);
    body.innerHTML = `<div class="monday-empty" style="color:var(--red)">Anzeige fehlgeschlagen</div>`;
  }
}

function filterMonday(q) {
  _mondayFilter = (q ?? '').trim().toLowerCase();
  if (_mondayData) renderMondayBoard(_mondayData);
}

function renderMondayLabelFilters() {
  const wrap = $('mondayLabelFilters');
  if (!wrap) return;
  const cols = _mondayData?.status_columns || [];
  _mondayLabelFilter = {};
  wrap.innerHTML = cols.map(col => `
    <select onchange="setMondayLabelFilter(${_jsAttr(col.id)}, this.value)"
            style="background:var(--surface2);color:var(--text1);border:1px solid var(--border);border-radius:8px;padding:7px 10px;font-size:12px">
      <option value="">${_esc(col.title)}: Alle</option>
      ${col.options.map(o => `<option value="${_esc(o.index)}">${_esc(col.title)}: ${_esc(o.label)}</option>`).join('')}
    </select>`).join('');
}

function setMondayLabelFilter(colId, index) {
  if (index === '') delete _mondayLabelFilter[colId];
  else _mondayLabelFilter[colId] = index;
  if (_mondayData) renderMondayBoard(_mondayData);
}

// Kein zusätzlicher input-Listener: das Suchfeld ruft filterMonday() bereits
// per inline oninput auf (index.html). Beides zusammen zeichnete das Board bei
// jedem Tastendruck zweimal — auf dem Pi Zero deutlich spürbar.

function toggleMondayNewForm() {
  const form = $('mondayNewForm');
  const open = form.style.display !== 'none';
  if (!open && _mondayData?.groups?.length) {
    $('mondayNewGroup').innerHTML = _mondayData.groups.map(g =>
      `<option value="${_esc(g.id)}">${_esc(g.title)}</option>`
    ).join('');
    $('mondayNewName').value = '';
    // Populate status column selects dynamically
    const statusRow = $('mondayNewStatusRow');
    statusRow.innerHTML = (_mondayData.status_columns || []).map(col => `
      <select id="mondayNewCol_${_esc(col.id)}"
              style="flex:1;min-width:130px;background:var(--surface2);color:var(--text1);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:13px"
              title="${_esc(col.title)}">
        <option value="">— ${_esc(col.title)} —</option>
        ${col.options.map(o =>
          `<option value="${_esc(o.label)}">${_esc(o.label)}</option>`
        ).join('')}
      </select>`).join('');
    setTimeout(() => $('mondayNewName').focus(), 50);
  }
  form.style.display = open ? 'none' : '';
}

async function createMondayItem() {
  const name = $('mondayNewName').value.trim();
  const groupId = $('mondayNewGroup').value;
  if (!name) { $('mondayNewName').focus(); return; }

  // Collect status column values
  const columnValues = {};
  (_mondayData?.status_columns || []).forEach(col => {
    const sel = $(`mondayNewCol_${col.id}`);
    if (sel?.value) columnValues[col.id] = { label: sel.value };
  });

  const btn = $('mondayCreateBtn');
  btn.disabled = true; btn.textContent = '…';
  try {
    const r = await fetch('/api/monday/item', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        group_id: groupId,
        name,
        column_values: Object.keys(columnValues).length ? columnValues : null,
      }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      alert('Fehler: ' + (e.detail || r.statusText));
      return;
    }
    const newItem = await r.json();
    const group = _mondayData?.groups?.find(g => g.id === groupId);
    if (group && newItem.id) {
      // Parse column_values from API response into the same format as get_board
      const cols = {};
      (newItem.column_values || []).forEach(cv => {
        const statusCols = _mondayData?.status_columns || [];
        if (statusCols.some(sc => sc.id === cv.id)) {
          let idx = '';
          try { idx = String(JSON.parse(cv.value || '{}').index ?? ''); } catch(_) {}
          cols[cv.id] = { text: cv.text || '', index: idx };
        }
      });
      group.items.unshift({ id: newItem.id, name: newItem.name || name, columns: cols });
    }
    toggleMondayNewForm();
    renderMondayBoard(_mondayData);
  } catch (e) {
    alert('Verbindungsfehler');
  } finally {
    btn.disabled = false; btn.textContent = 'Anlegen';
  }
}

function renderMondayBoard(data) {
  const body = $('mondayBody');
  if (!data || !data.groups?.length) {
    body.innerHTML = '<div class="monday-empty">Keine Aufgaben gefunden</div>';
    return;
  }
  const statusCols  = {};
  (data.status_columns || []).forEach(col => { statusCols[col.id] = col; });
  const primaryColId = data.status_columns?.[0]?.id;
  const fq = _mondayFilter;

  const labelFilters = Object.entries(_mondayLabelFilter);
  $('mondayResetBtn').style.display = _mondayFiltersActive() ? '' : 'none';

  let html = '';
  data.groups.forEach(group => {
    const visible = (group.items || []).filter(i => {
      if (fq && !i.name.toLowerCase().includes(fq)) return false;
      if (_mondayOnlyOpen && _mondayItemDone(i)) return false;
      // Label-Filter: alle gesetzten Spalten müssen passen
      for (const [colId, idx] of labelFilters) {
        if ((i.columns?.[colId]?.index ?? '') !== idx) return false;
      }
      return true;
    });
    if (!visible.length) return;
    const dot = group.color ? `<span class="monday-group-dot" style="background:${_esc(group.color)}"></span>` : '';
    html += `<div class="monday-group-hd">${dot}${_esc(group.title)} <span style="color:var(--text3);font-weight:400;margin-left:4px">${visible.length}</span></div>`;
    visible.forEach(item => {
      let pillsHtml = '';
      Object.entries(item.columns || {}).forEach(([colId, cv]) => {
        const col = statusCols[colId]; if (!col) return;
        const opt   = col.options.find(o => o.index === cv.index);
        const color = opt?.color || '#c4c4c4';
        const empty = !cv.text;
        pillsHtml += `<span class="monday-status-pill${empty ? ' empty' : ''}"
          style="background:${empty ? '' : _esc(color)}"
          onclick="openStatusPicker(event,${_jsAttr(item.id)},${_jsAttr(colId)},${_jsAttr(col.options)},${_jsAttr(cv.index ?? '')})"
          >${_esc(cv.text || '–')}</span>`;
      });
      if (!pillsHtml && primaryColId) {
        pillsHtml = `<span class="monday-status-pill empty"
          onclick="openStatusPicker(event,${_jsAttr(item.id)},${_jsAttr(primaryColId)},${_jsAttr(statusCols[primaryColId]?.options || [])},'')"
          >–</span>`;
      }
      html += `<div class="monday-item" data-item-id="${_esc(item.id)}">
        <span class="monday-item-name">${_esc(item.name)}</span>${pillsHtml}
      </div>`;
    });
  });
  body.innerHTML = html || `<div class="monday-empty">${fq ? 'Keine Treffer' : 'Keine Aufgaben'}</div>`;
}

// _esc() stand hier ein zweites Mal — zeichengleich mit der Fassung in
// core.js. Alle Bundle-Dateien teilen EINEN Scope, die spaetere Deklaration
// gewinnt also bundleweit. Solange beide gleich sind faellt das nicht auf;
// wer nur eine anpasst, aendert unbemerkt das Verhalten aller Aufrufer.
// Es gibt jetzt nur noch die eine in core.js.

// Wert als JS-Literal in ein inline-Attribut (onclick, onmouseenter …) setzen.
// Ein Attribut wird erst HTML-entschlüsselt und dann als JavaScript gelesen —
// reines _esc() reicht dort nicht, ein Apostroph im Text bricht sonst aus dem
// String aus. Deshalb erst JSON (schließt Anführungszeichen und Backslashes),
// danach HTML-Escape.
function _jsAttr(value) {
  return _esc(JSON.stringify(value ?? null));
}

function openStatusPicker(event, itemId, columnId, options, currentIndex) {
  event.stopPropagation();
  const picker = $('statusPicker');
  const bg     = $('statusPickerBg');
  picker.innerHTML = options.map(opt => {
    const active = opt.index === currentIndex;
    return `<div class="status-picker-item" onclick="applyStatus(${_jsAttr(itemId)},${_jsAttr(columnId)},${_jsAttr(opt.index)},${_jsAttr(opt.label)})">
      <span class="status-picker-dot" style="background:${_esc(opt.color)}"></span>
      <span style="${active ? 'font-weight:700' : ''}">${_esc(opt.label)}</span>
      ${active ? `<span style="margin-left:auto;color:var(--text3);display:inline-flex">${icon('check', { size: 13 })}</span>` : ''}
    </div>`;
  }).join('');

  // Position near the clicked pill
  const rect = event.currentTarget.getBoundingClientRect();
  picker.style.display = '';
  bg.style.display = '';
  const ph = picker.offsetHeight || 200;
  const top = rect.bottom + 6 + ph > window.innerHeight
    ? Math.max(8, rect.top - ph - 4)
    : rect.bottom + 4;
  const left = Math.min(rect.left, window.innerWidth - 180);
  picker.style.top  = top  + 'px';
  picker.style.left = left + 'px';
}

function closeStatusPicker() {
  $('statusPicker').style.display = 'none';
  $('statusPickerBg').style.display = 'none';
}

async function applyStatus(itemId, columnId, index, label) {
  closeStatusPicker();
  // Optimistically update the pill in the DOM
  const itemEl = document.querySelector(`[data-item-id="${itemId}"]`);
  const pill   = itemEl?.querySelector('.monday-status-pill');
  if (pill) { pill.textContent = label; pill.classList.remove('empty'); }
  // Update cached data
  if (_mondayData) {
    for (const g of _mondayData.groups) {
      const it = g.items.find(i => i.id === itemId);
      if (it && it.columns[columnId] !== undefined) {
        it.columns[columnId].text  = label;
        it.columns[columnId].index = index;
      }
    }
  }
  try {
    const r = await fetch(`/api/monday/item/${itemId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ column_id: columnId, label }),
    });
    if (!r.ok) {
      console.error('Monday status update failed:', await r.text());
      // Reload to get correct state
      _mondayData = null;
      loadMondayBoard();
    }
  } catch (e) {
    console.error('Monday status update error:', e);
  }
}

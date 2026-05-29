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
  try {
    const r = await fetch('/api/monday/board');
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      body.innerHTML = `<div class="monday-empty" style="color:var(--red)">${err.detail || 'Fehler beim Laden'}</div>`;
      return;
    }
    _mondayData = await r.json();
    renderMondayLabelFilters();
    renderMondayBoard(_mondayData);
    const titleEl = $('mondayBoardTitle');
    if (titleEl && _mondayData.name) titleEl.childNodes[titleEl.childNodes.length - 1].textContent = ' ' + _mondayData.name;
  } catch (e) {
    body.innerHTML = `<div class="monday-empty" style="color:var(--red)">Verbindung fehlgeschlagen</div>`;
  } finally {
    if (btn) { btn.classList.remove('spinning'); btn.disabled = false; }
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
    <select onchange="setMondayLabelFilter('${col.id}', this.value)"
            style="background:var(--surface2);color:var(--text1);border:1px solid var(--border);border-radius:8px;padding:7px 10px;font-size:12px">
      <option value="">${_esc(col.title)}: Alle</option>
      ${col.options.map(o => `<option value="${o.index}">${_esc(col.title)}: ${_esc(o.label)}</option>`).join('')}
    </select>`).join('');
}

function setMondayLabelFilter(colId, index) {
  if (index === '') delete _mondayLabelFilter[colId];
  else _mondayLabelFilter[colId] = index;
  if (_mondayData) renderMondayBoard(_mondayData);
}

// Robuster Event-Listener als Fallback (falls oninput-Inline nicht greift)
document.addEventListener('DOMContentLoaded', () => {
  const el = $('mondaySearchInput');
  if (el) el.addEventListener('input', e => filterMonday(e.target.value));
});

function toggleMondayNewForm() {
  const form = $('mondayNewForm');
  const open = form.style.display !== 'none';
  if (!open && _mondayData?.groups?.length) {
    $('mondayNewGroup').innerHTML = _mondayData.groups.map(g =>
      `<option value="${g.id}">${_esc(g.title)}</option>`
    ).join('');
    $('mondayNewName').value = '';
    // Populate status column selects dynamically
    const statusRow = $('mondayNewStatusRow');
    statusRow.innerHTML = (_mondayData.status_columns || []).map(col => `
      <select id="mondayNewCol_${col.id}"
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
    const dot = group.color ? `<span class="monday-group-dot" style="background:${group.color}"></span>` : '';
    html += `<div class="monday-group-hd">${dot}${_esc(group.title)} <span style="color:var(--text3);font-weight:400;margin-left:4px">${visible.length}</span></div>`;
    visible.forEach(item => {
      let pillsHtml = '';
      Object.entries(item.columns || {}).forEach(([colId, cv]) => {
        const col = statusCols[colId]; if (!col) return;
        const opt   = col.options.find(o => o.index === cv.index);
        const color = opt?.color || '#c4c4c4';
        const empty = !cv.text;
        pillsHtml += `<span class="monday-status-pill${empty ? ' empty' : ''}"
          style="background:${empty ? '' : color}"
          onclick="openStatusPicker(event,'${item.id}','${colId}',${JSON.stringify(col.options).replace(/"/g,'&quot;')},'${cv.index}')"
          >${_esc(cv.text || '–')}</span>`;
      });
      if (!pillsHtml && primaryColId) {
        pillsHtml = `<span class="monday-status-pill empty"
          onclick="openStatusPicker(event,'${item.id}','${primaryColId}',${JSON.stringify(statusCols[primaryColId]?.options||[]).replace(/"/g,'&quot;')},'')"
          >–</span>`;
      }
      html += `<div class="monday-item" data-item-id="${item.id}">
        <span class="monday-item-name">${_esc(item.name)}</span>${pillsHtml}
      </div>`;
    });
  });
  body.innerHTML = html || `<div class="monday-empty">${fq ? 'Keine Treffer' : 'Keine Aufgaben'}</div>`;
}

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function openStatusPicker(event, itemId, columnId, options, currentIndex) {
  event.stopPropagation();
  const picker = $('statusPicker');
  const bg     = $('statusPickerBg');
  picker.innerHTML = options.map(opt => {
    const active = opt.index === currentIndex;
    return `<div class="status-picker-item" onclick="applyStatus('${itemId}','${columnId}','${opt.index}',${JSON.stringify(opt.label).replace(/"/g,'&quot;')})">
      <span class="status-picker-dot" style="background:${opt.color}"></span>
      <span style="${active ? 'font-weight:700' : ''}">${_esc(opt.label)}</span>
      ${active ? '<span style="margin-left:auto;font-size:11px;color:var(--text3)">✓</span>' : ''}
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

// ── Stauplan ───────────────────────────────────────────────────────────────

const STAU_FAECHER = {
  'bug-sb':   { name: 'Bug Steuerbord',     color: '#fbbf24' },
  'bug-bb':   { name: 'Bug Backbord',       color: '#f59e0b' },
  'kab-bb':   { name: 'Kabine Backbord',    color: '#60a5fa' },
  'motor':    { name: 'Motorraum',          color: '#9ca3af' },
  'kab-sb':   { name: 'Kabine Steuerbord',  color: '#93c5fd' },
  'karten':   { name: 'Kartenraum',         color: '#34d399' },
  'kombuese': { name: 'Kombüse',            color: '#fb923c' },
  'salon':    { name: 'Salon',              color: '#a78bfa' },
  'werkbank': { name: 'Werkbank / Koje',    color: '#f87171' },
  'wc':       { name: 'WC / Bad',           color: '#22d3ee' },
  'heck':     { name: 'Heckstauraum',       color: '#94a3b8' },
};

// Beispieldaten — werden später über ein Formular befüllt
// Startwerte — werden beim ersten Laden mit Server-Daten überschrieben
let STAU_ITEMS = [
  { name: 'Rettungswesten',    fach: 'salon',    menge: '4×',     notiz: '' },
  { name: 'Erste-Hilfe-Set',   fach: 'karten',   menge: '1×',     notiz: 'Ablaufdatum prüfen' },
  { name: 'Signalraketen',     fach: 'karten',   menge: '1 Set',  notiz: '' },
  { name: 'Fender',            fach: 'heck',     menge: '6×',     notiz: '' },
  { name: 'Schleppleine',      fach: 'heck',     menge: '20 m',   notiz: '' },
  { name: 'Reservekanister',   fach: 'heck',     menge: '2 × 10 L', notiz: 'Diesel' },
  { name: 'Ankerleine',        fach: 'bug-bb',   menge: '50 m',   notiz: 'Nylon' },
  { name: 'Anker',             fach: 'bug-bb',   menge: '1×',     notiz: 'Danforth' },
  { name: 'Werkzeugkoffer',    fach: 'werkbank', menge: '1×',     notiz: '' },
  { name: 'Feuerlöscher',      fach: 'motor',    menge: '2×',     notiz: '' },
  { name: 'Gaskartusche',      fach: 'kombuese', menge: '3×',     notiz: '' },
  { name: 'Kartenmaterial',    fach: 'karten',   menge: '1 Set',  notiz: 'Nordsee / Ostsee' },
];

async function _stauSave() {
  try {
    await fetch('/api/stauplan', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(STAU_ITEMS),
    });
  } catch(e) { console.error('Stauplan speichern:', e); }
}

async function _stauLoad() {
  try {
    const r = await fetch('/api/stauplan');
    if (r.ok) {
      const data = await r.json();
      if (Array.isArray(data) && data.length) STAU_ITEMS = data;
    }
  } catch(_) {}
}

async function openStauplan() {
  _closeAllOverlays();
  history.pushState({ overlay: 'stauplan' }, '', '#stauplan');
  $('stauplanOverlay').classList.remove('hidden');
  _navActive('stauplanBtn');
  await _stauLoad();
  renderStauTable('');
}

function closeStauplan() {
  $('stauplanOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

let _stauFachFilter = null;

function selectFach(id) {
  if (_stauFachFilter === id) { clearStauFilter(); return; }
  _stauFachFilter = id;
  document.querySelectorAll('.stauplan-fach').forEach(el => {
    el.classList.remove('sp-sel');
    el.setAttribute('fill', 'transparent');
    el.setAttribute('stroke', 'none');
  });
  const el = document.querySelector(`.stauplan-fach[data-fach="${id}"]`);
  const fach = STAU_FAECHER[id];
  if (el) {
    el.classList.add('sp-sel');
    el.setAttribute('fill', fach.color + '30');
    el.setAttribute('stroke', fach.color);
  }
  const lbl = $('stauFilterLabel');
  lbl.textContent = fach.name;
  lbl.style.color  = fach.color;
  $('stauClearBtn').style.display = '';
  renderStauTable($('stauSearchInput')?.value || '');
}

function clearStauFilter() {
  _stauFachFilter = null;
  document.querySelectorAll('.stauplan-fach').forEach(el => {
    el.classList.remove('sp-sel');
    el.setAttribute('fill', 'transparent');
    el.setAttribute('stroke', 'none');
  });
  const lbl = $('stauFilterLabel');
  lbl.textContent = 'Alle Fächer';
  lbl.style.color  = 'var(--text3)';
  $('stauClearBtn').style.display = 'none';
  renderStauTable($('stauSearchInput')?.value || '');
}

function renderStauTable(query) {
  const q = query.trim().toLowerCase();
  let rows = _stauFachFilter ? STAU_ITEMS.filter(i => i.fach === _stauFachFilter) : STAU_ITEMS;
  if (q) rows = rows.filter(i =>
    i.name.toLowerCase().includes(q) ||
    (STAU_FAECHER[i.fach]?.name || '').toLowerCase().includes(q) ||
    i.notiz.toLowerCase().includes(q)
  );
  $('stauTableBody').innerHTML = rows.length
    ? rows.map(i => {
        const origIdx = STAU_ITEMS.indexOf(i);
        const f = STAU_FAECHER[i.fach];
        return `<tr style="cursor:pointer"
          onmouseenter="hoverFach('${i.fach}')"
          onmouseleave="unhoverFach('${i.fach}')"
          onclick="openStauEdit(${origIdx})">
          <td>${i.name}</td>
          <td><span class="stauplan-badge" style="background:${f?.color||'#888'}">${f?.name||i.fach}</span></td>
          <td>${i.menge}</td>
          <td style="color:var(--text3)">${i.notiz||'—'}</td>
        </tr>`;
      }).join('')
    : `<tr><td colspan="4" style="color:var(--text3);text-align:center;padding:20px">${_stauFachFilter ? 'Nichts in diesem Fach' : 'Keine Ergebnisse'}</td></tr>`;
}

function hoverFach(id) {
  if (_stauFachFilter === id) return;
  const el = document.querySelector(`.stauplan-fach[data-fach="${id}"]`);
  if (el) el.setAttribute('fill', (STAU_FAECHER[id]?.color || '#fff') + '28');
}
function unhoverFach(id) {
  if (_stauFachFilter === id) return;
  const el = document.querySelector(`.stauplan-fach[data-fach="${id}"]`);
  if (el) el.setAttribute('fill', 'transparent');
}

let _stauEditIdx = -1;

function openStauEdit(idx) {
  const item = STAU_ITEMS[idx];
  if (!item) return;
  _stauEditIdx = idx;
  $('stauEditTitle').textContent = 'Bearbeiten: ' + item.name;
  $('stauEditName').value  = item.name;
  $('stauEditMenge').value = item.menge;
  $('stauEditNotiz').value = item.notiz || '';
  // Populate fach dropdown
  $('stauEditFach').innerHTML = Object.entries(STAU_FAECHER).map(([k, f]) =>
    `<option value="${k}"${k === item.fach ? ' selected' : ''}>${f.name}</option>`
  ).join('');
  $('stauDeleteBtn').style.display = '';
  $('stauEditBg').style.display    = 'block';
  $('stauEditSheet').style.display = 'block';
  setTimeout(() => $('stauEditName').focus(), 80);
}

function closeStauEdit() {
  $('stauEditBg').style.display    = 'none';
  $('stauEditSheet').style.display = 'none';
}

function saveStauEdit() {
  const item = STAU_ITEMS[_stauEditIdx];
  if (!item) return;
  item.name  = $('stauEditName').value.trim()  || item.name;
  item.fach  = $('stauEditFach').value;
  item.menge = $('stauEditMenge').value.trim();
  item.notiz = $('stauEditNotiz').value.trim();
  closeStauEdit();
  _stauSave();
  renderStauTable($('stauSearchInput')?.value || '');
}

function deleteStauItem() {
  if (_stauEditIdx < 0) return;
  if (!confirm(`"${STAU_ITEMS[_stauEditIdx]?.name}" löschen?`)) return;
  STAU_ITEMS.splice(_stauEditIdx, 1);
  closeStauEdit();
  _stauSave();
  renderStauTable($('stauSearchInput')?.value || '');
}

function openStauNew() {
  _stauEditIdx = -1;
  $('stauEditTitle').textContent = 'Neuer Artikel';
  $('stauEditName').value  = '';
  $('stauEditMenge').value = '';
  $('stauEditNotiz').value = '';
  const defaultFach = _stauFachFilter || 'salon';
  $('stauEditFach').innerHTML = Object.entries(STAU_FAECHER).map(([k, f]) =>
    `<option value="${k}"${k === defaultFach ? ' selected' : ''}>${f.name}</option>`
  ).join('');
  $('stauDeleteBtn').style.display = 'none';
  $('stauEditBg').style.display    = 'block';
  $('stauEditSheet').style.display = 'block';
  setTimeout(() => $('stauEditName').focus(), 80);
}

// Override saveStauEdit to handle both new + edit
const _origSaveStauEdit = saveStauEdit;
function saveStauEdit() {
  if (_stauEditIdx === -1) {
    // New item
    const name = $('stauEditName').value.trim();
    if (!name) { $('stauEditName').focus(); return; }
    STAU_ITEMS.push({
      name,
      fach:  $('stauEditFach').value,
      menge: $('stauEditMenge').value.trim(),
      notiz: $('stauEditNotiz').value.trim(),
    });
    closeStauEdit();
    _stauSave();
    renderStauTable($('stauSearchInput')?.value || '');
  } else {
    _origSaveStauEdit();
  }
}

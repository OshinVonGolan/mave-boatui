// ── Light detail ───────────────────────────────────────────────────────────

let liveChannels  = Array(9).fill(0);
let lightDetailOpen = false;
let _dragging     = false;
let _throttleTimer = null;
let _throttlePending = false;

function openLightDetail() {
  _closeAllOverlays();
  liveChannels = [...(state.lights?.channels ?? Array(9).fill(0))];
  lightDetailOpen = true;
  buildLightSliders();
  history.pushState({ overlay: 'lights' }, '', '#lights');
  $('lightOverlay').classList.remove('hidden');
}

function closeLightDetail() {
  closePresetSave();
  lightDetailOpen = false;
  $('lightOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

function openPresetSave() {
  renderPresetEditList();
  $('presetSheet').style.display = 'block';
  $('presetSheetBg').style.display = 'block';
}

function closePresetSave() {
  $('presetSheet').style.display = 'none';
  $('presetSheetBg').style.display = 'none';
}

function sliderFill(slider, v) {
  const pct = (v / 255 * 100).toFixed(1);
  slider.style.background =
    `linear-gradient(to right, var(--accent) ${pct}%, var(--border) ${pct}%)`;
}

function buildLightSliders() {
  const wrap = $('chSlidersWrap');
  wrap.innerHTML = '';
  for (let i = 0; i < 4; i++) {
    const v = liveChannels[i] ?? 0;
    const card = document.createElement('div');
    card.className = 'ch-card';
    card.innerHTML = `
      <div class="ch-card-header">
        <span class="ch-card-num">${i + 1}</span>
        <span class="ch-card-name">${CH_NAMES[i]}</span>
        <span class="ch-card-val" id="chv${i}">${Math.round(v / 255 * 100)}%</span>
      </div>
      <input class="ch-slider-big" id="chs${i}" type="range" min="0" max="255" step="1" value="${v}">`;
    wrap.appendChild(card);
    const slider = $(`chs${i}`);
    sliderFill(slider, v);
    slider.addEventListener('pointerdown', () => { _dragging = true; });
    slider.addEventListener('pointerup',   () => { _dragging = false; });
    slider.addEventListener('input', () => {
      const val = parseInt(slider.value);
      liveChannels[i] = val;
      $(`chv${i}`).textContent = Math.round(val / 255 * 100) + '%';
      sliderFill(slider, val);
      checkPresetMatch();
      throttleSend();
    });
  }
  updateRelayBtn();
}

function updateRelayBtn() {
  const on = (liveChannels[8] ?? 0) > 0;
  $('relayBtn').textContent  = on ? 'AN' : 'AUS';
  $('relayBtn').className    = 'relay-btn ' + (on ? 'on' : 'off');
  $('relayHint').textContent = on ? 'Relais geschlossen' : 'Relais offen';
}

function toggleRelay() {
  liveChannels[8] = liveChannels[8] > 0 ? 0 : 1;
  updateRelayBtn();
  checkPresetMatch();
  throttleSend();
}

function throttleSend() {
  if (_throttleTimer) { _throttlePending = true; return; }
  sendChannels();
  _throttleTimer = setTimeout(() => {
    _throttleTimer = null;
    if (_throttlePending) { _throttlePending = false; throttleSend(); }
  }, 80);
}

async function sendChannels() {
  try {
    await fetch('/api/lights/channels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: liveChannels }),
    });
  } catch(e) { console.error('Senden fehlgeschlagen', e); }
}

function syncLightOverlay(channels) {
  if (_dragging) return;
  if (lightDetailOpen) {
    liveChannels = [...channels];
    for (let i = 0; i < 4; i++) {
      const s = $(`chs${i}`);
      if (!s) continue;
      const v = channels[i] ?? 0;
      s.value = v;
      sliderFill(s, v);
      $(`chv${i}`).textContent = Math.round(v / 255 * 100) + '%';
    }
    updateRelayBtn();
    checkPresetMatch();
  } else {
    checkPresetMatch(channels);
  }
}

function renderPresetEditList() {
  $('presetEditList').innerHTML = presets.map((p, i) => `
    <div class="preset-save-card" id="per${i}">
      <div class="preset-save-card-top">
        <span class="preset-save-card-emoji">${p.emoji}</span>
        <input class="preset-edit-name" id="pen${i}" type="text"
               value="${p.name}" maxlength="24" placeholder="Preset ${i + 1}">
      </div>
      <button class="preset-save-card-btn" id="psb${i}" onclick="saveCurrentAsPreset(${i})">
        Werte speichern
      </button>
    </div>`).join('');
}

async function saveCurrentAsPreset(idx) {
  const name = $(`pen${idx}`)?.value.trim() || presets[idx]?.name || `Preset ${idx + 1}`;
  const btn  = $(`psb${idx}`);
  try {
    const data = await fetch(`/api/lights/preset/${idx}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, values: liveChannels }),
    }).then(r => r.json());
    presets = data.presets ?? presets;
    renderPresets();
    btn.textContent = '✓ Gespeichert';
    btn.classList.add('saved');
    setTimeout(() => { btn.textContent = 'Werte speichern'; btn.classList.remove('saved'); }, 2000);
  } catch(e) { console.error('Preset speichern fehlgeschlagen', e); }
}

// ── Chart canvas — hover / touch crosshair ─────────────────────────────────

(function() {
  const canvas = $('chartMain');
  if (!canvas) return;

  function posFrom(clientX) {
    const rect = canvas.getBoundingClientRect();
    const CW = rect.width - CHART_PAD_L - CHART_PAD_R;
    return Math.max(0, Math.min(1, (clientX - rect.left - CHART_PAD_L) / CW));
  }

  canvas.addEventListener('mousemove', e => {
    chartHoverPos = posFrom(e.clientX);
    renderCharts();
  });
  canvas.addEventListener('mouseleave', () => {
    chartHoverPos = null;
    renderCharts();
  });
  canvas.addEventListener('touchstart', e => {
    e.preventDefault();
    chartHoverPos = posFrom(e.touches[0].clientX);
    renderCharts();
  }, { passive: false });
  canvas.addEventListener('touchmove', e => {
    e.preventDefault();
    chartHoverPos = posFrom(e.touches[0].clientX);
    renderCharts();
  }, { passive: false });
  canvas.addEventListener('touchend', () => {
    chartHoverPos = null;
    renderCharts();
  });
})();

// ── Hash routing (back-gesture support) ────────────────────────────────────

window.addEventListener('popstate', () => {
  _closeAllOverlays();
  const hash = location.hash.slice(1);
  if (hash) {
    const map = {
      battery:  () => { $('battOverlay').classList.remove('hidden'); if (_lastBms) updateBms(_lastBms); setTimeout(renderCharts, 50); },
      lights:   () => { liveChannels = [...(state.lights?.channels ?? Array(9).fill(0))]; buildLightSliders(); lightDetailOpen = true; $('lightOverlay').classList.remove('hidden'); },
      alarms:   () => { $('alarmOverlay').classList.remove('hidden'); switchTab('aktiv'); },
      network:  () => { $('networkOverlay').classList.remove('hidden'); fetchNetwork(); netTimer = setInterval(fetchNetwork, 5000); },
      settings: () => { _showSettingsPanel(); },
      connectivity: () => {
        $('connInetOverlay').classList.remove('hidden');
        renderConnectivity(_connData);
        fetchConnectivity();
        _connOverlayTimer = setInterval(fetchConnectivity, 20000);
      },
      wartung:  () => { $('wartungOverlay').classList.remove('hidden'); renderWartung(); },
      stauplan: () => { $('stauplanOverlay').classList.remove('hidden'); renderStauTable(''); },
      monday: () => {
        $('mondayOverlay').classList.remove('hidden');
        if (!_mondayData) loadMondayBoard();
        else renderMondayBoard(_mondayData);
      },
    };
    if (map[hash]) map[hash]();
  }
});

// ── Time sync ──────────────────────────────────────────────────────────────

async function syncTime() {
  const btn = $('timeSyncBtn');
  const fb  = $('timeSyncFeedback');
  btn.disabled = true;
  fb.style.color = 'var(--text2)';
  fb.textContent = 'Sende…';
  try {
    await fetch('/api/system/time-sync', { method: 'POST' });
    fb.textContent = 'Gesendet ✓';
    fb.style.color = 'var(--green)';
    setTimeout(() => { fb.textContent = ''; btn.disabled = false; }, 3000);
  } catch(e) {
    fb.textContent = 'Fehler';
    fb.style.color = 'var(--red)';
    btn.disabled = false;
  }
}

// ── Update ─────────────────────────────────────────────────────────────────

async function runUpdate() {
  const btn = $('updateBtn');
  const fb  = $('updateFeedback');
  const vi  = $('updateVersionInfo');
  btn.disabled = true;
  fb.style.color = 'var(--text2)';
  fb.textContent = 'Lade…';
  try {
    const r   = await fetch('/api/system/update', { method: 'POST' });
    const txt = await r.text();
    let data = {};
    try { data = JSON.parse(txt); } catch(_) { /* leere/HTML-Antwort beim Neustart */ }
    if (!r.ok) {
      fb.textContent = 'Fehler: ' + (data.detail || `HTTP ${r.status}`);
      fb.style.color = 'var(--red)';
      btn.disabled = false;
      return;
    }
    if (data.changed) {
      if (vi && data.version_before && data.version_after)
        vi.textContent = `${data.version_before} → ${data.version_after}`;
      fb.textContent = 'Aktualisiert — Server startet neu…';
      fb.style.color = 'var(--green)';
      // Nach Neustart auf Startseite neu laden (ohne #settings)
      setTimeout(() => { location.href = location.pathname; }, 4000);
    } else {
      fb.textContent = 'Bereits aktuell ✓';
      fb.style.color = 'var(--green)';
      btn.disabled = false;
    }
  } catch(e) {
    // Verbindungsabbruch durch Neustart ist hier wahrscheinlich = Erfolg
    fb.textContent = 'Server startet neu…';
    fb.style.color = 'var(--green)';
    setTimeout(() => { location.href = location.pathname; }, 5000);
  }
}

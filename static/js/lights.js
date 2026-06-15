// ── Channel bars ───────────────────────────────────────────────────────────

const VISIBLE_CH = [0, 1, 2, 3, 8]; // PWM 1–4 + relay

function buildChannelBars() {
  const row = $('channelsRow'), lbl = $('channelsLabel');
  row.innerHTML = lbl.innerHTML = '';
  VISIBLE_CH.forEach(i => {
    const wrap = document.createElement('div'); wrap.className = 'ch-bar-wrap';
    const bar  = document.createElement('div'); bar.className = 'ch-bar' + (i===8?' relay':'');
    bar.id = `chBar${i}`; bar.style.height = '0%';
    wrap.appendChild(bar); row.appendChild(wrap);
    const l = document.createElement('div'); l.className = 'ch-num';
    l.textContent = i < 8 ? (i+1) : 'R'; lbl.appendChild(l);
  });
}
buildChannelBars();

function updateChannels(channels) {
  VISIBLE_CH.forEach(i => {
    const v = channels[i] ?? 0, bar = $(`chBar${i}`);
    if (!bar) return;
    bar.style.height  = (i < 8 ? v/255*100 : v>0?100:0) + '%';
    bar.style.opacity = v > 0 ? '0.9' : '0.2';
  });
  updateWideSliders(channels);
}

// ── Wide tile: vertical zone sliders ────────────────────────────────────────

function buildWideSliders() {
  const wrap = $('lightsWideSliders');
  if (!wrap) return;
  wrap.innerHTML = '';
  VISIBLE_CH.forEach(i => {
    const isRelay = i >= 8;
    const item = document.createElement('div');
    item.className = 'lights-slider-item';
    if (isRelay) {
      item.innerHTML = `<button class="lights-relay-btn" id="wideRelayBtn"
        onclick="event.stopPropagation();_toggleRelayFromWide()">R</button>
        <span class="lights-slider-lbl">R</span>`;
    } else {
      item.innerHTML = `<input type="range" class="lights-vslider" id="wideSlider${i}"
        min="0" max="255" value="0"
        oninput="event.stopPropagation();_setChannelFromSlider(${i},+this.value)"
        onclick="event.stopPropagation()">
        <span class="lights-slider-lbl">${i + 1}</span>`;
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

let _wideDebounce = null;
function _setChannelFromSlider(ch, val) {
  liveChannels[ch] = val;
  checkPresetMatch(liveChannels);
  clearTimeout(_wideDebounce);
  _wideDebounce = setTimeout(() => {
    fetch('/api/lights/channels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: liveChannels }),
    }).catch(() => {});
  }, 80);
}

function _toggleRelayFromWide() {
  liveChannels[8] = liveChannels[8] > 0 ? 0 : 1;
  checkPresetMatch(liveChannels);
  updateChannels(liveChannels);
  fetch('/api/lights/channels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values: liveChannels }),
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
    // Apply tank names from config
    $('tank1Name').textContent = tanksConfig.tank1?.name ?? 'Tank 1';
    $('tank2Name').textContent = tanksConfig.tank2?.name ?? 'Tank 2';
    renderPresets();
  } catch(e) { console.error('Presets nicht geladen:', e); }
}

function renderPresets() {
  const grid = $('presetsGrid'); grid.innerHTML = '';
  presets.forEach((p, i) => {
    const btn = document.createElement('button');
    btn.className = 'preset-btn' + (p.values==null?' disabled':'') + (activePreset===i?' active':'');
    btn.innerHTML = `<span class="preset-emoji">${p.emoji}</span><span class="preset-name">${p.name}</span>`;
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

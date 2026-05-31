// ── SOC-Gauge ──────────────────────────────────────────────────────────────

const GAUGE_CX = 80, GAUGE_CY = 68, GAUGE_R = 58;
const GAUGE_START = 225, GAUGE_SWEEP = 270;

function polarXY(cx, cy, r, deg) {
  const rad = (deg - 90) * Math.PI / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}
function arcPath(cx, cy, r, start, sweep) {
  const [x1,y1] = polarXY(cx,cy,r,start);
  const [x2,y2] = polarXY(cx,cy,r,start+sweep);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${sweep>180?1:0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}
$('gaugeTrack').setAttribute('d', arcPath(GAUGE_CX,GAUGE_CY,GAUGE_R,GAUGE_START,GAUGE_SWEEP));

function updateGauge(soc) {
  const pct   = soc == null ? 0 : Math.max(0, Math.min(100, soc));
  const sweep = GAUGE_SWEEP * pct / 100;
  const color = pct >= 50 ? 'var(--green)' : pct >= 20 ? 'var(--yellow)' : 'var(--red)';
  $('gaugeValue').setAttribute('d', sweep < 2 ? '' : arcPath(GAUGE_CX,GAUGE_CY,GAUGE_R,GAUGE_START,sweep));
  $('gaugeValue').style.stroke = color;
  $('gaugePct').textContent = soc == null ? '--' : Math.round(soc) + '%';
  $('gaugePct').style.fill  = color;
}
updateGauge(null);

// ── Charge arrow (inside gauge) ────────────────────────────────────────────

function updateChargeStatus(b) {
  const arrow = $('gaugeArrow');
  const dir   = $('dChargeDir');
  const i = b.current;
  if (i == null || Math.abs(i) <= 0.3) {
    if (arrow) { arrow.textContent = ''; }
    if (dir) dir.textContent = '';
  } else if (i > 0.3) {
    if (arrow) { arrow.textContent = '▲'; arrow.setAttribute('fill', '#22c55e'); }
    if (dir) { dir.textContent = '▲ Lädt'; dir.style.color = 'var(--green)'; }
  } else {
    if (arrow) { arrow.textContent = '▼'; arrow.setAttribute('fill', '#f97316'); }
    if (dir) { dir.textContent = '▼ Entlädt'; dir.style.color = 'var(--orange)'; }
  }
}

// ── Starter battery min/max ────────────────────────────────────────────────

let _starterMin = null, _starterMax = null;
let _serviceMin = null, _serviceMax = null;
let _lastZelldiff = null;
let _lastBattery = null;

// ── Heute entnommene Energie (aus Shunt-Leistung akkumuliert) ──────────────

let _todayWhDrawn = 0;
let _todayAhDrawn = 0;
let _lastShuntTs  = null;
let _battEnergyUnit = 'ah';

function toggleBattUnit() {
  _battEnergyUnit = _battEnergyUnit === 'ah' ? 'wh' : 'ah';
  $('battToggleAh')?.classList.toggle('active', _battEnergyUnit === 'ah');
  $('battToggleWh')?.classList.toggle('active', _battEnergyUnit === 'wh');
  _renderBattGrid();
}

function _renderBattGrid() {
  const b   = _lastBattery;
  if (!b) return;
  const ah  = _battEnergyUnit === 'ah';

  // STROM / LEISTUNG
  const mainEl   = $('battIMain'), mainUnit = $('battIMainUnit');
  const subEl    = $('battISub'),  subUnit  = $('battISubUnit');
  const lblEl    = $('battStromLabel');
  if (ah) {
    if (mainEl)  mainEl.textContent  = fmt(b.current);
    if (mainEl)  mainEl.className    = b.current == null ? '' : b.current >= 0 ? 'val-green' : 'val-orange';
    if (mainUnit) mainUnit.textContent = 'A';
    if (subEl)   subEl.textContent   = b.power != null ? fmt(b.power, 0) : '--';
    if (subUnit) subUnit.textContent = 'W';
    if (lblEl)   lblEl.textContent   = 'Strom';
  } else {
    if (mainEl)  mainEl.textContent  = b.power != null ? fmt(b.power, 0) : '--';
    if (mainEl)  mainEl.className    = b.power == null ? '' : b.power >= 0 ? 'val-green' : 'val-orange';
    if (mainUnit) mainUnit.textContent = 'W';
    if (subEl)   subEl.textContent   = fmt(b.current);
    if (subUnit) subUnit.textContent = 'A';
    if (lblEl)   lblEl.textContent   = 'Leistung';
  }

  // RESTKAPAZITÄT
  const remEl   = $('battRemVal'), remUnit = $('battRemUnit');
  const capAh   = batteriesConfig.capacity_ah ?? null;
  if (remEl && capAh != null && b.consumed_ah != null) {
    const remainAh = Math.max(0, capAh + b.consumed_ah);
    if (ah) {
      remEl.textContent  = remainAh.toFixed(1);
      if (remUnit) remUnit.textContent = 'Ah';
    } else {
      const wh = remainAh * (b.voltage ?? 13.0);
      const { val, unit } = _fmtWh(wh);
      remEl.textContent  = val;
      if (remUnit) remUnit.textContent = unit;
    }
  }

  _renderTodayTile();
}

function _renderTodayTile() {
  const el   = $('battTodayWh');
  const unit = $('battTodayUnit');
  if (!el) return;
  if (_battEnergyUnit === 'ah') {
    el.textContent   = _todayAhDrawn > 0 ? _todayAhDrawn.toFixed(1) : '--';
    if (unit) unit.textContent = 'Ah';
  } else {
    const { val, unit: u } = _fmtWh(_todayWhDrawn > 0 ? _todayWhDrawn : null);
    el.textContent = val;
    if (unit) unit.textContent = u;
  }
}

function _accumTodayWh(power, current) {
  const now      = Date.now() / 1000;
  const midnight = new Date().setHours(0, 0, 0, 0) / 1000;
  if (_lastShuntTs === null || _lastShuntTs < midnight) {
    _todayWhDrawn = 0;
    _todayAhDrawn = 0;
    _lastShuntTs  = now;
    return;
  }
  const dtH = (now - _lastShuntTs) / 3600;
  if (power   != null && power   < 0) _todayWhDrawn += Math.abs(power)   * dtH;
  if (current != null && current < 0) _todayAhDrawn += Math.abs(current) * dtH;
  _lastShuntTs = now;
}

function _fmtWh(wh) {
  if (wh == null) return { val: '--', unit: 'Wh' };
  if (wh >= 1000) return { val: (wh / 1000).toFixed(2), unit: 'kWh' };
  return { val: Math.round(wh).toString(), unit: 'Wh' };
}

// ── Dual-source tile update ────────────────────────────────────────────────

function updateDualTiles() {
  const b   = _lastBattery;
  const bms = _lastBms;
  if (!b) return;
  const primary  = batteriesConfig.primary_source ?? 'shunt';
  const secLabel = primary === 'shunt' ? 'BMS' : 'Shunt';

  const socP = primary === 'shunt' ? b.soc          : bms?.soc;
  const socS = primary === 'shunt' ? bms?.soc        : b.soc;
  const vP   = primary === 'shunt' ? b.voltage       : bms?.voltage;
  const vS   = primary === 'shunt' ? bms?.voltage    : b.voltage;
  const iP   = primary === 'shunt' ? b.current       : bms?.current_total;
  const iS   = primary === 'shunt' ? bms?.current_total : b.current;

  updateGauge(socP);
  updateTopbarBatt(socP);

  const dSocEl = $('dSoc');
  if (dSocEl) dSocEl.textContent = socP != null ? Math.round(socP) : '--';
  const _pct = socP != null ? Math.max(0, Math.min(100, socP)) : 0;
  const socBar = $('dSocBar');
  if (socBar) {
    socBar.style.width = _pct + '%';
    socBar.style.background = _pct >= 50 ? 'var(--green)' : _pct >= 20 ? 'var(--yellow)' : 'var(--red)';
  }
  const socSec = $('dSocSec');
  if (socSec) {
    if (socS != null) { socSec.textContent = `${secLabel}: ${Math.round(socS)} %`; socSec.style.display = ''; }
    else socSec.style.display = 'none';
  }

  const dVEl = $('dV');
  if (dVEl) dVEl.textContent = fmtV(vP);
  const vSec = $('dVSec');
  if (vSec) {
    if (vS != null) { vSec.textContent = `${secLabel}: ${fmtV(vS)} V`; vSec.style.display = ''; }
    else vSec.style.display = 'none';
  }

  const dIEl = $('dI');
  if (dIEl) dIEl.textContent = fmt(iP);
  const iSec = $('dISec');
  if (iSec) {
    if (iS != null) { iSec.textContent = `${secLabel}: ${fmt(iS)} A`; iSec.style.display = ''; }
    else iSec.style.display = 'none';
  }
}

// ── Daily Ah accumulation ──────────────────────────────────────────────────

let _todayChargeAh = 0, _todayDischargeAh = 0;
let _lastBmsAhTs = null;

function accumBmsAh(chargeA, dischargeA) {
  const now = Date.now() / 1000;
  const midnightTs = new Date().setHours(0,0,0,0) / 1000;
  if (_lastBmsAhTs === null || _lastBmsAhTs < midnightTs) {
    _todayChargeAh = 0; _todayDischargeAh = 0;
    _lastBmsAhTs = now; return;
  }
  const dtH = (now - _lastBmsAhTs) / 3600;
  _todayChargeAh    += (chargeA    ?? 0) * dtH;
  _todayDischargeAh += (dischargeA ?? 0) * dtH;
  _lastBmsAhTs = now;
}

function recomputeDailyAhFromHist() {
  const midnightTs = new Date().setHours(0,0,0,0) / 1000;
  _todayChargeAh = 0; _todayDischargeAh = 0;
  const today = histData.filter(e => e.ts >= midnightTs && (e.current_charge != null || e.current_discharge != null));
  for (let i = 1; i < today.length; i++) {
    const dtH = (today[i].ts - today[i-1].ts) / 3600;
    _todayChargeAh    += (today[i-1].current_charge    ?? 0) * dtH;
    _todayDischargeAh += (today[i-1].current_discharge ?? 0) * dtH;
  }
}

// ── Battery update ─────────────────────────────────────────────────────────

function updateBattery(b) {
  const hasData = b.voltage != null || b.soc != null;
  $('battCard').style.display = hasData ? '' : 'none';
  if (!hasData) return;
  _lastBattery = b;
  updateChargeStatus(b);
  const vEl = $('battV'); vEl.textContent = fmtV(b.voltage); vEl.className = colorClass(b.voltage, 12.6, 12.0);
  $('battStarter').textContent = fmtV(b.starter_voltage);
  $('battCycles').textContent  = b.cycles ?? '--';
  $('battFull').textContent    = timeSince(b.time_since_full);

  _accumTodayWh(b.power, b.current);
  _renderBattGrid();

  // Detail-Overlay-Felder (non-dual)
  $('dP').textContent   = fmt(b.power, 0);
  $('dAh').textContent  = fmt(b.consumed_ah);
  $('dStarter').textContent = fmtV(b.starter_voltage);
  // Starter Min/Max: bevorzugt Shunt-Werte (H15/H16), Fallback JS-Session
  if (b.starter_min_voltage != null) _starterMin = b.starter_min_voltage;
  else if (b.starter_voltage != null && (_starterMin === null || b.starter_voltage < _starterMin)) _starterMin = b.starter_voltage;
  if (b.starter_max_voltage != null) _starterMax = b.starter_max_voltage;
  else if (b.starter_voltage != null && (_starterMax === null || b.starter_voltage > _starterMax)) _starterMax = b.starter_voltage;
  $('dStarterMin').textContent = fmtV(_starterMin);
  $('dStarterMax').textContent = fmtV(_starterMax);
  $('dCycles').textContent  = b.cycles   ?? '--';
  // min/max: prefer shunt PGN-130900 values; fall back to JS-tracked session extremes
  if (b.voltage != null) {
    if (_serviceMin === null || b.voltage < _serviceMin) _serviceMin = b.voltage;
    if (_serviceMax === null || b.voltage > _serviceMax) _serviceMax = b.voltage;
  }
  $('dMinV').textContent = fmtV(b.min_voltage ?? _serviceMin);
  $('dMaxV').textContent = fmtV(b.max_voltage ?? _serviceMax);
  $('dFull').textContent = timeSince(b.time_since_full);

  updateDualTiles();
  recordHistory(b);
  if (!$('battOverlay').classList.contains('hidden')) renderCharts();
}

function updateTopbarBatt(soc) {
  const fill = $('topbarBattFill');
  const pct  = $('topbarBattPct');
  const wrap = $('topbarBatt');
  if (!fill) return;
  if (soc == null) {
    fill.setAttribute('width', '0');
    if (pct)  pct.textContent = '--%';
    if (wrap) wrap.style.color = 'var(--text3)';
    return;
  }
  const v = Math.max(0, Math.min(100, soc));
  fill.setAttribute('width', String((v / 100 * 16).toFixed(1)));
  const color = v >= 50 ? 'var(--green)' : v >= 20 ? 'var(--yellow)' : 'var(--red)';
  if (wrap) wrap.style.color = color;
  if (pct)  pct.textContent = Math.round(v) + '%';
}

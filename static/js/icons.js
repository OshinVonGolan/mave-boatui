// ── SVG-Icon-System ────────────────────────────────────────────────────────
// Ersetzt Emoji-Piktogramme im UI (Projektregel: keine Emojis).
// Stil folgt der vorhandenen Konvention: 24er-Raster, currentColor, stroke 2.
//
//   icon('sun')                 -> <svg …>…</svg>  (16 px)
//   icon('sun', {size: 22})     -> größer
//   icon('sun', {cls: 'muted'}) -> zusätzliche CSS-Klasse
//
// Farbe kommt IMMER vom umgebenden Text (currentColor) — nie hart setzen.

const ICON_PATHS = {
  // Wetter
  sun:        '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon:       '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  cloud:      '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
  cloudSun:    '<path d="M12 2v2M4.93 4.93l1.41 1.41M20 12h2M19.07 4.93l-1.41 1.41"/>'
              + '<path d="M15.95 12.65a4 4 0 0 0-5.93-4.13"/>'
              + '<path d="M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6z"/>',
  rain:       '<path d="M18 8h-1.26A6.5 6.5 0 1 0 9 17h9a4.5 4.5 0 0 0 0-9z"/><path d="M8 20l-1 2M12 20l-1 2M16 20l-1 2"/>',
  drizzle:    '<path d="M18 8h-1.26A6.5 6.5 0 1 0 9 17h9a4.5 4.5 0 0 0 0-9z"/><path d="M8 20v1M12 20v1M16 20v1"/>',
  thunder:     '<path d="M6 16.3A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 .5 9"/>'
              + '<path d="M13 12l-3 5h4l-3 5"/>',
  snow:       '<path d="M18 8h-1.26A6.5 6.5 0 1 0 9 17h9a4.5 4.5 0 0 0 0-9z"/><path d="M8 21h.01M12 21h.01M16 21h.01M10 19h.01M14 19h.01"/>',
  fog:        '<path d="M18 8h-1.26A6.5 6.5 0 1 0 9 17h9a4.5 4.5 0 0 0 0-9z"/><path d="M5 20h14M7 23h10"/>',
  wind:       '<path d="M9.6 4.6A2 2 0 1 1 11 8H2M12.6 19.4A2 2 0 1 0 14 16H2M17.7 7.7A2.5 2.5 0 1 1 19.5 12H2"/>',
  waves:      '<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5s2.5 2 5 2c1.3 0 1.9-.5 2.5-1M2 12c.6.5 1.2 1 2.5 1C7 13 7 11 9.5 11s2.5 2 5 2c1.3 0 1.9-.5 2.5-1M2 18c.6.5 1.2 1 2.5 1C7 19 7 17 9.5 17s2.5 2 5 2c1.3 0 1.9-.5 2.5-1"/>',
  droplet:    '<path d="M12 2.7l5.7 5.6a8 8 0 1 1-11.4 0z"/>',
  thermometer:'<path d="M14 14.8V3.5a2.5 2.5 0 0 0-5 0v11.3a4 4 0 1 0 5 0z"/>',

  // Energie
  // alternator war derselbe Kreispfeil wie `refresh` — die Lichtmaschine
  // trug damit das Sinnbild fuer „neu laden". Jetzt das Zeichen fuer einen
  // Generator: Wellenlinie im Kreis.
  bolt:       '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
  // Der Anschluss sass vier Einheiten neben dem Gehaeuse und hing in der Luft.
  battery:    '<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M21 10.5v3"/>',
  plug:       '<path d="M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  solar:       '<path d="M6 5h12l3 12H3z"/><path d="M8.6 5L6.6 17M15.4 5l2 12M4.5 11h15"/>',
  alternator: '<circle cx="12" cy="12" r="9"/>'
            + '<path d="M7.5 13.5c1.5-3 3-3 4.5 0s3 3 4.5 0"/>',
  gauge:      '<path d="M12 14l3.5-3.5"/><path d="M3.6 18a9 9 0 1 1 16.8 0"/>',
  scale:      '<path d="M12 3v18M5 7h14M5 7l-3 6h6zM19 7l3 6h-6z"/>',

  // Licht
  bulb:       '<path d="M15 14c.2-1 .7-1.7 1.5-2.5A5.5 5.5 0 1 0 7.5 11.5C8.3 12.3 8.8 13 9 14"/><path d="M9 18h6M10 22h4"/>',
  bulbOff:     '<path d="M16.8 11.2c.8-.9 1.2-2 1.2-3.2a6 6 0 0 0-9.3-5"/>'
              + '<path d="M6.3 6.3a4.7 4.7 0 0 0 1.2 5.2c.7.7 1.3 1.5 1.5 2.5"/>'
              + '<path d="M9 18h6M10 22h4M2 2l20 20"/>',

  // Status / Navigation
  anchor:     '<circle cx="12" cy="5" r="3"/><path d="M12 22V8M5 12H2a10 10 0 0 0 20 0h-3"/>',
  warning:    '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h16.9a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  check:      '<path d="M20 6L9 17l-5-5"/>',
  close:      '<path d="M18 6L6 18M6 6l12 12"/>',
  wrench:     '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9l-3.8 3.8z"/>',
  clock:      '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  info:       '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
  arrowRight: '<path d="M5 12h14M12 5l7 7-7 7"/>',

  // Geräteübersicht — Kategorien und Bedienelemente
  wifi:       '<path d="M2 8.5a15 15 0 0 1 20 0M5.5 12a10 10 0 0 1 13 0M9 15.5a5 5 0 0 1 6 0"/><circle cx="12" cy="19" r="1.2" fill="currentColor" stroke="none"/>',
  chip:       '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  compass:    '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5.2-5.2 2 2-5.2z"/>',
  flame:      '<path d="M12 22a6 6 0 0 0 6-6c0-4-3-5.5-3.5-9.5C13 8 11.5 9 10.5 11 9.5 9.8 9 8.6 9 7.5 7.2 9 6 12.2 6 16a6 6 0 0 0 6 6z"/>',
  propeller:   '<path d="M10.8 16.4a6.1 6.1 0 0 1-8.6-7l5.4 1.4a6.1 6.1 0 0 1 7-8.6l-1.4 5.4a6.1 6.1 0 0 1'
              + ' 8.6 7l-5.4-1.4a6.1 6.1 0 0 1-7 8.6z"/><circle cx="12" cy="12" r=".6"/>',
  shield:     '<path d="M12 2.5l8 3.2v5.6c0 5-3.4 9.2-8 10.2-4.6-1-8-5.2-8-10.2V5.7z"/>',
  box:        '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  pin:        '<path d="M12 21c4-5.5 7-8.9 7-12a7 7 0 1 0-14 0c0 3.1 3 6.5 7 12z"/><circle cx="12" cy="9" r="2.5"/>',
  link:       '<path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/>',
  search:     '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  pencil:     '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  plus:       '<path d="M12 5v14M5 12h14"/>',
  refresh:    '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>',
};

/** Liefert ein Inline-SVG als String. Farbe erbt via currentColor. */
function icon(name, opts = {}) {
  const body = ICON_PATHS[name];
  if (!body) return '';
  const size = opts.size || 16;
  const cls  = opts.cls ? ` class="${opts.cls}"` : '';
  return `<svg${cls} width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" `
       + `stroke="currentColor" stroke-width="2" stroke-linecap="round" `
       + `stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}

/** Wetter-Code (Open-Meteo WMO) -> Icon-Name. */
function weatherIcon(code) {
  if (code == null) return 'cloud';
  if (code === 0)                       return 'sun';
  if (code === 1 || code === 2)         return 'cloudSun';
  if (code === 3)                       return 'cloud';
  if (code === 45 || code === 48)       return 'fog';
  if (code >= 51 && code <= 57)         return 'drizzle';
  if (code >= 61 && code <= 67)         return 'rain';
  if (code >= 71 && code <= 77)         return 'snow';
  if (code >= 80 && code <= 82)         return 'rain';
  if (code >= 85 && code <= 86)         return 'snow';
  if (code >= 95)                       return 'thunder';
  return 'cloud';
}

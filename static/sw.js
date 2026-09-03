// ── Service Worker ─────────────────────────────────────────────────────────
// Zweck ist zuerst die Installierbarkeit: Chrome bietet auf Android nur dann
// "Installieren" (eigenes Fenster, eigenes Symbol) statt "Zum Startbildschirm
// hinzufuegen" (blosse Verknuepfung, oeffnet im Browser), wenn ein Service
// Worker mit fetch-Handler registriert ist. Ohne ihn bleibt es bei der
// Verknuepfung — genau das war der Befund auf dem Tablet.
//
// Zweiter Zweck: die Oberflaeche startet auch ohne Netz.
//
// Was hier NICHT passiert, ist der wichtigere Teil: Messwerte werden nie
// zwischengespeichert. Ein aus dem Speicher beantworteter Batteriestand saehe
// aus wie ein aktueller und waere auf einem Boot gefaehrlich — lieber ein
// sichtbarer Fehler als eine unsichtbare Luege. Deshalb gehen /api/-Anfragen
// immer und ausschliesslich ins Netz.

const SPEICHER = 'mave-huelle-v1';

// Die Huelle: was die Oberflaeche zum Starten braucht. Bewusst kurz — alles
// Weitere kommt beim ersten Besuch von selbst dazu.
const HUELLE = [
  '/',
  '/js-bundle.js',
  '/static/css/style.css',
  '/static/css/geraete.css',
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

self.addEventListener('install', e => {
  // Nicht an einer einzelnen fehlenden Datei scheitern: lieber unvollstaendig
  // vorbereitet als gar nicht installiert.
  e.waitUntil(caches.open(SPEICHER)
    .then(c => Promise.allSettled(HUELLE.map(p => c.add(p))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(k => Promise.all(k.filter(n => n !== SPEICHER).map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                       // Schalten geht nie hier durch

  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.origin !== self.location.origin) return;        // Fremdes nicht anfassen

  // Messwerte, Zustaende, Verlauf: immer frisch oder gar nicht.
  if (url.pathname.startsWith('/api/')) return;

  // Alles Uebrige: erst das Netz fragen, den Erfolg mitschreiben, und nur bei
  // Netzausfall aus dem Speicher antworten. So gibt es nie ein altes Bundle,
  // solange eine Verbindung besteht — und trotzdem einen Start ohne Netz.
  e.respondWith(
    fetch(req)
      .then(antwort => {
        if (antwort && antwort.ok && antwort.type === 'basic') {
          const kopie = antwort.clone();
          caches.open(SPEICHER).then(c => c.put(req, kopie)).catch(() => {});
        }
        return antwort;
      })
      .catch(() => caches.match(req).then(treffer => treffer || (
        req.mode === 'navigate'
          ? caches.match('/')
          : Response.error()
      )))
  );
});

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

// Die Nummer MUSS hoch, wenn sich eine Datei aus der Huelle aendert: der
// activate-Schritt loescht jeden Speicher, der nicht so heisst. Ohne das
// haette ein Geraet nach dem Icon-Wechsel noch wochenlang das alte Symbol.
const SPEICHER = 'mave-huelle-v5';

// Die Huelle: was die Oberflaeche zum Starten braucht. Bewusst kurz — alles
// Weitere kommt beim ersten Besuch von selbst dazu.
const HUELLE = [
  '/',
  '/js-bundle.js',
  '/static/css/style.css',
  '/static/css/geraete.css',
  '/static/manifest.json',
  // Die Wandfassung: dieselbe Seite, aber als eigene Anwendung installierbar
  // (Vollbild ohne Browserleiste). Beide gehoeren in die Huelle, sonst startet
  // die eine oder die andere ohne Netz nicht.
  '/wand',
  '/static/manifest-wand.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
  // Der Schriftzug in der Kopfzeile ist eine Maskendatei — fehlt sie, steht
  // dort ein leerer Platz statt des Bootsnamens.
  '/static/marke-mave.svg',
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

// ── Benachrichtigungen ─────────────────────────────────────────────────────
// Sie kommen aus alarmton.js (bei offener App) und spaeter aus dem Push-Kanal
// (bei geschlossener). Beide Wege enden hier, wenn jemand darauf tippt.
//
// Warum ueberhaupt Code dafuer: ohne diesen Horcher passiert beim Antippen
// NICHTS. Eine Benachrichtigung, die sich nicht oeffnen laesst, ist eine
// Meldung ohne Weg zur Sache.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const ziel = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil((async () => {
    const fenster = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // Ist die Anwendung schon offen, wird sie nach vorn geholt statt ein
    // zweites Fenster aufzumachen.
    for (const f of fenster) {
      if (new URL(f.url).origin === self.location.origin) {
        await f.focus();
        try { await f.navigate(ziel); } catch (_) { /* manche Browser lassen das nicht zu */ }
        return;
      }
    }
    await self.clients.openWindow(ziel);
  })());
});

// ── Push ───────────────────────────────────────────────────────────────────
// Was ankommt, wenn die Anwendung ZU ist. Der Server schickt es ueber den
// Push-Dienst des Browserherstellers; hier wird daraus eine Benachrichtigung.
//
// `userVisibleOnly` gilt: ein Push MUSS sichtbar werden, sonst entzieht der
// Browser die Erlaubnis. Deshalb steht am Ende immer eine Meldung — auch wenn
// die Nutzlast unlesbar ankommt.
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { /* dann eben ohne */ }
  const titel = d.titel || 'Mave';
  e.waitUntil(self.registration.showNotification(titel, {
    body: d.text || 'Es gibt etwas Neues an Bord.',
    icon: '/static/icon-192.png',
    badge: '/static/favicon-32.png',
    tag: d.tag || 'mave',
    renotify: true,
    requireInteraction: !!d.dringend,
    data: { url: d.url || '/#alarme' },
  }));
});

// Der Push-Dienst kann ein Abo austauschen — meist nach laengerer Zeit oder
// wenn der Browser aufraeumt. Ohne diese Meldung waere das Geraet danach
// stumm, ohne dass es jemandem auffiele.
self.addEventListener('pushsubscriptionchange', e => {
  e.waitUntil((async () => {
    try {
      const alt = e.oldSubscription;
      const neu = e.newSubscription || await self.registration.pushManager.subscribe(
        e.oldSubscription ? e.oldSubscription.options : { userVisibleOnly: true });
      if (alt) {
        await fetch('/api/push/abmelden', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ abo: alt.toJSON(), endpunkt: alt.endpoint }),
        });
      }
      await fetch('/api/push/anmelden', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ abo: neu.toJSON() }),
      });
    } catch (_) { /* beim naechsten Oeffnen der App faellt es auf */ }
  })());
});


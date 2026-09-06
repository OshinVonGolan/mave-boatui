"""Reihenfolge der JS-Dateien im Bundle — EINE Quelle fuer Pi und Server.

Die Liste stand frueher doppelt (main.py und server/app.py) und war schon
auseinandergelaufen. Da beide Seiten dieselbe Oberflaeche ausliefern, muss die
Reihenfolge identisch sein: sonst verhaelt sich die PWA je nachdem, ob sie vom
Boot oder vom Server geladen wurde, unterschiedlich — ein Fehler, den man nur
an einem von beiden Orten sieht.

Reihenfolge ist bedeutsam: icons.js und core.js stellen Grundfunktionen, auf
die spaetere Dateien beim Laden zugreifen. quelle.js steht frueh, weil es den
Waechter um fetch() legt — alles danach soll bereits durch ihn laufen.
"""

JS_FILES = [
    'icons.js',
    'core.js', 'quelle.js', 'anmeldung.js', 'battery.js', 'tanks.js', 'lights.js', 'charts.js',
    'alarms.js', 'alarmton.js', 'settings.js', 'connectivity.js', 'ws.js', 'lightdetail.js',
    'wartung.js', 'stauplan.js', 'monday.js', 'flow.js', 'display.js',
    'wandbetrieb.js', 'waterlevel.js', 'weather.js', 'verlauf.js', 'heizung.js',
    'orte.js', 'topologie.js', 'geraete.js', 'init.js',
]

# Das Logbuch hat sein eigenes, kleines Buendel. wandbetrieb.js steht in beiden:
# der Nachtmodus gilt fuer den Bildschirm, nicht fuer eine einzelne Seite —
# wer nachts im Logbuch liest, will ihn genauso.
DIAGNOSE_FILES = ['wandbetrieb.js', 'diagnose.js']

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
    'alarms.js', 'settings.js', 'connectivity.js', 'ws.js', 'lightdetail.js',
    'wartung.js', 'stauplan.js', 'monday.js', 'flow.js', 'display.js',
    'waterlevel.js', 'weather.js', 'verlauf.js', 'heizung.js',
    'orte.js', 'topologie.js', 'geraete.js', 'init.js',
]

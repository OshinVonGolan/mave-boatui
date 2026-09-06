# Der Grundriss

Wo an Bord etwas liegt, beantworten drei Stellen der Oberfläche: der Stauplan,
die Geräteseite und das Ortsfeld in den Popups. Alle drei zeigen dasselbe
Boot — und bis zum 05.09.2026 stand dieses Boot dreimal im Programm: als 250
Zeilen SVG in `index.html`, als Tabelle `ORTE` in `orte.js` und als
`STAU_FAECHER` in `stauplan.js`. Drei Kopien, keine davon änderbar, alle drei
für *dieses* Boot gezeichnet.

Jetzt ist es eine Datei.

## Was der Riss ist

`grundriss.json` (Laufzeitdatei, `.gitignore`; daneben liegt
`grundriss.example.json` als Vorlage für ein frisches Gerät — dasselbe Muster
wie bei `devices.json`).

| Feld | Bedeutung |
|------|-----------|
| `name`, `loa_m`, `breite_m` | Bootsname und Maße; die Maße geben das Seitenverhältnis der Zeichenfläche vor |
| `ansicht` | `{w, h}` der Zeichenfläche in Zeichnungseinheiten |
| `rumpf` | SVG-Pfad des Umrisses. Alles im Boot wird daran beschnitten |
| `hintergrund` | Liste von Formen (rect/line/path/circle/ellipse/text): Möbel, Schotten, Beschriftung. `frei: true` heißt „liegt außerhalb des Rumpfes und wird nicht beschnitten" |
| `raeume` | die anfassbaren Flächen: `id`, `name`, `farbe`, `form` (`rechteck` oder `vieleck`) |
| `bild` | Rest aus dem Zeichenwerkzeug: wo eine Planvorlage lag. Wird noch angenommen, aber von nichts mehr gesetzt |

## Beim Speichern wird geprüft, und zwar streng

Der Inhalt wird im Browser zu SVG. Wer schreiben darf, könnte sonst
Zeichenketten unterbringen, die im Dokument etwas anderes tun als zeichnen.

`_grundriss_pruefen()` in `main.py` lässt nur ein Raster durch:

* bekannte Formen, bekannte Felder — der Prüfer **baut ein neues Objekt**,
  statt das eingehende zu säubern. Was er nicht kennt, existiert danach nicht.
* Zahlen zwischen −10000 und 10000,
* Farben nur `#rrggbb`, `none`, `transparent`, `schraffur`,
* Pfade nur aus Pfadzeichen (`^[MmLlHhVvCcSsQqTtAaZz0-9\s,.+-]{1,4000}$`),
* Texte gehen über `textContent` ins Dokument, nie über `innerHTML`.

## Kein Zeichenwerkzeug

Es gab eines, im Logbuch, mit gerechneten Rumpfformen und einer KI-gestützten
Erkennung aus einem Planfoto. Es ist am 06.09.2026 auf Eignerentscheidung
wieder entfernt worden: für dieses Boot wird der Riss hartkodiert
(`grundriss.example.json`), und ein Werkzeug dafür lohnt erst, wenn ein
zweites Boot dazukommt.

Was davon bleibt: der Riss als **Daten** samt Prüfung (oben), `PUT
/api/grundriss` mit dem Recht `einstellen`, und der Weg über den Durchleiter
des Servers. Wer das Werkzeug wieder aufbaut, findet es in der Historie unter
`dfd0de9`.

Der Zugang zu Claude, der für die Erkennung entstand, bleibt ebenfalls — er
war von Anfang an als Grundlage fürs ganze Logbuch gebaut und nicht für diese
eine Funktion (`server/ki.py`, Logbuch › Einstellungen › KI-Zugang).

## Ausdrücklich nicht

* **Keine Seitenansicht.** Übereinanderliegende Fächer zeichnet man
  nebeneinander ein. Ein Stauplan beantwortet „wo liegt das Ding"; eine zweite
  Ansicht verdoppelt die Arbeit beim Zeichnen und halbiert die Übersicht beim
  Suchen.
* **Keine Maßketten, keine Bemaßung.** Der Riss ist ein Suchbild, keine
  Bauzeichnung.

## Fallen, die beim Umbau auffielen

Beim pixelgleichen Übersetzen des alten SVG in Daten (0 von 2,18 Mio. Pixeln
Abweichung am Ende):

1. **Gruppen-Attribute werden vererbt** (`font-family`, `fill`, `text-anchor`).
   Wer nur Elemente einsammelt, verliert sie.
2. **Rumpffüllung und Rumpfkontur sind zwei Formen**, und „Bug"/„Heck" stehen
   außerhalb des `clip-path`.
3. **`letter-spacing: 0.04em` wird dort ausgerechnet, wo es steht**, und danach
   als feste Länge vererbt. Ein Kind mit eigener Schriftgröße erbt die Länge
   des Elternteils, nicht den Faktor.

Merke: beim Übersetzen von SVG in Daten die *berechneten* Werte nehmen, nicht
die geschriebenen.

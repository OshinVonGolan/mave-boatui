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
| `bild` | wo die Planvorlage liegt: `{x, y, w, h, deckkraft}` — nur Zahlen |

Das Bild selbst steht **nicht** in der JSON, sondern in
`grundriss-vorlage.jpg` daneben. Ein halbes Megabyte Base64 in der Antwort
holte sich sonst jede Seite mit, die nur die Raumnamen braucht.

## Beim Speichern wird geprüft, und zwar streng

Der Inhalt wird im Browser zu SVG. Wer schreiben darf, könnte sonst
Zeichenketten unterbringen, die im Dokument etwas anderes tun als zeichnen.
`_grundriss_pruefen()` in `main.py` lässt deshalb nur ein Raster durch:

* bekannte Formen, bekannte Felder — der Prüfer **baut ein neues Objekt**,
  statt das eingehende zu säubern. Was er nicht kennt, existiert danach nicht.
* Zahlen zwischen −10000 und 10000,
* Farben nur `#rrggbb`, `none`, `transparent`, `schraffur`,
* Pfade nur aus Pfadzeichen (`^[MmLlHhVvCcSsQqTtAaZz0-9\s,.+-]{1,4000}$`),
* Texte gehen über `textContent` ins Dokument, nie über `innerHTML`.

Die Planvorlage wird am **Dateianfang** erkannt (`\xff\xd8\xff` bzw. `\x89PNG`),
nicht am gemeldeten Typ: ein Browser darf behaupten, was er will, und diese
Datei wird später wieder ausgeliefert. Ein SVG wäre ein Dokument mit Skripten
darin und kommt deshalb nicht durch.

## Das Werkzeug (`static/js/grundrisseditor.js`)

Erreichbar über Einstellungen → Grundriss, oder direkt unter `#grundriss`.

* **Rechteck** aufziehen, **Vieleck** aus Punkten setzen (zurück auf den ersten
  Punkt oder Eingabetaste schließt die Fläche), **Auswählen** verschiebt
  Flächen und zieht Eckpunkte. Alles rastet auf 2 Einheiten.
* **Zurück** nimmt bis zu 40 Schritte zurück.
* Es wird auf einer **Arbeitskopie** gezeichnet. Erst Speichern macht die
  Änderung echt — sonst wäre jeder Fehlgriff sofort im Stauplan zu sehen.
* Was der Pi nach der Prüfung zurückgibt, wird zur neuen Wahrheit im Browser,
  nicht die Arbeitskopie.
* Nichts liegt außerhalb der Zeichenfläche: was draußen liegt, ist unsichtbar
  und damit weder wiederzufinden noch zu löschen.

### Rumpfformen statt Bootszeichnungen

Kein Boot sieht aus wie die Zeichnung von einem anderen Boot. Zehn feste
Grundrisse wären deshalb immer knapp daneben. Stattdessen sechs **gerechnete**
Formen — Langkieler, Fahrtenyacht, Moderne Yacht, Doppelender, Motorboot,
Plattbodenschiff — aus vier Zahlen:

| Zahl | Bedeutung |
|------|-----------|
| `bug` | wie völlig das Vorschiff ist (klein = spitz). Über 0,5 bekommt die Bugspitze eine Rundung, sonst läuft sie im Punkt zusammen |
| `heck` | Spiegelbreite im Verhältnis zur größten Breite |
| `breiteBei` | wo die größte Breite liegt (0 = ganz vorn) |
| `spiegel` | 0 = das Heck läuft spitz aus, 1 = gerade Kante |

Die Länge-zu-Breite-Angabe setzt die Zeichenfläche; vorhandene Räume werden
mitskaliert, sonst säßen sie nach dem Wechsel alle im Vorschiff.

Die Vorschau im Dialog steht bewusst **nicht** im echten Verhältnis: ein Boot
ist dreieinhalbmal so lang wie breit, und sechs solche Streifen nebeneinander
sind sechs Striche. Gewählt wird der Charakter des Rumpfes, nicht die Maße.

### Die Planvorlage

Der Weg, den fast jeder gehen will: Foto oder Scan des Bootsplans hochladen und
die Räume darüber nachziehen. Sichtbarkeit und Größe als Regler, Verschieben
mit dem Werkzeug „Vorlage".

**Verkleinert wird im Browser**, nicht auf dem Pi: Pillow und numpy sind dort
nicht installiert, und sie auf einem ARMv6-Kern zu bauen dauert Stunden (siehe
`requirements.txt`). Ein Handyfoto hat acht Megapixel; was ankommt, sind
danach ein paar hundert Kilobyte JPEG mit höchstens 1400 px Kantenlänge.

## Was noch fehlt: die automatische Erkennung

Der ursprüngliche Wunsch war ein **KI-gestütztes** Werkzeug: Plan hochladen,
Außenlinien und Schotten erkennen lassen, Räume als Vorschlag bekommen.

Das ist **nicht gebaut**, und der Grund ist kein technischer:

* Es braucht einen Bilddienst und einen Zugang dazu. In diesem Projekt gibt es
  keinen — weder ein Konto noch einen Schlüssel, und keine Stelle im Quelltext,
  an der einer erwartet würde.
* Es gehört auf den **Server**, nicht auf den Pi. Der Pi Zero hat einen ARMv6-
  Kern; und der Weg nach draußen ist die Mobilfunkverbindung des Bootes.
* Der Eigner sollte wissen, dass das Bild dabei das Boot verlässt.

Wenn es kommt, ist die Anschlussstelle klar: die Vorlage liegt bereits als
Datei da, der Prüfer nimmt Vielecke entgegen, und das Werkzeug kann sie
zeichnen. Zu bauen wäre ein Endpunkt auf dem Server, der das Bild annimmt und
eine Liste von Flächen zurückgibt — in genau der Form, die `raeume` schon hat.

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

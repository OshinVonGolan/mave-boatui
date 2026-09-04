# Leaflet

Kartenbibliothek, Fassung 1.9.4, von https://unpkg.com/leaflet@1.9.4/dist/
BSD-2-Clause, © 2010–2023 Vladimir Agafonkin, © 2010–2011 CloudMade.

**Warum hier und nicht von einem Verteilnetz:** die Seite soll nicht davon
abhängen, dass ein fremder Server erreichbar ist und dieselbe Fassung
ausliefert. Ausserdem sieht dann niemand von aussen, wer wann das Logbuch
öffnet.

Geändert wurde eine einzige Zeile: der Verweis `sourceMappingURL` am Ende von
`leaflet.js` ist entfernt. Die Quellkarte liefern wir nicht mit, und der
Browser hätte sie bei jedem Laden vergeblich angefordert.

Die Bildverweise in `leaflet.css` (`images/marker-icon.png`, `images/layers.png`)
bleiben stehen, werden aber nie angefordert: die Karte benutzt weder den
Standardmarker noch die Ebenenauswahl.

## Kacheln

Grundkarte: OpenStreetMap, `https://tile.openstreetmap.org/{z}/{x}/{y}.png`
Seezeichen: OpenSeaMap,    `https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png`

Beide verlangen eine Namensnennung; sie steht in der Ecke der Karte. Die
Nutzungsregeln von OpenStreetMap erlauben den Gebrauch in dieser Grössenordnung
ausdrücklich — ein Logbuch, das ein Mensch gelegentlich öffnet.

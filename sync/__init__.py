"""Gemeinsames Fundament fuer Pi und Server.

Alles in diesem Paket wird von BEIDEN Seiten benutzt: der Pi an Bord schnuert
damit seine Pakete, der Server schnuert sie auf. Deshalb gilt hier eine Regel
strenger als sonst im Projekt: **keine Abhaengigkeiten** ausser der
Standardbibliothek, und **keine Seiteneffekte** — kein Netz, keine Dateien,
keine Uhr, die von aussen nicht sichtbar ist. Alles ist reine Rechnung und
laesst sich ohne Boot, ohne Server und ohne Internet testen (test_sync.py).

Warum das wichtig ist: Ein Formatfehler zwischen den beiden Seiten faellt sonst
erst auf, wenn das Boot im Funkloch steht und die Pakete niemand mehr annimmt.
"""

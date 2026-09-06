"""Der Grundriss — geprueft, und zwar auf BEIDEN Seiten gleich.

Gezeichnet wird im Logbuch auf dem Server, gebraucht wird der Riss am Boot.
Zwei Pruefungen waeren zwei Gelegenheiten, auseinanderzulaufen: was der Server
annimmt, muesste der Pi sonst nicht annehmen, und das faellt erst beim Laden
auf. Deshalb steht sie hier, wie die Zugangsregeln in `zugang.py`.

Warum ueberhaupt so streng: der Inhalt wird im Browser zu SVG. Wer schreiben
darf, koennte sonst Zeichenketten unterbringen, die im Dokument etwas anderes
tun als zeichnen. Der Pruefer BAUT DESHALB EIN NEUES OBJEKT, statt das
eingehende zu saeubern — was er nicht kennt, existiert danach nicht.
"""
import math
import re

from fastapi import HTTPException


def _text(wert, max_laenge: int, name: str) -> str:
    if not isinstance(wert, str):
        raise HTTPException(400, detail=f'{name}: Text erwartet, '
                                        f'{type(wert).__name__} bekommen')
    return wert[:max_laenge]


_GR_FORMEN = {
    'rect':    ('x', 'y', 'w', 'h', 'rx', 'ry'),
    'line':    ('x1', 'y1', 'x2', 'y2'),
    'circle':  ('cx', 'cy', 'r'),
    'ellipse': ('cx', 'cy', 'rx', 'ry'),
    'path':    (),
    'text':    ('x', 'y', 'fs'),
}
_GR_ZAHL_FELDER = {'x', 'y', 'w', 'h', 'rx', 'ry', 'x1', 'y1', 'x2', 'y2',
                   'cx', 'cy', 'r', 'fs', 'sw'}
_GR_TEXT_FELDER = {'fill', 'stroke', 'anker', 'fw', 'ls', 'strich', 's', 'd', 'ff'}
_GR_FARBE   = re.compile(r'^(#[0-9a-fA-F]{6}|none|transparent|schraffur)$')
_GR_PFAD    = re.compile(r'^[MmLlHhVvCcSsQqTtAaZz0-9\s,.+-]{1,4000}$')
_GR_EINFACH = re.compile(r'^[\w .,%-]{0,32}$')       # Anker, Strichmuster, ...
# Schriftfamilien sind Listen und damit laenger — dieselben Zeichen, mehr davon.
_GR_SCHRIFT = re.compile(r'^[\w .,\'"-]{0,64}$')


def _gr_zahl(wert, name: str) -> float:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        raise HTTPException(400, detail=f'{name}: Zahl erwartet')
    # Grosszuegig, aber begrenzt: die Zeichenflaeche ist ein paar hundert
    # Einheiten gross, alles darueber ist ein Tippfehler oder Absicht.
    if not -10000 <= wert <= 10000:
        raise HTTPException(400, detail=f'{name}: {wert} liegt ausserhalb von -10000 bis 10000')
    return round(float(wert), 3)


def _gr_farbe(wert, name: str) -> str:
    wert = _text(wert, 32, name)
    if not _GR_FARBE.match(wert):
        raise HTTPException(400, detail=f'{name}: {wert!r} ist keine erlaubte Farbe')
    return wert


def _gr_form(roh, name: str) -> dict:
    if not isinstance(roh, dict):
        raise HTTPException(400, detail=f'{name}: Objekt erwartet')
    t = _text(roh.get('t', ''), 12, f'{name}.t')
    if t not in _GR_FORMEN:
        raise HTTPException(400, detail=f'{name}.t: {t!r} ist keine bekannte Form')
    aus = {'t': t}
    for feld, wert in roh.items():
        if feld == 't':
            continue
        if feld in _GR_ZAHL_FELDER:
            aus[feld] = _gr_zahl(wert, f'{name}.{feld}')
        elif feld in ('fill', 'stroke'):
            aus[feld] = _gr_farbe(wert, f'{name}.{feld}')
        elif feld == 'd':
            d = _text(wert, 4000, f'{name}.d')
            if not _GR_PFAD.match(d):
                raise HTTPException(400, detail=f'{name}.d: unerlaubte Zeichen in der Pfadangabe')
            aus['d'] = d
        elif feld == 's':
            aus['s'] = _text(wert, 120, f'{name}.s')
        elif feld == 'frei':
            # Liegt die Form ausserhalb des Rumpfumrisses? Dann wird sie nicht
            # beschnitten — die Beschriftungen "Bug" und "Heck" stehen neben
            # dem Boot, nicht darin.
            aus['frei'] = bool(wert)
        elif feld in _GR_TEXT_FELDER:
            # Schriftfamilien sind laenger als die uebrigen Angaben.
            v = _text(wert, 64 if feld == 'ff' else 32, f'{name}.{feld}')
            if not (_GR_SCHRIFT if feld == 'ff' else _GR_EINFACH).match(v):
                raise HTTPException(400, detail=f'{name}.{feld}: unerlaubte Zeichen')
            aus[feld] = v
        else:
            raise HTTPException(400, detail=f'{name}: unbekanntes Feld {feld!r}')
    return aus


def _gr_raum(roh, name: str) -> dict:
    if not isinstance(roh, dict):
        raise HTTPException(400, detail=f'{name}: Objekt erwartet')
    kennung = _text(roh.get('id', ''), 40, f'{name}.id').strip()
    if not re.match(r'^[a-z0-9][a-z0-9_-]{0,39}$', kennung):
        raise HTTPException(400, detail=f'{name}.id: nur Kleinbuchstaben, Ziffern, - und _')
    form = roh.get('form') or {}
    if not isinstance(form, dict):
        raise HTTPException(400, detail=f'{name}.form: Objekt erwartet')
    art = _text(form.get('t', ''), 12, f'{name}.form.t')
    if art == 'rechteck':
        geprueft = {'t': 'rechteck'}
        for feld in ('x', 'y', 'w', 'h'):
            geprueft[feld] = _gr_zahl(form.get(feld), f'{name}.form.{feld}')
    elif art == 'vieleck':
        punkte = form.get('punkte')
        if not isinstance(punkte, list) or not 3 <= len(punkte) <= 200:
            raise HTTPException(400, detail=f'{name}.form.punkte: 3 bis 200 Punkte erwartet')
        geprueft = {'t': 'vieleck', 'punkte': [
            [_gr_zahl((pt or [None, None])[0], f'{name}.form.punkte[{i}].x'),
             _gr_zahl((pt or [None, None])[1], f'{name}.form.punkte[{i}].y')]
            for i, pt in enumerate(punkte)]}
    else:
        raise HTTPException(400, detail=f'{name}.form.t: rechteck oder vieleck erwartet')
    return {
        'id': kennung,
        'name': _text(roh.get('name', ''), 60, f'{name}.name').strip() or kennung,
        'farbe': _gr_farbe(roh.get('farbe', '#94a3b8'), f'{name}.farbe'),
        'form': geprueft,
    }


def grundriss_pruefen(body) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Grundriss: Objekt erwartet')
    ansicht = body.get('ansicht') or {}
    if not isinstance(ansicht, dict):
        raise HTTPException(400, detail='ansicht: Objekt erwartet')
    rumpf = _text(body.get('rumpf', ''), 4000, 'rumpf')
    if rumpf and not _GR_PFAD.match(rumpf):
        raise HTTPException(400, detail='rumpf: unerlaubte Zeichen in der Pfadangabe')
    raeume = body.get('raeume') or []
    if not isinstance(raeume, list) or len(raeume) > 200:
        raise HTTPException(400, detail='raeume: Liste mit hoechstens 200 Eintraegen erwartet')
    hintergrund = body.get('hintergrund') or []
    if not isinstance(hintergrund, list) or len(hintergrund) > 2000:
        raise HTTPException(400, detail='hintergrund: Liste mit hoechstens 2000 Formen erwartet')
    geprueft_raeume = [_gr_raum(r, f'raeume[{i}]') for i, r in enumerate(raeume)]
    kennungen = [r['id'] for r in geprueft_raeume]
    doppelt = {k for k in kennungen if kennungen.count(k) > 1}
    if doppelt:
        raise HTTPException(400, detail=f'raeume: Kennung mehrfach vergeben: {sorted(doppelt)}')
    geprueft = {
        'name':  _text(body.get('name', ''), 60, 'name').strip(),
        'loa_m': _gr_zahl(body.get('loa_m', 0) or 0, 'loa_m'),
        'breite_m': _gr_zahl(body.get('breite_m', 0) or 0, 'breite_m'),
        'ansicht': {'w': _gr_zahl(ansicht.get('w', 200), 'ansicht.w'),
                    'h': _gr_zahl(ansicht.get('h', 680), 'ansicht.h')},
        'rumpf': rumpf,
        'hintergrund': [_gr_form(f, f'hintergrund[{i}]') for i, f in enumerate(hintergrund)],
        'raeume': geprueft_raeume,
    }
    # Wo die Planvorlage liegt und wie stark sie durchscheint. Nur Zahlen — das
    # BILD selbst steht nicht hier drin, sondern in einer eigenen Datei; ein
    # halbes Megabyte Base64 in der Antwort holt sich sonst jede Seite mit,
    # die nur die Raumnamen braucht.
    bild = body.get('bild')
    if isinstance(bild, dict):
        geprueft['bild'] = {
            'x': _gr_zahl(bild.get('x', 0) or 0, 'bild.x'),
            'y': _gr_zahl(bild.get('y', 0) or 0, 'bild.y'),
            'w': _gr_zahl(bild.get('w', 0) or 0, 'bild.w'),
            'h': _gr_zahl(bild.get('h', 0) or 0, 'bild.h'),
            'deckkraft': min(1.0, max(0.0, _gr_zahl(
                bild.get('deckkraft', .5), 'bild.deckkraft'))),
        }
    return geprueft



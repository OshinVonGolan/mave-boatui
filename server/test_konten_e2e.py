"""End-to-End: Anmeldung, Rechte und die Kontenkopie ans Boot.

Prüft die Kette an einem echt laufenden Server, nicht an Attrappen:

    Ersteinrichtung → erstes Konto → Anmeldung → Rechte → Kopie zum Pi
    → Sperre → Aussperrung an Bord

Gestartet wird das von `server/laufzeit_konten.sh`, das den Server hochfährt
und dieses Skript dagegen laufen lässt.
"""
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

import websockets

PORT = os.environ['PORT']
TOKEN = os.environ['MAVE_GERAET_TOKEN']
NOTZUGANG = os.environ['MAVE_PASSWORT']
BASIS = f'http://127.0.0.1:{PORT}'

EIGNER_PW = 'ein wirklich gutes Passwort'
CREW_PW = 'auch ein gutes Passwort'

fehler = []
geprueft = 0


def pruefe(bedingung, was: str) -> None:
    global geprueft
    geprueft += 1
    if bedingung:
        print(f'  ok    {was}')
    else:
        print(f'  FEHLT {was}')
        fehler.append(was)


class Sitzung:
    """Ein Browser: merkt sich das Sitzungscookie wie ein echter."""

    def __init__(self):
        self.keks = None

    def _http(self, pfad, methode='GET', rumpf=None, bearer=None):
        req = urllib.request.Request(BASIS + pfad, method=methode)
        if self.keks:
            req.add_header('Cookie', self.keks)
        if bearer:
            req.add_header('Authorization', f'Bearer {bearer}')
        daten = None
        if rumpf is not None:
            daten = json.dumps(rumpf).encode()
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, data=daten, timeout=15) as r:
                self._keks_merken(r)
                return r.status, json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b'{}')
            except Exception:
                return e.code, {}

    def _keks_merken(self, antwort):
        gesetzt = antwort.headers.get_all('Set-Cookie') or []
        for zeile in gesetzt:
            if zeile.startswith('mave_sitzung='):
                wert = zeile.split(';')[0]
                self.keks = None if wert == 'mave_sitzung=' else wert

    async def http(self, pfad, methode='GET', rumpf=None, bearer=None):
        return await asyncio.to_thread(self._http, pfad, methode, rumpf, bearer)


async def boot_rolle(verbunden, kopie_da, ende):
    """Der simulierte Pi: meldet sich an und nimmt die Kontenkopie entgegen."""
    async with websockets.connect(
            f'ws://127.0.0.1:{PORT}/sync',
            additional_headers={'Authorization': f'Bearer {TOKEN}'}) as ws:
        await ws.send(json.dumps({
            'typ': 'hallo', 'fassung': 1, 'wand': time.time(), 'mono': 1000.0,
            'gestellt': True,
            'daten': {'geraet': 'test-pi', 'fassung': 1, 'version': '0.0.0',
                      'verlauf_folge': 0, 'betriebsart': 'voll',
                      'start': {'letztes_ende': 'erststart'},
                      'konten_stand': ''},
        }))
        verbunden.set()
        empfangen = []
        try:
            while not ende.is_set():
                roh = await asyncio.wait_for(ws.recv(), timeout=20)
                n = json.loads(roh)
                empfangen.append(n)
                if n.get('typ') == 'konten':
                    kopie_da.append(n.get('daten') or {})
                    if len(kopie_da) >= 2:      # erste beim Verbinden, zweite nach der Sperre
                        return empfangen
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass
        return empfangen


async def main():
    browser = Sitzung()

    print('\n1. Ersteinrichtung — noch kein Konto')
    status, z = await browser.http('/api/zugang')
    pruefe(status == 200, '/api/zugang ist ohne Anmeldung erreichbar')
    pruefe(z.get('ersteinrichtung') is True, 'meldet den Zustand der Ersteinrichtung')
    pruefe(z.get('angemeldet') is False, 'niemand ist angemeldet')

    status, _ = await browser.http('/api/status')
    pruefe(status == 401, 'ohne Anmeldung kein Zustand (401), auch bei der Ersteinrichtung')

    print('\n2. Notzugang legt das erste Konto an')
    status, _ = await browser.http('/api/konten', 'POST', bearer=NOTZUGANG, rumpf={
        'name': 'eigner', 'passwort': EIGNER_PW, 'rolle': 'eigner'})
    pruefe(status == 201, 'erstes Konto über den Notzugang angelegt')

    status, z = await browser.http('/api/zugang')
    pruefe(z.get('ersteinrichtung') is False, 'die Ersteinrichtung ist damit vorbei')

    status, _ = await browser.http('/api/konten', 'POST', bearer=NOTZUGANG, rumpf={
        'name': 'zweiter', 'passwort': EIGNER_PW, 'rolle': 'eigner'})
    pruefe(status == 401, 'der Notzugang gilt NICHT mehr, sobald ein Konto besteht')

    print('\n3. Anmeldung')
    status, _ = await browser.http('/api/login', 'POST',
                                   rumpf={'name': 'eigner', 'passwort': 'falsch'})
    pruefe(status == 401, 'falsches Passwort wird abgewiesen')
    pruefe(browser.keks is None, 'dabei entsteht kein Sitzungscookie')

    status, konto = await browser.http('/api/login', 'POST',
                                       rumpf={'name': 'eigner', 'passwort': EIGNER_PW})
    pruefe(status == 200, 'richtiges Passwort meldet an')
    pruefe(browser.keks is not None, 'das Sitzungscookie wird gesetzt')
    pruefe(konto.get('rolle') == 'eigner', 'die Rolle kommt zurück')
    pruefe('diagnose' in (konto.get('oberflaechen') or []),
           'der Eigner darf die Diagnose öffnen')

    status, _ = await browser.http('/api/status')
    pruefe(status in (200, 409), 'mit Sitzung ist der Zustand lesbar')

    print('\n4. Crew: darf bedienen, nicht verwalten')
    status, _ = await browser.http('/api/konten', 'POST', rumpf={
        'name': 'crew', 'passwort': CREW_PW, 'rolle': 'crew'})
    pruefe(status == 201, 'der Eigner legt ein Crew-Konto an')

    crew = Sitzung()
    status, konto = await crew.http('/api/login', 'POST',
                                    rumpf={'name': 'crew', 'passwort': CREW_PW})
    pruefe(status == 200, 'die Crew meldet sich an')
    pruefe('pwa' in (konto.get('oberflaechen') or []), 'die Crew darf die PWA')
    pruefe('diagnose' not in (konto.get('oberflaechen') or []),
           'die Crew darf die Diagnose NICHT — ausdrücklicher Eigner-Wunsch')

    status, _ = await crew.http('/api/konten')
    pruefe(status == 403, 'die Crew darf keine Konten sehen (403, nicht 401)')

    status, _ = await crew.http('/api/konten', 'POST', rumpf={
        'name': 'schmuggel', 'passwort': EIGNER_PW, 'rolle': 'eigner'})
    pruefe(status == 403, 'die Crew kann sich kein Eigner-Konto anlegen')

    print('\n5. Der Pi bekommt die Kontenkopie')
    verbunden, kopien, ende = asyncio.Event(), [], asyncio.Event()
    boot = asyncio.create_task(boot_rolle(verbunden, kopien, ende))
    await asyncio.wait_for(verbunden.wait(), timeout=15)
    await asyncio.sleep(2)
    pruefe(len(kopien) >= 1, 'beim Verbinden kommt die Kopie von selbst')
    if kopien:
        namen = {k['name'] for k in kopien[0].get('konten', [])}
        pruefe(namen == {'eigner', 'crew'}, f'sie enthält beide Konten ({namen})')
        pruefe(all(k.get('hash') for k in kopien[0]['konten']),
               'mitsamt Hashes — sonst ginge an Bord keine Anmeldung')
        pruefe(not any('passwort' in k for k in kopien[0]['konten']),
               'aber ohne Klartext-Passwörter')

    print('\n6. Sperren wirkt sofort und wandert ans Boot')
    status, _ = await browser.http('/api/konten/crew', 'PATCH', rumpf={'gesperrt': True})
    pruefe(status == 200, 'der Eigner sperrt das Crew-Konto')

    status, _ = await crew.http('/api/status')
    pruefe(status == 401, 'die laufende Crew-Sitzung ist sofort ungültig')

    await asyncio.sleep(2)
    pruefe(len(kopien) >= 2, 'die Änderung geht ohne Nachfrage ans Boot')
    if len(kopien) >= 2:
        gesperrt = [k for k in kopien[1].get('konten', []) if k['name'] == 'crew']
        pruefe(gesperrt and gesperrt[0].get('gesperrt') is True,
               'und trägt die Sperre')

    print('\n7. Selbstschutz')
    status, _ = await browser.http('/api/konten/eigner', 'PATCH', rumpf={'gesperrt': True})
    pruefe(status == 400, 'das eigene Konto lässt sich nicht sperren')
    status, _ = await browser.http('/api/konten/eigner', 'DELETE')
    pruefe(status == 400, 'und nicht löschen')

    status, _ = await browser.http('/api/status')
    pruefe(status in (200, 409), 'der Eigner ist danach noch angemeldet')

    print('\n8. Abmelden')
    status, _ = await browser.http('/api/logout', 'POST')
    pruefe(status == 200, 'Abmelden geht')
    status, _ = await browser.http('/api/status')
    pruefe(status == 401, 'danach ist nichts mehr lesbar')

    ende.set()
    try:
        await asyncio.wait_for(boot, timeout=5)
    except asyncio.TimeoutError:
        boot.cancel()

    print(f'\n{geprueft - len(fehler)} von {geprueft} Prüfungen bestanden')
    if fehler:
        print('\nOffen:')
        for f in fehler:
            print('  -', f)
        sys.exit(1)
    print('Die Kette trägt.')


if __name__ == '__main__':
    asyncio.run(main())

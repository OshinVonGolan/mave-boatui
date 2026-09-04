"""End-to-End: simulierter Pi gegen echten Server.

Prueft die Kette, die der Eigner gefragt hat: kann der Server dem Pi Befehle
schicken, obwohl der Pi keinen offenen Port hat?
"""
import asyncio, json, os, sys, time
import urllib.request, urllib.error
import websockets

PORT = os.environ['PORT']
TOKEN = os.environ['MAVE_GERAET_TOKEN']
PASSWORT = os.environ['MAVE_PASSWORT']
BASIS = f'http://127.0.0.1:{PORT}'
JETZT = time.time()

async def http(pfad, methode='GET', rumpf=None, passwort=PASSWORT):
    return await asyncio.to_thread(_http, pfad, methode, rumpf, passwort)


def _http(pfad, methode='GET', rumpf=None, passwort=PASSWORT):
    req = urllib.request.Request(BASIS + pfad, method=methode)
    if passwort:
        req.add_header('Authorization', f'Bearer {passwort}')
    daten = None
    if rumpf is not None:
        daten = json.dumps(rumpf).encode()
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, data=daten, timeout=15) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read() or b'{}')
        except Exception: return e.code, {}

async def pi_rolle(bereit, fertig, halbzeit, weiter):
    """Der simulierte Pi: verbindet ausgehend, meldet sich, fuehrt Befehle aus."""
    async with websockets.connect(f'ws://127.0.0.1:{PORT}/sync',
                                  additional_headers={'Authorization': f'Bearer {TOKEN}'}) as ws:
        # Anmeldung OHNE gestellte Uhr — so kommt der Pi nach einem
        # Stromausfall hoch, bevor NTP greift.
        await ws.send(json.dumps({
            'typ': 'hallo',
            'zeit': {'wand': 1000.0, 'mono': 100.0, 'gestellt': False},
            'daten': {'geraet': 'mave-pi', 'fassung': 1, 'version': '1.57.0',
                      'verlauf_folge': 0, 'betriebsart': 'voll',
                      'start': {'letztes_ende': 'abbruch', 'rechner_neu': True,
                                'rechner_start_wand': JETZT - 300}},
        }))
        stand = json.loads(await ws.recv())
        print(f"  Pi: Server nennt Verlaufsstand {stand['daten']['verlauf_bis']}")

        # Verlauf aus der Zeit ohne Uhr: nicht datierbar, muss geparkt werden.
        await ws.send(json.dumps({'typ': 'verlauf', 'folge': 2,
            'zeit': {'wand': 1000.0, 'mono': 102.0, 'gestellt': False},
            'daten': [{'folge': 1, 'mono': 40.0, 'gestellt': False, 'daten': {'soc': 79}},
                      {'folge': 2, 'mono': 70.0, 'gestellt': False, 'daten': {'soc': 78}}]}))
        await asyncio.sleep(0.4)
        halbzeit.set()
        await asyncio.wait_for(weiter.wait(), timeout=10)

        # Jetzt greift NTP. Ab hier ist der Bezug bekannt, und die geparkten
        # Eintraege muessen nachtraeglich eingeordnet werden.
        await ws.send(json.dumps({'typ': 'zustand',
            'zeit': {'wand': JETZT, 'mono': 150.0, 'gestellt': True},
            'daten': {'battery': {'soc': 82, 'voltage': 13.1}}}))
        await ws.send(json.dumps({'typ': 'verlauf', 'folge': 3,
            'zeit': {'wand': JETZT, 'mono': 151.0, 'gestellt': True},
            'daten': [{'folge': 3, 'wand': JETZT - 10, 'gestellt': True, 'daten': {'soc': 80}}]}))
        await asyncio.sleep(0.4)
        bereit.set()

        # Auf Befehle warten und quittieren — das ist der Kern der Frage.
        while not fertig.is_set():
            try:
                roh = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
            except asyncio.TimeoutError:
                continue
            if roh.get('typ') != 'befehl':
                continue
            b = roh['daten']
            print(f"  Pi: Befehl empfangen — {b['methode']} {b['pfad']} {b['rumpf']}")
            await ws.send(json.dumps({'typ': 'quittung', 'daten': {
                'kennung': b['kennung'], 'ok': True,
                'antwort': {'ausgefuehrt': b['pfad'], 'wert': (b['rumpf'] or {}).get('values')},
            }}))

async def main():
    bereit, fertig = asyncio.Event(), asyncio.Event()
    halbzeit, weiter = asyncio.Event(), asyncio.Event()
    aufgabe = asyncio.create_task(pi_rolle(bereit, fertig, halbzeit, weiter))

    fehler = []
    def pruefe(name, bedingung, zusatz=''):
        print(f"  {'OK  ' if bedingung else 'FEHL'} {name} {zusatz}")
        if not bedingung: fehler.append(name)

    # Zwischenstand: der Pi hat sich ohne gestellte Uhr gemeldet
    await asyncio.wait_for(halbzeit.wait(), timeout=10)
    print('\n— Pi ohne gestellte Uhr —')
    code, h = await http('/api/history?stunden=1')
    pruefe('nichts wird datiert geraten', code == 200 and len(h['eintraege']) == 0)
    pruefe('beide Eintraege geparkt', h['geparkt'] == 2, f"({h['geparkt']})")
    weiter.set()

    await asyncio.wait_for(bereit.wait(), timeout=10)

    print('\n— nach dem NTP-Abgleich —')
    code, z = await http('/api/status')
    pruefe('Zustand kommt an', code == 200 and z.get('battery', {}).get('soc') == 82)
    pruefe('Alter wird mitgeliefert', z.get('alter_s') is not None, f"({z.get('alter_s')} s)")
    pruefe('Quelle ist benannt', z.get('quelle') == 'server')

    code, h = await http('/api/history?stunden=1')
    pruefe('geparkte Eintraege sind nachgewandert', code == 200 and len(h['eintraege']) == 3,
           f"({len(h['eintraege'])} datiert, {h['geparkt']} geparkt)")
    pruefe('nichts bleibt liegen', h['geparkt'] == 0)
    zeiten = [e['zeit'] for e in h['eintraege']]
    pruefe('Reihenfolge stimmt', zeiten == sorted(zeiten))
    pruefe('zurueckgerechnet, nicht geraten', all(z < JETZT for z in zeiten))

    code, v = await http('/api/verbindung')
    pruefe('Verbindung wird gemeldet', code == 200 and v['verbunden'] is True)

    print('\n— Zugang —')
    code, _ = await http('/api/status', passwort=None)
    pruefe('ohne Anmeldung abgewiesen', code == 401)
    code, _ = await http('/api/status', passwort='falsch')
    pruefe('falsches Passwort abgewiesen', code == 401)

    print('\n— Befehl durch die Verbindung (die Frage des Eigners) —')
    code, a = await http('/api/lights/channels', 'POST', {'values': [255, 0, 0]})
    pruefe('Licht schalten geht durch', code == 200 and a.get('ausgefuehrt') == '/api/lights/channels',
           f"-> {a}")
    code, a = await http('/api/lights/preset/2', 'POST', {})
    pruefe('Pfadteil wird eingesetzt', code == 200 and a.get('ausgefuehrt') == '/api/lights/preset/2')

    print('\n— Grenzen —')
    code, a = await http('/api/heizung/heater', 'POST', {'mode': 'manual', 'command': 'on'})
    pruefe('Heizung fern gesperrt', code == 403, f"({a.get('detail','')[:40]}...)")
    # Ein Pfad, der auf der Weissliste NICHT steht, darf gar nicht erst
    # hinausgehen. /api/jserror ist so einer: Fehlermeldungen aus dem Browser
    # gehoeren nicht durch den Befehlskanal.
    code, _ = await http('/api/jserror', 'POST', {'meldung': 'x'})
    pruefe('nicht gelisteter Pfad existiert nicht', code in (404, 405))
    code, d = await http('/api/diagnose/uebersicht')
    pruefe('Diagnose fuer den Eigner offen', code == 200 and 'sitzungen' in d)

    fertig.set()
    aufgabe.cancel()
    await asyncio.sleep(0.3)

    print('\n— ohne Boot —')
    code, a = await http('/api/lights/channels', 'POST', {'values': [1]})
    pruefe('Schalten ohne Verbindung sagt es klar', code == 409, f"({a.get('detail','')})")

    print('\n' + ('ALLE PRUEFUNGEN BESTANDEN' if not fehler else f'FEHLGESCHLAGEN: {fehler}'))
    sys.exit(1 if fehler else 0)

asyncio.run(main())

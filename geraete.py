"""Geraeteuebersicht — gepflegte Stammdaten und Live-Zustand zu einer Liste verbinden.

Die Uebersicht hat zwei Haelften. Die eine ist `devices.json`: was an Bord
verbaut ist, wo es sitzt, an welcher Sicherung es haengt. Die andere sind die
Quellen, die ohnehin schon laufen — der CAN-Bus, der Router, der Stoker-Hub.
Dieses Modul bringt beide zusammen und beantwortet je Geraet genau eine Frage:
lebt es?

Alles hier ist absichtlich rein rechnend und ohne Netzzugriff. Gepollt wird
anderswo (can_reader, connectivity, heating); dieses Modul bekommt die fertigen
Momentaufnahmen gereicht. Damit ist es ohne Boot, ohne CAN und ohne Router
testbar — und es kann im Request-Pfad laufen, ohne etwas zu blockieren.
"""
from __future__ import annotations

import time

# ── Kategorien ──────────────────────────────────────────────────────────────
# Reihenfolge ist zugleich die Reihenfolge der Kacheln in der Oberflaeche.
# Ein Geraet hat GENAU EINE Kategorie. Sonst zaehlt dieselbe Sache in zwei
# Kacheln und die Zahlen auf der Startebene sind wertlos.
KATEGORIEN: list[tuple[str, str]] = [
    ('netzwerk',   'Netzwerk'),
    ('energie',    'Energie'),
    ('heizung',    'Heizung & Lüftung'),
    ('n2k',        'NMEA-2000-Bus'),
    ('navigation', 'Navigation'),
    ('wasser',     'Wasser'),
    ('licht',      'Licht'),
    ('motor',      'Motor & Antrieb'),
    ('sicherheit', 'Sicherheit'),
    ('sonstiges',  'Sonstiges'),
]
KATEGORIE_KEYS = {k for k, _ in KATEGORIEN}
KATEGORIE_NAMEN = dict(KATEGORIEN)

# ── Netze ───────────────────────────────────────────────────────────────────
# Das Boot hat ZWEI getrennte NMEA-2000-Netze. Der Pi haengt nur am Bordnetz;
# das Navigationsnetz sieht er nicht. Geraete dort sind deshalb nicht "offline"
# — sie sind ausserhalb der Reichweite dieser Anzeige, und genau das muss die
# Oberflaeche sagen, statt einen Ausfall zu behaupten.
NETZE: dict[str, str] = {
    'n2k-bord': 'Bordnetz (NMEA 2000)',
    'n2k-nav':  'Navigationsnetz (NMEA 2000)',
    'seatalk':  'SeaTalk',
    'lan':      'Bordnetzwerk (LAN/WLAN)',
    'vedirect': 'VE.Direct',
    'analog':   'analog verdrahtet',
    'keins':    'ohne Anschluss',
}
# Netze, die der Pi selbst nicht mithoert. Ein Geraet dort ohne eigene
# Live-Quelle bekommt den Status `fremdnetz`.
NETZE_OHNE_EINBLICK = {'n2k-nav', 'seatalk'}

# ── Status ──────────────────────────────────────────────────────────────────
STATUS_TEXT: dict[str, str] = {
    'online':    'online',
    'traege':    'träge',
    'offline':   'offline',
    'stumm':     'stumm',
    'fremdnetz': 'anderes Netz',
    'unbekannt': 'unbekannt',
}
# Nur diese drei sagen etwas ueber einen Ausfall aus. `stumm` (kein Melder
# verbaut) und `fremdnetz` (anderes Netz) sind KEIN Fehler und duerfen die
# Kachelfarbe nicht rot faerben.
STATUS_WERTEND = ('offline', 'unbekannt', 'traege', 'online')
# Vollstaendige Rangfolge fuer die Kachelfarbe. Sie MUSS alle Status enthalten:
# eine Menge ohne wertenden Status (nur stumm, nur fremdnetz) haette sonst kein
# festes Ergebnis, weil ueber ein set iteriert wuerde — dessen Reihenfolge
# haengt am Hash-Seed und wechselt von Programmstart zu Programmstart.
STATUS_RANG = STATUS_WERTEND + ('fremdnetz', 'stumm')

# Fristen des Bordbusses, uebernommen aus static/js/alarms.js (dort 3 s / 15 s
# fuer die Punktfarbe, 300 s bis ein Eintrag ausgeblendet wird). Bewusst
# dieselben Zahlen: Netzwerk-Ansicht und Geraeteseite duerfen nicht zweierlei
# ueber dasselbe Geraet behaupten.
N2K_FRIST_ONLINE = 15.0
N2K_FRIST_WEG    = 300.0
# Der Hub meldet sich im Sekundentakt; heating.py haelt 30 s fuer erreichbar.
STOKER_FRIST_TRAEGE = 30.0


# ── Hilfsfunktionen ─────────────────────────────────────────────────────────

def _mac(wert) -> str:
    """MAC vereinheitlichen. Die IP taugt nicht als Schluessel, die MAC schon."""
    if not isinstance(wert, str):
        return ''
    return wert.strip().lower().replace('-', ':')


def _txt(wert) -> str:
    return wert.strip() if isinstance(wert, str) else ''


def _status_aus_alter(age_s, frist_online: float, frist_weg: float) -> str:
    if age_s is None:
        return 'unbekannt'
    if age_s <= frist_online:
        return 'online'
    if age_s <= frist_weg:
        return 'traege'
    return 'offline'


def schlechtester(status_liste) -> str:
    """Schlechtester WERTENDER Status einer Gruppe, sonst der erste vorhandene.

    Eine Kachel mit fuenf stummen Geraeten ist nicht rot — sie ist neutral.
    """
    menge = set(status_liste)
    for s in STATUS_RANG:
        if s in menge:
            return s
    return 'stumm'


# ── Quellen aufbereiten ─────────────────────────────────────────────────────

def n2k_index(netzwerk: list[dict] | None, namen: dict | None = None) -> dict[int, dict]:
    """Bus-Eintraege (eine Zeile je PGN) zu einer Zeile je Geraet zusammenfassen.

    `namen` ist die vom Bediener gepflegte Zuordnung Quelladresse → Name aus
    presets.json. Sie ist der Notnagel, solange ein Geraet keine Produktinfo
    (PGN 126996) geschickt hat.
    """
    namen = namen or {}
    geraete: dict[int, dict] = {}
    for e in (netzwerk or []):
        src = e.get('src')
        if src is None:
            continue
        g = geraete.setdefault(src, {
            'src': src, 'name': '', 'name_hex': '', 'pgns': [], 'age_s': None,
        })
        if not g['name']:
            g['name'] = _txt(e.get('device_name'))
        if not g['name_hex']:
            g['name_hex'] = _txt(e.get('name_hex'))
        alter = e.get('age_s')
        if alter is not None and (g['age_s'] is None or alter < g['age_s']):
            g['age_s'] = alter
        g['pgns'].append({
            'pgn':         e.get('pgn'),
            'beschreibung': e.get('description'),
            'instanz':     e.get('instance'),
            'intervall_ms': e.get('interval_ms'),
            'anzahl':      e.get('count'),
            'age_s':       alter,
        })
    for src, g in geraete.items():
        if not g['name']:
            g['name'] = _txt(namen.get(str(src)) or namen.get(src))
        g['status'] = _status_aus_alter(g['age_s'], N2K_FRIST_ONLINE, N2K_FRIST_WEG)
        g['pgns'].sort(key=lambda p: (p['pgn'] is None, p['pgn']))
    return geraete


def lan_index(conn: dict | None) -> dict:
    """Was der Router ueber angeschlossene Geraete weiss — zwei Qualitaeten.

    Die WLAN-Liste ist eine Aussage ueber JETZT: wer dort steht, funkt gerade.
    Eine DHCP-Lease ist etwas Schwaecheres: das Geraet hat hier einmal eine
    Adresse bekommen, und die laeuft noch stundenlang weiter, auch wenn es
    laengst von Bord ist. Beides in einen Topf zu werfen hiesse, ein
    weggefahrenes Handy als "online" zu zeigen.

    Die Leases sind trotzdem noetig: ein Geraet am KABEL taucht in der
    WLAN-Liste grundsaetzlich nicht auf.
    """
    router = (conn or {}).get('router') or {}
    clients = router.get('wifi_clients')
    leases  = router.get('dhcp_leases')
    if clients is None and not leases:
        return {'verfuegbar': False, 'funk': {}, 'leases': {}, 'clients': []}
    funk = {}
    for c in (clients or []):
        m = _mac(c.get('mac'))
        if m:
            funk[m] = c
    nach_lease = {}
    for l in (leases or []):
        m = _mac(l.get('mac'))
        if m:
            nach_lease[m] = l
    return {'verfuegbar': True, 'funk': funk, 'leases': nach_lease,
            'clients': list(clients or [])}


def stoker_index(snapshot: dict | None) -> dict:
    """Hub und Raumknoten aus der Momentaufnahme von heating.py.

    Kein eigener Poll: heating.py fragt den Hub bereits zentral ab (hoechstens
    1 Hz laut Geraetedoku). Ein zweiter Abruf von hier waere genau der Fehler,
    den die Doku verbietet.
    """
    snap = snapshot or {}
    if not snap.get('configured'):
        return {'verfuegbar': False, 'hub': None, 'raeume': {}}
    info  = snap.get('info')  or {}
    state = snap.get('state') or {}
    erreichbar = bool(snap.get('reachable'))
    hub = {
        'status':   'online' if erreichbar else 'offline',
        'ip':       ((info.get('wifi') or {}).get('ip') or ''),
        'ssid':     ((info.get('wifi') or {}).get('ssid') or ''),
        'rssi':     (info.get('wifi') or {}).get('rssi'),
        'firmware': info.get('firmware', ''),
        'uptime_s': info.get('uptimeS'),
        'age_s':    snap.get('age_s'),
        'fehler':   snap.get('error'),
    }
    raeume: dict[str, dict] = {}
    for r in (state.get('rooms') or []):
        conn = _txt(r.get('conn')) or 'unknown'
        status = {'online': 'online', 'stale': 'traege', 'connecting': 'traege',
                  'offline': 'offline'}.get(conn, 'unbekannt')
        if not erreichbar:
            # Der Hub ist die einzige Quelle fuer die Raumknoten. Ist er weg,
            # wissen wir ueber die Knoten nichts — nicht, dass sie tot sind.
            status = 'unbekannt'
        eintrag = {
            'status': status, 'conn': conn, 'rssi': r.get('rssi'),
            'age_s': r.get('lastSeenS'), 'fault': r.get('fault'),
            'raumtemp': r.get('roomTemp'), 'ziel': r.get('target'),
            'name': _txt(r.get('name')), 'roomId': r.get('id'), 'nodeId': r.get('nodeId'),
        }
        if r.get('id') is not None:
            raeume[f"id:{r['id']}"] = eintrag
        if eintrag['name']:
            raeume[f"name:{eintrag['name'].lower()}"] = eintrag
        if r.get('nodeId') is not None:
            raeume[f"node:{r['nodeId']}"] = eintrag
    return {'verfuegbar': True, 'hub': hub, 'raeume': raeume,
            'heizung': state.get('heater') or {}}


def intern_index(conn: dict | None, pi_online: bool = True) -> dict:
    """Quellen ohne eigene Adresse: Router, Starlink, der Pi selbst."""
    c = conn or {}
    router   = c.get('router')
    starlink = c.get('starlink')
    eintraege = {
        'pi': {'status': 'online' if pi_online else 'unbekannt',
               'kennzahlen': []},
    }
    if router:
        art = _txt(router.get('active_type')) or 'unbekannt'
        eintraege['router'] = {
            'status': 'online',
            'kennzahlen': [
                ('Uplink', {'wired': 'Kabel', 'mobile': 'Mobilfunk',
                            'wifi': 'WLAN'}.get(art, art)),
                ('WAN-IP', _txt(router.get('wan_ip'))),
                ('WLAN-Clients', router.get('wifi_client_count')),
            ],
        }
    else:
        eintraege['router'] = {'status': 'unbekannt', 'kennzahlen': []}
    if starlink:
        # Ein Dish-Datensatz ohne Zustandsfeld ist trotzdem eine Antwort —
        # sie kam gerade an, also lebt die Dish.
        zustand = _txt(starlink.get('state'))
        eintraege['starlink'] = {
            'status': 'online' if starlink.get('online', True) else 'offline',
            'kennzahlen': [('Zustand', zustand)] if zustand else [],
        }
    else:
        eintraege['starlink'] = {'status': 'unbekannt', 'kennzahlen': []}
    return eintraege


# ── Zuordnung Stammdaten → Live ─────────────────────────────────────────────
# Warum nicht einfach ueber die Quelladresse? Weil die NMEA-2000-Adresse beim
# Address Claim ausgehandelt wird und sich nach einem Neustart aendern kann.
# Stabil ist das 64-Bit-NAME aus PGN 60928 (`name_hex`). Danach kommt der
# Modellname aus PGN 126996, und erst als letzter Notnagel die Adresse.

def _pgn_treffer(geraet: dict, match: dict) -> dict | None:
    """Einzelne PGN eines Busgeraets — fuer alles, was HINTER einem Gateway sitzt.

    Der MPPT, der Orion und die beiden Phoenix haengen nicht selbst am CAN; sie
    reden VE.Direct mit dem Gateway, das daraus je Geraet eine eigene PGN macht.
    Wer nur auf das Gateway schaut, sieht vier Geraete als ein einziges. Ueber
    die PGN (und noetigenfalls die Instanz) wird jedes davon einzeln sichtbar.
    """
    pgn = match.get('pgn')
    if pgn is None:
        return None
    instanz = match.get('instanz')
    for p in geraet['pgns']:
        if p.get('pgn') != pgn:
            continue
        if instanz is not None and p.get('instanz') != instanz:
            continue
        return p
    return None


def _n2k_treffer(match: dict, index: dict[int, dict]) -> tuple[dict | None, str]:
    hex_soll = _txt(match.get('name_hex')).lower()
    if hex_soll:
        for g in index.values():
            if _txt(g.get('name_hex')).lower() == hex_soll:
                return g, 'name'
    modell = _txt(match.get('device_name')).lower()
    if modell:
        for g in index.values():
            if _txt(g.get('name')).lower() == modell:
                return g, 'modell'
    src = match.get('src')
    if isinstance(src, int) and src in index:
        return index[src], 'adresse'
    return None, ''


def _stoker_treffer(match: dict, raeume: dict) -> dict | None:
    for schluessel in (
        f"id:{match['roomId']}"       if match.get('roomId')   is not None else None,
        f"node:{match['nodeId']}"     if match.get('nodeId')   is not None else None,
        f"name:{_txt(match.get('roomName')).lower()}" if match.get('roomName') else None,
    ):
        if schluessel and schluessel in raeume:
            return raeume[schluessel]
    return None


def _kennzahlen(paare) -> list[dict]:
    """Label/Wert-Paare fuer die Anzeige. Leeres fliegt raus, 0 bleibt."""
    raus = []
    for label, wert in paare:
        if wert is None or wert == '':
            continue
        raus.append({'l': label, 'v': str(wert)})
    return raus


def _zustand_eines(eintrag: dict, quellen: dict) -> dict:
    """Live-Zustand fuer einen Registry-Eintrag ermitteln."""
    match = eintrag.get('match') or {}
    typ   = _txt(match.get('typ'))
    netz  = _txt(eintrag.get('netz'))

    if not typ:
        # Kein Melder vorgesehen. Im Navigationsnetz oder auf SeaTalk redet das
        # Geraet zwar, nur hoert der Pi dort nicht mit — das ist etwas anderes
        # als ein Geraet ohne jede Rueckmeldung.
        status = 'fremdnetz' if netz in NETZE_OHNE_EINBLICK else 'stumm'
        return {'status': status, 'kennzahlen': [], 'live': None, 'quelle': ''}

    if typ == 'n2k':
        idx = quellen['n2k']
        treffer, wie = _n2k_treffer(match, idx)
        if treffer is None:
            # Bus laeuft, Geraet nicht dabei: das ist eine echte Aussage.
            status = 'offline' if quellen['n2k_verfuegbar'] else 'unbekannt'
            return {'status': status, 'kennzahlen': [], 'live': None, 'quelle': 'n2k'}
        if match.get('pgn') is not None:
            teil = _pgn_treffer(treffer, match)
            if teil is None:
                # Das Traegergeraet sendet, nur diese PGN nicht. Genau der Fall
                # bei Hardware, die vorbereitet, aber nicht angeschlossen ist.
                return {'status': 'offline',
                        'kennzahlen': _kennzahlen([('über', treffer['name'] or treffer['src']),
                                                   ('PGN', match['pgn'])]),
                        'live': {'src': treffer['src'], 'pgns': [], 'age_s': None,
                                 'zuordnung': wie, 'traeger': treffer['name']},
                        'quelle': 'n2k'}
            alter = teil.get('age_s')
            return {
                'status': _status_aus_alter(alter, N2K_FRIST_ONLINE, N2K_FRIST_WEG),
                'kennzahlen': _kennzahlen([
                    ('über',    treffer['name'] or treffer['src']),
                    ('PGN',     teil.get('pgn')),
                    ('Takt',    f"{teil['intervall_ms']} ms" if teil.get('intervall_ms') else ''),
                    ('zuletzt', f"vor {alter:.0f} s" if alter is not None else ''),
                ]),
                'live': {'src': treffer['src'], 'name_hex': treffer['name_hex'],
                         'pgns': [teil], 'age_s': alter, 'zuordnung': wie,
                         'traeger': treffer['name']},
                'quelle': 'n2k',
            }
        return {
            'status': treffer['status'],
            'kennzahlen': _kennzahlen([
                ('Adresse', treffer['src']),
                ('PGNs',    len(treffer['pgns'])),
                ('zuletzt', f"vor {treffer['age_s']:.0f} s" if treffer['age_s'] is not None else ''),
            ]),
            'live': {'src': treffer['src'], 'name_hex': treffer['name_hex'],
                     'pgns': treffer['pgns'], 'age_s': treffer['age_s'],
                     'zuordnung': wie},
            'quelle': 'n2k',
        }

    if typ == 'lan':
        lan = quellen['lan']
        if not lan['verfuegbar']:
            return {'status': 'unbekannt', 'kennzahlen': [], 'live': None, 'quelle': 'lan'}
        mac = _mac(match.get('mac'))
        c = lan['funk'].get(mac)
        if c:
            return {
                'status': 'online',
                'kennzahlen': _kennzahlen([
                    ('IP',    c.get('ip')),
                    ('WLAN',  f"{c['signal']} dBm" if c.get('signal') is not None else ''),
                    ('Band',  c.get('band')),
                    ('SSID',  c.get('ssid')),
                ]),
                'live': dict(c),
                'quelle': 'lan',
            }
        lease = lan['leases'].get(mac)
        if lease:
            # Adresse ja, Verbindung ungeklaert. Bei einem Kabelgeraet ist das
            # alles, was der Router hergibt — als "online" waere es geraten.
            return {
                'status': 'unbekannt',
                'kennzahlen': _kennzahlen([
                    ('IP',       lease.get('ip')),
                    ('Anschluss', lease.get('interface')),
                    ('Hinweis',  'Adresse vergeben, Verbindung nicht bestätigt'),
                ]),
                'live': dict(lease),
                'quelle': 'lan',
            }
        return {'status': 'offline', 'kennzahlen': [], 'live': None, 'quelle': 'lan'}

    if typ == 'stoker':
        st = quellen['stoker']
        if not st['verfuegbar']:
            return {'status': 'unbekannt', 'kennzahlen': [], 'live': None, 'quelle': 'stoker'}
        if _txt(match.get('rolle')) == 'hub':
            hub = st['hub'] or {}
            return {
                'status': hub.get('status', 'unbekannt'),
                'kennzahlen': _kennzahlen([
                    ('IP',       hub.get('ip')),
                    ('WLAN',     f"{hub['rssi']} dBm" if hub.get('rssi') is not None else ''),
                    ('Firmware', hub.get('firmware')),
                    ('Laufzeit', _dauer(hub.get('uptime_s'))),
                ]),
                'live': dict(hub),
                'quelle': 'stoker',
            }
        raum = _stoker_treffer(match, st['raeume'])
        if raum is None:
            return {'status': 'unbekannt', 'kennzahlen': [], 'live': None, 'quelle': 'stoker'}
        stoerung = _txt(raum.get('fault'))
        return {
            'status': raum['status'],
            'kennzahlen': _kennzahlen([
                ('WLAN',      f"{raum['rssi']} dBm" if raum.get('rssi') is not None else ''),
                ('Raumtemp.', f"{raum['raumtemp']:.1f} °C" if isinstance(raum.get('raumtemp'), (int, float)) else ''),
                ('zuletzt',   f"vor {raum['age_s']:.0f} s" if isinstance(raum.get('age_s'), (int, float)) else ''),
                ('Störung',   stoerung if stoerung and stoerung != 'none' else ''),
            ]),
            'live': dict(raum),
            'quelle': 'stoker',
        }

    if typ == 'intern':
        eintr = quellen['intern'].get(_txt(match.get('key')))
        if not eintr:
            return {'status': 'unbekannt', 'kennzahlen': [], 'live': None, 'quelle': 'intern'}
        return {'status': eintr['status'],
                'kennzahlen': _kennzahlen(eintr['kennzahlen']),
                'live': None, 'quelle': 'intern'}

    return {'status': 'unbekannt', 'kennzahlen': [], 'live': None, 'quelle': typ}


def _dauer(sekunden) -> str:
    if not isinstance(sekunden, (int, float)) or sekunden < 0:
        return ''
    s = int(sekunden)
    if s < 3600:
        return f'{s // 60} min'
    if s < 86400:
        return f'{s // 3600} h {(s % 3600) // 60} min'
    return f'{s // 86400} d {(s % 86400) // 3600} h'


# ── Aggregation ─────────────────────────────────────────────────────────────

# Felder, die aus der Registry unveraendert nach vorn durchgereicht werden.
_STAMM_FELDER = ('id', 'name', 'kategorie', 'netz', 'ort', 'hersteller', 'modell',
                 'seriennr', 'baujahr', 'versorgung', 'sicherung', 'doku', 'notiz',
                 'verbunden_an', 'sprung', 'bruecke_zu')


def _lan_fund(mac: str, name: str, status: str, kennzahlen: list, roh: dict) -> dict:
    """Ein Geraet im Bordnetzwerk, das in keiner Liste steht."""
    return {
        'id': f'lan-{mac.replace(":", "")}', 'name': name or roh.get('ip') or mac,
        'kategorie': 'netzwerk', 'netz': 'lan', 'netz_name': NETZE['lan'],
        'ort': '', 'hersteller': '', 'modell': '', 'seriennr': '', 'baujahr': '',
        'versorgung': '', 'sicherung': '', 'doku': '', 'notiz': '',
        'verbunden_an': '', 'sprung': 'connectivity',
        'status': status, 'status_text': STATUS_TEXT[status],
        'kennzahlen': kennzahlen, 'live': dict(roh), 'quelle': 'lan',
        'gepflegt': False, 'vorschlag': {'typ': 'lan', 'mac': mac},
    }


def _bruecken(eintrag: dict) -> list[str]:
    """Weitere Netze, in denen ein Geraet haengt — fuer die Verbindungskarte.

    Der Pi ist der Fall, der niemandem auffaellt und den niemand pflegen sollte:
    er haengt im Bordnetzwerk UND liest den CAN-Bus. Das ist keine Angabe aus
    der Liste, sondern eine Eigenschaft dieses Rechners — er ist ja das Geraet,
    das diese Antwort schreibt. Ohne ihn staenden die beiden Netze in der Karte
    beziehungslos nebeneinander.
    """
    roh = eintrag.get('bruecke_zu')
    raus = [n for n in (roh or []) if _txt(n) in NETZE and _txt(n) != _txt(eintrag.get('netz'))]
    match = eintrag.get('match') or {}
    if _txt(match.get('typ')) == 'intern' and _txt(match.get('key')) == 'pi':
        if 'n2k-bord' not in raus and _txt(eintrag.get('netz')) != 'n2k-bord':
            raus.append('n2k-bord')
    return raus


def aggregiere(registry, netzwerk=None, presets_devices=None, stoker_snapshot=None,
               conn_status=None, jetzt=None, eigener_host=None) -> dict:
    """Ein Aufruf, eine fertige Liste. Das Frontend rechnet nichts zusammen."""
    quellen = {
        'n2k':            n2k_index(netzwerk, presets_devices),
        'n2k_verfuegbar': bool(netzwerk),
        'lan':            lan_index(conn_status),
        'stoker':         stoker_index(stoker_snapshot),
        'intern':         intern_index(conn_status),
    }

    geraete: list[dict] = []
    belegt_n2k: set[int] = set()
    belegt_lan: set[str] = set()
    # Ein Geraet, das schon ueber eine andere Quelle bekannt ist, darf nicht
    # ZUSAETZLICH als unbekannter WLAN-Client erscheinen. Der Stoker-Hub meldet
    # seine IP selbst, der Pi kennt seinen eigenen Rechnernamen — beides reicht,
    # um dieselbe Kiste wiederzuerkennen, ohne dass jemand MACs pflegen muss.
    belegt_ip: set[str] = set()
    belegt_host: set[str] = {eigener_host.strip().lower()} if _txt(eigener_host) else set()

    for eintrag in (registry or []):
        if not isinstance(eintrag, dict):
            continue
        zustand = _zustand_eines(eintrag, quellen)
        geraet = {f: eintrag.get(f) for f in _STAMM_FELDER}
        geraet['name'] = _txt(eintrag.get('name')) or _txt(eintrag.get('id'))
        geraet['kategorie'] = (_txt(eintrag.get('kategorie')) if _txt(eintrag.get('kategorie')) in KATEGORIE_KEYS
                               else 'sonstiges')
        geraet['netz_name'] = NETZE.get(_txt(eintrag.get('netz')), '')
        geraet['bruecke_zu'] = _bruecken(eintrag)
        geraet['status'] = zustand['status']
        geraet['status_text'] = STATUS_TEXT.get(zustand['status'], zustand['status'])
        geraet['kennzahlen'] = zustand['kennzahlen']
        geraet['live'] = zustand['live']
        geraet['quelle'] = zustand['quelle']
        geraet['gepflegt'] = True
        geraete.append(geraet)

        match = eintrag.get('match') or {}
        if zustand['live'] and zustand['quelle'] == 'n2k':
            belegt_n2k.add(zustand['live']['src'])
        if zustand['quelle'] == 'lan' and match.get('mac'):
            belegt_lan.add(_mac(match.get('mac')))
        if isinstance(zustand['live'], dict):
            ip = _txt(zustand['live'].get('ip'))
            if ip:
                belegt_ip.add(ip)

    # Was am Bus haengt, aber in keiner Liste steht, verschwindet nicht — es
    # wird als unbekanntes Geraet gezeigt und kann uebernommen werden. So
    # fuellt sich die Registry im Betrieb statt in einer Tippsitzung.
    for src, g in sorted(quellen['n2k'].items()):
        if src in belegt_n2k:
            continue
        geraete.append({
            'id': f'n2k-{src}', 'name': g['name'] or f'Gerät {src}',
            'kategorie': 'n2k', 'netz': 'n2k-bord', 'netz_name': NETZE['n2k-bord'],
            'ort': '', 'hersteller': '', 'modell': '', 'seriennr': '', 'baujahr': '',
            'versorgung': '', 'sicherung': '', 'doku': '', 'notiz': '',
            'verbunden_an': '', 'sprung': 'netzwerk',
            'status': g['status'], 'status_text': STATUS_TEXT.get(g['status'], g['status']),
            'kennzahlen': _kennzahlen([
                ('Adresse', src), ('PGNs', len(g['pgns'])),
                ('zuletzt', f"vor {g['age_s']:.0f} s" if g['age_s'] is not None else ''),
            ]),
            'live': {'src': src, 'name_hex': g['name_hex'], 'pgns': g['pgns'],
                     'age_s': g['age_s'], 'zuordnung': ''},
            'quelle': 'n2k', 'gepflegt': False,
            'vorschlag': {'typ': 'n2k', 'name_hex': g['name_hex'], 'src': src,
                          'device_name': g['name']},
        })

    gesehen_lan: set[str] = set()
    for mac, c in sorted(quellen['lan']['funk'].items()):
        gesehen_lan.add(mac)
        if (mac in belegt_lan
                or _txt(c.get('ip')) in belegt_ip
                or _txt(c.get('hostname')).lower() in belegt_host):
            continue
        geraete.append(_lan_fund(mac, _txt(c.get('hostname')), 'online', _kennzahlen([
            ('IP', c.get('ip')),
            ('WLAN', f"{c['signal']} dBm" if c.get('signal') is not None else ''),
            ('MAC', mac),
        ]), c))

    for mac, l in sorted(quellen['lan']['leases'].items()):
        if (mac in gesehen_lan or mac in belegt_lan
                or _txt(l.get('ip')) in belegt_ip
                or _txt(l.get('hostname')).lower() in belegt_host):
            continue
        geraete.append(_lan_fund(mac, _txt(l.get('hostname')), 'unbekannt', _kennzahlen([
            ('IP', l.get('ip')),
            ('Anschluss', l.get('interface')),
            ('MAC', mac),
            ('Hinweis', 'Adresse vergeben, Verbindung nicht bestätigt'),
        ]), l))

    kategorien = []
    for key, name in KATEGORIEN:
        teil = [g for g in geraete if g['kategorie'] == key]
        if not teil:
            continue
        status_menge = {g['status'] for g in teil}
        kategorien.append({
            'key': key, 'name': name,
            'gesamt':  len(teil),
            'online':  sum(1 for g in teil if g['status'] == 'online'),
            'problem': sum(1 for g in teil if g['status'] in ('offline', 'unbekannt')),
            'status':  schlechtester(status_menge),
        })

    return {
        'ts': jetzt if jetzt is not None else time.time(),
        'kategorien': kategorien,
        'geraete': geraete,
        'netze': [{'key': k, 'name': v} for k, v in NETZE.items()],
        'quellen': {
            'n2k':    {'verfuegbar': quellen['n2k_verfuegbar'], 'geraete': len(quellen['n2k'])},
            'lan':    {'verfuegbar': quellen['lan']['verfuegbar'],
                       'clients': len(quellen['lan']['funk']),
                       'adressen': len(quellen['lan']['leases'])},
            'stoker': {'verfuegbar': quellen['stoker']['verfuegbar'],
                       'hub': (quellen['stoker']['hub'] or {}).get('status', 'unbekannt')},
        },
    }


# ── Registry pruefen ────────────────────────────────────────────────────────
# Streng nach dem Muster von presets.json: unbekannte Felder werden abgelehnt,
# damit die Datei nicht bei jedem Tippfehler um einen Eintrag waechst.

_ERLAUBTE_FELDER = set(_STAMM_FELDER) | {'match'}
_MATCH_FELDER = {
    'n2k':    {'typ', 'name_hex', 'src', 'device_name', 'pgn', 'instanz'},
    'lan':    {'typ', 'mac'},
    'stoker': {'typ', 'rolle', 'roomId', 'nodeId', 'roomName'},
    'intern': {'typ', 'key'},
}


class RegistryFehler(ValueError):
    """Ungueltige Registry — die Meldung geht wortwoertlich an den Bediener."""


def pruefe_registry(daten) -> list[dict]:
    if not isinstance(daten, list):
        raise RegistryFehler('Liste erwartet.')
    gesehen: set[str] = set()
    sauber: list[dict] = []
    for i, e in enumerate(daten, 1):
        if not isinstance(e, dict):
            raise RegistryFehler(f'Eintrag {i}: Objekt erwartet.')
        unbekannt = set(e) - _ERLAUBTE_FELDER
        if unbekannt:
            raise RegistryFehler(f'Eintrag {i}: unbekanntes Feld {sorted(unbekannt)[0]!r}.')
        kennung = _txt(e.get('id'))
        if not kennung:
            raise RegistryFehler(f'Eintrag {i}: id fehlt.')
        if kennung in gesehen:
            raise RegistryFehler(f'id {kennung!r} kommt doppelt vor.')
        gesehen.add(kennung)
        kategorie = _txt(e.get('kategorie'))
        if kategorie and kategorie not in KATEGORIE_KEYS:
            raise RegistryFehler(f'{kennung}: Kategorie {kategorie!r} gibt es nicht.')
        netz = _txt(e.get('netz'))
        if netz and netz not in NETZE:
            raise RegistryFehler(f'{kennung}: Netz {netz!r} gibt es nicht.')
        bruecke = e.get('bruecke_zu')
        if bruecke is not None:
            if not isinstance(bruecke, list):
                raise RegistryFehler(f'{kennung}: bruecke_zu muss eine Liste sein.')
            for n in bruecke:
                if _txt(n) not in NETZE:
                    raise RegistryFehler(f'{kennung}: Netz {n!r} gibt es nicht.')
        match = e.get('match')
        if match is not None:
            if not isinstance(match, dict):
                raise RegistryFehler(f'{kennung}: match muss ein Objekt sein.')
            typ = _txt(match.get('typ'))
            if typ not in _MATCH_FELDER:
                raise RegistryFehler(f'{kennung}: match.typ {typ!r} gibt es nicht.')
            zuviel = set(match) - _MATCH_FELDER[typ]
            if zuviel:
                raise RegistryFehler(f'{kennung}: match kennt {sorted(zuviel)[0]!r} nicht.')
        sauber.append(e)
    return sauber

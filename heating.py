"""Anbindung an die Stoker-Heizungssteuerung (apiVersion 1).

Der Pi fragt den Heizungsknoten zentral ab und haelt den letzten Zustand vor.
Absicht: die Doku begrenzt auf hoechstens 1 Abfrage pro Sekunde und vier
WebSocket-Verbindungen im GANZEN Netz — dahinter sitzt ein ESP32, der nebenbei
regelt. Wuerde jedes Handy selbst pollen, waere das schnell ueberschritten.
Zweiter Grund: `.local`-Namen loest nicht jedes Geraet auf (Android), und aus
dem Browser heraus gaebe es CORS-Aerger.

Bewusst mit urllib aus der Standardbibliothek statt httpx: auf dem Pi Zero
soll die Heizung keine zusaetzliche Abhaengigkeit mitbringen, und die paar
Anfragen brauchen nichts Groesseres.

Der Relaisbetrieb aus der Geraetedoku ist hier absichtlich nicht abgebildet —
er faellt beim Eigner ohnehin weg.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from jsonio import read_json, write_json

log = logging.getLogger(__name__)

# Abfragetakt. Die Doku erlaubt 1 Hz; 2 s lassen der Regelung Luft und reichen
# fuer eine Anzeige voellig — die Heizung aendert sich nicht in Millisekunden.
_POLL_S = 2.0
# Nach einem Fehlschlag langsamer weiterfragen, statt ins Leere zu haemmern.
_POLL_FEHLER_S = 15.0
_TIMEOUT_S = 4.0

_VORGABEN: dict = {
    'enabled': False,       # erst einschalten, wenn ein Host eingetragen ist
    'host': '',             # z. B. "stoker-bf38.local" oder "192.168.1.60"
    'password': '',         # nur noetig, wenn der Schreibschutz an ist
    'set_time': True,       # Uhr des Geraets stellen, es hat keine gepufferte
    # Frostwacht je Raum: {"1": true, "2": false}. Schluessel sind Raum-IDs als
    # Text (JSON kennt keine Zahlschluessel). Ein Raum, der hier NICHT steht,
    # wird bewacht — bei einem Schutz gegen Sachschaden ist "an" die richtige
    # Vorgabe, sonst haette ein frisch angelernter Fuehler stillschweigend
    # keine Wache.
    'frostwacht': {},
}


def _raum_kennzahlen(state: dict | None) -> dict:
    """Raumliste zu Zahlen verdichten.

    Die Alarm-Engine laeuft mit Punktpfaden durch Dicts und kann NICHT in
    Listen greifen (alarm_engine._get_field). Alles, was einzelne Raeume
    betrifft, muss deshalb hier zu einem Skalar werden. Zehn Raeume rund
    2,4 mal je Sekunde durchzugehen kostet nichts.

    Gezaehlt wird nur, was der Eigner auch eingeschaltet hat: ein bewusst
    abgeschalteter Raum ist kein Fehler, und ein Fuehler in einem
    abgeschalteten Raum darf keinen Alarm ausloesen.
    """
    raeume = (state or {}).get('rooms') or []
    aktiv  = [r for r in raeume if isinstance(r, dict) and r.get('enabled') is not False]
    online = [r for r in aktiv if r.get('conn') == 'online']
    temps  = [r.get('roomTemp') for r in online
              if isinstance(r.get('roomTemp'), (int, float))]
    # Ist ueberhaupt ein Fuehler angelernt? An diesem Boot ist die Raumliste
    # derzeit leer (am Geraet geprueft) — dann darf NICHTS abgeleitet werden,
    # sonst stuende sofort ein Dauer-Alarm "kein Raum online" in der Liste.
    verbaut = len(aktiv) > 0
    return {
        'raeume_gesamt':  len(raeume),
        'raeume_aktiv':   len(aktiv),
        'raeume_online':  len(online) if verbaut else None,
        'raeume_offline': (len(aktiv) - len(online)) if verbaut else None,
        # ACHTUNG: fault ist immer ein String; der stoerungsfreie Normalwert
        # heisst "none" (Firmware lib/core/include/mave/types.h, RoomFault) und
        # ist damit truthy. Ein blosses `if r.get('fault')` wuerde jeden Raum
        # als gestoert zaehlen.
        'raeume_fehler':  sum(1 for r in aktiv
                              if r.get('fault') not in (None, '', 'none')) if verbaut else None,
        # Kaeltester eingeschalteter Raum, der auch meldet. None, wenn keiner
        # meldet — die Alarmregel wertet dann nicht aus, statt 0 anzunehmen.
        'raum_temp_min':  round(min(temps), 1) if temps else None,
    }


def _heizgeraet_verbaut(state: dict | None) -> bool:
    """Haengt ueberhaupt ein Heizgeraet an der Leitung des Hubs?

    Am laufenden Hub steht hier heute availability='not_wired', available=False
    und flowTemp=None: der ESP laeuft, das Autoterm-Geraet ist noch nicht
    angeschlossen. Alles, was das Geraet selbst betrifft (Fehlercode,
    Leitungsalter), darf deshalb erst beurteilt werden, wenn es verbaut ist.
    """
    h = ((state or {}).get('heater') or {})
    if h.get('available') is True:
        return True
    # Die Firmware kennt genau "ok", "not_wired", "no_contact", "bus_silent"
    # und "bus_error" (lib/platform/web/heater_health.h). Nur bei "ok" sind die
    # Geraetewerte frisch — bei den uebrigen sind sie alt und duerfen keinen
    # Alarm mehr tragen.
    return h.get('availability') == 'ok'


class _Haltezeit:
    """Wie lange ist eine Bedingung schon ununterbrochen wahr?

    Die Alarm-Engine kennt keine Entprellung — sie schlaegt beim ersten
    Messwert an, der die Schwelle reisst (alarm_engine.check). Ein kurzer
    Funkaussetzer wuerde damit sofort einen Alarm erzeugen. Statt die Engine
    umzubauen, liefern wir ihr Sekunden: die Regel vergleicht dann "wie lange
    schon" gegen eine Schwelle, und die Entprellung steckt in der Schwelle.

    Gemerkt wird ein Zeitpunkt, kein Zaehler — mehrfaches Abfragen im selben
    Moment veraendert das Ergebnis also nicht. Wichtig sind die drei Rueckgaben:
      None  Bedingung nicht beurteilbar (Anlage nicht verbaut, Daten fehlen).
            Die Engine ueberspringt None und loescht dabei einen bereits
            stehenden Alarm NICHT — genau richtig fuer eine Datenluecke.
      0.0   Bedingung ist falsch. Ausdruecklich eine Zahl und nicht None,
            damit ein stehender Alarm sich auch wieder aufloest.
      >0    Sekunden seit dem Wahrwerden.
    """

    __slots__ = ('_seit',)

    def __init__(self):
        self._seit: float | None = None

    def __call__(self, wahr: bool | None) -> float | None:
        if wahr is None:
            self._seit = None
            return None
        if not wahr:
            self._seit = None
            return 0.0
        jetzt = time.monotonic()
        if self._seit is None:
            self._seit = jetzt
        return round(jetzt - self._seit, 1)


class StokerClient:
    """Haelt Konfiguration, pollt den Hub und reicht Schaltbefehle durch."""

    def __init__(self, config_path: Path):
        self._path = config_path
        self._cfg = {**_VORGABEN, **(read_json(config_path, {}) or {})}
        self._lock = threading.Lock()
        self._state: dict | None = None      # letzte Antwort von /api/state
        self._info: dict | None = None       # letzte Antwort von /api/info
        self._last_ok: float | None = None   # monotone Zeit des letzten Erfolgs
        self._last_error: str | None = None
        self._zeit_gesetzt = False
        self._running = False
        self._thread: threading.Thread | None = None
        # Entprellung fuer die Alarmfelder (siehe _Haltezeit). Die fehlende
        # Verbindung braucht keinen: dafuer gibt es schon age_s.
        self._hz_fehler = _Haltezeit()   # Heizgeraet meldet Fehlercode
        self._hz_raeume = _Haltezeit()   # mindestens ein Fuehler stumm
        self._hz_leer   = _Haltezeit()   # kein einziger Fuehler mehr online

    # ── Konfiguration ───────────────────────────────────────────────────────

    def settings(self) -> dict:
        """Konfiguration fuer die Oberflaeche — ohne das Passwort."""
        with self._lock:
            c = dict(self._cfg)
        c['password_set'] = bool(c.pop('password', ''))
        return c

    def update_settings(self, patch: dict) -> dict:
        erlaubt = set(_VORGABEN) | {'password'}
        with self._lock:
            host_vorher = self._cfg.get('host', '')
            for k, v in patch.items():
                if k not in erlaubt:
                    continue
                if k in ('enabled', 'set_time'):
                    self._cfg[k] = bool(v)
                elif k in ('host', 'password'):
                    self._cfg[k] = str(v or '').strip()
                elif k == 'frostwacht':
                    if isinstance(v, dict):
                        # Nur, was sich als Raum-ID lesen laesst. Der Rest waere
                        # Muell, der spaeter still nie greift.
                        sauber = {}
                        for rid, an in v.items():
                            try:
                                sauber[str(int(rid))] = bool(an)
                            except (TypeError, ValueError):
                                continue
                        self._cfg['frostwacht'] = sauber
            cfg = dict(self._cfg)
            # Nur bei einem Hostwechsel ist das Zwischengespeicherte wertlos.
            # Vorher flog es bei JEDEM Speichern weg — wer nur einen Schalter
            # umlegte, sah danach eine leere Kachel, bis der naechste Abruf
            # durch war.
            if self._cfg.get('host', '') != host_vorher:
                self._state = self._info = None
                self._last_ok = None
                self._zeit_gesetzt = False
        write_json(self._path, cfg)
        return self.settings()

    # ── Abfrage ─────────────────────────────────────────────────────────────

    def _url(self, pfad: str) -> str | None:
        with self._lock:
            host = self._cfg.get('host', '').strip()
        if not host:
            return None
        if not host.startswith(('http://', 'https://')):
            host = 'http://' + host
        return host.rstrip('/') + pfad

    def _anfrage(self, pfad: str, rumpf: dict | None = None, methode: str = 'GET') -> dict:
        """Eine HTTP-Anfrage an den Hub. Wirft StokerFehler bei Problemen."""
        url = self._url(pfad)
        if not url:
            raise StokerFehler('kein_host', 'Es ist keine Adresse der Heizung hinterlegt.')

        daten = None
        kopf = {'Accept': 'application/json'}
        if rumpf is not None:
            daten = json.dumps(rumpf).encode()
            kopf['Content-Type'] = 'application/json'
        with self._lock:
            pw = self._cfg.get('password', '')
        if pw and methode != 'GET':
            kopf['X-Stoker-Auth'] = pw

        req = urllib.request.Request(url, data=daten, headers=kopf, method=methode)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
                roh = r.read()
            return json.loads(roh) if roh else {}
        except urllib.error.HTTPError as e:
            # Die Heizung antwortet immer im selben Fehlerformat.
            try:
                fehler = json.loads(e.read()).get('error', {})
            except Exception:
                fehler = {}
            raise StokerFehler(fehler.get('code') or f'http_{e.code}',
                               fehler.get('message') or f'HTTP {e.code}',
                               status=e.code, field=fehler.get('field')) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            raise StokerFehler('nicht_erreichbar',
                               'Die Heizung antwortet nicht.') from None
        except json.JSONDecodeError:
            raise StokerFehler('ungueltige_antwort',
                               'Die Heizung hat keine gültige Antwort geliefert.') from None

    def _einmal_pollen(self) -> None:
        with self._lock:
            an, host = self._cfg.get('enabled'), self._cfg.get('host', '').strip()
        if not an or not host:
            return

        try:
            state = self._anfrage('/api/state')
        except StokerFehler as e:
            with self._lock:
                self._last_error = e.message
            return

        info = None
        with self._lock:
            braucht_info = self._info is None
        if braucht_info:
            try:
                info = self._anfrage('/api/info')
            except StokerFehler:
                info = None

        with self._lock:
            self._state = state
            if info is not None:
                self._info = info
            self._last_ok = time.monotonic()
            self._last_error = None
            uhr_unsicher = bool((state.get('time') or {}).get('uncertain'))
            zeit_setzen = self._cfg.get('set_time') and uhr_unsicher and not self._zeit_gesetzt

        # Das Geraet hat keine gepufferte Uhr und verliert die Zeit bei jedem
        # Neustart. Ohne gestellte Uhr ist sein Verlauf ohne Zeitachse.
        if zeit_setzen:
            try:
                self._anfrage('/api/time', {'epoch': int(time.time())}, 'POST')
                with self._lock:
                    self._zeit_gesetzt = True
                log.info('Heizung: Uhr gestellt')
            except StokerFehler as e:
                log.debug('Heizung: Uhr stellen fehlgeschlagen: %s', e.message)

    def _schleife(self) -> None:
        while self._running:
            try:
                self._einmal_pollen()
            except Exception as e:                      # nie den Thread verlieren
                log.warning('Heizung: Abfrage fehlgeschlagen: %s', e)
            with self._lock:
                fehler = self._last_error is not None
            time.sleep(_POLL_FEHLER_S if fehler else _POLL_S)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._schleife, daemon=True, name='stoker')
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    # ── Zustand fuer die Oberflaeche ────────────────────────────────────────

    def snapshot(self) -> dict:
        """Zwischengespeicherter Zustand plus Erreichbarkeit."""
        with self._lock:
            cfg_an  = bool(self._cfg.get('enabled'))
            host    = self._cfg.get('host', '').strip()
            state   = self._state
            info    = self._info
            last_ok = self._last_ok
            fehler  = self._last_error

        alter = (time.monotonic() - last_ok) if last_ok is not None else None
        # Kurze Aussetzer sind normal; erst nach einer Weile gilt es als weg.
        erreichbar = alter is not None and alter < 30
        schnapp = {
            'enabled': cfg_an,
            'configured': bool(host),
            'reachable': erreichbar,
            'age_s': round(alter, 1) if alter is not None else None,
            'error': fehler,
            'info': info,
            'state': state,
        }
        schnapp.update(_raum_kennzahlen(state))
        with self._lock:
            schnapp.update(self._alarm_felder(schnapp, cfg_an, host, erreichbar,
                                              last_ok, state))
        return schnapp

    def _frost_temp(self, state: dict | None) -> float | None:
        """Kaeltester Raum, der bewacht werden soll — oder None.

        Bewacht wird ein Raum, wenn sein Schalter in den Einstellungen an ist.
        Fehlt er in der Konfiguration, gilt er als an (siehe _VORGABEN): ein
        neu angelernter Fuehler soll nicht stillschweigend ohne Wache laufen.

        Nur Raeume, die auch melden. Ein stummer Fuehler liefert keine
        Temperatur, und ein alter Wert waere hier gefaehrlicher als gar keiner.
        """
        wacht = self._cfg.get('frostwacht') or {}
        temps = []
        for r in ((state or {}).get('rooms') or []):
            if not isinstance(r, dict) or r.get('conn') != 'online':
                continue
            if not wacht.get(str(r.get('id')), True):
                continue
            t = r.get('roomTemp')
            if isinstance(t, (int, float)) and not isinstance(t, bool):
                temps.append(t)
        return round(min(temps), 1) if temps else None

    def _alarm_felder(self, schnapp: dict, cfg_an: bool, host: str,
                      erreichbar: bool, last_ok: float | None,
                      state: dict | None) -> dict:
        """Fertig entprellte Werte, auf die die Alarmregeln zeigen.

        Warum ueberhaupt eigene Felder statt der rohen? Zwei Gruende:

        1. 'reachable' ist auch dann falsch, wenn die Anbindung gar nicht
           eingeschaltet ist, kein Host eingetragen wurde oder der Pi gerade
           erst gestartet ist. Eine Regel darauf stuende bei jedem Boot ohne
           Heizung dauerhaft an — der sicherste Weg, ein Alarmsystem
           unglaubwuerdig zu machen.
        2. Die Engine entprellt nicht. Sekundenwerte verlagern die Entprellung
           in die Schwelle, die der Eigner in den Einstellungen sehen und
           aendern kann.

        Durchgaengig gilt: was nicht verbaut oder nicht bekannt ist, wird None
        — dann schweigt die Regel, statt zu raten.
        """
        h        = ((state or {}).get('heater') or {})
        verbaut  = _heizgeraet_verbaut(state)
        # Erst urteilen, wenn der Hub ueberhaupt schon einmal geantwortet hat.
        # last_ok is None heisst: eingeschaltet, aber noch nie erreicht (frisch
        # gestartet oder nie installiert) — kein Fall fuer einen Alarm.
        beurteilbar = cfg_an and bool(host) and last_ok is not None
        # Sekunden seit der letzten erfolgreichen Antwort. Das IST schon die
        # Entprellung — ein eigener Halte-Zaehler waere doppelt gemoppelt und
        # wuerde die Schwelle verfaelschen (er liefe erst an, wenn 'reachable'
        # nach 30 s umspringt; aus 120 s Schwelle wuerden real 150 s).
        # So bedeutet die Schwelle genau das, was in den Einstellungen steht:
        # so lange kein Lebenszeichen mehr.
        weg_s = round(schnapp.get('age_s') or 0.0, 1) if beurteilbar else None

        # Fehlercode des Heizgeraets. 0 heisst stoerungsfrei.
        code = h.get('errorCode')
        fehler_s = self._hz_fehler(
            (isinstance(code, (int, float)) and not isinstance(code, bool) and code != 0)
            if (verbaut and erreichbar) else None)

        # Frostschutz. Bewusst UNABHAENGIG davon, ob das Heizgeraet laeuft:
        # ein geplatztes Wasserrohr fragt nicht, warum es kalt geworden ist.
        # Frueher hing das an heater.mode ('off' -> kein Urteil); das war
        # falsch herum, denn ausgerechnet der wahrscheinlichste Fehler — die
        # Heizung steht auf Aus, gewollt oder nicht — legte damit den Alarm
        # still. Eigner-Entscheidung vom 02.09.2026.
        #
        # Zwei Bedingungen bleiben:
        #  - Der Hub muss erreichbar sein. _einmal_pollen() leert self._state
        #    bei einem Fehlschlag NICHT, sonst liefe der Alarm auf Messwerten
        #    von vorgestern an (Hub aus, Pi laeuft: monatelang). Ein bereits
        #    stehender Alarm bleibt dabei stehen — die Engine ueberspringt
        #    None, ohne ihn zu loeschen.
        #  - Gezaehlt werden nur Raeume, fuer die die Frostwacht in den
        #    Einstellungen eingeschaltet ist. Das ist ein EIGENER Schalter je
        #    Raum und haengt ausdruecklich nicht daran, ob der Raum mitheizt
        #    oder was am Heizgeraet eingestellt ist.
        frost_temp = self._frost_temp(state) if erreichbar else None

        offline   = schnapp.get('raeume_offline')
        online    = schnapp.get('raeume_online')
        raeume_s  = self._hz_raeume(None if (offline is None or not erreichbar)
                                    else offline > 0)
        leer_s    = self._hz_leer(None if (online is None or not erreichbar)
                                  else online < 1)

        # Bewusst KEINE Regel auf state.heater.link.lastFrameAgeS: die Leitung
        # zum Autoterm schweigt im Ruhebetrieb regulaer (Frostwacht, Pausen
        # zwischen Brennphasen), und 'availability' wird von der Firmware
        # ohnehin genau aus dieser Stille gebildet — mit 600 s Toleranz
        # (heater_health.h HEATER_QUIET_GRACE_S). Ein Alarm auf das
        # Leitungsalter, gegatet auf 'verbaut', waere ein Zirkelschluss: er
        # koennte nur im schmalen Fenster vor dem Umkippen feuern und wuerde
        # danach als Datenluecke haengen bleiben.

        return {
            'verbindung_weg_s': weg_s,
            'fehler_s':         fehler_s,
            'frost_temp':       frost_temp,
            'raeume_weg_s':     raeume_s,
            'kein_raum_s':      leer_s,
            'geraet_verbaut':   verbaut,
        }

    # ── Bedienen ────────────────────────────────────────────────────────────
    # Die Antworten der Heizung sind bereits der neue Zustand des betroffenen
    # Teils — ein zweiter Abruf ist danach nicht noetig.

    def set_room(self, room_id: int, patch: dict) -> dict:
        erlaubt = {'target', 'fanMode', 'manualSpeed', 'enabled'}
        rumpf = {k: v for k, v in patch.items() if k in erlaubt}
        if not rumpf:
            raise StokerFehler('invalid_value', 'Kein bekanntes Feld im Aufruf.', status=400)
        if 'target' in rumpf:
            try:
                ziel = float(rumpf['target'])
            except (TypeError, ValueError):
                raise StokerFehler('invalid_value', 'Solltemperatur muss eine Zahl sein.',
                                   status=400, field='target') from None
            # Weite, aber endliche Grenzen: ein Tippfehler soll nicht als
            # Solltemperatur an die Regelung gehen.
            if not (0.0 <= ziel <= 40.0):
                raise StokerFehler('invalid_value',
                                   'Solltemperatur muss zwischen 0 und 40 °C liegen.',
                                   status=400, field='target')
            rumpf['target'] = round(ziel, 1)
        if 'manualSpeed' in rumpf:
            try:
                v = int(rumpf['manualSpeed'])
            except (TypeError, ValueError):
                raise StokerFehler('invalid_value', 'Drehzahl muss eine Zahl sein.',
                                   status=400, field='manualSpeed') from None
            rumpf['manualSpeed'] = max(0, min(100, v))
        if 'fanMode' in rumpf and rumpf['fanMode'] not in ('off', 'auto', 'manual'):
            raise StokerFehler('invalid_value', 'Unbekannter Lüftermodus.',
                               status=400, field='fanMode')
        return self._anfrage(f'/api/room/{int(room_id)}', rumpf, 'POST')

    def set_heater(self, patch: dict) -> dict:
        rumpf: dict = {}
        if patch.get('cancelPending'):
            rumpf['cancelPending'] = True
        else:
            modus = patch.get('mode')
            if modus not in ('off', 'auto', 'manual'):
                raise StokerFehler('invalid_value', 'Unbekannter Heizungsmodus.',
                                   status=400, field='mode')
            rumpf['mode'] = modus
            if modus == 'manual':
                befehl = patch.get('command')
                if befehl not in ('on', 'off'):
                    raise StokerFehler('invalid_value',
                                       'Im Handbetrieb wird on oder off gebraucht.',
                                       status=400, field='command')
                rumpf['command'] = befehl
        return self._anfrage('/api/heater', rumpf, 'POST')

    def set_preset(self, index) -> dict:
        """index ist 0..3 oder 'none' zum Abwaehlen.

        Die Doku warnt ausdruecklich: Abwaehlen ist 'none', NICHT eine Zahl
        ausserhalb des Bereichs — die wird mit 404 abgewiesen.
        """
        if index in ('none', None, 'None'):
            return self._anfrage('/api/preset/none/activate', {}, 'POST')
        try:
            i = int(index)
        except (TypeError, ValueError):
            raise StokerFehler('invalid_value', 'Unbekannter Preset-Platz.',
                               status=400, field='index') from None
        if not (0 <= i <= 3):
            raise StokerFehler('not_found', 'Diesen Preset-Platz gibt es nicht.',
                               status=404, field='index')
        return self._anfrage(f'/api/preset/{i}/activate', {}, 'POST')

    def probe(self, host: str | None = None) -> dict:
        """GET /api/info gegen eine Adresse — zum Pruefen im Einstellungsdialog."""
        if host:
            with self._lock:
                alt = self._cfg.get('host')
                self._cfg['host'] = host.strip()
            try:
                return self._anfrage('/api/info')
            finally:
                with self._lock:
                    self._cfg['host'] = alt
        return self._anfrage('/api/info')


class StokerFehler(Exception):
    """Fehler der Heizung oder der Verbindung, im Format der Geraetedoku."""

    def __init__(self, code: str, message: str, status: int = 502, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.field = field

    def as_dict(self) -> dict:
        d = {'code': self.code, 'message': self.message}
        if self.field:
            d['field'] = self.field
        return {'error': d}


# ── Testdaten ───────────────────────────────────────────────────────────────
# Solange kein Geraet im Netz haengt, laesst sich die Oberflaeche sonst nicht
# beurteilen. Wird nur geliefert, wenn in den Einstellungen ausdruecklich
# eingeschaltet — und nur, wenn kein echter Zustand vorliegt.


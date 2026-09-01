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
}


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
            for k, v in patch.items():
                if k not in erlaubt:
                    continue
                if k in ('enabled', 'set_time'):
                    self._cfg[k] = bool(v)
                elif k in ('host', 'password'):
                    self._cfg[k] = str(v or '').strip()
            cfg = dict(self._cfg)
            # Host gewechselt: alles Zwischengespeicherte ist wertlos.
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
        return {
            'enabled': cfg_an,
            'configured': bool(host),
            'reachable': erreichbar,
            'age_s': round(alter, 1) if alter is not None else None,
            'error': fehler,
            'info': info,
            'state': state,
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


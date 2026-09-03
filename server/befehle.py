"""Befehle vom Server zum Boot.

Die Frage, die hier beantwortet wird: Wie erreicht ein Schaltbefehl den Pi,
wenn der keinen offenen Port hat?

Ueber dieselbe Verbindung, rueckwaerts. Ein WebSocket ist bidirektional — der
Pi baut ihn auf (ausgehend, deshalb braucht er keinen offenen Port), und
danach kann der Server jederzeit hineinsenden. Der Pi fuehrt den Befehl LOKAL
aus, gegen seine eigene API, und schickt eine Quittung zurueck.

Das hat drei Folgen, die alle gewollt sind:

  * **Ohne Verbindung wird nicht geschaltet.** Es gibt keine Warteschlange fuer
    Schaltbefehle. Niemand will, dass sich das Boot beim naechsten Funkkontakt
    an ein "Licht an" von vorgestern erinnert.
  * **Der Pi entscheidet.** Er prueft den Befehl selbst gegen seine eigenen
    Regeln. Ein uebernommener Server kann ihm nichts befehlen, was er nicht
    ohnehin von der Bordoberflaeche annehmen wuerde.
  * **Nur eine Weissliste.** Es gibt keinen allgemeinen Durchleiter. Was hier
    nicht steht, geht nicht durch — auch nicht mit gueltiger Anmeldung.
"""
from __future__ import annotations

import asyncio
import logging
import secrets

from sync import protokoll as p
from sync import rechte as r

log = logging.getLogger('mave-server.befehle')

# Wie lange auf die Quittung des Bootes gewartet wird. Ueber Mobilfunk sind
# zwei Sekunden Umlauf normal; nach zehn ist etwas kaputt und der Bediener soll
# es erfahren, statt in einen haengenden Knopf zu schauen.
FRIST_S = 10.0

# ── Die Weissliste ──────────────────────────────────────────────────────────
# Nur diese Pfade reicht der Server weiter, mit dem Recht, das sie verlangen.
# Die Liste spiegelt die schreibenden Endpunkte des Pi — aber nicht alle:
# /api/system/update und /api/jserror gehoeren nicht in die Ferne.
DURCHLEITEN: tuple[tuple[str, str, str], ...] = (
    # Bedienen
    ('POST', '/api/lights/channels',           r.SCHALTEN),
    ('POST', '/api/lights/preset/{preset_id}', r.SCHALTEN),
    ('POST', '/api/inverter/mode',             r.SCHALTEN),
    ('POST', '/api/charger/mode',              r.SCHALTEN),
    ('POST', '/api/display/power',             r.SCHALTEN),
    ('POST', '/api/display/brightness',        r.SCHALTEN),
    ('POST', '/api/heizung/room/{room_id}',    r.SCHALTEN),
    ('POST', '/api/heizung/preset/{index}',    r.SCHALTEN),
    # Die Heizung selbst: eigene Regel, siehe HEIKEL
    ('POST', '/api/heizung/heater',            r.SCHALTEN),
    # Alarme quittieren
    ('POST', '/api/alarms/ack-all',            r.SCHALTEN),
    ('POST', '/api/alarms/{alarm_id}/ack',     r.SCHALTEN),
    # Gepflegte Listen
    ('PUT',  '/api/wartung',                   r.EINSTELLEN),
    ('PUT',  '/api/stauplan',                  r.EINSTELLEN),
    ('PUT',  '/api/devices/registry',          r.EINSTELLEN),
    ('POST', '/api/settings',                  r.EINSTELLEN),
    ('POST', '/api/alarms/rules',              r.EINSTELLEN),
    # Fernwartung
    ('POST', '/api/system/time-sync',          r.FERNWARTEN),
    # Aktualisieren aus der Ferne. Es startet die Anwendung an Bord neu — das
    # ist der Eingriff, fuer den es das Recht FERNWARTEN ueberhaupt gibt.
    # Bewusst NICHT in HEIKEL: anders als die Heizung verbraucht es nichts und
    # laesst sich, falls etwas schiefgeht, an Bord wieder geradeziehen
    # (systemd startet den Dienst ohnehin neu).
    ('POST', '/api/system/update',             r.FERNWARTEN),
)

# Befehle, die aus der Ferne standardmaessig GESPERRT sind, auch mit Recht.
#
# Die Heizung verbrennt Diesel in einem Boot, in dem niemand steht. Das ist
# eine andere Kategorie als Licht. Freigeben laesst sich das mit
# MAVE_FERN_HEIZUNG=ja — bewusst als Umgebungsvariable und nicht als Haekchen
# in der Oberflaeche: wer es einschaltet, soll es einmal bewusst tun.
HEIKEL: frozenset = frozenset({'/api/heizung/heater'})


class KeinBoot(RuntimeError):
    """Das Boot ist nicht verbunden. Es wird nicht geschaltet."""


class Zeitueberschreitung(RuntimeError):
    """Der Befehl ging hinaus, aber es kam keine Quittung."""


class Vermittlung:
    """Haelt die offene Verbindung und ordnet Quittungen ihren Befehlen zu."""

    def __init__(self):
        self._ws = None
        self._offen: dict[str, asyncio.Future] = {}

    # ── Verbindung ──────────────────────────────────────────────────────────

    def verbunden(self, ws) -> None:
        self._ws = ws

    def getrennt(self) -> None:
        self._ws = None
        # Wer noch wartet, wartet umsonst — das gehoert gesagt, nicht
        # ausgesessen, sonst haengt die Oberflaeche bis zur Frist.
        for zukunft in self._offen.values():
            if not zukunft.done():
                zukunft.set_exception(KeinBoot('Verbindung zum Boot abgerissen'))
        self._offen.clear()

    @property
    def steht(self) -> bool:
        return self._ws is not None

    # ── Senden und warten ───────────────────────────────────────────────────

    async def senden(self, methode: str, pfad: str, rumpf, frist: float = FRIST_S,
                     konto: str = '') -> dict:
        if self._ws is None:
            raise KeinBoot('Das Boot ist nicht verbunden.')
        kennung = secrets.token_hex(8)
        zukunft: asyncio.Future = asyncio.get_running_loop().create_future()
        self._offen[kennung] = zukunft
        try:
            await self._ws.send_json(p.umschlag(p.BEFEHL, {
                'kennung': kennung, 'methode': methode, 'pfad': pfad, 'rumpf': rumpf,
                # Wer gefragt hat. Der Pi schlaegt den Namen in seiner eigenen
                # Kontenkopie nach und wendet DESSEN Rechte an — ohne diese
                # Angabe kaeme der Aufruf dort unangemeldet an.
                'konto': konto,
            }))
            return await asyncio.wait_for(zukunft, timeout=frist)
        except asyncio.TimeoutError:
            raise Zeitueberschreitung(
                'Das Boot hat den Befehl nicht bestätigt.') from None
        finally:
            self._offen.pop(kennung, None)

    async def senden_ohne_antwort(self, nachricht: dict) -> None:
        """Etwas zum Boot schicken, auf das es nicht antworten muss.

        Fuer die Kontenkopie: sie ist eine Mitteilung, keine Frage. Auf eine
        Quittung zu warten wuerde die Kontenverwaltung an der Erreichbarkeit
        des Bootes aufhaengen — und die Aenderung ist auf dem Server ohnehin
        schon gueltig.
        """
        if self._ws is None:
            raise KeinBoot('Das Boot ist nicht verbunden.')
        await self._ws.send_json(nachricht)

    def quittung(self, daten: dict) -> None:
        """Eine Quittung vom Boot einsortieren.

        Eine Kennung, die niemand erwartet, wird verworfen und protokolliert —
        sie deutet auf eine doppelte Ausfuehrung oder auf ein Paket, das nicht
        von dieser Verbindung stammt.
        """
        kennung = (daten or {}).get('kennung')
        zukunft = self._offen.get(kennung) if isinstance(kennung, str) else None
        if zukunft is None:
            log.warning('Quittung ohne wartenden Befehl: %r', kennung)
            return
        if not zukunft.done():
            zukunft.set_result(daten)


def gesperrt(pfad: str, fern_heizung_erlaubt: bool) -> bool:
    """Ob ein Pfad aus der Ferne gesperrt ist."""
    if pfad in HEIKEL:
        return not fern_heizung_erlaubt
    return False

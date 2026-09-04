"""Web Push — die Meldung, wenn die Anwendung zu ist.

Warum der SERVER das macht und nicht der Bordrechner: ein Push-Abo zeigt auf
den Push-Dienst des Browserherstellers (bei Chrome auf Googles Server). Der
Absender schickt dorthin, der Dienst stellt zu. Beide Seiten brauchen also
Internet — und der Server hat es zuverlaessig, der Pi nicht. Ausserdem laeuft
der Server durch, waehrend das Boot auch mal ohne Netz liegt.

Was das bedeutet, und es ist wichtig genug fuer diesen Absatz: **im Bordnetz
ohne Uplink kommt keine Benachrichtigung an**, obwohl der Pi im selben Raum
steht. Es gibt keinen Weg, "lokal" zu pushen — den Dienst waehlt der Browser.
Deshalb gibt es daneben den Ton bei offener Anwendung; der braucht niemanden.

Die Verschluesselung schreiben wir nicht selbst. Web Push verlangt ein
signiertes Zertifikat (VAPID, ES256) und eine verschluesselte Nutzlast
(AES128GCM) — das ist nichts zum Selbermachen. `pywebpush` erledigt beides.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Wie lange der Push-Dienst die Meldung aufheben soll, wenn das Geraet gerade
# nicht erreichbar ist. Sechs Stunden: ein Alarm von gestern nuetzt niemandem,
# einer von heute Nacht schon.
_TTL_S = 6 * 3600


class KeinPush(RuntimeError):
    """Push ist nicht eingerichtet oder die Bibliothek fehlt."""


class PushDienst:
    """Schluessel, Abos und der Versand."""

    def __init__(self, schluessel_datei: Path, speicher, kontakt: str = 'mailto:mail@joshy.eu'):
        self._datei = Path(schluessel_datei)
        self._speicher = speicher
        self._kontakt = kontakt
        self._lock = threading.Lock()
        self._vapid = None
        self._oeffentlich = ''
        self._bereit = False
        self._grund = 'noch nicht geladen'
        self._laden()

    # ── Schluessel ──────────────────────────────────────────────────────────

    def _laden(self) -> None:
        try:
            from py_vapid import Vapid01 as Vapid
        except Exception as e:                       # pragma: no cover
            self._grund = f'py_vapid fehlt ({e})'
            log.warning('Push nicht verfuegbar: %s', self._grund)
            return
        try:
            if self._datei.exists():
                self._vapid = Vapid.from_file(str(self._datei))
            else:
                # Einmalig erzeugt und dann behalten. Ein neuer Schluessel
                # macht JEDES bestehende Abo ungueltig — die Geraete muessten
                # sich alle neu anmelden, ohne es zu merken.
                self._vapid = Vapid()
                self._vapid.generate_keys()
                self._datei.parent.mkdir(parents=True, exist_ok=True)
                self._vapid.save_key(str(self._datei))
                self._datei.chmod(0o600)
                log.info('Push: neues Schluesselpaar erzeugt (%s)', self._datei)
            # py_vapid gibt den Schluessel als Kurvenobjekt heraus, nicht als
            # Zeichenkette. Der Browser will ihn als unkomprimierten Punkt in
            # url-sicherem Base64 — genau die Form, die `applicationServerKey`
            # erwartet.
            from cryptography.hazmat.primitives import serialization
            from py_vapid import b64urlencode
            roh = self._vapid.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint)
            self._oeffentlich = b64urlencode(roh)
            self._bereit = True
            self._grund = ''
        except Exception as e:                       # pragma: no cover
            self._grund = f'Schluessel nicht ladbar ({e})'
            log.warning('Push nicht verfuegbar: %s', self._grund)

    @property
    def bereit(self) -> bool:
        return self._bereit

    @property
    def grund(self) -> str:
        return self._grund

    @property
    def oeffentlicher_schluessel(self) -> str:
        return self._oeffentlich

    # ── Abos ────────────────────────────────────────────────────────────────

    def anmelden(self, abo: dict, konto: str, geraet: str = '') -> None:
        """Ein Geraet trägt sich ein.

        Der Endpunkt ist die Kennung: er ist je Gerät und Browser eindeutig,
        und er ist genau das, wohin gesendet wird. Meldet sich dasselbe Gerät
        erneut (nach einem Neuinstallieren), ersetzt es seinen alten Eintrag,
        statt einen zweiten anzulegen — sonst käme die Meldung doppelt.
        """
        endpunkt = (abo or {}).get('endpoint')
        if not endpunkt or not isinstance(abo.get('keys'), dict):
            raise ValueError('Das Abo ist unvollständig.')
        self._speicher.push_abo_setzen(endpunkt, konto, abo, geraet)

    def abmelden(self, endpunkt: str) -> None:
        self._speicher.push_abo_loeschen(endpunkt)

    def abos(self, konto: str | None = None) -> list[dict]:
        return self._speicher.push_abos(konto)

    # ── Versand ─────────────────────────────────────────────────────────────

    def senden(self, titel: str, text: str, *, ziel: str = '/#alarme',
               kennung: str = 'mave', dringend: bool = False) -> dict:
        """An alle eingetragenen Geräte. Gibt zurück, was daraus wurde.

        Abgelaufene Abos werden dabei entfernt: der Push-Dienst antwortet mit
        404 oder 410, wenn das Gerät sein Abo nicht mehr kennt. Wer die nicht
        aufräumt, schickt bis in alle Ewigkeit an Adressen, die es nicht gibt.
        """
        if not self._bereit:
            raise KeinPush(self._grund or 'Push ist nicht eingerichtet.')
        from pywebpush import webpush, WebPushException

        nutzlast = json.dumps({'titel': titel, 'text': text, 'url': ziel,
                               'tag': kennung, 'dringend': bool(dringend)},
                              ensure_ascii=False)
        geschickt = weg = fehler = 0
        with self._lock:
            for eintrag in self._speicher.push_abos():
                try:
                    webpush(
                        subscription_info=eintrag['abo'],
                        data=nutzlast,
                        vapid_private_key=self._vapid,
                        vapid_claims={'sub': self._kontakt},
                        ttl=_TTL_S,
                    )
                    geschickt += 1
                    self._speicher.push_abo_gesehen(eintrag['endpunkt'])
                except WebPushException as e:
                    code = getattr(getattr(e, 'response', None), 'status_code', None)
                    if code in (404, 410):
                        self._speicher.push_abo_loeschen(eintrag['endpunkt'])
                        weg += 1
                        log.info('Push-Abo abgelaufen und entfernt (%s)', code)
                    else:
                        fehler += 1
                        log.warning('Push fehlgeschlagen (%s): %s', code, e)
                except Exception as e:               # pragma: no cover
                    fehler += 1
                    log.warning('Push fehlgeschlagen: %s', e)
        return {'geschickt': geschickt, 'entfernt': weg, 'fehler': fehler,
                'zeit': time.time()}

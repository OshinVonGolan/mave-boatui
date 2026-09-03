"""Konten, Passwoerter und Sitzungen — fuer beide Seiten.

Die Wahrheit ueber Konten liegt beim Server. Der Pi haelt eine Kopie, damit die
Anmeldung an Bord auch ohne Internet funktioniert (Eigner-Entscheidung:
Anmeldung ueberall). Beide pruefen mit demselben Code, deshalb liegt er hier.

## Warum scrypt mit n=2^13 und nicht mehr

Gemessen auf dem Pi Zero W (ARMv6, ein Kern), 03.09.2026:

    scrypt n=2^12   247 ms        pbkdf2-sha256  50 000    602 ms
    scrypt n=2^13   488 ms        pbkdf2-sha256 100 000   1516 ms
    scrypt n=2^14   886 ms        pbkdf2-sha256 200 000   2590 ms
    scrypt n=2^15  1808 ms        pbkdf2-sha256 600 000   7439 ms

Die gaengige Empfehlung (pbkdf2, 600 000 Runden) braucht auf diesem Geraet
**siebeneinhalb Sekunden**. Das ist nicht nur unbedienbar, sondern eine
Angriffsflaeche: bei einem Kern legen zehn Anmeldeversuche die Anlage lahm.

scrypt mit n=2^13 kostet eine halbe Sekunde auf dem Pi und ist dabei
speicherhart — gegen Angriffe mit Grafikkarten deutlich staerker als pbkdf2
gleicher Laufzeit. Auf dem Server (x86) sind es ein paar Millisekunden.

**Der schwaechste Pruefer bestimmt die Kosten.** Der Server koennte haerter
hashen, aber der Pi muss denselben Hash pruefen — und Pruefen kostet genauso
viel wie Erzeugen. Deshalb gilt hier die Zahl des Pi, nicht die des Servers.

Aus demselben Grund steht in jedem Hash, mit welchen Parametern er entstand:
so laesst sich die Zahl spaeter erhoehen, ohne alte Passwoerter zu entwerten.
Beim Pruefen gilt trotzdem eine Obergrenze — ein Hash mit unsinnigen Parametern
(aus Versehen oder mit Absicht) wuerde den Pi sonst minutenlang rechnen lassen.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# Vorgabe fuer neue Passwoerter. Aendern erlaubt — alte Hashes bleiben gueltig,
# weil ihre Parameter in ihnen stehen.
SCRYPT_N_EXP = 13
SCRYPT_R = 8
SCRYPT_P = 1
_DKLEN = 32

# Obergrenze beim PRUEFEN. Begrenzt wird der SPEICHER, nicht der Exponent:
# scrypt braucht rund 128 * n * r * p Bytes, und die Exponenten einzeln zu
# deckeln reicht nicht — n=2^15 mit r=16 und p=4 waeren 268 MB und wuerden
# einen Pi mit 427 MB umbringen. 32 MB sind reichlich fuer alles, was hier
# jemals sinnvoll gerechnet wird.
_MAX_SPEICHER = 32 * 1024 * 1024
_MAX_N_EXP = 16


def _speicher(n_exp: int, r: int, p: int) -> int:
    """Was scrypt mit diesen Parametern belegt, plus Reserve."""
    return 128 * (2 ** n_exp) * r * p + 2 ** 20


class KontoFehler(ValueError):
    """Unbrauchbare Angabe. Die Meldung ist fuer den Bediener geschrieben."""


# ── Passwoerter ─────────────────────────────────────────────────────────────

def hash_erzeugen(passwort: str, *, n_exp: int = SCRYPT_N_EXP) -> str:
    """Einen Passworthash bauen. Format:

        scrypt$<n_exp>$<r>$<p>$<salz base64>$<hash base64>

    Die Parameter stehen mit drin, damit sie sich spaeter erhoehen lassen,
    ohne dass alte Passwoerter ungueltig werden.
    """
    if not isinstance(passwort, str) or len(passwort) < 8:
        raise KontoFehler('Das Passwort muss mindestens 8 Zeichen haben.')
    if len(passwort) > 1024:
        # Ohne Grenze koennte ein langes Passwort selbst zur Last werden.
        raise KontoFehler('Das Passwort ist zu lang.')
    salz = secrets.token_bytes(16)
    roh = hashlib.scrypt(passwort.encode('utf-8'), salt=salz, n=2 ** n_exp,
                         r=SCRYPT_R, p=SCRYPT_P, dklen=_DKLEN,
                         maxmem=_speicher(n_exp, SCRYPT_R, SCRYPT_P))
    return 'scrypt${}${}${}${}${}'.format(
        n_exp, SCRYPT_R, SCRYPT_P, _b64(salz), _b64(roh))


def hash_pruefen(passwort: str, gespeichert: str) -> bool:
    """Passwort gegen einen gespeicherten Hash pruefen.

    Gibt False zurueck, wo andere eine Ausnahme werfen wuerden: ein kaputter
    Hash in der Kontendatei darf die Anmeldung ablehnen, aber nicht den Dienst
    stoeren.
    """
    if not isinstance(passwort, str) or not isinstance(gespeichert, str):
        return False
    teile = gespeichert.split('$')
    if len(teile) != 6 or teile[0] != 'scrypt':
        return False
    try:
        n_exp, r, p = int(teile[1]), int(teile[2]), int(teile[3])
        salz, erwartet = _unb64(teile[4]), _unb64(teile[5])
    except (ValueError, TypeError):
        return False
    # Obergrenze: sonst laesst sich der Pi mit einem praeparierten Hash
    # minutenlang beschaeftigen.
    if not (1 <= n_exp <= _MAX_N_EXP and r >= 1 and p >= 1):
        return False
    speicher = _speicher(n_exp, r, p)
    if speicher > _MAX_SPEICHER:
        return False
    try:
        roh = hashlib.scrypt(passwort.encode('utf-8'), salt=salz, n=2 ** n_exp,
                             r=r, p=p, dklen=len(erwartet), maxmem=speicher)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(roh, erwartet)


def sollte_erneuert_werden(gespeichert: str) -> bool:
    """Ob ein Hash mit schwaecheren Parametern als heute ueblich entstand.

    Der Aufrufer kann ihn dann bei der naechsten erfolgreichen Anmeldung
    stillschweigend neu bilden — der einzige Moment, in dem das Passwort im
    Klartext vorliegt.
    """
    teile = (gespeichert or '').split('$')
    if len(teile) != 6 or teile[0] != 'scrypt':
        return True
    try:
        return int(teile[1]) < SCRYPT_N_EXP
    except ValueError:
        return True


# ── Sitzungen ───────────────────────────────────────────────────────────────
# Damit das teure Hashen nur bei der ANMELDUNG anfaellt und nicht bei jeder
# Anfrage. Auf einem Einkern-Geraet ist das kein Feinschliff, sondern der
# Unterschied zwischen bedienbar und unbedienbar.

# 32 Bytes Zufall. Der Wert wird nie gespeichert, nur sein Hash — wer die
# Kontendatei liest, kann damit keine Sitzung uebernehmen.
def sitzung_erzeugen() -> tuple[str, str]:
    """Gibt (klartext, gespeichert) zurueck. Der Klartext geht einmal an den
    Browser, der Rest bleibt hier."""
    roh = secrets.token_urlsafe(32)
    return roh, sitzung_kennung(roh)


def sitzung_kennung(token: str) -> str:
    """Der Wert, unter dem eine Sitzung abgelegt wird.

    Einfaches SHA-256 genuegt, kein scrypt: das Token ist bereits 256 Bit
    Zufall, es gibt nichts zu raten. Teures Hashen waere hier nur langsam.
    """
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def sitzung_gleich(token: str, kennung: str) -> bool:
    return hmac.compare_digest(sitzung_kennung(token), kennung or '')


# ── Konten ──────────────────────────────────────────────────────────────────

def konto_anlegen(name: str, passwort: str, rolle: str, **weiteres) -> dict:
    """Ein Konto als reines Datenobjekt. Wo es liegt, entscheidet der Aufrufer:
    beim Server in der Datenbank, auf dem Pi in einer Datei."""
    name = (name or '').strip()
    if not name or len(name) > 64:
        raise KontoFehler('Der Anmeldename fehlt oder ist zu lang.')
    return {
        'name': name,
        'hash': hash_erzeugen(passwort),
        'rolle': rolle,
        'gesperrt': False,
        'gueltig_bis': weiteres.get('gueltig_bis'),   # None = unbefristet
        'angelegt': weiteres.get('angelegt'),
    }


def abgelaufen(konto: dict, jetzt: float | None) -> bool:
    """Ob ein befristetes Konto abgelaufen ist.

    `jetzt` kommt von aussen, weil dieses Paket keine Uhr hat — und auf dem Pi
    ist "welche Zeit ist es" nach einem Stromausfall eine echte Frage. Ohne
    verlaessliche Zeit gilt ein befristetes Konto als NICHT abgelaufen: eine
    falsche Uhr darf niemanden aussperren, der berechtigt ist.
    """
    bis = (konto or {}).get('gueltig_bis')
    if bis is None or jetzt is None:
        return False
    try:
        return float(jetzt) > float(bis)
    except (TypeError, ValueError):
        return False


def _b64(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).decode('ascii').rstrip('=')


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))

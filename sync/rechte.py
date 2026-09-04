"""Wer was darf — an Bord und auf dem Server.

Das Modul liegt im gemeinsamen Paket, weil BEIDE Seiten pruefen muessen: die
PWA spricht im Bord-WLAN direkt mit dem Pi, und der Pi kann sich nicht darauf
verlassen, dass der Server vorher gefragt wurde.

Zwei Arten von Rechten, absichtlich getrennt:

  OBERFLAECHEN   Welches Werkzeug darf jemand ueberhaupt oeffnen — die PWA an
                 Bord, oder auch das Diagnose- und Fernwartungswerkzeug auf dem
                 Server? Eigner-Wunsch vom 03.09.2026: die Crew soll die PWA
                 nutzen, aber nicht die Langzeitdiagnose.
  HANDLUNGEN     Was jemand mit den Daten tun darf: lesen, schalten,
                 einstellen, Konten verwalten, fernwarten.

Warum getrennt: Ein Crewmitglied darf an Bord Licht schalten (Handlung), soll
aber das Diagnosewerkzeug nicht sehen (Oberflaeche). Das eine folgt nicht aus
dem anderen.

**Verstecken ist keine Sicherheit.** Die Oberflaeche blendet aus, was jemand
nicht darf — aber die Entscheidung faellt am Endpunkt. Wer die Adresse des
Diagnosewerkzeugs kennt, bekommt ohne das Recht eine Abweisung, keine Seite.

Zur Form: Rollen sind die VORGABE, kein Korsett. Jedes Konto kann einzelne
Rechte uebersteuern. Das kostet hier fast nichts und vermeidet die Sackgasse,
in die das Werft-Werkzeug gelaufen ist — dort mussten Rollen als
Berechtigungsgrundlage nachtraeglich durch Rechte pro Person ersetzt werden.
"""
from __future__ import annotations

# ── Oberflaechen ────────────────────────────────────────────────────────────
PWA      = 'pwa'        # die Bordoberflaeche, an Bord und aus der Ferne
DIAGNOSE = 'diagnose'   # das Werkzeug auf dem Server: Langzeit, Protokolle, Fernwartung
OBERFLAECHEN = (PWA, DIAGNOSE)

# ── Handlungen ──────────────────────────────────────────────────────────────
LESEN      = 'lesen'       # Werte und Verlauf sehen
SCHALTEN   = 'schalten'    # Licht, Heizung, Wechselrichter, Lader
EINSTELLEN = 'einstellen'  # Presets, Alarmregeln, Geraeteliste, Tanks, Heizung
VERWALTEN  = 'verwalten'   # Konten anlegen, sperren, Rollen aendern
FERNWARTEN = 'fernwarten'  # Update ausloesen, Dienst neu starten, Protokolle holen
HANDLUNGEN = (LESEN, SCHALTEN, EINSTELLEN, VERWALTEN, FERNWARTEN)

# ── Rollen ──────────────────────────────────────────────────────────────────
# Ein Boot, vier Rollen und der Kiosk. Mehr Verwaltung als Nutzen waere hier
# falsch — es geht nicht um eine Werft mit Personal.
ROLLEN: dict[str, dict] = {
    'eigner': {
        'name': 'Eigner',
        'oberflaechen': (PWA, DIAGNOSE),
        'handlungen': (LESEN, SCHALTEN, EINSTELLEN, VERWALTEN, FERNWARTEN),
    },
    'crew': {
        'name': 'Crew',
        # Darf an Bord alles Bedienen, aber nicht das Diagnosewerkzeug oeffnen.
        'oberflaechen': (PWA,),
        'handlungen': (LESEN, SCHALTEN),
    },
    'gast': {
        'name': 'Gast',
        'oberflaechen': (PWA,),
        'handlungen': (LESEN,),
    },
    'techniker': {
        'name': 'Techniker',
        # Der einzige Fremde im System. Deshalb grundsaetzlich befristet: ein
        # Zugang, den niemand zurueckzieht, bleibt sonst fuer immer offen.
        'oberflaechen': (PWA, DIAGNOSE),
        'handlungen': (LESEN, SCHALTEN, EINSTELLEN, FERNWARTEN),
        'befristet': True,
    },
    'kiosk': {
        'name': 'Kiosk am Kartentisch',
        # Eine Geraetesitzung, kein Mensch. Sie haengt oeffentlich zugaenglich
        # an Bord, also darf sie weniger als der Eigner, der sie einrichtet:
        # bedienen ja, Konten und Fernwartung nein.
        'oberflaechen': (PWA,),
        'handlungen': (LESEN, SCHALTEN, EINSTELLEN),
    },
}

VORGABE_ROLLE = 'gast'


def rolle(name) -> dict:
    """Die Rollenbeschreibung. Eine unbekannte Rolle wird zum Gast — im
    Zweifel das geringste Recht, nie das grosszuegigste."""
    return ROLLEN.get(name if isinstance(name, str) else '', ROLLEN[VORGABE_ROLLE])


def darf(konto, handlung: str) -> bool:
    """Ob ein Konto eine Handlung ausfuehren darf.

    `konto` ist ein Objekt mit `rolle` und optional `handlungen`/`oberflaechen`
    als Uebersteuerung, dazu `gesperrt` und `abgelaufen`.

    KEIN Konto heisst KEIN Recht. Das steht hier ausdruecklich, obwohl der
    Aufrufer ohnehin vorher abweisen sollte: `rolle(None)` faellt auf die
    Vorgaberolle zurueck, und die darf lesen — ohne diese Zeile bekaeme ein
    Unangemeldeter also Gastrechte, sobald irgendwo eine Pruefung vergessen
    wird. Genau das ist der Fehler, den man nie bemerkt.
    """
    if not konto:
        return False
    if _gesperrt(konto):
        return False
    eigen = (konto or {}).get('handlungen')
    if isinstance(eigen, (list, tuple, set)):
        return handlung in eigen
    return handlung in rolle((konto or {}).get('rolle'))['handlungen']


def darf_oberflaeche(konto, welche: str) -> bool:
    """Ob ein Konto eine Oberflaeche oeffnen darf.

    Das ist die Frage, die der Eigner gestellt hat: die Crew nutzt die PWA, das
    Diagnosewerkzeug bleibt ihr verschlossen.
    """
    if not konto:
        return False
    if _gesperrt(konto):
        return False
    eigen = (konto or {}).get('oberflaechen')
    if isinstance(eigen, (list, tuple, set)):
        return welche in eigen
    return welche in rolle((konto or {}).get('rolle'))['oberflaechen']


def uebersicht(konto) -> dict:
    """Was ein Konto darf, in einer Form fuer die Oberflaeche.

    Die PWA blendet damit aus, was ohnehin abgewiesen wuerde — als Bequemlichkeit,
    nicht als Schutz.
    """
    from sync.konten import anzeigename
    return {
        'name': (konto or {}).get('name'),
        'anzeigename': anzeigename(konto),
        'rolle': (konto or {}).get('rolle') or VORGABE_ROLLE,
        'rolle_name': rolle((konto or {}).get('rolle'))['name'],
        'gesperrt': _gesperrt(konto),
        'oberflaechen': [o for o in OBERFLAECHEN if darf_oberflaeche(konto, o)],
        'handlungen': [h for h in HANDLUNGEN if darf(konto, h)],
    }


def _gesperrt(konto) -> bool:
    """Gesperrt oder abgelaufen — beides nimmt alle Rechte.

    Die Frist wird NICHT hier gegen die Uhr geprueft: dieses Paket hat
    absichtlich keine Uhr (siehe sync/__init__.py). Der Aufrufer setzt
    `abgelaufen`, weil nur er weiss, ob seine Uhr ueberhaupt steht — auf dem Pi
    ist das nach einem Stromausfall eine echte Frage.
    """
    k = konto or {}
    return bool(k.get('gesperrt')) or bool(k.get('abgelaufen'))

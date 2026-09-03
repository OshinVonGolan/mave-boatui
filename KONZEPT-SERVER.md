# Konzept — Bordanlage mit Server

Stand 03.09.2026. Entwurf zur Abstimmung, noch keine Umsetzung.

Ziel des Eigners, in seinen Worten: Der Pi Zero bleibt an Bord am NMEA-2000-Bus
und am Bordrouter. **Eine** PWA — im Bord-WLAN spricht sie direkt mit dem Pi,
sonst zieht dieselbe Oberfläche die Daten vom Server. Dazu eine **zweite,
andere** Oberfläche auf dem Server für Diagnose und Fernwartung. Mehrere
Nutzerkonten mit unterschiedlichen Rechten. Nur dieses eine Boot. Losgelöst von
Bosun One und Gantrya.

---

Der tatsächliche Netzwerkaufbau, die Zugangswege und ihre Fallen stehen in
`NETZWERK-UND-ZUGANG.md` — dort am laufenden System erhoben.

## Das Konstruktionsprinzip

**Der Server ist Zusatz, nie Voraussetzung.** Fällt er aus, bleibt das Boot
vollständig bedienbar: die PWA im Bord-WLAN spricht direkt mit dem Pi, und der
Pi braucht den Server für nichts. Alles Weitere unten folgt aus diesem Satz.

Der zweite tragende Satz: **Der Pi ist die Wahrheit.** Für jede Liste, die der
Bediener ändern kann (Wartungsplan, Stauplan, Geräteliste, Presets, Alarmregeln,
Heizungskonfiguration) gilt der Stand auf dem Pi. Der Server hält eine Kopie zum
Anzeigen. Damit gibt es **keine bidirektionale Konfliktauflösung** — der
teuerste Einzelposten in solchen Systemen fällt weg.

---

## 1. Wie die PWA weiß, wo sie ist

Das ist die technisch heikelste Stelle, und sie entscheidet sich an einer
einzigen Frage: Eine HTTPS-Seite darf kein `http://192.168.1.103` abrufen
(Mixed Content). Ohne Lösung dafür gibt es keine „eine PWA".

**Der Pi bekommt ein echtes TLS-Zertifikat.** Ein öffentlicher DNS-Eintrag
(etwa `pi.mave.<domain>`) zeigt auf seine **private** LAN-Adresse; das
Zertifikat kommt per DNS-01-Prüfung, es braucht also keinen eingehenden Port.
Damit sind beide Gegenstellen HTTPS, und die PWA darf frei zwischen ihnen
wechseln.

Zwei Voraussetzungen: eine DNS-Zone, die per API bedienbar ist (IONOS kann das),
und eine feste Adresse für den Pi (DHCP-Reservierung im RUTX50).

Verworfen:
- **Tailscale.** Auf dem Pi läuft es heute, und es könnte dem Pi sogar ein
  gültiges Zertifikat für seinen `ts.net`-Namen ausstellen. Es soll aber weg
  (Eigner-Entscheidung): Die Bordanlage soll nicht von einem fremden
  Koordinationsdienst abhängen, und Gäste an Bord sind nicht im Tailnet.
  Abgeschaltet wird es **erst, wenn dieser Ersatz trägt** — vorher fiele der
  Fernzugriff ersatzlos weg.
- **PWA vom Pi installieren.** `http://` im LAN ist kein sicherer Kontext, also
  kein Service Worker und keine Installation. Fällt aus.
- **Zwei Installationen** (eine vom Pi, eine vom Server). Zwei Symbole, zwei
  Caches, zwei Anmeldungen — genau das, was der Eigner nicht will.

Die Erkennung selbst:

- Installiert wird die PWA **von der Server-Domain**. Ein Ursprung, ein Service
  Worker, ein Speicher.
- Beim Start und bei jedem Wechsel (`online`, `visibilitychange`, alle 60 s im
  Serverbetrieb) fragt sie `https://pi.mave.<domain>/api/system/version` mit
  **1,5 s Frist**. Antwortet der Pi mit gültigem JSON, läuft alles direkt über
  ihn.
- Geprüft wird die **Antwort**, nicht die Erreichbarkeit: ein Captive Portal
  liefert HTML und wird so erkannt.
- Die Kopfzeile zeigt die Quelle („An Bord" / „Über Server"), und in den
  Einstellungen lässt sie sich erzwingen. Wer im Hafen mit halbem WLAN steht,
  will das selbst entscheiden können.

---

## 2. Wie die Daten zum Server kommen

Der Pi hängt hinter Mobilfunk-NAT, also baut **er** die Verbindung auf: eine
ausgehende `wss://`-Verbindung zum Server, angemeldet mit einem Gerätetoken.
Darüber läuft alles — Zustand hinaus, Befehle herein. Kein MQTT-Broker: FastAPI
und `websockets` sind im Haus, und es geht um ein Boot.

| Was | Wie |
|---|---|
| Live-Zustand | gedrosselt (Vorschlag: alle 30 s, sofort bei relevanter Änderung), gzip |
| Verlauf | die **Minutenmittel**, die es schon gibt (`history_min.ndjson`), mit Folgenummer |
| Nachliefern | der Server nennt beim Verbinden seinen Stand, der Pi schickt ab dort weiter |
| Ereignisse | Alarme sofort, sie sind der Grund für Push |

**Datenvolumen** ist zu messen, nicht zu schätzen: der Zustand ist grob 2 KB
JSON, komprimiert deutlich weniger. Erst nach einer Woche Messung wird der Takt
festgelegt — ein zu enger Takt kostet Mobilfunkvolumen, ein zu weiter macht die
Fernansicht träge.

**Die Uhr.** Der Pi hat keine gepufferte Uhr und läuft nach Stromausfall mit
falscher Zeit los; das hat im Frontend schon einmal Zeitfenster zerschossen
(CLAUDE.md, Regel 3). Jedes Paket trägt deshalb drei Angaben: den Wanduhr-Wert,
einen monotonen Zähler seit Systemstart und ein Flag, ob die Uhr per NTP gestellt
ist. Der Server verwirft die Wandzeit von Einträgen, die vor dem ersten
NTP-Abgleich entstanden sind, und rechnet sie aus dem Zeitpunkt des Abgleichs
minus verstrichener Monotonzeit zurück. Ohne das verschmutzt der erste
Stromausfall den Langzeitverlauf.

---

## 3. Konten und Rechte

Die Wahrheit über Konten liegt beim **Server**. Aber an Bord muss die Anmeldung
**ohne Internet** funktionieren — auch für einen Gast, der gerade angelegt wurde.

Deshalb: Der Server schickt dem Pi bei jeder Verbindung die Kontenliste
(Anmeldename, Passwort-Hash, Rolle, Sperrstatus). Der Pi hält sie als Kopie und
prüft damit lokal. Eine Sperrung greift an Bord, sobald der Pi wieder Kontakt
hatte — bei einem privaten Boot ist diese Verzögerung tragbar, und im Notfall
lässt sich eine Sperre auch am Pi selbst setzen.

Vier Rollen, keine Rechte-Matrix. Bei **einem** Boot ist mehr Verwaltung als
Nutzen:

| Rolle | Darf |
|---|---|
| Eigner | alles, dazu Konten und Fernwartung |
| Crew | lesen und schalten (Licht, Heizung, Inverter, Lader) |
| Gast | nur lesen |
| Techniker | lesen, Diagnose, Fernwartung — zeitlich befristet |

### Zugang zur Oberfläche ist ein eigenes Recht

Eigner-Wunsch vom 03.09.2026: Die Crew soll die PWA nutzen, aber **nicht** das
Diagnose- und Fernwartungswerkzeug auf dem Server — Langzeitauswertung braucht
sie nicht.

Deshalb trägt jede Rolle zwei getrennte Angaben:

| | |
|---|---|
| **Oberflächen** | welches Werkzeug jemand öffnen darf — `pwa`, `diagnose` |
| **Handlungen** | was jemand mit den Daten darf — lesen, schalten, einstellen, verwalten, fernwarten |

Das eine folgt nicht aus dem anderen: Ein Crewmitglied darf an Bord Licht
schalten, soll das Diagnosewerkzeug aber nicht sehen. Der Kiosk am Kartentisch
darf sogar einstellen, aber keine Konten verwalten und keine Diagnose öffnen.

| Rolle | Oberflächen | Handlungen |
|---|---|---|
| Eigner | pwa, diagnose | lesen, schalten, einstellen, verwalten, fernwarten |
| Crew | pwa | lesen, schalten |
| Gast | pwa | lesen |
| Techniker (befristet) | pwa, diagnose | lesen, schalten, einstellen, fernwarten |
| Kiosk (Gerätesitzung) | pwa | lesen, schalten, einstellen |

**Verstecken ist keine Sicherheit.** Die Oberfläche blendet aus, was jemand
nicht darf — die Entscheidung fällt aber am Endpunkt. Wer die Adresse des
Diagnosewerkzeugs kennt, bekommt ohne das Recht eine Abweisung, keine Seite.

Rollen sind die **Vorgabe, kein Korsett**: jedes Konto kann einzelne Rechte
übersteuern. Das kostet hier fast nichts und vermeidet die Sackgasse, in die
das Werft-Werkzeug gelaufen ist — dort mussten Rollen als
Berechtigungsgrundlage nachträglich durch Rechte pro Person ersetzt werden.

Zwei Dinge, die entschieden werden müssen (siehe unten): ob im Bord-WLAN das
**Lesen** ohne Anmeldung offen bleibt (heute ist alles offen), und dass der
Touchscheirm am Kartentisch eine dauerhafte Gerätesitzung braucht — dort will
niemand bei jedem Blick ein Passwort tippen.

---

## 4. Eine Oberfläche, zwei Gegenstellen

Der Server bietet **dieselben Pfade** an wie der Pi: `/api/*` und `/ws`. Damit
läuft die PWA unverändert gegen beide — das ist die Entscheidung, die den
größten Aufwandsposten streicht, denn die Oberfläche muss nicht neu gebaut
werden.

- **Lesende Endpunkte** beantwortet der Server aus seiner Kopie, **immer mit
  Altersangabe**. Ein Wert von vor drei Tagen darf nicht aussehen wie live.
- **Schreibende Endpunkte** (21 Stück) kann der Server nicht selbst beantworten.
  Er nimmt den Befehl an, legt ihn in eine Warteschlange, antwortet „angenommen,
  noch nicht ausgeführt", und der Pi quittiert später. Die PWA hat dafür schon
  ein Muster: den Vorgriff aus der Heizungsseite (`_hzErwarte`).
- **Offline schalten** gibt es nicht. Eine Warteschlange über Stunden ist für
  Licht und Heizung falsch — niemand will, dass sich das Boot beim nächsten
  Funkkontakt an einen Befehl von vorgestern erinnert. Für gepflegte Listen
  (Wartung, Stauplan) ist eine Warteschlange dagegen sinnvoll.

**Im Repo** bleibt es einer, mit drei Teilen: `pi/` (was heute im
Wurzelverzeichnis liegt), `server/` (neu), `gemeinsam/` (Zustandsformat,
Folgenummern, Zeitlogik). `static/` liefern beide aus — auch der Server setzt
das JS-Bundle so zusammen wie der Pi heute, es gibt keinen Build-Schritt und
kein node. Der Pi zieht weiter per `git pull` und ignoriert `server/`; der
Server baut sein Abbild aus demselben Stand.

Die zweite Oberfläche (Diagnose, Fernwartung) liegt getrennt unter
`server/static/` und hat mit der PWA nichts gemeinsam außer dem Datenmodell.
Ihr Aufbau wird gesondert besprochen.

---

## 5. Betrieb auf dem vorhandenen Server

Gemessen am 02.09.2026: vier Kerne, 7,7 GB RAM (6,5 GB frei), 63 GB freie
Platte, Caddy auf 80/443, lokal belegt nur 8100 und 8110. Gantrya wird
weiterentwickelt und bleibt dort — **Isolation ist deshalb Pflicht, nicht Kür**:

- eigener Container, eigener lokaler Port, eigenes Compose-Fragment
- eigene Datenhaltung (SQLite mit WAL reicht für ein Boot; **nicht** Gantryas
  Postgres mitbenutzen)
- eigene Domain, eigener Caddy-Block
- eigenes Backup mit eigenem Cron

Ressourcen sind kein Entwurfskriterium — der Eigner mietet notfalls größer.
Sparsam muss nur die Seite zum Pi und zum Mobilfunk sein: dort kostet es Kern,
RAM und Datenvolumen.

**Überwachung** ist der eigentliche Gewinn: Der Server merkt, wenn der Pi
stundenlang nichts geschickt hat, und meldet das. Heute fällt ein stummer Pi
niemandem auf, bis man an Bord kommt.

**Angriffsfläche.** Das Gefährlichste ist Fernschalten der Heizung — Diesel an
Bord, niemand in der Nähe. Vorschlag: aus dem Internet **standardmäßig
gesperrt**, per Schalter freigebbar, und nur für den Eigner. Alles andere
(Licht, Anzeige) ist harmlos.

---

## War das Boot offline, oder ist der Pi abgestürzt?

Eigner-Wunsch vom 03.09.2026: In der Online-Ansicht soll sichtbar sein, ob das
Boot in einem Zeitraum offline war oder der Pi abgestürzt ist — auslesbar,
sobald es wieder Verbindung gibt.

Das sind **zwei verschiedene Dinge**, und sie brauchen zwei Quellen:

- **Der Server** weiß, wann er Kontakt hatte. Er führt Sitzungen (von der ersten
  Nachricht bis zum letzten Lebenszeichen); die Zwischenräume sind die Lücken.
- **Warum** der Kontakt fehlte, weiß nur der Pi. Ein Funkloch sieht auf dem
  Server genauso aus wie ein Stromausfall.

Der Pi führt deshalb eine **Startmarke** (`sync/startmarke.py`): Beim Hochlauf
schreibt er sie, beim geordneten Beenden entfernt er sie wieder. Liegt sie beim
nächsten Start noch da, war das Ende unsauber. Dazu die `boot_id` des Kernels —
sie sagt, ob der ganze Rechner neu gestartet ist oder nur der Dienst — und die
Laufzeit aus `/proc/uptime`, mit der sich der Systemstart zurückrechnen lässt.

Daraus deutet der Server jede Lücke:

| Befund | Bedeutung | Verlauf |
|---|---|---|
| **funkloch** | Der Pi lief durch, nur die Verbindung fehlte | vollständig, wird nachgeliefert |
| **stromlos** | Rechner war aus, unsauberes Ende — Stromausfall oder Absturz | fehlt in diesem Zeitraum wirklich |
| **neustart** | Rechner wurde geordnet neu gestartet | fehlt kurz |
| **dienst** | Nur der Dienst neu gestartet (Update), Rechner lief durch | fast lückenlos |

Ist der Systemstart bekannt, wird die Lücke geteilt: bis dahin war der Rechner
**aus**, danach lief er nur **ohne Verbindung**. Genau diese Unterscheidung
beantwortet die Frage „hat er weitergemessen?".

---

## Etappen

Jede Etappe kann für sich etwas, das vorher nicht ging.

| # | Etappe | Danach kannst du | Aufwand |
|---|---|---|---|
| 1 | **Fernblick mit Anmeldung** — Server-Container, Domain, TLS für den Pi, ausgehende Verbindung, Zustand und Verlauf hinauf, Quellenerkennung in der PWA. Dazu Anmeldung an beiden Enden, Kontenkopie und Gerätesitzung für den Kiosk (siehe Entscheidung) | von überall sehen, wie es dem Boot geht, den Verlauf lesen — und niemand sonst kann es | 6–9 Abende, dazu ein Tag Zertifikatsfummelei |
| 2 | **Rollen und Verwaltung** — vier Rollen wirksam, Konten anlegen und sperren, befristete Technikerzugänge, Alarm-Push | Crew und Gäste getrennt zugreifen lassen, Zugänge selbst verwalten | 3–5 Abende |
| 3 | **Befehle** — Warteschlange, Quittung, Vorgriff; Heizung-Fernschalten als freizugebende Ausnahme | aus der Ferne schalten, mit ehrlicher Rückmeldung | 3–4 Abende |
| 4 | **Diagnose und Fernwartung** — eigene Oberfläche: Protokolle, JS-Fehler, Bus-Statistik, Update auslösen, Alarmhistorie | dem Boot beim Problem zusehen, ohne an Bord zu sein | offen, Umfang noch zu besprechen |

**Nicht gebaut wird:** Mehrboot-Fähigkeit, Mandanten, feingranulare
Rechte-Matrix, Cloud-Abhängigkeit für den Bordbetrieb.

---

## Sicherheit

Das System hängt im Internet und schaltet Hardware auf einem Boot, in dem
niemand steht. „Ist sicher" ist keine Aussage — hier stehen die Maßnahmen
einzeln, damit man sie prüfen kann.

### Der Pi hat keinen offenen Port

Das ist die wichtigste Eigenschaft des Entwurfs. Der Pi **baut die Verbindung
selbst auf**, nach außen. Von außen ist er nicht erreichbar — auch nicht über
den Server. Sein TLS-Name zeigt auf eine **private** Adresse, die im Internet
nicht geroutet wird. Wer den Server übernimmt, hat damit keinen Zugriff auf das
Boot, sondern nur auf den Kanal.

Befehle laufen **rückwärts durch dieselbe Verbindung** (ein WebSocket ist
bidirektional). Drei Folgen, alle gewollt:

- **Ohne Verbindung wird nicht geschaltet.** Es gibt keine Warteschlange für
  Schaltbefehle — niemand will, dass sich das Boot beim nächsten Funkkontakt an
  ein „Licht an" von vorgestern erinnert. Der Server antwortet dann mit „Boot
  nicht verbunden", nicht mit „angenommen".
- **Der Pi entscheidet selbst.** Er führt den Befehl gegen seine eigene API aus
  und prüft ihn nach seinen eigenen Regeln. Ein übernommener Server kann ihm
  nichts befehlen, was er nicht auch von der Bordoberfläche annehmen würde.
- **Nur eine Weißliste.** Es gibt keinen allgemeinen Durchleiter: 17 Pfade sind
  eingetragen, alles andere existiert auf dem Server nicht. `/api/system/update`
  und die Fehlermeldungen der Oberfläche stehen ausdrücklich **nicht** darin.

### Die Heizung ist ein Sonderfall

Sie verbrennt Diesel. Fernschalten ist deshalb **standardmäßig gesperrt**, auch
für den Eigner, und lässt sich nur in der Serverkonfiguration freigeben
(`MAVE_FERN_HEIZUNG=ja`) — bewusst dort und nicht als Häkchen in der
Oberfläche: wer es einschaltet, soll es einmal bewusst tun und wiederfinden.

### Am Server

| Maßnahme | Warum |
|---|---|
| Ohne gesetztes Gerätetoken **und** Passwort nimmt der Server nichts an | Ein offener Sammelpunkt im Internet wäre schlimmer als keiner |
| Vergleich mit `secrets.compare_digest` | kein Rückschluss über die Antwortzeit |
| Nach zehn Fehlversuchen zehn Minuten Ruhe, je Herkunft | macht Durchprobieren teuer |
| Paketgröße wird **vor** dem Parsen geprüft | `json.loads` eines 500-MB-Textes belegt sonst den Speicher, bevor eine Prüfung greift |
| Richtungsprüfung im Protokoll | der Server nimmt keinen Befehl von einem Boot an, der Pi keinen Zustand vom Server |
| `/api/system/version` verrät nur die eigene Art | ob das Boot verbunden ist, ist eine Aussage über Anwesenheit und gehört hinter die Anmeldung |
| Container: nicht als root, `read_only`, `cap_drop: ALL`, `no-new-privileges`, Speicher- und Prozessgrenze | ein Ausbruch aus der Anwendung landet bei einem Konto, das nichts besitzt |
| Kein `ports:` am Container, eigenes Docker-Netz | genau eine Tür ins Internet, und die spricht TLS. Der Container erreicht die Gantrya-Dienste nicht |
| HSTS, `nosniff`, `X-Frame-Options: DENY`, strenge CSP, kein `Server`-Kopf | Standardhärtung am Rand, nicht in der Anwendung |
| Der Pi-Teil liegt **nicht** im Serverabbild | `can_reader.py` und die Bordlogik wären nur Angriffsfläche |

### Was noch fehlt

Ehrlich benannt, für die kommenden Etappen:

- **Passwörter werden noch nicht gehasht.** Der Übergangszugang ist ein Token
  aus der Serverkonfiguration. Mit der Kontenverwaltung kommt Argon2 oder
  bcrypt — auf dem Pi mit maßvollen Kosten, denn ARMv6 rechnet langsam, und die
  Anmeldung darf dort keine Sekunden dauern.
- **Sitzungen** (Cookie mit `HttpOnly`, `Secure`, `SameSite`, Rotation) gibt es
  noch nicht; heute wird bei jeder Anfrage das Token geprüft.
- **Keine Geheimnisse in Protokollen.** Das Projekt hat diese Falle schon
  einmal getreten: `git pull` schreibt die Remote-Adresse samt Token in seine
  Fehlermeldung, weshalb `main.py` sie ausdrücklich nicht weitergibt. Dieselbe
  Sorgfalt gilt für Gerätetoken und Passwörter.
- **Ein zweiter Faktor** für den Eigner ist noch offen. Bei einem System, das
  Heizungen schaltet, ist das keine Übertreibung.

---

## Entschieden am 03.09.2026

| Frage | Entscheidung |
|---|---|
| Adresse | **mave.circuit-sailor.com**, der Pi unter **pi.mave.circuit-sailor.com** auf seine LAN-Adresse. Die Zone liegt bei IONOS (`ui-dns.*`), von dort kommt auch das Zertifikat |
| TLS am Pi | **Ja**, echtes Let's-Encrypt-Zertifikat per DNS-01 über die IONOS-API. Der Eigner legt einen API-Schlüssel an, der Pi erneuert selbst. Kein eingehender Port |
| Anmeldung | **Überall Pflicht, auch im Bord-WLAN** (abweichend vom ersten Vorschlag) |
| Datenvolumen | **Zwei Betriebsarten**, siehe unten |
| Server | eigener Container, eigene Daten, eigene Domain — bestätigt |

### Anmeldung überall — was das bedeutet

Die Entscheidung ist strenger als der ursprüngliche Vorschlag und zieht drei
Dinge nach sich:

1. **Der Kiosk braucht eine Gerätesitzung.** Der Touchscreen am Kartentisch darf
   nicht vor einem Anmeldefenster stehen. Er wird einmal als Gerät angemeldet
   und bleibt es — mit einer Rolle, die weniger darf als der Eigner (er hängt
   öffentlich zugänglich an Bord).
2. **Die Anmeldung muss ohne Internet funktionieren**, sonst ist die Anlage im
   Funkloch unbedienbar. Dafür ist die Kontenkopie auf dem Pi da (Abschnitt 3).
   Sie muss vorhanden sein, BEVOR die Anmeldepflicht greift.
3. **Sie kommt früher als geplant.** Heute ist alles offen; die Pflicht betrifft
   alle 48 Endpunkte. Deshalb wandert die Anmeldung von Etappe 2 in Etappe 1 —
   ein Zwischenzustand, in dem der Server öffentlich erreichbar und der Pi offen
   ist, darf nicht entstehen.

### Zwei Betriebsarten für die Übertragung

Im Hafen hängt das Boot über Starlink am Netz, dort ist Volumen kein Thema.
Unterwegs läuft es über Mobilfunk, dort schon. Also:

| Betriebsart | Zustand | Verlauf | wann |
|---|---|---|---|
| **voll** | alle 10 s, bei Änderung sofort | feine Auflösung | Starlink oder Kabel |
| **gedrosselt** | alle 60 s, Alarme trotzdem sofort | nur Minutenmittel | Mobilfunk |

Umgeschaltet wird **automatisch anhand des Uplinks** — und dafür ist nichts
Neues nötig: `connectivity.py` kennt den aktiven Weg schon
(`router.active_type` liefert `wired`, `mobile` oder `wifi`, dazu den
Starlink-Zustand). Der Sync liest das und wählt seinen Takt.

Dazu ein **Handschalter** in den Einstellungen mit drei Stellungen: automatisch,
immer voll, immer gedrosselt. Wer im Hafen an einer teuren Marina-SIM hängt,
will das selbst bestimmen können.

Alarme gehen in **beiden** Betriebsarten sofort hinaus. Sie sind der Grund,
warum das System überhaupt nach draußen spricht.

---

## Was der Eigner beisteuern muss

1. **IONOS-API-Schlüssel** (Public Prefix und Secret) für die DNS-Prüfung des
   Pi-Zertifikats. Blockiert die Umschaltung der PWA, sonst nichts.
2. **DHCP-Reservierung im RUTX50** für den Pi, damit seine Adresse fest ist.

# Konzept — Bordanlage mit Server

Stand 03.09.2026. Entwurf zur Abstimmung, noch keine Umsetzung.

Ziel des Eigners, in seinen Worten: Der Pi Zero bleibt an Bord am NMEA-2000-Bus
und am Bordrouter. **Eine** PWA — im Bord-WLAN spricht sie direkt mit dem Pi,
sonst zieht dieselbe Oberfläche die Daten vom Server. Dazu eine **zweite,
andere** Oberfläche auf dem Server für Diagnose und Fernwartung. Mehrere
Nutzerkonten mit unterschiedlichen Rechten. Nur dieses eine Boot. Losgelöst von
Bosun One und Gantrya.

---

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

## Etappen

Jede Etappe kann für sich etwas, das vorher nicht ging.

| # | Etappe | Danach kannst du | Aufwand |
|---|---|---|---|
| 1 | **Fernblick** — Server-Container, Domain, TLS für den Pi, ausgehende Verbindung, Zustand und Verlauf hinauf, Quellenerkennung in der PWA, ein Konto mit Passwort | von überall sehen, wie es dem Boot geht, und den Verlauf lesen | 3–5 Abende, dazu ein Tag Zertifikatsfummelei |
| 2 | **Konten** — Konten am Server, Kontenkopie zum Pi, lokale Anmeldung, vier Rollen, Gerätesitzung für den Kiosk | Crew und Gäste getrennt zugreifen lassen, an Bord auch ohne Internet | 4–6 Abende |
| 3 | **Befehle** — Warteschlange, Quittung, Vorgriff; Heizung-Fernschalten als freizugebende Ausnahme | aus der Ferne schalten, mit ehrlicher Rückmeldung | 3–4 Abende |
| 4 | **Diagnose und Fernwartung** — eigene Oberfläche: Protokolle, JS-Fehler, Bus-Statistik, Update auslösen, Alarmhistorie | dem Boot beim Problem zusehen, ohne an Bord zu sein | offen, Umfang noch zu besprechen |

**Nicht gebaut wird:** Mehrboot-Fähigkeit, Mandanten, feingranulare
Rechte-Matrix, Cloud-Abhängigkeit für den Bordbetrieb.

---

## Was vor Etappe 1 entschieden sein muss

1. **Welche Domain** — eine der freien oder eine Subdomain von
   `circuit-sailor.com`?
2. **TLS für den Pi** — ist die DNS-Zone per API bedienbar (IONOS)? Ohne echtes
   Zertifikat am Pi ist „eine PWA für beide Seiten" nicht möglich.
3. **Bordnetz** — bleibt Lesen im Bord-WLAN ohne Anmeldung offen, oder wird
   auch dort angemeldet?
4. **Datenvolumen** — welches monatliche Budget darf der Sync kosten? Danach
   richtet sich der Takt.

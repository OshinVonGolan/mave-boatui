# Der Server neben dem Boot. Absichtlich klein: FastAPI, uvicorn, sonst nichts.
# Keine Datenbank im Abbild — SQLite liegt im Datenverzeichnis, das als Volume
# hereinkommt.
FROM python:3.12-slim

# Nicht als root. Ein Ausbruch aus der Anwendung landet dann bei einem Konto,
# das nichts besitzt ausser dem Datenverzeichnis.
RUN useradd --create-home --uid 10001 mave

WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.115.*" "uvicorn[standard]==0.32.*"

# Nur was gebraucht wird. Der Pi-Teil (main.py, can_reader.py und der Rest)
# gehoert NICHT ins Serverabbild — er wuerde nur Angriffsflaeche mitbringen.
# Die Bundle-Reihenfolge teilen sich Pi und Server — sonst laufen die
# beiden Oberflaechen auseinander.
# Drei Dateien aus der Wurzel, und nur diese drei. Sie sind geteilt, nicht
# geliehen: die Bundle-Reihenfolge muss auf beiden Seiten gleich sein, und ein
# Konto muss an Bord und auf dem Server identisch behandelt werden — sonst
# meldet sich jemand hier an und dort nicht. jsonio haengt an
# konten_speicher (atomares Schreiben von JSON).
# Der uebrige Pi-Code (main.py, can_reader.py und der Rest) bleibt draussen:
# er waere hier nur Angriffsflaeche.
COPY js_bundle_liste.py konten_speicher.py jsonio.py /app/
COPY sync/    /app/sync/
COPY server/  /app/server/
# Die Oberflaeche: dieselben Dateien wie auf dem Pi, damit es nicht zwei
# Staende gibt. Der Pi-Code (main.py, can_reader.py und der Rest) bleibt
# draussen — er waere hier nur Angriffsflaeche.
COPY static/  /app/static/

# Das Datenverzeichnis muss dem Benutzer gehoeren, BEVOR das Volume gemountet
# wird: Docker uebernimmt beim ersten Mount die Rechte aus dem Abbild. Fehlt
# das, gehoert /daten root — und der Dienst, der als mave laeuft, kann seine
# Datenbank nicht anlegen.
RUN mkdir -p /daten && chown mave:mave /daten
VOLUME /daten

USER mave
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MAVE_DATEN=/daten
EXPOSE 8000

# Ein Arbeiter reicht: es geht um ein Boot und eine Handvoll Browser. Mehrere
# Arbeiter haetten getrennte Zustaende (die offene Verbindung zum Boot liegt in
# EINEM Prozess) — das waere ein Fehler, keine Leistungssteigerung.
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]

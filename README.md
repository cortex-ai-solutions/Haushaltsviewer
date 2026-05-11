# Thüringer Haushaltsplan 2026/2027 – Interaktives Dashboard

Öffentliches, interaktives Dashboard zum Doppelhaushalt 2026/2027 des Freistaats Thüringen.  
Hosted auf GitHub Pages · Daten: [Thüringer Finanzministerium](https://finanzen.thueringen.de/themen/haushalt/haushaltsplaene/haushalt-2026/2027)

---

## Features

- 🔍 **Natural-Language-Query** – Fragen auf Deutsch, z.B. *„Wieviel kostet das Personal im Finanzministerium?"*
- 📊 **Treemap** – Gesamthaushalt auf einen Blick
- 🗃️ **Explorer** – Filterbar nach Ministerium, Ausgabenart, Jahr
- ⚡ **Komplett clientseitig** – DuckDB läuft im Browser, kein Server nötig
- 📥 **CSV-Download** – Rohdaten transparent verfügbar

---

## Schnellstart (Pilot: Finanzministerium EP 06)

```bash
# 1. Repository klonen
git clone https://github.com/DEIN-NAME/thuringen-haushalt.git
cd thuringen-haushalt

# 2. Python-Umgebung
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac/Linux

pip install -r pipeline/requirements.txt

# 3. Pipeline ausführen (nur EP 06 als Pilot)
python pipeline/01_download.py --pilot
python pipeline/02_parse.py --pilot
python pipeline/03_build_db.py
python pipeline/04_validate.py

# 4. Datenbank ins docs-Verzeichnis kopieren
mkdir docs\data
copy data\haushalt.db docs\data\
copy data\haushaltsstellen.csv docs\data\
copy data\meta.json docs\data\
```

Dann `docs/index.html` im Browser öffnen (oder lokalen Server starten):
```bash
python -m http.server 8080 --directory docs
# → http://localhost:8080
```

---

## Alle Einzelpläne verarbeiten

```bash
python pipeline/01_download.py    # alle 19 PDFs
python pipeline/02_parse.py       # alle PDFs parsen
python pipeline/03_build_db.py
python pipeline/04_validate.py
```

---

## PDF-Struktur debuggen

Falls der Parser falsche Ergebnisse liefert:

```bash
python pipeline/02_parse.py --debug ep_06
```

Zeigt die ersten 10 Seiten mit Rohzeilen und erkannten Tabellen.

---

## NL-Query einrichten (Cloudflare Worker)

1. [Cloudflare-Account](https://cloudflare.com) anlegen (kostenlos)
2. Wrangler installieren: `npm install -g wrangler`
3. Login: `wrangler login`
4. Worker deployen:
   ```bash
   cd worker
   wrangler deploy
   ```
5. Claude API-Key als Secret hinterlegen:
   ```bash
   wrangler secret put ANTHROPIC_API_KEY
   ```
6. Worker-URL im Dashboard eintragen (erscheint beim ersten Klick auf "Fragen →")

---

## GitHub Pages automatisch deployen

1. Repo auf GitHub pushen
2. Settings → Pages → Source: `gh-pages` Branch
3. Actions → „Haushaltsplan Pipeline & Deploy" → Run workflow

Die GitHub Action lädt PDFs, parst sie, baut die DB und deployt automatisch.

---

## Projektstruktur

```
thuringen-haushalt/
├── pipeline/
│   ├── requirements.txt      # Python-Abhängigkeiten
│   ├── 01_download.py        # PDFs von finanzen.thueringen.de laden
│   ├── 02_parse.py           # Tabellenextraktion (pdfplumber)
│   ├── 03_build_db.py        # SQLite-Datenbank aufbauen
│   └── 04_validate.py        # Plausibilitätsprüfung
├── data/                     # Generierte Daten (nicht im Git)
│   ├── pdfs/                 # Heruntergeladene PDFs
│   ├── haushalt.db           # SQLite-Datenbank
│   └── haushaltsstellen.csv  # Bereinigte CSV
├── docs/                     # GitHub Pages
│   ├── index.html            # Dashboard
│   ├── app.js                # DuckDB WASM + UI
│   ├── style.css             # Design
│   └── data/                 # Kopie der DB (für GitHub Pages)
├── worker/
│   ├── haushalt-worker.js    # Cloudflare Worker (NL→SQL Proxy)
│   └── wrangler.toml         # Worker-Konfiguration
└── .github/workflows/
    └── deploy.yml            # Automatischer Build + Deploy
```

---

## Datenquelle & Lizenz

Daten: [Thüringer Finanzministerium](https://finanzen.thueringen.de) (öffentlich zugänglich)  
Code: MIT-Lizenz  
Keine offizielle Seite des Freistaats Thüringen.

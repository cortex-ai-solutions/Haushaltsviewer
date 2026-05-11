"""
03_build_db.py – Baut die SQLite-Datenbank aus den extrahierten Rohdaten.

Ausführen: python pipeline/03_build_db.py

Ergebnis:
  data/haushalt.db           – SQLite-Datenbank (für GitHub Pages)
  data/haushaltsstellen.csv  – Bereinigte CSV (für Transparenz)
  data/meta.json             – Metadaten
"""

import io
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd

DATA_DIR        = Path(__file__).parent.parent / "data"
RAW_CSV         = DATA_DIR / "haushaltsstellen_raw.csv"
CLEAN_CSV       = DATA_DIR / "haushaltsstellen.csv"
DB_PATH         = DATA_DIR / "haushalt.db"
META_PATH       = DATA_DIR / "meta.json"
STELLEN_CSV     = DATA_DIR / "stellenplan_raw.csv"
UEBERSICHT_CSV  = DATA_DIR / "stellenuebersicht_raw.csv"

MINISTERIEN = {
    "01": "Thüringer Landtag",
    "02": "Thüringer Staatskanzlei",
    "03": "Ministerium für Inneres, Kommunales und Landesentwicklung",
    "04": "Ministerium für Bildung, Wissenschaft und Kultur",
    "05": "Ministerium für Justiz, Migration und Verbraucherschutz",
    "06": "Finanzministerium",
    "07": "Ministerium für Wirtschaft, Landwirtschaft und Ländlichen Raum",
    "08": "Ministerium für Soziales, Gesundheit, Arbeit und Familie",
    "09": "Ministerium für Umwelt, Energie, Naturschutz und Forsten",
    "10": "Ministerium für Digitales und Infrastruktur",
    "11": "Thüringer Rechnungshof",
    "12": "Thüringer Verfassungsgerichtshof",
    "16": "Informations- und Kommunikationstechnik",
    "17": "Allgemeine Finanzverwaltung",
    "18": "Staatliche Hochbaumaßnahmen",
    "00": "Gesamtplan",
}

PDF_URLS = {
    ep: f"https://finanzen.thueringen.de/fileadmin/medien_tfm/Haushalt/2026_2027/ep_{ep}_2026_2027.pdf"
    for ep in MINISTERIEN if ep != "00"
}


CREATE_HAUSHALTSSTELLEN = """
CREATE TABLE IF NOT EXISTS haushaltsstellen (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    einzelplan       TEXT NOT NULL,
    ministerium      TEXT NOT NULL,
    kapitel          TEXT,
    kapitel_name     TEXT,
    titel            TEXT,
    fkz              TEXT,
    titel_name       TEXT,
    hauptgruppe      TEXT,
    hauptgruppe_name TEXT,
    ansatz_2025      REAL,
    ansatz_2026      REAL,
    ansatz_2027      REAL,
    ist_2024         REAL,
    seite_pdf        INTEGER,
    quelle_pdf       TEXT
);
"""

CREATE_KAPITEL = """
CREATE TABLE IF NOT EXISTS kapitel (
    kapitel      TEXT PRIMARY KEY,
    einzelplan   TEXT,
    name         TEXT
);
"""

CREATE_EINZELPLAENE = """
CREATE TABLE IF NOT EXISTS einzelplaene (
    nr      TEXT PRIMARY KEY,
    name    TEXT,
    pdf_url TEXT
);
"""

CREATE_STELLENPLAN = """
CREATE TABLE IF NOT EXISTS stellenplan (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    einzelplan     TEXT NOT NULL,
    ministerium    TEXT NOT NULL,
    kapitel        TEXT,
    besoldung      TEXT,   -- z.B. "A12", "B3", "E9a"
    laufbahn       TEXT,   -- "hD", "gD", "mD" (nur Beamte)
    bezeichnung    TEXT,   -- z.B. "Amtsrat", "Tarifbeschäftigter"
    typ            TEXT,   -- "Beamter" oder "Tarifbeschäftigter"
    stellen_2025   INTEGER,
    stellen_2026   INTEGER,
    stellen_2027   INTEGER,
    kw_stellen     INTEGER,   -- kw = künftig wegfallend
    kw_ab_jahr     INTEGER,
    seite_pdf      INTEGER,
    quelle_pdf     TEXT
);
"""

CREATE_STELLENUEBERSICHT = """
CREATE TABLE IF NOT EXISTS stellenuebersicht (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    einzelplan  TEXT NOT NULL,
    ministerium TEXT NOT NULL,
    kapitel     TEXT,
    typ         TEXT,   -- "Beamter", "Tarifbeschäftigter", "Gesamt"
    jahr        INTEGER,
    stellen     INTEGER,
    seite_pdf   INTEGER,
    quelle_pdf  TEXT
);
"""

CREATE_VIEWS = """
CREATE VIEW IF NOT EXISTS v_personal AS
SELECT einzelplan, ministerium, kapitel, kapitel_name,
       titel, titel_name,
       ansatz_2026, ansatz_2027, ist_2024
FROM haushaltsstellen
WHERE hauptgruppe = '4';

CREATE VIEW IF NOT EXISTS v_ministerium_summen AS
SELECT ministerium,
       einzelplan,
       SUM(CASE WHEN hauptgruppe = '4' THEN ansatz_2026 ELSE 0 END) AS personal_2026,
       SUM(CASE WHEN hauptgruppe = '4' THEN ansatz_2027 ELSE 0 END) AS personal_2027,
       SUM(CASE WHEN hauptgruppe IN ('4','5','6','7','8','9') THEN ansatz_2026 ELSE 0 END) AS ausgaben_2026,
       SUM(CASE WHEN hauptgruppe IN ('4','5','6','7','8','9') THEN ansatz_2027 ELSE 0 END) AS ausgaben_2027,
       SUM(ansatz_2026)  AS gesamt_2026,
       SUM(ansatz_2027)  AS gesamt_2027,
       SUM(ist_2024)     AS gesamt_ist_2024
FROM haushaltsstellen
GROUP BY ministerium, einzelplan;
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ep        ON haushaltsstellen(einzelplan);
CREATE INDEX IF NOT EXISTS idx_kapitel   ON haushaltsstellen(kapitel);
CREATE INDEX IF NOT EXISTS idx_hgr       ON haushaltsstellen(hauptgruppe);
CREATE INDEX IF NOT EXISTS idx_ministerium ON haushaltsstellen(ministerium);
CREATE INDEX IF NOT EXISTS idx_stellen_ep  ON stellenplan(einzelplan);
CREATE INDEX IF NOT EXISTS idx_stellen_kap ON stellenplan(kapitel);
CREATE INDEX IF NOT EXISTS idx_ueber_kap   ON stellenuebersicht(kapitel, jahr, typ);
"""


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Zahlspalten bereinigen
    for col in ["ansatz_2026", "ansatz_2027", "ist_2024"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Textspalten bereinigen
    for col in ["titel_name", "kapitel_name", "hauptgruppe_name"]:
        df[col] = df[col].fillna("").str.strip()
    df["einzelplan"] = df["einzelplan"].astype(str).str.zfill(2)
    df["kapitel"] = df["kapitel"].astype(str).str.strip()
    return df


def build_db(df: pd.DataFrame):
    if DB_PATH.exists():
        DB_PATH.unlink()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.executescript(
        CREATE_HAUSHALTSSTELLEN + CREATE_KAPITEL + CREATE_EINZELPLAENE
        + CREATE_STELLENPLAN + CREATE_STELLENUEBERSICHT
    )

    # Einzelpläne
    eps = df[["einzelplan", "ministerium"]].drop_duplicates()
    for _, row in eps.iterrows():
        ep = row["einzelplan"]
        url = PDF_URLS.get(ep, "")
        cur.execute(
            "INSERT OR REPLACE INTO einzelplaene (nr, name, pdf_url) VALUES (?, ?, ?)",
            (ep, row["ministerium"], url),
        )

    # Kapitel
    kap = df[["kapitel", "einzelplan", "kapitel_name"]].drop_duplicates(subset=["kapitel"])
    for _, row in kap.iterrows():
        if row["kapitel"]:
            cur.execute(
                "INSERT OR REPLACE INTO kapitel (kapitel, einzelplan, name) VALUES (?, ?, ?)",
                (row["kapitel"], row["einzelplan"], row["kapitel_name"]),
            )

    # Haushaltsstellen
    cols = [
        "einzelplan", "ministerium", "kapitel", "kapitel_name",
        "titel", "fkz", "titel_name", "hauptgruppe", "hauptgruppe_name",
        "ansatz_2025", "ansatz_2026", "ansatz_2027", "ist_2024",
        "seite_pdf", "quelle_pdf",
    ]
    df_insert = df[[c for c in cols if c in df.columns]]
    df_insert.to_sql("haushaltsstellen", con, if_exists="append", index=False)

    # Stellenplan (Detail: je Besoldungsgruppe)
    if STELLEN_CSV.exists():
        df_stellen = pd.read_csv(STELLEN_CSV, dtype=str)
        # Spaltennamen auf DB-Schema normieren
        df_stellen = df_stellen.rename(columns={
            "besgruppe":  "besoldung",
            "kw_anzahl":  "kw_stellen",
            "kw_jahr":    "kw_ab_jahr",
        })
        for col in ["stellen_2025", "stellen_2026", "stellen_2027", "kw_stellen", "kw_ab_jahr"]:
            if col in df_stellen.columns:
                df_stellen[col] = pd.to_numeric(df_stellen[col], errors="coerce")
        df_stellen["einzelplan"] = df_stellen["einzelplan"].astype(str).str.zfill(2)
        df_stellen["kapitel"]    = df_stellen["kapitel"].astype(str).str.zfill(4)
        stellen_cols = [
            "einzelplan", "ministerium", "kapitel", "besoldung", "laufbahn",
            "bezeichnung", "typ", "stellen_2025", "stellen_2026", "stellen_2027",
            "kw_stellen", "kw_ab_jahr", "seite_pdf", "quelle_pdf",
        ]
        df_stellen[[c for c in stellen_cols if c in df_stellen.columns]].to_sql(
            "stellenplan", con, if_exists="append", index=False
        )
        print(f"  Stellenplan: {len(df_stellen)} Positionen geladen")
    else:
        print(f"  Stellenplan: {STELLEN_CSV.name} nicht gefunden – übersprungen")

    # Stellenübersicht (Kapitel-Summen)
    if UEBERSICHT_CSV.exists():
        df_ueber = pd.read_csv(UEBERSICHT_CSV, dtype=str)
        df_ueber["stellen"] = pd.to_numeric(df_ueber["stellen"], errors="coerce")
        df_ueber["jahr"]    = pd.to_numeric(df_ueber["jahr"], errors="coerce")
        df_ueber["einzelplan"] = df_ueber["einzelplan"].astype(str).str.zfill(2)
        # Kapitel mit führenden Nullen auffüllen (außer "GESAMT")
        df_ueber["kapitel"] = df_ueber["kapitel"].apply(
            lambda x: x.zfill(4) if str(x).isdigit() else x
        )
        ueber_cols = ["einzelplan", "ministerium", "kapitel", "typ", "jahr", "stellen", "seite_pdf", "quelle_pdf"]
        df_ueber[[c for c in ueber_cols if c in df_ueber.columns]].to_sql(
            "stellenuebersicht", con, if_exists="append", index=False
        )
        print(f"  Stellenübersicht: {len(df_ueber)} Summen-Zeilen geladen")
    else:
        print(f"  Stellenübersicht: {UEBERSICHT_CSV.name} nicht gefunden – übersprungen")

    cur.executescript(CREATE_VIEWS + CREATE_INDEX)
    con.commit()
    con.close()


def export_json_files(df_haus: pd.DataFrame):
    """Exportiert alle Tabellen als JSON → docs/data/ (für DuckDB WASM im Browser)."""
    docs_data = DATA_DIR.parent / "docs" / "data"
    docs_data.mkdir(parents=True, exist_ok=True)

    # haushaltsstellen
    df_haus.to_json(docs_data / "haushaltsstellen.json", orient="records",
                    force_ascii=False, indent=None)
    print(f"  JSON: haushaltsstellen.json ({len(df_haus)} Zeilen)")

    # kapitel
    kap = df_haus[["kapitel", "einzelplan", "kapitel_name"]].drop_duplicates(subset=["kapitel"])
    kap.to_json(docs_data / "kapitel.json", orient="records", force_ascii=False)
    print(f"  JSON: kapitel.json ({len(kap)} Zeilen)")

    # einzelplaene
    eps = df_haus[["einzelplan", "ministerium"]].drop_duplicates()
    eps = eps.rename(columns={"einzelplan": "nr", "ministerium": "name"})
    eps["pdf_url"] = eps["nr"].map(PDF_URLS).fillna("")
    eps.to_json(docs_data / "einzelplaene.json", orient="records", force_ascii=False)
    print(f"  JSON: einzelplaene.json ({len(eps)} Zeilen)")

    # stellenplan
    if STELLEN_CSV.exists():
        df_s = pd.read_csv(STELLEN_CSV, dtype=str).rename(columns={
            "besgruppe": "besoldung", "kw_anzahl": "kw_stellen", "kw_jahr": "kw_ab_jahr"
        })
        for col in ["stellen_2025", "stellen_2026", "stellen_2027", "kw_stellen", "kw_ab_jahr"]:
            if col in df_s.columns:
                df_s[col] = pd.to_numeric(df_s[col], errors="coerce")
        df_s["kapitel"] = df_s["kapitel"].astype(str).str.zfill(4)
        df_s.to_json(docs_data / "stellenplan.json", orient="records", force_ascii=False)
        print(f"  JSON: stellenplan.json ({len(df_s)} Zeilen)")

    # stellenuebersicht
    if UEBERSICHT_CSV.exists():
        df_u = pd.read_csv(UEBERSICHT_CSV, dtype=str)
        df_u["stellen"] = pd.to_numeric(df_u["stellen"], errors="coerce")
        df_u["jahr"]    = pd.to_numeric(df_u["jahr"],    errors="coerce")
        df_u["kapitel"] = df_u["kapitel"].apply(
            lambda x: str(x).zfill(4) if str(x).isdigit() else x
        )
        df_u.to_json(docs_data / "stellenuebersicht.json", orient="records", force_ascii=False)
        print(f"  JSON: stellenuebersicht.json ({len(df_u)} Zeilen)")


def main():
    if not RAW_CSV.exists():
        print(f"Rohdaten nicht gefunden: {RAW_CSV}")
        print("Zuerst ausführen: python pipeline/02_parse.py")
        raise SystemExit(1)

    print(f"\nLade Rohdaten: {RAW_CSV}")
    df = pd.read_csv(RAW_CSV, dtype=str)
    print(f"  {len(df)} Zeilen geladen")

    df = clean_df(df)

    # Duplikate entfernen (gleiche EP + Kapitel + Titel)
    before = len(df)
    df = df.drop_duplicates(subset=["einzelplan", "kapitel", "titel"])
    print(f"  {before - len(df)} Duplikate entfernt → {len(df)} Haushaltsstellen")

    print(f"\nBaue Datenbank: {DB_PATH}")
    build_db(df)

    # Bereinigte CSV exportieren
    df.to_csv(CLEAN_CSV, index=False, encoding="utf-8-sig")
    print(f"Bereinigte CSV: {CLEAN_CSV}")

    # JSON-Export für DuckDB WASM (kein SQLite-Extension nötig im Browser)
    export_json_files(df)

    # Metadaten
    meta = {
        "stand":           datetime.now().strftime("%Y-%m-%d"),
        "haushaltsjahr":   "2026/2027",
        "bundesland":      "Thüringen",
        "quelle":          "https://finanzen.thueringen.de",
        "haushaltsstellen": int(len(df)),
        "einzelplaene":    int(df["einzelplan"].nunique()),
        "kapitel":         int(df["kapitel"].nunique()),
        "summe_2026_eur":  float(df["ansatz_2026"].sum()),
        "summe_2027_eur":  float(df["ansatz_2027"].sum()),
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Metadaten: {META_PATH}")

    print(f"\n{'='*50}")
    print(f"Gesamtansatz 2026: {meta['summe_2026_eur'] / 1e9:.2f} Mrd. EUR")
    print(f"Gesamtansatz 2027: {meta['summe_2027_eur'] / 1e9:.2f} Mrd. EUR")
    print("\nWeiter mit: python pipeline/04_validate.py")


if __name__ == "__main__":
    main()

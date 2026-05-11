"""
02_parse.py – Tabellenextraktion aus Thüringer Haushaltsplan-PDFs (pdfplumber).

Ausführen:
  python pipeline/02_parse.py --pilot          # nur EP 06
  python pipeline/02_parse.py --debug ep_06    # Rohzeilen einer PDF anzeigen
  python pipeline/02_parse.py                  # alle heruntergeladenen PDFs

Ergebnis: data/haushaltsstellen_raw.csv (eine Zeile pro Haushaltsstelle)
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pdfplumber

DATA_DIR   = Path(__file__).parent.parent / "data"
PDF_DIR    = DATA_DIR / "pdfs"
OUTPUT_CSV = DATA_DIR / "haushaltsstellen_raw.csv"

# ── Haushaltssystematik ────────────────────────────────────────────────────────
HAUPTGRUPPEN = {
    "0": "Einnahmen – Steuern",
    "1": "Einnahmen – Verwaltungseinnahmen",
    "2": "Einnahmen – Zuweisungen",
    "3": "Einnahmen – Schuldenaufnahmen",
    "4": "Ausgaben – Personal",
    "5": "Ausgaben – Sachmittel",
    "6": "Ausgaben – Zuweisungen/Zuschüsse",
    "7": "Ausgaben – Baumaßnahmen",
    "8": "Ausgaben – Investitionen",
    "9": "Ausgaben – Besondere Finanzierungsausgaben",
}

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
}

# Regex-Muster ─────────────────────────────────────────────────────────────────
RE_KAPITEL     = re.compile(r"Kapitel\s+(\d{4})", re.IGNORECASE)
RE_TITEL_NR    = re.compile(r"^\s*(\d{3}\s*\d{2})\s")   # z.B. "422 01"
RE_ZAHL        = re.compile(r"^-?[\d\.]+$")
RE_SUMME       = re.compile(r"(Summe|Gesamtsumme|Einnahmen|Ausgaben)\s+Kapitel", re.IGNORECASE)
RE_EP_HEADER   = re.compile(r"Einzelplan\s+(\d{2})", re.IGNORECASE)


def clean_zahl(s: str | None) -> float | None:
    """'1.234.567' → 1234567.0, '-' oder None → None"""
    if not s:
        return None
    s = s.strip().replace(".", "").replace(",", ".").replace(" ", "")
    if s in ("-", "–", "—", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def detect_orientation(page) -> str:
    """'landscape' wenn Breite > Höhe, sonst 'portrait'"""
    return "landscape" if page.width > page.height else "portrait"


def extract_kapitel_name(page_text: str, kapitel_nr: str) -> str:
    """Versucht, den Kapitelnamen aus dem Seitentext zu extrahieren."""
    lines = page_text.splitlines()
    for i, line in enumerate(lines):
        if kapitel_nr in line and "Kapitel" in line:
            # Nächste nicht-leere Zeile als Name
            for j in range(i + 1, min(i + 4, len(lines))):
                candidate = lines[j].strip()
                if candidate and not RE_KAPITEL.match(candidate):
                    return candidate
    return ""


def parse_row(row: list) -> dict | None:
    """
    Versucht, eine Tabellenzeile als Haushaltsstelle zu interpretieren.
    Erwartet Spaltenreihenfolge: [Tit., (FKZ), Zweckbestimmung, Ansatz2026, Ansatz2027, Ist2024]
    Flexibel gegenüber leeren Zellen und unterschiedlichen Spaltenzahlen.
    """
    if not row:
        return None

    # Alle Zellen als String, leer → ""
    cells = [str(c).strip() if c is not None else "" for c in row]

    # Ersten nicht-leeren Wert als Titelkennzahl versuchen
    titel_raw = cells[0] if cells else ""
    titel_match = RE_TITEL_NR.match(titel_raw + " ")
    if not titel_match:
        return None

    titel = titel_match.group(1).replace(" ", "")  # "42201"
    hauptgruppe = titel[0]  # erste Stelle = Hauptgruppe

    # Bezeichnung ist die nächste nicht-numerische Zelle
    bezeichnung = ""
    zahl_cells = []
    for cell in cells[1:]:
        if not bezeichnung and not RE_ZAHL.match(cell.replace(".", "").replace(",", "").replace("-", "").replace("–", "").strip()):
            bezeichnung = cell
        elif cell:
            zahl_cells.append(cell)

    # Beträge: Ansatz 2026, Ansatz 2027, Ist 2024 (in dieser Reihenfolge)
    ansatz_2026 = clean_zahl(zahl_cells[0]) if len(zahl_cells) > 0 else None
    ansatz_2027 = clean_zahl(zahl_cells[1]) if len(zahl_cells) > 1 else None
    ist_2024    = clean_zahl(zahl_cells[2]) if len(zahl_cells) > 2 else None

    return {
        "titel":       titel,
        "titel_name":  bezeichnung,
        "hauptgruppe": hauptgruppe,
        "hauptgruppe_name": HAUPTGRUPPEN.get(hauptgruppe, ""),
        "ansatz_2026": ansatz_2026,
        "ansatz_2027": ansatz_2027,
        "ist_2024":    ist_2024,
    }


def parse_pdf(pdf_path: Path, einzelplan_nr: str, debug: bool = False) -> list[dict]:
    """
    Verarbeitet eine Haushaltsplan-PDF und gibt eine Liste von Haushaltsstellen zurück.
    """
    ministerium = MINISTERIEN.get(einzelplan_nr, f"EP {einzelplan_nr}")
    records = []
    current_kapitel = None
    current_kapitel_name = ""
    seiten_ohne_tabelle = 0

    print(f"\n  Verarbeite {pdf_path.name} ({ministerium}) ...")

    with pdfplumber.open(pdf_path) as pdf:
        print(f"  Seiten: {len(pdf.pages)}")

        for page_num, page in enumerate(pdf.pages, start=1):
            orientation = detect_orientation(page)
            text = page.extract_text() or ""

            # Kapitel-Header erkennen (auch auf Portrait-Seiten)
            kapitel_match = RE_KAPITEL.search(text)
            if kapitel_match:
                current_kapitel = kapitel_match.group(1)
                current_kapitel_name = extract_kapitel_name(text, current_kapitel)
                if debug:
                    print(f"    Seite {page_num} [{orientation}]: Kapitel {current_kapitel} – {current_kapitel_name}")

            # Nur Querformat-Seiten (oder explizite Tabellen) für Betragszeilen nutzen
            table_settings = {
                "vertical_strategy":   "text",
                "horizontal_strategy": "text",
                "snap_tolerance":      5,
                "join_tolerance":      3,
                "min_words_vertical":  2,
            }

            table = page.extract_table(table_settings)

            if not table:
                seiten_ohne_tabelle += 1
                if debug and orientation == "landscape":
                    print(f"    Seite {page_num} [{orientation}]: KEINE Tabelle erkannt")
                continue

            if debug:
                print(f"\n    === Seite {page_num} [{orientation}] – {len(table)} Zeilen ===")
                for i, row in enumerate(table[:8]):
                    print(f"    [{i}] {row}")
                if len(table) > 8:
                    print(f"    ... (+{len(table)-8} weitere Zeilen)")

            for row in table:
                # Summierzeilen überspringen
                row_text = " ".join(str(c) for c in row if c)
                if RE_SUMME.search(row_text):
                    continue

                parsed = parse_row(row)
                if parsed:
                    parsed.update({
                        "einzelplan":    einzelplan_nr,
                        "ministerium":   ministerium,
                        "kapitel":       current_kapitel or "",
                        "kapitel_name":  current_kapitel_name,
                        "seite_pdf":     page_num,
                        "quelle_pdf":    pdf_path.name,
                    })
                    records.append(parsed)

    print(f"  → {len(records)} Haushaltsstellen extrahiert ({seiten_ohne_tabelle} Seiten ohne Tabelle)")
    return records


def debug_mode(key: str):
    """Zeigt Rohzeilen einer einzelnen PDF – zum Verstehen der Struktur."""
    pdf_path = PDF_DIR / f"{key}.pdf"
    if not pdf_path.exists():
        print(f"PDF nicht gefunden: {pdf_path}")
        print("Zuerst ausführen: python pipeline/01_download.py --pilot")
        sys.exit(1)

    print(f"\n=== DEBUG-Modus: {pdf_path.name} ===\n")
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages[:10], start=1):
            orientation = detect_orientation(page)
            text = page.extract_text() or ""
            table = page.extract_table()
            print(f"\n{'='*60}")
            print(f"Seite {page_num} [{orientation}] – Textauszug:")
            print(text[:400].replace("\n", " | "))
            if table:
                print(f"\nTabelle ({len(table)} Zeilen, erste 5):")
                for row in table[:5]:
                    print(f"  {row}")
            else:
                print("  → Keine Tabelle erkannt")


def main():
    parser = argparse.ArgumentParser(description="PDF-Parser Thüringer Haushaltsplan")
    parser.add_argument("--pilot", action="store_true", help="Nur EP 06 verarbeiten")
    parser.add_argument("--debug", metavar="KEY", help="Debug-Modus für eine PDF (z.B. ep_06)")
    args = parser.parse_args()

    if args.debug:
        debug_mode(args.debug)
        return

    # PDFs auswählen
    if args.pilot:
        pdf_keys = ["ep_06"]
    else:
        pdf_keys = [p.stem for p in sorted(PDF_DIR.glob("*.pdf"))]

    if not pdf_keys:
        print("Keine PDFs gefunden. Zuerst ausführen: python pipeline/01_download.py")
        sys.exit(1)

    all_records = []
    fehler = []

    for key in pdf_keys:
        pdf_path = PDF_DIR / f"{key}.pdf"
        if not pdf_path.exists():
            print(f"  ✗ {key}.pdf nicht gefunden – übersprungen")
            continue
        nr = key.replace("ep_", "").zfill(2) if key.startswith("ep_") else "00"
        try:
            records = parse_pdf(pdf_path, nr)
            all_records.extend(records)
        except Exception as e:
            print(f"  ✗ FEHLER bei {key}: {e}")
            fehler.append(key)

    if not all_records:
        print("\n⚠ Keine Daten extrahiert.")
        print("Tipp: Starte mit --debug ep_06 um die PDF-Struktur zu prüfen.")
        sys.exit(1)

    df = pd.DataFrame(all_records)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n{'='*50}")
    print(f"Gesamt: {len(df)} Haushaltsstellen")
    print(f"Einzelpläne: {df['einzelplan'].nunique()}")
    print(f"Kapitel: {df['kapitel'].nunique()}")
    print(f"Ausgabe: {OUTPUT_CSV}")

    if fehler:
        print(f"Fehlgeschlagen: {fehler}")

    print("\nWeiter mit: python pipeline/03_build_db.py")


if __name__ == "__main__":
    main()

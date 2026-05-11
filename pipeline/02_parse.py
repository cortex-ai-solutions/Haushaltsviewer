"""
02_parse.py – Tabellenextraktion aus Thüringer Haushaltsplan-PDFs.

Strategie: extract_text() pro Seite + Zeile-für-Zeile-Regex
(extract_table() greift nicht, da Haushaltsstellen im Fließtext liegen)

Ausführen:
  python pipeline/02_parse.py --pilot          # nur EP 06
  python pipeline/02_parse.py --debug ep_06    # Rohzeilen einer PDF anzeigen
  python pipeline/02_parse.py                  # alle heruntergeladenen PDFs

Ergebnis: data/haushaltsstellen_raw.csv
"""

import argparse
import io
import re
import sys
from pathlib import Path

import pandas as pd
import pdfplumber

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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

# ── Regex-Muster ───────────────────────────────────────────────────────────────

# Kapitel-Header im Seiten-Kopf: "06 01 Ministerium" oder "06 03 Thüringer Landesamt..."
# Format in extract_text(): "[EP] [Ministerium] | [EP] [KapNr] [KapName] | Titel..."
RE_KAPITEL_HEADER = re.compile(
    r'(?:^|\|)\s*\d{2}\s+(\d{2})\s+([A-ZÄÖÜ][^\|]{3,60}?)(?:\s*\||$)',
    re.MULTILINE
)

# Zeile mit Ist-2024-Wert (folgt auf Titelzeile): ". 11.581" oder "11.581"
RE_IST_ZEILE = re.compile(
    r'^[\s\.]*(\d{1,3}(?:\.\d{3})*)\s*[\.\s]*$'
)

# Seiten überspringen die nur Erläuterungen/Stellenplan enthalten
SKIP_PATTERNS = [
    re.compile(r'Erläuterungen zu den Änderungen im Stellenplan'),
    re.compile(r'Erläuterungen zu den Änderungen in der Stellenübersicht'),
    re.compile(r'Haushaltsbelastungen nach Jahren'),
    re.compile(r'Verpflichtungsermächtigung.*fällig', re.DOTALL),
]

# Zeilenmuster die keine Haushaltsstellen sind
SKIP_LINE_PATTERNS = [
    re.compile(r'^(Summe|Abschluss|Einnahmen|Ausgaben|HGr\.|TGr\.|Titelgruppen|Angaben|Einzelplan)', re.IGNORECASE),
    re.compile(r'^-\s+\d+\s+-'),        # Seitennummern "- 15 -"
    re.compile(r'^noch zu \d'),         # Fortsetzungsseiten
    re.compile(r'^Belast\.'),           # VE-Tabellen
    re.compile(r'^[A-Z]\d+\s+[a-z]'),  # Besoldungsgruppen "A13 hD"
    re.compile(r'^E\s+\d+\s+Tarif'),   # Entgeltgruppen
    re.compile(r'^Summen?\s'),
    re.compile(r'^kw:'),
]


def clean_zahl(s: str | None) -> float | None:
    """'1.234.567' → 1234567.0  |  '-' oder '' → None"""
    if not s:
        return None
    s = str(s).strip().replace(".", "").replace(",", ".").replace("\xa0", "")
    if s in ("-", "–", "—", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_betrag_token(token: str) -> bool:
    """Prüft ob ein Token ein Geldbetrag sein könnte (z.B. '64.800', '0', '-')."""
    return bool(re.match(r'^\d{1,3}(?:\.\d{3})*$|^0$|^-$', token))


def parse_titel_line(line: str) -> dict | None:
    """
    Versucht, eine Textzeile als Haushaltsstelle zu interpretieren.

    Erwartetes Format:
      NNN NN [NNN] Bezeichnung ... Ansatz2025 Ansatz2026 Ansatz2027

    Rückgabe: dict mit Feldern oder None wenn keine Haushaltsstelle.
    """
    tokens = line.strip().split()

    # Mindestanforderung: Titelkennzahl (3+2 Stellen) + mind. 2 Beträge
    if len(tokens) < 5:
        return None

    # Token 0: exakt 3 Ziffern (Titelgruppe + lfd.Nr.)
    if not re.match(r'^\d{3}$', tokens[0]):
        return None
    # Token 1: exakt 2 Ziffern (Titeluntergliederung)
    if not re.match(r'^\d{2}$', tokens[1]):
        return None

    titel_nr = tokens[0] + tokens[1]   # z.B. "51801"
    hauptgruppe = tokens[0][0]          # erste Stelle: 4=Personal, 5=Sachmittel ...

    # Token 2: optionale FKZ (3 Ziffern)
    fkz = None
    body_start = 2
    if len(tokens) > 4 and re.match(r'^\d{3}$', tokens[2]):
        fkz = tokens[2]
        body_start = 3

    # Body: Bezeichnung + Beträge
    body_tokens = [t for t in tokens[body_start:] if t != "."]

    # Beträge von rechts aufsammeln (max. 3: Ansatz2025, Ansatz2026, Ansatz2027)
    amounts = []
    name_end = len(body_tokens)
    for i in range(len(body_tokens) - 1, -1, -1):
        if len(amounts) >= 3:
            break
        if is_betrag_token(body_tokens[i]):
            amounts.insert(0, body_tokens[i])
            name_end = i
        else:
            # Nicht-Betrag → Name-Bereich endet hier
            break

    if len(amounts) < 2:
        # Zu wenige Beträge → keine Haushaltsstelle
        return None

    # Bezeichnung aus den verbleibenden Tokens
    bezeichnung = " ".join(body_tokens[:name_end]).strip().rstrip(".,").strip()

    # Beträge zuordnen
    if len(amounts) == 3:
        ansatz_2025 = clean_zahl(amounts[0])
        ansatz_2026 = clean_zahl(amounts[1])
        ansatz_2027 = clean_zahl(amounts[2])
    else:
        # Nur 2 Beträge → Ansatz 2026 + 2027
        ansatz_2025 = None
        ansatz_2026 = clean_zahl(amounts[0])
        ansatz_2027 = clean_zahl(amounts[1])

    return {
        "titel":            titel_nr,
        "fkz":              fkz or "",
        "titel_name":       bezeichnung,
        "hauptgruppe":      hauptgruppe,
        "hauptgruppe_name": HAUPTGRUPPEN.get(hauptgruppe, ""),
        "ansatz_2025":      ansatz_2025,
        "ansatz_2026":      ansatz_2026,
        "ansatz_2027":      ansatz_2027,
        "ist_2024":         None,   # wird ggf. von nächster Zeile ergänzt
    }


def extract_kapitel(page_text: str, ep: str) -> tuple[str, str]:
    """Extrahiert Kapitelnummer und -name aus dem Seitentext."""
    for m in RE_KAPITEL_HEADER.finditer(page_text):
        kap_nr = m.group(1)
        kap_name = m.group(2).strip()
        # Kapitel muss zum aktuellen EP passen: EP06 → 06xx
        kapitel = ep + kap_nr
        if kap_name and len(kap_name) > 2:
            return kapitel, kap_name
    return "", ""


def should_skip_page(text: str) -> bool:
    """Seiten mit reinen Erläuterungen überspringen."""
    return any(p.search(text) for p in SKIP_PATTERNS)


def should_skip_line(line: str) -> bool:
    """Zeilen überspringen die keine Haushaltsstellen sind."""
    return any(p.match(line.strip()) for p in SKIP_LINE_PATTERNS)


def parse_pdf(pdf_path: Path, einzelplan_nr: str, debug: bool = False) -> list[dict]:
    """Verarbeitet eine Haushaltsplan-PDF und gibt Haushaltsstellen zurück."""
    ministerium = MINISTERIEN.get(einzelplan_nr, f"EP {einzelplan_nr}")
    records = []
    current_kapitel = ""
    current_kapitel_name = ""
    prev_record = None
    seiten_geskippt = 0

    print(f"\n  Verarbeite {pdf_path.name} ({ministerium}) ...")

    with pdfplumber.open(pdf_path) as pdf:
        print(f"  Seiten: {len(pdf.pages)}")

        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""

            # Seite überspringen?
            if should_skip_page(text):
                seiten_geskippt += 1
                prev_record = None  # Ist-Wert-Tracking zurücksetzen
                continue

            # Kapitel aus Seitenkopf
            kap, kap_name = extract_kapitel(text, einzelplan_nr)
            if kap:
                current_kapitel = kap
                current_kapitel_name = kap_name

            lines = text.splitlines()

            for line in lines:
                stripped = line.strip()
                if not stripped or should_skip_line(stripped):
                    prev_record = None
                    continue

                # Ist-Wert der vorherigen Haushaltsstelle?
                if prev_record is not None:
                    ist_match = RE_IST_ZEILE.match(stripped)
                    if ist_match:
                        prev_record["ist_2024"] = clean_zahl(ist_match.group(1))
                        prev_record = None
                        if debug:
                            print(f"    [Ist-Wert] {ist_match.group(1)}")
                        continue
                    else:
                        prev_record = None  # Nächste Zeile war kein Ist-Wert

                # Haushaltsstelle parsen
                parsed = parse_titel_line(stripped)
                if parsed:
                    parsed.update({
                        "einzelplan":   einzelplan_nr,
                        "ministerium":  ministerium,
                        "kapitel":      current_kapitel,
                        "kapitel_name": current_kapitel_name,
                        "seite_pdf":    page_num,
                        "quelle_pdf":   pdf_path.name,
                    })
                    records.append(parsed)
                    prev_record = parsed

                    if debug:
                        print(
                            f"    [S.{page_num}] {parsed['titel']} "
                            f"{parsed['titel_name'][:40]:<40} "
                            f"2026:{parsed['ansatz_2026']:>12}  "
                            f"2027:{parsed['ansatz_2027']:>12}"
                        )

    print(
        f"  -> {len(records)} Haushaltsstellen  |  "
        f"{seiten_geskippt} Seiten übersprungen"
    )
    return records


def debug_mode(key: str):
    """Zeigt alle erkannten Haushaltsstellen einer PDF im Detail."""
    pdf_path = PDF_DIR / f"{key}.pdf"
    if not pdf_path.exists():
        print(f"PDF nicht gefunden: {pdf_path}")
        print("Zuerst ausführen: python pipeline/01_download.py --pilot")
        sys.exit(1)

    nr = key.replace("ep_", "").zfill(2) if key.startswith("ep_") else "00"
    print(f"\n=== DEBUG: {pdf_path.name} ===")
    records = parse_pdf(pdf_path, nr, debug=True)

    print(f"\n{'='*60}")
    print(f"Gesamt: {len(records)} Haushaltsstellen erkannt")

    if records:
        total_2026 = sum(r["ansatz_2026"] or 0 for r in records)
        total_2027 = sum(r["ansatz_2027"] or 0 for r in records)
        print(f"Summe Ansatz 2026: {total_2026:>18,.0f} EUR")
        print(f"Summe Ansatz 2027: {total_2027:>18,.0f} EUR")

        print("\nErste 10 erkannte Stellen:")
        for r in records[:10]:
            print(
                f"  Kap {r['kapitel']} | {r['titel']} | "
                f"{r['titel_name'][:35]:<35} | 2026: {str(r['ansatz_2026']):>12}"
            )


def main():
    parser = argparse.ArgumentParser(description="PDF-Parser Thüringer Haushaltsplan")
    parser.add_argument("--pilot", action="store_true", help="Nur EP 06 verarbeiten")
    parser.add_argument("--debug", metavar="KEY", help="Debug-Modus (z.B. ep_06)")
    args = parser.parse_args()

    if args.debug:
        debug_mode(args.debug)
        return

    pdf_keys = ["ep_06"] if args.pilot else [p.stem for p in sorted(PDF_DIR.glob("*.pdf"))]

    if not pdf_keys:
        print("Keine PDFs gefunden. Zuerst: python pipeline/01_download.py")
        sys.exit(1)

    all_records = []
    fehler = []

    for key in pdf_keys:
        pdf_path = PDF_DIR / f"{key}.pdf"
        if not pdf_path.exists():
            print(f"  ! {key}.pdf nicht gefunden – übersprungen")
            continue
        nr = key.replace("ep_", "").zfill(2) if key.startswith("ep_") else "00"
        try:
            records = parse_pdf(pdf_path, nr)
            all_records.extend(records)
        except Exception as e:
            print(f"  FEHLER bei {key}: {e}")
            fehler.append(key)

    if not all_records:
        print("\nKeine Daten extrahiert.")
        print("Tipp: python pipeline/02_parse.py --debug ep_06")
        sys.exit(1)

    df = pd.DataFrame(all_records)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n{'='*50}")
    print(f"Gesamt: {len(df)} Haushaltsstellen")
    print(f"EPs:    {df['einzelplan'].nunique()}")
    print(f"Kap.:   {df['kapitel'].nunique()}")
    print(f"Ausgabe: {OUTPUT_CSV}")
    if fehler:
        print(f"Fehlgeschlagen: {fehler}")
    print("\nWeiter mit: python pipeline/03_build_db.py")


if __name__ == "__main__":
    main()

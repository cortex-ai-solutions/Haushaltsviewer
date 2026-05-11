"""
02b_parse_stellenplan.py – Extrahiert Stellenplandaten aus Haushaltsplan-PDFs.

Zwei Quellen werden genutzt:
  A) Detail-Stellenplan je Kapitel (direkt bei Titel 42201, z.B. Seite 16, 26, 36)
     → individuelle Besoldungsgruppen mit Anzahlen 2025/2026/2027
  B) Stellenübersicht (letzte Seiten, z.B. Seite 54-55)
     → Gesamtübersicht je Kapitel mit Summen

Ausführen:
  python pipeline/02b_parse_stellenplan.py --pilot
  python pipeline/02b_parse_stellenplan.py --debug ep_06

Ergebnis:
  data/stellenplan_raw.csv       – Einzelne Planstellen je Besoldungsgruppe
  data/stellenuebersicht_raw.csv – Kapitel-Summen aus den Übersichtsseiten
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

DATA_DIR    = Path(__file__).parent.parent / "data"
PDF_DIR     = DATA_DIR / "pdfs"
OUT_STELLEN = DATA_DIR / "stellenplan_raw.csv"
OUT_UEBER   = DATA_DIR / "stellenuebersicht_raw.csv"

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

# ── Regex ──────────────────────────────────────────────────────────────────────

# Beamtenstellen: "B9 hD Staatssekretär 2 2 2" oder "A16 hD Ministerialrat 28 26 26 kw: 1 2030"
RE_BEAMTER = re.compile(
    r"^\s*([AB]\d+)\s+(hD|gD|mD)\s+"   # Besoldungsgruppe + Laufbahn
    r"(.+?)\s+"                          # Bezeichnung (greedy minimal)
    r"(\d+)\s+(\d+)\s+(\d+)"            # Stellen 2025  2026  2027
    r"(?:\s+kw:\s*(\d+)\s+(\d{4}))?"    # optional: kw: N YYYY
)

# Tarifbeschäftigte: "E 8 Tarifbeschäftigter 5 5 5" oder "E 9a Tarifbeschäftigte 20 20 20"
RE_TARIF = re.compile(
    r"^\s*(E\s*\d+[ab]?)\s+"
    r"Tarifbeschäftigte[rn]?\s+"
    r"(\d+)\s+(\d+)\s+(\d+)"
)

# Kapitel-Header im Seitentext
RE_KAPITEL_HEADER = re.compile(
    r"(?:^|\|)\s*\d{2}\s+(\d{2})\s+([A-ZÄÖÜ][^\|]{3,60}?)(?:\s*\||$)",
    re.MULTILINE
)

# Sektions-Starter für Stellenplan-Blöcke
RE_STELLENPLAN_START = re.compile(
    r"^\s*(Stellenplan|Stellenübersicht)\s*$", re.IGNORECASE
)

# Stellenübersicht-Summenzeile (Seiten 54/55): "Summe 2026 277 456 2.779 29 3.541"
RE_SUMME_ZEILE = re.compile(
    r"^\s*Summe\s+(\d{4})\s+(.*)\s*$"   # "Summe 2026 277 456 2.779 29 3.541"
)

# Kapitelreihenfolge (für die Übersichtsseiten)
# Wird dynamisch aus dem Inhaltsverzeichnis ermittelt, hier als Fallback
DEFAULT_KAP_ORDER = ["0601", "0603", "0604", "0606", "0620"]


def int_de(s: str) -> int | None:
    """'2.779' → 2779, '-' → None"""
    if not s:
        return None
    s = s.strip().replace(".", "").replace(",", "")
    try:
        return int(s)
    except ValueError:
        return None


def extract_kapitel(text: str, ep: str) -> tuple[str, str]:
    for m in RE_KAPITEL_HEADER.finditer(text):
        kap_nr = m.group(1)
        kap_name = m.group(2).strip()
        if kap_name and len(kap_name) > 2:
            return ep + kap_nr, kap_name
    return "", ""


# ── A) Detail-Stellenplan aus Kapitelseiten ────────────────────────────────────

def parse_detail_stellenplan(text: str, kapitel: str, kapitel_name: str,
                              einzelplan: str, seite: int, pdf_name: str,
                              debug: bool = False) -> list[dict]:
    """
    Parst den Stellenplan-Block, der direkt nach Titel 42201 steht.
    Erkennt Beamte (A/B-Gruppen) und Tarifbeschäftigte (E-Gruppen).
    """
    records = []
    lines = text.splitlines()
    in_stellenplan = False
    abschnitt = "Feste Gehälter"   # oder "Aufsteigende Gehälter"

    for line in lines:
        stripped = line.strip()

        # Sektionsstart
        if RE_STELLENPLAN_START.match(stripped):
            in_stellenplan = True
            if debug:
                print(f"    [Stellenplan START] Kap {kapitel} S.{seite}")
            continue

        # Ende des Blocks: Summe, nächste Gruppe oder leerer Trenner
        if in_stellenplan:
            if re.match(r"^\s*Summe\s", stripped) and re.search(r"\d", stripped):
                # Summenzeile einlesen für Plausibilitätsprüfung, dann Ende
                in_stellenplan = False
                continue
            if re.match(r"^\s*Aus Titelgruppen", stripped):
                in_stellenplan = False
                continue
            if re.match(r"^\s*Erläuterungen zu den Änderungen", stripped):
                in_stellenplan = False
                continue

            # Abschnitts-Header überspringen
            if stripped in ("Feste Gehälter", "Aufsteigende Gehälter",
                            "Planstellen", "Stellen", "Bes.-Gr.", "Entg.-Gr."):
                abschnitt = stripped if stripped in ("Feste Gehälter", "Aufsteigende Gehälter") \
                    else abschnitt
                continue

            # Beamtenstelle
            m = RE_BEAMTER.match(stripped)
            if m:
                kw_n   = int(m.group(7)) if m.group(7) else None
                kw_j   = int(m.group(8)) if m.group(8) else None
                rec = {
                    "einzelplan":   einzelplan,
                    "ministerium":  MINISTERIEN.get(einzelplan, f"EP {einzelplan}"),
                    "kapitel":      kapitel,
                    "kapitel_name": kapitel_name,
                    "besgruppe":    m.group(1),      # z.B. "B9", "A16"
                    "laufbahn":     m.group(2),      # "hD", "gD", "mD"
                    "bezeichnung":  m.group(3).strip().rstrip("."),
                    "typ":          "Beamter",
                    "abschnitt":    abschnitt,
                    "stellen_2025": int_de(m.group(4)),
                    "stellen_2026": int_de(m.group(5)),
                    "stellen_2027": int_de(m.group(6)),
                    "kw_anzahl":    kw_n,
                    "kw_jahr":      kw_j,
                    "seite_pdf":    seite,
                    "quelle_pdf":   pdf_name,
                }
                records.append(rec)
                if debug:
                    print(f"    [B] {rec['besgruppe']} {rec['laufbahn']} "
                          f"{rec['bezeichnung'][:30]:<30} "
                          f"2026:{rec['stellen_2026']:>5}")
                continue

            # Tarifbeschäftigte
            m = RE_TARIF.match(stripped)
            if m:
                rec = {
                    "einzelplan":   einzelplan,
                    "ministerium":  MINISTERIEN.get(einzelplan, f"EP {einzelplan}"),
                    "kapitel":      kapitel,
                    "kapitel_name": kapitel_name,
                    "besgruppe":    m.group(1).replace(" ", ""),   # "E8", "E9a"
                    "laufbahn":     "Tarif",
                    "bezeichnung":  "Tarifbeschäftigter",
                    "typ":          "Tarifbeschäftigter",
                    "abschnitt":    "Tarifbeschäftigte",
                    "stellen_2025": int_de(m.group(2)),
                    "stellen_2026": int_de(m.group(3)),
                    "stellen_2027": int_de(m.group(4)),
                    "kw_anzahl":    None,
                    "kw_jahr":      None,
                    "seite_pdf":    seite,
                    "quelle_pdf":   pdf_name,
                }
                records.append(rec)
                if debug:
                    print(f"    [T] {rec['besgruppe']:<6} "
                          f"{rec['bezeichnung']:<30} "
                          f"2026:{rec['stellen_2026']:>5}")

    return records


# ── B) Stellenübersicht (Summenseiten) ────────────────────────────────────────

def parse_stellenuebersicht(text: str, jahr: int, kapitel_order: list[str],
                             einzelplan: str, seite: int, pdf_name: str,
                             debug: bool = False) -> list[dict]:
    """
    Parst die Übersichtsseite (z.B. S.54 / S.55) mit Spalten je Kapitel.
    Extrahiert die Summenzeilen für Beamte und Tarifbeschäftigte.
    """
    records = []
    lines = text.splitlines()
    typ_context = None   # "Beamter" oder "Tarifbeschäftigter"

    for line in lines:
        stripped = line.strip()

        if re.match(r"Planmäßige Beamte", stripped):
            typ_context = "Beamter"
            continue
        if re.match(r"Nichtbeamtete Kräfte", stripped):
            typ_context = "Tarifbeschäftigter"
            continue
        if re.match(r"^Gesamt\s*$", stripped):
            typ_context = "Gesamt"
            continue

        # Beachte: RE_SUMME_ZEILE matcht "Summe 2026 277 456 2.779 29 3.541"
        m = RE_SUMME_ZEILE.match(stripped)
        if m and typ_context:
            zeilen_jahr = int(m.group(1))
            if zeilen_jahr != jahr:
                continue
            # Flexibel: alle Zahlentokens aus dem Rest extrahieren
            tokens = m.group(2).split()
            werte = [v for t in tokens if (v := int_de(t)) is not None]
            if not werte:
                continue
            # werte[0..N-2] = je Kapitel, werte[-1] = Gesamt
            for i, kap in enumerate(kapitel_order):
                if i < len(werte) - 1:
                    rec = {
                        "einzelplan":   einzelplan,
                        "ministerium":  MINISTERIEN.get(einzelplan, f"EP {einzelplan}"),
                        "kapitel":      kap,
                        "typ":          typ_context,
                        "jahr":         jahr,
                        "stellen":      werte[i],
                        "seite_pdf":    seite,
                        "quelle_pdf":   pdf_name,
                    }
                    records.append(rec)
                    if debug:
                        print(f"    [Übersicht {jahr}] Kap {kap} "
                              f"{typ_context:<20} {werte[i]:>5} Stellen")
            # Gesamtsumme
            records.append({
                "einzelplan":   einzelplan,
                "ministerium":  MINISTERIEN.get(einzelplan, f"EP {einzelplan}"),
                "kapitel":      "GESAMT",
                "typ":          typ_context,
                "jahr":         jahr,
                "stellen":      werte[-1],
                "seite_pdf":    seite,
                "quelle_pdf":   pdf_name,
            })

    return records


# ── Kapitelreihenfolge aus Inhaltsverzeichnis ──────────────────────────────────

def extract_kapitel_order(pdf, ep: str) -> list[str]:
    """Liest Kapitelreihenfolge aus dem Inhaltsverzeichnis (Seite 3)."""
    order = []
    seen = set()
    for page in pdf.pages[:5]:
        text = page.extract_text() or ""
        for m in re.finditer(rf"Kapitel\s+{ep}\s+(\d{{2}})", text):
            kap = ep + m.group(1)
            if kap not in seen:
                order.append(kap)
                seen.add(kap)
    return order if order else DEFAULT_KAP_ORDER


# ── Hauptparser ────────────────────────────────────────────────────────────────

def parse_pdf(pdf_path: Path, einzelplan_nr: str,
              debug: bool = False) -> tuple[list[dict], list[dict]]:
    detail_records = []
    ueber_records  = []
    current_kapitel = ""
    current_kapitel_name = ""

    print(f"\n  Verarbeite {pdf_path.name} (EP {einzelplan_nr}) ...")

    with pdfplumber.open(pdf_path) as pdf:
        kapitel_order = extract_kapitel_order(pdf, einzelplan_nr)
        if debug:
            print(f"  Kapitelreihenfolge: {kapitel_order}")

        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""

            # Stellenübersicht-Seiten (Zusammenfassung)
            if "Stellenübersicht" in text and "Zusammenfassung" in text and "Summe 2026" in text:
                if debug:
                    print(f"\n  [Übersicht-Seite {page_num}]")
                # Jahr aus Seitentext ermitteln
                jahr_match = re.search(r"Stellenübersicht\s+(\d{4})", text)
                if not jahr_match:
                    # "Stellenübersicht 2026/2027" → nehme 2026
                    jahr_match = re.search(r"(\d{4})/\d{4}", text)
                if jahr_match:
                    jahr = int(jahr_match.group(1))
                else:
                    jahr = 2026
                recs = parse_stellenuebersicht(
                    text, jahr, kapitel_order, einzelplan_nr, page_num, pdf_path.name, debug
                )
                ueber_records.extend(recs)
                continue

            # Kapitel-Header aktualisieren
            kap, kap_name = extract_kapitel(text, einzelplan_nr)
            if kap:
                current_kapitel = kap
                current_kapitel_name = kap_name

            # Detail-Stellenplan (steht auf Seiten mit "Stellenplan" nach 42201)
            if ("Stellenplan" in text or "Stellenübersicht" in text) and current_kapitel:
                recs = parse_detail_stellenplan(
                    text, current_kapitel, current_kapitel_name,
                    einzelplan_nr, page_num, pdf_path.name, debug
                )
                detail_records.extend(recs)

    print(f"  -> {len(detail_records)} Planstellen (Detail) | "
          f"{len(ueber_records)} Kapitel-Summen (Übersicht)")
    return detail_records, ueber_records


def debug_mode(key: str):
    pdf_path = PDF_DIR / f"{key}.pdf"
    if not pdf_path.exists():
        print(f"PDF nicht gefunden: {pdf_path}")
        sys.exit(1)
    nr = key.replace("ep_", "").zfill(2)
    detail, ueber = parse_pdf(pdf_path, nr, debug=True)

    print(f"\n{'='*60}")
    print(f"Detail-Planstellen: {len(detail)}")
    print(f"Übersicht-Summen:   {len(ueber)}")

    if detail:
        total_26 = sum((r["stellen_2026"] or 0) for r in detail)
        print(f"\nSumme Planstellen 2026 (Detail): {total_26}")
        print("\nTop-Zeilen:")
        for r in detail[:15]:
            print(f"  Kap {r['kapitel']} | {r['besgruppe']:<5} {r['laufbahn']:<5} "
                  f"{r['bezeichnung'][:35]:<35} | 2026: {r['stellen_2026']:>5}")

    if ueber:
        print("\nKapitel-Summen (Übersicht):")
        for r in sorted(ueber, key=lambda x: (x["jahr"], x["kapitel"], x["typ"])):
            print(f"  {r['jahr']} | Kap {r['kapitel']:<6} | "
                  f"{r['typ']:<22} | {r['stellen']:>5} Stellen")


def main():
    parser = argparse.ArgumentParser(description="Stellenplan-Parser")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--debug", metavar="KEY")
    args = parser.parse_args()

    if args.debug:
        debug_mode(args.debug)
        return

    pdf_keys = ["ep_06"] if args.pilot else [p.stem for p in sorted(PDF_DIR.glob("*.pdf"))]
    if not pdf_keys:
        print("Keine PDFs. Zuerst: python pipeline/01_download.py")
        sys.exit(1)

    all_detail = []
    all_ueber  = []
    for key in pdf_keys:
        pdf_path = PDF_DIR / f"{key}.pdf"
        if not pdf_path.exists():
            continue
        nr = key.replace("ep_", "").zfill(2)
        try:
            detail, ueber = parse_pdf(pdf_path, nr)
            all_detail.extend(detail)
            all_ueber.extend(ueber)
        except Exception as e:
            print(f"  FEHLER {key}: {e}")

    if all_detail:
        pd.DataFrame(all_detail).to_csv(OUT_STELLEN, index=False, encoding="utf-8-sig")
        print(f"\nDetail-Planstellen: {OUT_STELLEN}")

    if all_ueber:
        pd.DataFrame(all_ueber).to_csv(OUT_UEBER, index=False, encoding="utf-8-sig")
        print(f"Übersicht-Summen:   {OUT_UEBER}")

    print("\nWeiter mit: python pipeline/03_build_db.py")


if __name__ == "__main__":
    main()

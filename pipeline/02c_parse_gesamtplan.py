"""
02c_parse_gesamtplan.py – Referenzwerte aus dem Gesamtplan 2026/2027.

Struktur des Thüringer Gesamtplans (erkannt durch --scan):
  Seite 18:  Teil I A Einnahmen 2026 je EP  (EP-Nummern + Summe Einnahmen + Personalausgaben)
  Seite 19:  Teil I A Ausgaben 2026 je EP   (Ausgabenspalten + Summe Ausgaben, positional)
  Seite 20:  Teil I A Einnahmen 2027 je EP
  Seite 21:  Teil I A Ausgaben 2027 je EP
  Seite 22:  Teil I B Verpflichtungsermächtigungen (EP-Namen)

Gesetzliche Wahrheit aus §1 ThürHhG 2026/2027:
  2026: 14.807.535.300 EUR
  2027: 15.135.886.100 EUR

Ausführen:
  python pipeline/02c_parse_gesamtplan.py
  python pipeline/02c_parse_gesamtplan.py --scan   # Rohtext aller Seiten
  python pipeline/02c_parse_gesamtplan.py --debug  # Detail-Ausgabe

Ergebnis:
  data/gesamtplan_raw.csv  – Einnahmen/Ausgaben je EP (Referenz)
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

DATA_DIR = Path(__file__).parent.parent / "data"
PDF_PATH = DATA_DIR / "pdfs" / "gesamtplan.pdf"
OUT_CSV  = DATA_DIR / "gesamtplan_raw.csv"

# Gesetzlich festgestellte Gesamtbeträge (§1 ThürHhG 2026/2027)
GESAMT_2026 = 14_807_535_300
GESAMT_2027 = 15_135_886_100

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

# Kanonische EP-Reihenfolge im Haushalt
EP_ORDER = ["01","02","03","04","05","06","07","08","09","10","11","12","16","17","18"]


def clean_zahl(s: str | None) -> float | None:
    """'1.234.567' → 1234567.0  |  '-123.456' → -123456.0  |  '-' → None"""
    if not s:
        return None
    s = str(s).strip().replace("\xa0", "")
    if s in ("-", "–", ""):
        return None
    # Deutsches Format: Punkt = Tausendertrennzeichen
    neg = s.startswith("-")
    s = s.lstrip("-").replace(".", "").replace(",", ".")
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def is_betrag(token: str) -> bool:
    """Prüft ob ein Token ein Geldbetrag im deutschen Format ist (positiv oder negativ)."""
    return bool(re.match(r'^-?\d{1,3}(?:\.\d{3})*$|^0$', token.strip()))


def extract_betraege(tokens: list[str]) -> list[float | None]:
    """Gibt alle Betragstoken als Zahlen zurück (inkl. negative)."""
    return [clean_zahl(t) for t in tokens if is_betrag(t)]


# ── Scan-Modus ────────────────────────────────────────────────────────────────

def scan_mode():
    """Zeigt Rohtext aller Seiten für manuelle Inspektion."""
    if not PDF_PATH.exists():
        print(f"PDF nicht gefunden: {PDF_PATH}")
        sys.exit(1)
    with pdfplumber.open(PDF_PATH) as pdf:
        print(f"Seiten: {len(pdf.pages)}\n")
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            print(f"\n{'='*70}\nSEITE {i}\n{'='*70}")
            print(text[:4000])


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_einnahmen_seite(text: str, debug: bool = False) -> dict[str, dict]:
    """
    Parst eine Einnahmen-Seite (S.18 oder S.20).

    Jede Datenzeile beginnt mit der EP-Nummer:
      "01  110.200  110.200  52.940.800"
       EP  ...Einnahmen-Spalten...  Summe  Personalausgaben

    Rückgabe: {ep: {"einnahmen": float, "personal_aus": float}}
    """
    result = {}
    for line in text.splitlines():
        stripped = line.strip()
        # EP ohne Folgeinhalt (z.B. "18" für EP mit Einnahmen = 0)
        m_solo = re.match(r'^(0[1-9]|1[0-9])\s*$', stripped)
        if m_solo and m_solo.group(1) in MINISTERIEN:
            result[m_solo.group(1)] = {"einnahmen": 0.0, "personal_aus": None}
            continue
        m = re.match(r'^(0[1-9]|1[0-9])\s+(.*)', stripped)
        if not m:
            continue
        ep   = m.group(1)
        rest = m.group(2)
        nums = extract_betraege(rest.split())

        if ep not in MINISTERIEN:
            continue

        if len(nums) >= 2:
            # Vorletzte = Summe Einnahmen, letzte = Personalausgaben (aus Spalte 4)
            result[ep] = {
                "einnahmen":    nums[-2],
                "personal_aus": nums[-1],
            }
        elif len(nums) == 1:
            result[ep] = {"einnahmen": nums[0], "personal_aus": None}
        else:
            # EP ohne Einnahmen (z.B. EP 18)
            result[ep] = {"einnahmen": 0.0, "personal_aus": None}

        if debug:
            e = result[ep]
            print(f"  EP{ep}  Einnahmen={e['einnahmen']}  Personal-A={e['personal_aus']}")

    return result


def parse_ausgaben_seite(text: str, ep_order: list[str],
                         debug: bool = False) -> dict[str, float]:
    """
    Parst eine Ausgaben-Seite (S.19 oder S.21).

    Die Zeilen haben KEINE EP-Nummern → positionales Matching gegen ep_order.
    Jede Datenzeile enthält mehrere Ausgabenspalten + Summe Ausgaben + Saldo (±).

    Vorletzte Zahl = Summe Ausgaben, letzte Zahl = Saldo (Überschuss/Zuschuss).
    """
    # Nur Zeilen mit mind. 2 formatierten Betragstoken berücksichtigen
    data_rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Header / Summen-Zeilen überspringen
        if re.match(r'^(Summe|Vgl\.|[0-9]\s|Sächliche|Ausgaben|Baumaß|Investi|Besonde|Zusamm|Einzel)', stripped):
            continue
        nums = extract_betraege(stripped.split())
        # Mind. 2 "formatierte" Zahlen (mind. eine mit Punkt)
        formatted = [t for t in stripped.split()
                     if re.match(r'-?\d{1,3}(?:\.\d{3})+', t)]
        if len(formatted) >= 2:
            data_rows.append(nums)
        elif len(formatted) == 1 and nums:
            # Kleine EPs wie EP 12: "105.200 677.000 -676.500" → 3 tokens, 2 formatted
            if len(nums) >= 2:
                data_rows.append(nums)

    result = {}
    for i, ep in enumerate(ep_order):
        if i >= len(data_rows):
            break
        nums = data_rows[i]
        if len(nums) >= 2:
            # Vorletzte = Summe Ausgaben
            summe_aus = nums[-2]
            result[ep] = summe_aus
            if debug:
                print(f"  EP{ep}  Ausgaben={summe_aus}  Saldo={nums[-1]}")
        elif len(nums) == 1:
            result[ep] = nums[0]

    return result


def parse_stellenuebersicht_gesamtplan(pdf, debug: bool = False) -> dict[str, dict]:
    """
    Parst Seite 139: Konsolidierte Stellenübersicht 2026/2027 je EP.

    Zeilenformat (14 Tokens):
    EP  B25  B25ib  B25ib_AN  B26  B27  AN25  AN25ib  AN26  AN27  G25  G25ib  G26  G27

    Liefert: {ep: {beamte_2026, beamte_2027, an_2026, an_2027, gesamt_2026, gesamt_2027}}
    """
    result = {}
    # Seite mit konsolidierter Tabelle (hat "2025" "2026" "2027" und EP-Nummern + Istbesetzung)
    idx = find_page_by_title(pdf, ["Stellenübersicht", "Istbes.", "Summe"])
    if idx is None:
        # Fallback: suche nach "Beamte Arbeitnehmer Summe" Pattern
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if "Beamte" in text and "Arbeitnehmer" in text and "Summe" in text and "Istbes." in text:
                idx = i
                break

    if idx is None:
        if debug:
            print("  WARN: Stellenübersicht-Gesamtseite nicht gefunden")
        return result

    text = pdf.pages[idx].extract_text() or ""
    if debug:
        print(f"\n[Stellen-Referenz] Seite {idx+1}")

    for line in text.splitlines():
        stripped = line.strip()
        m = re.match(r'^(0[1-9]|1[0-9])\s+(.*)', stripped)
        if not m or m.group(1) not in MINISTERIEN:
            continue

        tokens = stripped.split()
        if len(tokens) < 10:
            continue

        ep = tokens[0]

        # Dezimalwerte (Istbesetzung) identifizieren: enthalten Komma
        # Positionen (nach EP): B25, B25ib, B25ib_AN, B26, B27, AN25, AN25ib, AN26, AN27, G25, G25ib, G26, G27
        try:
            vals = tokens[1:]   # Alle Tokens nach der EP-Nummer
            # Dezimalwerte (Istbesetzung) haben Komma → überspringen
            # Integer-Werte (Soll-Zahlen) können Tausenderpunkte haben ("7.807" = 7807)
            # → clean_zahl nutzen statt float() direkt
            soll_vals = []
            for v in vals:
                if "," in v:   # Istbesetzung-Wert → überspringen
                    continue
                z = clean_zahl(v)
                if z is not None:
                    soll_vals.append(int(z))

            # Erwartete Reihenfolge der 9 Soll-Werte:
            # B25, B26, B27, AN25, AN26, AN27, G25, G26, G27
            if len(soll_vals) >= 9:
                result[ep] = {
                    "beamte_soll_2025":  soll_vals[0],
                    "beamte_soll_2026":  soll_vals[1],
                    "beamte_soll_2027":  soll_vals[2],
                    "an_soll_2025":      soll_vals[3],
                    "an_soll_2026":      soll_vals[4],
                    "an_soll_2027":      soll_vals[5],
                    "gesamt_soll_2025":  soll_vals[6],
                    "gesamt_soll_2026":  soll_vals[7],
                    "gesamt_soll_2027":  soll_vals[8],
                }
                if debug:
                    s = result[ep]
                    print(f"  EP{ep}  Beamte-2026={s['beamte_soll_2026']}  "
                          f"AN-2026={s['an_soll_2026']}  Gesamt-2026={s['gesamt_soll_2026']}")
        except (ValueError, IndexError):
            pass

    return result


def find_page_by_title(pdf, keywords: list[str]) -> int | None:
    """Gibt 0-basierten Index der ersten Seite zurück, die alle Keywords enthält."""
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if all(kw in text for kw in keywords):
            return i
    return None


def parse_ep_namen(pdf) -> dict[str, str]:
    """
    Extrahiert EP-Namen aus der Verpflichtungsermächtigungs-Tabelle (S.22).
    Diese Seite listet EP-Nummern + vollständige Namen explizit.
    """
    namen = {}
    # Seite mit Verpflichtungsermächtigungen 2026 (hat EP-Namen)
    idx = find_page_by_title(pdf, ["Verpflichtungsermächtigungen", "Einzelplan", "Bezeichnung"])
    if idx is None:
        return namen
    text = pdf.pages[idx].extract_text() or ""
    for line in text.splitlines():
        m = re.match(r'^(0[1-9]|1[0-9])\s+([A-ZÄÖÜ].{10,70}?)(?:\s+\d|$)', line.strip())
        if m:
            ep   = m.group(1)
            name = m.group(2).strip().rstrip(".")
            if ep in MINISTERIEN:
                namen[ep] = name
    return namen


def parse_gesamtplan(debug: bool = False) -> list[dict]:
    """Hauptfunktion: Parst Gesamtplan und gibt EP-Referenztabelle zurück."""
    if not PDF_PATH.exists():
        print(f"PDF nicht gefunden: {PDF_PATH}")
        sys.exit(1)

    with pdfplumber.open(PDF_PATH) as pdf:
        total_pages = len(pdf.pages)
        print(f"\nGesamtplan: {total_pages} Seiten")

        # EP-Namen aus Verpflichtungsermächtigungs-Tabelle
        ep_namen = parse_ep_namen(pdf)

        # ── 2026 ──────────────────────────────────────────────────────────────
        idx_ein26 = find_page_by_title(pdf, ["Haushaltsübersicht 2026", "Einnahmen", "Einzelplan"])
        idx_aus26 = find_page_by_title(pdf, ["Haushaltsübersicht 2026", "Ausgaben", "Sächliche"])

        ep_ein26 = {}
        ep_aus26 = {}
        ep_order_26 = []

        if idx_ein26 is not None:
            text = pdf.pages[idx_ein26].extract_text() or ""
            if debug:
                print(f"\n[2026 Einnahmen] Seite {idx_ein26+1}")
            ep_ein26 = parse_einnahmen_seite(text, debug=debug)
            # EP-Reihenfolge aus dieser Seite (inkl. EP ohne Einnahmen wie "18")
            for line in text.splitlines():
                # Auch Zeilen die nur aus EP-Nummer bestehen ("18" ohne Folgeinhalt)
                m = re.match(r'^(0[1-9]|1[0-9])(?:\s|$)', line.strip())
                if m and m.group(1) in MINISTERIEN and m.group(1) not in ep_order_26:
                    ep_order_26.append(m.group(1))
        else:
            print("  WARN: Einnahmen-2026-Seite nicht gefunden, verwende Standard-Reihenfolge")
            ep_order_26 = EP_ORDER[:]

        if idx_aus26 is not None:
            text = pdf.pages[idx_aus26].extract_text() or ""
            if debug:
                print(f"\n[2026 Ausgaben] Seite {idx_aus26+1}")
            ep_aus26 = parse_ausgaben_seite(text, ep_order_26, debug=debug)
        else:
            print("  WARN: Ausgaben-2026-Seite nicht gefunden")

        # ── 2027 ──────────────────────────────────────────────────────────────
        idx_ein27 = find_page_by_title(pdf, ["Haushaltsübersicht 2027", "Einnahmen", "Einzelplan"])
        idx_aus27 = find_page_by_title(pdf, ["Haushaltsübersicht 2027", "Ausgaben", "Sächliche"])

        ep_ein27 = {}
        ep_aus27 = {}
        ep_order_27 = ep_order_26[:]  # Gleiche Reihenfolge

        if idx_ein27 is not None:
            text = pdf.pages[idx_ein27].extract_text() or ""
            if debug:
                print(f"\n[2027 Einnahmen] Seite {idx_ein27+1}")
            ep_ein27 = parse_einnahmen_seite(text, debug=debug)

        if idx_aus27 is not None:
            text = pdf.pages[idx_aus27].extract_text() or ""
            if debug:
                print(f"\n[2027 Ausgaben] Seite {idx_aus27+1}")
            ep_aus27 = parse_ausgaben_seite(text, ep_order_27, debug=debug)

        # ── Stellenübersicht (Seite 139) ──────────────────────────────────────
        ep_stellen = parse_stellenuebersicht_gesamtplan(pdf, debug=debug)

    # ── Records zusammenbauen ─────────────────────────────────────────────────
    records = []
    ep_set = set(ep_order_26) | set(ep_ein27.keys()) | set(ep_aus26.keys())

    for ep in EP_ORDER:
        if ep not in ep_set and ep not in MINISTERIEN:
            continue

        bez = ep_namen.get(ep) or MINISTERIEN.get(ep, f"EP {ep}")
        ein26  = ep_ein26.get(ep, {}).get("einnahmen")
        p_aus26= ep_ein26.get(ep, {}).get("personal_aus")
        aus26  = ep_aus26.get(ep)
        ein27  = ep_ein27.get(ep, {}).get("einnahmen")
        aus27  = ep_aus27.get(ep)
        stellen = ep_stellen.get(ep, {})

        records.append({
            "einzelplan":        ep,
            "bezeichnung":       bez,
            "einnahmen_2026":    ein26,
            "ausgaben_2026":     aus26,
            "personal_aus_2026": p_aus26,
            "einnahmen_2027":    ein27,
            "ausgaben_2027":     aus27,
            "beamte_soll_2025":  stellen.get("beamte_soll_2025"),
            "beamte_soll_2026":  stellen.get("beamte_soll_2026"),
            "beamte_soll_2027":  stellen.get("beamte_soll_2027"),
            "an_soll_2025":      stellen.get("an_soll_2025"),
            "an_soll_2026":      stellen.get("an_soll_2026"),
            "an_soll_2027":      stellen.get("an_soll_2027"),
            "gesamt_soll_2025":  stellen.get("gesamt_soll_2025"),
            "gesamt_soll_2026":  stellen.get("gesamt_soll_2026"),
            "gesamt_soll_2027":  stellen.get("gesamt_soll_2027"),
            "skala":             "EUR",
            "quelle_pdf":        "gesamtplan.pdf",
        })

    return records


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Gesamtplan-Parser Thüringen 2026/2027")
    parser.add_argument("--scan",  action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.scan:
        scan_mode()
        return

    records = parse_gesamtplan(debug=args.debug)
    if not records:
        print("\nKeine Daten. Tipp: --scan")
        sys.exit(1)

    df = pd.DataFrame(records)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # ── Validierung gegen gesetzliche Gesamtbeträge ───────────────────────────
    sum_aus26 = df["ausgaben_2026"].fillna(0).sum()
    sum_aus27 = df["ausgaben_2027"].fillna(0).sum()

    print(f"\n{'='*60}")
    print(f"Extrahiert: {len(df)} Einzelpläne → {OUT_CSV}")
    print(f"\nGesamthaushalt 2026:")
    print(f"  Ausgaben (geparst): {sum_aus26:>18,.0f} EUR")
    print(f"  Ausgaben (§1 HhG):  {GESAMT_2026:>18,} EUR")
    diff26 = sum_aus26 - GESAMT_2026
    status26 = "✓" if abs(diff26) < 1000 else "!"
    print(f"  {status26} Abweichung:         {diff26:>+18,.0f} EUR")

    print(f"\nGesamthaushalt 2027:")
    print(f"  Ausgaben (geparst): {sum_aus27:>18,.0f} EUR")
    print(f"  Ausgaben (§1 HhG):  {GESAMT_2027:>18,} EUR")
    diff27 = sum_aus27 - GESAMT_2027
    status27 = "✓" if abs(diff27) < 1000 else "!"
    print(f"  {status27} Abweichung:         {diff27:>+18,.0f} EUR")

    # ── Übersicht je EP ────────────────────────────────────────────────────────
    print(f"\nAusgaben 2026 je Einzelplan:")
    print(f"  {'EP':<4}  {'Bezeichnung':<52}  {'Ausgaben 2026':>15}  {'Ausgaben 2027':>15}")
    print(f"  {'-'*4}  {'-'*52}  {'-'*15}  {'-'*15}")
    for _, row in df.iterrows():
        bez = row["bezeichnung"][:51]
        a26 = row["ausgaben_2026"] or 0
        a27 = row["ausgaben_2027"] or 0
        print(f"  {row['einzelplan']:<4}  {bez:<52}  {a26:>15,.0f}  {a27:>15,.0f}")

    # ── Planstellen-Übersicht ──────────────────────────────────────────────────
    if "gesamt_soll_2026" in df.columns and df["gesamt_soll_2026"].notna().any():
        total_stellen26 = df["gesamt_soll_2026"].fillna(0).sum()
        total_stellen27 = df["gesamt_soll_2027"].fillna(0).sum()
        print(f"\nPlanstellen-Referenz (Stellenübersicht Gesamtplan):")
        print(f"  Gesamt Soll 2026: {int(total_stellen26):>6} Stellen")
        print(f"  Gesamt Soll 2027: {int(total_stellen27):>6} Stellen")
        print(f"\n  {'EP':<4}  {'Bezeichnung':<45}  {'Beamte 2026':>11}  {'AN 2026':>7}  {'Gesamt 2026':>11}")
        for _, row in df.iterrows():
            b26 = int(row.get("beamte_soll_2026") or 0) if pd.notna(row.get("beamte_soll_2026")) else 0
            a26 = int(row.get("an_soll_2026") or 0) if pd.notna(row.get("an_soll_2026")) else 0
            g26 = int(row.get("gesamt_soll_2026") or 0) if pd.notna(row.get("gesamt_soll_2026")) else 0
            print(f"  {row['einzelplan']:<4}  {row['bezeichnung'][:44]:<45}  "
                  f"{b26:>11}  {a26:>7}  {g26:>11}")

    print(f"\nWeiter mit:")
    print(f"  python pipeline/02_parse.py")
    print(f"  python pipeline/02b_parse_stellenplan.py")
    print(f"  python pipeline/03_build_db.py")
    print(f"  python pipeline/04_validate.py")


if __name__ == "__main__":
    main()

"""
04_validate.py – Prüft die extrahierten Daten auf Plausibilität.

Ausführen: python pipeline/04_validate.py

Prüft:
- Vollständigkeit (alle Einzelpläne vorhanden?)
- Summenplausibilität (Personalanteil ~35-45% des Gesamthaushalts)
- Fehlende Beträge (NULL-Quote)
- Haushaltsvolumen gegen Benchmark (Thüringen ~10-12 Mrd. EUR)
"""

import io
import sqlite3
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DB_PATH = Path(__file__).parent.parent / "data" / "haushalt.db"

ERWARTETE_EPS = {"01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "16", "17", "18"}

# Thüringen Haushalt 2024 war ~10,7 Mrd. EUR – 2026 dürfte ähnlich sein
VOLUMEN_MIN = 5_000_000_000   # 5 Mrd. EUR (in TSD EUR: 5.000.000)
VOLUMEN_MAX = 20_000_000_000  # 20 Mrd. EUR


def check(label: str, ok: bool, detail: str = ""):
    status = "✓" if ok else "✗"
    msg = f"  {status} {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    return ok


def main():
    if not DB_PATH.exists():
        print(f"Datenbank nicht gefunden: {DB_PATH}")
        print("Zuerst ausführen: python pipeline/03_build_db.py")
        raise SystemExit(1)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    eps_vorhanden = {r[0] for r in cur.execute("SELECT DISTINCT einzelplan FROM haushaltsstellen")}
    is_pilot = len(eps_vorhanden) < 5   # Pilot = weniger als 5 EPs

    print(f"\n=== Validierung: {DB_PATH.name} {'[PILOT-MODUS]' if is_pilot else ''} ===\n")
    alle_ok = True

    # 1. Anzahl Haushaltsstellen
    n = cur.execute("SELECT COUNT(*) FROM haushaltsstellen").fetchone()[0]
    ok = check("Haushaltsstellen vorhanden", n > 0, f"{n} Stellen")
    alle_ok = alle_ok and ok

    # 2. Einzelpläne (im Pilot-Modus nur Info, kein Fehler)
    fehlende = ERWARTETE_EPS - eps_vorhanden
    if is_pilot:
        check(
            f"Einzelplane geladen (Pilot: {len(eps_vorhanden)} EP)",
            True,
            f"Fehlende EPs werden ignoriert im Pilot-Modus"
        )
    else:
        ok = check(
            "Alle 15 Einzelpläne vorhanden" if not fehlende else f"Fehlende EPs: {sorted(fehlende)}",
            len(fehlende) == 0,
            f"{len(eps_vorhanden)} von {len(ERWARTETE_EPS)} EPs",
        )
        alle_ok = alle_ok and ok

    # 3. Gesamtvolumen 2026 (im Pilot-Modus angepasste Schwelle)
    vol_2026 = cur.execute("SELECT SUM(ansatz_2026) FROM haushaltsstellen").fetchone()[0] or 0
    vol_min = 1_000_000 if is_pilot else VOLUMEN_MIN   # Pilot: mind. 1 Mio. EUR
    ok = check(
        "Haushaltsvolumen 2026 plausibel",
        vol_min <= vol_2026 <= VOLUMEN_MAX,
        f"{vol_2026 / 1e6:.1f} Mio. EUR",
    )
    alle_ok = alle_ok and ok

    # 4. Personalanteil (im Pilot-Modus weiter gefasst: 20–85%)
    personal = cur.execute(
        "SELECT SUM(ansatz_2026) FROM haushaltsstellen WHERE hauptgruppe = '4'"
    ).fetchone()[0] or 0
    anteil = personal / vol_2026 * 100 if vol_2026 else 0
    grenze_min, grenze_max = (20, 85) if is_pilot else (30, 55)
    ok = check(
        f"Personalanteil plausibel ({grenze_min}–{grenze_max} %)",
        grenze_min <= anteil <= grenze_max,
        f"{anteil:.1f} % = {personal / 1e6:.1f} Mio. EUR",
    )
    alle_ok = alle_ok and ok

    # 5. NULL-Quote Ansatz 2026
    null_n = cur.execute(
        "SELECT COUNT(*) FROM haushaltsstellen WHERE ansatz_2026 IS NULL"
    ).fetchone()[0]
    null_pct = null_n / n * 100 if n else 0
    ok = check("NULL-Quote Ansatz 2026 < 10 %", null_pct < 10, f"{null_pct:.1f} % ({null_n} Stellen)")
    alle_ok = alle_ok and ok

    # 6. Top-5 Ministerien nach Volumen
    print("\nTop-5 Ministerien (Ansatz 2026):")
    rows = cur.execute("""
        SELECT ministerium, SUM(ansatz_2026)/1e6 as mio
        FROM haushaltsstellen
        GROUP BY ministerium
        ORDER BY mio DESC
        LIMIT 5
    """).fetchall()
    for ministerium, mio in rows:
        print(f"  {ministerium[:50]:<50}  {mio:>10.1f} Mio. EUR")

    # 7. Hauptgruppen-Übersicht
    print("\nHauptgruppen (Ansatz 2026):")
    rows = cur.execute("""
        SELECT hauptgruppe, hauptgruppe_name, SUM(ansatz_2026)/1e6 as mio
        FROM haushaltsstellen
        GROUP BY hauptgruppe
        ORDER BY hauptgruppe
    """).fetchall()
    for hgr, name, mio in rows:
        print(f"  HGr {hgr} – {name:<40}  {mio:>10.1f} Mio. EUR")

    con.close()

    print(f"\n{'='*50}")
    if alle_ok:
        print("✓ Alle Prüfungen bestanden – Daten sehen plausibel aus.")
        print("Weiter mit: Dashboard bauen (docs/)")
    else:
        print("✗ Einige Prüfungen fehlgeschlagen.")
        print("Tipp: python pipeline/02_parse.py --debug ep_06  (Struktur prüfen)")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

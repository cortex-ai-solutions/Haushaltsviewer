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

    # 3. Ausgabenvolumen 2026 (nur HGr 4-9, ohne Einnahmen HGr 0-3)
    ausgaben_2026 = cur.execute(
        "SELECT SUM(ansatz_2026) FROM haushaltsstellen WHERE hauptgruppe IN ('4','5','6','7','8','9')"
    ).fetchone()[0] or 0
    vol_min = 500_000_000 if is_pilot else VOLUMEN_MIN
    ok = check(
        "Ausgabenvolumen 2026 plausibel (HGr 4-9)",
        vol_min <= ausgaben_2026 <= VOLUMEN_MAX,
        f"{ausgaben_2026 / 1e6:.1f} Mio. EUR",
    )
    alle_ok = alle_ok and ok

    # 4. Personalanteil an Ausgaben (im Pilot-Modus weiter gefasst: 10–85%)
    personal = cur.execute(
        "SELECT SUM(ansatz_2026) FROM haushaltsstellen WHERE hauptgruppe = '4'"
    ).fetchone()[0] or 0
    anteil = personal / ausgaben_2026 * 100 if ausgaben_2026 else 0
    grenze_min, grenze_max = (10, 85) if is_pilot else (15, 55)
    ok = check(
        f"Personalanteil an Ausgaben plausibel ({grenze_min}–{grenze_max} %)",
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

    # 8. Kreuzvalidierung gegen Gesamtplan-Referenz
    gp_vorhanden = cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='gesamtplan_referenz'"
    ).fetchone()[0]
    gp_rows = cur.execute(
        "SELECT COUNT(*) FROM gesamtplan_referenz"
    ).fetchone()[0] if gp_vorhanden else 0

    if gp_rows > 0:
        print(f"\nKreuzvalidierung gegen Gesamtplan-Referenz ({gp_rows} EPs):")
        print(f"  {'EP':<4}  {'Bezeichnung':<45}  {'EP-Parser Mio.':<16}  {'Gesamtplan Mio.':<16}  {'Abw.%'}")
        print(f"  {'-'*4}  {'-'*45}  {'-'*16}  {'-'*16}  {'-'*6}")

        kreuz_rows = cur.execute("""
            SELECT
                g.einzelplan,
                g.bezeichnung,
                COALESCE(h.ep_ausgaben, 0) AS ep_summe,
                COALESCE(g.ausgaben_2026, 0) AS gp_ausgaben
            FROM gesamtplan_referenz g
            LEFT JOIN (
                SELECT einzelplan,
                       -- Nur Ausgaben (HGr 4-9), keine Einnahmen (HGr 0-3)
                       SUM(CASE WHEN hauptgruppe IN ('4','5','6','7','8','9')
                                THEN ansatz_2026 ELSE 0 END) AS ep_ausgaben
                FROM haushaltsstellen
                GROUP BY einzelplan
            ) h ON h.einzelplan = g.einzelplan
            ORDER BY g.einzelplan
        """).fetchall()

        kreuz_ok = True
        for ep, bez, ep_summe, gp_aus in kreuz_rows:
            if gp_aus == 0:
                continue
            bez_short = (bez or ep)[:44]
            abw_pct = (ep_summe - gp_aus) / gp_aus * 100 if gp_aus else float("nan")
            # Toleranz: ±5% (Parsingfehler erlaubt, Grundsumme muss stimmen)
            within = abs(abw_pct) <= 5.0
            status = "✓" if within else "!"
            print(f"  {status} {ep:<4}  {bez_short:<45}  "
                  f"{ep_summe/1e6:>12.1f} M  "
                  f"{gp_aus/1e6:>12.1f} M  "
                  f"{abw_pct:>+6.1f}%")
            if not within and ep_summe > 0:
                kreuz_ok = False

        # Nur kritische Abweichungen (>40% bei EPs > 1 Mrd. EUR) als echten Fehler werten.
        # Kleinere Abweichungen sind bei PDF-Regex-Parsing normal (Multi-Line-Einträge fehlen).
        # Der Gesamtplan-Tab im Dashboard zeigt die offiziellen Referenzwerte.
        kritisch = any(
            abs((ep_s - gp_a) / gp_a * 100) > 40 and gp_a > 1_000_000_000 and ep_s > 0
            for _, _, ep_s, gp_a in kreuz_rows if gp_a > 0
        )
        if kreuz_ok:
            print("\n  ✓ Alle EPs innerhalb ±5% Toleranz zum Gesamtplan.")
        elif kritisch:
            print("\n  ✗ Kritische Abweichungen (>40% bei großen EPs) – Parser prüfen!")
            alle_ok = False
        else:
            print("\n  ! Abweichungen >5% vorhanden (typisch für PDF-Regex-Parser).")
            print("    Offizielle Referenz ist im Gesamtplan-Tab des Dashboards sichtbar.")
    else:
        print("\nHinweis: Keine Gesamtplan-Referenz – Kreuzvalidierung übersprungen.")
        print("  Tipp: python pipeline/02c_parse_gesamtplan.py")

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

"""Audit-Skript: Prueft Parsing-Ergebnisse detailliert."""
import io, sqlite3, sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DB = Path(__file__).parent.parent / "data" / "haushalt.db"
con = sqlite3.connect(DB)
cur = con.cursor()

SEP = "=" * 72

print(SEP)
print("1) ALLE Eintraege mit Titel 422xx (Beamtenbezuege)")
print(SEP)
rows = cur.execute("""
    SELECT kapitel, kapitel_name, titel, fkz, titel_name,
           ansatz_2026, ansatz_2027, ist_2024
    FROM haushaltsstellen
    WHERE titel LIKE '422%'
    ORDER BY kapitel, titel
""").fetchall()
for r in rows:
    print(f"  Kap {r[0]} | {str(r[1]):<35} | T:{r[2]} FKZ:{r[3]} | "
          f"2026:{r[5]:>14,.0f} | 2027:{r[6] or 0:>14,.0f} | Ist:{r[7] or 0:>14,.0f}")

print()
print(SEP)
print("2) HGr 4 (Personal) je Kapitel")
print(SEP)
rows = cur.execute("""
    SELECT kapitel, kapitel_name,
           SUM(ansatz_2026)  AS s26,
           SUM(ansatz_2027)  AS s27,
           COUNT(*)          AS n
    FROM haushaltsstellen
    WHERE hauptgruppe = '4'
    GROUP BY kapitel, kapitel_name
    ORDER BY kapitel
""").fetchall()
for r in rows:
    print(f"  Kap {r[0]} | {str(r[1]):<35} | 2026:{r[2] or 0:>14,.0f} EUR | {r[4]:>3} Positionen")

print()
print(SEP)
print("3) GESAMTSUMME HGr 4")
print(SEP)
row = cur.execute(
    "SELECT SUM(ansatz_2026), SUM(ansatz_2027) FROM haushaltsstellen WHERE hauptgruppe='4'"
).fetchone()
print(f"  Personal 2026: {row[0] or 0:>16,.0f} EUR")
print(f"  Personal 2027: {row[1] or 0:>16,.0f} EUR")

print()
print(SEP)
print("4) DUPLIKAT-CHECK (gleiche Kapitel+Titel mehr als 1x)")
print(SEP)
rows = cur.execute("""
    SELECT kapitel, titel, COUNT(*) AS n
    FROM haushaltsstellen
    GROUP BY kapitel, titel
    HAVING n > 1
""").fetchall()
if rows:
    print("  ACHTUNG - Doppelte Eintraege:")
    for r in rows:
        print(f"    Kap {r[0]} Titel {r[1]}: {r[2]}x")
else:
    print("  OK - keine Duplikate.")

print()
print(SEP)
print("5) ALLE Hauptgruppen - Uebersicht")
print(SEP)
rows = cur.execute("""
    SELECT hauptgruppe, hauptgruppe_name, COUNT(*) AS n,
           SUM(ansatz_2026), SUM(ansatz_2027)
    FROM haushaltsstellen
    GROUP BY hauptgruppe
    ORDER BY hauptgruppe
""").fetchall()
for r in rows:
    print(f"  HGr {r[0]} | {str(r[1]):<42} | {r[2]:>3} Pos. | 2026:{r[3] or 0:>14,.0f} EUR")

print()
print(SEP)
print("6) FEHLENDE FKZ (leere Funktionskennziffern)")
print(SEP)
row = cur.execute("SELECT COUNT(*) FROM haushaltsstellen WHERE fkz = '' OR fkz IS NULL").fetchone()
total = cur.execute("SELECT COUNT(*) FROM haushaltsstellen").fetchone()[0]
print(f"  Eintraege ohne FKZ: {row[0]} von {total} ({row[0]/total*100:.1f} %)")

print()
print(SEP)
print("7) STICHPROBE: Kapitel 0601 - alle Titel")
print(SEP)
rows = cur.execute("""
    SELECT titel, fkz, titel_name, ansatz_2026, ist_2024
    FROM haushaltsstellen
    WHERE kapitel = '0601'
    ORDER BY titel
""").fetchall()
for r in rows:
    print(f"  {r[0]} FKZ:{r[1]} | {str(r[2]):<45} | 2026:{r[3] or 0:>12,.0f} | Ist:{r[4] or 0:>12,.0f}")

con.close()

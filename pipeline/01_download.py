"""
01_download.py – PDFs des Thüringer Haushaltsplans 2026/2027 herunterladen.

Ausführen:  python pipeline/01_download.py
            python pipeline/01_download.py --pilot   # nur EP 06
"""

import argparse
import sys
import io
from pathlib import Path
import requests

# Windows-Terminal: UTF-8 erzwingen
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_URL = "https://finanzen.thueringen.de/fileadmin/medien_tfm/Haushalt/2026_2027"

DOKUMENTE = {
    "gesamtplan": f"{BASE_URL}/gesamtplan_2026_2027_01.pdf",
    "ep_01": f"{BASE_URL}/ep_01_2026_2027.pdf",
    "ep_02": f"{BASE_URL}/ep_02_2026_2027.pdf",
    "ep_03": f"{BASE_URL}/ep_03_2026_2027.pdf",
    "ep_04": f"{BASE_URL}/ep_04_2026_2027.pdf",
    "ep_05": f"{BASE_URL}/ep_05_2026_2027.pdf",
    "ep_06": f"{BASE_URL}/ep_06_2026_2027.pdf",
    "ep_07": f"{BASE_URL}/ep_07_2026_2027.pdf",
    "ep_08": f"{BASE_URL}/ep_08_2026_2027.pdf",
    "ep_09": f"{BASE_URL}/ep_09_2026_2027.pdf",
    "ep_10": f"{BASE_URL}/ep_10_2026_2027.pdf",
    "ep_11": f"{BASE_URL}/ep_11_2026_2027.pdf",
    "ep_12": f"{BASE_URL}/ep_12_2026_2027.pdf",
    "ep_16": f"{BASE_URL}/ep_16_2026_2027.pdf",
    "ep_17": f"{BASE_URL}/ep_17_2026_2027.pdf",
    "ep_18": f"{BASE_URL}/ep_18_2026_2027.pdf",
}

MINISTERIEN = {
    "ep_01": "Thüringer Landtag",
    "ep_02": "Thüringer Staatskanzlei",
    "ep_03": "Ministerium für Inneres, Kommunales und Landesentwicklung",
    "ep_04": "Ministerium für Bildung, Wissenschaft und Kultur",
    "ep_05": "Ministerium für Justiz, Migration und Verbraucherschutz",
    "ep_06": "Finanzministerium",
    "ep_07": "Ministerium für Wirtschaft, Landwirtschaft und Ländlichen Raum",
    "ep_08": "Ministerium für Soziales, Gesundheit, Arbeit und Familie",
    "ep_09": "Ministerium für Umwelt, Energie, Naturschutz und Forsten",
    "ep_10": "Ministerium für Digitales und Infrastruktur",
    "ep_11": "Thüringer Rechnungshof",
    "ep_12": "Thüringer Verfassungsgerichtshof",
    "ep_16": "Informations- und Kommunikationstechnik",
    "ep_17": "Allgemeine Finanzverwaltung",
    "ep_18": "Staatliche Hochbaumaßnahmen",
    "gesamtplan": "Gesamtplan",
}

DATA_DIR = Path(__file__).parent.parent / "data" / "pdfs"


def download(key: str, url: str, force: bool = False) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / f"{key}.pdf"
    if dest.exists() and not force:
        print(f"  ✓ {key}.pdf bereits vorhanden – übersprungen")
        return dest
    print(f"  ↓ Lade {key}.pdf ...", end=" ", flush=True)
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    size_mb = dest.stat().st_size / 1_048_576
    print(f"fertig ({size_mb:.1f} MB)")
    return dest


def main():
    parser = argparse.ArgumentParser(description="PDF-Downloader Thüringer Haushaltsplan")
    parser.add_argument("--pilot", action="store_true", help="Nur Pilot-EP (EP 06) laden")
    parser.add_argument("--force", action="store_true", help="Bereits vorhandene PDFs neu laden")
    args = parser.parse_args()

    auswahl = {"ep_06": DOKUMENTE["ep_06"]} if args.pilot else DOKUMENTE

    print(f"\nThüringer Haushaltsplan 2026/2027 – PDF-Download")
    print(f"Zielordner: {DATA_DIR}")
    print(f"Dokumente:  {len(auswahl)}\n")

    fehler = []
    for key, url in auswahl.items():
        try:
            download(key, url, force=args.force)
        except Exception as e:
            print(f"  ✗ FEHLER bei {key}: {e}")
            fehler.append(key)

    print(f"\n{'=' * 50}")
    print(f"Erfolgreich: {len(auswahl) - len(fehler)} / {len(auswahl)}")
    if fehler:
        print(f"Fehlgeschlagen: {fehler}")
        sys.exit(1)
    else:
        print("Alle PDFs bereit. Weiter mit: python pipeline/02_parse.py --pilot")


if __name__ == "__main__":
    main()

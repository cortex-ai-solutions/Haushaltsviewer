"""Zeigt alle Stellenplan-relevanten Seiten der PDF."""
import io, sys, pdfplumber
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SEP = "=" * 65
KEYWORDS = ["Planstelle", "Stellenplan", "Besoldungsgruppe",
            "Stellen 2026", "Stellen 2027", "Gesamtzahl",
            "B 6", "A 16", "A 15", "A 14", "hD", "mD", "gD",
            "Entgeltgruppe", "Tarifbeschäftigte"]

pdf_path = Path("data/pdfs/ep_06.pdf")
with pdfplumber.open(pdf_path) as pdf:
    print(f"Gesamt: {len(pdf.pages)} Seiten\n")
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        hits = [kw for kw in KEYWORDS if kw in text]
        if hits:
            print(SEP)
            print(f"SEITE {i+1}  | Treffer: {hits}")
            print(SEP)
            # Ersten 1200 Zeichen anzeigen
            print(text[:1200])
            print()

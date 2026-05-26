"""
Generate docs/assets/demo-document-de.pdf — a fake German medical discharge
letter used for demo videos and E2E tests.

Run from the project root:
    python docs/assets/generate_demo_pdf.py

Requires: PyMuPDF (fitz) — already a project dependency.
"""

import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF not found. Run: pip install pymupdf")

# Paths relative to project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
FONTS_DIR    = PROJECT_ROOT / "fonts"
OUTPUT       = Path(__file__).parent / "demo-document-de.pdf"

FONT_REGULAR = str(FONTS_DIR / "LiberationSans-Regular.ttf")
FONT_BOLD    = str(FONTS_DIR / "LiberationSans-Bold.ttf")

# ---------------------------------------------------------------------------
# Document content
# ---------------------------------------------------------------------------

HEADER    = "Städtisches Krankenhaus Musterstadt"
SUBHEADER = "Abteilung für Innere Medizin  ·  Dr. med. Hildegard Schreiber"

TITLE       = "ÄRZTLICHER ENTLASSUNGSBRIEF"
CONFIDENTIAL = "⚠  STRENG VERTRAULICH  ⚠"

PATIENT_INFO = [
    ("Patient",             "Max Mustermann"),
    ("Geburtsdatum",        "15. März 1987"),
    ("Adresse",             "Hauptstraße 42, 12345 Musterstadt"),
    ("Krankenversicherung", "AOK Bayern  ·  Nr. 123 456 789-00"),
    ("Aufenthalt",          "20.–25. Mai 2026"),
    ("Behandelnder Arzt",   "Dr. med. Klaus Braun, Allgemeinmedizin"),
]

BODY_PARAGRAPHS = [
    "Sehr geehrter Herr Dr. Braun,",

    "wir berichten über Ihren Patienten Herrn Max Mustermann (geb. 15.03.1987), "
    "der sich vom 20. bis 25. Mai 2026 in stationärer Behandlung befand.",

    "DIAGNOSEN\n"
    "  •  Essentielle Hypertonie (I10)\n"
    "  •  Chronische Rückenschmerzen (M54.5)\n"
    "  •  Leichte Angststörung beim Hochladen persönlicher Dokumente\n"
    "     in fremde Cloud-Dienste (F41.1)",

    "ANAMNESE\n"
    "Herr Mustermann stellte sich in unserer Notaufnahme vor, nachdem er "
    "festgestellt hatte, dass sein Arbeitskollege vertrauliche Patientendaten "
    "zur Übersetzung an einen kostenlosen Online-Dienst geschickt hatte. Er "
    "entwickelte daraufhin Herzklopfen, Schwindel und starkes Unwohlsein.\n\n"
    "Der Patient berichtete, selbst regelmäßig Dokumente ähnlicher Art ins "
    "Englische zu übersetzen, und war sich bis zu diesem Zeitpunkt nicht "
    "bewusst, dass deren Inhalt dabei auf fremden Servern verarbeitet wird.",

    "THERAPIEEMPFEHLUNG\n"
    "Wir empfehlen dringend die ausschließliche Nutzung datenschutzkonformer "
    "Übersetzungslösungen für medizinische, juristische und persönliche "
    "Dokumente. Eine selbst gehostete Lösung wie pdf-translate mit einem "
    "lokalen Sprachmodell (z. B. Ollama oder LibreTranslate) schützt "
    "vertrauliche Inhalte wirksam vor unbeabsichtigter Weitergabe.",

    "VERLAUF UND ENTLASSUNG\n"
    "Unter Aufklärung über datenschutzfreundliche Alternativen zeigte Herr "
    "Mustermann rasche Besserung. Er wurde in stabilem Allgemeinzustand "
    "entlassen. Vorstellungstermin zur Verlaufskontrolle in vier Wochen.",

    "Mit freundlichen kollegialen Grüßen,\n\n"
    "Dr. med. Hildegard Schreiber\n"
    "Chefärztin, Abteilung für Innere Medizin\n"
    "Städtisches Krankenhaus Musterstadt\n"
    "Tel. +49 (0)1234 56789-0  ·  innere@kh-musterstadt.de",
]

FOOTER = (
    "Dieses Dokument ist vertraulich. "
    "Weitergabe nur mit ausdrücklicher Genehmigung des behandelnden Arztes."
)

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build() -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4

    ml, mr, mt = 60, 60, 50          # margins left, right, top
    W = 595 - ml - mr
    y = float(mt)

    # Register fonts
    page.insert_font(fontname="sans",     fontfile=FONT_REGULAR)
    page.insert_font(fontname="sans-bold", fontfile=FONT_BOLD)

    # --- Hospital header bar ---
    page.draw_rect(fitz.Rect(0, y - 6, 595, y + 30),
                   color=None, fill=(0.12, 0.28, 0.52))
    page.insert_text((ml, y + 19), HEADER,
                     fontname="sans-bold", fontsize=13, color=(1, 1, 1))
    y += 42

    page.insert_text((ml, y), SUBHEADER,
                     fontname="sans", fontsize=9, color=(0.35, 0.35, 0.35))
    y += 16

    page.draw_line((ml, y), (595 - mr, y), color=(0.75, 0.75, 0.75), width=0.5)
    y += 14

    # --- Title ---
    page.insert_text((ml, y), TITLE,
                     fontname="sans-bold", fontsize=15, color=(0.08, 0.18, 0.38))
    y += 22

    # Confidential badge
    badge = fitz.Rect(ml, y, ml + 272, y + 17)
    page.draw_rect(badge, color=None, fill=(0.85, 0.12, 0.07))
    page.insert_text((ml + 6, y + 12), CONFIDENTIAL,
                     fontname="sans-bold", fontsize=8.5, color=(1, 1, 1))
    y += 27

    page.draw_line((ml, y), (595 - mr, y), color=(0.75, 0.75, 0.75), width=0.5)
    y += 14

    # --- Patient info table ---
    for label, value in PATIENT_INFO:
        page.insert_text((ml, y), label + ":",
                         fontname="sans-bold", fontsize=9, color=(0.3, 0.3, 0.3))
        page.insert_text((ml + 138, y), value,
                         fontname="sans", fontsize=9)
        y += 14
    y += 8

    page.draw_line((ml, y), (595 - mr, y), color=(0.75, 0.75, 0.75), width=0.5)
    y += 14

    # --- Body paragraphs ---
    para_gap = 8
    for para in BODY_PARAGRAPHS:
        rect = fitz.Rect(ml, y, 595 - mr, 795)
        overflow = page.insert_textbox(rect, para,
                                       fontname="sans", fontsize=9.5,
                                       lineheight=1.4)
        # Estimate used height: count lines in para
        lines = para.count("\n") + 1
        char_per_line = W / (9.5 * 0.55)          # rough estimate
        wrapped = max(lines, len(para) // int(char_per_line) + 1)
        y += wrapped * 9.5 * 1.4 + para_gap

    # --- Footer ---
    page.draw_line((ml, 810), (595 - mr, 810), color=(0.75, 0.75, 0.75), width=0.5)
    page.insert_text((ml, 824), FOOTER,
                     fontname="sans", fontsize=7.5, color=(0.5, 0.5, 0.5))

    doc.save(str(OUTPUT), garbage=4, deflate=True)
    size_kb = OUTPUT.stat().st_size // 1024
    print(f"Written: {OUTPUT}  ({size_kb} KB)")


if __name__ == "__main__":
    build()

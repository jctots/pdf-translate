"""
End-to-end integration tests using docs/assets/demo-document-de.pdf.

These tests run the full translation pipeline (prescan → translate → write
outputs) with a mock call_fn, verifying that the pipeline produces valid output
files from a real PDF fixture rather than from synthetic test data.

No live translation service is required — call_fn is a simple echo stub.
"""

import zipfile
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from pipeline import translate_pdf_sync

DEMO_PDF = Path(__file__).parent.parent / "docs" / "assets" / "demo-document-de.pdf"


def _mock_translate(text: str, source: str, target: str) -> str:
    """Stub translator: wraps text in [EN: …] to make translation visible."""
    return f"[EN: {text[:60]}]" if len(text) > 60 else f"[EN: {text}]"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def demo_pdf():
    if not DEMO_PDF.exists():
        pytest.skip(f"Demo PDF not found: {DEMO_PDF}")
    return str(DEMO_PDF)


@pytest.fixture(scope="module")
def translated_outputs(demo_pdf, tmp_path_factory):
    """Run translate_pdf_sync once; share results across tests in this module."""
    tmp = tmp_path_factory.mktemp("e2e")
    import shutil
    pdf_copy = str(tmp / "demo-document-de.pdf")
    shutil.copy(demo_pdf, pdf_copy)

    translated_path, sbs_path, html_path = translate_pdf_sync(
        pdf_path=pdf_copy,
        source="de",
        target="en",
        service="Mock",
        call_fn=_mock_translate,
    )
    return {
        "translated": Path(translated_path),
        "sbs":        Path(sbs_path),
        "html":       Path(html_path),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDemoPdf:
    def test_demo_pdf_has_text(self, demo_pdf):
        """Demo PDF must have extractable text blocks (not a scanned image)."""
        doc = fitz.open(demo_pdf)
        text = "".join(p.get_text() for p in doc)
        assert "Mustermann" in text
        assert "Angststörung" in text or "Angstst" in text  # umlaut tolerance

    def test_demo_pdf_is_single_page(self, demo_pdf):
        doc = fitz.open(demo_pdf)
        assert doc.page_count == 1

    def test_demo_pdf_contains_pii(self, demo_pdf):
        """Confirm the demo PDF has the PII that makes the privacy point."""
        doc = fitz.open(demo_pdf)
        text = "".join(p.get_text() for p in doc)
        assert "Hauptstra" in text        # address (ß may vary by extraction)
        assert "15.03.1987" in text or "15. M" in text   # date of birth
        assert "123 456 789" in text      # insurance number


class TestTranslationOutputs:
    def test_translated_pdf_exists(self, translated_outputs):
        assert translated_outputs["translated"].exists()

    def test_sbs_pdf_exists(self, translated_outputs):
        assert translated_outputs["sbs"].exists()

    def test_html_exists(self, translated_outputs):
        assert translated_outputs["html"].exists()

    def test_translated_pdf_is_valid(self, translated_outputs):
        doc = fitz.open(str(translated_outputs["translated"]))
        assert doc.page_count >= 1

    def test_sbs_pdf_is_valid(self, translated_outputs):
        doc = fitz.open(str(translated_outputs["sbs"]))
        assert doc.page_count >= 1

    def test_html_contains_translation_marker(self, translated_outputs):
        """Verify the HTML output contains the [EN: …] markers from our stub."""
        html = translated_outputs["html"].read_text(encoding="utf-8")
        assert "[EN:" in html

    def test_translated_pdf_has_text_layer(self, translated_outputs):
        """Translated PDF must have a selectable text layer (not blank)."""
        doc = fitz.open(str(translated_outputs["translated"]))
        text = "".join(p.get_text() for p in doc)
        assert "[EN:" in text

    def test_translated_pdf_page_count_matches_source(self, demo_pdf, translated_outputs):
        src_pages = fitz.open(demo_pdf).page_count
        out_pages = fitz.open(str(translated_outputs["translated"])).page_count
        assert out_pages == src_pages

    def test_sbs_pdf_has_double_width(self, demo_pdf, translated_outputs):
        """Side-by-side PDF landscape width should be ~2× the source page height."""
        src = fitz.open(demo_pdf)[0].rect
        sbs = fitz.open(str(translated_outputs["sbs"]))[0].rect
        # Source is A4 portrait (595×842); SBS is landscape (~1684×595)
        assert sbs.width > src.width * 1.5

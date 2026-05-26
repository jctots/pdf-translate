"""
Unit tests for ocr_utils.py.

pytesseract and Pillow are installed in the venv; their external calls are
mocked so no live OCR engine is needed.
"""

from unittest.mock import MagicMock, patch

import fitz
import pytest

import ocr_utils
from ocr_utils import (
    MIN_VISIBLE_CHARS,
    OCR_DPI,
    OCR_LLM_DEFAULT_MODEL,
    OCR_LLM_DEFAULT_PROMPT,
    OCR_LLM_DPI,
    OCR_LLM_MARGIN_X,
    OCR_LLM_MARGIN_Y,
    is_scanned_page,
    ocr_page,
    ocr_page_llm,
    source_lang_to_tesseract,
)


# ---------------------------------------------------------------------------
# source_lang_to_tesseract
# ---------------------------------------------------------------------------

class TestSourceLangToTesseract:
    def test_known_codes(self):
        assert source_lang_to_tesseract("en")  == "eng"
        assert source_lang_to_tesseract("nl")  == "nld"
        assert source_lang_to_tesseract("de")  == "deu"
        assert source_lang_to_tesseract("fr")  == "fra"
        assert source_lang_to_tesseract("es")  == "spa"
        assert source_lang_to_tesseract("it")  == "ita"
        assert source_lang_to_tesseract("pt")  == "por"
        assert source_lang_to_tesseract("ru")  == "rus"
        assert source_lang_to_tesseract("ja")  == "jpn"
        assert source_lang_to_tesseract("zh")  == "chi_sim"
        assert source_lang_to_tesseract("ko")  == "kor"
        assert source_lang_to_tesseract("ar")  == "ara"
        assert source_lang_to_tesseract("tr")  == "tur"
        assert source_lang_to_tesseract("pl")  == "pol"
        assert source_lang_to_tesseract("tl")  == "tgl"

    def test_auto_returns_multi_language_string(self):
        result = source_lang_to_tesseract("auto")
        for expected in ("eng", "nld", "deu", "fra", "spa", "por", "ita"):
            assert expected in result, f"Expected {expected!r} in auto OCR lang string"

    def test_unknown_falls_back_to_eng(self):
        assert source_lang_to_tesseract("xx") == "eng"
        assert source_lang_to_tesseract("")   == "eng"
        assert source_lang_to_tesseract("zz") == "eng"


# ---------------------------------------------------------------------------
# is_scanned_page
# ---------------------------------------------------------------------------

def _make_page(text: str) -> fitz.Page:
    """Create a one-page fitz document containing text and return the page."""
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text, fontsize=12)
    return page


class TestIsScannedPage:
    def test_rich_text_page_returns_false(self):
        page = _make_page("This is a paragraph of readable text with many characters.")
        assert is_scanned_page(page) is False

    def test_empty_page_returns_true(self):
        page = _make_page("")
        assert is_scanned_page(page) is True

    def test_very_short_text_returns_true(self):
        page = _make_page("Hi")
        assert is_scanned_page(page) is True

    def test_exactly_at_threshold_returns_false(self):
        text = "a" * MIN_VISIBLE_CHARS
        page = _make_page(text)
        assert is_scanned_page(page) is False

    def test_whitespace_only_counts_as_scanned(self):
        page = _make_page("   \t\n   ")
        assert is_scanned_page(page) is True


# ---------------------------------------------------------------------------
# ocr_page helpers — test grouping logic directly
# ---------------------------------------------------------------------------

def _make_tess_data(words, confs, block_nums, par_nums, line_nums,
                    lefts, tops, widths, heights):
    """Build a dict matching pytesseract.image_to_data Output.DICT format."""
    return {
        "text":      words,
        "conf":      confs,
        "block_num": block_nums,
        "par_num":   par_nums,
        "line_num":  line_nums,
        "left":      lefts,
        "top":       tops,
        "width":     widths,
        "height":    heights,
    }


def _group_paragraphs(data: dict) -> dict:
    """Run just the grouping loop from ocr_page (no pytesseract call).

    Groups words by (block_num, par_num) — paragraph level, matching ocr_page.
    """
    paragraphs: dict = {}
    for i, word in enumerate(data["text"]):
        word = word.strip()
        conf = int(data["conf"][i])
        if conf <= 0 or not word:
            continue
        key = (data["block_num"][i], data["par_num"][i])
        lx0 = data["left"][i]
        ly0 = data["top"][i]
        lx1 = lx0 + data["width"][i]
        ly1 = ly0 + data["height"][i]
        if key not in paragraphs:
            paragraphs[key] = {"words": [word], "x0": lx0, "y0": ly0, "x1": lx1, "y1": ly1}
        else:
            e = paragraphs[key]
            e["words"].append(word)
            e["x0"] = min(e["x0"], lx0)
            e["y0"] = min(e["y0"], ly0)
            e["x1"] = max(e["x1"], lx1)
            e["y1"] = max(e["y1"], ly1)
    return paragraphs


class TestGroupingLogic:
    def test_zero_confidence_word_filtered(self):
        data = _make_tess_data(
            words=["good", "bad"], confs=[85, -1],
            block_nums=[1, 1], par_nums=[1, 1], line_nums=[1, 1],
            lefts=[10, 90], tops=[10, 10], widths=[60, 50], heights=[20, 20],
        )
        paragraphs = _group_paragraphs(data)
        assert len(paragraphs) == 1
        assert paragraphs[(1, 1)]["words"] == ["good"]

    def test_two_words_same_paragraph_merged(self):
        data = _make_tess_data(
            words=["hello", "world"], confs=[90, 88],
            block_nums=[1, 1], par_nums=[1, 1], line_nums=[1, 1],
            lefts=[10, 80], tops=[10, 10], widths=[60, 60], heights=[20, 20],
        )
        paragraphs = _group_paragraphs(data)
        assert len(paragraphs) == 1
        entry = paragraphs[(1, 1)]
        assert entry["words"] == ["hello", "world"]
        assert entry["x0"] == 10
        assert entry["x1"] == 140   # 80 + 60

    def test_two_lines_same_paragraph_merged(self):
        """Lines within the same paragraph collapse into one block (fewer API calls)."""
        data = _make_tess_data(
            words=["line1", "line2"], confs=[90, 90],
            block_nums=[1, 1], par_nums=[1, 1], line_nums=[1, 2],
            lefts=[10, 10], tops=[10, 40], widths=[60, 60], heights=[20, 20],
        )
        paragraphs = _group_paragraphs(data)
        assert len(paragraphs) == 1
        assert paragraphs[(1, 1)]["words"] == ["line1", "line2"]

    def test_two_paragraphs_produce_two_groups(self):
        data = _make_tess_data(
            words=["para1", "para2"], confs=[90, 90],
            block_nums=[1, 1], par_nums=[1, 2], line_nums=[1, 1],
            lefts=[10, 10], tops=[10, 80], widths=[60, 60], heights=[20, 20],
        )
        paragraphs = _group_paragraphs(data)
        assert len(paragraphs) == 2

    def test_empty_word_filtered(self):
        data = _make_tess_data(
            words=["", "hello"], confs=[90, 90],
            block_nums=[1, 1], par_nums=[1, 1], line_nums=[1, 1],
            lefts=[10, 80], tops=[10, 10], widths=[60, 60], heights=[20, 20],
        )
        paragraphs = _group_paragraphs(data)
        assert paragraphs[(1, 1)]["words"] == ["hello"]

    def test_bbox_expanded_across_words(self):
        data = _make_tess_data(
            words=["a", "b", "c"], confs=[90, 90, 90],
            block_nums=[1, 1, 1], par_nums=[1, 1, 1], line_nums=[1, 1, 1],
            lefts=[5, 50, 100], tops=[10, 12, 10],
            widths=[30, 30, 30], heights=[20, 20, 20],
        )
        paragraphs = _group_paragraphs(data)
        entry = paragraphs[(1, 1)]
        assert entry["x0"] == 5
        assert entry["x1"] == 130   # 100 + 30
        assert entry["y0"] == 10
        assert entry["y1"] == 32    # max(10+20, 12+20, 10+20)


# ---------------------------------------------------------------------------
# ocr_page — call with mocked pytesseract + Pillow
# ---------------------------------------------------------------------------

def _make_ocr_data_single(word="hello", conf=90,
                           left=300, top=150, width=240, height=60):
    """Single-word tesseract data at OCR_DPI pixel coords."""
    return _make_tess_data(
        words=[word], confs=[conf],
        block_nums=[1], par_nums=[1], line_nums=[1],
        lefts=[left], tops=[top], widths=[width], heights=[height],
    )


def _fake_pytesseract(tess_data: dict) -> MagicMock:
    """Build a fake pytesseract module suitable for sys.modules injection."""
    fake = MagicMock()
    fake.image_to_data.return_value = tess_data
    fake.Output.DICT = "dict"
    return fake


class TestOcrPage:
    """
    Tests for ocr_page() that mock pytesseract (injected via sys.modules because
    it is a lazy import) and PIL.Image.open so no live Tesseract engine is needed.
    """

    def _run_ocr(self, tess_data: dict, lang: str = "eng") -> list[tuple]:
        import sys
        page = _make_page("")
        mock_img = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b""

        with (
            patch.object(page, "get_pixmap", return_value=mock_pix),
            patch.dict(sys.modules, {"pytesseract": _fake_pytesseract(tess_data)}),
            patch("PIL.Image.open", return_value=mock_img),
        ):
            return ocr_page(page, lang=lang)

    def test_single_word_returns_one_block(self):
        data = _make_ocr_data_single("hello", conf=90,
                                     left=300, top=150, width=240, height=60)
        blocks = self._run_ocr(data)
        assert len(blocks) == 1
        x0, y0, x1, y1, text, fontsize, font_name, flags, is_table_cell = blocks[0]
        assert text == "hello"
        assert font_name == "OCR"
        assert flags == 0
        assert is_table_cell is False
        assert fontsize >= 6.0

    def test_bounding_box_scaled_to_pdf_points(self):
        scale = OCR_DPI / 72.0
        px_left, px_top, px_w, px_h = 300, 150, 240, 60
        data = _make_ocr_data_single(left=px_left, top=px_top, width=px_w, height=px_h)
        blocks = self._run_ocr(data)
        x0, y0, x1, y1 = blocks[0][:4]
        assert abs(x0 - px_left / scale) < 0.01
        assert abs(y0 - px_top  / scale) < 0.01
        assert abs(x1 - (px_left + px_w) / scale) < 0.01
        assert abs(y1 - (px_top  + px_h) / scale) < 0.01

    def test_fontsize_fixed_default(self):
        # OCR blocks use a fixed 10.0 pt fontsize — per-word height estimates are
        # unreliable because lowercase letters without ascenders produce small bboxes.
        data = _make_ocr_data_single(height=60)
        blocks = self._run_ocr(data)
        assert blocks[0][5] == 10.0

    def test_fontsize_fixed_regardless_of_word_height(self):
        # Even with height=1px (which the old formula clamped to 6.0), the fixed
        # default is 10.0 — no clamping needed because the value is hardcoded.
        data = _make_ocr_data_single(height=1)
        blocks = self._run_ocr(data)
        assert blocks[0][5] == 10.0

    def test_low_confidence_word_produces_no_block(self):
        data = _make_tess_data(
            words=["noise"], confs=[-1],
            block_nums=[1], par_nums=[1], line_nums=[1],
            lefts=[0], tops=[0], widths=[50], heights=[20],
        )
        blocks = self._run_ocr(data)
        assert blocks == []

    def test_multi_word_line_joined_with_space(self):
        data = _make_tess_data(
            words=["hallo", "wereld"], confs=[90, 90],
            block_nums=[1, 1], par_nums=[1, 1], line_nums=[1, 1],
            lefts=[10, 80], tops=[10, 10], widths=[60, 60], heights=[20, 20],
        )
        blocks = self._run_ocr(data)
        assert len(blocks) == 1
        assert blocks[0][4] == "hallo wereld"

    def test_lang_passed_to_pytesseract(self):
        import sys
        data = _make_ocr_data_single()
        page = _make_page("")
        fake_tess = _fake_pytesseract(data)
        mock_img = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b""

        with (
            patch.object(page, "get_pixmap", return_value=mock_pix),
            patch.dict(sys.modules, {"pytesseract": fake_tess}),
            patch("PIL.Image.open", return_value=mock_img),
        ):
            ocr_page(page, lang="nld")

        _, kwargs = fake_tess.image_to_data.call_args
        assert kwargs.get("lang") == "nld"


# ---------------------------------------------------------------------------
# ocr_page_llm — call with mocked httpx
# ---------------------------------------------------------------------------

class TestOcrPageLlm:
    """Tests for ocr_page_llm() with mocked httpx so no live Ollama is needed."""

    def _run_llm_ocr(
        self,
        response_text: str,
        url: str = "http://localhost:11434",
        model: str = OCR_LLM_DEFAULT_MODEL,
        prompt: str = OCR_LLM_DEFAULT_PROMPT,
    ) -> list[tuple]:
        """Call ocr_page_llm with a mocked httpx response."""
        import sys
        page = _make_page("")  # blank page — pixel rendering is mocked below

        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG\r\n"  # minimal fake PNG bytes

        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"content": response_text}}

        with (
            patch.object(page, "get_pixmap", return_value=mock_pix),
            patch("ocr_utils.httpx", create=True) as mock_httpx,  # lazy import path
        ):
            # httpx is imported lazily inside the function — patch via sys.modules
            import httpx as real_httpx
            fake_httpx = MagicMock()
            fake_httpx.post.return_value = mock_response
            with patch.dict(sys.modules, {"httpx": fake_httpx}):
                return ocr_page_llm(page, url=url, model=model, prompt=prompt)

    def test_two_paragraphs_produce_two_blocks(self):
        result = self._run_llm_ocr("First paragraph.\n\nSecond paragraph.")
        assert len(result) == 2

    def test_block_text_preserved(self):
        result = self._run_llm_ocr("Hello world.\n\nGoodbye world.")
        assert result[0][4] == "Hello world."
        assert result[1][4] == "Goodbye world."

    def test_font_name_is_ocr_llm(self):
        result = self._run_llm_ocr("Some text.")
        assert result[0][6] == "OCR-LLM"

    def test_flags_and_table_cell_defaults(self):
        result = self._run_llm_ocr("Some text.")
        assert result[0][7] == 0
        assert result[0][8] is False

    def test_empty_response_returns_empty_list(self):
        result = self._run_llm_ocr("")
        assert result == []

    def test_whitespace_only_response_returns_empty_list(self):
        result = self._run_llm_ocr("   \n\n   ")
        assert result == []

    def test_bboxes_stack_and_cover_page_height(self):
        """Bounding boxes must tile the content area with no gaps."""
        result = self._run_llm_ocr("Para one.\n\nPara two.\n\nPara three.")
        assert len(result) == 3
        # Each box y1 should equal the next box y0 (no gaps between slices)
        for i in range(len(result) - 1):
            assert abs(result[i][3] - result[i + 1][1]) < 0.01

    def test_bboxes_respect_margins(self):
        """x0/x1 must respect OCR_LLM_MARGIN_X; y0 must start at OCR_LLM_MARGIN_Y."""
        result = self._run_llm_ocr("Only one paragraph.")
        assert len(result) == 1
        x0, y0, x1, y1 = result[0][:4]
        assert abs(x0 - OCR_LLM_MARGIN_X) < 0.01
        assert abs(y0 - OCR_LLM_MARGIN_Y) < 0.01
        # x1 must be less than full page width (margin applied on the right too)
        page_w = _make_page("").rect.width
        assert abs(x1 - (page_w - OCR_LLM_MARGIN_X)) < 0.01

    def test_fontsize_at_least_six(self):
        # Even a single long paragraph should produce fontsize >= 6
        result = self._run_llm_ocr("Only one paragraph here.")
        assert result[0][5] >= 6.0


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

class TestConstants:
    def test_ocr_dpi_at_least_150(self):
        assert OCR_DPI >= 150

    def test_ocr_llm_dpi_reasonable(self):
        assert 72 <= OCR_LLM_DPI <= 300

    def test_min_visible_chars_reasonable(self):
        assert 5 <= MIN_VISIBLE_CHARS <= 100

    def test_llm_default_model_set(self):
        assert OCR_LLM_DEFAULT_MODEL == "glm-ocr"

    def test_llm_default_prompt_nonempty(self):
        assert len(OCR_LLM_DEFAULT_PROMPT) > 20

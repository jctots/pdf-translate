"""
Unit tests for pipeline.py internal helpers.
"""

import pytest
from unittest.mock import MagicMock, patch

from pipeline import _translate_one_block


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _block(text: str = "hello", fontsize: float = 12.0) -> tuple:
    return (10.0, 20.0, 100.0, 30.0, text, fontsize, "Helvetica", 0, False)


# ---------------------------------------------------------------------------
# _translate_one_block
# ---------------------------------------------------------------------------

class TestTranslateOneBlock:
    def test_returns_translated_tuple(self):
        call_fn = MagicMock(return_value="hallo")
        result = _translate_one_block(_block("hello"), "en", "nl", "Google", call_fn, allow_wrap=False)
        assert result[4] == "hallo"

    def test_preserves_bbox(self):
        call_fn = MagicMock(return_value="hallo")
        result = _translate_one_block(_block(), "en", "nl", "Google", call_fn, allow_wrap=False)
        assert result[:4] == (10.0, 20.0, 100.0, 30.0)

    def test_preserves_metadata_fields(self):
        call_fn = MagicMock(return_value="hallo")
        block = (1.0, 2.0, 3.0, 4.0, "text", 11.5, "Arial", 4, True)
        result = _translate_one_block(block, "en", "nl", "Google", call_fn, allow_wrap=False)
        assert result[5] == 11.5    # fontsize
        assert result[6] == "Arial" # orig_font_name
        assert result[7] == 4       # flags
        assert result[8] is True    # is_table_cell

    def test_allow_wrap_collapses_newlines(self):
        call_fn = MagicMock(return_value="ok")
        _translate_one_block(_block("line one\nline two"), "en", "nl", "Google", call_fn, allow_wrap=True)
        call_fn.assert_called_once_with("line one line two", "en", "nl")

    def test_allow_wrap_false_preserves_newlines(self):
        call_fn = MagicMock(return_value="ok")
        _translate_one_block(_block("line one\nline two"), "en", "nl", "Google", call_fn, allow_wrap=False)
        call_fn.assert_called_once_with("line one\nline two", "en", "nl")

    def test_libretranslate_delay_applied(self):
        call_fn = MagicMock(return_value="hallo")
        with patch("pipeline.time.sleep") as mock_sleep:
            with patch("pipeline._LIBRE_BLOCK_DELAY_MS", 200):
                _translate_one_block(_block(), "en", "nl", "LibreTranslate", call_fn, allow_wrap=False)
        mock_sleep.assert_called_once_with(0.2)

    def test_libretranslate_delay_skipped_when_zero(self):
        call_fn = MagicMock(return_value="hallo")
        with patch("pipeline.time.sleep") as mock_sleep:
            with patch("pipeline._LIBRE_BLOCK_DELAY_MS", 0):
                _translate_one_block(_block(), "en", "nl", "LibreTranslate", call_fn, allow_wrap=False)
        mock_sleep.assert_not_called()

    def test_non_libretranslate_no_delay(self):
        call_fn = MagicMock(return_value="hallo")
        with patch("pipeline.time.sleep") as mock_sleep:
            _translate_one_block(_block(), "en", "nl", "Google", call_fn, allow_wrap=False)
        mock_sleep.assert_not_called()

    def test_backend_exception_propagates(self):
        call_fn = MagicMock(side_effect=RuntimeError("connection refused"))
        with pytest.raises(RuntimeError, match="connection refused"):
            _translate_one_block(_block(), "en", "nl", "Ollama", call_fn, allow_wrap=False)

    def test_call_fn_receives_source_and_target(self):
        call_fn = MagicMock(return_value="translated")
        _translate_one_block(_block("text"), "de", "fr", "Google", call_fn, allow_wrap=False)
        call_fn.assert_called_once_with("text", "de", "fr")

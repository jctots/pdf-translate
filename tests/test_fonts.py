"""
Unit tests for fonts.py — font resolution pipeline.
"""

import pytest
from fonts import (
    _family_from_name,
    _span_style,
    normalize_font_name,
    resolve_font,
)


# ---------------------------------------------------------------------------
# normalize_font_name
# ---------------------------------------------------------------------------

class TestNormalizeFontName:
    def test_strips_subset_prefix(self):
        assert normalize_font_name("ABCDEF+Arial") == "arial"

    def test_no_prefix_unchanged(self):
        assert normalize_font_name("TimesNewRoman") == "timesnewroman"

    def test_already_lowercase(self):
        assert normalize_font_name("helvetica") == "helvetica"

    def test_strips_whitespace(self):
        assert normalize_font_name("  Arial  ") == "arial"

    def test_hash_name_no_prefix(self):
        # Obfuscated/hash names don't contain '+' — just lowercased
        assert normalize_font_name("czbbbt1x01141") == "czbbbt1x01141"


# ---------------------------------------------------------------------------
# _family_from_name
# ---------------------------------------------------------------------------

class TestFamilyFromName:
    def test_sans_arial(self):
        assert _family_from_name("arial") == "sans"

    def test_sans_helvetica(self):
        assert _family_from_name("helvetica") == "sans"

    def test_serif_times(self):
        assert _family_from_name("timesnewroman") == "serif"

    def test_serif_georgia(self):
        assert _family_from_name("georgia") == "serif"

    def test_mono_courier(self):
        assert _family_from_name("courier") == "mono"

    def test_mono_consolas(self):
        assert _family_from_name("consolas") == "mono"

    def test_unknown_defaults_to_sans(self):
        assert _family_from_name("xyz123unknown") == "sans"


# ---------------------------------------------------------------------------
# _span_style
# ---------------------------------------------------------------------------

class TestSpanStyle:
    # Font name is authoritative when it carries style keywords
    def test_bold_from_name(self):
        is_bold, is_italic = _span_style("arialbold", flags=0)
        assert is_bold is True
        assert is_italic is False

    def test_italic_from_name(self):
        is_bold, is_italic = _span_style("arialitalic", flags=0)
        assert is_bold is False
        assert is_italic is True

    def test_bold_italic_from_name(self):
        is_bold, is_italic = _span_style("arialbolditalic", flags=0)
        assert is_bold is True
        assert is_italic is True

    def test_regular_from_name(self):
        is_bold, is_italic = _span_style("arial", flags=0)
        assert is_bold is False
        assert is_italic is False

    # Flags used as fallback when name has no style keywords (hash/obfuscated)
    def test_flags_used_when_name_has_no_style(self):
        # flags: bit 4 = bold (16), bit 1 = italic (2)
        is_bold, is_italic = _span_style("czbbbt1x01141", flags=16 | 2)
        assert is_bold is True
        assert is_italic is True

    def test_name_overrides_flags(self):
        # Name says bold; flags say not bold — name wins
        is_bold, is_italic = _span_style("arialbold", flags=0)
        assert is_bold is True


# ---------------------------------------------------------------------------
# resolve_font — three tiers
# ---------------------------------------------------------------------------

class TestResolveFont:
    def test_tier3_base14_when_ttf_missing(self):
        """Tier 2 skipped when TTF file doesn't exist → base14 fallback."""
        from unittest.mock import patch
        with patch("fonts.Path.exists", return_value=False):
            alias, font_bytes = resolve_font("xyz123unknown", flags=0, embedded_fonts={})
        assert font_bytes is None
        assert alias in ("helv", "hebo", "heit", "hebi",
                         "tiro", "tibo", "tiit", "tibi",
                         "cour", "cobo", "coit", "cobi")

    def test_tier3_base14_bold_when_ttf_missing(self):
        """Bold flag + missing TTF → bold base14 alias."""
        from unittest.mock import patch
        with patch("fonts.Path.exists", return_value=False):
            alias, font_bytes = resolve_font("xyz123unknown", flags=16, embedded_fonts={})
        # flags used as bold fallback (name has no style keywords) → hebo
        assert font_bytes is None
        assert alias == "hebo"

    def test_tier2_liberation_sans_regular(self):
        """Recognisable sans name, not embedded → Liberation Sans Regular."""
        alias, font_bytes = resolve_font("arial", flags=0, embedded_fonts={})
        assert font_bytes is not None  # TTF bytes loaded from fonts/
        assert "lsans" in alias

    def test_tier2_liberation_serif_bold(self):
        alias, font_bytes = resolve_font("timesnewromanbold", flags=0, embedded_fonts={})
        assert font_bytes is not None
        assert "lserif" in alias
        assert alias.endswith("b") or "b" in alias  # bold variant

    def test_tier2_liberation_mono_regular(self):
        alias, font_bytes = resolve_font("courier", flags=0, embedded_fonts={})
        assert font_bytes is not None
        assert "lmono" in alias

    def test_tier1_embedded_recognisable_name(self):
        """Embedded full font with a recognisable name → tier 1 (embedded bytes returned)."""
        fake_bytes = b"fakefontdata"
        embedded = {"arial": fake_bytes}
        alias, font_bytes = resolve_font("arial", flags=0, embedded_fonts=embedded)
        assert font_bytes is fake_bytes  # exact same object

    def test_tier1_skipped_for_hash_name(self):
        """Embedded font with hash/obfuscated name → tier 1 skipped, falls to tier 2/3."""
        fake_bytes = b"fakefontdata"
        embedded = {"czbbbt1x01141": fake_bytes}
        alias, font_bytes = resolve_font("czbbbt1x01141", flags=0, embedded_fonts=embedded)
        # Should NOT return the embedded bytes; hash names are not recognisable
        assert font_bytes is not fake_bytes

"""
pdf-translate — font resolution.

Three-tier strategy for every translated text block:
  1. Embedded in the source PDF — full fonts only (subset fonts skipped;
     hash/obfuscated names skipped — both are glyph-limited).
  2. Bundled Liberation TTF that matches the font family and style.
  3. Base14 fallback (helv/hebo/heit/hebi, tiro/…, cour/…).

Public API:
  extract_doc_fonts(doc)             → {norm_name: bytes}
  resolve_font(orig_name, flags, embedded_fonts) → (alias, bytes | None)
"""

import re
from pathlib import Path

import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Paths and lookup tables
# ---------------------------------------------------------------------------

FONTS_DIR = Path(__file__).parent / "fonts"

# base14 variants keyed by family and (is_bold, is_italic)
_BASE14: dict[str, dict[tuple[bool, bool], str]] = {
    "sans":  {(False, False): "helv", (True, False): "hebo",
              (False, True):  "heit", (True, True):  "hebi"},
    "serif": {(False, False): "tiro", (True, False): "tibo",
              (False, True):  "tiit", (True, True):  "tibi"},
    "mono":  {(False, False): "cour", (True, False): "cobo",
              (False, True):  "coit", (True, True):  "cobi"},
}

# Liberation font file names keyed by family and (is_bold, is_italic)
_LIBERATION: dict[str, dict[tuple[bool, bool], str]] = {
    "sans": {
        (False, False): "LiberationSans-Regular.ttf",
        (True,  False): "LiberationSans-Bold.ttf",
        (False, True):  "LiberationSans-Italic.ttf",
        (True,  True):  "LiberationSans-BoldItalic.ttf",
    },
    "serif": {
        (False, False): "LiberationSerif-Regular.ttf",
        (True,  False): "LiberationSerif-Bold.ttf",
        (False, True):  "LiberationSerif-Italic.ttf",
        (True,  True):  "LiberationSerif-BoldItalic.ttf",
    },
    "mono": {
        (False, False): "LiberationMono-Regular.ttf",
        (True,  False): "LiberationMono-Bold.ttf",
        (False, True):  "LiberationMono-Italic.ttf",
        (True,  True):  "LiberationMono-BoldItalic.ttf",
    },
}

# Font name substrings → family (first match wins)
_FAMILY_KEYWORDS: dict[str, list[str]] = {
    "sans":  ["arial", "helvetica", "verdana", "tahoma", "calibri",
              "trebuchet", "futura", "gill", "frank"],
    "serif": ["times", "georgia", "garamond", "palatino", "baskerville",
              "caslon", "minion", "cambria"],
    "mono":  ["courier", "consolas", "lucida", "monaco", "menlo",
              "inconsolata", "mono", "typewriter"],
}

# All family keywords flattened — used to detect hash/obfuscated font names
_ALL_FONT_KEYWORDS: list[str] = [kw for kws in _FAMILY_KEYWORDS.values() for kw in kws]

_BOLD_KEYWORDS   = ["bold", "-bd", "demi", "heavy", "black", "semibold", "extrabold"]
_ITALIC_KEYWORDS = ["italic", "oblique", "slant"]

# Lazy-loaded font bytes cache (avoids re-reading TTF files per page)
_font_bytes_cache: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_font_bytes(path: Path) -> bytes:
    key = str(path)
    if key not in _font_bytes_cache:
        _font_bytes_cache[key] = path.read_bytes()
    return _font_bytes_cache[key]


def normalize_font_name(name: str) -> str:
    """Strip ABCDEF+ subset prefix and lowercase."""
    if "+" in name:
        name = name.split("+", 1)[1]
    return name.lower().strip()


def _family_from_name(norm: str) -> str:
    """Map a normalised font name to 'sans', 'serif', or 'mono'. Default: 'sans'."""
    for family, keywords in _FAMILY_KEYWORDS.items():
        if any(kw in norm for kw in keywords):
            return family
    return "sans"


def _span_style(norm_name: str, flags: int) -> tuple[bool, bool]:
    """
    Return (is_bold, is_italic) for a span.
    Font name keywords are the primary indicator; flags are used as fallback
    when the name carries no style information (e.g. hash/obfuscated names).
    """
    name_bold   = any(x in norm_name for x in _BOLD_KEYWORDS)
    name_italic = any(x in norm_name for x in _ITALIC_KEYWORDS)
    flag_bold   = bool(flags & 16)
    flag_italic = bool(flags & 2)
    name_has_style = name_bold or name_italic
    is_bold   = name_bold   or (flag_bold   and not name_has_style)
    is_italic = name_italic or (flag_italic and not name_has_style)
    return is_bold, is_italic


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_doc_fonts(doc: fitz.Document) -> dict[str, bytes]:
    """
    Extract full embedded fonts from a PDF.
    Returns {normalised_name: font_bytes}.

    Subset fonts (basefont contains '+') are skipped — they only contain
    glyphs from the original text and would render translated characters
    as boxes.
    """
    seen: set[int] = set()
    result: dict[str, bytes] = {}
    for page in doc:
        for entry in page.get_fonts():
            xref: int    = entry[0]
            basefont: str = entry[3]
            if xref == 0 or xref in seen:
                continue
            seen.add(xref)
            if "+" in (basefont or ""):
                continue  # subset font — glyph-limited
            try:
                font_info = doc.extract_font(xref)
                if isinstance(font_info, (list, tuple)):
                    content = font_info[3] if len(font_info) > 3 else b""
                    fname   = font_info[0] if font_info else basefont
                else:
                    content = font_info.get("content", b"")
                    fname   = font_info.get("name", basefont)
                if content:
                    norm = normalize_font_name(basefont or fname or "")
                    if norm:
                        result[norm] = content
            except Exception:
                pass
    return result


def resolve_font(
    orig_name: str,
    flags: int,
    embedded_fonts: dict[str, bytes],
) -> tuple[str, bytes | None]:
    """
    Three-tier font resolution for one text span.

    Returns (alias, font_bytes_or_None).
    - font_bytes not None → call page.insert_font(fontname=alias, fontbuffer=font_bytes)
      before using alias in insert_textbox.
    - font_bytes is None  → alias is a base14 name, usable directly.
    """
    norm    = normalize_font_name(orig_name)
    is_bold, is_italic = _span_style(norm, flags)
    variant = (is_bold, is_italic)
    family  = _family_from_name(norm)

    # Tier 1: embedded full font with a recognisable name
    is_recognizable = any(kw in norm for kw in _ALL_FONT_KEYWORDS)
    if norm in embedded_fonts and is_recognizable:
        alias = re.sub(r"[^a-zA-Z0-9]", "", norm)[:20] or "emb"
        return alias, embedded_fonts[norm]

    # Tier 2: bundled Liberation TTF
    filename = _LIBERATION.get(family, {}).get(variant)
    if filename:
        path = FONTS_DIR / filename
        if path.exists():
            abbr   = {"sans": "lsans", "serif": "lserif", "mono": "lmono"}[family]
            suffix = ("b" if is_bold else "") + ("i" if is_italic else "") or "r"
            return f"{abbr}_{suffix}", _load_font_bytes(path)

    # Tier 3: base14 fallback
    base14 = _BASE14.get(family, _BASE14["sans"]).get(variant, "helv")
    return base14, None
